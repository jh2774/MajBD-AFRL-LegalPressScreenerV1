"""Fewer false positives, wider coverage, sturdier sources.

Every false positive named here was raised on the live site, screening the 495
contractors AFWERX awards to (November 2026): "foreign funding" on 494 of them,
five sanctions matches on generic words (HERMEUS CORP as the sanctioned
"HERMES"), "Russia named" copied from those matches, and two companies taken
for sanctioned ships. Nothing here talks to a government site.
"""
from __future__ import annotations

import csv
import io
import time
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from foci_screen import scopes
from foci_screen.connectors import screening_lists as sl
from foci_screen.connectors.fpds import _foreign_funding
from foci_screen.connectors.sam_exclusions import SAMExclusionsConnector
from foci_screen.httpclient import HttpClient
from foci_screen.models import Contract, Document, Entity
from foci_screen.risk import engine

# ------------------------------------------------------------ foreign funding


@pytest.mark.parametrize("value,expected", [
    ("X", ""), ("", ""), ("NOT APPLICABLE", ""), ("A", "Foreign Funds FMS"),
    ("B", "Foreign Funds non-FMS"), ("FOREIGN FUNDS NON-FMS", "FOREIGN FUNDS NON-FMS"),
])
def test_foreign_funding_is_read_as_fpds_codes_it(value, expected):
    assert engine.foreign_funds(value) == expected


def test_an_award_marked_not_applicable_is_not_flagged_for_foreign_funds():
    """What 494 of 495 AFWERX contractors were flagged for."""
    entity = Entity(name="ACME LLC", uei="UEI1")
    plain = Contract(award_id="A1", piid="A1", foreign_funding="X")
    funded = Contract(award_id="A2", piid="A2", foreign_funding="A")
    ctx = engine.RuleContext(entity=entity, contracts=[plain])
    assert not [s for s in engine.rule_contract_structural(plain, ctx)
                if s.rule_id == "STRUCT-FOREIGN-FUNDING-01"]
    [signal] = [s for s in engine.rule_contract_structural(funded, ctx)
                if s.rule_id == "STRUCT-FOREIGN-FUNDING-01"]
    assert "Foreign Funds FMS" in signal.evidence


def test_fpds_foreign_funding_keeps_only_foreign_money():
    def entry(code, description):
        return ET.fromstring(f'<entry><foreignFunding description="{description}">{code}'
                             f'</foreignFunding></entry>')
    assert _foreign_funding(entry("X", "NOT APPLICABLE")) == ""
    assert _foreign_funding(entry("B", "FOREIGN FUNDS NON-FMS")) == "Foreign Funds Non-Fms"


# ------------------------------------------------------------- name matching

def match(a, b):
    return sl.compare(sl.key_of(a), sl.key_of(b))


@pytest.mark.parametrize("contractor,listed", [
    ("HERMEUS CORP", "HERMES"),
    ("ADA TECHNOLOGIES, INC.", "0DAY TECHNOLOGIES"),
    ("RUNE TECHNOLOGIES INC.", "NEW TECHNOLOGIES"),
    ("ETC INTERNATIONAL LLC", "ETCO INTERNATIONAL COMPANY, LIMITED"),
    ("ETC INTERNATIONAL LLC", "CK INTERNATIONAL LTD"),
    ("ETC INTERNATIONAL LLC", "ETC Electronics"),
    ("DYNAMIC TECHNOLOGIES AND SERVICES, LLC", "Dynamic Engineering Corporation"),
    ("MIMIR SECURITY INC.", "DIGITAL SECURITY"),
    ("AUTONOMUS MEDICAL TECHNOLOGIES, INC.", "AUTONOMOUS ENERGY SYSTEMS"),
    ("PHOENIX TAILINGS INC", "PHOENIX"),
    ("ADVANCED AIRCRAFT COMPANY, LLC", "United Aircraft Corporation"),
    ("GLOBAL SYSTEMS INC", "GLOBAL SYSTEMS INTERNATIONAL"),
])
def test_generic_words_do_not_make_two_companies_one(contractor, listed):
    """Each pair was, or would have been, a sanctions flag on a real AFWERX
    contractor under whole-string similarity."""
    assert match(contractor, listed) == ""


@pytest.mark.parametrize("contractor,listed,kind", [
    ("ROSOBORON EXPORT JSC", "ROSOBORONEXPORT JSC", "exact"),
    ("Kaspersky Lab", "Joint Stock Company Kaspersky Lab", "exact"),
    ("Huawei Technologies USA Inc", "Huawei Technologies Co., Ltd.", "core"),
    ("Hikvision USA Inc", "Hikvision", "core"),
    ("ROSOBORONEKXPORT", "ROSOBORONEKSPORT OAO", "variant"),
    ("YANGTZE MEMORY TECHNOLOGIES", "Yangtze Memory Technologies (Japan) Inc.", "variant"),
])
def test_listed_companies_are_still_found(contractor, listed, kind):
    assert match(contractor, listed) == kind


CSL_COLUMNS = ["_id", "source", "entity_number", "type", "programs", "name", "title",
               "addresses", "federal_register_notice", "start_date", "end_date",
               "standard_order", "license_requirement", "license_policy", "call_sign",
               "vessel_type", "gross_tonnage", "gross_registered_tonnage", "vessel_flag",
               "vessel_owner", "remarks", "source_list_url", "alt_names", "citizenships",
               "dates_of_birth", "nationalities", "places_of_birth",
               "source_information_url", "ids"]


def csl(*rows) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, CSL_COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in CSL_COLUMNS})
    return "﻿" + out.getvalue()


ENTITY_LIST = "Entity List (EL) - Bureau of Industry and Security"
SDN = "Specially Designated Nationals (SDN) - Treasury Department"
ROWS = [
    {"_id": "e1", "source": ENTITY_LIST, "name": "Hikvision",
     "alt_names": "Hangzhou Hikvision Digital Technology Co., Ltd.",
     "addresses": "555 Qianmo Road, Hangzhou, CN", "start_date": "2019-10-09",
     "federal_register_notice": "84 FR 54002",
     "source_information_url": "https://www.bis.gov/entity-list"},
    {"_id": "s1", "source": SDN, "type": "Entity", "name": "LLC AXIOM",
     "programs": "RUSSIA-EO14024", "addresses": "Ul. Entuziastov 1, Moscow, RU"},
    {"_id": "s2", "source": SDN, "type": "Individual", "name": "SMITH, John"},
    {"_id": "s3", "source": SDN, "type": "Vessel", "name": "VICTORY"},
    {"_id": "s4", "source": SDN, "type": "Entity", "name": "ACME DYNAMICS LLC",
     "programs": "IRAN", "addresses": "Tehran, IR"},
]


def lists_with(rows=ROWS) -> sl.ScreeningLists:
    lists = sl.ScreeningLists(SimpleNamespace(get=lambda url, **kw: {
        "status": 200, "text": csl(*rows)} if url == sl.CSL_URL else {"status": 404}))
    return lists


def test_people_ships_and_aircraft_are_not_matched_against_companies():
    lists = lists_with()
    assert [e.name for e in lists.index().entries] == ["Hikvision", "LLC AXIOM",
                                                       "ACME DYNAMICS LLC"]
    assert lists.screen("VICTORY SYSTEMS LLC") == []


def test_a_match_says_which_list_where_the_company_is_and_by_which_name():
    [doc] = lists_with().screen("Hangzhou Hikvision Digital Technology Co Ltd")
    assert doc.meta["list"] == "Commerce Entity List" and doc.meta["match"] == "exact"
    assert doc.meta["matched_name"] == "Hangzhou Hikvision Digital Technology Co., Ltd."
    assert doc.meta["listed_countries"] == "China" and doc.meta["listed_abroad"] is True
    assert "84 FR 54002" in doc.text and "another name for Hikvision" in doc.text


def ctx(name="X", countries=()):
    return engine.RuleContext(entity=Entity(name=name, countries=list(countries)),
                              contracts=[])


def test_matches_are_graded_by_how_good_they_are_and_where_the_company_is():
    lists = lists_with()
    [exact] = lists.screen("ACME DYNAMICS LLC")
    [s] = engine.rule_sanctions_hit(exact, ctx())
    assert (s.rule_id, s.severity) == ("SANCTION-01", "critical")
    assert "Treasury SDN list" in s.title and "name match only" in s.rationale

    [arm] = lists.screen("Hikvision USA Inc")
    [s] = engine.rule_sanctions_hit(arm, ctx())
    assert (s.rule_id, s.severity) == ("SANCTION-02", "high"), "its U.S. arm, by name"

    [weak] = lists.screen("AXIOM TECHNOLOGIES LLC")
    [s] = engine.rule_sanctions_hit(weak, ctx())
    assert (s.rule_id, s.severity) == ("SANCTION-04", "low")
    assert "Russia" in s.rationale and "recorded rather than flagged" in s.rationale
    [s] = engine.rule_sanctions_hit(weak, ctx(countries=["RUS"]))
    assert s.severity == "high", "a contractor itself abroad is not let off"


def test_a_list_entry_does_not_also_count_as_the_country_being_named():
    """HERMEUS CORP: the SDN entry's programme, RUSSIA-EO14024, became a
    second flag, "Russia named in ofac document"."""
    doc = Document(source="screening_lists", key="list:s1", text=(
        "Treasury SDN list: HERMES (programs: RUSSIA-EO14024)\nListed addresses: Moscow, "
        "Russia. Shareholder of Rostec; acquisition of a stake in Russia."))
    assert engine.rule_foci_jurisdiction(doc, ctx()) == []


def test_without_trade_gov_treasury_stands_in_and_the_run_says_so():
    sdn = '1,"ACME DYNAMICS LLC",-0- ,"IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-\n'
    alt = '1,10,"aka","ACME DYNAMICS TRADING",-0-\n'
    files = {sl.OFAC_FILES["sdn"]: sdn, sl.OFAC_FILES["alt"]: alt}
    lists = sl.ScreeningLists(SimpleNamespace(get=lambda url, **kw: (
        {"status": 200, "text": files[url]} if url in files else {"status": 429})))
    assert [d.meta["matched_name"] for d in lists.screen("Acme Dynamics Trading Inc")] \
        == ["ACME DYNAMICS TRADING"]
    assert lists.complete is False and "Entity List" in lists.coverage


def test_nothing_at_all_is_said_too():
    lists = sl.ScreeningLists(SimpleNamespace(get=lambda url, **kw: {"status": 503}))
    assert lists.screen("ACME DYNAMICS LLC") == [] and not lists.available
    assert "no contractor was checked" in lists.coverage


# --------------------------------------------------------------- exclusions

def test_a_sam_exclusion_name_match_is_the_same_name_and_never_a_ship(tmp_path):
    from test_sam_exclusions import StubHttp, extract, record
    rows = [record("Vessel", name="VICTORY", sam="V1"),
            record(name="Crius Limited", sam="C1"),
            record(name="Name Only Industries", sam="N1")]
    conn = SAMExclusionsConnector(StubHttp(body=extract(rows)), str(tmp_path))
    assert conn.screen("VICTORY SYSTEMS LLC") == []
    assert conn.screen("CRIUS TECHNOLOGY GROUP, INC.") == []
    assert [d.key for d in conn.screen("NAME ONLY INDUSTRIES INC")] == ["samexcl:N1"]


# ------------------------------------------------------------------ the HTTP client

class Cfg:
    user_agent = "test (test@example.org)"
    rate_limit_qps = 0
    cache_ttl_seconds = 3600
    cache_max_mb = 8
    timeout = 5
    max_retries = 3

    def __init__(self, cache_dir):
        self.cache_dir = str(cache_dir)


class Resp:
    def __init__(self, status, text="ok", headers=None):
        self.status_code, self.text, self.url = status, text, "u"
        self.headers = {"content-type": "text/plain", **(headers or {})}

    def json(self):
        raise ValueError


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from foci_screen import httpclient
    httpclient._breaker.clear()
    slept: list[float] = []
    monkeypatch.setattr(httpclient.time, "sleep", slept.append)
    http = HttpClient(Cfg(tmp_path))
    answers: list = []
    asked: list[str] = []

    def request(method, url, **kw):
        asked.append(url)
        answer = answers.pop(0) if answers else Resp(200)
        if isinstance(answer, Exception):
            raise answer
        return answer
    http.session.request = request
    yield SimpleNamespace(http=http, answers=answers, slept=slept, asked=asked)
    httpclient._breaker.clear()


def test_a_source_asking_to_wait_is_waited_for_and_the_last_try_is_not(client):
    client.answers += [Resp(429, headers={"Retry-After": "7"}), Resp(200, "fine")]
    assert client.http.get("https://data.sec.gov/a")["text"] == "fine"
    assert client.slept == [7.0]
    client.answers += [Resp(503), Resp(503), Resp(503)]
    out = client.http.get("https://data.sec.gov/b", use_cache=False)
    assert out["status"] == 503 and len(client.slept) == 3, "two waits, not three"


def test_a_host_that_keeps_failing_is_rested_and_the_run_is_told(client):
    import requests
    client.answers += [requests.ConnectionError("down")] * 4
    client.http.get("https://www.fpds.gov/a", use_cache=False)
    client.http.get("https://www.fpds.gov/b", use_cache=False)
    before = len(client.asked)
    out = client.http.get("https://www.fpds.gov/c", use_cache=False)
    assert len(client.asked) == before, "not asked again while it rests"
    assert out["status"] == 0 and "failed repeatedly" in out["error"]
    assert client.http.failures["www.fpds.gov"]["skipped"] == 1
    assert client.http.get("https://www.sec.gov/x", use_cache=False)["status"] == 200


def test_an_old_copy_stands_in_when_the_source_is_down(client, tmp_path):
    import os
    client.http.get("https://www.treasury.gov/list.csv")          # cached
    for f in tmp_path.glob("*.json"):
        old = time.time() - 2 * 86400
        os.utime(f, (old, old))
    client.answers += [Resp(503)] * 3
    out = client.http.get("https://www.treasury.gov/list.csv")
    assert out["text"] == "ok" and out["stale_days"] == 2.0
    assert client.http.failures["www.treasury.gov"]["stale"] == 1


def test_nothing_is_retried_past_the_runs_deadline(client):
    client.http.deadline = time.monotonic() - 1
    out = client.http.get("https://api.usaspending.gov/x", use_cache=False)
    assert out["status"] == 0 and "out of time" in out["error"] and client.asked == []


# ------------------------------------------------------------------ the screen

def bare_screener(**sources):
    from foci_screen.pipeline import Screener
    screener = object.__new__(Screener)
    screener.edgar = SimpleNamespace()
    screener.iapd = SimpleNamespace()
    screener.exclusions = SimpleNamespace(screen=lambda *a, **k: [],
                                          coverage_note=lambda: "")
    screener.lists = SimpleNamespace(screen=lambda name, whose="": [], available=True,
                                     complete=True)
    screener.uspto = SimpleNamespace(available=False)
    screener.patent_dataset = SimpleNamespace(available=False)
    screener.sam = SimpleNamespace(available=False)
    screener.alerts = SimpleNamespace(available=lambda: False)
    for k, v in sources.items():
        setattr(screener, k, v)
    return screener


def test_a_source_that_fails_is_recorded_and_the_rest_still_run():
    from foci_screen.pipeline import ScreenOptions

    def broken(*a, **k):
        raise KeyError("entityData")
    asked = []
    screener = bare_screener(
        exclusions=SimpleNamespace(screen=broken, coverage_note=lambda: ""),
        lists=SimpleNamespace(screen=lambda name, whose="": asked.append(name) or [],
                              available=True, complete=True))
    coverage: dict = {}
    screener._gather(Entity(name="ACME LLC", uei="U1"), ScreenOptions(agency="x",
                     skip_web=True), lambda m: None, coverage)
    assert coverage["SAM.gov exclusions"] == "not checked: it failed (KeyError)"
    assert asked == ["ACME LLC"], "the lists were still checked"
    assert coverage["Sanctions and export lists"] == "checked"
    assert coverage["SEC filings"].startswith("not checked: no SEC record")
    assert coverage["USPTO patent liens"].startswith("not checked")


def test_the_parent_and_the_5_percent_holders_are_checked_against_the_lists():
    from foci_screen.pipeline import ScreenOptions
    asked = []
    holder = Document(source="sec_edgar", key="o1", text="13G", doc_type="SC 13G",
                      meta={"holder": "NORTHWIND CAPITAL LP", "person_type": "PN"})
    person = Document(source="sec_edgar", key="o2", text="13G", doc_type="SC 13G",
                      meta={"holder": "Jane Roe", "person_type": "IN"})
    screener = bare_screener(
        edgar=SimpleNamespace(recent_filings=lambda cik, limit: [],
                              full_text_search=lambda *a, **k: [],
                              ownership_filings=lambda cik: [holder, person]),
        iapd=SimpleNamespace(find_adviser=lambda *a, **k: []),
        lists=SimpleNamespace(screen=lambda name, whose="contractor":
                              asked.append((name, whose)) or [],
                              available=True, complete=True))
    entity = Entity(name="ACME LLC", cik="1", parent_name="ACME HOLDINGS INC")
    screener._gather(entity, ScreenOptions(agency="x", skip_web=True,
                     fetch_filing_bodies=False), lambda m: None)
    assert asked == [("ACME LLC", "contractor"), ("ACME HOLDINGS INC", "parent"),
                     ("NORTHWIND CAPITAL LP", "holder")]


def test_contractors_screened_before_the_rules_changed_are_due_again():
    from datetime import datetime, timezone
    early = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert scopes.rescreen_before(30, early) == scopes.RULES_CHANGED
    later = datetime(2027, 3, 1, tzinfo=timezone.utc)
    assert scopes.rescreen_before(30, later) == "2027-01-30T00:00:00Z"


def test_a_scope_counts_what_is_due(tmp_path):
    from test_scopes import AFWERX, award

    from foci_screen.store import Store
    store = Store(str(tmp_path / "due.db"), tenant_id="t")
    store.start_run("x", {}, run_id="r1")
    for i in range(3):
        store.save_contract(award(f"A{i}", f"CO{i}", f"UEI{i}"), "r1", f"UEI{i}")
    store.mark_entity_screened("UEI0")
    assert store.scope_due(AFWERX, "2000-01-01T00:00:00Z") == 2
    assert store.scope_due(AFWERX, "2999-01-01T00:00:00Z") == 3
    store.close()
