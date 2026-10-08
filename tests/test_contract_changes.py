"""An award's record moving between screens, and what that is worth.

The document side of the tool has always treated a change as stronger evidence
than a standing fact. The contract record had no equivalent: it was written
over in place, so a novation or a change of registered seat read afterwards as
though it had always been so. These cover the detection, the scoring, and the
cases that must *not* fire — a first sighting, and a field the source simply
stopped returning.
"""
from __future__ import annotations

import pytest
from test_search import make_contract

from foci_screen.models import Entity
from foci_screen.risk import engine
from foci_screen.store import Store

ENTITY = Entity(name="ACME DYNAMICS LLC", uei="UEI123")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "changes.db"), tenant_id="acme")
    yield s
    s.close()


def _screen(store, run_id, entity_key="UEI123", **kw):
    """Save one award as a given run would, and return what moved."""
    store.start_run("DoD", {}, run_id=run_id)
    return store.save_contract(make_contract(**kw), run_id, entity_key)


# ----------------------------------------------------------------- detection

def test_a_first_sighting_is_not_a_change(store):
    """A baseline is not a transition. The document side is careful about this
    distinction and the contract side has to be too, or the first screen of
    any agency produces a novation signal for every award in it."""
    assert _screen(store, "r1") == []


def test_an_award_changing_hands_is_recorded(store):
    _screen(store, "r1", recipient_uei="UEI123")
    changes = _screen(store, "r2", entity_key="UEI555", recipient_uei="UEI555",
                      recipient_name="NEWCO HOLDINGS LLC")

    fields = {c["field"]: (c["old"], c["new"]) for c in changes}
    assert fields["entity_key"] == ("UEI123", "UEI555")
    assert fields["recipient_uei"] == ("UEI123", "UEI555")
    assert fields["entity_name"] == ("ACME DYNAMICS LLC", "NEWCO HOLDINGS LLC")


def test_a_change_survives_the_row_being_written_over(store):
    """The point of the table: the contracts row keeps only the new value."""
    _screen(store, "r1", country_of_incorporation="USA")
    _screen(store, "r2", country_of_incorporation="CHN")

    assert store.get_contract("N0001925C0001")["country_of_incorporation"] == "CHN"
    stored = store.contract_changes(entity_key="UEI123")
    assert [(c["field"], c["old_value"], c["new_value"]) for c in stored] == [
        ("country_of_incorporation", "USA", "CHN")]
    assert stored[0]["label"] == "country of incorporation"


def test_a_field_the_source_stopped_returning_is_not_a_change(store):
    """A connector outage is missing data, not a contractor doing something.

    Reporting "country of incorporation: USA -> (blank)" would put an upstream
    failure in front of a reviewer wearing the clothes of a finding.
    """
    _screen(store, "r1", country_of_incorporation="USA")
    changes = _screen(store, "r2", country_of_incorporation="")

    assert [c["field"] for c in changes] == []
    assert store.get_contract("N0001925C0001")["country_of_incorporation"] == "USA"


def test_an_unchanged_re_screen_records_nothing(store):
    _screen(store, "r1")
    assert _screen(store, "r2") == []


# ------------------------------------------------------------------- scoring

def test_a_novation_is_reported_once_not_twice(store):
    """entity_key and recipient_uei move together — one event, one signal."""
    _screen(store, "r1")
    changes = _screen(store, "r2", entity_key="UEI555", recipient_uei="UEI555")

    signals = engine.evaluate_contract_changes(changes, ENTITY, [make_contract()])
    novations = [s for s in signals if s.rule_id == "CHANGE-NOVATION-01"]
    assert len(novations) == 1
    assert novations[0].is_new is True
    assert novations[0].severity in ("medium", "high")


def test_a_move_to_a_covered_nation_outscores_a_move_to_an_ally(store):
    """The jurisdiction has to carry the weight, not the fact of moving."""
    _screen(store, "r1", country_of_incorporation="USA")
    to_china = _screen(store, "r2", country_of_incorporation="CHN")

    other = Store(store.path.replace("changes.db", "other.db"), tenant_id="acme")
    other.start_run("DoD", {}, run_id="r1")
    other.save_contract(make_contract(country_of_incorporation="USA"), "r1", "UEI123")
    other.start_run("DoD", {}, run_id="r2")
    to_canada = other.save_contract(
        make_contract(country_of_incorporation="CAN"), "r2", "UEI123")

    china = engine.evaluate_contract_changes(to_china, ENTITY, [make_contract()])[0]
    canada = engine.evaluate_contract_changes(to_canada, ENTITY, [make_contract()])[0]
    other.close()

    assert china.jurisdiction == "China"
    assert china.score > canada.score
    assert china.rule_id == canada.rule_id == "CHANGE-COUNTRY-01"


def test_newly_self_certified_foreign_ownership_fires(store):
    _screen(store, "r1")
    changes = _screen(store, "r2", foreign_owned_and_located=True)

    signals = engine.evaluate_contract_changes(changes, ENTITY, [make_contract()])
    assert [s.rule_id for s in signals] == ["CHANGE-FOREIGN-OWNED-01"]
    assert signals[0].severity in ("medium", "high", "critical")


def test_the_flag_being_withdrawn_is_recorded_but_does_not_fire(store):
    """A certification being removed is as likely to be data cleanup as news.
    It stays in the log; it does not become a finding."""
    _screen(store, "r1", foreign_owned_and_located=True)
    changes = _screen(store, "r2", foreign_owned_and_located=False)

    assert [c["field"] for c in changes] == ["foreign_owned"]
    assert engine.evaluate_contract_changes(changes, ENTITY, [make_contract()]) == []


def test_an_officer_reassignment_is_logged_without_a_signal(store):
    """Who to notify, not evidence of risk."""
    _screen(store, "r1", ko_email="jane.doe@mail.mil")
    changes = _screen(store, "r2", ko_email="new.officer@mail.mil")

    assert [c["field"] for c in changes] == ["ko_email"]
    assert engine.evaluate_contract_changes(changes, ENTITY, [make_contract()]) == []


def test_changes_are_scoped_to_the_tenant(store, tmp_path):
    _screen(store, "r1")
    _screen(store, "r2", entity_key="UEI555", recipient_uei="UEI555")

    other = Store(str(tmp_path / "changes.db"), tenant_id="other-tenant")
    assert other.contract_changes(entity_key="UEI555") == []
    assert store.contract_changes(entity_key="UEI555")
    other.close()


# ------------------------------------------------- both sides, one event each

def _novate(store, piid, award_id, from_key="UEI123", to_key="UEI555"):
    """Screen one award under `from_key`, then again under `to_key`."""
    _screen(store, "r1", entity_key=from_key, piid=piid, award_id=award_id,
            recipient_uei=from_key)
    _screen(store, "r2", entity_key=to_key, piid=piid, award_id=award_id,
            recipient_uei=to_key, recipient_name="NEWCO HOLDINGS LLC")


def test_a_novation_shows_on_the_contractor_that_lost_the_award(store):
    """Changes are filed under the award's present holder, so the side that
    lost the work used to show nothing at all."""
    _novate(store, "N0001925C0001", "A1")

    lost = store.contract_change_events("UEI123")
    assert [(e["direction"], e["counterparty_key"]) for e in lost] == [
        ("departed", "UEI555")]
    assert lost[0]["counterparty_name"] == "NEWCO HOLDINGS LLC"
    assert lost[0]["contracts"] == ["N0001925C0001"]


def test_the_new_holder_sees_where_the_award_came_from(store):
    _novate(store, "N0001925C0001", "A1")

    gained = store.contract_change_events("UEI555")
    moved = [e for e in gained if e["field"] == "entity_key"]
    assert [(e["direction"], e["counterparty_key"]) for e in moved] == [
        ("arrived", "UEI123")]
    assert moved[0]["counterparty_name"] == "ACME DYNAMICS LLC"
    # The UEI and name that came with the award are the same move, not two
    # more events; the "moved here" row already names the previous holder.
    assert [e["field"] for e in gained] == ["entity_key"]


def test_a_name_change_without_a_move_is_still_its_own_event(store):
    _screen(store, "r1", recipient_name="ACME DYNAMICS LLC")
    _screen(store, "r2", recipient_name="ACME DYNAMICS HOLDINGS LLC")
    assert [e["field"] for e in store.contract_change_events("UEI123")] == ["entity_name"]


def test_the_losing_side_sees_only_the_move_not_the_new_holders_details(store):
    _novate(store, "N0001925C0001", "A1")
    assert [e["field"] for e in store.contract_change_events("UEI123")] == ["entity_key"]


def test_one_re_registration_across_many_awards_is_one_event(store):
    for run_id, country in (("r1", "USA"), ("r2", "CYM")):
        store.start_run("DoD", {}, run_id=run_id)
        for i in range(4):
            store.save_contract(
                make_contract(piid=f"N00019{i}", award_id=f"A{i}",
                              country_of_incorporation=country), run_id, "UEI123")

    events = [e for e in store.contract_change_events("UEI123")
              if e["field"] == "country_of_incorporation"]
    assert len(events) == 1
    assert (events[0]["old_value"], events[0]["new_value"]) == ("USA", "CYM")
    assert events[0]["contract_count"] == 4
    assert sorted(events[0]["contracts"]) == [f"N00019{i}" for i in range(4)]


def test_the_same_change_in_two_screens_is_two_events(store):
    _screen(store, "r1", country_of_incorporation="USA")
    _screen(store, "r2", country_of_incorporation="CYM")
    _screen(store, "r3", country_of_incorporation="USA")

    events = store.contract_change_events("UEI123")
    assert [(e["old_value"], e["new_value"]) for e in events] == [
        ("CYM", "USA"), ("USA", "CYM")]


def test_a_contractor_whose_awards_all_left_still_has_a_page(tmp_path, monkeypatch):
    """No awards and no finding used to mean 404 — for exactly the contractor
    the move matters most to."""
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
    _novate(app_module.store_for("acme"), "N0001925C0001", "A1")
    c = TestClient(app_module.app)
    auth = {"X-API-Key": "secret-key"}

    body = c.get("/v1/entities/UEI123", headers=auth).json()
    assert body["contracts"] == [] and body["contract_count"] == 0
    assert body["entity"]["name"] == "ACME DYNAMICS LLC"
    assert body["record_changes"][0]["direction"] == "departed"

    assert c.get("/v1/entities/NEVERSEEN", headers=auth).status_code == 404
