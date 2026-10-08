"""A watchlist's own screening policy, laid over the tenant's.

The case the roadmap names: a portfolio of high-priority primes notifies on
"low" while a broad agency sweep stays at the tenant's threshold.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from test_policy import finding, sig

from foci_screen import policy as pol
from foci_screen import scheduler
from foci_screen.jobs import raise_notices
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "wl.db"), tenant_id="acme")
    for run_id in ("r1", "r2"):
        s.start_run("DoD", {}, run_id=run_id)
    yield s
    s.close()


# ------------------------------------------------------------------ merging

def test_a_watchlist_changes_only_what_it_names():
    tenant = {"notice_min_severity": "high", "notice_mode": "changes_only"}
    policy, notes = pol.merge(tenant, {"notice_min_severity": "low"})
    assert policy.notice_min_severity == "low"
    assert policy.notice_mode == "changes_only"     # still the tenant's
    assert notes == []


def test_no_override_is_the_tenant_policy():
    tenant = {"notice_min_severity": "high"}
    assert pol.merge(tenant, None)[0] == pol.ScreeningPolicy.from_dict(tenant)[0]


def test_only_named_settings_are_stored_so_later_tenant_changes_flow_through():
    override, _, _ = pol.clean_override({}, {"notice_min_severity": "low"})
    assert override == {"notice_min_severity": "low"}
    # The tenant later switches to changes_only; the watchlist follows.
    policy, _ = pol.merge({"notice_mode": "changes_only"}, override)
    assert (policy.notice_min_severity, policy.notice_mode) == ("low", "changes_only")


def test_an_override_is_normalised_like_a_saved_policy():
    """A watchlist that stops screening FOCI cannot still be notified about it."""
    override, policy, notes = pol.clean_override(
        {}, {"categories": ["STRUCTURE", "SANCTIONS"], "colour": "blue"})
    assert "FOCI" not in policy.notice_categories
    assert any("unknown setting" in n and "colour" in n for n in notes)
    assert "colour" not in override


# ------------------------------------------------------------------ notices

def test_a_low_finding_notifies_under_the_watchlist_but_not_the_tenant(store):
    store.set_screening_policy({"notice_min_severity": "high"})
    priority = store.create_watchlist("priority primes", {"agency": "DoD"},
                                      policy={"notice_min_severity": "low"})
    sweep = store.create_watchlist("agency sweep", {"agency": "DoD"})
    low = finding([sig(severity="low", score=4.0)], severity="low")

    raised, held = raise_notices(store, [low], "r1", watchlist_id=sweep)
    assert raised == [] and any("high threshold" in r for r in held)

    raised, _ = raise_notices(store, [low], "r2", watchlist_id=priority)
    assert len(raised) == 1


def test_a_run_without_a_watchlist_uses_the_tenant_policy(store):
    store.set_screening_policy({"notice_min_severity": "high"})
    raised, _ = raise_notices(store, [finding([sig(severity="low")], severity="low")], "r1")
    assert raised == []


def test_an_unknown_watchlist_falls_back_to_the_tenant(store):
    """A watchlist deleted while its run was queued must not break the run."""
    assert pol.policy_for(store, "gone") == pol.ScreeningPolicy.from_dict(
        store.screening_policy())[0]


# ---------------------------------------------------------------- scheduler

class FakeQueue:
    backend = "rq"
    enqueued: list = []

    def __init__(self, cfg):
        pass

    def enqueue(self, tenant_id, run_id, options):
        FakeQueue.enqueued.append((tenant_id, run_id, options))


def test_a_scheduled_run_knows_which_watchlist_started_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "sched.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    FakeQueue.enqueued = []
    s = Store(str(tmp_path / "sched.db"), tenant_id="acme")
    wid = s.create_watchlist("navy", {"agency": "Department of Defense"})
    s.close()

    assert scheduler.sweep() == 1
    _, _, options = FakeQueue.enqueued[0]
    assert options["watchlist_id"] == wid
    assert options["agency"] == "Department of Defense"


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_a_watchlist_is_created_with_its_own_threshold(client):
    body = client.post("/v1/watchlists", headers=AUTH, json={
        "name": "priority primes", "screen": {"agency": "Department of Defense"},
        "policy": {"notice_min_severity": "low"}}).json()
    assert body["policy_override"] == {"notice_min_severity": "low"}
    assert body["policy"]["notice_min_severity"] == "low"

    listed = client.get("/v1/watchlists", headers=AUTH).json()["watchlists"][0]
    assert listed["policy_override"] == {"notice_min_severity": "low"}
    assert listed["policy"]["notice_mode"] == "first_seen"   # the tenant default


def test_a_watchlist_policy_can_be_replaced_and_cleared(client):
    wid = client.post("/v1/watchlists", headers=AUTH, json={
        "name": "sweep", "screen": {"agency": "Department of Energy"}}).json()["watchlist_id"]

    put = client.put(f"/v1/watchlists/{wid}/policy", headers=AUTH,
                     json={"notice_min_severity": "critical"}).json()
    assert put["policy"]["notice_min_severity"] == "critical"

    cleared = client.delete(f"/v1/watchlists/{wid}/policy", headers=AUTH).json()
    assert cleared["policy_override"] == {}
    assert cleared["policy"]["notice_min_severity"] == "medium"   # tenant default


def test_a_missing_watchlist_is_404(client):
    assert client.put("/v1/watchlists/nope/policy", headers=AUTH,
                      json={"notice_min_severity": "low"}).status_code == 404
    assert client.delete("/v1/watchlists/nope/policy", headers=AUTH).status_code == 404


def test_running_a_watchlist_records_it_on_the_run(client, monkeypatch):
    from foci_screen.api import app as app_module
    sent = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda tenant, run_id, options, **kw: sent.append(options))
    wid = client.post("/v1/watchlists", headers=AUTH, json={
        "name": "navy", "screen": {"agency": "Department of Defense"}}).json()["watchlist_id"]
    client.post(f"/v1/watchlists/{wid}/run", headers=AUTH)
    assert sent[0]["watchlist_id"] == wid


# ------------------------------------------------------- screening, upgrade

def test_screening_categories_follow_the_watchlist(store):
    """Not only notices: what a watchlist screens for is its own too."""
    from foci_screen.pipeline import Screener

    wid = store.create_watchlist("sanctions only", {"agency": "DoD"},
                                 policy={"categories": ["SANCTIONS"]})
    screener = object.__new__(Screener)
    screener.store = store
    screener._watchlist_id, screener._cached_policy = wid, None
    assert screener._policy().categories == ["SANCTIONS"]

    screener._watchlist_id, screener._cached_policy = "", None
    assert "FOCI" in screener._policy().categories


def test_a_database_from_before_watchlist_policies_is_upgraded(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE watchlists (watchlist_id TEXT PRIMARY KEY,"
               " tenant_id TEXT NOT NULL DEFAULT 'default', name TEXT NOT NULL,"
               " params TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
               " last_run_at TEXT, last_run_id TEXT, created_at TEXT NOT NULL)")
    db.execute("INSERT INTO watchlists VALUES ('w1','acme','old','{}',1,NULL,NULL,'x')")
    db.commit()
    db.close()

    s = Store(str(path), tenant_id="acme")
    try:
        assert s.watchlist_policy("w1") is None
        assert s.set_watchlist_policy("w1", {"notice_min_severity": "low"})
        assert s.watchlist_policy("w1") == {"notice_min_severity": "low"}
    finally:
        s.close()


def test_a_paused_watchlist_can_be_resumed(client):
    wid = client.post("/v1/watchlists", headers=AUTH, json={
        "name": "navy", "screen": {"agency": "Department of Defense"}}).json()["watchlist_id"]
    def active():
        rows = client.get("/v1/watchlists", headers=AUTH).json()["watchlists"]
        return bool(rows[0]["active"])

    client.delete(f"/v1/watchlists/{wid}", headers=AUTH)
    assert active() is False

    assert client.post(f"/v1/watchlists/{wid}/resume", headers=AUTH).json()["active"] is True
    assert active() is True
    assert client.post("/v1/watchlists/nope/resume", headers=AUTH).status_code == 404
