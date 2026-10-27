"""Signing in by email, for the few people who look after the site.

The admin key does the job of a password, but it is one secret shared by
everyone who has it: pasted into each browser, impossible to take back from
one person without changing it for all, and recorded on every approval as
"api-key:default" — which says no one in particular approved a notice that
went to a federal official. Signing in fixes those three things and nothing
more. Visitors to a public site still need nothing; the key still works.

How it works, with nothing to configure beyond who may sign in
(FOCI_ADMIN_EMAILS) and the mail service the site already has:

  * Someone asks for a link with their address. If it is on the list, a link
    is emailed that works once, for 20 minutes; the answer is the same either
    way, so the form says nothing about who is on the list.
  * Opening the link shows a button — mail scanners open links — and
    pressing it starts a session: a random secret in an HttpOnly cookie,
    stored here only as a hash, good for 30 days or until signing out.
    Taking an address off FOCI_ADMIN_EMAILS ends its sessions at once.
  * The site's own pages mark every request with a header a form on another
    site cannot send. A cookie on a request that changes something counts
    only with that header, and the cookie is SameSite=Lax as well.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request

COOKIE = "foci_session"
HEADER = "x-foci-session"
LINK_MINUTES = 20
SESSION_DAYS = 30
LINKS_PER_HOUR = 5

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

ASKED = ("If that address may sign in, a link is on its way to it. It works once, for "
         f"{LINK_MINUTES} minutes. Check spam if it does not arrive.")


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _stamp(when: datetime) -> str:
    return when.replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SignIn:
    def __init__(self, store_getter, cfg) -> None:
        self._store = store_getter
        self.cfg = cfg

    @property
    def allowed(self) -> list[str]:
        return self.cfg.admin_email_list

    @property
    def configured(self) -> bool:
        return bool(self.allowed)

    # --------------------------------------------------------- the session
    def email_for(self, request: Request) -> str:
        """Who this request is signed in as, or ""."""
        if not self.configured:
            return ""
        secret = request.cookies.get(COOKIE, "")
        if not secret:
            return ""
        if request.method not in SAFE_METHODS and request.headers.get(HEADER) != "1":
            return ""
        email = self._store().session_email(digest(secret))
        return email if email in self.allowed else ""

    def start_session(self, email: str) -> tuple[str, int]:
        """(the cookie's value, its lifetime in seconds)."""
        secret = secrets.token_urlsafe(32)
        self._store().create_session(digest(secret), email,
                                     _stamp(_now() + timedelta(days=SESSION_DAYS)))
        return secret, SESSION_DAYS * 86400

    def end_session(self, request: Request) -> None:
        secret = request.cookies.get(COOKIE, "")
        if secret:
            self._store().end_session(digest(secret))

    # ------------------------------------------------------------ the link
    def link_for(self, email: str) -> str:
        """A new link's secret for an address on the list, or "" — for one not
        on it, or one that has asked too often this hour."""
        email = email.strip().lower()
        if email not in self.allowed:
            return ""
        store = self._store()
        if store.sign_in_links_since(email, _stamp(_now() - timedelta(hours=1))) \
                >= LINKS_PER_HOUR:
            return ""
        secret = secrets.token_urlsafe(32)
        store.create_sign_in_link(digest(secret), email,
                                  _stamp(_now() + timedelta(minutes=LINK_MINUTES)))
        return secret

    def link_usable(self, secret: str) -> bool:
        row = self._store().sign_in_link(digest(secret))
        return bool(row and not row["used_at"] and row["expires_at"] > _stamp(_now())
                    and row["email"] in self.allowed)

    def use_link(self, secret: str) -> str:
        """The address the link signs in, used up; "" if it cannot be used."""
        email = self._store().use_sign_in_link(digest(secret))
        return email if email in self.allowed else ""


def link_email(url: str, site: str) -> tuple[str, str, str]:
    """(subject, text, html) of the email carrying a sign-in link."""
    import html as html_lib

    e = html_lib.escape
    subject = "Your FOCI-Screener sign-in link"
    text = (f"Someone, hopefully you, asked to sign in to FOCI-Screener ({site}).\n\n"
            f"Sign in here — the link works once, for {LINK_MINUTES} minutes:\n{url}\n\n"
            f"If it was not you, ignore this email; nothing happens unless the link is "
            f"used.")
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1c1c1c;max-width:600px">'
        f"<p>Someone, hopefully you, asked to sign in to FOCI-Screener ({e(site)}).</p>"
        f'<p><a href="{e(url)}" style="display:inline-block;background:#c8a24a;'
        f'color:#1c1c1c;padding:9px 16px;border-radius:4px;text-decoration:none">'
        f"Sign in</a></p>"
        f'<p style="color:#777;font-size:13px">The link works once, for {LINK_MINUTES} '
        f"minutes. If it was not you, ignore this email.</p></div>")
    return subject, text, html


def secure(request: Request) -> bool:
    """Whether the browser reached the site over HTTPS, so the cookie can say
    so. Behind Vercel the request this process sees may be plain HTTP."""
    return request.url.scheme == "https" or \
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
