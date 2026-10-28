"""Email-and-password accounts, each with a workspace of its own.

An account owns a tenant. Everything this tool already keeps per tenant —
screens, findings, notices and their decisions, watchlists, the screening
policy and rule settings, alert lists — is therefore the account's own, with
no change to how any of it is stored. The portfolio, which used to live only
in the browser, is kept on the account so it follows the person to another
machine.

Signing in sits beside the API keys rather than replacing them: the daily
scheduled check and the command line still use a key, and a key still
selects its own tenant.

Choices worth knowing:

  * **Passwords** are hashed with scrypt from the standard library, with a
    random salt per account. The parameters are stored with each hash, so
    raising them later re-hashes on the next sign-in rather than locking
    anyone out.
  * **Sessions** are a random token in an HttpOnly, SameSite=Lax cookie. Only
    a SHA-256 of the token is stored, so a copy of the database does not
    hand anyone a working session.
  * **Guessing** is slowed per account: ten wrong passwords lock it for
    fifteen minutes. An unknown address costs the same time as a known one,
    so the sign-in form does not reveal who has an account.
  * **Who may sign up** is a setting (`FOCI_SIGNUP`), closed by default.
    The auth module's own note applies: a self-service credential endpoint
    is the most attacked surface a tool like this has, and a deployment
    should not take sign-ups from whoever finds it first.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

MIN_PASSWORD = 10
MAX_PASSWORD = 1024            # scrypt is fed the whole thing; bound the work
SESSION_DAYS = 30
MAX_FAILURES = 10
LOCK_MINUTES = 15
COOKIE = "foci_session"
# Sent by the site's own pages on every request. A cookie is attached to a
# request whichever site starts it; this header cannot be added by another site
# without a CORS preflight this server does not grant, so requiring it on
# changes made with a cookie closes cross-site request forgery.
CLIENT_HEADER = "x-foci-client"

EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# n=2**14, r=8: about 16 MB and a few tens of milliseconds per check.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


class AccountError(ValueError):
    """Something the person signing up or in can correct."""


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RX.match(email) or len(email) > 254:
        raise AccountError("Enter a valid email address.")
    return email


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise AccountError("That password is too long.")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt((password or "").encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))


def needs_rehash(stored: str) -> bool:
    return not stored.startswith(f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$")


# Checked against when the address is unknown, so a wrong address takes as
# long as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_time(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def new_session_token() -> tuple[str, str]:
    """(token for the cookie, hash for the database)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def new_tenant_id() -> str:
    return "u-" + secrets.token_hex(6)


def signup_allowed(setting: str, email: str) -> bool:
    """Whether `email` may create an account under FOCI_SIGNUP."""
    setting = (setting or "").strip().lower()
    if not setting:
        return False
    if setting == "open":
        return True
    domain = "@" + email.rsplit("@", 1)[-1]
    allowed = {item.strip() for item in setting.split(",") if item.strip()}
    return email in allowed or domain in allowed


def signup_mode(setting: str) -> str:
    """open | restricted | closed, for the sign-in page to word itself by."""
    setting = (setting or "").strip().lower()
    return "closed" if not setting else "open" if setting == "open" else "restricted"


# ------------------------------------------------------------------ emails
# The links an account is sent. Reset and invitation links put the token
# after "#", which a browser never sends to a server: it stays out of access
# logs and Referer headers, and a mail scanner that opens the link cannot use
# it up, because using it takes a form the person submits.

RESET_HOURS = 1
VERIFY_HOURS = 72
INVITE_HOURS = 7 * 24
# One email of a kind per address per this long, so the forms cannot be used to
# flood someone's inbox.
RESEND_SECONDS = 120
ROLES = ("owner", "member")

FOOTER = ("\n\n—\nFOCI-Screener. If you did not expect this email, you can ignore "
          "it; nothing changes unless the link is used.")


def reset_email(link: str) -> tuple[str, str]:
    return ("Reset your FOCI-Screener password",
            "Someone — hopefully you — asked to reset the password for this address "
            "on FOCI-Screener.\n\n"
            f"Choose a new password here (the link works once, for {RESET_HOURS} hour):"
            f"\n{link}\n\n"
            "Resetting signs the account out everywhere it is signed in." + FOOTER)


def verify_email(link: str, organisation: str) -> tuple[str, str]:
    return ("Confirm your email address for FOCI-Screener",
            f"You created a FOCI-Screener account for \"{organisation}\".\n\n"
            f"Confirm this is your address (the link lasts {VERIFY_HOURS // 24} days):"
            f"\n{link}\n\n"
            "Confirming lets a forgotten password be reset by email." + FOOTER)


def invite_email(link: str, organisation: str, inviter: str) -> tuple[str, str]:
    return (f"{inviter} invited you to {organisation} on FOCI-Screener",
            f"{inviter} has invited you to join \"{organisation}\" on FOCI-Screener, "
            f"a tool that screens federal contractors for foreign ownership and "
            f"intellectual-property risk using public records.\n\n"
            f"Accept, and choose a password (the link lasts {INVITE_HOURS // 24} days):"
            f"\n{link}\n\n"
            f"You will share {organisation}'s workspace: its portfolio, screens, "
            f"notices, watchlists and settings." + FOOTER)


def too_soon(last: dict | None) -> bool:
    """Whether an email of this kind went to this address within RESEND_SECONDS."""
    from datetime import datetime, timezone

    if not last:
        return False
    try:
        sent = datetime.fromisoformat(str(last["created_at"]).replace("Z", "+00:00"))
    except ValueError:
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - sent).total_seconds() < RESEND_SECONDS
