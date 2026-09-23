"""Reviewer verdicts on individual signals, and the precision they measure.

Rule weights in this tool were set by judgement and have never been checked
against an outcome. A verdict is one labelled example; `rule_precision` is what
they add up to, and the only basis on which a rule can be retired.
"""
from __future__ import annotations

import pytest

from foci_screen.models import Signal, signal_key
from foci_screen.store import Store


def sig(rule_id="FOCI-JURIS-01", evidence="Cayman Islands investor group",
        severity="high", category="FOCI"):
    return Signal(rule_id=rule_id, category=category, severity=severity, score=6.0,
                  title="t", rationale="r", evidence=evidence, source="web")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "disp.db"), tenant_id="acme")
    yield s
    s.close()


# ------------------------------------------------------------- signal identity

def test_signal_id_is_stable_across_runs():
    """The same rule on the same text must key the same, or yesterday's verdict
    does not apply to today's finding."""
    assert sig().key() == sig().key()


def test_signal_id_ignores_whitespace_reflow():
    """Evidence is re-extracted each run and can rewrap."""
    assert sig(evidence="Cayman  Islands\ninvestor group").key() == sig().key()


def test_signal_id_separates_rules_and_evidence():
    assert sig().key() != sig(rule_id="IPCOL-01").key()
    assert sig().key() != sig(evidence="Something else entirely").key()


def test_signal_id_travels_in_the_payload():
    assert sig().to_dict()["signal_id"] == sig().key()


def test_findings_stored_before_signal_ids_existed_get_one_on_read(store):
    """A verdict must be recordable against a finding written by an older build."""
    import json

    payload = {"entity": {"name": "ACME", "uei": "U1"}, "severity": "high",
               "signals": [{"rule_id": "FOCI-JURIS-01", "evidence": "Cayman Islands",
                            "severity": "high"}]}
    with store._tx() as c:
        c.execute("INSERT INTO findings (run_id, tenant_id, entity_key, entity_name,"
                  " severity, score, payload, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)",
                  ("r1", "acme", "U1", "ACME", "high", 9.0, json.dumps(payload),
                   "2026-01-01T00:00:00Z"))

    got = store.search_findings(entity_key="U1")[0]
    assert got["signals"][0]["signal_id"] == signal_key("FOCI-JURIS-01", "Cayman Islands")


# ------------------------------------------------------------------ recording

def test_verdict_round_trips(store):
    store.record_disposition(signal_id="abc123", entity_key="u1", rule_id="FOCI-JURIS-01",
                             verdict="false_positive", note="missile range, not an investor",
                             decided_by="analyst@org", category="FOCI", severity="high")

    got = store.dispositions_for_entity("U1")["abc123"]
    assert got["verdict"] == "false_positive"
    assert got["note"] == "missile range, not an investor"
    assert got["entity_key"] == "U1", "entity keys are normalised"


def test_a_reviewer_can_change_their_mind(store):
    """Unlike a notice decision, a verdict is a judgement, not an action."""
    for verdict in ("true_positive", "false_positive"):
        store.record_disposition(signal_id="abc", entity_key="U1", rule_id="R1",
                                 verdict=verdict)

    rows = store.dispositions_for_entity("U1")
    assert len(rows) == 1
    assert rows["abc"]["verdict"] == "false_positive"


def test_same_signal_on_two_entities_is_two_verdicts(store):
    """A rule can be right about one company and wrong about another."""
    for entity in ("U1", "U2"):
        store.record_disposition(signal_id="abc", entity_key=entity, rule_id="R1",
                                 verdict="true_positive")

    assert len(store.dispositions_for_entity("U1")) == 1
    assert len(store.dispositions_for_entity("U2")) == 1
    assert store.rule_precision()[0]["true_positive"] == 2


def test_unknown_verdict_is_refused(store):
    with pytest.raises(ValueError):
        store.record_disposition(signal_id="a", entity_key="U1", rule_id="R1",
                                 verdict="probably_fine")


def test_verdicts_are_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.record_disposition(signal_id="a", entity_key="U1", rule_id="R1",
                                verdict="true_positive")
        assert other.dispositions_for_entity("U1") == {}
        assert other.rule_precision() == []
    finally:
        acme.close()
        other.close()


# ------------------------------------------------------------------ precision

def test_precision_counts_only_decided_verdicts(store):
    """"Unclear" is recorded but kept out of the denominator: a reviewer who
    could not tell has not said the rule was wrong."""
    for i, verdict in enumerate(["true_positive", "true_positive", "true_positive",
                                 "false_positive", "unclear", "unclear"]):
        store.record_disposition(signal_id=f"s{i}", entity_key="U1", rule_id="R1",
                                 verdict=verdict)

    row = store.rule_precision()[0]
    assert row["true_positive"] == 3
    assert row["false_positive"] == 1
    assert row["unclear"] == 2
    assert row["precision"] == 0.75          # 3 of 4 decided, not 3 of 6
    assert row["reviewed"] == 6


def test_a_rule_with_only_unclear_verdicts_has_no_precision(store):
    store.record_disposition(signal_id="s1", entity_key="U1", rule_id="R1",
                             verdict="unclear")
    assert store.rule_precision()[0]["precision"] is None


def test_worst_rules_come_first(store):
    store.record_disposition(signal_id="a", entity_key="U1", rule_id="GOOD",
                             verdict="true_positive")
    store.record_disposition(signal_id="b", entity_key="U1", rule_id="BAD",
                             verdict="false_positive")
    store.record_disposition(signal_id="c", entity_key="U1", rule_id="UNJUDGED",
                             verdict="unclear")

    order = [r["rule_id"] for r in store.rule_precision()]
    assert order[0] == "BAD"          # 0% precision
    assert order[-1] == "UNJUDGED"    # unmeasured sorts last, not first


def test_no_verdicts_is_an_empty_report(store):
    assert store.rule_precision() == []


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


def _post(client, **kw):
    body = {"signal_id": "abc123", "entity_key": "U1", "rule_id": "FOCI-JURIS-01",
            "verdict": "false_positive"}
    body.update(kw)
    return client.post("/v1/dispositions", headers=AUTH, json=body)


def test_recording_a_verdict_over_the_api(client):
    c, _ = client
    assert _post(c).status_code == 201

    body = c.get("/v1/entities/U1/dispositions", headers=AUTH).json()
    assert body["dispositions"]["abc123"]["verdict"] == "false_positive"


def test_verdict_is_attributed_to_the_key_when_unnamed(client):
    c, _ = client
    _post(c)
    got = c.get("/v1/entities/U1/dispositions", headers=AUTH).json()
    assert got["dispositions"]["abc123"]["decided_by"] == "api-key:acme"


def test_api_rejects_an_invented_verdict(client):
    c, _ = client
    assert _post(c, verdict="looks_fine").status_code == 422


def test_api_rejects_an_implausible_signal_id(client):
    """Guards against a caller inventing keys that could never collide with a
    real signal, which would quietly produce precision figures about nothing."""
    c, _ = client
    assert _post(c, signal_id="s1").status_code == 422


def test_precision_endpoint(client):
    c, _ = client
    # Real signal ids are 16 hex characters; the schema enforces a minimum
    # length, so two-character stand-ins are rejected before they are stored.
    assert _post(c, signal_id="aaaa1111", verdict="true_positive").status_code == 201
    assert _post(c, signal_id="bbbb2222", verdict="false_positive").status_code == 201
    assert _post(c, signal_id="cccc3333", verdict="true_positive",
                 rule_id="IPCOL-01").status_code == 201

    body = c.get("/v1/rules/precision", headers=AUTH).json()
    rules = {r["rule_id"]: r for r in body["rules"]}

    assert rules["FOCI-JURIS-01"]["precision"] == 0.5
    assert rules["IPCOL-01"]["precision"] == 1.0
    assert body["totals"]["verdicts"] == 3
    assert body["totals"]["overall_precision"] == round(2 / 3, 3)


def test_precision_endpoint_with_no_data(client):
    c, _ = client
    body = c.get("/v1/rules/precision", headers=AUTH).json()
    assert body["rules"] == []
    assert body["totals"]["overall_precision"] is None


def test_disposition_routes_need_a_key(client):
    c, _ = client
    assert c.post("/v1/dispositions", json={}).status_code == 401
    assert c.get("/v1/rules/precision").status_code == 401
