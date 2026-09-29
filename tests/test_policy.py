"""Choosing what to screen for, and what raises a notice.

The defect underneath this feature: a notice had no memory. Every finding at
medium or above queued a draft on every run, so a contractor on a nightly
watchlist with a standing finding sent the same draft to the same contracting
officer every night — the fastest way to teach a reviewer to approve without
reading. Most of what follows pins the fix; the rest pins the choices.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_search import make_contract

from foci_screen import policy as pol
from foci_screen.jobs import raise_notices
from foci_screen.models import Document, Entity, Finding, Signal
from foci_screen.risk import engine
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


def sig(rule_id="FOCI-JURIS-02", category="FOCI", severity="medium", score=12.0,
        evidence="shareholder domiciled in a covered jurisdiction", is_new=False):
    return Signal(rule_id=rule_id, category=category, severity=severity,
                  score=score, title=rule_id, rationale="r", evidence=evidence,
                  source="test", is_new=is_new)


def finding(signals, severity="medium", uei="UEI123", run_id="r1"):
    return Finding(run_id=run_id, entity=Entity(name="ACME DYNAMICS LLC", uei=uei),
                   signals=signals, contracts=[make_contract()], severity=severity,
                   total_score=sum(s.score for s in signals))


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "policy.db"), tenant_id="acme")
    for run_id in ("r1", "r2", "r3"):
        s.start_run("DoD", {}, run_id=run_id)
    yield s
    s.close()


# ----------------------------------------------------------------- defaults

def test_the_defaults_keep_the_old_threshold():
    p = pol.ScreeningPolicy()
    assert p.notice_min_severity == "medium"
    assert p.notice_mode == "first_seen"
    assert set(p.categories) == set(pol.CATEGORIES)
    assert "DISCLOSURE" not in p.notice_categories, \
        "disclosures score zero; they reach the queue only through an explicit event"
    assert p.notice_always == []


# ------------------------------------------------------------- normalising

def test_unknown_values_are_dropped_and_reported():
    p, notes = pol.ScreeningPolicy.from_dict(
        {"categories": ["FOCI", "ASTROLOGY"], "notice_always": ["novation", "comet"]})
    assert p.categories == ["FOCI"]
    assert p.notice_always == []            # novation needs STRUCTURE, which is off
    assert any("ASTROLOGY" in n for n in notes)
    assert any("comet" in n for n in notes)


def test_category_names_are_case_insensitive():
    p, _ = pol.ScreeningPolicy.from_dict({"categories": ["foci", "Sanctions"]})
    assert p.categories == ["FOCI", "SANCTIONS"]


def test_info_is_not_a_notice_threshold():
    """A notice about something the engine scores as informational has
    nothing in it for a contracting officer to act on."""
    p, notes = pol.ScreeningPolicy.from_dict({"notice_min_severity": "info"})
    assert p.notice_min_severity == "medium"
    assert notes


def test_you_cannot_be_notified_about_what_is_not_screened():
    p, notes = pol.ScreeningPolicy.from_dict(
        {"categories": ["FOCI"], "notice_categories": ["FOCI", "SANCTIONS"]})
    assert p.notice_categories == ["FOCI"]
    assert any("SANCTIONS" in n and "not screened" in n for n in notes)


def test_an_always_event_with_its_source_switched_off_is_dropped():
    p, notes = pol.ScreeningPolicy.from_dict(
        {"release_kinds": ["press"], "notice_always": ["legal_release"]})
    assert p.notice_always == []
    assert any("legal_release" in n for n in notes)


def test_a_consistent_policy_produces_no_notes():
    _, notes = pol.ScreeningPolicy.from_dict(pol.ScreeningPolicy().to_dict())
    assert notes == []


# --------------------------------------------------------------- screening

def test_switched_off_categories_are_filtered():
    p, _ = pol.ScreeningPolicy.from_dict({"categories": ["SANCTIONS"]})
    kept = pol.filter_signals([sig(), sig("SANCTION-01", "SANCTIONS")], p)
    assert [s.rule_id for s in kept] == ["SANCTION-01"]


def test_a_release_of_an_unread_kind_is_not_wanted():
    p, _ = pol.ScreeningPolicy.from_dict({"release_kinds": ["legal"]})
    press = Document(source="web", key="feed:x", meta={"release_kind": "press"})
    legal = Document(source="web", key="feed:y", meta={"release_kind": "legal"})
    filing = Document(source="sec_edgar", key="0001:abc")
    assert pol.wants_document(legal, p)
    assert not pol.wants_document(press, p)
    assert pol.wants_document(filing, p), "only releases are filtered by kind"


# ----------------------------------------------------------------- notices

def test_a_finding_notifies_once_then_stops():
    """The nightly duplicate, reduced to its two lines."""
    p = pol.ScreeningPolicy()
    s = sig()
    first = pol.decide_notice("medium", [s], p, already_notified=set())
    again = pol.decide_notice("medium", [s], p, already_notified={s.key()})
    assert first.raise_notice
    assert not again.raise_notice
    assert "already" in again.reason


def test_new_evidence_on_an_old_finding_notifies_again():
    p = pol.ScreeningPolicy()
    old, new = sig(), sig(evidence="a second, different disclosure")
    d = pol.decide_notice("medium", [old, new], p, already_notified={old.key()})
    assert d.raise_notice
    assert d.signal_ids == [new.key()]


def test_below_the_threshold_is_held():
    d = pol.decide_notice("low", [sig(severity="low", score=4)],
                          pol.ScreeningPolicy(), set())
    assert not d.raise_notice
    assert "below the medium threshold" in d.reason


def test_a_lower_threshold_lets_low_findings_through():
    p, _ = pol.ScreeningPolicy.from_dict({"notice_min_severity": "low"})
    assert pol.decide_notice("low", [sig(severity="low", score=4)], p, set()).raise_notice


def test_changes_only_ignores_standing_evidence():
    p, _ = pol.ScreeningPolicy.from_dict({"notice_mode": "changes_only"})
    held = pol.decide_notice("medium", [sig(is_new=False)], p, set())
    raised = pol.decide_notice("medium", [sig(is_new=True)], p, set())
    assert not held.raise_notice and "changed" in held.reason
    assert raised.raise_notice


def test_a_category_set_not_to_notify_is_held():
    p, _ = pol.ScreeningPolicy.from_dict({"notice_categories": ["SANCTIONS"]})
    d = pol.decide_notice("medium", [sig()], p, set())
    assert not d.raise_notice
    assert "set not to raise notices" in d.reason


def test_an_always_event_ignores_the_threshold():
    """A new legal disclosure scores zero on purpose, and still notifies when
    that has been asked for."""
    p, _ = pol.ScreeningPolicy.from_dict({"notice_always": ["legal_release"]})
    release = sig("RELEASE-LEGAL-01", "DISCLOSURE", "info", 0.0,
                  evidence="Meridian settles False Claims Act matter", is_new=True)
    d = pol.decide_notice("info", [release], p, set())
    assert d.raise_notice
    assert d.reason.startswith("Always notify")


def test_a_release_backlog_is_not_news():
    """On a company's first screen every release reads as unseen; notifying on
    each would bury the reviewer in a year of announcements."""
    p, _ = pol.ScreeningPolicy.from_dict({"notice_always": ["legal_release"]})
    backlog = sig("RELEASE-LEGAL-01", "DISCLOSURE", "info", 0.0, is_new=False)
    assert not pol.decide_notice("info", [backlog], p, set()).raise_notice


def test_a_sanctions_match_notifies_whenever_it_is_first_found():
    p, _ = pol.ScreeningPolicy.from_dict({"notice_always": ["sanctions_match"]})
    match = sig("SANCTION-01", "SANCTIONS", "low", 4.0, is_new=False)
    assert pol.decide_notice("low", [match], p, set()).raise_notice


def test_always_is_still_once():
    p, _ = pol.ScreeningPolicy.from_dict({"notice_always": ["novation"]})
    novation = sig("CHANGE-NOVATION-01", "STRUCTURE", "medium", 9.6, is_new=True)
    assert not pol.decide_notice("medium", [novation], p,
                                 {novation.key()}).raise_notice


def test_the_decision_works_on_stored_payloads_too():
    """The preview runs over history, where signals are dicts."""
    s = sig()
    payload = {**s.to_dict()}
    assert pol.decide_notice("medium", [payload], pol.ScreeningPolicy(), set()).raise_notice
    assert not pol.decide_notice("medium", [payload], pol.ScreeningPolicy(),
                                 {payload["signal_id"]}).raise_notice


# ---------------------------------------------------- the release disclosure

def _release(kind):
    return Document(source="web", key=f"feed:x/{kind}", title=f"ACME — ACME {kind} news",
                    url="https://acme.example/news", text="text " * 40,
                    doc_type=f"{kind}_release", meta={"release_kind": kind})


@pytest.mark.parametrize("kind,rule", [("legal", "RELEASE-LEGAL-01"),
                                       ("financial", "RELEASE-FINANCIAL-01")])
def test_a_new_release_becomes_a_zero_weight_disclosure(kind, rule):
    ctx = engine.RuleContext(entity=Entity(name="ACME"), contracts=[], is_new=True)
    [s] = engine.rule_release_disclosure(_release(kind), ctx)
    assert s.rule_id == rule
    assert s.score == 0.0 and s.severity == "info"
    assert s.category == "DISCLOSURE"


def test_an_unchanged_release_or_a_press_release_produces_nothing():
    ctx_old = engine.RuleContext(entity=Entity(name="ACME"), contracts=[], is_new=False)
    ctx_new = engine.RuleContext(entity=Entity(name="ACME"), contracts=[], is_new=True)
    assert engine.rule_release_disclosure(_release("legal"), ctx_old) == []
    assert engine.rule_release_disclosure(_release("press"), ctx_new) == []


def test_a_disclosure_never_raises_a_findings_severity():
    """Quarterly results every quarter must not inflate risk on a calendar."""
    base = [sig(severity="low", score=4.0)]
    disclosure = sig("RELEASE-FINANCIAL-01", "DISCLOSURE", "info", 0.0,
                     evidence="Q3 results", is_new=True)
    without = engine.build_finding(Entity(name="A"), [], list(base))
    with_it = engine.build_finding(Entity(name="A"), [], base + [disclosure])
    assert without.severity == with_it.severity
    assert without.total_score == with_it.total_score


# ------------------------------------------------------------------- store

def test_the_policy_is_stored_per_tenant(store, tmp_path):
    store.set_screening_policy({"notice_min_severity": "high"})
    assert store.screening_policy()["notice_min_severity"] == "high"

    other = Store(str(tmp_path / "policy.db"), tenant_id="someone-else")
    assert other.screening_policy() == {}
    other.close()

    store.clear_screening_policy()
    assert store.screening_policy() == {}


def test_a_rejected_notice_counts_as_said(store):
    """A reviewer's "no" is the only labelled false positive this tool gets.
    The next screen must not overrule it."""
    f = finding([sig()])
    [notice_id], _ = raise_notices(store, [f], "r1")
    store.decide_notice(notice_id, "rejected", decided_by="reviewer", note="not FOCI")

    again, held = raise_notices(store, [finding([sig()], run_id="r2")], "r2")
    assert again == []
    assert any("already" in reason for reason in held)


# ---------------------------------------------------------- the whole gate

def test_an_unchanged_finding_does_not_queue_a_notice_every_night(store):
    """The regression this feature exists for."""
    first, _ = raise_notices(store, [finding([sig()], run_id="r1")], "r1")
    second, _ = raise_notices(store, [finding([sig()], run_id="r2")], "r2")
    third, _ = raise_notices(store, [finding([sig()], run_id="r3")], "r3")

    assert len(first) == 1
    assert second == [] and third == []
    assert len(store.list_notices(status="pending")) == 1


def test_new_evidence_supersedes_the_pending_draft(store):
    """One pending notice per contractor, carrying the current finding."""
    [old], _ = raise_notices(store, [finding([sig()], run_id="r1")], "r1")
    [new], _ = raise_notices(
        store, [finding([sig(), sig(evidence="a newly filed 8-K")], run_id="r2")], "r2")

    assert store.get_notice(old)["status"] == "superseded"
    assert store.get_notice(new)["status"] == "pending"
    assert "Superseded by" in store.get_notice(old)["decision_note"]


def test_a_decided_notice_is_never_superseded(store):
    [old], _ = raise_notices(store, [finding([sig()], run_id="r1")], "r1")
    store.decide_notice(old, "approved", decided_by="reviewer")
    raise_notices(store, [finding([sig(), sig(evidence="new")], run_id="r2")], "r2")
    assert store.get_notice(old)["status"] == "approved"


def test_the_notice_says_why_it_was_raised(store):
    [n], _ = raise_notices(store, [finding([sig()])], "r1")
    assert "FOCI-JURIS-02" in store.get_notice(n)["trigger_reason"]


def test_held_findings_are_counted_by_reason(store):
    store.set_screening_policy({"notice_min_severity": "high"})
    raised, held = raise_notices(store, [finding([sig()])], "r1")
    assert raised == []
    assert list(held.values()) == [1]
    assert "below the high threshold" in next(iter(held))


def test_notices_from_before_the_upgrade_are_remembered(store):
    """A notice written before `signal_ids` existed has none recorded. Without
    the fallback, the first screen after deploying this would re-notify
    everything that had ever been notified."""
    f = finding([sig()], run_id="r1")
    store.save_finding(f)
    store.create_notice(run_id="r1", entity_key=f.entity.key(), entity_name="ACME",
                        severity="medium", recipient="x@mail.mil",
                        officer_confidence="high", subject="s", body_text="b")
    # Simulate the legacy row: no signal ids recorded.
    with store._tx() as c:
        c.execute("UPDATE notices SET signal_ids=NULL")

    assert sig().key() in store.notified_signal_ids(f.entity.key())
    again, _ = raise_notices(store, [finding([sig()], run_id="r2")], "r2")
    assert again == []


def test_an_existing_database_gains_the_new_columns(tmp_path):
    """Their live database already has notices. The columns must arrive by
    migration, not only on a fresh schema."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE notices (notice_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL"
        " DEFAULT 'default', run_id TEXT NOT NULL, finding_id TEXT, entity_key TEXT"
        " NOT NULL, entity_name TEXT NOT NULL, severity TEXT NOT NULL, recipient"
        " TEXT, officer_confidence TEXT, subject TEXT NOT NULL, body_text TEXT NOT"
        " NULL, original_body_text TEXT, status TEXT NOT NULL, decided_by TEXT,"
        " decided_at TEXT, decision_note TEXT, created_at TEXT NOT NULL)")
    conn.commit()
    conn.close()

    s = Store(str(path), tenant_id="acme")
    columns = {r["name"] for r in s._query("PRAGMA table_info(notices)")}
    assert {"signal_ids", "trigger_reason"} <= columns
    s.close()


# --------------------------------------------------------------------- API

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
    return TestClient(app_module.app), app_module


def test_the_defaults_come_with_their_catalogue(client):
    c, _ = client
    body = c.get("/v1/policy", headers=AUTH).json()
    assert body["is_default"] is True
    assert body["policy"]["notice_min_severity"] == "medium"
    assert "novation" in body["catalogue"]["events"]
    assert "legal" in body["catalogue"]["release_kinds"]


def test_saving_returns_what_was_corrected(client):
    c, _ = client
    body = c.put("/v1/policy", headers=AUTH, json={
        "categories": ["FOCI"], "notice_categories": ["FOCI", "SANCTIONS"]}).json()
    assert body["policy"]["notice_categories"] == ["FOCI"]
    assert body["notes"]
    assert c.get("/v1/policy", headers=AUTH).json()["is_default"] is False


def test_reset_returns_to_the_defaults(client):
    c, _ = client
    c.put("/v1/policy", headers=AUTH, json={"notice_min_severity": "high"})
    assert c.delete("/v1/policy", headers=AUTH).status_code == 204
    assert c.get("/v1/policy", headers=AUTH).json()["is_default"] is True


def test_the_preview_queues_nothing(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_finding(finding([sig()]))

    r = c.post("/v1/policy/preview", headers=AUTH, json={}).json()
    assert r["contractors"] == 1 and r["would_raise"] == 1
    assert store.list_notices() == [], "a preview must not write a notice"

    stricter = c.post("/v1/policy/preview", headers=AUTH,
                      json={"notice_min_severity": "critical"}).json()
    assert stricter["would_raise"] == 0
    assert stricter["held"][0]["count"] == 1


def test_superseded_notices_can_be_listed(client):
    """Housekeeping, not a decision — but still part of the record."""
    c, app_module = client
    store = app_module.store_for("acme")
    for run_id in ("r1", "r2"):
        store.start_run("DoD", {}, run_id=run_id)
    raise_notices(store, [finding([sig()], run_id="r1")], "r1")
    raise_notices(store, [finding([sig(), sig(evidence="new 8-K")], run_id="r2")], "r2")

    r = c.get("/v1/notices?status=superseded", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()["notices"]) == 1
    pending = c.get("/v1/notices?status=pending", headers=AUTH).json()["notices"]
    assert len(pending) == 1
    assert "Why" not in pending[0].get("trigger_reason", "")
    assert pending[0]["trigger_reason"], "the reason reaches the API"


def test_policy_routes_need_a_key(client):
    c, _ = client
    assert c.get("/v1/policy").status_code == 401
    assert c.put("/v1/policy", json={}).status_code == 401
    assert c.post("/v1/policy/preview", json={}).status_code == 401
