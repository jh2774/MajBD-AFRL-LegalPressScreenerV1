"""Offline tests: patent liens from the USPTO Patent Assignment Dataset.

The fixtures follow the dataset's published schema
(uspto.gov/sites/default/files/documents/pat_assign_dataset_schema.pdf):
one CSV per table, joined on rf_id, with the conveyance type in a constructed
`assignment_conveyance` table.
"""
from __future__ import annotations

import csv
import io
import zipfile
from types import SimpleNamespace

import pytest

from foci_screen.connectors import uspto_dataset
from foci_screen.connectors.uspto_dataset import (
    DatasetError,
    PatentAssignmentDataset,
    import_dataset,
    parse_date,
)
from foci_screen.models import Contract, Entity
from foci_screen.risk import engine

# rf_id: (convey_ty, convey_text, record_dt, assignors, (lender, country), patents)
RECORDS = {
    # Acme pledges three patents to a Cayman lender; one is released later.
    101: ("security", "SECURITY INTEREST", "2019-05-01",
          ["ACME DYNAMICS, INC."], ("HARBOUR PEAK CREDIT LTD", "CAYMAN ISLANDS"),
          ["9000001", "9000002", "9000003"]),
    102: ("release", "RELEASE BY SECURED PARTY", "2021-02-01",
          ["HARBOUR PEAK CREDIT LTD"], ("ACME DYNAMICS INC", "UNITED STATES"),
          ["9000001"]),
    # Acme's older lien, released in full.
    201: ("security", "PATENT SECURITY AGREEMENT", "2012-03-10",
          ["ACME DYNAMICS INC"], ("FIRST NATIONAL BANK", "UNITED STATES"),
          ["8000001", "8000002"]),
    202: ("release", "RELEASE OF SECURITY INTEREST", "2014-07-15",
          ["FIRST NATIONAL BANK"], ("ACME DYNAMICS INC", "UNITED STATES"),
          ["8000001", "8000002"]),
    # A lien whose only "release" was recorded before it: not a release of it.
    301: ("security", "GRANT OF RIGHTS", "2020-01-01",
          ["ACME DYNAMICS LLC"], ("NORTHERN LENDING LLC", "UNITED STATES"),
          ["7000001"]),
    302: ("release", "RELEASE BY SECURED PARTY", "2018-01-01",
          ["NORTHERN LENDING LLC"], ("ACME DYNAMICS LLC", "UNITED STATES"),
          ["7000001"]),
    # A plain sale, and a different company with a similar name: neither counts.
    401: ("assignment", "ASSIGNMENT OF ASSIGNORS INTEREST", "2022-01-01",
          ["ACME DYNAMICS INC"], ("BUYER CORP", "UNITED STATES"), ["6000001"]),
    501: ("security", "SECURITY INTEREST", "2022-06-01",
          ["ACME DYNAMICS EUROPE GMBH"], ("SOME BANK", "GERMANY"), ["5000001"]),
}


def _csv(header, rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


def _tables(records=RECORDS, purged=()):
    conv, assign, assignor, assignee, docs = [], [], [], [], []
    for rf, (ty, text, recorded, ors, (ee, country), patents) in records.items():
        conv.append([rf, ty, 0])
        assign.append([rf, 1, "CORRESPONDENT", "", "", "", "", rf // 10, rf % 10, text,
                       recorded, recorded, 3, 1 if rf in purged else 0])
        assignor += [[rf, name, recorded, ""] for name in ors]
        assignee.append([rf, ee, "1 MAIN ST", "", "GEORGE TOWN", "", "KY1", country])
        docs += [[rf, "TITLE", "en", "", "", "US", "", "", "", p, "", "US"] for p in patents]
    return {
        "assignment_conveyance": _csv(["rf_id", "convey_ty", "employer_assign"], conv),
        "assignment": _csv(["rf_id", "file_id", "cname", "caddress_1", "caddress_2",
                            "caddress_3", "caddress_4", "reel_no", "frame_no",
                            "convey_text", "record_dt", "last_update_dt", "page_count",
                            "purge_in"], assign),
        "assignor": _csv(["rf_id", "or_name", "exec_dt", "ack_dt"], assignor),
        "assignee": _csv(["rf_id", "ee_name", "ee_address_1", "ee_address_2", "ee_city",
                          "ee_state", "ee_postcode", "ee_country"], assignee),
        "documentid": _csv(["rf_id", "title", "lang", "appno_doc_num", "appno_date",
                            "appno_country", "pgpub_doc_num", "pgpub_date",
                            "pgpub_country", "grant_doc_num", "grant_date",
                            "grant_country"], docs),
    }


@pytest.fixture()
def combined_zip(tmp_path):
    """The full-set download: one csv.zip holding every table."""
    path = tmp_path / "csv.zip"
    with zipfile.ZipFile(path, "w") as z:
        for table, data in _tables().items():
            z.writestr(f"csv/{table}.csv", data)
    return path


@pytest.fixture()
def dataset(tmp_path, combined_zip):
    index = tmp_path / "patent_assignments.db"
    import_dataset(combined_zip, index, progress=lambda m: None)
    return PatentAssignmentDataset(str(index))


# ------------------------------------------------------------------ import

def test_import_keeps_only_security_interests_and_releases(tmp_path, combined_zip):
    index = tmp_path / "idx.db"
    report = import_dataset(combined_zip, index, progress=lambda m: None)
    assert report["records"] == 7          # the plain sale is dropped
    assert report["latest_recorded"] == "2022-06-01"
    assert index.exists()
    assert not (tmp_path / "idx.db.partial").exists()


def test_a_folder_of_per_table_downloads_imports_the_same(tmp_path):
    folder = tmp_path / "downloads"
    folder.mkdir()
    for table, data in _tables().items():
        with zipfile.ZipFile(folder / f"{table}.csv.zip", "w") as z:
            z.writestr(f"{table}.csv", data)
    report = import_dataset(folder, tmp_path / "idx.db", progress=lambda m: None)
    assert report["records"] == 7


def test_a_missing_table_is_refused_and_leaves_no_index(tmp_path):
    path = tmp_path / "partial.zip"
    with zipfile.ZipFile(path, "w") as z:
        for table, data in _tables().items():
            if table != "documentid":
                z.writestr(f"{table}.csv", data)
    with pytest.raises(DatasetError, match="documentid"):
        import_dataset(path, tmp_path / "idx.db", progress=lambda m: None)
    assert not (tmp_path / "idx.db").exists()


def test_a_purged_record_is_not_imported(tmp_path):
    path = tmp_path / "csv.zip"
    with zipfile.ZipFile(path, "w") as z:
        for table, data in _tables(purged={101}).items():
            z.writestr(f"{table}.csv", data)
    idx = tmp_path / "idx.db"
    import_dataset(path, idx, progress=lambda m: None)
    found = PatentAssignmentDataset(str(idx)).security_interests("Acme Dynamics")
    keys = [d.key for d in found]
    assert "uspto:10-1" not in keys


@pytest.mark.parametrize("raw,expected", [
    ("2019-05-01", "2019-05-01"),
    ("20190501", "2019-05-01"),
    ("01may2019", "2019-05-01"),
    ("21670", "2019-05-01"),        # Stata: days since 1960-01-01
    ("", ""), ("not a date", ""),
])
def test_recorded_dates_in_every_export_format(raw, expected):
    assert parse_date(raw) == expected


# ------------------------------------------------------------------ lookup

def test_liens_are_found_for_the_contractor_as_the_party_pledging(dataset):
    docs = dataset.security_interests("ACME DYNAMICS LLC")
    assert [d.meta["reel_frame"] for d in docs] == ["30/1", "10/1"]  # newest first


def test_a_similarly_named_company_is_not_the_contractor(dataset):
    assignors = {d.meta["assignor"] for d in dataset.security_interests("Acme Dynamics Inc")}
    assert "ACME DYNAMICS EUROPE GMBH" not in assignors


def test_a_fully_released_lien_is_dropped_and_a_partial_one_says_so(dataset):
    docs = {d.meta["reel_frame"]: d for d in dataset.security_interests("Acme Dynamics")}
    assert "20/1" not in docs                     # released in full in 2014
    partial = docs["10/1"].meta
    assert (partial["patents_recorded"], partial["patents_released"],
            partial["patent_count"]) == (3, 1, 2)
    assert "Released since: 1 of 3" in docs["10/1"].text


def test_a_release_recorded_before_the_lien_does_not_release_it(dataset):
    lien = next(d for d in dataset.security_interests("Acme Dynamics")
                if d.meta["reel_frame"] == "30/1")
    assert lien.meta["patents_released"] == 0


def test_without_an_index_the_dataset_is_unavailable(tmp_path):
    ds = PatentAssignmentDataset(str(tmp_path / "absent.db"))
    assert not ds.available
    assert ds.security_interests("Acme Dynamics") == []


# ------------------------------------------------------------------ the rule

def test_a_dataset_lien_fires_the_recorded_lien_rule(dataset):
    ctx_entity = Entity(name="ACME DYNAMICS LLC")
    contracts = [Contract(award_id="N1")]
    lien = next(d for d in dataset.security_interests("Acme Dynamics")
                if d.meta["reel_frame"] == "10/1")
    signals = engine.evaluate_documents([(lien, None)], ctx_entity, contracts)
    rule = [s for s in signals if s.rule_id == "IPCOL-USPTO-01"]
    assert len(rule) == 1
    assert rule[0].jurisdiction == "Cayman Islands"
    assert "2 propert" in rule[0].rationale     # the outstanding count, not the recorded 3


def test_the_dataset_classification_covers_unusual_wording(dataset):
    """"GRANT OF RIGHTS" names no lien term, but the dataset classifies it as one."""
    lien = next(d for d in dataset.security_interests("Acme Dynamics")
                if d.meta["reel_frame"] == "30/1")
    signals = engine.evaluate_documents([(lien, None)], Entity(name="ACME"), [])
    assert [s.rule_id for s in signals if s.category == "IP_COLLATERAL"] == ["IPCOL-USPTO-01"]


# ------------------------------------------------------------------ pipeline

def test_a_screen_uses_the_dataset_and_says_how_current_it_is(dataset):
    from foci_screen.pipeline import Screener, ScreenOptions

    screener = object.__new__(Screener)
    screener.edgar = SimpleNamespace()
    screener.iapd = SimpleNamespace()
    screener.ofac = SimpleNamespace(screen=lambda name: [])
    screener.uspto = SimpleNamespace(available=False)
    screener.patent_dataset = dataset
    screener.sam = SimpleNamespace(available=False)
    screener.alerts = SimpleNamespace(available=lambda: False)
    said: list[str] = []
    entity = Entity(name="ACME DYNAMICS LLC", parent_name="ACME DYNAMICS INC")

    docs = screener._gather(entity, ScreenOptions(agency="x", skip_web=True), said.append)

    assert sorted(d.meta["reel_frame"] for d in docs) == ["10/1", "30/1"]  # once each
    assert any("recorded through 2022-06-01" in m and "NOT checked" in m for m in said)


def test_module_documents_where_the_dataset_comes_from():
    assert "patent-assignment-dataset" in uspto_dataset.DATASET_URL
