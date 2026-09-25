"""Suggestions while typing, and editing a portfolio from a page.

The load-bearing constraint: typing must never start a screen. A screen takes
minutes and walks half a dozen government APIs — one per keystroke would be
both absurd and a rude way to treat a public service. Suggesting is reading;
screening stays an explicit action.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from test_search import make_contract

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    app_module._suggest_cache.clear()
    # No network in the tests: the remote half is stubbed per test.
    monkeypatch.setattr(app_module, "_remote_suggestions", lambda q: [])

    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    return TestClient(app_module.app), app_module


# --------------------------------------------------------------- suggesting

def test_a_short_prefix_suggests_nothing(client):
    """One character matches most of the database and helps nobody."""
    c, _ = client
    body = c.get("/v1/suggest?q=a", headers=AUTH).json()
    assert body["local"] == [] and body["remote"] == []


def test_screened_contractors_are_suggested(client):
    c, _ = client
    body = c.get("/v1/suggest?q=acme", headers=AUTH).json()

    entities = [s for s in body["local"] if s["kind"] == "entity"]
    assert entities and entities[0]["label"] == "ACME DYNAMICS LLC"
    assert entities[0]["key"] == "UEI123"


def test_officers_and_agencies_are_suggested_too(client):
    c, _ = client
    kinds = {s["kind"] for s in c.get("/v1/suggest?q=jane", headers=AUTH).json()["local"]}
    assert "officer" in kinds

    kinds = {s["kind"] for s in c.get("/v1/suggest?q=navy", headers=AUTH).json()["local"]}
    assert "agency" in kinds


def test_an_unscreened_contractor_is_offered_and_labelled(client, monkeypatch):
    c, app_module = client
    monkeypatch.setattr(app_module, "_remote_suggestions",
                        lambda q: ["RAYTHEON COMPANY"])

    body = c.get("/v1/suggest?q=rayth", headers=AUTH).json()
    assert body["remote"][0]["label"] == "RAYTHEON COMPANY"
    assert body["remote"][0]["kind"] == "unscreened"
    assert "not screened here" in body["remote"][0]["detail"]


def test_a_contractor_already_screened_is_not_offered_twice(client, monkeypatch):
    c, app_module = client
    monkeypatch.setattr(app_module, "_remote_suggestions",
                        lambda q: ["ACME DYNAMICS LLC", "ACME OTHER LLC"])

    body = c.get("/v1/suggest?q=acme", headers=AUTH).json()
    assert [s["label"] for s in body["remote"]] == ["ACME OTHER LLC"]


def test_suggesting_never_starts_a_screen(client, monkeypatch):
    """The whole point. Typing reads; it does not enqueue work."""
    c, app_module = client
    enqueued = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda *a, **kw: enqueued.append(a))

    for prefix in ("a", "ac", "acm", "acme", "acme d", "raytheon"):
        c.get(f"/v1/suggest?q={prefix}", headers=AUTH)

    assert enqueued == []
    assert c.get("/v1/screens", headers=AUTH).json()["runs"][0]["run_id"] == "r1"


def test_a_slow_or_broken_upstream_still_returns_local_suggestions(client, monkeypatch):
    """USAspending's autocomplete times out on short prefixes — measured. The
    box must never wait on it, and must never fail because of it."""
    c, app_module = client

    def boom(q):
        raise TimeoutError("upstream took too long")

    monkeypatch.setattr(app_module, "_remote_suggestions", boom)
    with pytest.raises(TimeoutError):
        app_module._remote_suggestions("acme")

    # The route's own guard is inside _remote_suggestions, so exercise the
    # real one with a failing connector instead.
    importlib.reload(app_module)
    app_module._suggest_cache.clear()
    monkeypatch.setattr(app_module.USASpendingConnector, "suggest_recipients",
                        lambda self, text, limit=6: (_ for _ in ()).throw(
                            TimeoutError("upstream took too long")))
    fresh = TestClient(app_module.app)
    body = fresh.get("/v1/suggest?q=acme", headers=AUTH).json()
    assert body["remote"] == []


def test_suggestions_need_a_key(client):
    c, _ = client
    assert c.get("/v1/suggest?q=acme").status_code == 401


# ------------------------------------------------------- editing a portfolio

def test_adding_to_an_empty_portfolio_starts_one(client):
    c, _ = client
    d = c.post("/v1/portfolio/edit", headers=AUTH,
               json={"key": "", "add": ["UEI123"]}).json()

    assert d["companies"] == 1
    assert d["key"].startswith("FOCI-PORTFOLIO-1.")


def test_adding_carries_the_name_from_the_database(client):
    c, _ = client
    key = c.post("/v1/portfolio/edit", headers=AUTH,
                 json={"add": ["UEI123"]}).json()["key"]
    loaded = c.post("/v1/portfolio", headers=AUTH, json={"key": key}).json()
    assert loaded["companies"][0]["entity_name"] == "ACME DYNAMICS LLC"


def test_adding_the_same_company_twice_does_not_duplicate_it(client):
    c, _ = client
    first = c.post("/v1/portfolio/edit", headers=AUTH,
                   json={"add": ["UEI123"]}).json()["key"]
    second = c.post("/v1/portfolio/edit", headers=AUTH,
                    json={"key": first, "add": ["uei123"]}).json()
    assert second["companies"] == 1


def test_removing_the_last_company_returns_no_key(client):
    c, _ = client
    key = c.post("/v1/portfolio/edit", headers=AUTH,
                 json={"add": ["UEI123"]}).json()["key"]
    d = c.post("/v1/portfolio/edit", headers=AUTH,
               json={"key": key, "remove": ["UEI123"]}).json()

    assert d["key"] == ""
    assert "no companies" in d["detail"]


def test_an_older_key_still_opens_the_older_portfolio(client):
    """Every edit mints a new key; the one saved last month still opens what
    was saved last month. That is a property, not an inconvenience."""
    c, _ = client
    one = c.post("/v1/portfolio/edit", headers=AUTH,
                 json={"add": ["UEI123"]}).json()["key"]
    two = c.post("/v1/portfolio/edit", headers=AUTH,
                 json={"key": one, "add": ["UEI999"]}).json()["key"]

    assert c.post("/v1/portfolio", headers=AUTH,
                  json={"key": one}).json()["totals"]["companies"] == 1
    assert c.post("/v1/portfolio", headers=AUTH,
                  json={"key": two}).json()["totals"]["companies"] == 2


def test_editing_a_damaged_key_is_refused(client):
    c, _ = client
    r = c.post("/v1/portfolio/edit", headers=AUTH,
               json={"key": "FOCI-PORTFOLIO-1.zzz.0000", "add": ["UEI123"]})
    assert r.status_code == 400
