"""Form ADV Schedules A and B: who owns an investment firm, and who owns them.

The fixture follows the extracted text of two real filings — the table header
that wraps across seven lines, cells that wrap mid-row, the "DE/FE/I" column
run into the next heading, the blank last column for entities — with invented
names and the form's long instruction lines shortened. The traps it carries
were all found in real text: instructions that mention "Schedule B" at the
start of a line, a name that wraps onto two lines, a title that does, and (in
Schedule B) names that themselves contain "DE".
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from foci_screen import portfolio as pf
from foci_screen.connectors import adv
from foci_screen.connectors.adv import (
    READER_VERSION,
    AdvConnector,
    AdviserSnapshot,
    Owner,
    compare,
    parse_form,
    parse_owners,
)

AUTH = {"X-API-Key": "secret-key"}

A_HEAD = """Schedule A
Direct Owners and Executive Officers
1. Complete Schedule A only if you are submitting an initial application or report.
Schedule B asks for information about your indirect owners. Use Schedule C to amend.
3. Do you have any indirect owners to be reported on Schedule B?   Yes   No
4. In the DE/FE/I column below, enter "DE" if the owner is a domestic entity, "FE" if
the owner is an entity incorporated or domiciled in a foreign country, or
"I" if the owner or executive officer is an individual.
(c) Complete each column.
FULL LEGAL NAME (Individuals:
Last Name, First Name, Middle
Name)
DE/FE/ITitle or Status Date Title or Status
Acquired
MM/YYYY
Ownership
Code
Control
Person
PR CRD No. If None: S.S. No. and
Date of Birth, IRS Tax No. or
Employer ID No.
"""

A_ROWS = """REYES, DANA, MARIE I MANAGING PARTNER 01/2012 C Y N 6000001
MCALLISTER, SAM I CHIEF COMPLIANCE
OFFICER
10/2021 NA Y N 6000002
HARBORLIGHT HOLDINGS
INC.
DE SOLE MEMBER 08/2005 E Y N
OKAFOR, JOHN, I I MEMBER 03/2019 A N N 6000003
"""

B_HEAD = """Schedule B
Indirect Owners
1. Complete Schedule B only if you are submitting an initial application or report.
Schedule B asks for information about your indirect owners; you must first complete
Schedule A, which asks for information about your direct owners.
6. Ownership codes are: C - 25% but less than 50% E - 75% or more
D - 50% but less than 75% F - Other (general partner, trustee, or elected manager)
(c) Complete each column.
"""

B_TABLE_HEAD = """FULL LEGAL NAME (Individuals: Last
Name, First Name, Middle Name)
DE/FE/IEntity in Which
Interest is Owned
Status Date Status
Acquired
MM/YYYY
Ownership
Code
Control
Person
PR CRD No. If None: S.S. No.
and Date of Birth, IRS Tax
No. or Employer ID No.
"""

B_ROWS = """NORTHSEA HOLDINGS LTD FE HARBORLIGHT
HOLDINGS INC.
SHAREHOLDER 08/2005 E Y N
BANCO DE AVILA S.A. FE NORTHSEA HOLDINGS LTD SHAREHOLDER 11/2024 C Y N
AVILA FAMILY TRUST DE BANCO DE AVILA S.A. TRUSTEE 11/2024 F Y N 12-3456789
"""

AFTER = """Schedule D - Miscellaneous
You may use the space below to explain a response to an Item.
Schedule R
No Information Filed
"""


def schedules(a_rows=A_ROWS, b_rows=B_ROWS):
    b = B_HEAD + (B_TABLE_HEAD + b_rows if b_rows else "No Information Filed\n")
    return "Item 12 Small Businesses\n" + A_HEAD + a_rows + b + AFTER


def by_name(owners):
    return {o.name: o for o in owners}


# ------------------------------------------------------------------ reading

def test_direct_owners_are_read_through_wrapped_names_and_titles():
    owners, read = parse_owners(schedules())
    assert read
    direct = by_name(o for o in owners if not o.indirect)
    assert list(direct) == ["REYES, DANA, MARIE", "MCALLISTER, SAM",
                            "HARBORLIGHT HOLDINGS INC.", "OKAFOR, JOHN, I"]

    dana = direct["REYES, DANA, MARIE"]
    assert (dana.kind, dana.title, dana.since, dana.code) == (
        "I", "MANAGING PARTNER", "01/2012", "C")
    assert dana.control and dana.display == "Dana Marie Reyes" and dana.share == "25% to 50%"

    assert direct["MCALLISTER, SAM"].title == "CHIEF COMPLIANCE OFFICER"
    assert direct["MCALLISTER, SAM"].display == "Sam McAllister"

    holdings = direct["HARBORLIGHT HOLDINGS INC."]
    assert (holdings.kind, holdings.title, holdings.code) == ("DE", "SOLE MEMBER", "E")

    # A middle initial "I" is part of the name, not the person/entity column.
    assert direct["OKAFOR, JOHN, I"].kind == "I"
    assert direct["OKAFOR, JOHN, I"].title == "MEMBER" and not direct["OKAFOR, JOHN, I"].control


def test_the_chain_of_indirect_owners_is_read_with_who_owns_whom():
    owners, _ = parse_owners(schedules())
    chain = by_name(o for o in owners if o.indirect)
    assert chain["NORTHSEA HOLDINGS LTD"].through == "HARBORLIGHT HOLDINGS INC."
    assert chain["NORTHSEA HOLDINGS LTD"].foreign

    # "DE" inside a name is not the column: the reading whose remainder starts
    # with an owner already listed is the right one.
    banco = chain["BANCO DE AVILA S.A."]
    assert (banco.kind, banco.through, banco.title, banco.code) == (
        "FE", "NORTHSEA HOLDINGS LTD", "SHAREHOLDER", "C")

    trust = chain["AVILA FAMILY TRUST"]
    assert (trust.kind, trust.through, trust.code) == ("DE", "BANCO DE AVILA S.A.", "F")


def test_the_identifying_number_column_is_never_kept():
    """It can hold a CRD, a tax number, or a Social Security number."""
    owners, _ = parse_owners(schedules())
    stored = json.dumps([o.to_dict() for o in owners])
    assert "6000001" not in stored and "12-3456789" not in stored


def test_no_indirect_owners_is_read_as_none_not_as_unread():
    owners, read = parse_owners(schedules(b_rows=""))
    assert read and len(owners) == 4 and not any(o.indirect for o in owners)


def test_a_copy_cut_off_by_a_page_break_is_not_mistaken_for_no_owners():
    """Unread is not empty: owners missed tonight would all be 'new' tomorrow."""
    cut = "Item 12\n" + A_HEAD + "REYES, DANA, MARIE I MANAGING PARTNER 01/2012 C Y N 6000001\n"
    owners, read = parse_owners(cut)
    assert owners == [] and read is False

    owners, read = parse_owners("no ownership schedules in this text")
    assert owners == [] and read is False


def test_the_complete_copy_wins_over_the_cut_off_ones_before_it():
    cut = "Item 12\n" + A_HEAD + "REYES, DANA, MARIE I MANAGING PARTNER 01/2012 C Y N 6000001\n"
    owners, read = parse_owners(cut + schedules() + schedules())
    assert read and len(owners) == 7


def test_a_snapshot_survives_storage_and_an_older_one_still_loads():
    owners, _ = parse_owners(schedules())
    snap = AdviserSnapshot(crd="1", name="X", owners=owners, owners_read=True,
                           reader_version=READER_VERSION)
    back = AdviserSnapshot.from_dict(json.loads(json.dumps(snap.to_dict())))
    assert back.owners == owners and back.owners_read

    old = AdviserSnapshot.from_dict({"crd": "1", "name": "X", "funds_read": True})
    assert old.owners == [] and not old.owners_read and old.reader_version == 0


# ------------------------------------------------------------------ changes

def snap(owners, read=True, date="03/31/2026"):
    return AdviserSnapshot(crd="999001", name="HARBORLIGHT CAPITAL", filing_date=date,
                           owners=list(owners), owners_read=read)


def owner(name, kind="I", code="C", control=True, title="MEMBER", **kw):
    return Owner(name=name, kind=kind, code=code, control=control, title=title,
                 since="01/2020", **kw)


DANA = owner("REYES, DANA")


def kinds(changes):
    return [c.kind for c in changes]


def test_owners_read_for_the_first_time_report_nothing():
    """A firm stored before the reader knew these schedules must not have all
    of its owners announced the night it is read again."""
    owners, _ = parse_owners(schedules())
    assert compare(snap([], read=False), snap(owners)) == []


def test_a_foreign_owner_up_the_chain_ranks_first_and_reads_plainly():
    new = owner("SIPCO LTD", kind="FE", code="E", title="SHAREHOLDER", indirect=True,
                through="CP HOLDINGS LTD")
    [c] = compare(snap([DANA]), snap([DANA, new]))
    assert c.kind == "new_owner" and c.importance == 6
    assert c.headline == ("HARBORLIGHT CAPITAL added an owner behind its owners: SIPCO LTD, "
                          "a company based outside the United States.")
    assert c.detail == ("SIPCO LTD is a company based outside the United States. It owns "
                        "75% or more of CP HOLDINGS LTD (listed as shareholder since "
                        "01/2020) and is reported as having control.")
    assert c.where == "Schedule B — Indirect Owners"
    assert "does not say which country" in c.explainer


def test_a_new_direct_owner_who_is_a_person_is_named_without_a_label():
    new = owner("OKAFOR, SAM", code="B", control=False)
    [c] = compare(snap([DANA]), snap([DANA, new]))
    assert c.headline == "HARBORLIGHT CAPITAL added a direct owner: Sam Okafor."
    assert c.detail.startswith("Sam Okafor owns 10% to 25% of the firm")
    assert "is not reported as having control" in c.detail and c.importance == 4


def test_new_executives_with_no_stake_are_one_low_item():
    cfo = owner("AMES, RUTH", code="NA", title="CHIEF FINANCIAL OFFICER")
    cco = owner("HALE, LIN", code="NA", title="CHIEF COMPLIANCE OFFICER")
    [c] = compare(snap([DANA]), snap([DANA, cfo, cco]))
    assert c.kind == "new_officer" and c.importance == 2
    assert "added 2 people to its list of executives" in c.headline
    assert c.detail == ("Ruth Ames (chief financial officer); "
                        "Lin Hale (chief compliance officer).")


def test_an_owner_leaving_a_share_rising_and_control_changing_are_reported():
    before = [DANA, owner("OKAFOR, SAM", code="A", control=False),
              owner("NORTHSEA HOLDINGS LTD", kind="FE", code="C", title="SHAREHOLDER")]
    after = [owner("OKAFOR, SAM", code="C", control=True),
             owner("NORTHSEA HOLDINGS LTD", kind="FE", code="D", title="SHAREHOLDER")]
    changes = compare(snap(before), snap(after))
    assert sorted(kinds(changes)) == ["owner_control", "owner_gone", "owner_share",
                                      "owner_share"]
    foreign = next(c for c in changes if "NORTHSEA" in c.headline)
    assert foreign.importance == 6
    assert "went from 25% to 50% to 50% to 75%" in foreign.headline
    gone = next(c for c in changes if c.kind == "owner_gone")
    assert "Dana Reyes is no longer listed" in gone.headline
    assert gone.detail.startswith("On the previous filing: Dana Reyes owns 25% to 50%")


def test_an_owner_newly_marked_foreign_is_reported():
    before = [owner("NORTHSEA HOLDINGS LLC", kind="DE", title="MEMBER")]
    after = [owner("NORTHSEA HOLDINGS LLC", kind="FE", title="MEMBER")]
    [c] = compare(snap(before), snap(after))
    assert c.kind == "owner_kind" and c.importance == 6
    assert "is now reported as a company based outside the United States" in c.headline


# ------------------------------------------------------------------ refresh

class StubHttp:
    def __init__(self):
        self.pdf_calls = 0

    def get(self, url, **kw):
        return {"status": 200, "json": {"hits": {"hits": [{"_source": {"iacontent": json.dumps({
            "basicInformation": {"firmName": "HARBORLIGHT CAPITAL",
                                 "advFilingDate": "03/31/2026"}})}}]}}}

    def get_bytes(self, url, **kw):
        self.pdf_calls += 1
        return (200, b"%PDF-stub")


def test_a_firm_stored_by_an_older_reader_is_read_once_more(monkeypatch):
    """Otherwise its owners would stay blank until it next files — up to a year."""
    monkeypatch.setattr(adv, "read_form", lambda _: parse_form(schedules()))
    http = StubHttp()
    conn = AdvConnector(http)
    stored = AdviserSnapshot(crd="999001", name="HARBORLIGHT CAPITAL",
                             filing_date="03/31/2026", funds_read=True)   # version 0

    fresh = conn.refresh("999001", previous=stored)
    assert http.pdf_calls == 1 and fresh.owners_read and len(fresh.owners) == 7
    assert compare(stored, fresh) == [], "same filing, newly read owners: nothing to report"

    conn.refresh("999001", previous=fresh)
    assert http.pdf_calls == 1, "read once, not every night"


def test_a_stray_word_in_front_of_a_name_is_still_the_same_owner():
    """A wrapped row at the edge of a band leaves its second line in front of
    the next row's name. Where the edge falls moves between filings; the owner
    did not."""
    plain = [DANA, owner("OKAFOR, SAM", code="B", control=False)]
    stray = [DANA, owner("OFFICER OKAFOR, SAM", code="B", control=False)]
    assert compare(snap(plain), snap(stray)) == []
    assert compare(snap(stray), snap(plain)) == []

    # A real change to that owner is still seen through the stray word.
    promoted = [DANA, owner("OFFICER OKAFOR, SAM", code="C", control=True)]
    assert sorted(kinds(compare(snap(plain), snap(promoted)))) == ["owner_control",
                                                                  "owner_share"]


# ----------------------------------------------------------------------- API

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


def test_a_firm_in_a_portfolio_shows_how_many_owners_are_foreign(client):
    c, app_module = client
    store = app_module.store_for("acme")
    owners, _ = parse_owners(schedules())
    read = AdviserSnapshot(crd="999001", name="HARBORLIGHT CAPITAL", funds_read=True,
                           owners=owners, owners_read=True)
    store.save_adv_snapshot("999001", read.to_dict(), "03/31/2026")
    store.save_adv_snapshot("999002", AdviserSnapshot(crd="999002", name="OLD READING",
                                                      funds_read=True).to_dict(), "")
    key = pf.encode(pf.from_entity_keys("Watch", ["CRD:999001", "CRD:999002"]))
    rows = c.post("/v1/portfolio", headers=AUTH, json={"key": key}).json()["companies"]
    assert rows[0]["foreign_owners"] == 2
    assert rows[1]["foreign_owners"] is None, "not read is not the same as none"

    shown = c.get("/v1/advisers/999001", headers=AUTH).json()["snapshot"]["owners"]
    assert shown[0]["display"] == "Dana Marie Reyes" and shown[0]["share"] == "25% to 50%"
