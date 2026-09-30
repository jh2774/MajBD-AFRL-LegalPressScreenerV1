"""Form ADV: what an investment firm tells the SEC about itself, watched for change.

Form ADV is the registration form an investment firm — private equity, venture
capital, a hedge fund manager — files with the SEC and must keep current. It is
where a firm that owns or backs a defence contractor has to disclose, in
public, the funds it runs, how big they are, how many investors are in them,
and how much of each is owned by investors outside the United States.

**Schedule D, Section 7.B.(1)** is the part that matters most here. Each
private fund the firm advises gets its own entry: name, where it is organised,
its size, its investor count, and — question 16 — the share owned by non-U.S.
persons. A new entry is a firm raising money. A change in question 16 is
foreign money arriving in, or leaving, a fund that may own a contractor.

Where it comes from, and why this way:

  * The firm's summary is a JSON record from IAPD's API. It carries the date
    of the latest filing, which is all that is needed to know whether anything
    changed — so the expensive part is skipped on most nights.
  * The form itself is only available as a PDF. adviserinfo.sec.gov serves the
    same form section by section as web pages, and its robots.txt disallows
    those pages to automated clients. This tool does not read them.

That PDF has a quirk worth knowing: its extracted text is cumulative. Each
page repeats every page before it within a section, so one fund's entry
appears a dozen times, some copies cut off mid-way at a page boundary. Funds
are therefore keyed on their SEC fund number and the most complete copy wins.

Everything this module says to a reader is written for someone with no
background in finance: what changed, one sentence on what the item on the form
is, and where to look. It does not judge what the change means for the
contractor — that is the reader's call, and the point of the alert is to put
them in front of the form, not to stand in for it.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger("foci.adv")

SEARCH_API = "https://api.adviserinfo.sec.gov/search/firm"
FIRM_API = "https://api.adviserinfo.sec.gov/search/firm/{crd}"
PDF_URL = "https://reports.adviserinfo.sec.gov/reports/ADV/{crd}/PDF/{crd}.pdf"
SUMMARY_URL = "https://adviserinfo.sec.gov/firm/summary/{crd}"

SECTION_7B = "Schedule D, Section 7.B.(1) — Private Fund Reporting"


# ----------------------------------------------------------------- the record

@dataclass
class PrivateFund:
    fund_id: str                          # the SEC's 805- number: stable forever
    name: str = ""
    state: str = ""
    country: str = ""
    general_partner: str = ""
    gross_asset_value: int | None = None  # question 11
    minimum_investment: int | None = None  # question 12
    investors: int | None = None           # question 13
    owned_by_firm_pct: int | None = None   # question 14
    owned_by_funds_of_funds_pct: int | None = None  # question 15(a)
    owned_by_non_us_pct: int | None = None  # question 16

    def completeness(self) -> int:
        return sum(v is not None and v != "" for v in asdict(self).values())


@dataclass
class AdviserSnapshot:
    crd: str
    name: str = ""
    filing_date: str = ""      # MM/DD/YYYY, from IAPD
    filing_kind: str = ""      # "Annual Amendment - All Sections", from the form
    office: str = ""           # "Bethesda, MD, United States"
    office_country: str = ""
    related_firms: list[str] = field(default_factory=list)
    registrations: list[str] = field(default_factory=list)
    funds: list[PrivateFund] = field(default_factory=list)
    funds_declared: int | None = None  # "Total Funds: 5" on the form
    funds_read: bool = False           # whether Schedule D was actually read

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> AdviserSnapshot:
        raw = dict(raw or {})
        funds = [PrivateFund(**f) for f in raw.pop("funds", []) or []]
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known, funds=funds)

    def fund(self, fund_id: str) -> PrivateFund | None:
        return next((f for f in self.funds if f.fund_id == fund_id), None)


# ------------------------------------------------------------------- reading

def parse_firm_record(crd: str, payload: dict) -> AdviserSnapshot:
    """The cheap half: IAPD's JSON record for one firm."""
    import json

    snap = AdviserSnapshot(crd=str(crd))
    hits = ((payload or {}).get("hits") or {}).get("hits") or []
    if not hits:
        return snap
    content = hits[0].get("_source", {}).get("iacontent")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    content = content or {}

    basic = content.get("basicInformation") or {}
    snap.name = basic.get("firmName") or ""
    snap.filing_date = basic.get("advFilingDate") or ""

    office = ((content.get("iaFirmAddressDetails") or {}).get("officeAddress") or {})
    snap.office_country = office.get("country") or ""
    snap.office = ", ".join(x for x in (office.get("city", "").title(),
                                         office.get("state", ""),
                                         snap.office_country) if x)

    # Relying advisers are the separate firms — usually one partnership per
    # fund — that share this registration. A new one often means a new fund.
    snap.related_firms = sorted({(r.get("name") or "").strip()
                                 for r in content.get("relyingAdvisors") or []
                                 if (r.get("status") or "").upper() == "ACTIVE"
                                 and r.get("name")})
    snap.registrations = sorted(
        f"{r.get('secJurisdiction') or r.get('jurisdiction') or '?'}: {r.get('status')}"
        for r in content.get("registrationStatus") or [])
    return snap


_MONEY = r"\$\s*([\d,]+)"
_PCT = r"(\d{1,3})\s*%"

FIELDS = {
    # attribute: (question text as it appears on the form, value pattern)
    "gross_asset_value": (r"11\.\s*Current gross asset value of the private fund:", _MONEY),
    "minimum_investment": (r"12\.\s*Minimum investment commitment required of an investor "
                           r"in the private fund:", _MONEY),
    "investors": (r"13\.\s*Approximate number of the private fund'?s beneficial owners:",
                  r"([\d,]+)"),
    "owned_by_firm_pct": (r"14\.\s*What is the approximate percentage of the private fund "
                          r"beneficially owned by you and your related persons:", _PCT),
    "owned_by_funds_of_funds_pct": (r"15\.\s*\(a\)\s*What is the approximate percentage of "
                                    r"the private fund beneficially owned \(in the aggregate\) "
                                    r"by funds of funds:", _PCT),
    # The form says "non-United States"; the PDF's text comes out "non- United
    # States". Both are matched, because the first version of this looked for
    # the printed spelling and found nothing.
    "owned_by_non_us_pct": (r"16\.\s*What is the approximate percentage of the private "
                            r"fund beneficially owned by non-\s*United States persons:", _PCT),
}

NAME_RX = re.compile(r"1\.\s*\(a\)\s*Name of the private fund:\s*\n\s*(.+)")
ID_RX = re.compile(r"(805-\d{10})")
STATE_RX = re.compile(r"organized:\s*\n\s*State:\s*\n\s*(.*?)\s*\n\s*Country:\s*\n\s*(.+)")
GP_RX = re.compile(r"Name of General Partner, Manager, Trustee, or Director\s*\n\s*(.+)")
TOTAL_RX = re.compile(r"Total Funds:\s*(\d+)")
KIND_RX = re.compile(r"\n((?:Annual|Other-Than-Annual|Initial)[^\n]*?)\s*Rev\.")
FUND_START = re.compile(r"1\.\s*\(a\)\s*Name of the private fund:")


def _number(text: str) -> int | None:
    try:
        return int(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_schedule_d(text: str) -> tuple[list[PrivateFund], int | None, str]:
    """(funds, funds declared on the form, filing kind) from the form's text."""
    starts = [m.start() for m in FUND_START.finditer(text)]
    best: dict[str, PrivateFund] = {}

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else start + 12000
        block = text[start:min(end, start + 12000)]

        id_match = ID_RX.search(block[:600])
        if not id_match:
            continue
        name = NAME_RX.search(block)
        fund = PrivateFund(fund_id=id_match.group(1),
                           name=(name.group(1).strip() if name else ""))

        where = STATE_RX.search(block)
        if where:
            fund.state = where.group(1).strip()
            fund.country = where.group(2).strip()
        gp = GP_RX.search(block)
        if gp:
            fund.general_partner = gp.group(1).strip()

        for attr, (question, value) in FIELDS.items():
            m = re.search(question + r"\s*\n?\s*" + value, block, re.I)
            if m:
                setattr(fund, attr, _number(m.group(1)))

        kept = best.get(fund.fund_id)
        if kept is None or fund.completeness() > kept.completeness():
            best[fund.fund_id] = fund

    declared = TOTAL_RX.search(text)
    kind = KIND_RX.search(text)
    return (sorted(best.values(), key=lambda f: f.name),
            int(declared.group(1)) if declared else None,
            kind.group(1).strip() if kind else "")


def pdf_text(pdf: bytes) -> str:
    """Every page's text. Slow on these filings — see `read_form`."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _drawn_size(page) -> int:
    """Bytes of what a page draws, read without laying out any text."""
    try:
        xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
        return sum(len(ref.get_object().get_data()) for ref in xobjects.values())
    except Exception:       # noqa: BLE001 - a page we cannot size is simply read
        return 0


def section_end_pages(reader) -> list[int] | None:
    """Pages that end a cumulative section, or None if the PDF is not built so.

    IAPD renders Form ADV through an HTML-to-PDF tool that draws each page as
    one object holding everything since the start of its section, so the
    last page of a section already contains all of it. Extracting all 63
    pages of a real filing took 27 seconds; extracting the three that end a
    section takes about three. The sizes that reveal the structure cost
    nothing to read.

    None means the pattern is absent, and every page must be read.
    """
    sizes = [_drawn_size(p) for p in reader.pages]
    if not sizes or not any(sizes):
        return None
    # A section restarts with a collapse in size — 562 KB to 39 KB on a real
    # filing — while the pages inside one wobble by a few percent. Only the
    # collapses are section ends. Treating every wobble as one was correct but
    # read eleven pages instead of three; `read_form` checks the result either
    # way, and reads everything if anything is missing.
    ends = [i for i in range(len(sizes))
            if i == len(sizes) - 1 or sizes[i + 1] < sizes[i] * 0.5]
    # In a cumulative document runs are long; in an ordinary one sizes wander
    # and nearly every other page would look like an "end".
    if len(ends) > max(3, len(sizes) // 4):
        return None
    return ends


def read_form(pdf: bytes) -> tuple[list[PrivateFund], int | None, str]:
    """Schedule D 7.B from a Form ADV, as quickly as it can be done correctly.

    The fast path reads only the pages that end each section, and is trusted
    only when it recovers every fund the form says it has, each with its
    foreign-ownership figure. Anything less and every page is read — slower,
    but a fund silently missing from an alert is the failure that matters.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    ends = section_end_pages(reader)
    if ends:
        text = "\n".join((reader.pages[i].extract_text() or "") for i in ends)
        funds, declared, kind = parse_schedule_d(text)
        complete = (declared is not None and len(funds) >= declared
                    and all(f.owned_by_non_us_pct is not None for f in funds))
        if complete or declared == 0:
            return funds, declared, kind
        log.info("fast read of Form ADV found %d of %s funds; reading every page",
                 len(funds), declared)
    return parse_schedule_d("\n".join((p.extract_text() or "") for p in reader.pages))


class AdvConnector:
    """Reads a firm's IAPD record and, when it has changed, its Form ADV."""

    name = "adv"

    def __init__(self, http) -> None:
        self.http = http

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Investment firms by name, for someone choosing which one to watch."""
        query = (query or "").strip()
        if len(query) < 2:
            return []
        resp = self.http.get(SEARCH_API, params={
            "query": query, "hl": "true", "nrows": limit, "start": 0,
            "r": 25, "sort": "score+desc", "wt": "json"}, use_cache=False)
        out = []
        for hit in (((resp.get("json") or {}).get("hits") or {}).get("hits") or []):
            src = hit.get("_source", {})
            if not src.get("firm_ia_scope"):
                continue        # a broker-dealer only: it files no Form ADV
            out.append({
                "crd": str(src.get("firm_source_id") or ""),
                "name": src.get("firm_name") or "",
                "status": src.get("firm_ia_scope") or "",
                "where": ", ".join(x for x in (
                    (src.get("firm_ia_address_details") or {}).get("city", "")
                    if isinstance(src.get("firm_ia_address_details"), dict) else "",
                ) if x),
            })
        return out

    def refresh(self, crd: str, previous: AdviserSnapshot | None = None) -> AdviserSnapshot:
        """Current state of a firm. Schedule D is read only when the filing is new."""
        crd = str(crd).strip()
        resp = self.http.get(FIRM_API.format(crd=crd), use_cache=False)
        if resp.get("status") != 200:
            raise AdvUnavailable(f"IAPD did not return firm {crd} "
                                 f"(status {resp.get('status') or 'no response'}).")
        snap = parse_firm_record(crd, resp.get("json") or {})
        if not snap.name:
            raise AdvUnavailable(f"IAPD has no investment-adviser record for CRD {crd}.")

        unchanged = (previous is not None and previous.funds_read
                     and previous.filing_date == snap.filing_date)
        if unchanged:
            snap.funds = previous.funds
            snap.funds_declared = previous.funds_declared
            snap.filing_kind = previous.filing_kind
            snap.funds_read = True
            return snap

        status, pdf = self.http.get_bytes(PDF_URL.format(crd=crd))
        if status != 200 or not pdf.startswith(b"%PDF"):
            log.warning("Form ADV for %s not readable (status %s)", crd, status)
            return snap
        try:
            funds, declared, kind = read_form(pdf)
        except Exception as exc:        # noqa: BLE001 - a bad PDF must not sink a run
            log.warning("Form ADV for %s did not parse: %s", crd, exc)
            return snap
        snap.funds, snap.funds_declared, snap.filing_kind = funds, declared, kind
        snap.funds_read = True
        return snap


class AdvUnavailable(RuntimeError):
    """IAPD could not be read for this firm. Said plainly, not raised as a bug."""


# ------------------------------------------------------------------- changes

@dataclass
class AdvChange:
    crd: str
    firm: str
    kind: str
    headline: str          # what happened, in one plain sentence
    detail: str            # the numbers, if there are any
    explainer: str         # what this item on the form is, for a non-specialist
    where: str             # the part of the form to open
    importance: int        # for ordering: higher first

    @property
    def change_id(self) -> str:
        raw = f"{self.crd}|{self.kind}|{self.headline}|{self.detail}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def links(self) -> dict:
        return {"summary": SUMMARY_URL.format(crd=self.crd),
                "form": PDF_URL.format(crd=self.crd)}

    def to_dict(self) -> dict:
        return {**asdict(self), "change_id": self.change_id, "links": self.links()}


def money(value: int | None) -> str:
    """$1,424,911,780 -> "$1.42 billion". Read by people, not accountants."""
    if value is None:
        return "an unreported amount"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f} billion"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f} million"
    return f"${value:,}"


EXPLAIN = {
    "filing": ("Form ADV is the registration form investment firms file with the SEC. "
               "They must update it at least once a year and whenever important facts "
               "change."),
    "fund": ("A private fund is a pool of money a firm collects from investors and "
             "invests on their behalf — often by buying companies. A new one usually "
             "means the firm has raised, or is raising, money."),
    "fund_gone": ("A fund drops off the form when it has closed, been wound down, or is "
                  "now reported by a different firm."),
    "non_us": ("This is the share of the fund owned by people or organisations based "
               "outside the United States (question 16 on the form)."),
    "investors": ("The number of separate investors in the fund. It usually rises when "
                  "the fund takes in money from new investors."),
    "size": ("The total value of what the fund holds. It rises when investors put in "
             "new money and also when the fund's investments grow in value, so the "
             "number alone does not say which."),
    "country": ("Where the fund is legally set up. Funds organised outside the U.S. are "
                "common, but a move is worth knowing about."),
    "related": ("Firms often create a separate partnership to run each new fund, and "
                "list it on their form as a related firm. A new one frequently comes "
                "with a new fund."),
    "office": "Where the firm says its main office is.",
}

# A reported value moves with the market every year; below this it is noise.
SIZE_CHANGE_FRACTION = 0.25


def compare(old: AdviserSnapshot | None, new: AdviserSnapshot) -> list[AdvChange]:
    """What changed, in words. A first look is a baseline and reports nothing."""
    if old is None:
        return []
    firm = new.name or old.name
    out: list[AdvChange] = []

    def add(kind, headline, detail, explainer, where, importance):
        # Fund names end in "L.P." — so a sentence ending in one came out
        # "L.P..". Collapsed here, once, for every headline and detail.
        headline, detail = (re.sub(r"\.\.+(\s|$)", r".\1", t) for t in (headline, detail))
        out.append(AdvChange(new.crd, firm, kind, headline, detail, explainer,
                             where, importance))

    if new.filing_date and new.filing_date != old.filing_date:
        kind = f" ({new.filing_kind.split(' - ')[0].lower()})" if new.filing_kind else ""
        add("new_filing", f"{firm} filed an updated Form ADV{kind} on {new.filing_date}.",
            f"Previous filing: {old.filing_date or 'not recorded'}.",
            EXPLAIN["filing"], "The whole form", 1)

    for name in sorted(set(new.related_firms) - set(old.related_firms)):
        add("new_related_firm", f"{firm} added a related firm: {name}.", "",
            EXPLAIN["related"], "Schedule R and Section 1.B of Schedule D", 3)

    if new.office_country and old.office_country \
            and new.office_country != old.office_country:
        add("office_moved", f"{firm} moved its main office from {old.office_country} "
                            f"to {new.office_country}.",
            f"Now listed as {new.office}.", EXPLAIN["office"], "Item 1.F", 4)

    # Fund-level changes need both sides to have been read. Comparing a read
    # form against an unread one would report every fund as new or gone.
    if not (old.funds_read and new.funds_read):
        return sorted(out, key=lambda c: -c.importance)

    before = {f.fund_id: f for f in old.funds}
    after = {f.fund_id: f for f in new.funds}

    for fund_id in sorted(set(after) - set(before)):
        f = after[fund_id]
        facts = [f"It holds {money(f.gross_asset_value)}"]
        if f.investors is not None:
            facts.append(f"has {f.investors:,} investor{'s' if f.investors != 1 else ''}")
        if f.owned_by_non_us_pct is not None:
            facts.append(f"is {f.owned_by_non_us_pct}% owned by investors outside the "
                         f"United States")
        where_set = f"It is organised in {f.state + ', ' if f.state else ''}{f.country}." \
            if f.country else ""
        add("new_fund", f"{firm} reported a new private fund: {f.name}.",
            ", ".join(facts) + ". " + where_set, EXPLAIN["fund"], SECTION_7B, 5)

    for fund_id in sorted(set(before) - set(after)):
        add("fund_gone", f"{firm} no longer reports the private fund {before[fund_id].name}.",
            "", EXPLAIN["fund_gone"], SECTION_7B, 2)

    for fund_id in sorted(set(before) & set(after)):
        a, b = before[fund_id], after[fund_id]
        if a.owned_by_non_us_pct is not None and b.owned_by_non_us_pct is not None \
                and a.owned_by_non_us_pct != b.owned_by_non_us_pct:
            direction = "rose" if b.owned_by_non_us_pct > a.owned_by_non_us_pct else "fell"
            add("non_us_share", f"The share of {b.name} owned by investors outside the "
                                f"United States {direction} from {a.owned_by_non_us_pct}% "
                                f"to {b.owned_by_non_us_pct}%.",
                f"The fund holds {money(b.gross_asset_value)}.",
                EXPLAIN["non_us"], SECTION_7B + ", question 16",
                6 if direction == "rose" else 4)

        if a.investors is not None and b.investors is not None and a.investors != b.investors:
            add("investors", f"{b.name} went from {a.investors:,} to {b.investors:,} "
                             f"investors.", "", EXPLAIN["investors"],
                SECTION_7B + ", question 13", 3)

        if a.gross_asset_value and b.gross_asset_value:
            move = (b.gross_asset_value - a.gross_asset_value) / a.gross_asset_value
            if abs(move) >= SIZE_CHANGE_FRACTION:
                add("size", f"{b.name} reported {money(b.gross_asset_value)}, "
                            f"{'up' if move > 0 else 'down'} from "
                            f"{money(a.gross_asset_value)}.",
                    f"A change of {abs(move):.0%}.", EXPLAIN["size"],
                    SECTION_7B + ", question 11", 2)

        if a.country and b.country and a.country != b.country:
            add("fund_country", f"{b.name} is now organised in {b.country} "
                                f"(previously {a.country}).",
                "", EXPLAIN["country"], SECTION_7B + ", question 2", 4)

    return sorted(out, key=lambda c: -c.importance)
