"""Per-tenant rule tuning.

The weights in `risk/engine.py` are defaults, not law. Once the precision
report shows a rule does not earn its place, an analyst needs to act on that
without waiting for a deploy — and the effect has to be honest: a rule scored
down must lose the severity label it no longer earns, and a retired rule must
not sneak back in through the compound rule.
"""
from __future__ import annotations

import pytest

from foci_screen.models import Contract, Entity, Signal
from foci_screen.risk import engine
from foci_screen.store import Store


def sig(rule_id="IPCOL-01", score=7.0, severity="medium", category="IP_COLLATERAL"):
    return Signal(rule_id=rule_id, category=category, severity=severity, score=score,
                  title="t", rationale="r", evidence="e", source="sec_edgar")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "rules.db"), tenant_id="acme")
    yield s
    s.close()


# ----------------------------------------------------------------- the engine

def test_no_settings_changes_nothing():
    signals = [sig()]
    assert engine.apply_rule_settings(signals, {}) == signals


def test_a_disabled_rule_is_dropped():
    out = engine.apply_rule_settings(
        [sig("IPCOL-01"), sig("FOCI-JURIS-01")],
        {"IPCOL-01": {"enabled": False, "weight": 1.0}})

    assert [s.rule_id for s in out] == ["FOCI-JURIS-01"]


def test_weight_rescales_and_rebands():
    """A signal scored down to 2.0 that still reads 'critical' would be worse
    than not rescaling at all."""
    out = engine.apply_rule_settings(
        [sig(score=20.0, severity="high")],
        {"IPCOL-01": {"enabled": True, "weight": 0.1}})

    assert out[0].score == 2.0
    # 2.0 sits below the "low" threshold of 3.0, so the band is informational.
    assert out[0].severity == engine.band(2.0) == "info"


def test_weight_can_raise_a_band_too():
    out = engine.apply_rule_settings(
        [sig(score=10.0, severity="medium")],
        {"IPCOL-01": {"enabled": True, "weight": 2.0}})

    assert out[0].score == 20.0
    assert out[0].severity == "high"


def test_weight_is_clamped():
    out = engine.apply_rule_settings(
        [sig(score=10.0)], {"IPCOL-01": {"enabled": True, "weight": 99.0}})
    assert out[0].score == 10.0 * engine.MAX_RULE_WEIGHT


def test_the_original_signal_is_not_mutated():
    """Rules are evaluated once and the objects are shared; rescaling in place
    would leak a tenant's tuning into whatever looked at them next."""
    original = sig(score=20.0, severity="high")
    engine.apply_rule_settings([original], {"IPCOL-01": {"enabled": True, "weight": 0.1}})

    assert original.score == 20.0
    assert original.severity == "high"


def test_a_disabled_rule_cannot_compound():
    """The compound rule pairs a foreign nexus with an IP encumbrance. A rule
    an analyst retired must not escalate something through that back door."""
    entity = Entity(name="ACME", uei="U1")
    contracts = [Contract(award_id="N1", piid="N1")]
    # The FOCI half only compounds when it names a jurisdiction — a nexus with
    # no country attached is not half of this pair.
    foci = sig("FOCI-JURIS-01", score=18.0, severity="high", category="FOCI")
    foci.jurisdiction = "Cayman Islands"
    signals = [foci, sig("IPCOL-01", score=18.0, severity="high",
                         category="IP_COLLATERAL")]

    with_both = engine.build_finding(entity, contracts, list(signals))
    kept = engine.apply_rule_settings(signals, {"IPCOL-01": {"enabled": False,
                                                             "weight": 1.0}})
    with_one = engine.build_finding(entity, contracts, kept)

    assert any(s.rule_id.startswith("COMPOUND") for s in with_both.signals)
    assert not any(s.rule_id.startswith("COMPOUND") for s in with_one.signals)


# ------------------------------------------------------------------ the store

def test_setting_round_trips(store):
    store.set_rule_setting("IPCOL-01", enabled=False, weight=0.5,
                           note="fires on every revolver", decided_by="analyst")

    got = store.rule_settings()["IPCOL-01"]
    assert got["enabled"] is False
    assert got["weight"] == 0.5
    assert got["note"] == "fires on every revolver"


def test_clearing_returns_to_the_default(store):
    """Deleting the row, not writing 1.0 — so a later change to the engine
    default takes effect for this tenant."""
    store.set_rule_setting("IPCOL-01", enabled=False)
    store.clear_rule_setting("IPCOL-01")

    assert "IPCOL-01" not in store.rule_settings()


def test_store_clamps_weight(store):
    store.set_rule_setting("R1", weight=12.0)
    store.set_rule_setting("R2", weight=-3.0)

    settings = store.rule_settings()
    assert settings["R1"]["weight"] == 5.0
    assert settings["R2"]["weight"] == 0.0


def test_settings_are_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.set_rule_setting("IPCOL-01", enabled=False)
        assert other.rule_settings() == {}
    finally:
        acme.close()
        other.close()


def test_rules_seen_is_built_from_findings(store):
    from foci_screen.models import Finding

    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="U1"),
        signals=[sig("IPCOL-01"), sig("FOCI-JURIS-01", category="FOCI")],
        total_score=9.0, severity="medium", run_id="r1"))

    seen = store.rules_seen()
    assert seen["IPCOL-01"] == "IP_COLLATERAL"
    assert seen["FOCI-JURIS-01"] == "FOCI"


# ------------------------------------------------------------------ pipeline

def test_a_screen_applies_the_tenant_settings(tmp_path):
    """The whole point: disabling a rule must change the next screen's output.

    Runs `_screen_entity` itself rather than the helper it calls, because the
    wiring between store and engine is exactly what could be missing.
    """
    from foci_screen.pipeline import Screener, ScreenOptions

    entity = Entity(name="ACME", uei="U1")
    # A foreign country of incorporation makes the structural rule fire without
    # needing any documents, so no connectors are involved.
    contracts = [Contract(award_id="N1", piid="N1", recipient_name="ACME",
                          country_of_incorporation="CHN")]

    fired = engine.evaluate_contracts(contracts, entity)
    assert fired, "expected the structural rule to fire on a covered-nation entity"
    rule_id = fired[0].rule_id

    store = Store(str(tmp_path / "p.db"), tenant_id="acme")
    screener = Screener.__new__(Screener)
    screener.store = store
    screener._gather = lambda *a, **k: []          # no documents, no network

    opts = ScreenOptions(agency="DoD", min_severity="info")
    try:
        before = screener._screen_entity(entity, contracts, "r1", opts, lambda *_: None)
        assert rule_id in [s.rule_id for s in before.signals]

        store.set_rule_setting(rule_id, enabled=False)
        after = screener._screen_entity(entity, contracts, "r2", opts, lambda *_: None)
        assert rule_id not in [s.rule_id for s in after.signals]
    finally:
        store.close()


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


def test_tuning_a_rule_over_the_api(client):
    c, _ = client
    r = c.put("/v1/rules/IPCOL-01", headers=AUTH,
              json={"enabled": False, "weight": 0.5, "note": "too noisy"})

    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["decided_by"] == "api-key:acme"


def test_rule_list_merges_precision_and_settings(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.record_disposition(signal_id="aaaa1111", entity_key="U1",
                             rule_id="IPCOL-01", verdict="false_positive")
    store.set_rule_setting("IPCOL-01", enabled=False, weight=0.25)

    rows = {r["rule_id"]: r for r in c.get("/v1/rules", headers=AUTH).json()["rules"]}
    assert rows["IPCOL-01"]["precision"] == 0.0
    assert rows["IPCOL-01"]["enabled"] is False
    assert rows["IPCOL-01"]["weight"] == 0.25
    assert rows["IPCOL-01"]["overridden"] is True


def test_an_untuned_rule_reports_engine_defaults(client):
    c, app_module = client
    app_module.store_for("acme").record_disposition(
        signal_id="bbbb2222", entity_key="U1", rule_id="FOCI-JURIS-01",
        verdict="true_positive")

    rows = {r["rule_id"]: r for r in c.get("/v1/rules", headers=AUTH).json()["rules"]}
    assert rows["FOCI-JURIS-01"]["enabled"] is True
    assert rows["FOCI-JURIS-01"]["weight"] == 1.0
    assert rows["FOCI-JURIS-01"]["overridden"] is False


def test_resetting_a_rule(client):
    c, _ = client
    c.put("/v1/rules/IPCOL-01", headers=AUTH, json={"enabled": False})
    assert c.delete("/v1/rules/IPCOL-01", headers=AUTH).status_code == 204

    rows = {r["rule_id"]: r for r in c.get("/v1/rules", headers=AUTH).json()["rules"]}
    assert rows.get("IPCOL-01", {"overridden": False})["overridden"] is False


def test_api_rejects_an_out_of_range_weight(client):
    c, _ = client
    assert c.put("/v1/rules/IPCOL-01", headers=AUTH,
                 json={"weight": 50}).status_code == 422


def test_precision_route_is_not_shadowed_by_the_rule_id_route(client):
    """GET /v1/rules/precision must not be read as a rule named "precision"."""
    c, _ = client
    assert c.get("/v1/rules/precision", headers=AUTH).status_code == 200


def test_rule_routes_need_a_key(client):
    c, _ = client
    assert c.get("/v1/rules").status_code == 401
    assert c.put("/v1/rules/IPCOL-01", json={"enabled": False}).status_code == 401
