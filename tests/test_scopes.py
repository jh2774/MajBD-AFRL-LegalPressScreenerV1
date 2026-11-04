"""Every contractor at an office or agency, watched with one search.

AFWERX asked to have alerts on all the companies it contracts with set up at
once. AFWERX is a contracting office inside AFRL, below anything USAspending
filters on, so its awards are read from FPDS by office. Nothing here talks to
FPDS, USAspending or a mail service.
"""
from __future__ import annotations

import importlib
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from test_mailer import Post

from foci_screen import alerts as alerts_engine
from foci_screen import portfolio as pf
from foci_screen import scopes
from foci_screen.connectors.fpds import FPDSConnector, contract_from_entry
from foci_screen.models import Contract, ContractingOfficer, Entity, Finding
from foci_screen.store import Store

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"
ME = "jordan.smith@afwerx.example"
AFWERX = scopes.PRESETS["afwerx"]
ENV = ("DATABASE_URL", "REDIS_URL", "GMAIL_ENABLED", "FOCI_EMAIL_REDIRECT_TO", "BREVO_API_KEY",
       "MAIL_PROVIDER", "SMTP_HOST", "ALERTS_SEND", "ALERTS_REPLY_TO", "NOTICES_SEND",
       "FOCI_PUBLIC_URL", "VERCEL_PROJECT_PRODUCTION_URL", "FOCI_SIGNUP", "CRON_SECRET",
       "FOCI_SCOPE_RUNS_PER_DAY")


def award(piid, name, uei, office="FA8649", amount=100_000.0, **kw):
    return Contract(award_id=piid, piid=piid, recipient_name=name, recipient_uei=uei,
                    awarding_agency="Department of Defense",
                    awarding_sub_agency="Department of the Air Force", award_amount=amount,
                    contracting_office_id=office,
                    contracting_office_name=f"{office} USAF SBIR STTR CNTRCTNG AFRL",
                    officer=ContractingOfficer(name="Jane Doe", email="jane.doe@us.af.mil",
                                               confidence="high"), **kw)


# ------------------------------------------------------------------ scopes

@pytest.mark.parametrize("typed,expected", [
    ("AFWERX", "offices:FA2280+FA8649"), ("afwerx", "offices:FA2280+FA8649"),
    ("AFRL", "office-name:AFRL"), ("Air Force Research Laboratory", "office-name:AFRL"),
    ("fa8650", "offices:FA8650"), ("offices:FA8649+FA2280", "offices:FA2280+FA8649"),
    ("agency:Department of Defense|Department of the Air Force",
     "agency:Department of Defense|Department of the Air Force"),
])
def test_a_scope_is_named_any_of_the_ways_people_say_it(typed, expected):
    assert scopes.parse(typed).id == expected


def test_presets_keep_their_names_and_nonsense_is_refused():
    assert scopes.parse("offices:FA8649+FA2280").title() == "AFWERX"
    with pytest.raises(scopes.ScopeError):
        scopes.parse("offices:not-a-code")
    with pytest.raises(scopes.ScopeError):
        scopes.parse("something else")
    assert [s.title() for s in scopes.suggest("afw")] == ["AFWERX"]
    assert [s.id for s in scopes.suggest("FA9453")] == ["offices:FA9453"]


def test_a_scope_matches_its_awards_in_the_database(tmp_path):
    store = Store(str(tmp_path / "s.db"), tenant_id="t")
    store.start_run("x", {}, run_id="r1")
    store.save_contract(award("A1", "ACME", "UEI1"), "r1", "UEI1")
    store.save_contract(award("A2", "BETA", "UEI2", office="FA2280"), "r1", "UEI2")
    store.save_contract(award("A3", "GAMMA", "UEI3", office="FA8650"), "r1", "UEI3")
    assert store.scope_entity_keys(AFWERX) == ["UEI1", "UEI2"]
    assert store.scope_entity_keys(scopes.parse("AFRL")) == ["UEI1", "UEI2", "UEI3"]
    # A later save from USAspending, which knows no office, does not lose it.
    store.save_contract(award("A1", "ACME", "UEI1", office=""), "r1", "UEI1")
    assert "UEI1" in store.scope_entity_keys(AFWERX)
    store.close()


# ------------------------------------------------------------------- FPDS

ENTRY = """<entry xmlns="http://www.w3.org/2005/Atom"><content><ns1:award
 xmlns:ns1="https://www.fpds.gov/FPDS"><ns1:awardID><ns1:awardContractID>
 <ns1:PIID>FA864924P0001</ns1:PIID><ns1:modNumber>{mod}</ns1:modNumber>
 </ns1:awardContractID></ns1:awardID>
 <ns1:relevantContractDates><ns1:signedDate>{signed}</ns1:signedDate>
 <ns1:currentCompletionDate>2026-06-30 00:00:00</ns1:currentCompletionDate>
 </ns1:relevantContractDates>
 <ns1:dollarValues><ns1:obligatedAmount>{amount}</ns1:obligatedAmount></ns1:dollarValues>
 <ns1:totalDollarValues><ns1:totalObligatedAmount>{total}</ns1:totalObligatedAmount>
 </ns1:totalDollarValues>
 <ns1:purchaserInformation>
 <ns1:contractingOfficeAgencyID name="DEPT OF THE AIR FORCE">5700
 </ns1:contractingOfficeAgencyID>
 <ns1:contractingOfficeID name="FA8649  USAF SBIR STTR CNTRCTNG AFRL">FA8649
 </ns1:contractingOfficeID>
 </ns1:purchaserInformation>
 <ns1:contractData><ns1:descriptionOfContractRequirement>SBIR PHASE I
 </ns1:descriptionOfContractRequirement></ns1:contractData>
 <ns1:vendor><ns1:vendorHeader><ns1:vendorName>ORBITAL WIDGETS INC</ns1:vendorName>
 </ns1:vendorHeader>
 <ns1:vendorSiteDetails><ns1:entityIdentifiers><ns1:vendorUEIInformation>
 <ns1:UEI>ABCDEF123456</ns1:UEI></ns1:vendorUEIInformation></ns1:entityIdentifiers>
 </ns1:vendorSiteDetails></ns1:vendor>
 <ns1:transactionInformation><ns1:approvedBy>JANE.DOE@US.AF.MIL</ns1:approvedBy>
 </ns1:transactionInformation></ns1:award></content></entry>"""


def feed(*entries, more=False):
    nxt = '<link rel="next" href="https://www.fpds.gov/next"/>' if more else ""
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{nxt}{"".join(entries)}</feed>'


def test_an_fpds_action_becomes_an_award_with_its_office():
    entry = ET.fromstring(ENTRY.format(mod="0", signed="2026-03-02 00:00:00",
                                       amount="150000", total="150000"))
    c = contract_from_entry(entry)
    assert (c.piid, c.recipient_name, c.recipient_uei) == (
        "FA864924P0001", "ORBITAL WIDGETS INC", "ABCDEF123456")
    assert (c.contracting_office_id, c.contracting_office_name) == (
        "FA8649", "FA8649  USAF SBIR STTR CNTRCTNG AFRL")
    assert (c.awarding_agency, c.awarding_sub_agency) == (
        "Department of Defense", "Department of the Air Force")
    assert c.award_amount == 150000 and c.start_date == "2026-03-02"
    assert c.officer.email == "jane.doe@us.af.mil" and c.description == "SBIR PHASE I"


class FeedHttp:
    def __init__(self, text):
        self.text, self.calls = text, []

    def get(self, url, params=None, **kw):
        self.calls.append(params)
        return {"status": 200, "text": self.text}


def test_a_page_folds_modifications_into_their_award():
    text = feed(ENTRY.format(mod="0", signed="2026-03-02 00:00:00", amount="150000",
                             total="150000"),
                ENTRY.format(mod="P00001", signed="2026-05-01 00:00:00", amount="50000",
                             total="200000"), more=True)
    http = FeedHttp(text)
    contracts, more, answered = FPDSConnector(http).search_page(
        'CONTRACTING_OFFICE_ID:"FA8649"')
    assert answered and more
    assert [(c.piid, c.award_amount, c.start_date) for c in contracts] == [
        ("FA864924P0001", 200000.0, "2026-05-01")], "the newest action's total"
    assert http.calls[0]["start"] == "0"


# --------------------------------------------------------- a run over AFWERX

class StatsHttp:
    stats: dict = {}

    def get(self, url, **kw):
        return {"status": 404, "text": ""}


def scope_screener(tmp_path, pages):
    """A screener whose FPDS answers from `pages` {(query, start): [awards]} and
    whose per-contractor screen only records who it was asked about."""
    from foci_screen.pipeline import Screener

    class Cfg:
        uspto_api_key = ""
        sam_api_key = ""

    store = Store(str(tmp_path / "run.db"), tenant_id="t")
    screener = Screener(Cfg(), StatsHttp(), store)
    asked: list[str] = []

    def search_page(query, start=0):
        office = query.split('"')[1]
        got = pages.get((office, start), [])
        return got, bool(pages.get((office, start + 10))), True

    screener.fpds.search_page = search_page
    screener._screen_entity = lambda entity, *a, **kw: asked.append(entity.key()) or None
    return screener, store, asked


def test_a_run_reads_every_award_and_screens_contractors_in_turn(tmp_path):
    from foci_screen.pipeline import ScreenOptions
    pages = {("FA8649", 0): [award("A1", "ACME", "UEI1", amount=900.0),
                             award("A2", "BETA", "UEI2", amount=500.0)],
             ("FA8649", 10): [award("A3", "GAMMA", "UEI3", amount=100.0)],
             ("FA2280", 0): [award("A4", "DELTA", "UEI4", office="FA2280", amount=700.0)]}
    screener, store, asked = scope_screener(tmp_path, pages)
    screener.SCOPE_SCREEN_CAP = 2           # time allowing, two a run
    opts = ScreenOptions(scope=AFWERX.id, months_back=1, max_entities=2, time_budget=600)

    first = screener.run(opts)
    assert store.scope_entity_keys(AFWERX) == ["UEI1", "UEI2", "UEI3", "UEI4"]
    assert asked == ["UEI1", "UEI4"], "never screened first, largest first"
    assert first.stats["backlog"] is True and first.stats["unscreened"] == 2

    second = screener.run(opts)
    assert asked == ["UEI1", "UEI4", "UEI2", "UEI3"], "the next run takes the rest"
    assert second.stats["backlog"] is False
    rows = {(r["month"], r["query"]): r for r in store.scope_index_rows(AFWERX.id)}
    assert all(r["done"] for r in rows.values())
    store.close()


def test_a_run_screens_as_many_as_its_time_allows(tmp_path):
    """A watch made when runs screened ten keeps max_entities=10; its runs
    screen as many as fit all the same."""
    from foci_screen.pipeline import ScreenOptions
    pages = {("FA8649", 0): [award(f"A{i}", f"CO{i}", f"UEI{i}") for i in range(10)],
             ("FA8649", 10): [award(f"B{i}", f"CP{i}", f"VEI{i}") for i in range(10)]}
    screener, store, asked = scope_screener(tmp_path, pages)
    result = screener.run(ScreenOptions(scope=AFWERX.id, months_back=1, max_entities=10,
                                        time_budget=600))
    assert len(asked) == 20 and result.stats["backlog"] is False
    store.close()


def test_a_run_stops_screening_before_its_time_is_up(tmp_path, monkeypatch):
    from foci_screen import pipeline
    from foci_screen.pipeline import ScreenOptions
    pages = {("FA8649", 0): [award(f"A{i}", f"CO{i}", f"UEI{i}") for i in range(10)]}
    screener, store, asked = scope_screener(tmp_path, pages)
    now = [0]                               # each contractor takes 20 seconds
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    screen = screener._screen_entity
    screener._screen_entity = lambda *a, **kw: now.__setitem__(0, now[0] + 20) or screen(*a)
    result = screener.run(ScreenOptions(scope=AFWERX.id, months_back=1, max_entities=10,
                                        time_budget=120))
    # 120 seconds less the 45 kept in hand: those started by 75 seconds in.
    assert len(asked) == 4, "the rest wait for the next run"
    assert result.stats["backlog"] is True
    store.close()


def test_a_run_stops_reading_when_its_share_of_time_is_spent(tmp_path, monkeypatch):
    from foci_screen import pipeline
    from foci_screen.pipeline import ScreenOptions
    pages = {("FA8649", s): [award(f"A{s}", f"CO{s}", f"UEI{s}")] for s in range(0, 50, 10)}
    screener, store, _asked = scope_screener(tmp_path, pages)
    clock = iter(range(0, 10_000, 30))
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))
    result = screener.run(ScreenOptions(scope="offices:FA8649", months_back=1,
                                        max_entities=0, time_budget=200))
    month = scopes.months_back(pipeline.date.today(), 1)[0]
    [row] = [r for r in store.scope_index_rows("offices:FA8649") if r["month"] == month]
    assert not row["done"] and row["next_start"] > 0, "picks up from here next time"
    assert result.stats["backlog"] is True
    store.close()


# ------------------------------------------------- watching it, in one go

@pytest.fixture()
def site(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "default:admin-key", "FOCI_DB": str(tmp_path / "w.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_PUBLIC": "true", "RESEND_API_KEY": "re_not_real",
                "ALERTS_FROM": "alerts@contractscreener.com", "FOCI_SCREEN_TIMES": "any"}
        for name in ENV:
            monkeypatch.delenv(name, raising=False)
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        started = []
        monkeypatch.setattr(app_module.queue, "enqueue",
                            lambda tenant, run_id, options, **kw: started.append(options))
        return TestClient(app_module.app), app_module, started
    return make


def seed(store, *awards):
    store.start_run("seed", {}, run_id="seed-" + store.tenant_id)
    for a in awards:
        store.save_contract(a, "seed-" + store.tenant_id, a.recipient_uei)


def test_one_search_sets_up_the_watch_the_portfolio_and_the_alerts(site, monkeypatch):
    post = Post(monkeypatch)
    c, app_module, started = site()
    account = c.post("/v1/auth/signup", headers=WEB, json={
        "email": ME, "password": PASSWORD, "name": "Jordan Smith"}).json()["account"]
    store = app_module.store_for(account["workspace"])
    seed(store, award("A1", "ACME", "UEI1"), award("A2", "BETA", "UEI2", office="FA2280"))

    looked = c.get("/v1/scopes/lookup?q=afwerx").json()
    assert looked["scope"]["label"] == "AFWERX" and looked["progress"]["watch"] is None

    r = c.post("/v1/scopes/watch", headers=WEB,
               json={"scope": "AFWERX", "emails": [ME, "pat.lee@afwerx.example"]})
    assert r.status_code == 201
    body = r.json()
    assert started[-1]["scope"] == AFWERX.id and started[-1]["skip_web"] is True
    assert body["account"]["portfolio_key"] == body["portfolio_key"]
    sub = store.alert_subscription(body["alerts"]["subscription"]["subscription_id"])
    assert sorted(sub["companies"]) == ["UEI1", "UEI2"]
    assert "pat.lee@afwerx.example" in [p for p in body["alerts"]["consent"]["asked"]]

    again = c.post("/v1/scopes/watch", headers=WEB, json={"scope": "afwerx", "emails": [ME]})
    assert again.json()["watchlist_id"] == body["watchlist_id"]
    assert len(store.alert_subscriptions()) == 1, "the same list, not a second"
    assert len(c.get("/v1/me/portfolios").json()["portfolios"]) == 1
    assert sent_to_count(post, "pat.lee@afwerx.example") == 1


def sent_to_count(post, address):
    return sum(1 for call in post.calls if address in call["body"]["to"])


def test_the_portfolio_of_a_whole_office_lists_it_a_page_at_a_time(site, monkeypatch):
    Post(monkeypatch)
    c, app_module, _ = site()
    store = app_module.store_for("default")
    seed(store, *[award(f"A{n}", f"CO {n}", f"UEI{n:03d}", amount=float(n))
                  for n in range(105)])
    store.save_finding(Finding(entity=Entity(name="CO 3", uei="UEI003"), signals=[],
                               total_score=30.0, severity="high", run_id="seed-default"))
    key = pf.encode(pf.from_scope(AFWERX))
    page = c.post("/v1/portfolio", json={"key": key}).json()
    assert page["totals"]["companies"] == 105
    assert page["companies"][0]["entity_key"] == "UEI003", "the flagged one first"
    assert page["severity"] == {"high": 1}
    assert len(page["companies"]) == 100 and page["scope"]["more"] is True
    rest = c.post("/v1/portfolio", json={"key": key, "offset": 100}).json()
    assert len(rest["companies"]) == 5 and rest["scope"]["more"] is False
    edit = c.post("/v1/portfolio/edit", json={"key": key, "add": ["UEI999"]})
    assert edit.status_code == 400


def test_an_alert_list_on_an_office_follows_it(site, monkeypatch):
    Post(monkeypatch)
    _c, app_module, _ = site()
    store = app_module.store_for("default")
    seed(store, award("A1", "ACME", "UEI1"))
    key = pf.encode(pf.from_scope(AFWERX))
    sid = store.save_alert_subscription(name="AFWERX", emails=["x@y.example"],
                                        companies=["UEI1"], portfolio_key=key)
    store.start_run("later", {}, run_id="later")
    store.save_contract(award("A9", "NEWCO", "UEI9"), "later", "UEI9")
    old = store.create_notice(run_id="later", entity_key="UEI9", entity_name="NEWCO",
                              severity="high", recipient="", officer_confidence="",
                              subject="s", body_text="b")
    sub = store.alert_subscription(sid)
    assert alerts_engine.follow_scope(store, sub) == 1
    assert store.alert_subscription(sid)["companies"] == ["UEI1", "UEI9"]
    assert f"notice:{old}" in store.alert_already_sent(sid), "history is not news"
    new = store.create_notice(run_id="later", entity_key="UEI9", entity_name="NEWCO",
                              severity="high", recipient="", officer_confidence="",
                              subject="s", body_text="b")
    items = alerts_engine.pending_items(store, store.alert_subscription(sid), "")
    assert [i["item_id"] for i in items] == [f"notice:{new}"]


def test_a_watch_catching_up_runs_again_the_same_day_up_to_its_limit(site, monkeypatch):
    c, app_module, started = site(FOCI_SCOPE_RUNS_PER_DAY="2")
    store = app_module.store_for("default")
    wid = store.create_watchlist("AFWERX", {"scope": AFWERX.id, "agency": "AFWERX"})
    store.set_watchlist_backlog(wid, True)
    monkeypatch.setattr(app_module, "CATCH_UP_MINUTES", -1)      # due again at once
    anyone = TestClient(app_module.app)
    assert anyone.post("/v1/schedule/watchlists").json()["status"] == "queued"
    assert anyone.post("/v1/schedule/watchlists").json()["status"] == "queued"
    assert anyone.post("/v1/schedule/watchlists").json()["status"] == "nothing due"
    assert len(started) == 2
    store.set_watchlist_backlog(wid, False)
    assert anyone.post("/v1/schedule/watchlists").json()["status"] == "nothing due"


def test_the_search_box_offers_the_office(site, monkeypatch):
    c, app_module, _ = site()
    monkeypatch.setattr(app_module, "_remote_suggestions", lambda q: [])
    first = c.get("/v1/suggest?q=afwer").json()["local"][0]
    assert (first["kind"], first["label"], first["key"]) == ("scope", "AFWERX", AFWERX.id)
