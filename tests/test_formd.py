"""A contractor's own Form D fundraising notices, and alerts on them.

The fixture follows the layout of real Form D XML read from EDGAR (element
names, nesting, the "Indefinite" and "yetToOccur" spellings, EDGAR's state and
country codes). Company and people names are invented.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from foci_screen import alerts
from foci_screen import portfolio as pf
from foci_screen.connectors.formd import (
    FormDConnector,
    IssuerSnapshot,
    compare,
    is_outside_us,
    label_entity,
    parse_form_d,
)
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}
CIK = "4400123"

CEO = ("Dana", "Reyes", ("Executive Officer", "Director"), "Chief Executive Officer",
       "TX", "TEXAS", "Austin")
DIRECTOR = ("Sam", "Okafor", ("Director",), "", "TX", "TEXAS", "Austin")
FOREIGN_DIRECTOR = ("Lin", "Hale", ("Director",), "", "X0", "UNITED KINGDOM", "London")


def person_xml(first, last, roles, title, code, desc, city):
    rel = "".join(f"<relationship>{r}</relationship>" for r in roles)
    return f"""
        <relatedPersonInfo>
            <relatedPersonName><firstName>{first}</firstName><lastName>{last}</lastName></relatedPersonName>
            <relatedPersonAddress>
                <street1>1 Main St</street1><city>{city}</city>
                <stateOrCountry>{code}</stateOrCountry>
                <stateOrCountryDescription>{desc}</stateOrCountryDescription>
            </relatedPersonAddress>
            <relatedPersonRelationshipList>{rel}</relatedPersonRelationshipList>
            <relationshipClarification>{title}</relationshipClarification>
        </relatedPersonInfo>"""


def form_d_xml(*, name="Northwind Robotics, Inc.", code="TX", desc="TEXAS", city="Austin",
               jurisdiction="DELAWARE", amendment=False, previous="",
               offered="50000000", sold="20000000", investors="12",
               first_sale="<value>2026-03-02</value>", people=(CEO,),
               combination="false", non_accredited="", recipients="",
               securities="<isEquityType>true</isEquityType>", xmlns=""):
    prev = f"<previousAccessionNumber>{previous}</previousAccessionNumber>" if previous else ""
    new_or_amendment = (f"<isAmendment>{'true' if amendment else 'false'}</isAmendment>"
                        f"{prev}")
    na = (f"<hasNonAccreditedInvestors>true</hasNonAccreditedInvestors>"
          f"<numberNonAccreditedInvestors>{non_accredited}</numberNonAccreditedInvestors>"
          if non_accredited else
          "<hasNonAccreditedInvestors>false</hasNonAccreditedInvestors>")
    return f"""<?xml version="1.0"?>
<edgarSubmission{f' xmlns="{xmlns}"' if xmlns else ''}>
    <schemaVersion>X0708</schemaVersion>
    <submissionType>{'D/A' if amendment else 'D'}</submissionType>
    <primaryIssuer>
        <cik>000{CIK}</cik>
        <entityName>{name}</entityName>
        <issuerAddress>
            <street1>1312 Harbor Lane</street1><city>{city}</city>
            <stateOrCountry>{code}</stateOrCountry>
            <stateOrCountryDescription>{desc}</stateOrCountryDescription>
        </issuerAddress>
        <jurisdictionOfInc>{jurisdiction}</jurisdictionOfInc>
        <entityType>Corporation</entityType>
        <yearOfInc><withinFiveYears>true</withinFiveYears><value>2022</value></yearOfInc>
    </primaryIssuer>
    <relatedPersonsList>{''.join(person_xml(*p) for p in people)}</relatedPersonsList>
    <offeringData>
        <industryGroup><industryGroupType>Other Technology</industryGroupType></industryGroup>
        <typeOfFiling>
            <newOrAmendment>{new_or_amendment}</newOrAmendment>
            <dateOfFirstSale>{first_sale}</dateOfFirstSale>
        </typeOfFiling>
        <typesOfSecuritiesOffered>{securities}</typesOfSecuritiesOffered>
        <businessCombinationTransaction>
            <isBusinessCombinationTransaction>{combination}</isBusinessCombinationTransaction>
        </businessCombinationTransaction>
        <salesCompensationList>{recipients}</salesCompensationList>
        <offeringSalesAmounts>
            <totalOfferingAmount>{offered}</totalOfferingAmount>
            <totalAmountSold>{sold}</totalAmountSold>
            <totalRemaining>30000000</totalRemaining>
        </offeringSalesAmounts>
        <investors>{na}<totalNumberAlreadyInvested>{investors}</totalNumberAlreadyInvested></investors>
        <signatureBlock><signature>
            <nameOfSigner>Dana Reyes</nameOfSigner>
            <signatureTitle>Chief Executive Officer</signatureTitle>
        </signature></signatureBlock>
    </offeringData>
</edgarSubmission>"""


def filing(accession, filed, **kw):
    form = "D/A" if kw.get("amendment") else "D"
    return parse_form_d(form_d_xml(**kw), accession=accession, form=form, filed=filed)


def issuer(*filings):
    """Newest first, as EDGAR lists them."""
    fs = sorted(filings, key=lambda f: f.filed, reverse=True)
    return IssuerSnapshot(cik=CIK, name="Northwind Robotics, Inc.", filings=fs,
                          total_filings=len(fs))


# ------------------------------------------------------------------ reading

def test_every_fact_is_read_from_a_filing():
    f = filing("0004400123-26-000001", "2026-03-10")
    assert f.issuer == "Northwind Robotics, Inc."
    assert f.incorporated_in == "DELAWARE" and f.year_of_inc == "2022"
    assert f.place == "Austin, TEXAS" and not f.outside_us
    assert (f.offering_amount, f.amount_sold, f.investors) == (50_000_000, 20_000_000, 12)
    assert f.first_sale == "2026-03-02"
    assert f.securities == ["shares (equity)"]
    assert not f.business_combination and not f.is_amendment
    assert f.signed_by == "Dana Reyes, Chief Executive Officer"
    [p] = f.related_persons
    assert p.name == "Dana Reyes" and p.roles == ["Executive Officer", "Director"]
    assert p.title == "Chief Executive Officer"


def test_the_awkward_values_are_read_as_meant():
    recipient = """<recipient><recipientName>Harbour Placement Ltd</recipientName>
        <recipientCRDNumber>None</recipientCRDNumber>
        <recipientAddress><city>London</city><stateOrCountry>X0</stateOrCountry>
        <stateOrCountryDescription>UNITED KINGDOM</stateOrCountryDescription></recipientAddress>
        </recipient>"""
    f = filing("a", "2026-01-01", offered="Indefinite", sold="0",
               first_sale="<yetToOccur>true</yetToOccur>", non_accredited="3",
               people=(CEO, FOREIGN_DIRECTOR), recipients=recipient,
               securities="<isDebtType>true</isDebtType><isOptionToAcquireType>true"
                          "</isOptionToAcquireType>")
    assert f.offering_indefinite and f.offering_amount is None
    assert f.first_sale == "not yet"
    assert f.non_accredited == 3
    assert f.securities == ["loans or notes (debt)", "options or warrants"]
    assert [p.outside_us for p in f.related_persons] == [False, True]
    [r] = f.sales_recipients
    assert r.outside_us and r.crd == "" and r.place == "London, UNITED KINGDOM"


def test_an_amendment_names_the_filing_it_replaces():
    f = filing("b", "2026-06-01", amendment=True, previous="0004400123-26-000001")
    assert f.is_amendment and f.amends == "0004400123-26-000001"


def test_a_namespaced_document_reads_the_same():
    f = parse_form_d(form_d_xml(xmlns="http://www.sec.gov/edgar/formd"), accession="a")
    assert f.amount_sold == 20_000_000 and f.related_persons


def test_a_broken_document_is_marked_unread_not_raised():
    f = parse_form_d("<edgarSubmission><primaryIssuer>", accession="a", filed="2026-01-01")
    assert f.read_ok is False


def test_places_outside_the_united_states():
    assert is_outside_us("X0") and is_outside_us("A6") and is_outside_us("F4")
    assert not any(is_outside_us(c) for c in ("TX", "DC", "X1", "PR", "GU", "XX", ""))


def test_edgar_names_are_labelled_to_help_a_person_choose():
    q = "Northwind Robotics"
    assert label_entity("Northwind Robotics Inc", q) == "company"
    series = "ZX Northwind Robotics-02, a Series of ZX Northwind-A LLC"
    assert label_entity(series, q) == "vehicle"
    assert label_entity("MW LSVC Northwind Robotics, LLC", q) == "vehicle"
    assert label_entity("Northwind Fund, LLC", q) == "vehicle"
    assert label_entity("Northwind Robotics Coinvest I, LP", q) == "vehicle"
    assert label_entity("REYES DANA J", q) == "person"
    assert label_entity("NORTHWIND ROBOTICS CORP", q) == "company"


# ------------------------------------------------------------------ changes

def kinds(changes):
    return [c.kind for c in changes]


def test_a_first_look_reports_nothing():
    assert compare(None, issuer(filing("a", "2026-03-10"))) == []


def test_a_new_raise_is_reported_with_its_numbers_in_plain_words():
    first = filing("a", "2025-02-01", people=(CEO,))
    new = filing("b", "2026-03-10", sold="20000000", investors="12", people=(CEO,))
    [c] = compare(issuer(first), issuer(first, new))
    assert c.kind == "new_raise" and c.importance == 5
    assert c.headline == ("Northwind Robotics, Inc. reported raising money privately: "
                          "$20.0 million from 12 investors (Form D filed 2026-03-10).")
    assert "trying to raise $50.0 million" in c.detail
    assert "first sale was on 2026-03-02" in c.detail
    assert "does not name the investors" in c.explainer.lower()
    assert c.links()["filing"].endswith("/4400123/b/xslFormDX01/primary_doc.xml")


def test_a_notice_with_no_sales_says_so_rather_than_zero_dollars():
    new = filing("b", "2026-03-10", sold="0", investors="0",
                 first_sale="<yetToOccur>true</yetToOccur>")
    [c] = compare(issuer(), issuer(new))
    assert "reported no sales yet" in c.headline and "$0" not in c.headline


def test_an_amendment_reports_the_money_moving():
    first = filing("a", "2026-03-10", sold="20000000", investors="12")
    update = filing("b", "2026-09-01", amendment=True, previous="a",
                    sold="45000000", investors="19")
    [c] = compare(issuer(first), issuer(first, update))
    assert c.kind == "amended_raise"
    assert "money raised rose from $20.0 million to $45.0 million" in c.headline
    assert "Investors: 12 before, 19 now." in c.detail


def test_a_new_director_is_reported_once():
    first = filing("a", "2026-03-10", people=(CEO,))
    second = filing("b", "2026-09-01", people=(CEO, DIRECTOR))
    changes = compare(issuer(first), issuer(first, second))
    assert kinds(changes).count("new_person") == 1
    person = next(c for c in changes if c.kind == "new_person")
    assert "Sam Okafor" in person.headline and person.importance == 3


def test_several_new_people_on_one_filing_are_one_item():
    first = filing("a", "2026-03-10", people=(CEO,))
    extra = ("Ruth", "Ames", ("Executive Officer",), "Chief Financial Officer",
             "TX", "TEXAS", "Austin")
    second = filing("b", "2026-09-01", people=(CEO, DIRECTOR, extra))
    [person] = [c for c in compare(issuer(first), issuer(first, second))
                if c.kind == "new_person"]
    assert "names 2 people on its Form D for the first time" in person.headline
    assert person.detail == "Sam Okafor (director); Ruth Ames (Chief Financial Officer)."


def test_a_new_director_abroad_ranks_highest():
    first = filing("a", "2026-03-10", people=(CEO,))
    second = filing("b", "2026-09-01", people=(CEO, FOREIGN_DIRECTOR))
    changes = compare(issuer(first), issuer(first, second))
    assert changes[0].kind in ("new_person", "new_raise") and changes[0].importance == 6
    person = next(c for c in changes if c.kind == "new_person")
    assert "with an address in London, UNITED KINGDOM" in person.headline


def test_a_first_ever_filing_lists_its_people_instead_of_calling_each_new():
    new = filing("a", "2026-03-10", people=(CEO, DIRECTOR))
    changes = compare(issuer(), issuer(new))
    assert kinds(changes) == ["new_raise"]
    assert "Dana Reyes (Chief Executive Officer)" in changes[0].detail
    assert "Sam Okafor (director)" in changes[0].detail


def test_a_merger_and_a_move_abroad_are_reported():
    first = filing("a", "2026-03-10")
    second = filing("b", "2026-09-01", combination="true", code="X0",
                    desc="UNITED KINGDOM", city="London")
    changes = compare(issuer(first), issuer(first, second))
    assert {"merger", "place"} <= set(kinds(changes))
    moved = next(c for c in changes if c.kind == "place")
    assert moved.importance == 6 and "London, UNITED KINGDOM" in moved.headline


def test_a_filing_that_could_not_be_read_is_still_reported():
    first = filing("a", "2026-03-10")
    broken = parse_form_d("<oops", accession="b", form="D", filed="2026-09-01")
    [c] = compare(issuer(first), issuer(first, broken))
    assert c.kind == "unread" and "2026-09-01" in c.headline


def test_a_change_id_is_stable():
    first = filing("a", "2026-03-10")
    new = filing("b", "2026-09-01")
    one = compare(issuer(first), issuer(first, new))
    two = compare(issuer(first), issuer(first, new))
    assert [c.change_id for c in one] == [c.change_id for c in two]


# ------------------------------------------------------------------ refresh

class EdgarStub:
    """The two EDGAR sources: a company's filing list, and each filing."""

    def __init__(self, rows, docs):
        self.rows, self.docs = rows, docs
        self.doc_calls: list[str] = []

    def get(self, url, **kw):
        if "submissions" in url:
            forms, dates, accs = zip(*self.rows, strict=True) if self.rows else ((), (), ())
            return {"status": 200, "json": {
                "name": "Northwind Robotics, Inc.", "stateOfIncorporation": "DE",
                "addresses": {"business": {"city": "AUSTIN", "stateOrCountry": "TX"}},
                "filings": {"recent": {"form": list(forms) + ["8-K"],
                                       "filingDate": list(dates) + ["2026-01-01"],
                                       "accessionNumber": list(accs) + ["x-8k"]}}}}
        if "search-index" in url:
            return {"status": 200, "json": {"hits": {"hits": [
                {"_id": "9000001", "_source": {"entity": "MW LSVC Northwind Robotics, LLC"}},
                {"_id": "9000002", "_source": {"entity": "REYES DANA J"}},
                {"_id": CIK, "_source": {"entity": "Northwind Robotics, Inc."}}]}}}
        acc = url.split("/")[-2]
        self.doc_calls.append(acc)
        doc = self.docs.get(acc)
        return {"status": 200 if doc else 404, "text": doc or ""}


def test_only_filings_not_read_before_are_fetched():
    http = EdgarStub([("D", "2026-03-10", "0004400123-26-000001")],
                     {"000440012326000001": form_d_xml()})
    conn = FormDConnector(http)
    first = conn.refresh(CIK)
    assert http.doc_calls == ["000440012326000001"] and first.total_filings == 1
    assert first.place == "Austin, TX" and first.incorporated_in == "DE"

    http.rows.insert(0, ("D/A", "2026-09-01", "0004400123-26-000002"))
    http.docs["000440012326000002"] = form_d_xml(amendment=True, sold="45000000")
    second = conn.refresh(CIK, previous=first)
    assert http.doc_calls[1:] == ["000440012326000002"], "the first filing is not refetched"
    assert [f.accession for f in second.filings] == ["0004400123-26-000002",
                                                     "0004400123-26-000001"]


def test_a_filing_that_failed_to_load_is_tried_again():
    http = EdgarStub([("D", "2026-03-10", "0004400123-26-000001")], {})
    conn = FormDConnector(http)
    first = conn.refresh(CIK)
    assert first.filings[0].read_ok is False
    http.docs["000440012326000001"] = form_d_xml()
    again = conn.refresh(CIK, previous=first)
    assert again.filings[0].read_ok and again.filings[0].amount_sold == 20_000_000


def test_search_puts_companies_first_and_describes_them():
    conn = FormDConnector(EdgarStub([("D", "2026-03-10", "acc")], {}))
    results = conn.search("Northwind Robotics")
    assert [r["label"] for r in results] == ["company", "vehicle", "person"]
    assert results[0]["form_d_count"] == 1 and results[0]["place"] == "Austin, TX"
    assert "form_d_count" not in results[1], "vehicles are labelled, not looked up"


# ------------------------------------------------------------ alerts, end to end

class FakeCfg:
    alerts_send = False
    smtp_host = ""
    smtp_port = 587
    smtp_user = ""
    smtp_password = ""
    smtp_security = "starttls"
    alerts_from = ""
    email_redirect_to = ""


class FakeAdv:
    def refresh(self, crd, previous=None):
        raise AssertionError("no investment firms in these portfolios")


class FakeFormD:
    def __init__(self):
        self.current: dict[str, IssuerSnapshot] = {}
        self.calls: list[str] = []

    def refresh(self, cik, previous=None):
        self.calls.append(cik)
        return self.current[cik]


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "formd.db"), tenant_id="acme")
    yield s
    s.close()


def subscribe(store, companies):
    key = pf.encode(pf.from_entity_keys("Northwind watch", list(companies)))
    sid = store.save_alert_subscription(name="Northwind watch", emails=["ko@agency.gov"],
                                        companies=list(companies), portfolio_key=key)
    alerts.baseline(store, sid, list(companies))
    return sid


def confirm(store, key="UEI777", cik=CIK):
    store.set_entity_link(key, status="confirmed", cik=cik.zfill(10), decided_by="test")


def test_a_raise_after_subscribing_is_emailed_with_where_to_look(store):
    confirm(store)
    formd = FakeFormD()
    first = filing("0004400123-26-000001", "2026-03-10")
    formd.current[CIK] = issuer(first)
    alerts.refresh_issuers(store, formd, [CIK])              # the baseline reading

    sid = subscribe(store, ["UEI777"])
    formd.current[CIK] = issuer(first, filing("0004400123-26-000002", "2026-09-01",
                                              sold="45000000", investors="19"))
    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()),
                               "https://site.example", formd=formd)
    assert result["companies_checked"] == 1
    [delivery] = store.alert_deliveries(sid)
    body = delivery["body_text"]
    assert "reported raising money privately: $45.0 million from 19 investors" in body
    assert "What this is: Form D is the short notice" in body
    assert "Where to look: Form D, Item 13" in body
    assert "xslFormDX01/primary_doc.xml" in body
    assert "https://site.example/#/entity/UEI777" in body


def test_an_unreviewed_name_match_is_neither_checked_nor_emailed(store):
    """A similarity score is not enough to tell someone a company raised money."""
    store.record_auto_link("UEI777", entity_name="NORTHWIND", cik=CIK.zfill(10),
                           confidence=0.93)
    formd = FakeFormD()
    sid = subscribe(store, ["UEI777"])
    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), formd=formd)
    assert formd.calls == [] and result["companies_checked"] == 0
    assert store.alert_deliveries(sid) == []


def test_picking_a_company_after_subscribing_does_not_send_its_history(store):
    formd = FakeFormD()
    first = filing("0004400123-26-000001", "2026-03-10")
    formd.current[CIK] = issuer(first)
    alerts.refresh_issuers(store, formd, [CIK])
    formd.current[CIK] = issuer(first, filing("0004400123-26-000002", "2026-09-01"))
    alerts.refresh_issuers(store, formd, [CIK])              # recorded; nobody linked yet

    sid = subscribe(store, ["UEI777"])
    confirm(store)
    alerts.baseline_entity(store, "UEI777")
    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), formd=formd)
    assert result["emails"][0]["status"] == "nothing new"
    assert store.alert_deliveries(sid) == []


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
    return TestClient(app_module.app), app_module


def test_nothing_is_attributed_until_a_person_picks_the_company(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.record_auto_link("UEI777", entity_name="NORTHWIND", cik=CIK.zfill(10),
                           confidence=0.93)
    body = c.get("/v1/entities/UEI777/formd", headers=AUTH).json()
    assert body["linked"] is False and "snapshot" not in body
    assert body["link"]["status"] == "auto"


def test_a_picked_company_shows_its_filings_with_links(client):
    c, app_module = client
    store = app_module.store_for("acme")
    snap = issuer(filing("0004400123-26-000001", "2026-03-10"))
    store.save_formd_snapshot(CIK, snap.to_dict(), "2026-03-10")
    r = c.post("/v1/entities/UEI777/identity", headers=AUTH,
               json={"status": "confirmed", "cik": CIK.zfill(10),
                     "matched_title": "Northwind Robotics, Inc."})
    assert r.status_code == 200

    body = c.get("/v1/entities/UEI777/formd", headers=AUTH).json()
    assert body["linked"] and body["cik"] == CIK
    [f] = body["snapshot"]["filings"]
    assert f["url"].endswith("/000440012326000001/xslFormDX01/primary_doc.xml")
    assert body["links"]["company"] == f"https://www.sec.gov/edgar/browse/?CIK={CIK}"

    key = pf.encode(pf.from_entity_keys("Watch", ["UEI777"]))
    row = c.post("/v1/portfolio", headers=AUTH, json={"key": key}).json()["companies"][0]
    assert row["latest_raise"]["amount_sold"] == 20_000_000
    assert row["latest_raise"]["filed"] == "2026-03-10"


def test_fundraising_routes_need_a_key(client):
    c, _ = client
    assert c.get("/v1/edgar/companies?q=northwind").status_code == 401
    assert c.get("/v1/entities/UEI777/formd").status_code == 401
