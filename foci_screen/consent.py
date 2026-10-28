"""Who has agreed to be sent alerts, and asking those who have not.

A site anyone can open must not be a way to email anyone. So an address a
visitor types into an alert list is sent one short request to confirm, and
nothing else until it is confirmed. Every alert after that carries a link that
unsubscribes that one address and nobody else (`alerts.personalizer`); the
link and the request to confirm share one token per address per list.

An address added by someone holding the site's key — on a site that needs a
key for everything, that is everyone — is taken as agreed, as it always was:
they are vouching for it. Someone who unsubscribed is not put back by anyone
else: a save that names them is declined, and they rejoin from their own link
(`Store.set_opted_out`).

The requests are kept within the mail service's allowance: one a day at most
to any address for any list, and a ceiling across the whole site, so that
filling in the form over and over cannot use up the day's email.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

log = logging.getLogger("foci.consent")

PENDING, CONFIRMED, UNSUBSCRIBED = "pending", "confirmed", "unsubscribed"

ASK_AGAIN_AFTER_HOURS = 24
# Across the site, per day (UTC). Resend's free plan allows 100 emails a day;
# this leaves the larger part for the alerts themselves.
ASKS_PER_DAY = 40

EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")


def mask(address: str) -> str:
    """j•••@agency.gov — enough to recognise one's own, not to collect."""
    local, _, domain = (address or "").partition("@")
    return f"{local[:1]}•••@{domain}" if domain else "•••"


def mask_text(text: str) -> str:
    """Every address in a piece of text, masked."""
    return EMAIL_IN_TEXT.sub(lambda m: mask(m.group(0)), text or "")


def confirm_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/alerts/confirm/{token}"


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


# --------------------------------------------------------------- who is sent

def recipients(store, subscription: dict) -> list[str]:
    """The addresses to send to: confirmed ones only.

    One with no record at all was put on its list before records were kept,
    by someone holding a key, and is recorded as confirmed now.
    """
    sid = subscription["subscription_id"]
    records = store.alert_recipients(sid)
    to = []
    for email in subscription["emails"]:
        row = records.get(email.lower()) or store.add_alert_recipient(sid, email, CONFIRMED)
        if row["status"] == CONFIRMED:
            to.append(email)
    return to


def waiting(store, subscription: dict) -> bool:
    """Whether anyone on the list has yet to confirm."""
    records = store.alert_recipients(subscription["subscription_id"])
    return any((records.get(e.lower()) or {}).get("status") == PENDING
               for e in subscription["emails"])


def statuses(store, subscription: dict) -> list[dict]:
    """Each address on a list and where it stands, for the portfolio page."""
    records = store.alert_recipients(subscription["subscription_id"])
    return [{"email": e, "status": (records.get(e.lower()) or {}).get("status", CONFIRMED)}
            for e in subscription["emails"]]


# ------------------------------------------------------------------- asking

def add(store, mailer, subscription: dict, emails: list[str], base_url: str,
        trusted: bool, own: str = "") -> dict:
    """Record addresses just put on a list, and ask the ones that must agree.

    Then ask any still waiting that were never asked — added while no mail
    service was connected, say — so saving the list again, once one is,
    sends their requests. Returns the addresses by what happened to them:
    asked, already asked today, held back by the site's daily ceiling, or
    not sent (with why).

    `own` is the address of whoever is signed in: signing in proved it is
    theirs, so it is not asked to confirm.
    """
    sid = subscription["subscription_id"]
    outcome: dict[str, list] = {"asked": [], "waiting": [], "held": [], "not_sent": []}
    for email in emails:
        before = store.alert_recipients(sid).get(email.lower())
        if before is None and (trusted or (own and email.lower() == own.lower())):
            store.add_alert_recipient(sid, email, CONFIRMED)
        else:
            store.add_alert_recipient(sid, email, PENDING)

    for row in store.alert_recipients(sid).values():
        if row["status"] != PENDING or row["email"] not in subscription["emails"]:
            continue
        if row["email"] not in emails and row.get("asked_at"):
            continue                    # asked before, on an earlier save
        result, detail = ask(store, mailer, subscription, row, base_url)
        if result == "not_sent":
            outcome["not_sent"].append({"email": row["email"], "detail": detail})
        else:
            outcome[result].append(row["email"])
    return outcome


def ask(store, mailer, subscription: dict, row: dict, base_url: str) -> tuple[str, str]:
    """Send one address its request to confirm, unless it had one — for this
    list or any other — within the day, or the site has sent its day's worth.
    (outcome, why not sent)."""
    now = datetime.now(timezone.utc)
    last = store.alert_recipient_last_asked(row["email"])
    if last > _iso(now - timedelta(hours=ASK_AGAIN_AFTER_HOURS)):
        return "waiting", ""
    midnight = now.replace(hour=0, minute=0, second=0)
    if store.alert_recipients_asked_since(_iso(midnight)) >= ASKS_PER_DAY:
        return "held", ""
    subject, text, html = request_email(subscription["name"],
                                        confirm_url(base_url, row["token"]), base_url)
    delivery = mailer.send([row["email"]], subject, text, html)
    if delivery.status in ("sent", "partial"):
        store.mark_alert_recipient_asked(subscription["subscription_id"], row["email"])
        return "asked", ""
    # Drafted — no mail service yet — or refused. Not recorded as asked, so
    # the next save of the list tries again.
    log.warning("request to confirm not sent for list %s: %s",
                subscription["subscription_id"], delivery.detail)
    return "not_sent", delivery.detail


def request_email(name: str, url: str, base_url: str) -> tuple[str, str, str]:
    """(subject, text, html) of the one email an unconfirmed address gets."""
    site = urlsplit(base_url).netloc or base_url or "FOCI-Screener"
    subject = f"Confirm: email alerts for \"{name}\""
    what = ("The list sends an email when something changes for the companies it "
            "watches: a new fund or a change in foreign ownership at an investment "
            "firm, a contractor raising money privately (a Form D filing), a new fund "
            "named after a contractor, or a contractor being flagged. Each email says "
            "what changed and where to look.")
    text = (f"Someone added this address to \"{name}\", an alert list on FOCI-Screener "
            f"({site}).\n\n{what}\n\nTo start receiving them, confirm here:\n{url}\n\n"
            f"If you were not expecting this, ignore it. Nothing more will be sent to "
            f"you unless you confirm.")
    e = html_lib.escape
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1c1c1c;max-width:600px">'
        f"<p>Someone added this address to <strong>{e(name)}</strong>, an alert list on "
        f"FOCI-Screener ({e(site)}).</p><p>{e(what)}</p>"
        f'<p><a href="{e(url)}" style="display:inline-block;background:#c8a24a;color:#1c1c1c;'
        f'padding:9px 16px;border-radius:4px;text-decoration:none">Confirm alerts</a></p>'
        '<p style="color:#777;font-size:13px">If you were not expecting this, ignore it. '
        "Nothing more will be sent to you unless you confirm.</p></div>")
    return subject, text, html
