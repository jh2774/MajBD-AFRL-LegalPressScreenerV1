"""Investment funds named after a contractor.

The fund is tied to the contractor by its name and nothing else, so most of
what is tested here is restraint: whole words only, words a person chose,
history never sent as news, and a fund a person has ruled out staying out.
"""
from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from foci_screen import alerts, vehicles
from foci_screen import portfolio as pf
from foci_screen.connectors.formd import FormDFiling, RelatedPerson
from foci_screen.connectors.vehicles import (
    VehicleConnector,
    bare_name,
    default_phrase,
    facts_of,
    name_matches,
    parse_master_index,
    phrase_problem,
)
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}
TODAY = datetime.now(timezone.utc).date()
NAME = "NORTHWIND ROBOTICS INC"


def iso(day: date) -> str:
    return day.isoformat()


def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


def row(n: int, cik: str, name: str, filed: str, form: str = "D") -> dict:
    return {"accession": f"{int(cik):010d}-26-{n:06d}", "cik": cik, "name": name,
            "form": form, "filed": filed}


def facts(sold=12_000_000, investors=40, *, place="New York, NEW YORK", abroad=False,
          people=None) -> dict:
    filing = FormDFiling(
        accession="x", form="D", filed="2026-01-01", amount_sold=sold, investors=investors,
        place=place, outside_us=abroad,
        related_persons=people if people is not None else [
            RelatedPerson("Dana Reyes", ["Executive Officer"], "Managing Member",
                          "New York, NEW YORK")])
    return facts_of(filing)


# ------------------------------------------------------------- pure helpers

def test_a_phrase_matches_whole_words_in_order():
    assert name_matches("Shield AI", "CDT Shield.AI SPV LLC")
    assert name_matches("shield ai", "HII Shield AI-05, a Series of HII Shield AI-A LLC")
    assert not name_matches("Shield AI", "SHIELDS AIDAN H"), "a substring is not a name"
    assert not name_matches("Shield AI", "AI Shield Fund LP"), "the order matters"
    assert not name_matches("", "Anything LLC")


def test_the_suggested_words_are_what_people_call_the_company():
    assert default_phrase("ANDURIL INDUSTRIES, INC.") == "Anduril"
    assert default_phrase("SHIELD AI INC") == "Shield AI"
    assert default_phrase("The Boeing Company") == "Boeing"
    assert default_phrase(NAME) == "Northwind Robotics"
    # Nothing distinctive would be left, so the generic word stays...
    assert default_phrase("DEFENSE SYSTEMS LLC") == "Defense Systems"
    # ...and looking it up is refused rather than matching hundreds of filers.
    assert "too general" in phrase_problem("Defense Systems")


def test_a_company_written_into_the_name_boxes_backwards_is_put_right():
    """The form has a first-name and a last-name box and nowhere for a company."""
    people = [RelatedPerson("LLC Sydecar", ["Promoter"], "Administrator of the Issuer"),
              RelatedPerson("Dana Reyes", ["Director"]),
              RelatedPerson("The Carlyle Group", ["Promoter"]),
              RelatedPerson("- Greenbird Management LLC", ["Promoter"]),
              RelatedPerson("N/A N/A Harborlight GP", ["Executive Officer"]),
              RelatedPerson("-", ["Director"])]
    read = facts(people=people)["people"]
    assert [p["name"] for p in read] == [
        "Sydecar LLC", "Dana Reyes", "The Carlyle Group", "Greenbird Management LLC",
        "Harborlight GP", "-"], "and a name that is only a dash is left as filed"
    assert read[0]["role"] == "Administrator of the Issuer" and read[1]["role"] == "director"


def test_a_name_without_its_legal_ending():
    assert bare_name("Shield AI, Inc.") == bare_name("SHIELD AI INC") == ("shield", "ai")
    assert bare_name("The Boeing Company") == ("boeing",)
    assert bare_name("Shield AI Fund LLC") != bare_name("Shield AI"), "a fund is not it"


def test_words_too_short_or_too_general_are_refused():
    assert phrase_problem("AI").startswith("Use at least four letters")
    assert phrase_problem("  ") != ""
    assert "too general" in phrase_problem("Capital Partners")
    assert phrase_problem("Anduril") == ""
    assert phrase_problem("Shield AI") == ""


def test_the_daily_list_keeps_only_form_d_rows():
    text = "\n".join([
        "Description:           Daily Index of EDGAR Dissemination Feed by Company Name",
        "CIK|Company Name|Form Type|Date Filed|File Name",
        "--------------------------------------------------------------------------------",
        "2145910|HII Shield AI-05, a Series of HII Shield AI-A LLC|D|20260717|"
        "edgar/data/2145910/0002145910-26-000001.txt",
        "0002100001|Moringa x Anduril LLC|D/A|20260717|"
        "edgar/data/2100001/0002100001-26-000004.txt",
        "320193|Apple Inc.|10-K|20260717|edgar/data/320193/0000320193-26-000010.txt",
        "99|Broken|D|20260717",
        "77|No Accession Co|D|20260717|edgar/data/77/index.htm",
    ])
    assert parse_master_index(text) == [
        {"accession": "0002145910-26-000001", "cik": "2145910", "form": "D",
         "name": "HII Shield AI-05, a Series of HII Shield AI-A LLC", "filed": "2026-07-17"},
        {"accession": "0002100001-26-000004", "cik": "2100001", "form": "D/A",
         "name": "Moringa x Anduril LLC", "filed": "2026-07-17"},
    ]


# ----------------------------------------------------- the connector, on the wire

class Http:
    """EDGAR as the connector sees it: directory listings, day files, the lookup."""

    def __init__(self, listings=None, days=None, typed=None):
        self.listings, self.days, self.typed = listings or {}, days or {}, typed or {}
        self.urls: list[str] = []
        self.asked: list[str] = []

    def get(self, url, params=None, **kw):
        self.urls.append(url)
        if url.endswith("index.json"):
            quarter = url.split("/")[-2]
            items = [{"name": n} for n in self.listings.get(quarter, [])]
            return {"status": 200, "json": {"directory": {"item": items}}}
        if "master." in url:
            text = self.days.get(url.rsplit("master.", 1)[1].removesuffix(".idx"))
            return {"status": 200 if text else 404, "text": text or ""}
        typed = (params or {}).get("keysTyped", "")
        self.asked.append(typed)
        hits = self.typed.get(typed, [])
        return {"status": 200, "json": {"hits": {"hits": [
            {"_id": cik, "_source": {"entity": name}} for cik, name in hits]}}}


def test_which_days_exist_is_asked_of_edgar_not_guessed():
    http = Http(listings={
        "QTR3": ["master.20260929.idx", "master.20260930.idx", "form.20260930.idx"],
        "QTR4": ["master.20261001.idx", "master.20261002.idx", "master.20261005.idx",
                 "master.20261006.idx", "sitemap.20261006.xml"]})
    days = VehicleConnector(http).available_days(date(2026, 9, 30), date(2026, 10, 5))
    assert days == ["20260930", "20261001", "20261002", "20261005"], "no weekend, in range"
    assert [u.split("/")[-2] for u in http.urls] == ["QTR3", "QTR4"]
    assert VehicleConnector(http).available_days(date(2026, 10, 6), date(2026, 10, 5)) == []


def test_a_day_that_cannot_be_read_is_none_not_an_empty_day():
    line = ("2100001|Moringa x Anduril LLC|D|20261006|"
            "edgar/data/2100001/0002100001-26-000004.txt")
    vc = VehicleConnector(Http(days={"20261006": "CIK|Company Name|...\n" + line}))
    assert [r["name"] for r in vc.day_filings("20261006")] == ["Moringa x Anduril LLC"]
    assert vc.day_filings("20261007") is None


def test_a_full_answer_from_the_lookup_is_asked_again_letter_by_letter():
    ten = [(str(5000 + i), f"Anduril Fund {i} LLC") for i in range(9)] \
        + [("4999", "ANDURILLO PEDRO")]
    http = Http(typed={"Anduril": ten, "Anduril x": [("7000", "Moringa x Anduril LLC")],
                       "Anduril i": [("5003", "Anduril Fund 3 LLC")]})
    found, more = VehicleConnector(http).lookup("Anduril")
    assert more is True, "ten came back, so there may be names it had no room for"
    assert len(http.asked) == 37, "the phrase, then with each next letter and digit"
    names = [e["name"] for e in found]
    assert names[0] == "Moringa x Anduril LLC", "highest SEC number first: the newest"
    assert len(found) == 10 and "ANDURILLO PEDRO" not in names

    http = Http(typed={"Shield AI": [("8001", "CDT Shield.AI SPV LLC")]})
    found, more = VehicleConnector(http).lookup("Shield AI")
    assert (len(found), more, http.asked) == (1, False, ["Shield AI"])


# ----------------------------------------------------------------- the engine

class FakeEdgar:
    """Stands in for VehicleConnector: daily lists, a name lookup, and filings."""

    def __init__(self):
        self.days: dict[str, list[dict] | None] = {}     # None: cannot be read
        self.names: list[dict] = []
        self.full = False
        self.listings: dict[str, list[dict]] = {}
        self.facts: dict[str, dict] = {}
        self.day_calls: list[str] = []
        self.lookups: list[str] = []
        self.listed: list[str] = []
        self.reads: list[str] = []

    def available_days(self, start, end):
        return sorted(d for d in self.days if ymd(start) <= d <= ymd(end))

    def day_filings(self, day):
        self.day_calls.append(day)
        return self.days[day]

    def lookup(self, phrase):
        self.lookups.append(phrase)
        return [e for e in self.names if name_matches(phrase, e["name"])], self.full

    def fund(self, cik, name, *rows_, **facts_kw):
        """A fund the lookup knows: its filings, newest first, and what the latest says."""
        self.names.append({"cik": cik, "name": name})
        self.listings[cik] = list(rows_)
        self.facts[rows_[0]["accession"]] = facts(**facts_kw)

    def filings_of(self, cik):
        self.listed.append(cik)
        return self.listings.get(cik, [])

    def read(self, row_):
        self.reads.append(row_["accession"])
        return self.facts.get(row_["accession"], facts())


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "vehicles.db"), tenant_id="acme")
    yield s
    s.close()


def weekdays(first: date, last: date) -> list[str]:
    out, day = [], first
    while day <= last:
        if day.weekday() < 5:
            out.append(ymd(day))
        day += timedelta(days=1)
    return out


def test_the_first_sync_reads_recent_days_and_later_ones_carry_on_from_there(store):
    today = date(2026, 10, 7)
    edgar = FakeEdgar()
    for d in weekdays(date(2026, 7, 1), date(2026, 10, 9)):
        edgar.days[d] = [row(1, "2100001", f"Fund of {d} LLC", f"{d[:4]}-{d[4:6]}-{d[6:]}")]

    first = vehicles.sync_daily_index(store, edgar, today=today)
    assert first["days"] == vehicles.BACKFILL_DAYS and first["through"] == "20261006"
    assert edgar.day_calls[0] == "20260916" and "20261007" not in edgar.day_calls, \
        "three weeks back, and never the day still being written"

    again = vehicles.sync_daily_index(store, edgar, today=today)
    assert again["days"] == 0 and len(edgar.day_calls) == vehicles.BACKFILL_DAYS

    later = vehicles.sync_daily_index(store, edgar, today=date(2026, 10, 9))
    assert later["days"] == 2 and later["through"] == "20261008"


def test_a_day_that_cannot_be_read_stops_the_sync_so_it_is_not_skipped(store):
    edgar = FakeEdgar()
    edgar.days = {"20261001": [], "20261002": None, "20261005": []}
    first = vehicles.sync_daily_index(store, edgar, today=date(2026, 10, 6))
    assert first["through"] == "20261001" and store.edgar_days() == {"20261001"}

    edgar.days["20261002"] = [row(1, "2100001", "Late Fund LLC", "2026-10-02")]
    second = vehicles.sync_daily_index(store, edgar, today=date(2026, 10, 6))
    assert second == {"days": 2, "filings": 1, "through": "20261005"}


def northwind_funds(edgar):
    """Two funds on EDGAR: one amended this year, one run from London."""
    edgar.fund("9000002", "Northwind Co-Invest II LP",
               row(3, "9000002", "Northwind Co-Invest II LP", "2026-05-02", "D/A"),
               row(2, "9000002", "Northwind Co-Invest II LP", "2025-11-20"),
               sold=30_000_000, investors=55)
    edgar.fund("9000001", "MW LSVC Northwind, LLC",
               row(1, "9000001", "MW LSVC Northwind, LLC", "2024-02-01"),
               sold=4_500_000, investors=12, place="London, UNITED KINGDOM", abroad=True)
    return edgar


def look_up_northwind(store, edgar=None, key="UEI777", phrase="Northwind"):
    """Both halves of a look: the names, then everything they filed."""
    edgar = edgar or northwind_funds(FakeEdgar())
    vehicles.find(store, edgar, key, phrase)
    return edgar, vehicles.read_more(store, edgar, key, limit=25)


def test_looking_a_name_up_finds_its_funds_and_reads_what_each_last_said(store):
    edgar, page = look_up_northwind(store)
    assert [f["name"] for f in page["vehicles"]] == ["Northwind Co-Invest II LP",
                                                     "MW LSVC Northwind, LLC"]
    newest = page["vehicles"][0]
    assert (newest["filings"], newest["first_filed"]) == (2, "2025-11-20")
    assert newest["latest"]["form"] == "D/A" and newest["latest"]["facts"]["investors"] == 55
    assert newest["latest"]["url"].endswith(
        "/9000002/000900000226000003/xslFormDX01/primary_doc.xml")
    assert newest["url"] == "https://www.sec.gov/edgar/browse/?CIK=9000002"
    assert page["totals"] == {"funds": 2, "read": 2, "raised": 34_500_000,
                              "investors": 67, "abroad": 1}
    assert len(edgar.reads) == 2, "each fund's latest filing only, not its history"
    assert page["watch"]["phrase"] == "Northwind" and page["watch"]["watching"] is False
    assert page["pending_names"] == [] and page["watch"]["more_exist"] is False


def test_the_contractors_own_filings_are_not_called_a_fund(store):
    """Its own Form D is its own fundraising, shown in the card above."""
    store.set_entity_link("UEI777", status="confirmed", cik="0004400123", decided_by="test")
    edgar = FakeEdgar()
    edgar.fund("4400123", "Northwind Robotics, Inc.",
               row(9, "4400123", "Northwind Robotics, Inc.", "2026-03-10"))
    edgar.fund("9000001", "MW LSVC Northwind, LLC",
               row(1, "9000001", "MW LSVC Northwind, LLC", "2024-02-01"))
    _, page = look_up_northwind(store, edgar)
    assert [f["cik"] for f in page["vehicles"]] == ["9000001"]
    assert edgar.listed == ["9000001"]


def test_the_names_come_first_and_what_each_filed_is_read_a_few_at_a_time(store):
    """One long request left the page silent for a minute and a half."""
    edgar = FakeEdgar()
    edgar.full = True
    for n in (1, 2, 3):
        cik = f"900000{n}"
        edgar.fund(cik, f"Northwind Fund {n} LLC",
                   row(n, cik, f"Northwind Fund {n} LLC", f"2026-0{n}-15"))

    page = vehicles.find(store, edgar, "UEI777", "Northwind")
    assert page["vehicles"] == [] and (edgar.listed, edgar.reads) == ([], [])
    assert [p["name"] for p in page["pending_names"]] == [
        "Northwind Fund 3 LLC", "Northwind Fund 2 LLC", "Northwind Fund 1 LLC"]
    assert page["watch"]["looked_at"] and page["watch"]["more_exist"] is True

    page = vehicles.read_more(store, edgar, "UEI777", limit=1)
    assert [f["cik"] for f in page["vehicles"]] == ["9000003"], "newest registration first"
    assert (len(page["pending_names"]), page["unread"]) == (2, 0)

    page = vehicles.read_more(store, edgar, "UEI777", limit=2)
    assert edgar.lookups == ["Northwind"], "EDGAR is not asked for the names again"
    assert len(page["vehicles"]) == 3 and page["pending_names"] == []
    assert page["watch"]["more_exist"] is True, "still true: nobody asked EDGAR again"
    assert vehicles.STEP * 2 <= 12, "a step stays a few seconds of requests"


def test_a_filer_with_the_companys_own_name_is_set_aside_not_counted_as_a_fund(store):
    """No SEC record has been picked, so the contractor's number is unknown —
    but "Northwind Robotics, Inc." raising $240 million is not a fund named
    after Northwind, and adding it in would swamp what the funds pooled."""
    award(store, "UEI777", NAME, "P1")
    edgar = FakeEdgar()
    edgar.fund("4400123", "Northwind Robotics, Inc.",
               row(9, "4400123", "Northwind Robotics, Inc.", "2026-03-10"), sold=240_000_000)
    edgar.fund("4400999", "NORTHWIND LLC", row(8, "4400999", "NORTHWIND LLC", "2025-01-10"))
    edgar.fund("9000001", "MW LSVC Northwind, LLC",
               row(1, "9000001", "MW LSVC Northwind, LLC", "2024-02-01"), sold=4_500_000)
    _, page = look_up_northwind(store, edgar)

    assert [f["cik"] for f in page["vehicles"]] == ["9000001"]
    assert page["totals"]["raised"] == 4_500_000 and page["pending_names"] == []
    assert [s["name"] for s in page["same_name"]] == ["NORTHWIND LLC",
                                                     "Northwind Robotics, Inc."]
    assert edgar.listed == ["9000001"], "and nothing is spent fetching them"

    watch = store.save_vehicle_watch("UEI777", watching=True)
    store.add_formd_index([row(10, "4400123", "Northwind Robotics, Inc.", iso(TODAY))])
    assert vehicles.alert_items(store, watch) == [], "its own raise is not a fund's"


def test_a_filing_that_fails_to_read_does_not_lose_the_others(store):
    edgar = FakeEdgar()
    edgar.fund("9000001", "Northwind Fund 1 LLC",
               row(1, "9000001", "Northwind Fund 1 LLC", "2026-01-15"))
    edgar.fund("9000002", "Northwind Fund 2 LLC",
               row(2, "9000002", "Northwind Fund 2 LLC", "2026-02-15"))
    real = edgar.read

    def read(row_):
        if row_["cik"] == "9000002":
            raise TimeoutError("EDGAR was slow")
        return real(row_)

    edgar.read = read
    _, page = look_up_northwind(store, edgar)
    assert page["totals"]["funds"] == 2 and page["totals"]["read"] == 1
    assert page["unread"] == 1, "so the page knows to offer it again"
    unread = next(f for f in page["vehicles"] if f["cik"] == "9000002")
    assert unread["latest"]["facts"] is None


# ------------------------------------------------------------------- alerts

def since(store, key, day):
    """Pretend the watch was switched on earlier than the test's own clock."""
    with store._tx() as c:
        c.execute("UPDATE vehicle_watches SET watching_since=? WHERE entity_key=?",
                  (iso(day), key))
    return store.vehicle_watch(key)


def list_made_on(store, subscription_id, day):
    with store._tx() as c:
        c.execute("UPDATE alert_subscriptions SET created_at=? WHERE subscription_id=?",
                  (f"{iso(day)}T00:00:00Z", subscription_id))


def test_history_is_never_an_alert(store):
    """A look fills the index with years of filings. None was filed after the
    watch began, so none can reach anybody."""
    look_up_northwind(store)
    watch = store.save_vehicle_watch("UEI777", watching=True)
    assert watch["watching_since"] == iso(TODAY)
    assert vehicles.alert_items(store, watch) == []

    new = row(7, "9000007", "Northwind Opportunity Fund L.P.", iso(TODAY))
    store.add_formd_index([new], "daily")
    store.set_formd_facts(new["accession"], facts(sold=8_000_000, investors=1))
    [item] = vehicles.alert_items(store, watch, "https://site.example")
    assert item["item_id"] == "vehicle:UEI777:0009000007-26-000007"
    assert item["headline"] == ("An investment fund named after Northwind filed with the "
                                "SEC: Northwind Opportunity Fund L.P.")
    assert item["detail"].startswith("It reports raising $8.0 million from 1 investor (Form D")
    assert "Run by: Dana Reyes (Managing Member)." in item["detail"]
    assert "name only" in item["explainer"] and item["where"].startswith("Form D, Item 13")
    assert dict(item["links"])["The contractor in FOCI-Screener"] == \
        "https://site.example/#/entity/UEI777"


def test_an_update_and_a_fund_abroad_are_worded_and_ranked_apart(store):
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    first = row(1, "9000001", "Northwind Fund 1 LLC", iso(TODAY))
    update = row(2, "9000002", "Northwind Fund 2 LLC", iso(TODAY), "D/A")
    abroad = row(3, "9000003", "Northwind Fund 3 LLC", iso(TODAY))
    unread = row(4, "9000004", "Northwind Fund 4 LLC", iso(TODAY))
    store.add_formd_index([first, update, abroad, unread])
    store.set_formd_facts(first["accession"], facts(sold=0))
    store.set_formd_facts(update["accession"], facts())
    store.set_formd_facts(abroad["accession"], facts(people=[RelatedPerson(
        "Wei Chen", ["Director"], "", "Singapore, SINGAPORE", True)]))

    items = {i["item_id"][-1]: i for i in
             vehicles.alert_items(store, store.vehicle_watch("UEI777"))}
    assert "no money raised yet" in items["1"]["detail"] and items["1"]["importance"] == 5
    assert "updated its filing with the SEC: Northwind Fund 2 LLC." in items["2"]["headline"]
    assert items["2"]["importance"] == 4
    assert "Wei Chen (director, address in Singapore, SINGAPORE)" in items["3"]["detail"]
    assert items["3"]["importance"] == 6
    assert "could not be read here; the link opens it" in items["4"]["detail"]


def test_a_fund_marked_unrelated_is_hidden_and_never_alerted(store):
    look_up_northwind(store)
    new = row(7, "9000001", "MW LSVC Northwind, LLC", iso(TODAY), "D/A")
    store.add_formd_index([new])
    watch = store.save_vehicle_watch("UEI777", watching=True, unrelated=["9000001"])
    page = vehicles.view(store, "UEI777")
    assert [f["cik"] for f in page["vehicles"]] == ["9000002"]
    assert page["unrelated"] == [{"cik": "9000001", "name": "MW LSVC Northwind, LLC"}]
    assert page["totals"]["funds"] == 1 and page["totals"]["abroad"] == 0
    assert vehicles.alert_items(store, watch) == []

    watch = store.save_vehicle_watch("UEI777", unrelated=[])
    assert len(vehicles.alert_items(store, watch)) == 1, "and undoing it brings it back"


def test_changing_the_words_starts_the_clock_again(store):
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    old = since(store, "UEI777", TODAY - timedelta(days=30))
    assert old["watching_since"] == iso(TODAY - timedelta(days=30))
    assert store.save_vehicle_watch("UEI777", unrelated=["1"])["watching_since"] == \
        old["watching_since"], "an unrelated decision leaves it alone"
    assert store.save_vehicle_watch("UEI777", phrase="Northwind Robotics")[
        "watching_since"] == iso(TODAY), "different words match different funds"

    off = store.save_vehicle_watch("UEI777", watching=False)
    assert vehicles.alert_items(store, off) == [] and store.vehicle_watches() == []


class FakeCfg:
    alerts_send = None
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


def subscribe(store, companies):
    key = pf.encode(pf.from_entity_keys("Northwind watch", list(companies)))
    sid = store.save_alert_subscription(name="Northwind watch", emails=["ko@agency.gov"],
                                        companies=list(companies), portfolio_key=key)
    alerts.baseline(store, sid, list(companies))
    return sid


def award(store, entity_key, name, piid):
    with store._tx() as c:
        c.execute(
            "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, entity_key,"
            " entity_name, amount, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (store.tenant_id, piid, "r1", piid, entity_key, name, 1000.0,
             "2026-01-01T00:00:00Z"))


def a_week_of_edgar(new_rows):
    """Daily lists for the past week, with `new_rows` filed on the latest day."""
    edgar = FakeEdgar()
    last = vehicles.today_for_edgar() - timedelta(days=1)
    for back in range(7, 0, -1):
        edgar.days[ymd(last - timedelta(days=back))] = []
    edgar.days[ymd(last)] = [dict(r, filed=iso(last)) for r in new_rows]
    return edgar, last


def test_a_new_fund_is_emailed_once_with_where_to_look(store):
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    since(store, "UEI777", TODAY - timedelta(days=30))
    sid = subscribe(store, ["UEI777"])
    list_made_on(store, sid, TODAY - timedelta(days=20))

    new = row(7, "9000007", "Northwind Co-Invest III LLC", "")
    edgar, filed = a_week_of_edgar([new, row(8, "9100000", "Southwind Fund LLC", "")])
    edgar.facts[new["accession"]] = facts(sold=12_000_000, investors=40)

    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), "https://site.example",
                               vehicles=edgar)
    assert result["funds"]["watched"] == 1 and result["funds"]["read"] == 1
    assert edgar.reads == [new["accession"]], "only the filing being reported is read"
    [delivery] = store.alert_deliveries(sid)
    body = delivery["body_text"]
    assert ("An investment fund named after Northwind filed with the SEC: "
            "Northwind Co-Invest III LLC.") in body
    assert f"It reports raising $12.0 million from 40 investors (Form D filed {iso(filed)})" \
        in body
    assert "What this is: A fund like this collects money" in body
    assert "Where to look: Form D, Item 13 (amounts)" in body
    assert "/000900000726000007/xslFormDX01/primary_doc.xml" in body
    assert "https://site.example/#/entity/UEI777" in body
    assert "Southwind" not in body

    again = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), vehicles=edgar)
    assert again["emails"][0]["status"] == "nothing new"
    assert len(store.alert_deliveries(sid)) == 1


def test_a_list_does_not_get_what_was_filed_before_it_existed(store):
    """The watch is older than the list, and the index learns of the filing
    only after the list was made — it is still not this list's news."""
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    since(store, "UEI777", TODAY - timedelta(days=30))
    sid = subscribe(store, ["UEI777"])                       # made today
    edgar, _ = a_week_of_edgar([row(7, "9000007", "Northwind Co-Invest III LLC", "")])
    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), vehicles=edgar)
    assert result["emails"][0]["status"] == "nothing new"
    assert store.alert_deliveries(sid) == []


def test_a_contractor_added_to_an_older_list_does_not_bring_its_backlog(store):
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    since(store, "UEI777", TODAY - timedelta(days=30))
    recent = row(7, "9000007", "Northwind Co-Invest III LLC", iso(TODAY - timedelta(days=3)))
    store.add_formd_index([recent])

    sid = subscribe(store, ["UEI999"])
    list_made_on(store, sid, TODAY - timedelta(days=60))
    assert alerts.baseline(store, sid, ["UEI777"]) == 1      # what adding it to the list does
    with store._tx() as c:
        c.execute("UPDATE alert_subscriptions SET companies=? WHERE subscription_id=?",
                  ('["UEI999", "UEI777"]', sid))
    assert alerts.pending_items(store, store.alert_subscription(sid), "") == []

    store.add_formd_index([row(8, "9000008", "Northwind Co-Invest IV LLC", iso(TODAY))])
    [item] = alerts.pending_items(store, store.alert_subscription(sid), "")
    assert item["item_id"].endswith("-000008")


def test_a_portfolio_of_typed_names_follows_the_watch_on_the_screened_page(store):
    award(store, "UEI777", NAME, "P1")
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    since(store, "UEI777", TODAY - timedelta(days=30))
    sid = subscribe(store, [NAME])                           # the portfolio holds the name
    list_made_on(store, sid, TODAY - timedelta(days=20))
    edgar, _ = a_week_of_edgar([row(7, "9000007", "Northwind Co-Invest III LLC", "")])
    alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), vehicles=edgar)
    [delivery] = store.alert_deliveries(sid)
    assert "Northwind Co-Invest III LLC" in delivery["body_text"]


def test_edgar_failing_does_not_stop_the_rest_of_the_alert(store):
    store.save_vehicle_watch("UEI777", phrase="Northwind", watching=True)
    sid = subscribe(store, ["UEI777"])
    store.start_run("DoD", {}, run_id="r1")
    store.create_notice(run_id="r1", entity_key="UEI777", entity_name=NAME,
                        severity="high", recipient="x@mail.mil", officer_confidence="high",
                        subject="s", body_text="b", trigger_reason="Always notify.")

    class Down(FakeEdgar):
        def available_days(self, start, end):
            raise ConnectionError("EDGAR is not answering")

    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), vehicles=Down())
    assert result["funds"] == {"error": "ConnectionError: EDGAR is not answering"}
    [delivery] = store.alert_deliveries(sid)
    assert f"{NAME} was flagged (HIGH)" in delivery["body_text"]


def test_nothing_is_fetched_when_no_contractor_is_watched_for_funds(store):
    subscribe(store, ["UEI777"])
    edgar, _ = a_week_of_edgar([])
    result = alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), vehicles=edgar)
    assert result["funds"] == {} and edgar.day_calls == []


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
    edgar = FakeEdgar()
    monkeypatch.setattr(app_module, "_vehicles", lambda: edgar)
    return TestClient(app_module.app), app_module, edgar


URL = "/v1/entities/UEI777/vehicles"


def test_the_page_offers_words_and_shows_nothing_until_someone_looks(client):
    c, app_module, edgar = client
    award(app_module.store_for("acme"), "UEI777", "NORTHWIND INDUSTRIES, INC.", "P1")
    body = c.get(URL, headers=AUTH).json()
    assert body["suggested_phrase"] == "Northwind" and body["watch"] is None
    assert body["vehicles"] == [] and edgar.lookups == [], "reading the page asks EDGAR nothing"

    typed = c.get(f"/v1/entities/{NAME.replace(' ', '%20')}/vehicles", headers=AUTH).json()
    assert typed["suggested_phrase"] == "Northwind Robotics", "works with no screen at all"


def test_a_decision_needs_words_and_the_words_need_to_be_usable(client):
    c, _, edgar = client
    assert c.post(URL, headers=AUTH, json={"watching": True}).status_code == 409
    assert c.post(URL, headers=AUTH, json={"more": True}).status_code == 409
    r = c.post(URL, headers=AUTH, json={"look": True, "phrase": "AI"})
    assert r.status_code == 422 and "four letters" in r.json()["detail"]
    r = c.post(URL, headers=AUTH, json={"look": True, "phrase": "Capital Partners"})
    assert r.status_code == 422 and "too general" in r.json()["detail"]
    assert c.post(URL, headers=AUTH, json={"unrelated": "not-a-number"}).status_code == 422
    assert edgar.lookups == []


def test_look_then_watch_then_rule_a_fund_out(client):
    c, _, edgar = client
    northwind_funds(edgar)

    page = c.post(URL, headers=AUTH, json={"look": True, "phrase": " Northwind "}).json()
    assert edgar.lookups == ["Northwind"] and edgar.reads == []
    assert page["vehicles"] == [] and len(page["pending_names"]) == 2, "names first"
    assert page["watch"]["watching"] is False and page["watch"]["looked_at"]

    page = c.post(URL, headers=AUTH, json={"more": True}).json()
    assert [f["cik"] for f in page["vehicles"]] == ["9000002", "9000001"]
    assert (page["pending_names"], page["unread"]) == ([], 0), "nothing left to ask for"

    page = c.post(URL, headers=AUTH, json={"watching": True}).json()
    assert page["watch"]["watching"] and page["watch"]["watching_since"] == iso(TODAY)

    page = c.post(URL, headers=AUTH, json={"unrelated": "0009000001"}).json()
    assert [f["cik"] for f in page["vehicles"]] == ["9000002"]
    assert page["unrelated"] == [{"cik": "9000001", "name": "MW LSVC Northwind, LLC"}]
    assert page["watch"]["watching"], "ruling one out does not switch alerts off"

    page = c.post(URL, headers=AUTH, json={"related": "9000001"}).json()
    assert len(page["vehicles"]) == 2 and page["unrelated"] == []

    page = c.post(URL, headers=AUTH, json={"more": True}).json()
    assert edgar.lookups == ["Northwind"], "reading more does not ask for the names again"
    assert len(edgar.reads) == 2, "and what was read is not read twice"
    assert c.get(URL, headers=AUTH).json()["vehicles"] == page["vehicles"]


def test_edgar_not_answering_is_said_plainly(client):
    c, _, edgar = client

    def down(phrase):
        raise ConnectionError("no route")

    edgar.lookup = down
    r = c.post(URL, headers=AUTH, json={"look": True, "phrase": "Northwind"})
    assert r.status_code == 502 and "EDGAR did not answer" in r.json()["detail"]


def test_one_tenants_watch_is_not_anothers(client):
    c, app_module, edgar = client
    look_up_northwind(app_module.store_for("acme"), edgar)
    other = Store(app_module.cfg.db_path, tenant_id="globex")
    try:
        assert other.vehicle_watch("UEI777") is None
        assert vehicles.view(other, "UEI777")["vehicles"] == []
    finally:
        other.close()


def test_fund_routes_need_a_key(client):
    c, _, _ = client
    assert c.get(URL).status_code == 401
    assert c.post(URL, json={"look": True, "phrase": "Northwind"}).status_code == 401
