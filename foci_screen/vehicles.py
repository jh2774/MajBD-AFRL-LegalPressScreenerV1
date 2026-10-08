"""Which investment funds are named after a contractor, and telling people when
a new one files.

`connectors/vehicles.py` explains what these funds are and where the data
comes from. This module is the bookkeeping around it:

  * keeping the local index of Form D filers current from EDGAR's daily list;
  * for one contractor, looking up funds that filed before that index began;
  * turning a new filing by a matching fund into an alert item.

One rule keeps history from being sent out as news. A contractor's watch
records the day it was switched on, and **only a filing made on or after that
day is ever an alert**. Looking a name up fills the index with years of old
filings; none of them can reach anybody, because none is dated after the
watch began.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .connectors.adv import money
from .connectors.vehicles import (
    VehicleConnector,
    bare_name,
    company_url,
    filing_url,
    name_matches,
    words,
)

log = logging.getLogger("foci.vehicles")

# On first use, how many recent days of the daily index to read. Enough that a
# newly watched company shows what happened in the last three weeks.
BACKFILL_DAYS = 15
# A sync that has fallen behind catches up this many days at a time.
MAX_DAYS_PER_SYNC = 20
# Per request from the page: funds whose filing lists are fetched, and filings
# read in full. Small on purpose. A well-known company has eighty of these
# funds and each costs two requests to EDGAR, so the page asks for a few at a
# time and shows them as they arrive instead of going quiet for two minutes.
STEP = 6
# Per alert run, across every watched contractor.
READ_PER_RUN = 25

EXPLAINER = (
    "A fund like this collects money from a group of investors to buy shares in one "
    "company. Its Form D says how much it raised, from how many investors, and who "
    "runs it. The link to the contractor is the fund's name only — the filing does "
    "not say what the fund invests in — so confirm it before relying on it.")
WHERE = ("Form D, Item 13 (amounts), Item 14 (investors) and Item 3 (the people "
         "running the fund)")


def today_for_edgar(now: datetime | None = None) -> date:
    """The date whose daily index may still be being written.

    A day's index is complete once the US evening has passed, so the clock is
    read six hours behind UTC and everything before that date is safe to read.
    """
    return ((now or datetime.now(timezone.utc)) - timedelta(hours=6)).date()


def _day(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


# --------------------------------------------------------------- the index

def sync_daily_index(store, vc: VehicleConnector, today: date | None = None) -> dict:
    """Read the days of EDGAR's daily index not read yet, oldest first.

    Stops at the first day that cannot be read rather than skipping it: the
    next sync starts after the last day that was read, so a day stepped over
    would never be looked at again.
    """
    done = store.edgar_days()
    end = (today or today_for_edgar()) - timedelta(days=1)
    if done:
        start = _day(max(done)) + timedelta(days=1)
    else:
        start = end - timedelta(days=BACKFILL_DAYS * 2)     # trading days are ~5 in 7
    if start > end:
        return {"days": 0, "filings": 0, "through": max(done) if done else ""}

    days = [d for d in vc.available_days(start, end) if d not in done]
    if not done:
        days = days[-BACKFILL_DAYS:]
    read = filings = 0
    for day in days[:MAX_DAYS_PER_SYNC]:
        rows = vc.day_filings(day)
        if rows is None:
            break
        store.add_formd_index(rows, "daily")
        store.mark_edgar_day(day, len(rows))
        read += 1
        filings += len(rows)
    through = max(store.edgar_days(), default="")
    return {"days": read, "filings": filings, "through": through}


def own_ciks(store, entity_key: str) -> set[str]:
    """The contractor's own SEC number, if a person has picked it — its own
    Form D filings are its fundraising, shown elsewhere, not a fund's."""
    key = entity_key.upper()
    return set(store.confirmed_ciks([key, store.resolve_entity_key(key)]))


def contractor_name(store, entity_key: str) -> str:
    """What to call a contractor: the name on its awards, else the key itself —
    which for a company typed into a portfolio is its name."""
    key = entity_key.upper()
    rows = store.contracts_where("entity_key", store.resolve_entity_key(key), limit=1)
    return (rows[0].get("entity_name") if rows else "") or key


def _itself(store, watch: dict) -> set[tuple[str, ...]]:
    """The bare names that are the contractor, not a fund named after it.

    Its SEC number is the sure way to tell (`own_ciks`), but most contractors
    here have none picked. Without this, "Shield AI Inc" is listed among the
    funds named after Shield AI and its own raise is added to theirs.
    """
    return {bare_name(watch["phrase"]),
            bare_name(contractor_name(store, watch["entity_key"]))}


def _coarse_word(phrase: str) -> str:
    return max(words(phrase), key=len, default="")


def matching(store, watch: dict, own: set[str], since: str = "") -> list[dict]:
    """Index rows filed by a fund named after the watched phrase."""
    phrase = watch["phrase"]
    skip = set(own) | set(watch.get("unrelated") or [])
    itself = _itself(store, watch)
    return [r for r in store.formd_index_named(_coarse_word(phrase), since=since)
            if r["cik"] not in skip and name_matches(phrase, r["name"])
            and bare_name(r["name"]) not in itself]


def _read_missing(store, vc: VehicleConnector, rows: list[dict], limit: int) -> int:
    read = 0
    for row in rows:
        if read >= limit:
            break
        if row.get("facts") is not None:
            continue
        try:
            facts = vc.read(row)
        except Exception as exc:        # noqa: BLE001 - one filing must not stop the rest
            log.warning("could not read %s: %s", row["accession"], exc)
            continue
        store.set_formd_facts(row["accession"], facts)
        row["facts"] = facts
        read += 1
    return read


# ------------------------------------------------------------ one contractor

def find(store, vc: VehicleConnector, entity_key: str, phrase: str) -> dict:
    """Ask EDGAR which filers are named after a contractor, and remember them.

    Names only. What each fund filed is fetched by `read_more`, a few at a
    time, so a person sees how many there are before the reading starts.
    """
    store.save_vehicle_watch(entity_key, phrase=phrase)
    sync_daily_index(store, vc)
    found, full = vc.lookup(phrase)
    store.add_edgar_entities(found)
    # `more_exist` is about EDGAR's lookup having been full — names it may not
    # have returned at all. Names found but not read yet are counted separately.
    store.save_vehicle_watch(entity_key, looked=True, more_exist=full)
    return view(store, entity_key)


def _unlisted(store, watch: dict, own: set[str]) -> list[dict]:
    """Funds found by name whose filings have not been fetched; newest first."""
    phrase = watch["phrase"]
    skip = set(own) | set(watch.get("unrelated") or [])
    itself = _itself(store, watch)
    waiting = [e for e in store.edgar_entities_named(_coarse_word(phrase))
               if not e["listed_at"] and e["cik"] not in skip
               and name_matches(phrase, e["name"]) and bare_name(e["name"]) not in itself]
    return sorted(waiting, key=lambda e: -int(e["cik"]))    # highest number: newest


def read_more(store, vc: VehicleConnector, entity_key: str, limit: int = STEP) -> dict:
    """Fetch the filings of the next few funds found, and read each one's latest."""
    watch = store.vehicle_watch(entity_key)
    own = own_ciks(store, entity_key)
    for entity in _unlisted(store, watch, own)[:limit]:
        try:
            rows = vc.filings_of(entity["cik"])
        except Exception as exc:        # noqa: BLE001
            log.warning("could not list filings of %s: %s", entity["cik"], exc)
            continue
        store.add_formd_index(rows, "lookup")
        store.mark_entity_listed(entity["cik"])

    latest = [v["latest"] for v in _grouped(matching(store, watch, own))]
    _read_missing(store, vc, latest, limit)
    return view(store, entity_key)


def _grouped(rows: list[dict]) -> list[dict]:
    """One entry per fund, from its filings; most recent activity first."""
    by_cik: dict[str, dict] = {}
    for row in rows:                    # newest first
        fund = by_cik.setdefault(row["cik"], {
            "cik": row["cik"], "name": row["name"], "latest": row, "filings": 0,
            "first_filed": row["filed"]})
        fund["filings"] += 1
        fund["first_filed"] = min(fund["first_filed"], row["filed"])
    return list(by_cik.values())


def _abroad(facts: dict | None) -> bool:
    facts = facts or {}
    return bool(facts.get("outside_us")
                or any(p.get("outside_us") for p in facts.get("people") or []))


def view(store, entity_key: str) -> dict:
    """What is known, for the page. Reads the database only."""
    key = entity_key.upper()
    watch = store.vehicle_watch(key)
    if not watch:
        return {"watch": None, "vehicles": [], "unrelated": [], "pending_names": [],
                "same_name": [], "unread": 0, "totals": None,
                "index_through": max(store.edgar_days(), default="")}

    own = own_ciks(store, key)
    funds = _grouped(matching(store, watch, own))
    for fund in funds:
        latest = fund["latest"]
        fund["latest"] = {**latest, "url": filing_url(latest["cik"], latest["accession"])}
        fund["url"] = company_url(fund["cik"])
        fund["abroad"] = _abroad(latest.get("facts"))

    listed = {f["cik"] for f in funds}
    word = _coarse_word(watch["phrase"])
    # Found by name, filings not fetched yet: named, so nothing looks missing.
    pending = [{"cik": e["cik"], "name": e["name"], "url": company_url(e["cik"])}
               for e in _unlisted(store, watch, own) if e["cik"] not in listed]

    names = {r["cik"]: r["name"] for r in store.formd_index_named(word)}
    names.update({e["cik"]: e["name"] for e in store.edgar_entities_named(word)})
    unrelated = [{"cik": c, "name": names.get(c, f"CIK {c}")} for c in watch["unrelated"]]
    # Filers with the contractor's own name: said, not silently dropped.
    itself = _itself(store, watch)
    same_name = [{"cik": c, "name": n, "url": company_url(c)}
                 for c, n in sorted(names.items(), key=lambda kv: -int(kv[0]))
                 if bare_name(n) in itself and c not in own and c not in watch["unrelated"]]

    known = [f["latest"]["facts"] for f in funds if f["latest"].get("facts")]
    return {
        "watch": watch, "vehicles": funds, "unrelated": unrelated,
        "pending_names": pending, "same_name": same_name,
        "unread": len(funds) - len(known),
        "totals": {
            "funds": len(funds),
            "read": len(known),
            "raised": sum(k.get("amount_sold") or 0 for k in known),
            "investors": sum(k.get("investors") or 0 for k in known),
            "abroad": sum(1 for f in funds if f["abroad"]),
        },
        "index_through": max(store.edgar_days(), default=""),
    }


# ------------------------------------------------------------------- alerts

def refresh_for_alerts(store, vc: VehicleConnector, watches: list[dict]) -> dict:
    """Bring the index up to date, and read the new filings that will be alerted."""
    result = sync_daily_index(store, vc)
    budget = READ_PER_RUN
    for watch in watches:
        if budget <= 0:
            break
        new = matching(store, watch, own_ciks(store, watch["entity_key"]),
                       since=watch.get("watching_since") or "9999")
        budget -= _read_missing(store, vc, new, budget)
    return {**result, "watched": len(watches), "read": READ_PER_RUN - budget}


def _people(facts: dict, limit: int = 4) -> str:
    out = []
    for p in (facts.get("people") or [])[:limit]:
        role = p.get("role") or "named on the filing"
        where = f", address in {p['place']}" if p.get("outside_us") and p.get("place") else ""
        out.append(f"{p['name']} ({role}{where})")
    more = len(facts.get("people") or []) - limit
    return "; ".join(out) + (f"; and {more} more" if more > 0 else "")


def describe(row: dict) -> str:
    """One filing in a sentence or three, for a reader with no finance background."""
    facts = row.get("facts") or {}
    if not facts.get("read_ok"):
        return (f"Form {row['form']} filed {row['filed']}. Its contents could not be "
                f"read here; the link opens it.")
    parts = []
    sold, investors = facts.get("amount_sold"), facts.get("investors")
    if sold:
        who = (f" from {investors:,} investor{'' if investors == 1 else 's'}"
               if investors is not None else "")
        parts.append(f"It reports raising {money(sold)}{who} (Form {row['form']} filed "
                     f"{row['filed']}).")
    else:
        parts.append(f"It reports no money raised yet (Form {row['form']} filed "
                     f"{row['filed']}).")
    if facts.get("place"):
        parts.append(f"Its address is in {facts['place']}"
                     + (", outside the United States." if facts.get("outside_us") else "."))
    people = _people(facts)
    if people:
        parts.append(f"Run by: {people}.")
    return " ".join(parts)


def alert_items(store, watch: dict, base_url: str = "", since: str = "") -> list[dict]:
    """Alert items for one watched contractor: filings made since watching began
    (and since `since`, the day the alert list itself was created)."""
    if not watch.get("watching"):
        return []
    start = max(watch.get("watching_since") or "9999", since or "")
    key = watch["entity_key"]
    company = watch["phrase"]
    items = []
    for row in matching(store, watch, own_ciks(store, key), since=start):
        first = row["form"] == "D"
        links = [("The filing on EDGAR", filing_url(row["cik"], row["accession"])),
                 ("Everything this fund has filed", company_url(row["cik"]))]
        if base_url:
            links.append(("The contractor in FOCI-Screener",
                          f"{base_url.rstrip('/')}/#/entity/{key}"))
        # Fund names end in "L.P." often enough that the sentence's own full
        # stop would double it.
        name = row["name"].rstrip(". ") + ("." if row["name"].rstrip().endswith(".") else "")
        items.append({
            "item_id": f"vehicle:{key}:{row['accession']}",
            "kind": "vehicle",
            "company": company,
            "headline": (f"An investment fund named after {company} filed with the SEC: "
                         f"{name}" if first else
                         f"An investment fund named after {company} updated its filing "
                         f"with the SEC: {name}") + ("" if name.endswith(".") else "."),
            "detail": describe(row),
            "explainer": EXPLAINER,
            "where": WHERE,
            "links": links,
            "importance": 6 if _abroad(row.get("facts")) else (5 if first else 4),
        })
    return items
