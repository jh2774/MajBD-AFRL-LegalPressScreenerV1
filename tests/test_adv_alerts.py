"""Form ADV watching and portfolio email alerts.

The fixture mirrors a real filing's extracted text — layout, numbering, and
the two traps found in it: text that repeats cumulatively page after page
(with some copies cut off mid-fund), and "non- United States" spelled with a
space. The firm and fund names are invented.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from foci_screen import alerts
from foci_screen import portfolio as pf
from foci_screen.connectors.adv import (
    AdvConnector,
    AdviserSnapshot,
    PrivateFund,
    band_drawings,
    compare,
    form_text,
    money,
    parse_firm_record,
    parse_form,
    parse_schedule_d,
)
from foci_screen.notify.mailer import Mailer
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


# The form's question wording, verbatim. Each must reach the text as one line,
# because that is how the form prints it and what the parser matches.
Q3 = ("3. (a) Name(s) of General Partner, Manager, Trustee, or Directors "
      "(or persons serving in a similar capacity):")
Q14 = ("14. What is the approximate percentage of the private fund beneficially "
       "owned by you and your related persons:")
Q15 = ("15. (a) What is the approximate percentage of the private fund beneficially "
       "owned (in the aggregate) by funds of funds:")
Q16 = ("16. What is the approximate percentage of the private fund beneficially "
       "owned by non- United States persons:")


def fund_block(name, fund_id, gav, investors, non_us, country="United States",
               state="Delaware"):
    # A fund with no U.S. state prints both labels on one line, country below.
    organised = f"State:\n{state}\nCountry:\n{country}" if state \
        else f"State: Country: \n{country}"
    return f"""A. PRIVATE FUND
Information About the Private Fund
1. (a) Name of the private fund:
{name}
(b) Private fund identification number:
(include the "805-" prefix also)
{fund_id}
2. Under the laws of what state or country is the private fund organized:
{organised}
{Q3}
Name of General Partner, Manager, Trustee, or Director
{name.split(',')[0]} GP, L.L.C.
11. Current gross asset value of the private fund:
$ {gav:,}
Ownership
12. Minimum investment commitment required of an investor in the private fund:
$ 5,000,000
13. Approximate number of the private fund's beneficial owners:
{investors}
{Q14}
2%
{Q15}
10%
{Q16}
{non_us}%
Your Advisory Services
"""


def filing(*blocks, kind="Annual Amendment - All Sections"):
    header = (f"FORM ADV\nPrimary Business Name:HARBORLIGHT CAPITAL CRD Number: 999001\n"
              f"{kind} Rev. 10/2021\n3/31/2026 4:14:38 PM\n")
    body = (f"SECTION 7.B.(1) Private Fund Reporting\n"
            f"Funds per Page: 15 Total Funds: {len(blocks)}\n" + "".join(blocks))
    # The real PDF's text is cumulative: an early page carries a fund cut off
    # at the page break, and a later page repeats everything in full.
    first = blocks[0] if blocks else ""
    truncated = first[: first.find("11. Current")] if first else ""
    return header + truncated + "\n" + header + body


FUND_A = ("HARBORLIGHT PARTNERS IV, L.P.", "805-1111111111")
FUND_B = ("HARBORLIGHT PARTNERS V, L.P.", "805-2222222222")


# ------------------------------------------------------------------ parsing

def test_every_field_is_read_from_a_fund_entry():
    funds, declared, kind = parse_schedule_d(filing(fund_block(*FUND_A, 1_424_911_780, 57, 34)))
    assert declared == 1
    assert kind == "Annual Amendment - All Sections"
    [f] = funds
    assert (f.name, f.fund_id) == FUND_A
    assert f.gross_asset_value == 1_424_911_780
    assert f.investors == 57
    assert f.minimum_investment == 5_000_000
    assert f.owned_by_firm_pct == 2
    assert f.owned_by_funds_of_funds_pct == 10
    assert f.owned_by_non_us_pct == 34, "the 'non- United' spelling must still match"
    assert (f.state, f.country) == ("Delaware", "United States")


def test_a_fund_repeated_by_the_pdf_is_counted_once_at_its_most_complete():
    """The truncated early copy has no size or ownership; it must not win."""
    funds, _, _ = parse_schedule_d(filing(fund_block(*FUND_A, 900_000_000, 40, 20),
                                          fund_block(*FUND_B, 2_000_000_000, 80, 45)))
    assert [f.fund_id for f in funds] == [FUND_A[1], FUND_B[1]]
    assert all(f.gross_asset_value for f in funds)


def test_a_fund_organised_abroad_keeps_its_country():
    """Found on a real filing: 19 Cayman funds came back with no country,
    because a fund without a U.S. state prints that line differently."""
    [f] = parse_schedule_d(filing(fund_block(
        *FUND_A, 155_349_827, 22, 100, country="Cayman Islands", state=None)))[0]
    assert (f.state, f.country) == ("", "Cayman Islands")
    assert f.owned_by_non_us_pct == 100


def test_money_reads_like_a_person_wrote_it():
    assert money(1_424_911_780) == "$1.42 billion"
    assert money(318_900_000) == "$318.9 million"
    assert money(5_000) == "$5,000"
    assert money(None) == "an unreported amount"


def test_the_firm_record_gives_the_filing_date_and_related_firms():
    record = {"hits": {"hits": [{"_source": {"iacontent": json.dumps({
        "basicInformation": {"firmName": "HARBORLIGHT CAPITAL", "advFilingDate": "03/31/2026"},
        "iaFirmAddressDetails": {"officeAddress": {"city": "BETHESDA", "state": "MD",
                                                   "country": "United States"}},
        "relyingAdvisors": [{"name": "HARBORLIGHT V, L.P.", "status": "ACTIVE"},
                            {"name": "OLD ENTITY", "status": "INACTIVE"}],
    })}}]}}
    snap = parse_firm_record("999001", record)
    assert snap.name == "HARBORLIGHT CAPITAL"
    assert snap.filing_date == "03/31/2026"
    assert snap.related_firms == ["HARBORLIGHT V, L.P."], "inactive ones are left out"
    assert snap.office == "Bethesda, MD, United States"


# ------------------------------------------------------------------ changes

def snapshot(date="03/31/2026", funds=(), related=(), read=True, country="United States"):
    return AdviserSnapshot(crd="999001", name="HARBORLIGHT CAPITAL", filing_date=date,
                           funds=list(funds), related_firms=list(related),
                           funds_read=read, office_country=country)


def fund(fid=FUND_A[1], name=FUND_A[0], gav=1_000_000_000, investors=50, non_us=30,
         country="United States"):
    return PrivateFund(fund_id=fid, name=name, gross_asset_value=gav, investors=investors,
                       owned_by_non_us_pct=non_us, country=country)


def kinds(changes):
    return [c.kind for c in changes]


def test_a_first_reading_reports_nothing():
    """A baseline is not news — the firm's whole history would otherwise
    arrive in someone's inbox as though it had just happened."""
    assert compare(None, snapshot(funds=[fund()])) == []


def test_a_new_fund_is_reported_with_its_facts_in_plain_words():
    old = snapshot(funds=[fund()])
    new = snapshot(date="09/30/2026", funds=[fund(), fund(fid=FUND_B[1], name=FUND_B[0],
                                                          gav=2_100_000_000,
                                                          investors=40, non_us=38)])
    changes = compare(old, new)
    new_fund = next(c for c in changes if c.kind == "new_fund")
    assert "reported a new private fund: HARBORLIGHT PARTNERS V, L.P." in new_fund.headline
    assert not new_fund.headline.endswith(".."), "a name ending in L.P. is not '..'"
    assert "$2.10 billion" in new_fund.detail
    assert "40 investors" in new_fund.detail
    assert "38% owned by investors outside the United States" in new_fund.detail
    assert "7.B.(1)" in new_fund.where
    assert "raised, or is raising, money" in new_fund.explainer


def test_foreign_ownership_rising_is_the_most_important_change():
    old = snapshot(funds=[fund(non_us=30)])
    new = snapshot(date="09/30/2026", funds=[fund(non_us=51)])
    changes = compare(old, new)
    assert changes[0].kind == "non_us_share", "ordered first"
    assert "rose from 30% to 51%" in changes[0].headline
    assert "question 16" in changes[0].where


def test_investor_count_and_a_large_size_move_are_reported():
    old = snapshot(funds=[fund(gav=1_000_000_000, investors=50)])
    new = snapshot(date="09/30/2026", funds=[fund(gav=1_400_000_000, investors=65)])
    assert {"investors", "size"} <= set(kinds(compare(old, new)))


def test_ordinary_valuation_drift_is_not_reported():
    """A fund's value moves with its investments every year; below a quarter
    it is noise, and an alert about it would teach people to ignore alerts."""
    old = snapshot(funds=[fund(gav=1_000_000_000)])
    new = snapshot(date="09/30/2026", funds=[fund(gav=1_100_000_000)])
    assert "size" not in kinds(compare(old, new))


def test_a_new_related_firm_and_a_new_filing_are_reported():
    old = snapshot(related=["HARBORLIGHT V, L.P."])
    new = snapshot(date="09/30/2026", related=["HARBORLIGHT V, L.P.", "HARBORLIGHT VI, L.P."])
    changes = compare(old, new)
    assert "new_related_firm" in kinds(changes)
    assert "new_filing" in kinds(changes)


def test_funds_are_not_compared_when_either_side_went_unread():
    """Comparing a read form with an unread one would announce every fund as
    new — or as gone."""
    old = snapshot(funds=[], read=False)
    new = snapshot(funds=[fund()])
    assert "new_fund" not in kinds(compare(old, new))
    assert "fund_gone" not in kinds(compare(snapshot(funds=[fund()]), snapshot(read=False)))


def test_a_change_id_is_stable():
    old = snapshot(funds=[fund(non_us=30)])
    new = snapshot(funds=[fund(non_us=51)])
    assert compare(old, new)[0].change_id == compare(old, new)[0].change_id


# ------------------------------------------------------------------ refresh

class StubHttp:
    def __init__(self, filing_date="03/31/2026", pdf_text=None):
        self.filing_date = filing_date
        self.pdf_text = pdf_text
        self.pdf_calls = 0

    def get(self, url, **kw):
        return {"status": 200, "json": {"hits": {"hits": [{"_source": {"iacontent": json.dumps({
            "basicInformation": {"firmName": "HARBORLIGHT CAPITAL",
                                 "advFilingDate": self.filing_date}})}}]}}}

    def get_bytes(self, url, **kw):
        self.pdf_calls += 1
        return (200, b"%PDF-stub")


def test_the_pdf_is_only_downloaded_when_there_is_a_new_filing(monkeypatch):
    """Megabytes a night per firm for nothing would be both slow and rude."""
    monkeypatch.setattr("foci_screen.connectors.adv.read_form",
                        lambda _: parse_form(
                            filing(fund_block(*FUND_A, 900_000_000, 40, 20))))
    http = StubHttp()
    conn = AdvConnector(http)

    first = conn.refresh("999001")
    assert http.pdf_calls == 1 and first.funds_read

    again = conn.refresh("999001", previous=first)
    assert http.pdf_calls == 1, "same filing date: the stored funds are reused"
    assert again.funds == first.funds

    http.filing_date = "09/30/2026"
    conn.refresh("999001", previous=again)
    assert http.pdf_calls == 2


# ------------------------------------------------- reading the PDF band by band
#
# Stand-ins for the three pypdf objects the reader touches, drawing the way
# IAPD's PDFs were found to: nothing on the page itself, one drawing per band
# showing the band from its top down to that page, and a second drawing — the
# first lines of the next band — on the page where a band ends.

class Drawing:
    """A form XObject that shows `lines`, in order."""

    def __init__(self, lines):
        self.lines = list(lines)

    def get_object(self):
        return self

    def get_data(self):
        def esc(s):
            return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return "\n".join(f"BT ({esc(line)}) Tj ET" for line in self.lines).encode()


class Page:
    def __init__(self, *drawings, own_text=""):
        self.drawings, self.own_text, self.read_whole = drawings, own_text, False

    def get(self, key):
        if key == "/Resources":
            return {"/XObject": {f"/D{i}": d for i, d in enumerate(self.drawings)}}
        if key == "/Contents" and self.own_text:
            return Drawing([self.own_text])
        return None

    def extract_xform_text(self, drawing):
        return "\n".join(drawing.lines)

    def extract_text(self):
        self.read_whole = True
        return "\n".join([self.own_text, *(ln for d in self.drawings for ln in d.lines)])


class Reader:
    def __init__(self, pages):
        self.pages = pages


def banded(*bands, cuts=()):
    """Pages for a form drawn in `bands` (each a list of lines).

    `cuts[b]` lists how many lines of band b are visible on each page before
    the one that completes it.
    """
    pages = []
    for b, lines in enumerate(bands):
        for shown in (cuts[b] if b < len(cuts) else ()):
            pages.append(Page(Drawing(lines[:shown])))
        final = [Drawing(lines)]
        if b + 1 < len(bands):
            final.append(Drawing(bands[b + 1][:2]))     # the sliver of the next band
        pages.append(Page(*final))
    return Reader(pages)


def lines_of(*chunks):
    return [ln for chunk in chunks for ln in chunk.strip("\n").split("\n")]


def test_one_complete_drawing_is_taken_for_each_band():
    one = [f"line {i} of band one" for i in range(60)]
    two = [f"line {i} of band two" for i in range(50)]
    reader = banded(one, two, cuts=([10, 30], [20]))

    drawings = band_drawings(reader)
    assert [d.lines for _, d in drawings] == [one, two]
    assert form_text(reader).split("\n") == one + two, "every line once, in order"
    assert not any(p.read_whole for p in reader.pages)


def test_joining_every_page_gives_a_fund_another_funds_numbers():
    """The failure this reader exists to avoid, as found on a real filing.

    Band one ends part-way through a Cayman fund's entry; band two therefore
    opens with the rest of it — its size, its investors, its 100% foreign
    ownership. A page inside band two then stops part-way through the next
    fund. Joined page after page, that half-entry is followed by the top of
    band two again, and reads as one whole entry carrying the Cayman fund's
    figures. Reading one drawing per band gives each fund its own.
    """
    cayman = fund_block("HARBORLIGHT OFFSHORE FUND, L.P.", "805-3333333333",
                        155_349_827, 22, 100, country="Cayman Islands", state=None)
    split = cayman.index("11. Current gross asset value")
    domestic = fund_block(*FUND_B, 8_894_873, 2, 0)
    header = ("FORM ADV\nAnnual Amendment - All Sections Rev. 10/2021\n"
              "Item 7 Private Fund Reporting")

    one = lines_of(header, fund_block(*FUND_A, 900_000_000, 40, 20), cayman[:split])
    two = lines_of(cayman[split:], domestic, "Item 8 Participation in Client Transactions")
    # The page inside band two ends just before the domestic fund's figures.
    before_figures = domestic[:domestic.index("11. Current gross asset value")]
    stops_inside_domestic = len(lines_of(cayman[split:])) + len(lines_of(before_figures))
    reader = banded(one, two, cuts=([12], [stops_inside_domestic]))

    naive = parse_form("\n".join(p.extract_text() for p in reader.pages))
    wrong = next(f for f in naive.funds if f.fund_id == FUND_B[1])
    assert (wrong.gross_asset_value, wrong.owned_by_non_us_pct) == (155_349_827, 100), \
        "page-by-page, the domestic fund takes the Cayman fund's size and foreign share"

    funds = {f.fund_id: f for f in parse_form(form_text(reader)).funds}
    assert len(funds) == 3
    assert (funds[FUND_B[1]].gross_asset_value, funds[FUND_B[1]].owned_by_non_us_pct) == (
        8_894_873, 0)
    offshore = funds["805-3333333333"]
    assert (offshore.country, offshore.gross_asset_value, offshore.investors,
            offshore.owned_by_non_us_pct) == ("Cayman Islands", 155_349_827, 22, 100), \
        "an entry that spans two bands is read across them"


def test_a_band_redrawn_part_way_is_still_one_band():
    """A table running off the page is drawn in part, then differently once
    the next page shows more of it. The half-drawn version is not a band."""
    body = [f"question {i}" for i in range(50)]
    half_drawn = Drawing(body + ["AL", "AZ", "IL"])
    full = Drawing(body + ["AL", "AK", "AZ", "AR", "IL", "IN"] + ["after the table"])
    reader = Reader([Page(Drawing(body[:20])), Page(half_drawn), Page(full)])

    [(page, drawing)] = band_drawings(reader)
    assert page == 2 and drawing is full


def test_an_ordinary_pdf_is_read_page_by_page():
    """Text on the page itself means it is not built in bands; each page then
    holds only its own text and reading them all is right."""
    reader = Reader([Page(own_text="page one"), Page(own_text="page two")])
    assert band_drawings(reader) is None
    assert "page one" in form_text(reader) and "page two" in form_text(reader)

    unrelated = Reader([Page(Drawing([f"stamp {i}", "x"])) for i in range(12)])
    assert band_drawings(unrelated) is None, "a different drawing on every page is not bands"


def test_fewer_funds_than_the_form_declares_is_not_a_reading():
    text = filing(fund_block(*FUND_A, 900_000_000, 40, 20)).replace(
        "Total Funds: 1", "Total Funds: 3")
    form = parse_form(text)
    assert form.funds_declared == 3 and len(form.funds) == 1 and form.funds_read is False

    old = snapshot(funds=[fund(), fund(fid=FUND_B[1], name=FUND_B[0])])
    short = snapshot(date="09/30/2026", funds=form.funds, read=form.funds_read)
    assert kinds(compare(old, short)) == ["new_filing"], "no fund is reported as gone"


def test_a_failed_download_keeps_the_last_good_reading(monkeypatch):
    """Otherwise the next successful reading becomes the baseline, and what
    changed in between is never reported."""
    class Http(StubHttp):
        ok = True

        def get_bytes(self, url, **kw):
            self.pdf_calls += 1
            return (200, b"%PDF-stub") if self.ok else (503, b"")

    http = Http()
    conn = AdvConnector(http)
    texts = iter([filing(fund_block(*FUND_A, 900_000_000, 40, 20)),
                  filing(fund_block(*FUND_A, 900_000_000, 40, 55))])
    monkeypatch.setattr("foci_screen.connectors.adv.read_form",
                        lambda _: parse_form(next(texts)))
    first = conn.refresh("999001")

    http.filing_date, http.ok = "09/30/2026", False
    held = conn.refresh("999001", previous=first)
    assert held.funds == first.funds and held.filing_date == "03/31/2026"
    assert compare(first, held) == []

    http.ok = True
    fresh = conn.refresh("999001", previous=held)
    assert "non_us_share" in kinds(compare(held, fresh)), "the change still surfaces"


def test_a_reading_by_an_older_reader_is_not_compared_field_by_field():
    """Correcting this tool's own misreading must not go out as news."""
    misread = snapshot(funds=[fund(non_us=0)])                    # reader version 0
    correct = snapshot(funds=[fund(non_us=100)])
    correct.reader_version = 2
    assert compare(misread, correct) == []

    refiled = snapshot(date="09/30/2026", funds=[fund(non_us=100)])
    refiled.reader_version = 2
    [only] = compare(misread, refiled)
    assert only.kind == "new_filing" and "reading of the form was improved" in only.detail


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
    """Serves whatever snapshot the test sets, per CRD."""

    def __init__(self):
        self.current: dict[str, AdviserSnapshot] = {}

    def refresh(self, crd, previous=None):
        return self.current[crd]


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "alerts.db"), tenant_id="acme")
    yield s
    s.close()


def subscribe(store, companies, emails=("ko@agency.gov",)):
    key = pf.encode(pf.from_entity_keys("Harborlight watch", list(companies)))
    sid = store.save_alert_subscription(name="Harborlight watch", emails=list(emails),
                                        companies=list(companies), portfolio_key=key)
    alerts.baseline(store, sid, list(companies))
    return sid


def test_a_change_after_subscribing_produces_one_readable_email(store):
    adv = FakeAdv()
    adv.current["999001"] = snapshot(funds=[fund(non_us=30)])
    alerts.refresh_advisers(store, adv, ["999001"])          # the baseline reading

    sid = subscribe(store, ["CRD:999001"])
    adv.current["999001"] = snapshot(date="09/30/2026", funds=[fund(non_us=51)])

    result = alerts.run_alerts(store, adv, Mailer(FakeCfg()), "https://site.example")
    assert result["emails"][0]["status"] == "drafted", "sending is off by default"

    [delivery] = store.alert_deliveries(sid)
    body = delivery["body_text"]
    assert "rose from 30% to 51%" in body
    assert "What this is:" in body and "Where to look:" in body
    assert "https://adviserinfo.sec.gov/firm/summary/999001" in body
    assert "does not say what the change means" in body


def test_an_item_is_emailed_once(store):
    adv = FakeAdv()
    adv.current["999001"] = snapshot(funds=[fund(non_us=30)])
    alerts.refresh_advisers(store, adv, ["999001"])
    sid = subscribe(store, ["CRD:999001"])
    adv.current["999001"] = snapshot(date="09/30/2026", funds=[fund(non_us=51)])

    alerts.run_alerts(store, adv, Mailer(FakeCfg()))
    second = alerts.run_alerts(store, adv, Mailer(FakeCfg()))
    assert second["emails"][0]["status"] == "nothing new"
    assert len(store.alert_deliveries(sid)) == 1


def test_history_before_subscribing_is_not_sent(store):
    """Someone who starts watching a firm hears what happens next, not a
    backlog of the firm's past presented as news."""
    adv = FakeAdv()
    adv.current["999001"] = snapshot(funds=[fund(non_us=30)])
    alerts.refresh_advisers(store, adv, ["999001"])
    adv.current["999001"] = snapshot(date="09/30/2026", funds=[fund(non_us=51)])
    alerts.refresh_advisers(store, adv, ["999001"])          # change recorded, no one watching

    sid = subscribe(store, ["CRD:999001"])
    result = alerts.run_alerts(store, adv, Mailer(FakeCfg()))
    assert result["emails"][0]["status"] == "nothing new"
    assert store.alert_deliveries(sid) == []


def test_a_failed_send_is_retried_next_time(store, monkeypatch):
    adv = FakeAdv()
    adv.current["999001"] = snapshot(funds=[fund(non_us=30)])
    alerts.refresh_advisers(store, adv, ["999001"])
    sid = subscribe(store, ["CRD:999001"])
    adv.current["999001"] = snapshot(date="09/30/2026", funds=[fund(non_us=51)])

    cfg = FakeCfg()
    cfg.alerts_send = True          # on, but no server configured: fails
    failed = alerts.run_alerts(store, adv, Mailer(cfg))
    assert failed["emails"][0]["status"] == "failed"

    retry = alerts.run_alerts(store, adv, Mailer(FakeCfg()))
    assert retry["emails"][0]["status"] == "drafted", "the item was not lost"
    assert len(store.alert_deliveries(sid)) == 2


def test_a_flagged_contractor_in_the_portfolio_is_included(store):
    sid = subscribe(store, ["UEI123"])
    store.start_run("DoD", {}, run_id="r1")
    store.create_notice(run_id="r1", entity_key="UEI123", entity_name="ACME DYNAMICS LLC",
                        severity="high", recipient="x@mail.mil", officer_confidence="high",
                        subject="s", body_text="b", trigger_reason="Always notify: a novation.")

    alerts.run_alerts(store, FakeAdv(), Mailer(FakeCfg()), "https://site.example")
    [delivery] = store.alert_deliveries(sid)
    assert "ACME DYNAMICS LLC was flagged (HIGH)" in delivery["body_text"]
    assert "https://site.example/#/entity/UEI123" in delivery["body_text"]


# -------------------------------------------------------------------- mailer

class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=30):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def sending_cfg(**kw):
    cfg = FakeCfg()
    cfg.alerts_send = True
    cfg.smtp_host, cfg.smtp_user, cfg.smtp_password = "smtp.example", "me@example.com", "pw"
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_sending_is_off_by_default():
    d = Mailer(FakeCfg()).send(["ko@agency.gov"], "s", "t")
    assert d.status == "drafted"


def test_a_configured_mailer_sends(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    d = Mailer(sending_cfg()).send(["ko@agency.gov", "ko@agency.gov"], "Subject", "Body")
    assert d.status == "sent"
    assert FakeSMTP.sent[0]["To"] == "ko@agency.gov", "duplicates collapse"


def test_the_pilot_redirect_still_applies(monkeypatch):
    """The safety valve that governs notices to contracting officers governs
    these too: while it is set, one person reads everything first."""
    FakeSMTP.sent = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    d = Mailer(sending_cfg(email_redirect_to="me@example.com")).send(
        ["ko@agency.gov"], "Alert", "Body")
    assert d.recipients == ["me@example.com"]
    assert "[for ko@agency.gov]" in FakeSMTP.sent[0]["Subject"]


def test_the_status_says_what_will_happen():
    assert Mailer(FakeCfg()).status()["mode"] == "draft"
    cfg = FakeCfg()
    cfg.alerts_send = True
    assert Mailer(cfg).status()["mode"] == "misconfigured"


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


def portfolio_key(*companies):
    return pf.encode(pf.from_entity_keys("Watch", list(companies)))


def test_a_list_is_saved_with_cleaned_addresses(client):
    c, _ = client
    r = c.post("/v1/alerts/subscriptions", headers=AUTH, json={
        "name": "Watch", "emails": ["KO@Agency.gov, colleague@example.com; ko@agency.gov"],
        "portfolio_key": portfolio_key("CRD:999001")})
    assert r.status_code == 200
    sub = r.json()["subscription"]
    assert sub["emails"] == ["ko@agency.gov", "colleague@example.com"]
    assert sub["companies"] == ["CRD:999001"]


def test_a_bad_address_is_refused_with_its_name(client):
    c, _ = client
    r = c.post("/v1/alerts/subscriptions", headers=AUTH, json={
        "emails": ["not-an-address"], "portfolio_key": portfolio_key("UEI123")})
    assert r.status_code == 422
    assert "not-an-address" in r.text


def test_the_alerts_page_says_sending_is_off(client):
    c, _ = client
    body = c.get("/v1/alerts", headers=AUTH).json()
    assert body["sending"]["mode"] == "draft"
    assert "ALERTS_SEND" in body["sending"]["explanation"]


def test_alert_routes_need_a_key(client):
    c, _ = client
    assert c.get("/v1/alerts").status_code == 401
    assert c.post("/v1/alerts/run").status_code == 401
    assert c.get("/v1/advisers/search?q=x").status_code == 401


def test_a_crd_must_be_digits(client):
    c, _ = client
    assert c.get("/v1/advisers/abc", headers=AUTH).status_code == 400


def test_an_investment_firm_in_a_portfolio_shows_as_a_firm(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.save_adv_snapshot("999001", snapshot(funds=[fund(gav=2_000_000_000)]).to_dict(),
                            "03/31/2026")
    key = pf.encode(pf.from_entity_keys("Watch", ["CRD:999001"],
                                        {"CRD:999001": "HARBORLIGHT CAPITAL"}))
    row = c.post("/v1/portfolio", headers=AUTH, json={"key": key}).json()["companies"][0]
    assert row["kind"] == "adviser"
    assert row["fund_count"] == 1
    assert row["fund_assets"] == 2_000_000_000
