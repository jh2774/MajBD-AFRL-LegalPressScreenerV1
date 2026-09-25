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
