"""Sending an alert email to the addresses someone typed into a portfolio.

Three ways out, chosen by which settings exist:

  * **Brevo** or **Resend**, over HTTPS — one API key and a sender address.
    This is the route for a free Render instance: since September 2025 Render's
    free web services cannot open connections to the SMTP ports (25, 465, 587),
    so plain SMTP to Gmail or Outlook times out there no matter how it is
    configured. An HTTPS request is ordinary web traffic and is not blocked.
  * **SMTP**, for a paid instance or another host. Port 2525, which most relay
    services also listen on, is not one of the blocked ports.

Connecting one of them is what turns sending on. With none, every alert is
still composed and logged exactly as it would be sent, so the wording can be
read before anybody receives one. `ALERTS_SEND=false` keeps that behaviour
even with a service connected.

Every recipient gets a message of their own. A list holds people at different
organisations who have no reason to learn each other's addresses, and one
address that bounces should not hold up the rest.

Outcomes, recorded for every email:

  * **drafted** — composed and logged; nothing left the building.
  * **sent** — the mail service accepted it for everyone on the list.
  * **partial** — accepted for some; the detail names who it failed for and why.
  * **failed** — accepted for nobody; the reason is recorded and the alert is
    tried again on the next run.

`FOCI_EMAIL_REDIRECT_TO`, the pilot safety valve that already governs notices
to contracting officers, applies here too. While it is set, every alert goes to
that one address instead, with the intended recipients named in the subject —
so the first weeks of alerts can be read by the person responsible for them
before anyone else receives one.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger("foci.mailer")

SENDER_NAME = "FOCI-Screener"
BREVO_URL = "https://api.brevo.com/v3/smtp/email"
RESEND_URL = "https://api.resend.com/emails"

# The settings each route needs, as they are named on the host.
LABEL = {"brevo": "Brevo", "resend": "Resend", "smtp": "SMTP"}
# Ports a free Render web service cannot reach.
BLOCKED_ON_FREE_RENDER = (25, 465, 587)

HOW_TO_CONNECT = (
    "To start sending, connect a mail service: add BREVO_API_KEY and ALERTS_FROM "
    "to this site's environment settings (DEPLOY.md, \"Turning on email alerts\", "
    "has the steps).")


@dataclass
class Delivery:
    status: str                 # drafted | sent | partial | failed
    recipients: list[str]       # who it was addressed to, after any redirect
    detail: str = ""


class Mailer:
    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def _setting(self, name: str) -> str:
        return str(getattr(self.cfg, name, "") or "").strip()

    @property
    def transport(self) -> str:
        """brevo, resend or smtp — whichever has been set up — or ""."""
        chosen = self._setting("mail_provider").lower()
        if chosen in LABEL:
            return chosen
        if self._setting("brevo_api_key"):
            return "brevo"
        if self._setting("resend_api_key"):
            return "resend"
        if self._setting("smtp_host"):
            return "smtp"
        return ""

    @property
    def sender(self) -> str:
        return self._setting("alerts_from") or self._setting("smtp_user")

    @property
    def missing(self) -> list[str]:
        """Settings the chosen route still needs, by the name to set."""
        needs = {
            "brevo": [("brevo_api_key", "BREVO_API_KEY")],
            "resend": [("resend_api_key", "RESEND_API_KEY")],
            "smtp": [("smtp_host", "SMTP_HOST")],
        }.get(self.transport)
        if needs is None:
            return ["BREVO_API_KEY", "ALERTS_FROM"]
        out = [label for attr, label in needs if not self._setting(attr)]
        if not self.sender:
            out.append("ALERTS_FROM")
        return out

    @property
    def configured(self) -> bool:
        return bool(self.transport) and not self.missing

    @property
    def switched_off(self) -> bool:
        return getattr(self.cfg, "alerts_send", None) is False

    @property
    def insisted(self) -> bool:
        return getattr(self.cfg, "alerts_send", None) is True

    @property
    def redirect(self) -> str:
        return self._setting("email_redirect_to")

    def _port_warning(self) -> str:
        port = int(getattr(self.cfg, "smtp_port", 0) or 0)
        host = str(getattr(self.cfg, "managed_host", "") or "")
        if self.transport == "smtp" and port in BLOCKED_ON_FREE_RENDER \
                and "render" in host.lower():
            return (f" Note: Render's free plan blocks port {port}. If alerts fail with "
                    f"a timeout, set SMTP_PORT=2525 where the mail service offers it, or "
                    f"use BREVO_API_KEY instead.")
        return ""

    def status(self) -> dict:
        """What will happen to the next alert, in words for the settings page."""
        base = {"provider": LABEL.get(self.transport, ""), "from": self.sender,
                "missing": [] if self.configured else self.missing}
        if self.switched_off:
            return {**base, "mode": "draft", "explanation":
                    "Sending is switched off (ALERTS_SEND=false). Alerts are written and "
                    "shown here exactly as they would be sent, but no email leaves."}
        if not self.transport and not self.insisted:
            return {**base, "mode": "draft", "explanation":
                    "No email is being sent yet, because no mail service is connected. "
                    "Alerts are still written and shown here exactly as they would be "
                    "sent. " + HOW_TO_CONNECT}
        if not self.configured:
            return {**base, "mode": "misconfigured", "explanation":
                    f"Sending is not working yet: {' and '.join(self.missing)} "
                    f"still need{'s' if len(self.missing) == 1 else ''} to be set. "
                    f"Until then alerts will fail and be tried again later."}
        via = f"from {self.sender} through {LABEL[self.transport]}"
        if self.redirect:
            return {**base, "mode": "redirect", "explanation":
                    f"Sending is on, {via}, but every alert goes to {self.redirect} "
                    f"while FOCI_EMAIL_REDIRECT_TO is set. Clear it to send to the "
                    f"addresses on each list." + self._port_warning()}
        return {**base, "mode": "send", "explanation":
                f"Sending is on: alerts go to the addresses on this list, {via}."
                + self._port_warning()}

    # ------------------------------------------------------------------ sending
    def send(self, to: list[str], subject: str, text: str,
             html: str | None = None) -> Delivery:
        recipients = [a for a in dict.fromkeys(x.strip() for x in to) if a]
        if not recipients:
            return Delivery("failed", [], "No recipients.")

        if self.redirect:
            subject = f"[for {', '.join(recipients)}] {subject}"
            recipients = [self.redirect]

        if self.switched_off:
            return Delivery("drafted", recipients,
                            "Sending is switched off (ALERTS_SEND=false).")
        if not self.transport and not self.insisted:
            return Delivery("drafted", recipients,
                            "No mail service is connected, so nothing was sent.")
        if not self.configured:
            return Delivery("failed", recipients,
                            f"Not sent: {' and '.join(self.missing)} not set.")

        deliver = {"brevo": self._brevo, "resend": self._resend,
                   "smtp": self._smtp}[self.transport]
        failures = deliver(recipients, subject, text, html)

        reached = [a for a in recipients if a not in failures]
        if failures:
            # Reasons, never credentials: `_post` and `_smtp` build them from the
            # service's reply, not from anything this side sent.
            log.warning("alert email not delivered to %d of %d recipient(s): %s",
                        len(failures), len(recipients),
                        "; ".join(sorted(set(failures.values()))))
        if not reached:
            reasons = sorted(set(failures.values()))
            return Delivery("failed", recipients, "; ".join(reasons)[:500])
        if failures:
            return Delivery("partial", recipients,
                            f"Delivered to {len(reached)} of {len(recipients)}. Not "
                            f"delivered: " + "; ".join(
                                f"{a} ({why})" for a, why in failures.items())[:600])
        return Delivery("sent", recipients)

    # Each route returns {address: reason} for the addresses it could not
    # deliver to; an empty dict is everyone reached.

    @staticmethod
    def _post(url: str, headers: dict, body: dict, ok: tuple[int, ...]) -> str:
        """"" when the service accepted the message, else why it did not."""
        import requests

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=30)
        except requests.RequestException as exc:
            return f"could not reach the mail service ({type(exc).__name__})"
        if resp.status_code in ok:
            return ""
        try:
            message = str((resp.json() or {}).get("message") or "")
        except ValueError:
            message = ""
        if resp.status_code in (401, 403) and not message:
            message = "the API key was not accepted"
        return f"the mail service refused it ({resp.status_code}): " \
               f"{message[:240] or 'no reason given'}"

    def _brevo(self, recipients, subject, text, html) -> dict[str, str]:
        headers = {"api-key": self._setting("brevo_api_key"), "accept": "application/json"}
        failures = {}
        for address in recipients:
            body = {"sender": {"name": SENDER_NAME, "email": self.sender},
                    "to": [{"email": address}], "subject": subject, "textContent": text}
            if html:
                body["htmlContent"] = html
            reason = self._post(BREVO_URL, headers, body, ok=(201, 202))
            if reason:
                failures[address] = reason
        return failures

    def _resend(self, recipients, subject, text, html) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._setting('resend_api_key')}"}
        failures = {}
        for address in recipients:
            body = {"from": formataddr((SENDER_NAME, self.sender)), "to": [address],
                    "subject": subject, "text": text}
            if html:
                body["html"] = html
            reason = self._post(RESEND_URL, headers, body, ok=(200, 201, 202))
            if reason:
                failures[address] = reason
        return failures

    def _smtp(self, recipients, subject, text, html) -> dict[str, str]:
        security = (self._setting("smtp_security") or "starttls").lower()
        host, port = self._setting("smtp_host"), int(self.cfg.smtp_port)
        failures: dict[str, str] = {}
        handed_over: set[str] = set()

        def reason_of(exc: Exception) -> str:
            # The first line only: a failed login can echo what was sent.
            first = str(exc).splitlines()[0][:240] if str(exc) else ""
            return f"{type(exc).__name__}: {first}" if first else type(exc).__name__

        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=30,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(host, port, timeout=30)
            with server:
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                user, password = self._setting("smtp_user"), self._setting("smtp_password")
                if user and password:
                    server.login(user, password)
                for address in recipients:
                    message = EmailMessage()
                    message["From"] = formataddr((SENDER_NAME, self.sender))
                    message["To"] = address
                    message["Subject"] = subject
                    message["Message-ID"] = make_msgid(
                        domain=self.sender.split("@")[-1] or None)
                    message.set_content(text)
                    if html:
                        message.add_alternative(html, subtype="html")
                    try:
                        server.send_message(message)
                        handed_over.add(address)
                    except smtplib.SMTPException as exc:
                        failures[address] = reason_of(exc)
        except (smtplib.SMTPException, OSError) as exc:
            # Could not connect or sign in, or the connection dropped part-way:
            # everyone the server had not already taken a message for.
            why = reason_of(exc)
            if isinstance(exc, TimeoutError) and port in BLOCKED_ON_FREE_RENDER:
                why += (f" — port {port} is blocked on Render's free plan; use port "
                        f"2525 or BREVO_API_KEY")
            for address in recipients:
                if address not in handed_over:
                    failures.setdefault(address, why)
        return failures
