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

**Schedules A and B** say who owns the firm itself. A lists everyone holding
5% or more directly, and the top executives; B climbs the chain, listing who
owns those owners at 25% or more. Each owner is marked as a person, a U.S.
entity, or a foreign entity — the one place the form says, in a single
column, that the firm running the money is itself held from abroad. It does
not name the country, and it says nothing about a person's nationality.

Where it comes from, and why this way:

  * The firm's summary is a JSON record from IAPD's API. It carries the date
    of the latest filing, which is all that is needed to know whether anything
    changed — so the expensive part is skipped on most nights.
  * The form itself is only available as a PDF. adviserinfo.sec.gov serves the
    same form section by section as web pages, and its robots.txt disallows
    those pages to automated clients. This tool does not read them.

That PDF is built in a way that decides how it must be read. IAPD renders the
form onto tall strips — "bands", about 24 pages each, cut wherever the strip
runs out and not where the form's sections end. Every page then draws its
band from the top of the band down to that page, so a page's extracted text is
everything in the band so far, and a page that crosses two bands draws both.

Two consequences. Reading every page and joining the text is not a slower,
safer option: it is wrong. An entry cut off by a page break is followed by the
next page's text, which starts at the top of the band — usually the tail end
of a *different* fund's entry — and the two read as one complete entry
carrying the other fund's numbers. On a real 303-page filing that put five
funds at the same $1.9 million and 0% foreign-owned; one of them is a $155
million Cayman fund wholly owned from outside the United States. And the
right reading is cheap: the last drawing of each band holds the whole band,
so one drawing per band, in order, is the form exactly once. See
`band_drawings`.

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
PEOPLE_API = "https://api.adviserinfo.sec.gov/search/individual"
FIRM_API = "https://api.adviserinfo.sec.gov/search/firm/{crd}"
PDF_URL = "https://reports.adviserinfo.sec.gov/reports/ADV/{crd}/PDF/{crd}.pdf"
SUMMARY_URL = "https://adviserinfo.sec.gov/firm/summary/{crd}"

SECTION_7B = "Schedule D, Section 7.B.(1) — Private Fund Reporting"
SCHEDULE_A = "Schedule A — Direct Owners and Executive Officers"
SCHEDULE_B = "Schedule B — Indirect Owners"

# Bumped whenever this module learns to read another part of the form, so a
# firm stored by an older version is read once more even though its filing
# date has not moved.
READER_VERSION = 2


# ----------------------------------------------------------------- the record

# The form's ownership codes, in the order they rank. F appears only on
# Schedule B and is not a share at all.
OWNERSHIP = {
    "NA": "less than 5%",
    "A": "5% to 10%",
    "B": "10% to 25%",
    "C": "25% to 50%",
    "D": "50% to 75%",
    "E": "75% or more",
    "F": "a general partner, trustee or elected manager (no percentage given)",
}
_RANK = {"NA": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5}

OWNER_KIND = {"I": "a person", "DE": "a U.S. company",
              "FE": "a company based outside the United States"}


@dataclass
class Owner:
    """One row of Schedule A (direct) or Schedule B (indirect)."""
    name: str                  # as filed: "MURPHY, BRIAN, JOSEPH" or "SIPCO LTD"
    kind: str = ""             # "I" person, "DE" U.S. entity, "FE" foreign entity
    title: str = ""            # title or status: "MEMBER", "CHIEF COMPLIANCE OFFICER"
    since: str = ""            # MM/YYYY
    code: str = ""             # ownership code; see OWNERSHIP
    control: bool = False      # the form's "control person" column
    public_company: bool = False
    indirect: bool = False     # True for Schedule B
    through: str = ""          # Schedule B: the owner this one owns

    @property
    def key(self) -> str:
        return f"{'B' if self.indirect else 'A'}|{' '.join(self.name.upper().split())}"

    @property
    def foreign(self) -> bool:
        return self.kind == "FE"

    @property
    def display(self) -> str:
        """A person's name the way it is said; an entity's as filed."""
        if self.kind != "I" or "," not in self.name:
            return self.name
        last, *rest = (p.strip() for p in self.name.split(","))
        said = " ".join(x for x in (*rest, last) if x).title()
        return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), said)

    @property
    def share(self) -> str:
        return OWNERSHIP.get(self.code, "")

    def to_dict(self) -> dict:
        return {**asdict(self), "display": self.display, "share": self.share,
                "foreign": self.foreign}

    @classmethod
    def from_dict(cls, raw: dict) -> Owner:
        return cls(**{k: v for k, v in (raw or {}).items()
                      if k in cls.__dataclass_fields__})

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
    owners: list[Owner] = field(default_factory=list)   # Schedules A and B
    owners_read: bool = False          # whether Schedule A was found and read
    reader_version: int = 0            # READER_VERSION when the form was read

    def to_dict(self) -> dict:
        return {**asdict(self), "owners": [o.to_dict() for o in self.owners]}

    @classmethod
    def from_dict(cls, raw: dict) -> AdviserSnapshot:
        raw = dict(raw or {})
        funds = [PrivateFund(**f) for f in raw.pop("funds", []) or []]
        owners = [Owner.from_dict(o) for o in raw.pop("owners", []) or []]
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known, funds=funds, owners=owners)

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
# A fund organised outside the U.S. has no state, and the form then prints both
# labels on one line with only the country under them. Missing this left every
# foreign-organised fund — 19 of 55 on one real filing, all in the Cayman
# Islands — with no country at all.
COUNTRY_ONLY_RX = re.compile(r"organized:\s*\n\s*State:\s*Country:\s*\n\s*(.+)")
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
        abroad = COUNTRY_ONLY_RX.search(block)
        if where:
            fund.state = where.group(1).strip()
            fund.country = where.group(2).strip()
        elif abroad:
            fund.country = abroad.group(1).strip()
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


# ------------------------------------------------------- Schedules A and B
#
# Who owns the firm (A) and who owns those owners (B). Each is a table whose
# cells wrap, so a row arrives as several lines:
#
#     INVESTCORP INTERNATIONAL
#     HOLDINGS INC.
#     DE SOLE MEMBER 08/2005 E Y N
#
# What every row does end with is the same four columns in a fixed shape —
# date, ownership code, control, public company — so rows are found by that
# tail and everything before it is the name, the DE/FE/I column, and the title.
# The last column on the form (CRD, tax or Social Security number) is matched
# so it does not run into the next row, and is never kept.

_SCHED_A = re.compile(r"^Schedule A\s*\nDirect Owners and Executive Officers", re.M)
_SCHED_B = re.compile(r"^Schedule B\s*\nIndirect Owners", re.M)
_AFTER_B = re.compile(r"^(?:Schedule D - Miscellaneous|Schedule R\b|DRP Pages)", re.M)
_TABLE_HEADER = re.compile(r"FULL LEGAL NAME.*?Employer\s+ID\s+No\.", re.S)
_NOTHING_FILED = re.compile(r"\(c\) Complete each column\.\s*No Information Filed")
_ROW_TAIL = re.compile(
    r"(?P<since>\d{2}/\d{4})\s+(?P<code>NA|[A-F])\s+(?P<control>[YN])\s+(?P<pr>PR|N)"
    r"(?:\s+[\d-]{3,12}(?=\s|$))?")
_STATUS = re.compile(
    r"\b(?:GENERAL PARTNER|LIMITED PARTNER|MANAGING MEMBER|SOLE MEMBER|ELECTED MANAGER|"
    r"SHAREHOLDER|STOCKHOLDER|MEMBER|PARTNER|TRUSTEE|BENEFICIARY|MANAGER|OWNER)\b.*$", re.I)


def _norm(name: str) -> str:
    return " ".join(name.upper().split())


def _schedule_tables(text: str, heading: re.Pattern, end: re.Pattern) -> list[str] | None:
    """The table body of every complete copy of one schedule.

    None when no complete copy exists — the heading is missing, or every copy
    is cut off by a page boundary. That is "not read", which is not the same
    as "nothing filed" (an empty string here) and must not be mistaken for it:
    owners that go unread one night would all be reported as new the next.
    """
    tables: list[str] = []
    for m in heading.finditer(text):
        rest = text[m.end(): m.end() + 80000]
        again = heading.search(rest)
        if again:
            rest = rest[: again.start()]
        stop = end.search(rest)
        if not stop:
            continue                    # cut off mid-schedule
        body = rest[: stop.start()]
        if _NOTHING_FILED.search(body):
            tables.append("")
            continue
        header = _TABLE_HEADER.search(body)
        if header:
            tables.append(_TABLE_HEADER.sub(" ", body[header.end():]))
    return tables or None


def _splits(lead: str) -> list[tuple[str, str, str]]:
    """Every way to read "NAME <DE|FE|I> REST" out of a row's leading text.

    Names contain these tokens too — "BANCO DE CHILE", a middle initial I — so
    there can be more than one, and the caller chooses. A token straight after
    a comma is part of a person's name and is never the column.
    """
    tokens = lead.split()
    return [(" ".join(tokens[:i]), tok, " ".join(tokens[i + 1:]))
            for i, tok in enumerate(tokens)
            if i and tok in OWNER_KIND and not tokens[i - 1].endswith(",")]


def _pick(lead: str, *, prefer_last_entity: bool) -> tuple[str, str, str] | None:
    options = _splits(lead)
    if not options:
        return None
    if lead.split()[0].endswith(","):           # "LAST, FIRST ..." — a person
        return next((o for o in options if o[1] == "I"), options[0])
    entities = [o for o in options if o[1] != "I"]
    if not entities:
        return options[0]
    return entities[-1] if prefer_last_entity else entities[0]


def _leads(table: str) -> list[tuple[str, re.Match]]:
    flat = " ".join(table.split())
    rows, pos = [], 0
    for m in _ROW_TAIL.finditer(flat):
        lead = flat[pos:m.start()].strip()
        pos = m.end()
        if lead:
            rows.append((lead, m))
    return rows


def _owner(name: str, kind: str, tail: re.Match, **kw) -> Owner:
    return Owner(name=name, kind=kind, since=tail["since"], code=tail["code"],
                 control=tail["control"] == "Y", public_company=tail["pr"] == "PR", **kw)


def _direct_owners(table: str) -> list[Owner]:
    out: dict[str, Owner] = {}
    for lead, tail in _leads(table):
        # A title never contains DE or FE, so the last such token is the column.
        split = _pick(lead, prefer_last_entity=True)
        if split:
            owner = _owner(split[0], split[1], tail, title=split[2])
            out.setdefault(owner.key, owner)
    return list(out.values())


def _indirect_owners(table: str, known: set[str]) -> list[Owner]:
    """Schedule B rows: NAME, DE/FE/I, the owner it holds, status.

    The owner it holds is always someone already listed — on Schedule A, or
    further down this same schedule — which is what settles an ambiguous row:
    the right reading is the one whose remainder starts with a known name.
    The chain is climbed a level at a time until nothing more resolves.
    """
    def through(rest: str, names: set[str]) -> tuple[str, str] | None:
        upper = _norm(rest)
        best = max((n for n in names if upper == n or upper.startswith(n + " ")),
                   key=len, default="")
        return (rest[: len(best)].strip(), rest[len(best):].strip()) if best else None

    names = set(known)
    pending = [(lead, tail, _splits(lead)) for lead, tail in _leads(table)]
    done: dict[int, Owner] = {}
    progress = True
    while progress:
        progress = False
        for i, (_lead, tail, options) in enumerate(pending):
            if i in done:
                continue
            for name, kind, rest in options:
                hit = through(rest, names)
                if hit:
                    done[i] = _owner(name, kind, tail, indirect=True,
                                     through=hit[0], title=hit[1])
                    names.add(_norm(name))
                    progress = True
                    break

    for i, (lead, tail, _options) in enumerate(pending):
        if i in done:
            continue
        split = _pick(lead, prefer_last_entity=False)
        if not split:
            continue
        status = _STATUS.search(split[2])
        done[i] = _owner(split[0], split[1], tail, indirect=True,
                         through=split[2][: status.start()].strip() if status else split[2],
                         title=status.group(0).strip() if status else "")

    out: dict[str, Owner] = {}
    for i in sorted(done):
        out.setdefault(done[i].key, done[i])
    return list(out.values())


def parse_owners(text: str) -> tuple[list[Owner], bool]:
    """(owners on Schedules A and B, whether both were read in full)."""
    direct_tables = _schedule_tables(text, _SCHED_A, _SCHED_B)
    if direct_tables is None:
        return [], False
    direct = max((_direct_owners(t) for t in direct_tables), key=len)

    if not _SCHED_B.search(text):
        return direct, False
    indirect_tables = _schedule_tables(text, _SCHED_B, _AFTER_B)
    if indirect_tables is None:
        return direct, False
    known = {_norm(o.name) for o in direct}
    indirect = max((_indirect_owners(t, known) for t in indirect_tables), key=len)
    return direct + indirect, True


@dataclass
class FormReading:
    """Everything read from one Form ADV PDF."""
    funds: list[PrivateFund] = field(default_factory=list)
    funds_declared: int | None = None
    filing_kind: str = ""
    owners: list[Owner] = field(default_factory=list)
    owners_read: bool = False
    # False when the form says it has more funds than were found. The funds
    # are then not compared with anything: a short list would report the
    # missing ones as gone.
    funds_read: bool = True


def parse_form(text: str) -> FormReading:
    funds, declared, kind = parse_schedule_d(text)
    owners, owners_read = parse_owners(text)
    short = declared is not None and len(funds) < declared
    if short:
        log.warning("Form ADV declares %d private funds; %d were read", declared, len(funds))
    return FormReading(funds, declared, kind, owners, owners_read, funds_read=not short)


# A text-showing operator in a PDF content stream: (string) Tj, [array] TJ or
# <hex> Tj. What a drawing shows, in the order it shows it, whatever the
# coordinates around it are doing.
_TEXT_ITEM = re.compile(
    rb"\((?:[^()\\]|\\.)*\)\s*Tj|\[(?:[^\]\\]|\\.)*\]\s*TJ|<[0-9A-Fa-f]+>\s*Tj")

# A band redrawn rather than extended must still share this much of what the
# earlier drawing showed to count as the same band — see `band_drawings`.
_REDRAW_MIN_ITEMS = 40


def _text_items(drawing) -> list[bytes]:
    try:
        return _TEXT_ITEM.findall(drawing.get_data())
    except Exception:       # noqa: BLE001 - a drawing we cannot read is not a band
        return []


def _shared_start(a: list[bytes], b: list[bytes]) -> int:
    """How many leading items two drawings have in common."""
    n = min(len(a), len(b))
    if a[:n] == b[:n]:
        return n
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _page_has_own_text(page) -> bool:
    """Whether a page shows text itself, rather than only through drawings."""
    try:
        contents = page.get("/Contents")
        contents = contents.get_object() if contents is not None else None
        if contents is None:
            return False
        parts = contents if isinstance(contents, list) else [contents]
        return any(_TEXT_ITEM.search(p.get_object().get_data()) for p in parts)
    except Exception:       # noqa: BLE001 - unreadable: assume it might
        return True


def band_drawings(reader) -> list[tuple[int, object]] | None:
    """(page number, drawing) holding each band of the form in full, in order.

    IAPD's PDFs put nothing on a page directly. Each page draws one or two
    reusable drawings (form XObjects): its band, from the top of the band down
    to this page, and — where the page crosses into the next band — the first
    sliver of that one too. So a band's drawings form a chain, each showing
    everything the one before showed and more, and the last link is the whole
    band. This walks the pages, sorts every drawing into its chain, and
    returns the last link of each.

    A drawing joins a chain seen on the previous page when it repeats
    everything that chain has shown so far. One case needs more care: a table
    that runs off the bottom of a page is drawn part-way, and drawn in a
    different order once the next page shows more of it, so the next drawing
    replaces the chain's tail instead of extending it. That is the same band
    redrawn — it shares the chain's long opening — and is taken as such, which
    is what stops the half-drawn version being read as a band of its own.

    None means the PDF is not built this way: an ordinary document, where each
    page holds its own text and reading every page is correct.
    """
    chains: list[dict] = []
    pages = list(reader.pages)
    for number, page in enumerate(pages):
        if _page_has_own_text(page):
            return None
        try:
            xobjects = (page.get("/Resources") or {}).get("/XObject") or {}
            drawings = [ref.get_object() for ref in xobjects.values()]
        except Exception:       # noqa: BLE001
            return None
        for drawing in drawings:
            items = _text_items(drawing)
            if not items:
                continue            # an image, or a drawing with no text
            best, shared = None, 0
            for chain in chains:
                if chain["page"] < number - 1:
                    continue        # a band is on consecutive pages or it is over
                n = _shared_start(chain["items"], items)
                extends = n == len(chain["items"])
                redrawn = n >= max(_REDRAW_MIN_ITEMS, len(chain["items"]) // 2)
                if (extends or redrawn) and n >= shared:
                    best, shared = chain, n
            if best is None:
                chains.append({"page": number, "items": items, "drawing": drawing,
                               "most": len(items), "best_page": number})
                continue
            best["page"], best["items"] = number, items
            if len(items) >= best["most"]:
                best.update(most=len(items), drawing=drawing, best_page=number)

    if not chains:
        return None
    # A long form is a few bands of many pages each. Nearly as many chains as
    # pages means every page stood alone — an ordinary PDF with drawings in it.
    if len(pages) > 3 and len(chains) > max(3, len(pages) // 4):
        return None
    return [(c["best_page"], c["drawing"]) for c in chains]


def form_text(reader) -> str:
    """The form's text exactly once, however the PDF repeats it."""
    drawings = band_drawings(reader)
    if drawings is None:
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return "\n".join((reader.pages[number].extract_xform_text(drawing) or "")
                     for number, drawing in drawings)


def read_form(pdf: bytes) -> FormReading:
    """The funds and owners on a Form ADV.

    There is no slower fallback that reads every page, and that is deliberate:
    on these PDFs it is the reading that goes wrong (see the module docstring).
    A form that does not check out — fewer funds than it declares, ownership
    schedules not found — is reported as not read, which costs a blank section
    on a page. Reading it the other way costs a wrong number in an alert.
    """
    from pypdf import PdfReader

    return parse_form(form_text(PdfReader(io.BytesIO(pdf))))


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

    def employers_of(self, person: str) -> list[dict]:
        """The investment firms a person is registered with, as [{crd, name}] —
        but only when the SEC's database holds exactly one person of that
        first and last name.

        A fund's Form D names its officers and not the firm they work for,
        and this is the one public record that joins the two. The search
        behind it is loose: "Christopher DeLap" brings back five people named
        Delaney. So anything but the same first and last name is ignored, and
        two people of one name are treated as nobody, because which of them
        is meant cannot be told from a name. Nothing about the person is kept;
        the firm is only ever a place to look, never an answer in itself.
        """
        wanted = re.findall(r"[a-z]+", (person or "").casefold())
        if len(wanted) < 2:
            return []
        first, last = wanted[0], wanted[-1]
        resp = self.http.get(PEOPLE_API, params={
            "query": person.strip(), "hl": "true", "nrows": 10, "start": 0,
            "r": 25, "sort": "score+desc", "wt": "json"}, use_cache=False)

        def bare(text) -> str:
            return re.sub(r"[^a-z]", "", str(text or "").casefold())

        same_name = [hit.get("_source", {}) for hit in
                     (((resp.get("json") or {}).get("hits") or {}).get("hits") or [])
                     if bare(hit.get("_source", {}).get("ind_firstname")) == first
                     and bare(hit.get("_source", {}).get("ind_lastname")) == last]
        if len(same_name) != 1:
            return []
        firms: dict[str, dict] = {}
        for job in same_name[0].get("ind_ia_current_employments") or []:
            crd = str(job.get("firm_id") or "").strip()
            if crd.isdigit() and job.get("firm_name"):
                firms.setdefault(crd, {"crd": crd, "name": job["firm_name"]})
        return list(firms.values())

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

        # The same filing, already read by this version of the reader: nothing
        # to download. A reading made before the reader learned a section is
        # read once more, so that section is not blank until the firm refiles.
        unchanged = (previous is not None and previous.funds_read
                     and previous.filing_date == snap.filing_date
                     and previous.reader_version >= READER_VERSION)
        if unchanged:
            snap.funds = previous.funds
            snap.funds_declared = previous.funds_declared
            snap.filing_kind = previous.filing_kind
            snap.funds_read = True
            snap.owners, snap.owners_read = previous.owners, previous.owners_read
            snap.reader_version = previous.reader_version
            return snap

        def keep_last_reading() -> AdviserSnapshot:
            """The form could not be read tonight: hold on to the last good one.

            Storing a blank reading would make the next successful one a fresh
            baseline, and whatever changed in between would never be reported.
            Keeping the old filing date as well means tomorrow still sees a
            new filing, reads it, and compares it with what was kept.
            """
            if previous is not None and previous.funds_read:
                snap.funds, snap.funds_declared = previous.funds, previous.funds_declared
                snap.filing_kind, snap.funds_read = previous.filing_kind, True
                snap.owners, snap.owners_read = previous.owners, previous.owners_read
                snap.reader_version = previous.reader_version
                snap.filing_date = previous.filing_date
            return snap

        status, pdf = self.http.get_bytes(PDF_URL.format(crd=crd))
        if status != 200 or not pdf.startswith(b"%PDF"):
            log.warning("Form ADV for %s not readable (status %s)", crd, status)
            return keep_last_reading()
        try:
            form = read_form(pdf)
        except Exception as exc:        # noqa: BLE001 - a bad PDF must not sink a run
            log.warning("Form ADV for %s did not parse: %s", crd, exc)
            return keep_last_reading()
        snap.funds, snap.funds_declared = form.funds, form.funds_declared
        snap.filing_kind = form.filing_kind
        snap.funds_read = form.funds_read
        snap.owners, snap.owners_read = form.owners, form.owners_read
        snap.reader_version = READER_VERSION
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
    "owner": ("Schedule A of Form ADV lists everyone who directly owns 5% or more of the "
              "investment firm, plus its top executives. The form marks each owner as a "
              "person, a U.S. company, or a company based outside the United States; it "
              "does not say which country."),
    "indirect": ("Schedule B lists who owns the firm's owners — the next level up the "
                 "chain, and the one above that — for anyone holding 25% or more. The "
                 "form marks each as a person, a U.S. company, or a company based "
                 "outside the United States; it does not say which country."),
    "control": ("On this form, \"control\" means the power to direct the firm's "
                "management or policies. The form presumes it for anyone owning 25% or "
                "more, and for most top executives."),
}

# A reported value moves with the market every year; below this it is noise.
SIZE_CHANGE_FRACTION = 0.25


def _describe(o: Owner) -> str:
    """One owner in a sentence or two, for someone who has never seen the form.

    "SIPCO LTD is a company based outside the United States. It owns 75% or
    more of CP HOLDINGS LTD (listed as shareholder since 11/2024) and is
    reported as having control."
    """
    of = o.through if o.indirect and o.through else "the firm"
    if o.code == "F":
        holds = f"is a general partner, trustee or elected manager of {of}"
    elif o.code == "NA" and not o.indirect:
        holds = "owns less than 5% of the firm, or none"
    elif o.share:
        holds = f"owns {o.share} of {of}"
    else:
        holds = f"has an interest in {of}"
    listed = ""
    if o.title:
        listed = f" (listed as {o.title.lower()}" + (f" since {o.since})" if o.since else ")")
    control = ("is reported as having control" if o.control
               else "is not reported as having control")
    who = f"{o.display} is {OWNER_KIND[o.kind]}. It" if o.kind in ("DE", "FE") else o.display
    return f"{who} {holds}{listed} and {control}."


def _owner_importance(o: Owner) -> int:
    if o.foreign:
        return 6
    if o.kind == "I" and o.code == "NA":
        return 2                    # an executive with no real stake
    if _RANK.get(o.code, 0) >= _RANK["C"] or o.code == "F":
        return 5
    return 4


def _compare_owners(old: AdviserSnapshot, new: AdviserSnapshot, firm: str, add) -> None:
    before = {o.key: o for o in old.owners}
    after = {o.key: o for o in new.owners}

    # A row whose cells wrap can straddle the edge of a band, and its second
    # line then lands in front of the next row's name: "OFFICER SMITH, JOHN".
    # Where the edge falls moves from filing to filing, so without this the
    # same person would be reported as gone under one name and new under the
    # other. A name that is another name with words in front is that owner.
    for new_key in sorted(set(after) - set(before)):
        for old_key in sorted(set(before) - set(after)):
            a, b = _norm(before[old_key].name), _norm(after[new_key].name)
            if before[old_key].indirect == after[new_key].indirect \
                    and (a.endswith(" " + b) or b.endswith(" " + a)):
                after[old_key] = after.pop(new_key)
                break

    def where(o: Owner) -> str:
        return SCHEDULE_B if o.indirect else SCHEDULE_A

    def explain(o: Owner) -> str:
        return EXPLAIN["indirect"] if o.indirect else EXPLAIN["owner"]

    arrived = [after[k] for k in sorted(set(after) - set(before))]
    # Executives with no stake arrive in batches when a firm reorganises its
    # management; one line each would bury the owner that matters.
    officers = [o for o in arrived if _owner_importance(o) == 2]
    for o in (o for o in arrived if _owner_importance(o) != 2):
        role = "an owner behind its owners" if o.indirect else "a direct owner"
        kind = f", {OWNER_KIND[o.kind]}" if o.kind in ("DE", "FE") else ""
        add("new_owner", f"{firm} added {role}: {o.display}{kind}.",
            _describe(o), explain(o), where(o), _owner_importance(o))
    if len(officers) == 1:
        o = officers[0]
        add("new_officer", f"{firm} added {o.display} to its list of executives.",
            f"Listed as {o.title.lower() or 'an executive'}"
            + (f" since {o.since}." if o.since else "."), EXPLAIN["owner"], SCHEDULE_A, 2)
    elif officers:
        add("new_officer", f"{firm} added {len(officers)} people to its list of executives.",
            "; ".join(f"{o.display} ({o.title.lower() or 'executive'})" for o in officers)
            + ".", EXPLAIN["owner"], SCHEDULE_A, 2)

    for key in sorted(set(before) - set(after)):
        o = before[key]
        what = "owners behind its owners" if o.indirect else "owners and executives"
        add("owner_gone", f"{o.display} is no longer listed among {firm}'s {what}.",
            f"On the previous filing: {_describe(o)}", explain(o), where(o),
            4 if o.foreign or _RANK.get(o.code, 0) >= _RANK["C"] else 2)

    for key in sorted(set(before) & set(after)):
        a, b = before[key], after[key]
        of = b.through if b.indirect and b.through else firm
        if a.kind != b.kind and a.kind and b.kind:
            add("owner_kind", f"{b.display} is now reported as "
                              f"{OWNER_KIND.get(b.kind, b.kind)} (previously "
                              f"{OWNER_KIND.get(a.kind, a.kind)}).",
                _describe(b), explain(b), where(b), 6 if b.foreign else 4)
        if a.code != b.code and a.share and b.share:
            ranked = a.code in _RANK and b.code in _RANK
            rose = ranked and _RANK[b.code] > _RANK[a.code]
            add("owner_share", f"{b.display}'s share of {of} went from {a.share} to "
                               f"{b.share}.",
                _describe(b), explain(b), where(b),
                (6 if b.foreign else 5) if rose else 3)
        if a.control != b.control:
            add("owner_control", f"{b.display} is now reported as having control of {of}."
                if b.control else
                f"{b.display} is no longer reported as having control of {of}.",
                _describe(b), EXPLAIN["control"], where(b),
                (6 if b.foreign else 5) if b.control else 3)


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

    # A reading made by an older version of this reader is not compared field
    # by field with a newer one: the differences may be the reader's, not the
    # firm's. The older reader could give a fund another fund's figures, and
    # correcting that must not go out as "foreign ownership rose from 0% to
    # 100%". The filing itself is still announced, and says why it is bare.
    reader_changed = old.reader_version != new.reader_version

    if new.filing_date and new.filing_date != old.filing_date:
        kind = f" ({new.filing_kind.split(' - ')[0].lower()})" if new.filing_kind else ""
        add("new_filing", f"{firm} filed an updated Form ADV{kind} on {new.filing_date}.",
            f"Previous filing: {old.filing_date or 'not recorded'}."
            + (" This tool's reading of the form was improved since the last check, so "
               "fund-by-fund and owner-by-owner differences are not listed for this one "
               "filing; open the form to compare." if reader_changed else ""),
            EXPLAIN["filing"], "The whole form", 1)

    for name in sorted(set(new.related_firms) - set(old.related_firms)):
        add("new_related_firm", f"{firm} added a related firm: {name}.", "",
            EXPLAIN["related"], "Schedule R and Section 1.B of Schedule D", 3)

    if new.office_country and old.office_country \
            and new.office_country != old.office_country:
        add("office_moved", f"{firm} moved its main office from {old.office_country} "
                            f"to {new.office_country}.",
            f"Now listed as {new.office}.", EXPLAIN["office"], "Item 1.F", 4)

    if reader_changed:
        return sorted(out, key=lambda c: -c.importance)

    # Owners, like funds, are compared only when both readings have them.
    if old.owners_read and new.owners_read:
        _compare_owners(old, new, firm, add)

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
