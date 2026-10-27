"""What the mail service reports back: addresses that bounce, and complaints.

An alert to an address that no longer exists is refused by the receiving
server long after the mail service accepted it, so `Mailer.send` sees
"sent" and the alert is never tried again — but nobody received it, and the
next alert goes to the same dead address. Brevo and Resend both report these
afterwards, to a URL of ours. This turns those reports into one shape.

Three kinds of event matter, and they are treated differently:

  * **bounce** — the address does not exist, or its server refuses us for
    good. Not mailed again until someone fixes or clears it. A *temporary*
    failure (mailbox full, server down) is not a bounce and is ignored.
  * **complaint** — the reader marked an alert as spam. Not mailed again,
    and the list's owner cannot simply clear it: that would be overruling
    the person, and repeated complaints are what get a sender blocked.
  * **blocked** — the mail service itself declined to send, usually because
    of an earlier bounce or complaint on its side. Treated as a bounce.

Everything else (delivered, opened, clicked) is ignored: tracking whether
people read the alerts is not something this tool does.

Authenticity. Anyone can POST to a public URL, and a forged bounce would
silently stop alerts to a real person. Resend signs every webhook (Svix
scheme) and the signature is checked against RESEND_WEBHOOK_SECRET. Brevo
does not sign, so its webhook URL is given a secret, MAIL_WEBHOOK_SECRET, to
send back as a bearer token or `?token=`. With neither configured, reports
are refused rather than trusted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

# Svix rejects signatures older than this, to stop a captured request being
# replayed later; the same tolerance here.
SIGNATURE_TOLERANCE_SECONDS = 5 * 60

BREVO_EVENTS = {
    "hard_bounce": "bounce",
    "invalid_email": "bounce",
    "blocked": "blocked",
    "spam": "complaint",
    "complaint": "complaint",
}


@dataclass
class MailEvent:
    email: str
    kind: str          # bounce | complaint | blocked
    reason: str = ""
    provider: str = ""


class NotAuthentic(Exception):
    """The report could not be shown to come from the mail service."""


# ------------------------------------------------------------- authenticity

def check_shared_secret(expected: str, presented: str) -> None:
    if not expected:
        raise NotAuthentic("MAIL_WEBHOOK_SECRET is not set, so reports are not accepted.")
    if not presented or not hmac.compare_digest(expected.encode(), presented.encode()):
        raise NotAuthentic("The report did not carry the webhook secret.")


def check_svix_signature(secret: str, headers: dict, body: bytes,
                         now: float | None = None) -> None:
    """Resend's webhooks are signed with the Svix scheme.

    The signed content is "{svix-id}.{svix-timestamp}.{raw body}", HMAC-SHA256
    with the secret's base64 key (after its "whsec_" prefix). The header can
    carry several space-separated "v1,<base64>" signatures during a key
    rotation; any one matching is enough.
    """
    if not secret:
        raise NotAuthentic("RESEND_WEBHOOK_SECRET is not set, so reports are not accepted.")
    lower = {k.lower(): v for k, v in headers.items()}
    msg_id = lower.get("svix-id", "")
    stamp = lower.get("svix-timestamp", "")
    signatures = lower.get("svix-signature", "")
    if not (msg_id and stamp and signatures):
        raise NotAuthentic("The report is not signed.")
    try:
        sent_at = int(stamp)
    except ValueError as exc:
        raise NotAuthentic("The report's timestamp is not a number.") from exc
    if abs((now if now is not None else time.time()) - sent_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise NotAuthentic("The report's signature is too old or too new.")
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError as exc:
        raise NotAuthentic("RESEND_WEBHOOK_SECRET is not a valid signing secret.") from exc
    signed = f"{msg_id}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for candidate in signatures.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise NotAuthentic("The report's signature does not match.")


# ------------------------------------------------------------------ parsing

def _clean(address) -> str:
    return str(address or "").strip().lower()


def parse_brevo(payload) -> list[MailEvent]:
    """Brevo's transactional webhook: one event per object, sometimes a list."""
    events = payload if isinstance(payload, list) else [payload]
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        kind = BREVO_EVENTS.get(str(e.get("event") or "").lower())
        email = _clean(e.get("email"))
        if kind and email:
            out.append(MailEvent(email, kind, str(e.get("reason") or "")[:300], "Brevo"))
    return out


def parse_resend(payload) -> list[MailEvent]:
    """Resend: {"type": "email.bounced" | "email.complained", "data": {...}}."""
    if not isinstance(payload, dict):
        return []
    kind_of = {"email.bounced": "bounce", "email.complained": "complaint"}
    kind = kind_of.get(str(payload.get("type") or ""))
    data = payload.get("data") or {}
    if not kind or not isinstance(data, dict):
        return []
    reason = ""
    bounce = data.get("bounce") or {}
    if kind == "bounce" and isinstance(bounce, dict):
        # A temporary failure is not a dead address.
        if str(bounce.get("type") or "").lower() in ("transient", "undetermined"):
            return []
        reason = str(bounce.get("message") or bounce.get("subType") or "")[:300]
    to = data.get("to") or []
    addresses = to if isinstance(to, list) else [to]
    return [MailEvent(_clean(a), kind, reason, "Resend") for a in addresses if _clean(a)]


def describe(row: dict) -> str:
    """One line for the Portfolio page and the alert log."""
    what = {"bounce": "bounced", "blocked": "was blocked by the mail service",
            "complaint": "marked an alert as spam"}.get(row.get("kind"), row.get("kind"))
    when = str(row.get("last_at") or "")[:10]
    reason = f": {row['reason']}" if row.get("reason") else ""
    return f"{row['email']} {what}{f' on {when}' if when else ''}{reason}"
