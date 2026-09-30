"""Email the people watching a portfolio when something about it changes.

Three kinds of item go into an alert:

  * **Form ADV changes** for the investment firms in the portfolio — a new
    fund, a change in how much of a fund foreign investors own, a new filing.
  * **Form D filings** by the contractors themselves — the notice a company
    files when it raises money privately. Only for contractors whose SEC
    record a person has picked (see `Store.confirmed_ciks`).
  * **Flagged contractors** — a notice the screening policy raised for a
    contractor in the portfolio.

What an alert does not contain is a verdict. Each item says what changed, one
sentence on what that part of the form is, and exactly where to look. Deciding
what it means is left to the person reading it, who is better placed to — the
alert's job is to get them to the source, not to stand in for it.

Every item goes to each subscription once. When a portfolio is first saved, or
companies are added to it, everything already known about them is marked as
sent: the subscriber hears about what happens from then on, not a backlog of
the firm's history presented as news.
"""
from __future__ import annotations

import html as html_lib
import logging

from .connectors import formd as formd_mod
from .connectors.adv import AdvConnector, AdviserSnapshot, AdvUnavailable, compare
from .connectors.formd import FormDConnector, FormDUnavailable, IssuerSnapshot
from .notify.mailer import Mailer

log = logging.getLogger("foci.alerts")

FOOTER = (
    "You are receiving this because your address was added to the \"{name}\" alert "
    "list in FOCI-Screener. To stop, ask whoever added you to remove it, or reply "
    "to this message.\n\n"
    "Drawn from public records. This is not a FOCI determination or a finding of "
    "wrongdoing — it tells you where something changed so you can look for yourself."
)


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

def adv_item(change: dict) -> dict:
    return {
        "item_id": f"adv:{change['change_id']}",
        "kind": "adv",
        "company": change.get("firm", ""),
        "headline": change.get("headline", ""),
        "detail": change.get("detail", ""),
        "explainer": change.get("explainer", ""),
        "where": change.get("where", ""),
        "links": [("Firm summary on the SEC's adviser site", change["links"]["summary"]),
                  ("Full Form ADV (PDF)", change["links"]["form"])],
        "importance": change.get("importance", 1),
    }


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
    }


def notice_item(notice: dict, base_url: str) -> dict:
    key = notice.get("entity_key", "")
    link = f"{base_url.rstrip('/')}/#/entity/{key}" if base_url else ""
    return {
        "item_id": f"notice:{notice['notice_id']}",
        "kind": "notice",
        "company": notice.get("entity_name", ""),
        "headline": f"{notice.get('entity_name', '')} was flagged "
                    f"({(notice.get('severity') or '').upper()}).",
        "detail": notice.get("trigger_reason") or notice.get("subject") or "",
        "explainer": "A flag means the screen found evidence worth a person's attention. "
                     "The contractor's page lists the evidence and the source of each item.",
        "where": "The contractor's page in FOCI-Screener",
        "links": [("Open the contractor", link)] if link else [],
        "importance": 5,
    }


def pending_items(store, subscription: dict, base_url: str) -> list[dict]:
    crds, entities = split_keys(subscription["companies"])
    ciks = store.confirmed_ciks(entities)
    sent = store.alert_already_sent(subscription["subscription_id"])
    items = [adv_item(c) for c in store.adv_changes(crds, limit=200)]
    items += [formd_item(c, base_url, ciks.get(str(c.get("cik")), ""))
              for c in store.formd_changes(list(ciks), limit=200)]
    items += [notice_item(n, base_url) for n in store.notices_for_entities(entities)]
    fresh = [i for i in items if i["item_id"] not in sent]
    return sorted(fresh, key=lambda i: -i["importance"])


def baseline(store, subscription_id: str, companies: list[str]) -> int:
    """Mark everything already known as sent. Future changes only."""
    crds, entities = split_keys(companies)
    ids = [f"adv:{c['change_id']}" for c in store.adv_changes(crds, limit=5000)]
    ciks = list(store.confirmed_ciks(entities))
    ids += [f"formd:{c['change_id']}" for c in store.formd_changes(ciks, limit=5000)]
    ids += [f"notice:{n['notice_id']}" for n in store.notices_for_entities(entities, 5000)]
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
        if key in {c.upper() for c in sub["companies"]}:
            store.mark_alert_sent(sub["subscription_id"], ids)
            marked += len(ids)
    return marked


# ----------------------------------------------------------------- the email

def compose(subscription: dict, items: list[dict]) -> tuple[str, str, str]:
    """(subject, plain text, html) — plain text first; it is what is read."""
    name = subscription["name"]
    n = len(items)
    subject = f"FOCI-Screener: {n} update{'s' if n != 1 else ''} for \"{name}\""

    lines = [
        f"FOCI-Screener found {n} update{'s' if n != 1 else ''} for the companies "
        f"in \"{name}\".",
        "",
        "Each item says what changed and where to look. It does not say what the "
        "change means — that is for you to judge from the source.",
        "",
    ]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item['headline']}")
        if item["detail"]:
            lines.append(f"   {item['detail']}")
        if item["explainer"]:
            lines.append(f"   What this is: {item['explainer']}")
        if item["where"]:
            lines.append(f"   Where to look: {item['where']}")
        for label, url in item["links"]:
            lines.append(f"   {label}: {url}")
        lines.append("")
    lines += ["—", FOOTER.format(name=name)]
    text = "\n".join(lines)

    e = html_lib.escape
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1c1c1c;max-width:640px">',
        f"<p>FOCI-Screener found <strong>{n} update{'s' if n != 1 else ''}</strong> "
        f"for the companies in <strong>{e(name)}</strong>.</p>",
        '<p style="color:#555">Each item says what changed and where to look. It does '
        "not say what the change means — that is for you to judge from the source.</p>",
    ]
    for item in items:
        links = " · ".join(f'<a href="{e(url)}">{e(label)}</a>' for label, url in item["links"])
        parts.append(
            '<div style="border-left:3px solid #c8a24a;padding:6px 12px;margin:14px 0">'
            f"<div><strong>{e(item['headline'])}</strong></div>"
            + (f"<div>{e(item['detail'])}</div>" if item["detail"] else "")
            + (f'<div style="color:#555;font-size:13.5px"><em>What this is:</em> '
               f"{e(item['explainer'])}</div>" if item["explainer"] else "")
            + (f'<div style="font-size:13.5px"><em>Where to look:</em> {e(item["where"])}'
               f"</div>" if item["where"] else "")
            + (f'<div style="font-size:13.5px">{links}</div>' if links else "")
            + "</div>")
    parts.append(f'<p style="color:#777;font-size:12.5px">'
                 f'{e(FOOTER.format(name=name)).replace(chr(10), "<br>")}</p></div>')
    return subject, text, "".join(parts)


# ------------------------------------------------------------------- the run

def run_alerts(store, adv: AdvConnector, mailer: Mailer, base_url: str = "",
               formd: FormDConnector | None = None) -> dict:
    """Refresh every watched firm and company, then email each portfolio what is new."""
    subscriptions = store.alert_subscriptions(active_only=True)
    all_crds: list[str] = []
    all_entities: list[str] = []
    for s in subscriptions:
        crds, entities = split_keys(s["companies"])
        all_crds += crds
        all_entities += entities
    firms = refresh_advisers(store, adv, all_crds)
    companies = (refresh_issuers(store, formd, list(store.confirmed_ciks(all_entities)))
                 if formd is not None else {})

    summary = {"subscriptions": len(subscriptions), "firms_checked": len(firms),
               "firms": firms, "companies_checked": len(companies),
               "companies": companies, "emails": []}
    for s in subscriptions:
        items = pending_items(store, s, base_url)
        store.touch_alert_subscription(s["subscription_id"])
        if not items:
            summary["emails"].append({"subscription": s["name"], "status": "nothing new"})
            continue
        subject, text, html = compose(s, items)
        delivery = mailer.send(s["emails"], subject, text, html)
        store.log_alert_delivery(
            subscription_id=s["subscription_id"], recipients=delivery.recipients,
            subject=subject, body_text=text, item_count=len(items),
            status=delivery.status, detail=delivery.detail)
        # A failed send is retried next run; drafted and sent are both "said".
        if delivery.status in ("sent", "drafted"):
            store.mark_alert_sent(s["subscription_id"], [i["item_id"] for i in items])
        summary["emails"].append({"subscription": s["name"], "status": delivery.status,
                                  "items": len(items), "detail": delivery.detail})
    return summary
