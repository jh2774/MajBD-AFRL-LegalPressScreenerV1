"""Sending an approved notice: to the contracting officer named on it, to an
address the reviewer chooses, or to both.

Two ways out, as for everything else this site sends:

  * **Gmail**, when GMAIL_ENABLED (`gmail.py`): a draft in the sender's
    Gmail unless GMAIL_SEND, so a person presses send.
  * Otherwise **the site's own mail service** (`mailer.py` — Resend on the
    Vercel site, where a Gmail sign-in cannot be kept). It has no drafts, so
    sending to a contracting officer this way is a switch of its own,
    NOTICES_SEND, the deliberate act GMAIL_SEND is for Gmail. A copy to an
    address the reviewer chooses needs only the mail service: it is how to
    read a notice in an inbox first, or to forward it yourself.

Nothing here decides that a notice should go. The route that calls this has
checked the notice is approved and that whoever asked may send it.
FOCI_EMAIL_REDIRECT_TO redirects every copy, as it does every alert.

Only the officer's copy moves a notice on (to drafted or sent). A copy to
anyone else is recorded beside it, and the notice can still be delivered to
the officer afterwards. Every attempt is logged (`Store.log_notice_send`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import render
from .mailer import LABEL, Mailer

OFFICER, COPY = "officer", "copy"

OFFICERS_OFF = (
    "Nothing was sent to the contracting officer: neither GMAIL_ENABLED nor NOTICES_SEND "
    "is switched on for this site, so a notice reaches an officer only by hand. Download "
    "the .eml, or send a copy to yourself and forward it.")


@dataclass
class Outcome:
    role: str            # officer | copy
    to: str              # where it went, after any redirect
    status: str          # sent | drafted (a Gmail draft) | rendered (nothing left) | error
    detail: str = ""
    via: str = ""        # gmail, or the mail service's name

    @property
    def delivered(self) -> bool:
        return self.status in ("sent", "drafted")

    def as_dict(self) -> dict:
        return asdict(self)


def state(cfg) -> dict:
    """What sending a notice does on this site, for the page and /health.

    `mode` is where the officer's copy goes — render_only, draft, send or
    redirected — and `copies` whether a copy to a chosen address can go.
    """
    mailer = Mailer(cfg)
    redirect = cfg.email_redirect_to
    if cfg.gmail_enabled:
        verb = "sends" if cfg.gmail_send else "drafts"
        if redirect:
            out = {"mode": "redirected",
                   "detail": f"Approved notices go to {redirect} instead of the officer, as "
                             f"Gmail {verb}."}
        else:
            out = {"mode": "send" if cfg.gmail_send else "draft",
                   "detail": "Approved notices are sent through Gmail." if cfg.gmail_send
                   else "Approved notices become Gmail drafts for someone to send."}
        return {**out, "copies": True, "via": "Gmail"}
    via = LABEL.get(mailer.transport, "")
    if cfg.notices_send and mailer.configured:
        if redirect:
            out = {"mode": "redirected",
                   "detail": f"Approved notices go to {redirect} instead of the officer, "
                             f"through {via}."}
        else:
            out = {"mode": "send",
                   "detail": f"Approved notices are sent to the contracting officer through "
                             f"{via}, from {mailer.sender}."}
        return {**out, "copies": True, "via": via}
    return {"mode": "render_only",
            "detail": "GMAIL_ENABLED is false and NOTICES_SEND is not set; download the "
                      ".eml to route it."
                      + (f" A copy to an address you choose goes through {via}."
                         if mailer.configured else ""),
            "copies": mailer.configured, "via": via}


def send(cfg, gmail, notice: dict, *, role: str, address: str) -> Outcome:
    """Send one copy of an approved notice to `address`, as `role`."""
    if cfg.gmail_enabled:
        r = gmail.deliver_notice(notice, recipient=address)
        status = "error" if r.status == "suppressed" else r.status
        return Outcome(role, r.recipient, status, r.detail, "gmail")

    if role == OFFICER and not cfg.notices_send:
        return Outcome(role, address, "rendered", OFFICERS_OFF)

    mailer = Mailer(cfg)
    body = notice.get("body_text") or ""
    html = render.notice_html(body)
    headers = {"X-FOCI-Notice": notice.get("notice_id") or "",
               "X-FOCI-Entity": notice.get("entity_key") or "",
               "X-FOCI-Severity": notice.get("severity") or ""}
    delivery = mailer.send([address], notice.get("subject") or "FOCI/IP screening notice",
                           body, html, personalize=lambda _a: (body, html, headers))
    status = {"sent": "sent", "drafted": "rendered"}.get(delivery.status, "error")
    detail = delivery.detail or (f"addressed to {address}" if delivery.recipients == [address]
                                 else f"redirected from {address} by FOCI_EMAIL_REDIRECT_TO")
    return Outcome(role, (delivery.recipients or [address])[0], status, detail,
                   mailer.transport)
