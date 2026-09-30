"""Form D: a company telling the SEC it has raised money privately, watched for new filings.

A company that sells shares (or loans, options, or other stakes) to private
investors must file a short notice with the SEC — **Form D** — within 15 days
of the first sale. It is the only public record most private defence start-ups
ever make about their money: how much they set out to raise, how much they
have raised, how many investors bought in, who runs the company, and whether
the money came with a merger or acquisition. It does not name the investors.

Form ADV (see `adv.py`) is filed by investment *firms*. Form D is filed by the
*company*, so it is the direct answer to "has this contractor raised money?".

Where it comes from, and why this way:

  * A company's list of filings is a JSON record at data.sec.gov. It is cheap,
    so a nightly check costs one request per company.
  * Each filing's content is an XML document in the EDGAR archive. A filing
    never changes once made, so each is read once and kept.
  * Finding a private company's SEC number (its CIK) uses EDGAR's own
    company-name lookup. It returns investment vehicles and people alongside
    companies — "HII Shield AI-02, a Series of HII Shield AI-A LLC" is a pool
    of money that bought Shield AI shares, not Shield AI — so the choice is
    always a person's, never made here. A wrong pick would put another
    entity's fundraising under the contractor's name.

Everything said to a reader follows the rule in `adv.py`: what changed, one
sentence on what that part of the form is, where to look. No verdict.
"""
from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

from .adv import money

log = logging.getLogger("foci.formd")

TYPEAHEAD_API = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_API = "https://data.sec.gov/submissions/CIK{cik10}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"
# The same document rendered as the form a person would recognise.
READABLE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/xslFormDX01/primary_doc.xml"
COMPANY_URL = "https://www.sec.gov/edgar/browse/?CIK={cik}"

FORMS = ("D", "D/A")
# Filings read in full on a company's first check. Older ones are counted, not
# read: they are history, and the page only needs the recent picture.
MAX_FILINGS = 25

# EDGAR's codes for places in the United States — the fifty states, D.C., the
# territories, and X1 ("UNITED STATES"). Every other code is a province or a
# country. XX ("UNKNOWN") is neither, and is not called foreign.
US_CODES = frozenset("""
    AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS
    MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI
    WY X1 PR GU VI 1V 2J B5
""".split())


def cik10(cik: str | int) -> str:
    return str(int(str(cik).strip())).zfill(10)


def is_outside_us(code: str) -> bool:
    code = (code or "").strip().upper()
    return bool(code) and code != "XX" and code not in US_CODES


# ----------------------------------------------------------------- the record

@dataclass
class RelatedPerson:
    name: str
    roles: list[str] = field(default_factory=list)   # Director, Executive Officer, Promoter
    title: str = ""            # "Chief Executive Officer", when given
    place: str = ""            # "Austin, TEXAS"
    outside_us: bool = False

    def key(self) -> str:
        """Who this is, ignoring middle names — filings are not consistent."""
        parts = self.name.casefold().split()
        return f"{parts[0]} {parts[-1]}" if len(parts) > 1 else self.name.casefold()


@dataclass
class SalesRecipient:
    """Someone paid to find investors: a broker or a finder (Item 12)."""
    name: str
    crd: str = ""
    place: str = ""
    outside_us: bool = False


@dataclass
class FormDFiling:
    accession: str
    form: str                   # "D" or "D/A"
    filed: str                  # YYYY-MM-DD
    amends: str = ""            # the accession number an amendment replaces
    issuer: str = ""
    incorporated_in: str = ""   # "DELAWARE"
    entity_type: str = ""
    year_of_inc: str = ""
    place: str = ""             # "Austin, TEXAS"
    outside_us: bool = False
    industry: str = ""
    securities: list[str] = field(default_factory=list)
    first_sale: str = ""        # a date, or "not yet"
    offering_amount: int | None = None
    offering_indefinite: bool = False
    amount_sold: int | None = None
    remaining: int | None = None
    investors: int | None = None
    non_accredited: int | None = None
    business_combination: bool = False
    related_persons: list[RelatedPerson] = field(default_factory=list)
    sales_recipients: list[SalesRecipient] = field(default_factory=list)
    signed_by: str = ""
    read_ok: bool = True

    @property
    def is_amendment(self) -> bool:
        return self.form == "D/A" or bool(self.amends)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> FormDFiling:
        raw = dict(raw or {})
        persons = [RelatedPerson(**p) for p in raw.pop("related_persons", []) or []]
        recipients = [SalesRecipient(**r) for r in raw.pop("sales_recipients", []) or []]
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known, related_persons=persons, sales_recipients=recipients)


@dataclass
class IssuerSnapshot:
    cik: str
    name: str = ""
    place: str = ""
    incorporated_in: str = ""
    filings: list[FormDFiling] = field(default_factory=list)   # newest first
    total_filings: int = 0      # every Form D and D/A listed, read or not

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> IssuerSnapshot:
        raw = dict(raw or {})
        filings = [FormDFiling.from_dict(f) for f in raw.pop("filings", []) or []]
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known, filings=filings)

    def filing(self, accession: str) -> FormDFiling | None:
        return next((f for f in self.filings if f.accession == accession), None)


# ------------------------------------------------------------------- reading

SECURITY_NAMES = {
    "isEquityType": "shares (equity)",
    "isDebtType": "loans or notes (debt)",
    "isOptionToAcquireType": "options or warrants",
    "isSecurityToBeAcquiredType": "securities issued when options are used",
    "isPooledInvestmentFundType": "stakes in an investment fund",
    "isTenantInCommonType": "shared ownership of property",
    "isMineralPropertyType": "mineral rights",
    "isBusinessCombinationTransaction": "",
}


def _strip_namespaces(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _int(value: str) -> int | None:
    try:
        return int(float(value.replace(",", "")))
    except (AttributeError, ValueError):
        return None


def _true(value: str) -> bool:
    return value.strip().lower() in ("true", "y", "yes", "1")


def _place(address: ET.Element | None) -> tuple[str, bool]:
    if address is None:
        return "", False
    code = _text(address, "stateOrCountry")
    where = _text(address, "stateOrCountryDescription") or code
    city = _text(address, "city").title()
    return ", ".join(x for x in (city, where) if x), is_outside_us(code)


def parse_form_d(xml: str, *, accession: str = "", form: str = "",
                 filed: str = "") -> FormDFiling:
    """One Form D document, as plain facts."""
    filing = FormDFiling(accession=accession, form=form, filed=filed)
    try:
        root = _strip_namespaces(ET.fromstring(xml.encode("utf-8") if isinstance(xml, str)
                                               else xml))
    except (ET.ParseError, ValueError) as exc:
        log.warning("Form D %s did not parse: %s", accession, exc)
        filing.read_ok = False
        return filing

    filing.form = form or _text(root, "submissionType")
    issuer = root.find("primaryIssuer")
    filing.issuer = _text(issuer, "entityName")
    filing.incorporated_in = _text(issuer, "jurisdictionOfInc")
    filing.entity_type = _text(issuer, "entityType")
    filing.year_of_inc = _text(issuer, "yearOfInc/value") or (
        "more than five years ago" if _true(_text(issuer, "yearOfInc/overFiveYears"))
        else "")
    filing.place, filing.outside_us = _place(
        issuer.find("issuerAddress") if issuer is not None else None)

    for info in root.findall("relatedPersonsList/relatedPersonInfo"):
        name = " ".join(x for x in (_text(info, "relatedPersonName/firstName"),
                                    _text(info, "relatedPersonName/middleName"),
                                    _text(info, "relatedPersonName/lastName")) if x)
        if not name:
            continue
        place, outside = _place(info.find("relatedPersonAddress"))
        filing.related_persons.append(RelatedPerson(
            name=name,
            roles=[(r.text or "").strip() for r in
                   info.findall("relatedPersonRelationshipList/relationship") if r.text],
            title=_text(info, "relationshipClarification"),
            place=place, outside_us=outside))

    offering = root.find("offeringData")
    if offering is None:
        return filing

    filing.industry = _text(offering, "industryGroup/industryGroupType")
    filing.amends = _text(offering, "typeOfFiling/newOrAmendment/previousAccessionNumber")
    first = offering.find("typeOfFiling/dateOfFirstSale")
    if first is not None:
        filing.first_sale = (_text(first, "value")
                             or ("not yet" if _true(_text(first, "yetToOccur")) else ""))

    kinds = offering.find("typesOfSecuritiesOffered")
    if kinds is not None:
        for child in kinds:
            if _true(child.text or "") and SECURITY_NAMES.get(child.tag):
                filing.securities.append(SECURITY_NAMES[child.tag])
        other = _text(kinds, "descriptionOfOtherType")
        if other:
            filing.securities.append(other)

    filing.business_combination = _true(_text(
        offering, "businessCombinationTransaction/isBusinessCombinationTransaction"))

    amounts = offering.find("offeringSalesAmounts")
    offered = _text(amounts, "totalOfferingAmount")
    filing.offering_indefinite = offered.lower() == "indefinite"
    filing.offering_amount = _int(offered)
    filing.amount_sold = _int(_text(amounts, "totalAmountSold"))
    filing.remaining = _int(_text(amounts, "totalRemaining"))

    investors = offering.find("investors")
    filing.investors = _int(_text(investors, "totalNumberAlreadyInvested"))
    if _true(_text(investors, "hasNonAccreditedInvestors")):
        filing.non_accredited = _int(_text(investors, "numberNonAccreditedInvestors"))

    for recipient in offering.findall("salesCompensationList/recipient"):
        name = _text(recipient, "recipientName")
        if not name or name.lower() == "none":
            continue
        place, outside = _place(recipient.find("recipientAddress"))
        crd = _text(recipient, "recipientCRDNumber")
        filing.sales_recipients.append(SalesRecipient(
            name=name, crd="" if crd.lower() == "none" else crd,
            place=place, outside_us=outside))

    signature = offering.find("signatureBlock/signature")
    if signature is not None:
        who, title = _text(signature, "nameOfSigner"), _text(signature, "signatureTitle")
        filing.signed_by = f"{who}, {title}" if who and title else who
    return filing


# Names in EDGAR's company list that are not operating companies. People are
# listed surname first, in capitals, with no corporate ending; vehicles that
# pool money to buy one company's shares carry tell-tale words. Neither guess
# decides anything — they are labels to help a person choose.
_VEHICLE = re.compile(
    r"\b(a series of|series [ivx\d]+|spv|fund|funds|co-?invest\w*|investors?|feeder|"
    r"vehicle|opportunit\w+|syndicate|l\.?p\.?|holdings? [ivx]+)\b", re.I)
_CORPORATE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|llc|l\.l\.c|ltd|limited|plc|"
    r"lp|l\.p|llp|gmbh|ag|sa|nv|bv|pbc|trust|group|holdings?|technologies|systems|"
    r"industries|labs?)\b\.?", re.I)


def label_entity(name: str, query: str = "") -> str:
    """"company", "vehicle" or "person" — a hint, shown as one."""
    words = name.replace(",", " ").split()
    if _VEHICLE.search(name):
        return "vehicle"
    # "MW LSVC Shield AI, LLC": the searched name sits behind a prefix and
    # ends in LLC/LP — the pattern of a vehicle named after its target.
    q = query.strip().casefold()
    if q and q in name.casefold() and not name.casefold().startswith(q) \
            and re.search(r"\b(llc|lp|l\.p\.)\W*$", name, re.I):
        return "vehicle"
    if name.isupper() and not _CORPORATE.search(name) and 2 <= len(words) <= 4 \
            and not any(ch.isdigit() for ch in name):
        return "person"
    return "company"


class FormDConnector:
    """Finds a company on EDGAR and reads its Form D filings."""

    name = "formd"

    def __init__(self, http) -> None:
        self.http = http

    # ---------------------------------------------------------- finding one
    def search(self, query: str, limit: int = 10, describe: int = 4) -> list[dict]:
        """Entities on EDGAR by name, labelled, for a person to choose from.

        The first few that look like companies are looked up once more for
        where they are and how many Form D notices they have filed — the two
        facts that usually settle which one is meant.
        """
        query = (query or "").strip()
        if len(query) < 2:
            return []
        resp = self.http.get(TYPEAHEAD_API, params={"keysTyped": query}, use_cache=False)
        hits = ((resp.get("json") or {}).get("hits") or {}).get("hits") or []
        out = []
        for hit in hits[:limit]:
            name = (hit.get("_source") or {}).get("entity") or ""
            cik = str(hit.get("_id") or "").strip()
            if not name or not cik.isdigit():
                continue
            out.append({"cik": cik, "name": name, "label": label_entity(name, query)})

        order = {"company": 0, "vehicle": 1, "person": 2}
        out.sort(key=lambda r: order[r["label"]])
        for row in [r for r in out if r["label"] == "company"][:describe]:
            try:
                sub = self._submissions(row["cik"])
            except Exception:       # noqa: BLE001 - a description is optional
                continue
            row.update(self._describe(sub))
        return out

    def _submissions(self, cik: str) -> dict:
        resp = self.http.get(SUBMISSIONS_API.format(cik10=cik10(cik)), use_cache=False)
        if resp.get("status") != 200:
            raise FormDUnavailable(f"EDGAR did not return company {cik} "
                                   f"(status {resp.get('status') or 'no response'}).")
        return resp.get("json") or {}

    @staticmethod
    def _describe(sub: dict) -> dict:
        business = ((sub.get("addresses") or {}).get("business") or {})
        city = (business.get("city") or "").title()
        where = (business.get("stateOrCountryDescription")
                 or business.get("stateOrCountry") or "")
        recent = ((sub.get("filings") or {}).get("recent") or {})
        return {
            "place": ", ".join(x for x in (city, where) if x),
            "incorporated_in": sub.get("stateOfIncorporation") or "",
            "form_d_count": sum(1 for f in recent.get("form", []) if f in FORMS),
            "tickers": sub.get("tickers") or [],
        }

    # ------------------------------------------------------------ reading
    def filing_list(self, cik: str) -> tuple[dict, list[tuple[str, str, str]]]:
        """(the company record, [(form, filed, accession), ...] newest first)."""
        sub = self._submissions(cik)
        recent = ((sub.get("filings") or {}).get("recent") or {})
        # EDGAR's columns are parallel lists; strict=False so a short one
        # truncates rather than sinking the whole company.
        rows = [(form, filed, acc) for form, filed, acc in zip(
            recent.get("form", []), recent.get("filingDate", []),
            recent.get("accessionNumber", []), strict=False) if form in FORMS]
        return sub, rows

    def read_filing(self, cik: str, form: str, filed: str, accession: str) -> FormDFiling:
        url = DOC_URL.format(cik=int(cik), acc=accession.replace("-", ""))
        resp = self.http.get(url)       # a filing never changes: caching is right
        text = resp.get("text") or ""
        if resp.get("status") != 200 or "<edgarSubmission" not in text:
            log.warning("Form D %s for %s not readable (status %s)",
                        accession, cik, resp.get("status"))
            return FormDFiling(accession=accession, form=form, filed=filed, read_ok=False)
        return parse_form_d(text, accession=accession, form=form, filed=filed)

    def refresh(self, cik: str, previous: IssuerSnapshot | None = None) -> IssuerSnapshot:
        """The company's Form D filings now. Only filings not read before are fetched."""
        cik = str(int(str(cik).strip()))
        sub, rows = self.filing_list(cik)
        info = self._describe(sub)
        known = {f.accession: f for f in (previous.filings if previous else [])}
        filings = []
        for form, filed, accession in rows[:MAX_FILINGS]:
            kept = known.get(accession)
            filings.append(kept if kept and kept.read_ok
                           else self.read_filing(cik, form, filed, accession))
        return IssuerSnapshot(cik=cik, name=sub.get("name") or "", place=info["place"],
                              incorporated_in=info["incorporated_in"], filings=filings,
                              total_filings=len(rows))


class FormDUnavailable(RuntimeError):
    """EDGAR could not be read for this company. Said plainly, not raised as a bug."""


# ------------------------------------------------------------------- changes

@dataclass
class FormDChange:
    cik: str
    company: str
    kind: str
    headline: str
    detail: str
    explainer: str
    where: str
    importance: int
    accession: str = ""
    filed: str = ""

    @property
    def change_id(self) -> str:
        raw = f"formd|{self.cik}|{self.accession}|{self.kind}|{self.headline}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def links(self) -> dict:
        out = {"company": COMPANY_URL.format(cik=self.cik)}
        if self.accession:
            out["filing"] = READABLE_URL.format(cik=int(self.cik),
                                                acc=self.accession.replace("-", ""))
        return out

    def to_dict(self) -> dict:
        return {**asdict(self), "change_id": self.change_id, "links": self.links()}


EXPLAIN = {
    "raise": ("Form D is the short notice a company files with the SEC within 15 days of "
              "first selling shares, or other stakes, to private investors. It says how "
              "much the company is trying to raise, how much it has raised so far, and "
              "how many investors have put money in. It does not name the investors."),
    "amended": ("A company updates its Form D when the amount raised or other facts "
                "change, and at least once a year while the fundraising is still open."),
    "person": ("Form D lists the company's directors and top executives, and anyone paid "
               "to promote the fundraising. A name not on the company's earlier filings "
               "usually means a new board member or executive."),
    "merger": ("Form D asks whether the money is being raised as part of a merger, an "
               "acquisition or an exchange of shares. \"Yes\" means ownership of the "
               "company, or of another company, may be changing hands — not only new "
               "money coming in."),
    "place": "Where the company says its main office is.",
    "incorporated": "The state or country under whose laws the company is set up.",
    "unread": ("The notice is on EDGAR but its contents could not be read here. The "
               "link opens it."),
}

WHERE_AMOUNTS = "Form D, Item 13 (amounts) and Item 14 (investors)"
WHERE_PEOPLE = "Form D, Item 3 (related persons)"


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _role(p: RelatedPerson) -> str:
    return p.title or " and ".join(r.lower() for r in p.roles) or "a related person"


def _person(p: RelatedPerson) -> str:
    where = f", address in {p.place}" if p.outside_us and p.place else ""
    return f"{p.name} ({_role(p)}{where})"


def raise_facts(f: FormDFiling) -> str:
    """One or two sentences of numbers, for a reader with no finance background."""
    parts = []
    if f.offering_indefinite:
        parts.append("It does not set a total it is trying to raise")
    elif f.offering_amount is not None:
        parts.append(f"It is trying to raise {money(f.offering_amount)} in total")
    if f.first_sale == "not yet":
        parts.append("nothing had been sold yet")
    elif f.first_sale:
        parts.append(f"the first sale was on {f.first_sale}")
    text = "; ".join(parts) + "." if parts else ""
    if f.securities:
        text += f" What is being sold: {', '.join(f.securities)}."
    if f.non_accredited:
        text += (f" {_plural(f.non_accredited, 'investor')} did not meet the SEC's "
                 f"wealth test for private investments.")
    abroad = [r for r in f.sales_recipients if r.outside_us]
    if abroad:
        text += " Paid to find investors, outside the U.S.: " + "; ".join(
            f"{r.name} ({r.place})" for r in abroad) + "."
    return text.strip()


def compare(old: IssuerSnapshot | None, new: IssuerSnapshot) -> list[FormDChange]:
    """Filings in `new` that `old` did not have, in words. A first look reports nothing."""
    if old is None:
        return []
    company = new.name or old.name
    seen = {f.accession for f in old.filings}
    fresh = [f for f in new.filings if f.accession not in seen]
    if not fresh:
        return []

    out: list[FormDChange] = []
    # People already named on any earlier filing: a name is new once.
    known_people = {p.key() for f in old.filings for p in f.related_persons}
    had_filings = bool(old.filings)
    latest_before = old.filings[0] if old.filings else None

    def add(f, kind, headline, detail, explainer, where, importance):
        out.append(FormDChange(new.cik, company, kind, headline, detail.strip(),
                               explainer, where, importance, f.accession, f.filed))

    for f in sorted(fresh, key=lambda x: (x.filed, x.accession)):
        if not f.read_ok:
            add(f, "unread", f"{company} filed a Form D{'/A' if f.is_amendment else ''} "
                             f"on {f.filed}.", "", EXPLAIN["unread"], "The filing itself", 4)
            continue

        sold = money(f.amount_sold) if f.amount_sold is not None else "an unreported amount"
        investors = (f" from {_plural(f.investors, 'investor')}"
                     if f.investors is not None else "")
        abroad = [p for p in f.related_persons if p.outside_us]

        if not f.is_amendment:
            detail = raise_facts(f)
            if not had_filings and f.related_persons:
                detail += " Named on the filing: " + "; ".join(
                    _person(p) for p in f.related_persons) + "."
            headline = (f"{company} reported raising money privately: {sold}{investors} "
                        f"(Form D filed {f.filed})." if f.amount_sold else
                        f"{company} told the SEC on {f.filed} that it is raising money "
                        f"privately; it reported no sales yet.")
            add(f, "new_raise", headline, detail, EXPLAIN["raise"], WHERE_AMOUNTS,
                6 if abroad or f.outside_us else 5)
        else:
            prior = new.filing(f.amends) or old.filing(f.amends)
            if prior and prior.read_ok and prior.amount_sold is not None \
                    and f.amount_sold is not None and prior.amount_sold != f.amount_sold:
                change = "rose" if f.amount_sold > prior.amount_sold else "fell"
                headline = (f"{company} updated its Form D: money raised {change} from "
                            f"{money(prior.amount_sold)} to {sold}.")
                detail = (f"Investors: {prior.investors:,} before, {f.investors:,} now. "
                          if prior.investors is not None and f.investors is not None
                          else "") + raise_facts(f)
            else:
                headline = (f"{company} updated its Form D (filed {f.filed}): it now "
                            f"reports {sold} raised{investors}.")
                detail = raise_facts(f)
            add(f, "amended_raise", headline, detail, EXPLAIN["amended"], WHERE_AMOUNTS,
                4 if not abroad else 6)

        if f.business_combination:
            add(f, "merger", f"{company} says its Form D filed {f.filed} is connected to "
                             f"a merger, acquisition or exchange of shares.",
                "", EXPLAIN["merger"], "Form D, Item 10", 5)

        if had_filings:
            newcomers = list({p.key(): p for p in f.related_persons
                              if p.key() not in known_people}.values())
            known_people |= {p.key() for p in newcomers}
            # Someone abroad gets an item of their own; everyone else on the
            # same filing is one item, not one email line per board seat.
            for p in (p for p in newcomers if p.outside_us):
                add(f, "new_person", f"{company} names {p.name} on its Form D for the "
                                     f"first time — with an address in {p.place}.",
                    f"Listed as {_role(p)}.", EXPLAIN["person"], WHERE_PEOPLE, 6)
            local = [p for p in newcomers if not p.outside_us]
            if len(local) == 1:
                p = local[0]
                add(f, "new_person", f"{company} names {p.name} on its Form D for the "
                                     f"first time.",
                    f"Listed as {_role(p)}" + (f"; address in {p.place}." if p.place else "."),
                    EXPLAIN["person"], WHERE_PEOPLE, 3)
            elif local:
                add(f, "new_person", f"{company} names {len(local)} people on its Form D "
                                     f"for the first time.",
                    "; ".join(_person(p) for p in local) + ".",
                    EXPLAIN["person"], WHERE_PEOPLE, 3)
        else:
            known_people |= {p.key() for p in f.related_persons}

        before = latest_before
        if before and before.read_ok:
            if f.place and before.place and f.place != before.place:
                add(f, "place", f"{company} now gives its main address as {f.place} "
                                f"(previously {before.place}).",
                    "", EXPLAIN["place"], "Form D, Item 2",
                    6 if f.outside_us and not before.outside_us else 3)
            if f.incorporated_in and before.incorporated_in \
                    and f.incorporated_in != before.incorporated_in:
                add(f, "incorporated", f"{company} now says it is incorporated in "
                                       f"{f.incorporated_in} (previously "
                                       f"{before.incorporated_in}).",
                    "", EXPLAIN["incorporated"], "Form D, Item 1", 5)
        latest_before = f
        had_filings = True

    return sorted(out, key=lambda c: -c.importance)
