"""Subcontractor screening.

FOCI risk concentrates in small, cash-hungry suppliers, and ranking by obligated
dollars buries them. Screening subawards reaches them — but the data is weaker
than prime-award data in two specific ways, both established by querying the
live API, and both of which these tests pin down:

  * the sub-agency filter is ignored by the endpoint, so a screen scoped to one
    command silently returns another command's suppliers;
  * subaward values are self-reported by the prime through FSRS and are often
    wrong by orders of magnitude.
"""
from __future__ import annotations

import pytest

from foci_screen.connectors.fpds import FPDSConnector
from foci_screen.connectors.usaspending import USASpendingConnector
from foci_screen.models import Contract, ContractingOfficer, Entity, Finding
from foci_screen.notify import render


def sub_row(**kw):
    row = {
        "Sub-Award ID": "5000653817",
        "Sub-Award Type": "sub-contract",
        "Sub-Awardee Name": "DESIGN/OL, INC.",
        "Sub-Recipient UEI": "YDMQMHPLCBW4",
        "Sub-Award Amount": 5000653817.0,
        "Sub-Award Date": "2025-09-30",
        "Sub-Award Description": "MACHINE SHOPS",
        "Awarding Agency": "Department of Defense",
        "Awarding Sub Agency": "Department of the Navy",
        "Prime Award ID": "N0001923F0220",
        "Prime Recipient Name": "NORTHROP GRUMMAN SYSTEMS CORPORATION",
        "prime_award_generated_internal_id": "CONT_AWD_N0001923F0220_9700",
    }
    row.update(kw)
    return row


class StubHttp:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.payloads: list[dict] = []

    def post(self, url, json_body=None, **kw):
        self.payloads.append(json_body)
        return {"status": 200, "json": {"results": self.rows}}

    def get(self, url, **kw):
        return {"status": 404, "text": ""}


# --------------------------------------------------------------- the query

def test_subaward_search_orders_by_date_not_amount():
    """Sorting by value would rank the least trustworthy figures first."""
    http = StubHttp([sub_row()])
    USASpendingConnector(http).search_subawards("Department of Defense")

    assert http.payloads[0]["sort"] == "Sub-Award Date"
    assert http.payloads[0]["order"] == "desc"
    assert http.payloads[0]["subawards"] is True


def test_sub_agency_is_filtered_client_side():
    """The endpoint ignores the subtier filter: asking for DoD+Navy returns the
    same rows as DoD alone. Without this, a Navy screen reports Army suppliers."""
    rows = [sub_row(**{"Awarding Sub Agency": "Department of the Navy"}),
            sub_row(**{"Sub-Award ID": "A2", "Sub-Awardee Name": "ARMY SUPPLIER LLC",
                       "Sub-Recipient UEI": "U2", "Awarding Sub Agency":
                       "Department of the Army"})]
    http = StubHttp(rows)

    got = USASpendingConnector(http).search_subawards(
        "Department of Defense", sub_agency="Department of the Navy")

    assert [c.recipient_name for c in got] == ["DESIGN/OL, INC."]


def test_no_sub_agency_keeps_everything():
    http = StubHttp([sub_row(), sub_row(**{"Sub-Award ID": "A2", "Sub-Recipient UEI": "U2",
                                           "Awarding Sub Agency": "Department of the Army"})])
    assert len(USASpendingConnector(http).search_subawards("Department of Defense")) == 2


def test_non_contract_subawards_are_skipped():
    """Sub-grants are a different instrument and not what this screens."""
    http = StubHttp([sub_row(**{"Sub-Award Type": "sub-grant"})])
    assert USASpendingConnector(http).search_subawards("Department of Defense") == []


def test_subaward_maps_to_a_contract_with_its_prime():
    http = StubHttp([sub_row()])
    c = USASpendingConnector(http).search_subawards("Department of Defense")[0]

    assert c.is_subaward is True
    assert c.recipient_name == "DESIGN/OL, INC."
    assert c.prime_award_id == "N0001923F0220"
    assert c.prime_recipient_name == "NORTHROP GRUMMAN SYSTEMS CORPORATION"
    # The figure is kept but marked, so nothing downstream quotes it as an
    # obligation: this one is a $5B subaward to a machine shop.
    assert c.amount_is_self_reported is True


# ------------------------------------------------------- who gets notified

def test_fpds_is_asked_about_the_prime_not_the_subaward():
    """A subcontract has no contracting officer of its own and FPDS has no
    record of it at all."""
    sub = Contract(award_id="SUB1", piid="SUB1", is_subaward=True,
                   prime_award_id="N0001923F0220")
    assert sub.fpds_piid == "N0001923F0220"

    prime = Contract(award_id="N0001917C0001", piid="N0001917C0001")
    assert prime.fpds_piid == "N0001917C0001"


def test_prime_vendor_details_are_not_copied_onto_the_subcontractor():
    """FPDS returns the prime's vendor record. Applying it wholesale would
    attribute one company's ownership and parent to another."""
    from xml.etree import ElementTree as ET

    xml = """<entry xmlns="http://www.w3.org/2005/Atom"><content><award>
        <transactionInformation>
          <approvedBy>JANE.DOE.N00019@JSF.MIL</approvedBy>
        </transactionInformation>
        <vendor><countryOfIncorporation>USA</countryOfIncorporation>
          <ultimateParentUEIName>NORTHROP GRUMMAN CORP</ultimateParentUEIName>
          <isForeignOwnedAndLocated>false</isForeignOwnedAndLocated></vendor>
        <contractData><descriptionOfContractRequirement>PRIME AIRCRAFT WORK
        </descriptionOfContractRequirement></contractData>
        </award></content></entry>"""

    class OneEntry(FPDSConnector):
        def fetch_award(self, piid):
            return [ET.fromstring(xml)]

    sub = Contract(award_id="SUB1", piid="SUB1", is_subaward=True,
                   prime_award_id="N0001923F0220",
                   recipient_name="TINY MACHINE SHOP LLC",
                   description="MACHINE SHOPS")
    OneEntry(None).enrich(sub)

    # The officer transfers: that is a fact about the prime contract.
    assert sub.officer.email == "jane.doe.n00019@jsf.mil"   # normalised to lower case
    # The vendor facts do not.
    assert sub.parent_recipient_name == ""
    assert sub.country_of_incorporation == ""
    assert sub.description == "MACHINE SHOPS"


# ------------------------------------------------------------ the notice

def _subaward_finding():
    officer = ContractingOfficer(name="Jane Doe", email="jane.doe@mail.mil",
                                 source="FPDS-NG", confidence="high")
    sub = Contract(award_id="SUB1", piid="SUB1", is_subaward=True,
                   amount_is_self_reported=True, award_amount=5_000_653_817.0,
                   prime_award_id="N0001923F0220",
                   prime_recipient_name="NORTHROP GRUMMAN SYSTEMS CORPORATION",
                   recipient_name="DESIGN/OL, INC.",
                   awarding_sub_agency="Department of the Navy",
                   start_date="2025-09-30", officer=officer)
    return Finding(entity=Entity(name="DESIGN/OL, INC.", uei="YDMQMHPLCBW4"),
                   contracts=[sub], signals=[], total_score=20.0,
                   severity="high", run_id="r1")


def test_notice_explains_that_the_company_is_a_subcontractor():
    """The recipient holds no contract with this company. A notice that implies
    otherwise is wrong in the first sentence."""
    text = render.render_text(_subaward_finding())

    assert "SUBCONTRACTOR" in text
    assert "NORTHROP GRUMMAN SYSTEMS CORPORATION" in text
    assert "no privity" in text
    assert "Subaward under prime N0001923F0220" in text


def test_notice_does_not_present_the_subaward_value_as_an_obligation():
    text = render.render_text(_subaward_finding())
    assert "self-reported by the prime; not an obligated amount" in text


def test_prime_notice_is_unchanged():
    officer = ContractingOfficer(name="Jane Doe", email="jane.doe@mail.mil",
                                 confidence="high")
    prime = Contract(award_id="N1", piid="N1", recipient_name="ACME",
                     award_amount=1_000_000.0, officer=officer)
    text = render.render_text(
        Finding(entity=Entity(name="ACME", uei="U1"), contracts=[prime],
                signals=[], total_score=10.0, severity="medium", run_id="r1"))

    assert "SUBCONTRACTOR" not in text
    assert "which holds contract actions" in text


# -------------------------------------------------------------- the store

def test_subaward_round_trips_through_the_store(tmp_path):
    from foci_screen.store import Store

    store = Store(str(tmp_path / "sub.db"))
    try:
        store.save_contract(
            Contract(award_id="SUB1", piid="SUB1", is_subaward=True,
                     prime_award_id="N0001923F0220",
                     prime_recipient_name="NORTHROP GRUMMAN SYSTEMS CORPORATION",
                     recipient_name="DESIGN/OL, INC.", award_amount=1.0),
            "r1", "YDMQMHPLCBW4")
        row = store.get_contract("SUB1")
    finally:
        store.close()

    assert row["is_subaward"] is True
    assert row["prime_award_id"] == "N0001923F0220"
    assert row["prime_recipient_name"] == "NORTHROP GRUMMAN SYSTEMS CORPORATION"


# -------------------------------------------------------- named individuals

@pytest.mark.parametrize("name", [
    "JOSHUA D GOODWIN",          # returned by a live Navy subaward query
    "Jane Smith",
    "SMITH JOHN A",
])
def test_people_are_recognised_as_people(name):
    from foci_screen.connectors.usaspending import looks_like_an_individual
    assert looks_like_an_individual(name) is True


@pytest.mark.parametrize("name", [
    "SF&B, INC.",
    "DESIGN/OL, INC.",
    "TRIO MANUFACTURING INC",
    "LOCKHEED MARTIN CORPORATION",
    "Acme Systems",
    "GOODWIN ENGINEERING",
    "",
])
def test_companies_are_not_mistaken_for_people(name):
    from foci_screen.connectors.usaspending import looks_like_an_individual
    assert looks_like_an_individual(name) is False


def test_individual_subawardees_are_skipped_and_reported():
    """Screening a named person, then writing to their customer's contracting
    officer about them, needs a deliberate decision — not a default."""
    from foci_screen.pipeline import Screener, ScreenOptions, ScreenResult

    subs = [
        Contract(award_id="S1", piid="S1", is_subaward=True, recipient_uei="U1",
                 recipient_name="JOSHUA D GOODWIN"),
        Contract(award_id="S2", piid="S2", is_subaward=True, recipient_uei="U2",
                 recipient_name="SF&B, INC."),
    ]
    screener = Screener.__new__(Screener)
    screener.usaspending = SubOnlyScreener(subs).usaspending

    opts = ScreenOptions(agency="DoD", max_subaward_entities=5)
    result = ScreenResult(run_id="r1", options=opts)
    picked = screener._subaward_entities(opts, {}, result, lambda *_: None)

    assert [c[1][0].recipient_name for c in picked] == ["SF&B, INC."]
    note = " ".join(result.notes)
    assert "looks like a person" in note
    # Named, so a company wrongly caught by the heuristic is visible, not silent.
    assert "JOSHUA D GOODWIN" in note


# ----------------------------------------------------------- the pipeline

class SubOnlyScreener:
    """Just enough of a screener to exercise the selection logic."""

    def __init__(self, subs):
        self.usaspending = type("C", (), {"search_subawards": lambda *a, **k: subs})()


@pytest.mark.parametrize("requested,expected", [(0, 0), (1, 1), (5, 2)])
def test_subaward_entity_quota_is_respected(requested, expected):
    from foci_screen.pipeline import Screener, ScreenOptions

    subs = [
        Contract(award_id="S1", piid="S1", is_subaward=True, recipient_uei="U1",
                 recipient_name="SUPPLIER ONE LLC"),
        Contract(award_id="S2", piid="S2", is_subaward=True, recipient_uei="U2",
                 recipient_name="SUPPLIER TWO LLC"),
    ]
    screener = Screener.__new__(Screener)
    screener.usaspending = SubOnlyScreener(subs).usaspending

    from foci_screen.pipeline import ScreenResult
    opts = ScreenOptions(agency="DoD", max_subaward_entities=requested)
    result = ScreenResult(run_id="r1", options=opts)

    picked = screener._subaward_entities(opts, {}, result, lambda *_: None)
    assert len(picked) == expected


def test_subcontractor_already_screened_as_a_prime_is_not_repeated():
    from foci_screen.pipeline import Screener, ScreenOptions, ScreenResult

    subs = [Contract(award_id="S1", piid="S1", is_subaward=True, recipient_uei="U1",
                     recipient_name="ALREADY SCREENED INC")]
    screener = Screener.__new__(Screener)
    screener.usaspending = SubOnlyScreener(subs).usaspending

    opts = ScreenOptions(agency="DoD", max_subaward_entities=5)
    result = ScreenResult(run_id="r1", options=opts)
    picked = screener._subaward_entities(opts, {"U1": []}, result, lambda *_: None)

    assert picked == []


def test_run_notes_warn_about_self_reported_values():
    from foci_screen.pipeline import Screener, ScreenOptions, ScreenResult

    subs = [Contract(award_id="S1", piid="S1", is_subaward=True, recipient_uei="U1",
                     recipient_name="SUPPLIER ONE LLC")]
    screener = Screener.__new__(Screener)
    screener.usaspending = SubOnlyScreener(subs).usaspending

    opts = ScreenOptions(agency="DoD", max_subaward_entities=1)
    result = ScreenResult(run_id="r1", options=opts)
    screener._subaward_entities(opts, {}, result, lambda *_: None)

    note = " ".join(result.notes)
    assert "self-reported" in note
    assert "prime contract" in note
