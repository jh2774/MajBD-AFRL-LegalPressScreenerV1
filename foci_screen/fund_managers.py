"""Which investment firm runs a fund named after a contractor — in the firm's
own words.

A fund's Form D names its officers, not the investment firm behind it. The
firm says so itself. Form ADV, Schedule D, Section 7.B.(1) lists every private
fund a firm manages, by name, with its size, its investor count and — question
16 — the share owned by people outside the United States. So when a firm's
Form ADV lists a fund under exactly the name a Form D was filed under, that is
its manager, and the foreign share is the firm's own figure for that fund.

**The match is the whole name, not a likeness.** "MW LSVC Shield AI, LLC" on a
Form D and "MW LSVC SHIELD AI, LLC" on Manhattan West's Form ADV are one fund.
Deciding which firms to read is guesswork — a firm with a similar name, a firm
one of the fund's officers is registered with — and a guess is never shown as
a finding: a firm is named here only once its own filing lists the fund.

Many of these funds are one *series* of a larger company ("HII Shield AI-05,
a Series of HII Shield AI-A LLC"), and a firm may report the company without
each series. That is shown too, and said for what it is: the firm's figures
then cover every series together, not the one named after the contractor.

Form ADV is refiled once a year, so a fund set up since the firm's last filing
is not on it yet. Not being listed is not evidence of anything.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from .connectors.adv import AdvConnector
from .connectors.vehicles import bare_name, name_matches, words

log = logging.getLogger("foci.managers")

# Per press of the button: firm names searched, officers looked up, and firms
# handed back to have their Form ADV read. Reading one is a PDF of up to 40 MB,
# so the last of these is the one that costs.
MAX_FIRM_SEARCHES = 16
MAX_PEOPLE = 30
MAX_FIRMS = 12

_SERIES_OF = re.compile(
    r"\b(?:an?\s+)?(?:(?:individual|protected|separate|designated|registered)\s+"
    r"(?:(?:and|&)\s+)?)*series\s+of\s+(.+)$", re.I)
_THEN_SERIES = re.compile(r"^(.+?)[\s,-]+series\b", re.I)

# A related "person" on Form D with one of these in its name is a company.
_COMPANY = {"llc", "lp", "llp", "inc", "ltd", "limited", "corp", "corporation", "company",
            "gp", "management", "capital", "partners", "advisors", "advisers", "ventures",
            "group", "fund", "funds", "holdings", "trust", "associates", "investments",
            "admin", "services"}
# Words in a fund's name that say what it is, not whose it is.
_NOISE = {"a", "an", "of", "and", "the", "series", "protected", "individual", "separate",
          "spv", "fund", "funds", "co", "invest", "coinvest", "investment", "investments",
          "investors", "opportunity", "opportunities", "holdings", "llc", "lp", "llp",
          "inc", "ltd", "limited", "corp", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii",
          "ix", "x", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept",
          "oct", "nov", "dec"}
# Words in an investment firm's name that any firm might carry.
_FIRM_WORDS = {"management", "capital", "partners", "advisors", "advisers", "advisor",
               "adviser", "ventures", "asset", "assets", "group", "investment",
               "investments", "fund", "funds", "holdings", "wealth", "financial", "global",
               "associates", "services", "gp"}


def squash(name: str) -> str:
    """A name with only its letters and digits: how two filings of one fund's
    name are compared. "Shield.AI, L.P." and "SHIELD AI LP" are the same."""
    return "".join(words(name))


def parent_name(name: str) -> str:
    """The company a fund is one series of, or "" if it is not a series.

    "HII Shield AI-05, a Series of HII Shield AI-A LLC" -> "HII Shield AI-A LLC"
    "Greenbird Intelligence Fund, LLC, Series W - Shield AI" -> "Greenbird
    Intelligence Fund, LLC"
    """
    found = _SERIES_OF.search(name or "") or _THEN_SERIES.match(name or "")
    return found.group(1).strip(" ,.-") if found else ""


def _coarse(name: str) -> str:
    return max(words(name), key=len, default="")


def _filed(entry: dict) -> tuple[int, ...]:
    """A Form ADV date, MM/DD/YYYY, as something that sorts."""
    try:
        month, day, year = (int(x) for x in (entry.get("filing_date") or "").split("/"))
    except ValueError:
        return (0, 0, 0)
    return (year, month, day)


def _entry(crd: str, snapshot: dict, fund: dict) -> dict:
    """One line of a firm's Form ADV fund list, with whose list it is."""
    return {
        "crd": str(crd), "firm": snapshot.get("name") or f"CRD {crd}",
        "filing_date": snapshot.get("filing_date") or "",
        "fund_id": fund.get("fund_id") or "", "name": fund.get("name") or "",
        "non_us_pct": fund.get("owned_by_non_us_pct"),
        "gross_asset_value": fund.get("gross_asset_value"),
        "investors": fund.get("investors"),
        "country": fund.get("country") or "",
    }


# ------------------------------------------------- what is already on file

def reported(store, watch: dict, funds: list[dict],
             hidden: frozenset[str] | set[str] = frozenset()) -> dict:
    """Mark each fund with what a firm's Form ADV says about it.

    Each fund gains `reported` (firms listing a fund of exactly its name) and
    `reported_parent` (firms listing the company it is a series of, when no
    firm lists the fund itself). Returned: the firms involved, and funds named
    after the contractor that are on a Form ADV but not among `funds` — a firm
    need not sell a fund in a way that calls for a Form D.

    `hidden` is squashed names never to list on their own: funds a person has
    marked as unrelated.
    """
    phrase = watch["phrase"]
    parents = {f["cik"]: parent_name(f["name"]) for f in funds}
    look_for = [_coarse(phrase)] + [_coarse(p) for p in parents.values() if p]

    by_name: dict[str, list[dict]] = {}
    named_after: list[dict] = []
    for row in store.adv_snapshots_mentioning(look_for):
        for fund in row["snapshot"].get("funds") or []:
            if not fund.get("name"):
                continue
            entry = _entry(row["crd"], row["snapshot"], fund)
            by_name.setdefault(squash(entry["name"]), []).append(entry)
            if name_matches(phrase, entry["name"]):
                named_after.append(entry)
    for entries in by_name.values():
        entries.sort(key=_filed, reverse=True)

    firms: dict[str, dict] = {}

    def credit(entry: dict, column: str) -> None:
        firm = firms.setdefault(entry["crd"], {
            "crd": entry["crd"], "name": entry["firm"], "filing_date": entry["filing_date"],
            "funds": 0, "series": 0, "only": 0})
        firm[column] += 1

    accounted = set(hidden)             # names shown, or not to be shown, elsewhere
    for fund in funds:
        accounted.add(squash(fund["name"]))
        fund["reported"] = by_name.get(squash(fund["name"]), [])
        parent = parents[fund["cik"]]
        fund["reported_parent"] = ([] if fund["reported"] or not parent
                                   else by_name.get(squash(parent), []))
        if parent:
            accounted.add(squash(parent))
        for entry in fund["reported"]:
            credit(entry, "funds")
        for entry in fund["reported_parent"]:
            credit(entry, "series")

    only, listed = [], set()
    for entry in sorted(named_after, key=_filed, reverse=True):
        line = (squash(entry["name"]), entry["crd"])
        if line[0] in accounted or line in listed:
            continue
        listed.add(line)
        only.append(entry)
        credit(entry, "only")

    ranked = sorted(firms.values(),
                    key=lambda f: (-f["funds"], -f["series"], -f["only"], f["name"]))
    return {"managers": ranked, "reported_only": only}


# ------------------------------------------ the same link, seen from the firm

# The kinds of Form ADV change that are about one fund, whose name is in the
# headline (`connectors.adv.compare`).
FUND_CHANGES = {"new_fund", "fund_gone", "non_us_share", "investors", "size", "fund_country"}


def contractors_named_in(text: str, watches: list[dict]) -> list[dict]:
    """The contractors whose words — chosen by a person, on the contractor's
    page — appear in `text` as whole words, in order."""
    return [w for w in watches if w.get("phrase") and name_matches(w["phrase"], text)]


def funds_named_after(funds: list[dict], watches: list[dict]) -> dict[str, list[dict]]:
    """For a firm's page: fund id -> the contractors that fund is named after."""
    out: dict[str, list[dict]] = {}
    for fund in funds:
        named = contractors_named_in(fund.get("name") or "", watches)
        if named and fund.get("fund_id"):
            out[fund["fund_id"]] = [{"entity_key": w["entity_key"], "phrase": w["phrase"]}
                                    for w in named]
    return out


def tie_to_contractor(change: dict, watches: list[dict]) -> str:
    """One sentence for a Form ADV alert about a fund named after a contractor
    in the same portfolio, or "". The firm's own name is set aside first: a
    change at "Anduril Partners" is not thereby about a fund named after
    Anduril."""
    if change.get("kind") not in FUND_CHANGES:
        return ""
    headline = change.get("headline") or ""
    if change.get("firm"):
        headline = headline.replace(change["firm"], " ")
    named = contractors_named_in(headline, watches)
    if not named:
        return ""
    names = " and ".join(dict.fromkeys(w["phrase"] for w in named))
    return (f"This fund is named after {names}, which is in this portfolio. The link is "
            f"the name only.")


# --------------------------------------------------------- where to look

# A firm whose Form ADV could not be read is not tried again for this long.
RETRY_UNREADABLE_DAYS = 7


def worth_reading(stored: dict | None, now: datetime | None = None) -> bool:
    """Whether to go to the SEC for a firm, given what is on file for it.

    Almost always yes: a firm already read costs one small request to learn
    that nothing was filed since. The exception is a firm whose form could
    not be read — some run past the 40 MB this tool will download — where
    every further try would fetch all of it again to fail the same way.
    """
    if not stored or stored.get("funds_read"):
        return True
    try:
        tried = datetime.fromisoformat(str(stored.get("checked_at")).replace("Z", "+00:00"))
    except ValueError:
        return True
    if tried.tzinfo is None:
        tried = tried.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - tried > timedelta(days=RETRY_UNREADABLE_DAYS)


def _is_company(name: str) -> bool:
    return any(w in _COMPANY for w in words(name))


def _name_query(text: str, phrase: str) -> str:
    """The words of a fund's name that might be its sponsor's: what is left
    once the contractor, the boilerplate, the dates and the numbers are gone.
    "Fuel Venture Capital Shield AI, LLC" -> "fuel venture capital"."""
    skip = set(words(phrase)) | _NOISE
    kept = [w for w in words(text)
            if w not in skip and len(w) > 1 and not any(c.isdigit() for c in w)]
    query = kept[:3]
    return " ".join(query) if sum(len(w) for w in query) >= 5 else ""


def _related(firm: str, text: str, phrase: str) -> bool:
    """Whether a firm's name is in `text`, beyond the words every firm has.

    "FUEL VENTURE CAPITAL PARTNERS LLC" is in "Fuel Venture Capital Shield AI,
    LLC"; "EDGESTONE PARTNERS, INC." is not in "Edge Partners LLC". The
    contractor's own name does not count: a firm called "Shield Capital" is
    not thereby the manager of every fund named after Shield AI.
    """
    own = [w for w in bare_name(firm) if w not in _FIRM_WORDS]
    there = set(words(text)) - set(words(phrase))
    return sum(len(w) for w in own) >= 5 and all(w in there for w in own)


def search_terms(funds: list[dict], phrase: str) -> dict:
    """What to look up for the funds whose manager is not known yet:

      named   [(search for, the text it came from)] — companies named on a
              fund's filing, the best lead there is
      people  [name] — the funds' officers
      loose   [(search for, the fund's name)] — the sponsor's part of a
              fund's own name
    """
    named: dict[str, str] = {}
    loose: dict[str, str] = {}
    people: dict[str, None] = {}
    for fund in funds:
        if fund.get("reported") or fund.get("reported_parent"):
            continue
        facts = (fund.get("latest") or {}).get("facts") or {}
        for person in facts.get("people") or []:
            name = person.get("name") or ""
            if _is_company(name):
                query = " ".join(bare_name(name))
                if len(query) >= 5:
                    named.setdefault(query, name)
            elif len(re.findall(r"[A-Za-z]+", name)) >= 2:
                people.setdefault(name)
        parent = parent_name(fund["name"])
        head = _SERIES_OF.sub("", fund["name"]) if _SERIES_OF.search(fund["name"]) else (
            "" if parent else fund["name"])
        for text in (head, parent):
            query = _name_query(text, phrase)
            if query:
                loose.setdefault(query, fund["name"])
    first = list(named.items())[:MAX_FIRM_SEARCHES]
    # "fuel venture capital" adds nothing once "fuel venture capital partners",
    # named on the filing itself, is being looked up.
    rest = [(q, text) for q, text in loose.items()
            if not any(n == q or n.startswith(q + " ") for n in named)]
    return {"named": first, "people": list(people)[:MAX_PEOPLE],
            "loose": rest[:MAX_FIRM_SEARCHES - len(first)]}


def find_candidates(adv: AdvConnector, funds: list[dict], phrase: str) -> list[dict]:
    """Firms worth reading, as [{crd, name}]: places to look, not answers.

    Best lead first — a company named on a fund's filing, then a firm one of
    its officers is registered with, then a firm whose name is in the fund's —
    because only the first `MAX_FIRMS` are read.
    """
    terms = search_terms(funds, phrase)
    found: dict[str, dict] = {}
    asked = failed = 0

    def ask(call, *args):
        nonlocal asked, failed
        asked += 1
        try:
            return call(*args)
        except Exception as exc:        # noqa: BLE001 - one lookup must not stop the rest
            failed += 1
            log.warning("adviser database lookup failed: %s", exc)
            return []

    def by_firm_name(queries: list[tuple[str, str]]) -> None:
        for query, text in queries:
            for hit in ask(adv.search, query, 6):
                if (hit.get("status") or "").upper() == "ACTIVE" and hit.get("crd") \
                        and _related(hit["name"], text, phrase):
                    found.setdefault(hit["crd"], {"crd": hit["crd"], "name": hit["name"]})

    by_firm_name(terms["named"])
    for person in terms["people"]:
        for firm in ask(adv.employers_of, person):
            found.setdefault(firm["crd"], firm)
    by_firm_name(terms["loose"])
    if asked and failed == asked:
        raise RuntimeError("the adviser database did not answer")
    return list(found.values())[:MAX_FIRMS]
