"""Email the people watching a portfolio when something about it changes.

Four kinds of item go into an alert:

  * **Form ADV changes** for the investment firms in the portfolio — a new
    fund, a change in how much of a fund foreign investors own, a new filing.
  * **Form D filings** by the contractors themselves — the notice a company
    files when it raises money privately. Only for contractors whose SEC
    record a person has picked (see `Store.confirmed_ciks`).
  * **Investment funds named after a contractor** filing a Form D of their
    own — money being pooled to buy its shares. Only where a person has
    switched that on for the contractor, and only filings made since. With
    it comes what the firms managing those funds change about them on their
    own Form ADV, whether or not the firm is in the portfolio.
  * **Flagged contractors** — a notice the screening policy raised for a
    contractor in the portfolio.

Each item is a flag: one line saying how serious the screen rates it, what
was found and which sources said so, then the detail under it — the evidence
word for word, why the rule counts it, the document to open and what could
not be checked (`notify.flags`). A change to a filing that the screen does
not rate itself is rated from how its reader orders such changes, and one
of little weight is an UPDATE rather than a flag. What an alert does not
contain is a determination: deciding what it means is left to the person
reading it, and every line takes them to the source.

Everyone on a list can take themselves off it. Each message carries a link of
its own, and the headers mail clients turn into an "Unsubscribe" button
(RFC 8058). The link needs the site's address, so a check run with none known
falls back to asking whoever added them.

Every item goes to each subscription once. When a portfolio is first saved, or
companies are added to it, everything already known about them is marked as
sent: the subscriber hears about what happens from then on, not a backlog of
the firm's history presented as news.
"""
from __future__ import annotations

import html as html_lib
import logging
from datetime import datetime, timedelta, timezone

from . import consent, fund_managers
from . import portfolio as portfolio_keys
from . import scopes as scopes_mod
from . import vehicles as vehicles_engine
from .connectors import formd as formd_mod
from .connectors.adv import AdvConnector, AdviserSnapshot, AdvUnavailable, compare
from .connectors.formd import FormDConnector, FormDUnavailable, IssuerSnapshot
from .connectors.vehicles import VehicleConnector
from .notify import bounces, flags, names
from .notify.mailer import Mailer

log = logging.getLogger("foci.alerts")

FOOTER = (
    "You are receiving this because your address was added to the \"{name}\" alert "
    "list in FOCI-Screener. {stop}\n\n"
    "Drawn from public records. This is not a FOCI determination or a finding of "
    "wrongdoing — it tells you where something changed so you can look for yourself."
)
STOP_ASK = "To stop, ask whoever added you to remove it, or reply to this message."
STOP_LINK = "To stop receiving these, unsubscribe here: {url}"


def footer(name: str, unsubscribe_url: str = "") -> str:
    stop = STOP_LINK.format(url=unsubscribe_url) if unsubscribe_url else STOP_ASK
    return FOOTER.format(name=name, stop=stop)


def unsubscribe_url(store, subscription_id: str, address: str, base_url: str) -> str:
    """This address's own unsubscribe link, or "" when the site's address is unknown."""
    if not base_url:
        return ""
    token = store.unsubscribe_token(subscription_id, address)
    return f"{base_url.rstrip('/')}/u/{token}"


def personalizer(store, subscription: dict, base_url: str, build):
    """A `Mailer.send(personalize=...)` callback for one list.

    `build(url)` returns (text, html) with that unsubscribe link in the footer.
    Only addresses on the list get a link: a redirected pilot copy goes to
    someone who is not on it, and a link minted for them would unsubscribe
    nobody. Each opens with a greeting by name (`names`).
    """
    on_list = {e.lower() for e in subscription["emails"]}

    def content(address: str):
        url = (unsubscribe_url(store, subscription["subscription_id"], address, base_url)
               if address.lower() in on_list else "")
        text, html = names.greet(*build(url), names.first_name_for(store, address))
        headers = {"List-Unsubscribe": f"<{url}>",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"} if url else {}
        return text, html, headers
    return content


def recipients(store, subscription: dict) -> list[str]:
    """The list's addresses that have agreed to be emailed (`consent`), less
    any the mail service reported as bouncing or complaining. That leaves out
    anyone who has unsubscribed.

    Unsubscribing already takes the address off the list; this is the second
    lock, for a list saved from a page opened before they did. A bounced
    address stays on the list, so its owner can see it and correct it.
    """
    dead = store.suppressed(subscription["emails"])
    return [e for e in consent.recipients(store, subscription) if e.lower() not in dead]


def held_back(store, subscription: dict) -> str:
    """Why addresses on the list were not sent to, for the alert log."""
    rows = store.suppressed(subscription["emails"]).values()
    return "; ".join(bounces.describe(r) for r in sorted(rows, key=lambda r: r["email"]))


def split_keys(companies: list[str]) -> tuple[list[str], list[str]]:
    """(investment firm CRD numbers, contractor keys) from a portfolio's keys."""
    crds, entities = [], []
    for key in companies or []:
        k = (key or "").strip()
        if k.upper().startswith("CRD:"):
            number = k.split(":", 1)[1].strip()
            if number.isdigit():
                crds.append(number)
        elif k:
            entities.append(k.upper())
    return crds, entities


# ------------------------------------------------------------ Form ADV refresh

def refresh_advisers(store, adv: AdvConnector, crds: list[str]) -> dict[str, dict]:
    """Read each firm, compare with the last reading, store both."""
    results: dict[str, dict] = {}
    for crd in dict.fromkeys(crds):
        stored = store.adv_snapshot(crd)
        previous = AdviserSnapshot.from_dict(stored) if stored else None
        try:
            current = adv.refresh(crd, previous)
        except AdvUnavailable as exc:
            results[crd] = {"ok": False, "detail": str(exc), "changes": 0}
            continue
        except Exception as exc:        # noqa: BLE001 - one firm must not stop the rest
            log.warning("Form ADV refresh for %s failed: %s", crd, exc)
            results[crd] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}",
                            "changes": 0}
            continue

        changes = [c.to_dict() for c in compare(previous, current)]
        store.save_adv_snapshot(crd, current.to_dict(), current.filing_date)
        if changes:
            store.record_adv_changes(changes)
        results[crd] = {"ok": True, "name": current.name, "changes": len(changes),
                        "baseline": previous is None, "funds_read": current.funds_read}
    return results


# -------------------------------------------------------------- Form D refresh

def refresh_issuers(store, formd: FormDConnector, ciks: list[str]) -> dict[str, dict]:
    """Read each company's Form D list, compare with the last reading, store both."""
    results: dict[str, dict] = {}
    for cik in dict.fromkeys(str(int(c)) for c in ciks):
        stored = store.formd_snapshot(cik)
        previous = IssuerSnapshot.from_dict(stored) if stored else None
        try:
            current = formd.refresh(cik, previous)
        except FormDUnavailable as exc:
            results[cik] = {"ok": False, "detail": str(exc), "changes": 0}
            continue
        except Exception as exc:        # noqa: BLE001 - one company must not stop the rest
            log.warning("Form D refresh for %s failed: %s", cik, exc)
            results[cik] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}",
                            "changes": 0}
            continue

        changes = [c.to_dict() for c in formd_mod.compare(previous, current)]
        latest = current.filings[0].filed if current.filings else ""
        store.save_formd_snapshot(cik, current.to_dict(), latest)
        if changes:
            store.record_formd_changes(changes)
        results[cik] = {"ok": True, "name": current.name, "changes": len(changes),
                        "baseline": previous is None, "filings": current.total_filings}
    return results


# ------------------------------------------------------------------- items

def adv_item(change: dict, watches: list[dict] | tuple = ()) -> dict:
    """`watches` are the fund watches of the contractors in the same portfolio:
    a change to a fund named after one of them says so, and goes up the list."""
    tie = fund_managers.tie_to_contractor(change, list(watches))
    return {
        "item_id": f"adv:{change['change_id']}",
        "kind": "adv",
        "company": change.get("firm", ""),
        "headline": change.get("headline", ""),
        "detail": " ".join(x for x in (change.get("detail", ""), tie) if x),
        "explainer": change.get("explainer", ""),
        "where": change.get("where", ""),
        "links": [("Firm summary on the SEC's adviser site", change["links"]["summary"]),
                  ("Full Form ADV (PDF)", change["links"]["form"])],
        "importance": change.get("importance", 1) + (1 if tie else 0),
        "severity": flags.from_importance(change.get("importance", 1) + (1 if tie else 0)),
        "flag": _flag(change.get("headline", "")),
        "sources": ["Form ADV", "IAPD"],
    }


def _flag(headline: str) -> str:
    return (headline or "").strip().rstrip(".")


def formd_item(change: dict, base_url: str = "", entity_key: str = "") -> dict:
    links = []
    if change["links"].get("filing"):
        links.append(("The Form D filing on EDGAR", change["links"]["filing"]))
    links.append(("All of the company's SEC filings", change["links"]["company"]))
    if base_url and entity_key:
        links.append(("The contractor in FOCI-Screener",
                      f"{base_url.rstrip('/')}/#/entity/{entity_key}"))
    return {
        "item_id": f"formd:{change['change_id']}",
        "kind": "formd",
        "company": change.get("company", ""),
        "headline": change.get("headline", ""),
        "detail": change.get("detail", ""),
        "explainer": change.get("explainer", ""),
        "where": change.get("where", ""),
        "links": links,
        "importance": change.get("importance", 1),
        "severity": flags.from_importance(change.get("importance", 1)),
        "flag": _flag(change.get("headline", "")),
        "sources": ["Form D", "SEC EDGAR"],
    }


def notice_item(notice: dict, base_url: str, finding: dict | None = None,
                titles: dict[str, str] | None = None) -> dict:
    """A contractor the screen flagged: what it found, each piece of evidence
    with its source and why it counts, the awards, and what was not checked."""
    parts = flags.notice_parts(notice, finding, titles or {}, base_url)
    severity = (notice.get("severity") or "medium").lower()
    return {
        "item_id": f"notice:{notice['notice_id']}",
        "kind": "notice",
        "company": notice.get("entity_name", ""),
        "headline": f"{notice.get('entity_name', '')} was flagged "
                    f"({severity.upper()}).",
        "detail": "" if parts["points"] else (notice.get("trigger_reason")
                                             or notice.get("subject") or ""),
        "explainer": "",
        "where": "" if parts["links"] else "The contractor's page in FOCI-Screener",
        "importance": 5,
        "severity": severity if severity in flags.SEVERITIES else "medium",
        **parts,
    }


def watched_keys(store, entities: list[str]) -> list[str]:
    """Each contractor key, plus the key its awards are filed under if different.

    A portfolio built from typed names holds names; notices and SEC identities
    are recorded against the screened contractor's UEI. Both are looked up, so
    a company is followed whichever way it was added.
    """
    out: list[str] = []
    for key in entities:
        out.append(key)
        resolved = store.resolve_entity_key(key)
        if resolved != key:
            out.append(resolved)
    return list(dict.fromkeys(out))


def list_scope(subscription: dict):
    """The office or agency a list follows (scopes.Scope), or None."""
    try:
        scope = portfolio_keys.decode(subscription.get("portfolio_key") or "").scope
        return scopes_mod.parse(scope["id"]) if scope else None
    except (portfolio_keys.PortfolioKeyError, scopes_mod.ScopeError):
        return None


def follow_scope(store, subscription: dict) -> int:
    """Bring a list on a whole office up to date with it: every contractor its
    awards name now. Those just added are baselined — their history is not
    news. Returns how many joined."""
    scope = list_scope(subscription)
    if scope is None:
        return 0
    now = store.scope_entity_keys(scope)
    added = [k for k in now if k not in set(subscription["companies"])]
    if set(now) != set(subscription["companies"]):
        store.save_alert_subscription(
            subscription_id=subscription["subscription_id"], name=subscription["name"],
            emails=subscription["emails"], companies=now,
            portfolio_key=subscription.get("portfolio_key") or "",
            active=bool(subscription.get("active", True)))
        subscription["companies"] = now
        baseline(store, subscription["subscription_id"], added, award_keys=True)
    return len(added)


def followed(store, subscription: dict, entities: list[str]) -> tuple[list[str], list[str]]:
    """(the keys notices are looked for under, the keys with an SEC record or
    funds to follow). A list on a whole office holds the award record's own
    keys, thousands of them: none needs resolving, and only the few with more
    than notices to follow are looked at one by one."""
    if list_scope(subscription) is not None:
        return entities, store.keys_followed_further(entities)
    entities = watched_keys(store, entities)
    return entities, entities


def pending_items(store, subscription: dict, base_url: str) -> list[dict]:
    crds, entities = split_keys(subscription["companies"])
    entities, further = followed(store, subscription, entities)
    ciks = store.confirmed_ciks(further)
    sent = store.alert_already_sent(subscription["subscription_id"])
    looked_up = fund_watches(store, further)
    items = [adv_item(c, looked_up) for c in store.adv_changes(crds, limit=200)]
    items += [formd_item(c, base_url, ciks.get(str(c.get("cik")), ""))
              for c in store.formd_changes(list(ciks), limit=200)]
    items += [flagged_item(store, n, base_url)
              for n in store.notices_for_entities(entities)
              if f"notice:{n['notice_id']}" not in sent]
    # Funds named after a contractor: an item exists only for a filing made
    # after both the watch and this list began, whenever the index learns of it.
    started = str(subscription.get("created_at") or "")[:10]
    for watch in watched_vehicles(store, further):
        items += vehicles_engine.alert_items(store, watch, base_url, since=started)
        items += manager_items(store, watch, since=started)
    # A firm that is in the portfolio and also manages a watched contractor's
    # fund reaches here twice with the same change: say it once.
    fresh, seen = [], set(sent)
    for item in items:
        if item["item_id"] not in seen:
            seen.add(item["item_id"])
            fresh.append(item)
    return sorted(fresh, key=lambda i: (flags.rank(i.get("severity", "info")),
                                        -i["importance"]))


def flagged_item(store, notice: dict, base_url: str) -> dict:
    """`notice_item` with the finding it came from, and the titles of the
    documents its evidence is in."""
    finding = store.finding_for_notice(notice)
    keys = [s.get("document_key") for s in (finding or {}).get("signals") or []
            if s.get("document_key")]
    return notice_item(notice, base_url, finding, store.document_titles(keys))


def fund_watches(store, entities: list[str]) -> list[dict]:
    """The fund watches of a portfolio's contractors, switched on or not —
    each once, wherever it is kept (`vehicles.watch_key`)."""
    found = {}
    for key in dict.fromkeys(entities):
        watch = store.vehicle_watch(vehicles_engine.watch_key(store, key))
        if watch:
            found.setdefault(watch["entity_key"], watch)
    return list(found.values())


def watched_vehicles(store, entities: list[str]) -> list[dict]:
    """The switched-on fund watches among a portfolio's contractors."""
    return [w for w in fund_watches(store, entities) if w["watching"]]


def manager_items(store, watch: dict, since: str = "") -> list[dict]:
    """What the firms managing a contractor's funds changed, on their own Form
    ADV, about a fund named after it.

    Asking to be told about a contractor's funds covers this without the firm
    being added to the portfolio: the manager's yearly filing is where a new
    fund's share owned outside the United States first appears. Only changes
    to funds carrying the contractor's name are passed on — the rest of what
    the firm does is its own business until someone chooses to watch it — and,
    as with the funds themselves, only what was noticed after the watch and
    the list began.
    """
    if not watch.get("watching"):
        return []
    start = max(watch.get("watching_since") or "9999", since or "")
    crds = vehicles_engine.manager_crds(store, watch)
    return [adv_item(change, [watch])
            for change in store.adv_changes(crds, limit=200)
            if str(change.get("detected_at") or "")[:10] >= start
            and fund_managers.tie_to_contractor(change, [watch])]


def baseline(store, subscription_id: str, companies: list[str],
             award_keys: bool = False) -> int:
    """Mark everything already known as sent. Future changes only.

    `award_keys`: the companies are the award record's own keys (a list on a
    whole office), so none needs resolving, and only those with an SEC record
    or funds to follow are looked at one by one."""
    crds, entities = split_keys(companies)
    if award_keys:
        further = store.keys_followed_further(entities)
    else:
        entities = watched_keys(store, entities)
        further = entities
    ids = [f"adv:{c['change_id']}" for c in store.adv_changes(crds, limit=5000)]
    ciks = list(store.confirmed_ciks(further))
    ids += [f"formd:{c['change_id']}" for c in store.formd_changes(ciks, limit=5000)]
    ids += [f"notice:{n['notice_id']}"
            for n in store.notices_for_entities(entities, max(5000, len(entities) * 3))]
    # A contractor added to a list that already existed: the fund filings made
    # since its watch began were news before it joined, not now.
    for watch in watched_vehicles(store, further):
        ids += [i["item_id"] for i in vehicles_engine.alert_items(store, watch)]
        ids += [i["item_id"] for i in manager_items(store, watch)]
    store.mark_alert_sent(subscription_id, ids)
    return len(ids)


def baseline_entity(store, entity_key: str) -> int:
    """A contractor's SEC record was just picked: its filing history is not news.

    Lists that already watch the contractor were baselined before it had a
    CIK, so without this the next alert would present every Form D change
    ever recorded for that company as new.
    """
    key = entity_key.upper()
    ciks = list(store.confirmed_ciks([key]))
    if not ciks:
        return 0
    ids = [f"formd:{c['change_id']}" for c in store.formd_changes(ciks, limit=5000)]
    marked = 0
    for sub in store.alert_subscriptions():
        if key in watched_keys(store, split_keys(sub["companies"])[1]):
            store.mark_alert_sent(sub["subscription_id"], ids)
            marked += len(ids)
    return marked


# ----------------------------------------------------------------- the email

def compose(subscription: dict, items: list[dict],
            unsubscribe: str = "") -> tuple[str, str, str]:
    """(subject, plain text, html) — plain text first; it is what is read.

    One line per flag, most serious first, then each again with its detail
    (`notify.flags`). The subject carries the most serious rating."""
    name = subscription["name"]
    items = [_as_flag(i) for i in items]
    subject = (f"FOCI-Screener [{flags.LABEL[flags.worst(items)]}]: "
               f"{flags.counts(items)} for \"{name}\"")
    text = flags.text_body(name, items, footer(name, unsubscribe))

    e = html_lib.escape
    note = e(footer(name)).replace(chr(10), "<br>")
    if unsubscribe:
        note = e(footer(name, "\0")).replace(chr(10), "<br>").replace(
            "\0", f'<a href="{e(unsubscribe)}" style="color:#777">{e(unsubscribe)}</a>')
    return subject, text, flags.html_body(name, items, note)


def _as_flag(item: dict) -> dict:
    """An item from anywhere — a fund named after a contractor, or an older
    caller — with the fields a flag needs."""
    item = dict(item)
    item.setdefault("severity", flags.from_importance(item.get("importance", 1)))
    item.setdefault("flag", _flag(item.get("headline", "")))
    item.setdefault("sources", ["Form D", "SEC EDGAR"] if item.get("kind") == "vehicle"
                    else [])
    return item


# ------------------------------------------------------------------- the run

# How stale a list may get before opening its portfolio triggers a check. Under
# a day, so someone who looks every morning gets a check every morning.
DUE_AFTER_HOURS = 20


def is_due(store, hours: float = DUE_AFTER_HOURS) -> bool:
    """Whether any active list has gone unchecked for longer than `hours`."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for s in store.alert_subscriptions(active_only=True):
        last = s.get("last_run_at")
        if not last:
            return True
        try:
            when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            return True
    return False


# Firms followed only because they manage a watched contractor's fund, per run.
MANAGERS_PER_RUN = 40


def run_alerts(store, adv: AdvConnector, mailer: Mailer, base_url: str = "",
               formd: FormDConnector | None = None,
               vehicles: VehicleConnector | None = None) -> dict:
    """Refresh every watched firm and company, then email each portfolio what is new."""
    subscriptions = store.alert_subscriptions(active_only=True)
    all_crds: list[str] = []
    all_entities: list[str] = []
    for s in subscriptions:
        follow_scope(store, s)
        crds, entities = split_keys(s["companies"])
        all_crds += crds
        all_entities += followed(store, s, entities)[1]
    firms = refresh_advisers(store, adv, all_crds)
    companies = (refresh_issuers(store, formd, list(store.confirmed_ciks(all_entities)))
                 if formd is not None else {})

    funds: dict = {}
    watching = watched_vehicles(store, all_entities)
    if vehicles is not None and watching:
        try:
            funds = vehicles_engine.refresh_for_alerts(store, vehicles, watching)
        except Exception as exc:        # noqa: BLE001 - the rest of the alert still goes
            log.warning("could not update the index of fund filings: %s", exc)
            funds = {"error": f"{type(exc).__name__}: {exc}"}

    # The firms that manage those funds, whether or not anyone added them to a
    # portfolio. One small request each on a night they filed nothing.
    seen = set(all_crds)
    managers = [crd for crd in dict.fromkeys(
        crd for watch in watching for crd in vehicles_engine.manager_crds(store, watch))
        if crd not in seen][:MANAGERS_PER_RUN]
    managed = refresh_advisers(store, adv, managers)

    summary = {"subscriptions": len(subscriptions), "firms_checked": len(firms),
               "firms": firms, "companies_checked": len(companies),
               "companies": companies, "funds": funds,
               "managers_checked": len(managed), "managers": managed, "emails": []}
    for s in subscriptions:
        items = pending_items(store, s, base_url)
        store.touch_alert_subscription(s["subscription_id"])
        if not items:
            summary["emails"].append({"subscription": s["name"], "status": "nothing new"})
            continue
        # Only to addresses that agreed, less anyone who left. With nobody,
        # nothing is marked as said: whoever confirms first hears what changed
        # since the list began.
        to = recipients(store, s)
        skipped = held_back(store, s)
        if not to:
            summary["emails"].append({
                "subscription": s["name"], "items": len(items),
                "status": "waiting for confirmation" if consent.waiting(store, s)
                else "nobody to send to", "detail": skipped})
            continue
        subject, text, html = compose(s, items)
        delivery = mailer.send(
            to, subject, text, html,
            personalize=personalizer(store, s, base_url,
                                     lambda url, s=s, items=items: compose(s, items, url)[1:]))
        detail = " ".join(x for x in (
            delivery.detail, f"Not sent to: {skipped}." if skipped else "") if x)
        store.log_alert_delivery(
            subscription_id=s["subscription_id"], recipients=delivery.recipients,
            subject=subject, body_text=text, item_count=len(items),
            status=delivery.status, detail=detail)
        # A failed send — nobody reached — is retried next run. Drafted and
        # sent are both "said". So is partial: sending again would give the
        # people it reached a second copy, and the address it missed is named
        # in the log for someone to correct, which a retry would not do.
        if delivery.status in ("sent", "drafted", "partial"):
            store.mark_alert_sent(s["subscription_id"], [i["item_id"] for i in items])
        summary["emails"].append({"subscription": s["name"], "status": delivery.status,
                                  "items": len(items), "detail": detail})
    return summary
