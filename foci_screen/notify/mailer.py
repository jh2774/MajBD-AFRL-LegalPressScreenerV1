"""Sending an alert email, or — until someone says otherwise — not sending it.

Plain SMTP, because every mail provider speaks it: Gmail and Outlook with an
app password, Amazon SES, SendGrid, Resend, Postmark. Some hosts block the
usual SMTP ports on their free tier; most providers also listen on 2525, 2587
or 2465 for exactly that reason, and the port is a setting.

Three outcomes, recorded for every email:

  * **drafted** — `ALERTS_SEND` is off, so the email was composed and logged
    but nothing left the building. The default.
  * **sent** — handed to the mail server, which accepted it.
  * **failed** — sending was on and it did not work; the reason is recorded.

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


@dataclass
class Delivery:
    status: str                 # drafted | sent | failed
    recipients: list[str]       # who it actually went to, after any redirect
    detail: str = ""


class Mailer:
    def __init__(self, cfg) -> None:
        self.cfg = cfg

    @property
    def configured(self) -> bool:
        return bool(self.cfg.smtp_host and self.sender)

    @property
    def sender(self) -> str:
        return self.cfg.alerts_from or self.cfg.smtp_user

    @property
    def redirect(self) -> str:
        return (getattr(self.cfg, "email_redirect_to", "") or "").strip()

    def status(self) -> dict:
        """What will happen to the next alert, in words for the settings page."""
        if not self.cfg.alerts_send:
            return {"mode": "draft", "explanation":
                    "Sending is off. Alerts are written and shown here exactly as they "
                    "would be sent, but no email leaves. Set ALERTS_SEND=true, and the "
                    "SMTP settings, to turn it on."}
        if not self.configured:
            return {"mode": "misconfigured", "explanation":
                    "ALERTS_SEND is on but no mail server is set. Add SMTP_HOST, "
                    "SMTP_USER, SMTP_PASSWORD and ALERTS_FROM, or alerts will fail."}
        if self.redirect:
            return {"mode": "redirect", "explanation":
                    f"Sending is on, but every alert goes to {self.redirect} while "
                    f"FOCI_EMAIL_REDIRECT_TO is set. Clear it to send to the real "
                    f"recipients."}
        return {"mode": "send", "explanation":
                f"Sending is on, from {self.sender} via {self.cfg.smtp_host}."}

    def send(self, to: list[str], subject: str, text: str,
             html: str | None = None) -> Delivery:
        recipients = [a for a in dict.fromkeys(x.strip() for x in to) if a]
        if not recipients:
            return Delivery("failed", [], "No recipients.")

        if self.redirect:
            subject = f"[for {', '.join(recipients)}] {subject}"
            recipients = [self.redirect]

        if not self.cfg.alerts_send:
            return Delivery("drafted", recipients, "Sending is off (ALERTS_SEND).")
        if not self.configured:
            return Delivery("failed", recipients, "No mail server configured.")

        message = EmailMessage()
        message["From"] = formataddr(("FOCI-Screener", self.sender))
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1] or None)
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")

        security = (self.cfg.smtp_security or "starttls").lower()
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port,
                                          context=ssl.create_default_context(), timeout=30)
            else:
                server = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30)
            with server:
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if self.cfg.smtp_user and self.cfg.smtp_password:
                    server.login(self.cfg.smtp_user, self.cfg.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            # The message, never the password: exception text from a failed
            # login can echo what was sent, so it is trimmed to its first line.
            reason = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
            log.warning("alert email to %s failed: %s", recipients, reason)
            return Delivery("failed", recipients, f"{type(exc).__name__}: {reason}")
        return Delivery("sent", recipients)
