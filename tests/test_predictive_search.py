"""Finding things the way people actually type them.

Three habits the old matching could not serve, each of which returned nothing
and so was indistinguishable from the thing not existing:

  * a contracting officer typed surname first,
  * an agency typed as an abbreviation,
  * a company typed as a prefix, ranked behind a larger firm that merely
    contained the same letters.
"""
from __future__ import annotations

import pytest
from test_search import make_contract

from foci_screen.aliases import expand_agency, tokens
from foci_screen.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "predict.db"), tenant_id="acme")
    s.start_run("DoD", {}, run_id="r1")

    s.save_contract(make_contract(
        piid="N001", award_id="A1", recipient_name="LOCKHEED MARTIN CORPORATION",
        recipient_uei="UEILMT", awarding_agency="Department of Defense",
        awarding_sub_agency="Department of the Navy", award_amount=50_000_000.0,
        ko_name="Jane Doe", ko_email="jane.doe@mail.mil"), "r1", "UEILMT")
    s.save_contract(make_contract(
        piid="N002", award_id="A2", recipient_name="UNLOCK SYSTEMS HOLDINGS LLC",
        recipient_uei="UEIUNL", awarding_agency="Department of Defense",
        awarding_sub_agency="Defense Advanced Research Projects Agency",
        award_amount=90_000_000.0,
        ko_name="Robert Van Der Berg", ko_email="robert.vanderberg@mail.mil"),
        "r1", "UEIUNL")
    s.save_contract(make_contract(
        piid="N003", award_id="A3", recipient_name="ORBITAL OPTICS INC",
        recipient_uei="UEIORB", awarding_agency="National Aeronautics and "
        "Space Administration", awarding_sub_agency="", award_amount=2_000_000.0,
        ko_name="Sam Smith", ko_email="sam.smith@nasa.gov"), "r1", "UEIORB")
    yield s
    s.close()


# --------------------------------------------------------- officers by name

def test_an_officer_is_found_surname_first(store):
    """"Doe, Jane" is how a record reads; "Jane Doe" is how she is addressed."""
    assert [o["ko_name"] for o in store.search_officers("doe jane")] == ["Jane Doe"]
    assert [o["ko_name"] for o in store.search_officers("jane doe")] == ["Jane Doe"]


def test_a_comma_between_the_names_does_not_break_it(store):
    assert [o["ko_name"] for o in store.search_officers("Doe, Jane")] == ["Jane Doe"]


def test_either_name_alone_still_works(store):
    assert [o["ko_name"] for o in store.search_officers("doe")] == ["Jane Doe"]
    assert [o["ko_name"] for o in store.search_officers("jane")] == ["Jane Doe"]


def test_a_multi_part_surname_matches_in_any_order(store):
    found = store.search_officers("berg robert")
    assert [o["ko_name"] for o in found] == ["Robert Van Der Berg"]


def test_the_address_is_searchable_alongside_the_name(store):
    assert store.search_officers("vanderberg")[0]["ko_name"] == "Robert Van Der Berg"


def test_two_words_that_do_not_both_match_find_nobody(store):
    """Tokens are ANDed. "jane smith" is neither officer, and saying so is
    better than returning both because each word matched someone."""
    assert store.search_officers("jane smith") == []


# ------------------------------------------------------ agencies by shorthand

def test_an_abbreviation_finds_the_long_name(store):
    found = store.search_agencies("DoD")
    assert found, "DoD should reach Department of Defense"
    assert all("Defense" in (a["agency"] or "") for a in found)


def test_darpa_finds_the_spelled_out_agency(store):
    found = store.search_agencies("darpa")
    assert [a["sub_agency"] for a in found] == [
        "Defense Advanced Research Projects Agency"]


def test_nasa_reaches_its_full_name(store):
    found = store.search_agencies("nasa")
    assert found and "National Aeronautics" in found[0]["agency"]


def test_a_sub_agency_shorthand_works(store):
    assert [a["sub_agency"] for a in store.search_agencies("navy")] == [
        "Department of the Navy"]


def test_the_long_name_still_matches_as_before(store):
    assert store.search_agencies("Department of Defense")


def test_an_unknown_abbreviation_is_an_ordinary_search(store):
    """It degrades to substring matching rather than to an error."""
    assert store.search_agencies("zzqq") == []


# ---------------------------------------------------------- ranking by prefix

def test_a_name_starting_with_the_query_outranks_a_larger_firm(store):
    """UNLOCK holds more dollars and contains "lock"; LOCKHEED starts with it,
    and is what somebody typing "lock" means."""
    names = [e["entity_name"] for e in store.search_entities("lock")]
    assert names[0] == "LOCKHEED MARTIN CORPORATION"
    assert "UNLOCK SYSTEMS HOLDINGS LLC" in names


def test_contractor_words_match_in_any_order(store):
    assert [e["entity_name"] for e in store.search_entities("martin lockheed")] == [
        "LOCKHEED MARTIN CORPORATION"]


def test_without_a_query_the_largest_come_first(store):
    names = [e["entity_name"] for e in store.search_entities("")]
    assert names[0] == "UNLOCK SYSTEMS HOLDINGS LLC"


# ------------------------------------------------------------- the helpers

def test_tokens_drops_punctuation():
    assert tokens("Doe, Jane  R.") == ["doe", "jane", "r"]
    assert tokens("") == []


def test_expand_agency_keeps_the_query_first():
    out = expand_agency("dod")
    assert out[0] == "dod"
    assert "department of defense" in out


def test_expand_agency_leaves_an_unknown_term_alone():
    assert expand_agency("acme widgets") == ["acme widgets"]
