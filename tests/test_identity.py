"""Which SEC registrant a contractor is, and who gets to decide.

This is the highest-consequence mapping in the tool. EDGAR full-text search is
constrained by CIK, so a wrong answer does not come back empty — it comes back
with another registrant's exhibits attached to this contractor's name. Name
similarity is rerun every screen and can land differently as the ticker file
changes, so the answer is stored, and a human verdict outranks the matcher
permanently in both directions.
"""
from __future__ import annotations

import pytest

from foci_screen.models import Contract
from foci_screen.pipeline import Screener
from foci_screen.store import Store

LOCKHEED = "0000936468"
LEIDOS = "0001336920"


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "identity.db"), tenant_id="acme")
    yield s
    s.close()


class StubEdgar:
    """Stands in for the name matcher; records what it was asked."""

    def __init__(self, cik=LOCKHEED, title="LOCKHEED MARTIN CORP", score=0.97):
        self.result = (cik, title, score)
        self.calls: list[str] = []

    def resolve_cik_scored(self, name):
        self.calls.append(name)
        return self.result


def screener_with(store, edgar):
    s = Screener.__new__(Screener)
    s.store = store
    s.edgar = edgar
    return s


def contract(uei="G4KDGE4JFFK7", parent=""):
    return Contract(award_id="N1", piid="N1", recipient_uei=uei,
                    recipient_name="LOCKHEED MARTIN CORPORATION",
                    parent_recipient_name=parent)


# ------------------------------------------------------------- first resolution

def test_first_screen_resolves_and_remembers(store):
    edgar = StubEdgar()
    cik, title = screener_with(store, edgar)._resolve_identity(
        "G4KDGE4JFFK7", "LOCKHEED MARTIN CORPORATION", contract())

    assert cik == LOCKHEED
    link = store.get_entity_link("G4KDGE4JFFK7")
    assert link["status"] == "auto"
    assert link["cik"] == LOCKHEED
    assert link["confidence"] == 0.97
    assert title == "LOCKHEED MARTIN CORP"


def test_the_parent_name_is_what_gets_matched(store):
    """A subsidiary files under its parent; matching the operating name finds
    nothing or, worse, something else."""
    edgar = StubEdgar()
    screener_with(store, edgar)._resolve_identity(
        "U1", "SIKORSKY AIRCRAFT", contract(parent="LOCKHEED MARTIN CORP"))

    assert edgar.calls == ["LOCKHEED MARTIN CORP"]


def test_a_near_miss_is_recorded_even_when_rejected(store):
    """The review queue needs to show how close the nearest registrant was, not
    merely that nothing matched."""
    edgar = StubEdgar(cik="", title="LOCKHEED MARTIN CORP", score=0.71)
    cik, _ = screener_with(store, edgar)._resolve_identity("U1", "ACME", contract("U1"))

    assert cik == ""
    link = store.get_entity_link("U1")
    assert link["confidence"] == 0.71
    assert link["matched_title"] == "LOCKHEED MARTIN CORP"


# ----------------------------------------------------------- human decisions

def test_a_confirmed_mapping_is_not_re_resolved(store):
    """The matcher must not get a second opinion after a person has decided."""
    store.set_entity_link("U1", status="confirmed", cik=LOCKHEED,
                          matched_title="LOCKHEED MARTIN CORP", decided_by="analyst")
    edgar = StubEdgar(cik=LEIDOS, title="LEIDOS HOLDINGS INC")

    cik, title = screener_with(store, edgar)._resolve_identity("U1", "ACME", contract("U1"))

    assert cik == LOCKHEED, "a reviewer's answer outranks a similarity score"
    assert title == "LOCKHEED MARTIN CORP"
    assert edgar.calls == [], "no lookup should even be attempted"


def test_a_rejected_mapping_stays_rejected(store):
    """Re-deciding by similarity would reintroduce the misattribution the
    reviewer just removed."""
    store.set_entity_link("U1", status="rejected", note="different company, same name",
                          decided_by="analyst")
    edgar = StubEdgar()

    cik, title = screener_with(store, edgar)._resolve_identity("U1", "ACME", contract("U1"))

    assert (cik, title) == ("", "")
    assert edgar.calls == []


def test_rejecting_clears_any_cik(store):
    store.record_auto_link("U1", cik=LOCKHEED, matched_title="LOCKHEED MARTIN CORP",
                           confidence=0.9)
    link = store.set_entity_link("U1", status="rejected", decided_by="analyst")

    assert link["cik"] == ""
    assert link["status"] == "rejected"


def test_a_later_run_cannot_overwrite_a_human_decision(store):
    for status in ("confirmed", "rejected"):
        store.set_entity_link("U1", status=status, cik=LOCKHEED, decided_by="analyst")
        store.record_auto_link("U1", cik=LEIDOS, matched_title="LEIDOS HOLDINGS INC",
                               confidence=0.99)

        link = store.get_entity_link("U1")
        assert link["status"] == status
        assert link["cik"] != LEIDOS


def test_auto_links_do_update_each_other(store):
    """An unreviewed mapping should track the matcher as data improves."""
    store.record_auto_link("U1", cik=LEIDOS, matched_title="LEIDOS", confidence=0.88)
    store.record_auto_link("U1", cik=LOCKHEED, matched_title="LOCKHEED MARTIN CORP",
                           confidence=0.97)

    assert store.get_entity_link("U1")["cik"] == LOCKHEED


def test_unknown_status_is_refused(store):
    with pytest.raises(ValueError):
        store.set_entity_link("U1", status="probably")


def test_review_queue_is_least_confident_first(store):
    store.record_auto_link("SURE", cik=LOCKHEED, confidence=0.99)
    store.record_auto_link("SHAKY", cik=LEIDOS, confidence=0.87)

    assert [r["entity_key"] for r in store.entity_links(status="auto")] == ["SHAKY", "SURE"]


def test_links_are_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.set_entity_link("U1", status="confirmed", cik=LOCKHEED)
        assert other.get_entity_link("U1") is None
    finally:
        acme.close()
        other.close()


# ----------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_confirming_over_the_api(client):
    c, app_module = client
    app_module.store_for("acme").record_auto_link(
        "U1", cik=LOCKHEED, matched_title="LOCKHEED MARTIN CORP", confidence=0.97)

    r = c.post("/v1/entities/U1/identity", headers=AUTH,
               json={"status": "confirmed", "note": "checked the 10-K cover page"})
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"
    assert r.json()["decided_by"] == "api-key:acme"


def test_confirming_without_a_cik_is_refused(client):
    """"Confirmed" with nothing to attribute to is a contradiction."""
    c, _ = client
    r = c.post("/v1/entities/UNKNOWN/identity", headers=AUTH, json={"status": "confirmed"})
    assert r.status_code == 422
    assert "needs a CIK" in r.json()["detail"]


def test_a_malformed_cik_is_refused(client):
    c, _ = client
    for bad in ("936468", "not-a-cik", "00009364680"):
        r = c.post("/v1/entities/U1/identity", headers=AUTH,
                   json={"status": "confirmed", "cik": bad})
        assert r.status_code == 422, bad


def test_correcting_to_a_different_registrant(client):
    c, app_module = client
    app_module.store_for("acme").record_auto_link("U1", cik=LOCKHEED, confidence=0.87)

    r = c.post("/v1/entities/U1/identity", headers=AUTH,
               json={"status": "confirmed", "cik": LEIDOS,
                     "matched_title": "LEIDOS HOLDINGS, INC.",
                     "note": "the matcher had the wrong prime"})

    assert r.json()["cik"] == LEIDOS
    assert r.json()["matched_title"] == "LEIDOS HOLDINGS, INC."


def test_identity_review_queue_endpoint(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.record_auto_link("SHAKY", cik=LEIDOS, confidence=0.87)
    store.set_entity_link("SETTLED", status="confirmed", cik=LOCKHEED)

    unreviewed = c.get("/v1/identity?status=auto", headers=AUTH).json()["links"]
    assert [link["entity_key"] for link in unreviewed] == ["SHAKY"]


def test_unknown_entity_identity_is_404(client):
    c, _ = client
    assert c.get("/v1/entities/NOBODY/identity", headers=AUTH).status_code == 404


def test_identity_routes_need_a_key(client):
    c, _ = client
    assert c.get("/v1/identity").status_code == 401
    assert c.post("/v1/entities/U1/identity", json={"status": "rejected"}).status_code == 401
