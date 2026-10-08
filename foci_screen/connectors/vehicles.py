"""Investment vehicles named after a company: money pooled to buy its shares.

A private company's shares change hands without the company filing anything.
What does get filed is the vehicle: a small entity — "HII Shield AI-05, a
Series of HII Shield AI-A LLC", "Moringa x Anduril LLC" — set up to collect
money from a group of investors and put it into one business. Each files its
own Form D, which says how much it raised, from how many investors, and who
runs it and where they are. Those vehicles are often the first public sign
that money is moving into a contractor, and the only public record of who is
organising it.

**The link is the name, and only the name.** A Form D does not say what the
vehicle invests in. A vehicle called "Anduril Fund" is very likely a fund for
buying Anduril shares and could, in principle, be named after the sword. So
everything here is presented as "named after", a person chooses the words to
match on, and any vehicle can be marked as unrelated.

Two EDGAR sources, because neither is enough alone (both checked against real
data before this was built):

  * **The daily index** lists every filing made on a day with the filer's
    name — about 350 KB. One request a day, whatever the number of companies
    watched, and it includes a vehicle's *first* filing. That is what catches
    a new vehicle.
  * **The company-name lookup** finds vehicles that filed before the daily
    index was being kept. It returns ten names at a time, so it is asked
    again with each next letter when the first answer is full, and what it
    finds is described as "at least" — it is a look-up, not a census.

EDGAR's full-text search is deliberately not used: it turned out to index
amended notices only, never a vehicle's first, so it misses exactly the event
this exists to report.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict
from datetime import date

from .formd import COMPANY_URL, READABLE_URL, TYPEAHEAD_API, FormDConnector, FormDFiling

log = logging.getLogger("foci.vehicles")

DAILY_DIR = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/"
FORMS = ("D", "D/A")
# The lookup answers with at most this many names.
LOOKUP_PAGE = 10
LOOKUP_NEXT = "abcdefghijklmnopqrstuvwxyz0123456789"

# Endings that say what kind of company it is, not which one.
_LEGAL = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "lp",
          "llp", "ltd", "limited", "plc", "pbc", "the"}
# Words too common to match on by themselves.
_GENERIC = {"systems", "technologies", "technology", "defense", "defence", "group",
            "capital", "holdings", "global", "american", "national", "united",
            "international", "aerospace", "solutions", "services", "industries",
            "partners", "ventures", "labs", "space", "federal", "government", "fund"}


def words(text: str) -> list[str]:
    """Lower-case letters-and-digits runs: "Shield.AI, LLC" -> shield, ai, llc."""
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def name_matches(phrase: str, name: str) -> bool:
    """Whether a filer's name contains the phrase as whole words, in order.

    "Shield AI" is in "CDT Shield.AI SPV LLC" and in "HII Shield AI-05"; it is
    not in "SHIELDS AIDAN H", which a plain substring test would accept.
    """
    want, have = words(phrase), words(name)
    if not want:
        return False
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


def bare_name(name: str) -> tuple[str, ...]:
    """A name's words without its legal ending: "Shield AI, Inc." -> shield, ai.

    A filer whose bare name is the contractor's is the contractor itself (or a
    namesake), not a fund named after it: "Shield AI Inc" raising money is the
    company's own fundraising, and adding it to what funds pooled would swamp
    the total.
    """
    return tuple(w for w in words(name) if w not in _LEGAL)


def default_phrase(company_name: str) -> str:
    """The words to suggest matching on, from a contractor's name.

    Funds are named after what a company is called, not its legal name:
    "Moringa x Anduril LLC", not "... Anduril Industries, Inc.". So the legal
    ending goes, a leading "The" goes, and so does a trailing word like
    "Industries" or "Technologies" — as long as something distinctive is left.
    Award records are in capitals; the suggestion is not, since it is repeated
    back in sentences.
    """
    parts = re.findall(r"[A-Za-z0-9][A-Za-z0-9.&'-]*", company_name or "")

    def bare(word: str) -> str:
        return re.sub(r"[^a-z0-9]", "", word.casefold())

    while parts and bare(parts[-1]) in _LEGAL:
        parts.pop()
    while len(parts) > 1 and bare(parts[0]) == "the":
        parts.pop(0)
    shorter = list(parts)
    while len(shorter) > 1 and bare(shorter[-1]) in _GENERIC:
        shorter.pop()
    if not phrase_problem(" ".join(shorter)):
        parts = shorter

    if all(p.upper() == p for p in parts):          # all capitals: make it readable
        parts = [p if len(bare(p)) <= 3 else p.capitalize() for p in parts]
    return " ".join(parts).strip(" ,.")


def phrase_problem(phrase: str) -> str:
    """Why a phrase cannot be matched on, or "" if it can."""
    tokens = words(phrase)
    if not tokens or sum(len(t) for t in tokens) < 4:
        return "Use at least four letters of the company's name."
    if all(t in _GENERIC or t in _LEGAL for t in tokens):
        return (f"\"{phrase.strip()}\" is too general on its own — hundreds of unrelated "
                f"filers share it. Add the distinctive part of the name.")
    return ""


def parse_master_index(text: str) -> list[dict]:
    """The Form D and D/A rows of one day's master index.

    After a short header, each line is five fields split by "|":
        2145910|HII Shield AI-05, a Series of HII Shield AI-A LLC|D|20260717|
        edgar/data/2145910/0002145910-26-000001.txt
    (one line in the file). Anything that is not five fields is skipped.
    """
    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or parts[2] not in FORMS or not parts[0].strip().isdigit():
            continue
        accession = parts[4].rsplit("/", 1)[-1].removesuffix(".txt").strip()
        day = parts[3].strip()
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or len(day) != 8:
            continue
        rows.append({"accession": accession, "cik": str(int(parts[0])),
                     "name": parts[1].strip(), "form": parts[2],
                     "filed": f"{day[:4]}-{day[4:6]}-{day[6:]}"})
    return rows


def _readable(name: str) -> str:
    """A company's name as a person would write it.

    The form has a first-name and a last-name box and nowhere for a company,
    and a fund is usually run by one. Filers put a dash or "N/A" in the spare
    box ("- Greenbird Management LLC"), or split the name across both, so that
    "LLC" then "Sydecar" reads back as "LLC Sydecar".

    Done here, for display, and not where the form is parsed: Form D changes
    are told apart by a person's name as first read, and tidying it there
    would announce people already on file as new.
    """
    parts = [p for p in name.split()
             if p.strip(" .-—_").casefold() not in ("", "n/a", "na", "none")]
    if len(parts) > 1 and re.sub(r"[^a-z]", "", parts[0].casefold()) in _LEGAL - {"the"}:
        parts = parts[1:] + parts[:1]
    return " ".join(parts) or name


def facts_of(filing: FormDFiling) -> dict:
    """What a vehicle's Form D says, kept small enough to store with its row."""
    people = [{"name": _readable(p.name), "role": p.title or ", ".join(p.roles).lower(),
               "place": p.place, "outside_us": p.outside_us}
              for p in filing.related_persons]
    return {
        "read_ok": filing.read_ok,
        "amount_sold": filing.amount_sold,
        "offering_amount": filing.offering_amount,
        "offering_indefinite": filing.offering_indefinite,
        "investors": filing.investors,
        "first_sale": filing.first_sale,
        "place": filing.place,
        "outside_us": filing.outside_us,
        "incorporated_in": filing.incorporated_in,
        "people": people,
        "recipients": [asdict(r) for r in filing.sales_recipients],
    }


def filing_url(cik: str, accession: str) -> str:
    return READABLE_URL.format(cik=int(cik), acc=accession.replace("-", ""))


def company_url(cik: str) -> str:
    return COMPANY_URL.format(cik=int(cik))


def _quarter(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


class VehicleConnector:
    """Reads EDGAR's daily index and name lookup; filings go through FormDConnector."""

    name = "vehicles"

    def __init__(self, http, formd: FormDConnector | None = None) -> None:
        self.http = http
        self.formd = formd or FormDConnector(http)

    # ---------------------------------------------------------- daily index
    def available_days(self, start: date, end: date) -> list[str]:
        """Days in [start, end] that have an index, as YYYYMMDD, oldest first.

        Asked of EDGAR rather than worked out from a calendar: there is no
        file for a weekend or a federal holiday, and guessing would mean a
        request that fails for each one.
        """
        if start > end:
            return []
        days: set[str] = set()
        (year, quarter), last = _quarter(start), _quarter(end)
        while (year, quarter) <= last:
            url = DAILY_DIR.format(year=year, quarter=quarter) + "index.json"
            resp = self.http.get(url, use_cache=False)
            items = (((resp.get("json") or {}).get("directory") or {}).get("item") or [])
            for item in items:
                m = re.fullmatch(r"master\.(\d{8})\.idx", item.get("name") or "")
                if m:
                    days.add(m.group(1))
            year, quarter = (year, quarter + 1) if quarter < 4 else (year + 1, 1)
        lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        return sorted(d for d in days if lo <= d <= hi)

    def day_filings(self, day: str) -> list[dict] | None:
        """Every Form D and D/A filed on one day, or None if it could not be read."""
        when = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        year, quarter = _quarter(when)
        url = DAILY_DIR.format(year=year, quarter=quarter) + f"master.{day}.idx"
        resp = self.http.get(url, use_cache=False)
        text = resp.get("text") or ""
        if resp.get("status") != 200 or "|" not in text:
            log.warning("EDGAR daily index for %s not readable (status %s)",
                        day, resp.get("status"))
            return None
        return parse_master_index(text)

    # ---------------------------------------------------------- name lookup
    def _ask(self, typed: str) -> list[dict]:
        resp = self.http.get(TYPEAHEAD_API, params={"keysTyped": typed}, use_cache=False)
        out = []
        for hit in (((resp.get("json") or {}).get("hits") or {}).get("hits") or []):
            name = (hit.get("_source") or {}).get("entity") or ""
            cik = str(hit.get("_id") or "").strip()
            if name and cik.isdigit():
                out.append({"cik": str(int(cik)), "name": name})
        return out

    def lookup(self, phrase: str) -> tuple[list[dict], bool]:
        """(entities whose name contains the phrase, whether there may be more).

        The lookup returns at most ten names. When the plain phrase fills
        that, it is asked again with each possible next letter, which brings
        back the names it had no room for — most of them, not provably all.
        """
        found: dict[str, dict] = {}

        def take(hits):
            for hit in hits:
                if name_matches(phrase, hit["name"]):
                    found.setdefault(hit["cik"], hit)

        first = self._ask(phrase)
        take(first)
        full = len(first) >= LOOKUP_PAGE
        if full:
            for nxt in LOOKUP_NEXT:
                take(self._ask(f"{phrase} {nxt}"))
        return sorted(found.values(), key=lambda e: -int(e["cik"])), full

    # ----------------------------------------------------------- one entity
    def filings_of(self, cik: str) -> list[dict]:
        """An entity's Form D and D/A filings, newest first, as index rows."""
        sub, rows = self.formd.filing_list(cik)
        name = sub.get("name") or ""
        return [{"accession": acc, "cik": str(int(cik)), "name": name, "form": form,
                 "filed": filed} for form, filed, acc in rows]

    def read(self, row: dict) -> dict:
        """What one filing says."""
        filing = self.formd.read_filing(row["cik"], row["form"], row["filed"],
                                        row["accession"])
        return facts_of(filing)
