"""Offline tests: SAM.gov's public exclusions extract.

The columns are the extract's own, as read from the October 2026 file.
"""
from __future__ import annotations

import csv
import io
import os
import time
import zipfile
from datetime import date, timedelta

import pytest

from foci_screen.connectors.sam_exclusions import SAMExclusionsConnector, build_index
from foci_screen.models import Contract, Document, Entity
from foci_screen.risk import engine

COLUMNS = ["Classification", "Name", "Prefix", "First", "Middle", "Last", "Suffix",
           "Address 1", "Address 2", "Address 3", "Address 4", "City",
           "State / Province", "Country", "Zip Code", "Open Data Flag",
           "Blank (Deprecated)", "Unique Entity ID", "Exclusion Program",
           "Excluding Agency", "CT Code", "Exclusion Type", "Additional Comments",
           "Active Date", "Termination Date", "Record Status", "Cross-Reference",
           "SAM Number", "CAGE", "NPI", "Creation_Date"]

FUTURE = (date.today() + timedelta(days=365)).isoformat()
PAST = (date.today() - timedelta(days=30)).isoformat()


def record(classification="Firm", name="", first="", last="", uei="", cage="",
           sam="S1", ends=FUTURE, comments="", country="USA"):
    r = dict.fromkeys(COLUMNS, "")
    r.update({"Classification": classification, "Name": name, "First": first,
              "Last": last, "Unique Entity ID": uei, "CAGE": cage, "SAM Number": sam,
              "Exclusion Program": "Reciprocal", "Excluding Agency": "DLA",
              "Exclusion Type": "Ineligible (Proceedings Completed)",
              "Additional Comments": comments, "Active Date": "2025-01-15",
              "Termination Date": ends, "Record Status": "Active",
              "Country": country, "City": "GEORGE TOWN",
              "Address 1": "Ugland House, Grand Cayman, Cayman Islands"
              if country == "CYM" else "1 Main St"})
    return [r[c] for c in COLUMNS]


RECORDS = [
    record(name="Harbour Optics LLC", uei="HARBOUR00001", cage="1ABC2", sam="S1",
           comments="DEBARMENT ON THIS ENTITY AND ALL ASSOCIATED PARTIES."),
    record(name="Harbour Optics LLC", uei="HARBOUR00001", sam="S2"),     # a second action
    record(name="Northern Parent Holdings", uei="PARENT000001", sam="S3"),
    record(name="Lapsed Supplies Inc", uei="LAPSED000001", sam="S4", ends=PAST),
    record(name="Name Only Industries", sam="S5"),
    record("Individual", first="Acme", last="Dynamics", sam="S6"),         # no identifier
    record("Individual", first="Jane", last="Roe", uei="PERSON000001", sam="S7"),
    record(name="Cayman Front Ltd", uei="CAYMAN000001", sam="S8", country="CYM"),
]


def extract(rows=RECORDS) -> bytes:
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(COLUMNS)
    w.writerows(rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SAM_Exclusions_Public_Extract_V2_26282.CSV", text.getvalue())
    return buf.getvalue()


class StubHttp:
    """SAM.gov's file listing and download, or an outage."""

    def __init__(self, files=("SAM_Exclusions_Public_Extract_V2_26282.ZIP",),
                 body=None, status=200):
        self.files, self.body, self.status = files, body, status
        self.downloads: list[str] = []

    def get(self, url, **kw):
        return {"status": 200, "json": {"_embedded": {"customS3ObjectSummaryList": [
            {"displayKey": f} for f in self.files]}}}

    def get_bytes(self, url, **kw):
        self.downloads.append(url)
        return (self.status, self.body if self.body is not None else extract())


@pytest.fixture()
def conn(tmp_path):
    return SAMExclusionsConnector(StubHttp(), str(tmp_path))


# ------------------------------------------------------------------ index

def test_records_that_can_never_match_are_left_out(tmp_path):
    n = build_index(extract(), tmp_path / "x.db", "f.ZIP")
    # Dropped: the lapsed exclusion, and the individual with no UEI or CAGE.
    assert n == len(RECORDS) - 2


def test_the_newest_file_is_downloaded_across_a_year_boundary(tmp_path):
    http = StubHttp(files=("SAM_Exclusions_Public_Extract_V2_26364.ZIP",
                           "SAM_Exclusions_Public_Extract_V2_27002.ZIP",
                           "SAM_Exclusions_Public_Extract_V2_26365.ZIP"))
    SAMExclusionsConnector(http, str(tmp_path)).screen("x", uei="HARBOUR00001")
    assert http.downloads[0].endswith("V2_27002.ZIP?privacy=Public")


def test_a_fresh_index_is_reused_without_downloading_again(tmp_path):
    SAMExclusionsConnector(StubHttp(), str(tmp_path)).screen("x", uei="HARBOUR00001")
    http = StubHttp()
    assert SAMExclusionsConnector(http, str(tmp_path)).screen("x", uei="HARBOUR00001")
    assert http.downloads == []


def test_a_failed_refresh_keeps_yesterdays_index(tmp_path):
    SAMExclusionsConnector(StubHttp(), str(tmp_path)).screen("x", uei="HARBOUR00001")
    old = time.time() - 2 * 24 * 3600
    os.utime(tmp_path / "sam_exclusions.db", (old, old))
    stale = SAMExclusionsConnector(StubHttp(status=503, body=b""), str(tmp_path))
    assert stale.screen("x", uei="HARBOUR00001")


def test_an_outage_with_no_index_returns_nothing_rather_than_failing(tmp_path):
    c = SAMExclusionsConnector(StubHttp(status=503, body=b""), str(tmp_path))
    assert c.screen("Harbour Optics LLC", uei="HARBOUR00001") == []
    assert "503" in c.unavailable_reason


# ------------------------------------------------------------------ matching

def test_a_contractor_is_matched_by_uei_and_each_exclusion_is_its_own_record(conn):
    docs = conn.screen("Something Else", uei="HARBOUR00001")
    assert sorted(d.key for d in docs) == ["samexcl:S1", "samexcl:S2"]
    assert {d.meta["match_basis"] for d in docs} == {"uei"}


def test_a_contractor_is_matched_by_cage(conn):
    docs = conn.screen("Something Else", cage="1abc2")
    assert [(d.key, d.meta["match_basis"]) for d in docs] == [("samexcl:S1", "cage")]


def test_a_name_is_tried_only_when_no_identifier_matched(conn):
    assert [d.meta["match_basis"] for d in conn.screen("NAME ONLY INDUSTRIES INC")] == ["name"]
    by_uei = conn.screen("Name Only Industries", uei="HARBOUR00001")
    assert {d.meta["match_basis"] for d in by_uei} == {"uei"}


def test_an_individual_is_never_matched_by_name(conn):
    assert conn.screen("Acme Dynamics") == []
    assert conn.screen("Jane Roe") == []
    assert [d.key for d in conn.screen("x", uei="PERSON000001")] == ["samexcl:S7"]


def test_a_lapsed_exclusion_is_not_reported(conn):
    assert conn.screen("Lapsed Supplies Inc", uei="LAPSED000001") == []


def test_the_parent_is_checked_by_uei(conn):
    docs = conn.screen("Clean Subsidiary LLC", uei="CLEAN0000001",
                       parent_name="Northern Parent Holdings", parent_uei="PARENT000001")
    assert [(d.key, d.meta["whose"]) for d in docs] == [("samexcl:S3", "parent")]


def test_the_evidence_is_the_whole_record_as_sam_gov_asks(conn):
    doc = conn.screen("x", uei="HARBOUR00001", cage="1ABC2")[0]
    for field in ("Exclusion Type: Ineligible", "Excluding Agency: DLA",
                  "ALL ASSOCIATED PARTIES", "Termination Date:"):
        assert field in doc.text


# ------------------------------------------------------------------ the rule

def fire(doc):
    return engine.evaluate_documents([(doc, None)], Entity(name="X"),
                                     [Contract(award_id="A1")])


def test_an_identified_exclusion_outranks_a_name_match(conn):
    by_uei = fire(conn.screen("x", uei="HARBOUR00001", cage="1ABC2")[0])
    by_name = fire(conn.screen("Name Only Industries")[0])
    assert [s.rule_id for s in by_uei] == ["EXCL-SAM-01"]
    assert [s.rule_id for s in by_name] == ["EXCL-SAM-NAME-01"]
    assert by_uei[0].score > by_name[0].score
    assert by_uei[0].category == "SANCTIONS"
    assert "FAR 9.405-1" in by_uei[0].rationale
    assert "Confirm the identifiers" in by_name[0].rationale


def test_a_parent_exclusion_has_its_own_rule(conn):
    doc = conn.screen("x", uei="CLEAN0000001", parent_uei="PARENT000001")[0]
    assert [s.rule_id for s in fire(doc)] == ["EXCL-SAM-PARENT-01"]


def test_an_address_in_the_record_is_not_read_as_a_jurisdiction_mention(conn):
    doc = conn.screen("x", uei="CAYMAN000001")[0]
    assert [s.rule_id for s in fire(doc)] == ["EXCL-SAM-01"]


def test_other_documents_do_not_fire_the_exclusion_rule():
    doc = Document(source="ofac", key="o1", text="x", meta={"match_basis": "uei"})
    assert "EXCL-SAM-01" not in [s.rule_id for s in fire(doc)]


def test_a_screen_checks_the_contractor_and_its_parent_by_identifier():
    from types import SimpleNamespace

    from foci_screen.pipeline import Screener, ScreenOptions

    asked: list = []
    screener = object.__new__(Screener)
    screener.edgar = SimpleNamespace()
    screener.iapd = SimpleNamespace()
    screener.exclusions = SimpleNamespace(
        screen=lambda name, **ids: asked.append((name, ids)) or [])
    screener.ofac = SimpleNamespace(screen=lambda name: [])
    screener.uspto = SimpleNamespace(available=False)
    screener.patent_dataset = SimpleNamespace(available=False)
    screener.sam = SimpleNamespace(available=False)
    screener.alerts = SimpleNamespace(available=lambda: False)
    entity = Entity(name="HARBOUR OPTICS LLC", uei="HARBOUR00001", cage="1ABC2",
                    parent_name="NORTHERN PARENT HOLDINGS", parent_uei="PARENT000001")

    screener._gather(entity, ScreenOptions(agency="x", skip_web=True), lambda m: None)

    assert asked == [("HARBOUR OPTICS LLC", {
        "uei": "HARBOUR00001", "cage": "1ABC2",
        "parent_name": "NORTHERN PARENT HOLDINGS", "parent_uei": "PARENT000001"})]
