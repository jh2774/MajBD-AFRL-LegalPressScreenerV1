"""Which investment firm manages a fund named after a contractor.

The claim "this firm manages this fund" rests on one thing: the firm's own
Form ADV listing a fund of exactly that name. Everything else here — similar
names, an officer's registration — only decides which firms get read, and the
tests hold it to that.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from foci_screen import fund_managers as fm
from foci_screen import vehicles
from foci_screen.connectors.adv import AdvConnector, AdviserSnapshot, PrivateFund
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}
MW = "MANHATTAN WEST ASSET MANAGEMENT, LLC"


# ------------------------------------------------------------- pure helpers

def test_one_funds_name_on_two_filings_is_compared_letter_for_letter():
    assert fm.squash("MW LSVC Shield AI, LLC") == fm.squash("MW LSVC SHIELD AI, LLC")
    assert fm.squash("Shield.AI Fund, L.P.") == fm.squash("SHIELD AI FUND LP")
    assert fm.squash("MW LSVC Shield AI, LLC") != fm.squash("MW LSVC Shield AI-II, LLC")


def test_the_company_a_fund_is_a_series_of():
    assert fm.parent_name("HII Shield AI-05, a Series of HII Shield AI-A LLC") \
        == "HII Shield AI-A LLC"
    assert fm.parent_name("Fuel Venture Capital Shield AI, LLC a Protected Series of Fuel "
                          "Venture Capital Co-Invest Series, LLC") \
        == "Fuel Venture Capital Co-Invest Series, LLC"
    assert fm.parent_name("Greenbird Intelligence Fund, LLC, Series W - Shield AI") \
        == "Greenbird Intelligence Fund, LLC"
    assert fm.parent_name("SHIELD AI A SERIES OF VUVP FUND LLC") == "VUVP FUND LLC"
    assert fm.parent_name("MW LSVC Shield AI, LLC") == ""


# ----------------------------------------------- what firms have already said

@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "managers.db"), tenant_id="acme")
    yield s
    s.close()


def fund(n: int, name: str, filed: str = "2026-05-01", people=()) -> dict:
    return {"accession": f"{n:010d}-26-{n:06d}", "cik": str(9000000 + n), "name": name,
            "form": "D", "filed": filed,
            "facts": {"read_ok": True, "amount_sold": 1_000_000, "investors": 10,
                      "people": [{"name": p, "role": "", "place": "", "outside_us": False}
                                 for p in people]}}


def on_file(store, *funds_, phrase="Shield AI", key="UEI777"):
    """Funds as a look would have left them: indexed, read, and watched."""
    store.save_vehicle_watch(key, phrase=phrase, looked=True)
    store.add_formd_index(list(funds_), "lookup")
    for f in funds_:
        store.set_formd_facts(f["accession"], f["facts"])
    return key


def firm(store, crd, name, funds_, filed="07/14/2026", read=True):
    """A firm's Form ADV as stored: [(fund name, % owned outside the U.S.)]."""
    snap = AdviserSnapshot(
        crd=crd, name=name, filing_date=filed, funds_read=read,
        funds=[PrivateFund(fund_id=f"805-{crd}{i}", name=n, owned_by_non_us_pct=pct,
                           gross_asset_value=4_000_000, investors=14, country="United States")
               for i, (n, pct) in enumerate(funds_)])
    store.save_adv_snapshot(crd, snap.to_dict(), filed)
    return snap


def by_cik(page):
    return {f["cik"]: f for f in page["vehicles"]}


def test_a_fund_is_tied_to_a_firm_by_the_firms_own_filing_and_nothing_else(store):
    key = on_file(store, fund(1, "MW LSVC Shield AI, LLC"),
                  fund(2, "MW LSVC Shield AI-II, LLC"),
                  fund(3, "Shield Capital Shield AI Co-Invest LLC"))
    firm(store, "283630", MW, [("MW LSVC SHIELD AI, LLC", 21),
                               ("MW LSVC SHIELD AI-II, LLC", 0),
                               ("MW REAL ESTATE FUND, LP", 5)])
    # A name every bit as similar as a name can be, and no fund of theirs matches.
    firm(store, "111111", "SHIELD CAPITAL MANAGEMENT LLC",
         [("SHIELD CAPITAL FUND I, LP", 40)])

    page = vehicles.view(store, key)
    funds = by_cik(page)
    [said] = funds["9000001"]["reported"]
    assert (said["firm"], said["crd"], said["non_us_pct"]) == (MW, "283630", 21)
    assert said["filing_date"] == "07/14/2026" and said["gross_asset_value"] == 4_000_000
    assert funds["9000002"]["reported"][0]["non_us_pct"] == 0, "0% is a figure, not a blank"
    assert funds["9000003"]["reported"] == [] and funds["9000003"]["reported_parent"] == []
    assert page["managers"] == [{"crd": "283630", "name": MW, "filing_date": "07/14/2026",
                                 "funds": 2, "series": 0, "only": 0}]
    assert page["totals"]["reported"] == 2 and page["reported_only"] == []


def test_a_series_is_tied_to_the_company_the_firm_reports_and_says_so(store):
    """The firm's figure is then for every series together."""
    key = on_file(
        store, fund(1, "Greenbird Intelligence Fund, LLC, Series W - Shield AI"),
        fund(2, "HII Shield AI-05, a Series of HII Shield AI-A LLC"),
        fund(3, "HII Shield AI-04, a Series of HII Shield AI-A LLC"))
    firm(store, "324547", "GREENBIRD INTELLIGENCE MANAGEMENT, LLC",
         [("GREENBIRD INTELLIGENCE FUND, LLC", 12)])
    firm(store, "555555", "HII ADVISERS LLC",
         [("HII SHIELD AI-A LLC", 3), ("HII SHIELD AI-05, A SERIES OF HII SHIELD AI-A LLC", 9)])

    page = vehicles.view(store, key)
    funds = by_cik(page)
    assert funds["9000001"]["reported"] == []
    [umbrella] = funds["9000001"]["reported_parent"]
    assert umbrella["name"] == "GREENBIRD INTELLIGENCE FUND, LLC"
    assert umbrella["non_us_pct"] == 12
    # Listed in its own right: the series' own figure, not the company's.
    assert funds["9000002"]["reported"][0]["non_us_pct"] == 9
    assert funds["9000002"]["reported_parent"] == []
    assert funds["9000003"]["reported_parent"][0]["non_us_pct"] == 3
    counts = {m["name"]: (m["funds"], m["series"], m["only"]) for m in page["managers"]}
    assert counts == {"HII ADVISERS LLC": (1, 1, 0),
                      "GREENBIRD INTELLIGENCE MANAGEMENT, LLC": (0, 1, 0)}
    assert page["reported_only"] == [], "the company is accounted for by its series"
    assert page["totals"]["reported"] == 3


def test_a_fund_only_a_form_adv_knows_about_is_listed_apart(store):
    """Sold abroad, say, so no Form D was ever due."""
    ruled_out = fund(2, "Shield AI Sidecar LLC")
    key = on_file(store, fund(1, "MW LSVC Shield AI, LLC"), ruled_out)
    firm(store, "283630", MW, [("MW LSVC SHIELD AI, LLC", 21),
                               ("MW SHIELD AI OFFSHORE FUND, LTD", 100),
                               ("SHIELD AI SIDECAR LLC", 50),
                               ("MW LSV ANDURIL INDUSTRIES-II, LLC", 20)])
    store.save_vehicle_watch(key, unrelated=[ruled_out["cik"]])

    page = vehicles.view(store, key)
    [extra] = page["reported_only"]
    assert (extra["name"], extra["non_us_pct"]) == ("MW SHIELD AI OFFSHORE FUND, LTD", 100)
    assert page["managers"][0]["only"] == 1 and page["managers"][0]["funds"] == 1
    assert [f["cik"] for f in page["vehicles"]] == ["9000001"], \
        "and a fund ruled out stays out, on either form"


def test_two_firms_listing_one_fund_are_both_named_newest_filing_first(store):
    key = on_file(store, fund(1, "MW LSVC Shield AI, LLC"))
    firm(store, "100", "OLDER FILER LLC", [("MW LSVC SHIELD AI, LLC", 10)], filed="03/31/2025")
    firm(store, "200", "NEWER FILER LLC", [("MW LSVC SHIELD AI, LLC", 15)], filed="01/15/2026")
    said = by_cik(vehicles.view(store, key))["9000001"]["reported"]
    assert [e["firm"] for e in said] == ["NEWER FILER LLC", "OLDER FILER LLC"]


def test_nothing_on_file_is_nothing_said(store):
    key = on_file(store, fund(1, "MW LSVC Shield AI, LLC"))
    page = vehicles.view(store, key)
    assert page["managers"] == [] and page["reported_only"] == []
    assert page["vehicles"][0]["reported"] == [] and page["totals"]["reported"] == 0
    assert vehicles.view(store, "NEVER-LOOKED-UP")["managers"] == []


# ------------------------------------------- the same link, seen from the firm

def test_a_form_adv_alert_about_a_fund_named_after_a_contractor_says_so(store):
    """And goes above the same change to a fund that is not."""
    from foci_screen import alerts
    from foci_screen.connectors.adv import compare

    def filing(date, shield, estate, extra=()):
        return AdviserSnapshot(
            crd="283630", name=MW, filing_date=date, funds_read=True,
            funds=[PrivateFund("805-1", "MW LSVC SHIELD AI, LLC", owned_by_non_us_pct=shield,
                               gross_asset_value=4_000_000),
                   PrivateFund("805-2", "MW REAL ESTATE FUND, LP", owned_by_non_us_pct=estate,
                               gross_asset_value=4_000_000),
                   *extra])

    store.save_vehicle_watch("UEI777", phrase="Shield AI")       # looked up, alerts off
    sid = store.save_alert_subscription(name="Watch", emails=["ko@agency.gov"],
                                        companies=["CRD:283630", "UEI777"])
    new_fund = PrivateFund("805-3", "MW LSVC SHIELD AI-III, LLC", owned_by_non_us_pct=40,
                           gross_asset_value=1_000_000)
    changes = compare(filing("03/31/2026", 21, 5), filing("07/14/2026", 30, 9, [new_fund]))
    store.record_adv_changes([c.to_dict() for c in changes])

    items = alerts.pending_items(store, store.alert_subscription(sid), "")
    by_headline = {i["headline"]: i for i in items}
    tie = "This fund is named after Shield AI, which is in this portfolio. The link is the name"
    shield = by_headline["The share of MW LSVC SHIELD AI, LLC owned by investors outside the "
                         "United States rose from 21% to 30%."]
    estate = by_headline["The share of MW REAL ESTATE FUND, LP owned by investors outside the "
                         "United States rose from 5% to 9%."]
    assert shield["detail"].startswith("The fund holds $4.0 million.")
    assert tie in shield["detail"] and tie not in estate["detail"]
    assert shield["importance"] == estate["importance"] + 1 and items[0] is shield
    added = by_headline[f"{MW} reported a new private fund: MW LSVC SHIELD AI-III, LLC."]
    assert tie in added["detail"]
    filed = next(i for i in items if "filed an updated Form ADV" in i["headline"])
    assert tie not in filed["detail"], "a filing is not a fund"

    # A portfolio without the contractor is told about the firm and nothing more.
    other = store.save_alert_subscription(name="Firms only", emails=["a@b.gov"],
                                          companies=["CRD:283630"])
    assert all(tie not in i["detail"]
               for i in alerts.pending_items(store, store.alert_subscription(other), ""))


def test_a_firm_sharing_the_contractors_name_does_not_make_its_funds_the_contractors():
    watches = [{"entity_key": "UEI9", "phrase": "Anduril"}]
    change = {"kind": "new_fund", "firm": "ANDURIL PARTNERS LLC",
              "headline": "ANDURIL PARTNERS LLC reported a new private fund: "
                          "GROWTH FUND II, LP."}
    assert fm.tie_to_contractor(change, watches) == ""
    change["headline"] = ("ANDURIL PARTNERS LLC reported a new private fund: "
                          "AP ANDURIL CO-INVEST, LP.")
    assert "named after Anduril" in fm.tie_to_contractor(change, watches)
    assert fm.tie_to_contractor(change, []) == ""


# ------------------------------------------------------------ where to look

FUNDS = [
    fund(1, "Greenbird Intelligence Fund, LLC, Series W - Shield AI",
         people=["Greenbird Intelligence Management LLC", "Michael Gestone"]),
    fund(2, "Fuel Venture Capital Shield AI, LLC a Protected Series of Fuel Venture Capital "
            "Co-Invest Series, LLC", people=["Jeff Ransdell"]),
    fund(3, "MW LSVC Shield AI, LLC", people=["Lorenzo Esparza", "Hubert Ryan"]),
    fund(4, "Shield AI May 2026 a Series of CGF2021 LLC",
         people=["Sydecar LLC", "Brett Sagan"]),
    fund(5, "HII Shield AI-05, a Series of HII Shield AI-A LLC",
         people=["Christopher DeLap"]),
    fund(6, "Edge Partners LLC, Series I Shield AI",
         people=["Edge Partners Management LLC"]),
    fund(7, "Gaingels Shield AI 2023 LLC", people=["Lorenzo Esparza"]),
]


def as_page(funds_):
    """Funds in the shape `vehicles.view` hands them over."""
    return [{"cik": f["cik"], "name": f["name"], "latest": f, "reported": [],
             "reported_parent": []} for f in funds_]


def test_what_is_looked_up_comes_from_the_filings_themselves():
    terms = fm.search_terms(as_page(FUNDS), "Shield AI")
    assert [q for q, _ in terms["named"]] == [
        "greenbird intelligence management", "sydecar", "edge partners management"]
    assert terms["people"] == ["Michael Gestone", "Jeff Ransdell", "Lorenzo Esparza",
                               "Hubert Ryan", "Brett Sagan", "Christopher DeLap"]
    loose = [q for q, _ in terms["loose"]]
    assert loose == ["fuel venture capital", "mw lsvc", "gaingels"], (
        "not the contractor's own name, not a platform's serial number, not three "
        "letters, and not what a company on the filing already covers")


def test_a_fund_whose_manager_is_known_is_not_looked_up_again():
    page = as_page(FUNDS[:3])
    page[0]["reported_parent"] = [{"crd": "324547"}]
    page[2]["reported"] = [{"crd": "283630"}]
    terms = fm.search_terms(page, "Shield AI")
    assert terms["named"] == [] and terms["people"] == ["Jeff Ransdell"]
    assert [q for q, _ in terms["loose"]] == ["fuel venture capital"]


class FakeAdv:
    """The SEC's adviser database: firms by name, a person's firm, a firm's form."""

    def __init__(self):
        self.by_query: dict[str, list[dict]] = {}
        self.by_person: dict[str, list[dict]] = {}
        self.forms: dict[str, AdviserSnapshot] = {}
        self.searched: list[str] = []
        self.looked_up: list[str] = []
        self.read: list[str] = []
        self.down = False

    def hits(self, query, *firms_):
        self.by_query[query] = [{"crd": c, "name": n, "status": s, "where": ""}
                                for c, n, s in firms_]

    def search(self, query, limit=8):
        self.searched.append(query)
        if self.down:
            raise ConnectionError("no route")
        return self.by_query.get(query, [])

    def employers_of(self, person):
        self.looked_up.append(person)
        if self.down:
            raise ConnectionError("no route")
        return self.by_person.get(person, [])

    def refresh(self, crd, previous=None):
        self.read.append(crd)
        return self.forms.get(crd) or AdviserSnapshot(crd=crd, name=f"FIRM {crd}",
                                                      filing_date="01/01/2026")


def a_database():
    adv = FakeAdv()
    adv.hits("greenbird intelligence management",
             ("324547", "GREENBIRD INTELLIGENCE MANAGEMENT, LLC", "ACTIVE"),
             ("310382", "GREENBIRD ADVISORS, LLC", "INACTIVE"))
    adv.hits("edge partners management",
             ("298360", "METAL EDGE PARTNERS, LLC", "ACTIVE"),
             ("281846", "EDGESTONE PARTNERS, INC.", "ACTIVE"),
             ("318752", "PARTNERS EDGE MANAGEMENT", "ACTIVE"))
    adv.hits("fuel venture capital",
             ("304225", "FUEL VENTURE CAPITAL PARTNERS LLC", "ACTIVE"),
             ("999001", "SHIELD VENTURE CAPITAL LLC", "ACTIVE"))
    adv.hits("gaingels", ("317100", "GAINGELS MANAGEMENT LLC", "ACTIVE"),
             ("319530", "GAINGELS 10X CAPITAL DIVERSITY MANAGEMENT LLC", "ACTIVE"))
    adv.by_person["Lorenzo Esparza"] = [{"crd": "283630", "name": MW}]
    return adv


def test_firms_worth_reading_best_lead_first():
    adv = a_database()
    found = fm.find_candidates(adv, as_page(FUNDS), "Shield AI")
    assert [f["name"] for f in found] == [
        "GREENBIRD INTELLIGENCE MANAGEMENT, LLC",       # a company named on a filing
        MW,                                             # where an officer is registered
        "FUEL VENTURE CAPITAL PARTNERS LLC",            # its name is in the fund's name
        "GAINGELS MANAGEMENT LLC"]
    names = {f["name"] for f in found}
    assert "GREENBIRD ADVISORS, LLC" not in names, "no longer registered: no current form"
    assert not names & {"METAL EDGE PARTNERS, LLC", "EDGESTONE PARTNERS, INC.",
                        "PARTNERS EDGE MANAGEMENT"}, "'edge' alone is not a name"
    assert "SHIELD VENTURE CAPITAL LLC" not in names, \
        "sharing the contractor's name is not sharing the fund's"
    assert "GAINGELS 10X CAPITAL DIVERSITY MANAGEMENT LLC" not in names
    assert adv.looked_up.count("Lorenzo Esparza") == 1, "named on two funds, asked once"


def test_one_failed_lookup_is_skipped_and_all_of_them_failing_is_said():
    adv = a_database()
    real = adv.search

    def flaky(query, limit=8):
        if query == "sydecar":
            raise TimeoutError("slow")
        return real(query, limit)

    adv.search = flaky
    assert len(fm.find_candidates(adv, as_page(FUNDS), "Shield AI")) == 4

    adv = a_database()
    adv.down = True
    with pytest.raises(RuntimeError, match="did not answer"):
        fm.find_candidates(adv, as_page(FUNDS), "Shield AI")
    assert fm.find_candidates(FakeAdv(), [], "Shield AI") == [], \
        "nothing to ask is not a failure"


def test_a_firm_that_could_not_be_read_is_not_downloaded_again_for_a_week():
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)

    def tried(days_ago, read):
        when = (now - timedelta(days=days_ago)).replace(tzinfo=None).isoformat() + "Z"
        return {"funds_read": read, "checked_at": when}

    assert fm.worth_reading(None, now) is True
    assert fm.worth_reading(tried(0, True), now) is True, "one small request says if it refiled"
    assert fm.worth_reading(tried(2, False), now) is False
    assert fm.worth_reading(tried(8, False), now) is True
    assert fm.worth_reading({"funds_read": False, "checked_at": "nonsense"}, now) is True


# ------------------------------------------------- a person's firm, on the wire

class People:
    def __init__(self, *people):
        self.people = people
        self.calls = 0

    def get(self, url, params=None, **kw):
        self.calls += 1
        assert url.endswith("/search/individual") and params["query"]
        return {"status": 200, "json": {"hits": {"hits": [
            {"_source": {"ind_firstname": first, "ind_lastname": last,
                         "ind_ia_current_employments": [
                             {"firm_id": crd, "firm_name": name} for crd, name in jobs]}}
            for first, last, jobs in self.people]}}}


def test_a_persons_firm_counts_only_when_the_name_is_exact_and_belongs_to_one_person():
    mw = [(283630, MW), (283630, MW)]
    one = People(("LORENZO", "ESPARZA", mw), ("LORENA", "ESPARZA", [(1, "OTHER LLC")]))
    assert AdvConnector(one).employers_of("Lorenzo Esparza") == [{"crd": "283630", "name": MW}]
    assert AdvConnector(one).employers_of("Lorenzo Daniel Esparza") == \
        [{"crd": "283630", "name": MW}], "a middle name on the filing changes nothing"

    # The search is loose: asked for DeLap, it answers with Delaneys.
    loose = People(("CHRISTOPHER", "DELANEY", [(19616, "WELLS FARGO ADVISORS")]))
    assert AdvConnector(loose).employers_of("Christopher DeLap") == []

    two = People(("JOHN", "SMITH", [(1, "A LLC")]), ("John", "Smith", [(2, "B LLC")]))
    assert AdvConnector(two).employers_of("John Smith") == [], "which John Smith?"

    unregistered = People(("ANDREW", "BAIR", []))
    assert AdvConnector(unregistered).employers_of("Andrew Bair") == []

    nobody = People()
    assert AdvConnector(nobody).employers_of("Sydecar") == [] and nobody.calls == 0


# ----------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ALERTS_SEND", raising=False)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    adv = a_database()
    monkeypatch.setattr(app_module, "_adv", lambda: adv)
    return TestClient(app_module.app), app_module.store_for("acme"), adv


URL = "/v1/entities/UEI777/vehicles/managers"


def test_looking_for_managers_needs_the_funds_first(client):
    c, _, adv = client
    assert c.post(URL, headers=AUTH, json={"find": True}).status_code == 409
    assert c.post(URL, json={"find": True}).status_code == 401
    assert c.post(URL, headers=AUTH, json={"check": "not-a-number"}).status_code == 422
    assert adv.searched == []


def test_find_then_read_each_firm_and_only_its_own_filing_names_it(client):
    c, store, adv = client
    on_file(store, *FUNDS)
    adv.forms["283630"] = AdviserSnapshot(
        crd="283630", name=MW, filing_date="07/14/2026", funds_read=True,
        funds=[PrivateFund(fund_id="805-1", name="MW LSVC SHIELD AI, LLC",
                           owned_by_non_us_pct=21)])
    adv.forms["304225"] = AdviserSnapshot(
        crd="304225", name="FUEL VENTURE CAPITAL PARTNERS LLC", filing_date="03/31/2026",
        funds_read=True,
        funds=[PrivateFund(fund_id="805-2", name="FUEL VENTURE CAPITAL CAYMAN, LTD")])

    page = c.post(URL, headers=AUTH, json={"find": True}).json()
    crds = [f["crd"] for f in page["candidates"]]
    assert crds[:2] == ["324547", "283630"] and set(crds[2:]) == {"304225", "317100"}
    assert page["managers"] == [] and adv.read == [], "finding where to look reads nothing"

    page = c.post(URL, headers=AUTH, json={"check": "283630"}).json()
    assert page["checked"] == {"crd": "283630", "name": MW, "funds_read": True, "lists": 1}
    assert by_cik(page)["9000003"]["reported"][0]["non_us_pct"] == 21
    assert [m["name"] for m in page["managers"]] == [MW]

    page = c.post(URL, headers=AUTH, json={"check": "304225"}).json()
    assert page["checked"]["lists"] == 0 and page["checked"]["funds_read"] is True
    assert [m["name"] for m in page["managers"]] == [MW], \
        "a firm with the fund's name in its own is still not its manager"

    c.post(URL, headers=AUTH, json={"find": True})
    assert adv.looked_up.count("Hubert Ryan") == 1, \
        "named only on a fund whose manager is now known: not asked about again"
    assert c.get("/v1/entities/UEI777/vehicles", headers=AUTH).json()["managers"] \
        == page["managers"], "and it is there the next time the page is opened"


def test_a_firm_whose_form_cannot_be_read_is_said_so_and_not_fetched_twice(client):
    c, store, adv = client
    on_file(store, *FUNDS)                       # 317100's form is left unreadable
    first = c.post(URL, headers=AUTH, json={"check": "317100"}).json()["checked"]
    assert first["funds_read"] is False and first["lists"] == 0
    c.post(URL, headers=AUTH, json={"check": "317100"})
    assert adv.read == ["317100"], "40 MB is not downloaded again to fail the same way"


def test_the_firms_own_page_marks_its_funds_named_after_a_contractor(client):
    c, store, adv = client
    on_file(store, fund(1, "MW LSVC Shield AI, LLC"))
    firm(store, "283630", MW, [("MW LSVC SHIELD AI, LLC", 21), ("MW REAL ESTATE FUND, LP", 5)])
    page = c.get("/v1/advisers/283630", headers=AUTH).json()
    assert page["named_after"] == {
        "805-2836300": [{"entity_key": "UEI777", "phrase": "Shield AI"}]}
    assert adv.read == [], "already on file: opening the page reads nothing"


def test_the_adviser_database_not_answering_is_said_plainly(client):
    c, store, adv = client
    on_file(store, *FUNDS)
    adv.down = True
    r = c.post(URL, headers=AUTH, json={"find": True})
    assert r.status_code == 502 and "adviser database did not answer" in r.json()["detail"]
