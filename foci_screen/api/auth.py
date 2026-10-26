"""API-key authentication, mapping a key to a tenant.

Keys live in `FOCI_API_KEYS` as `tenant:key` pairs, comma separated:

    FOCI_API_KEYS="acme:sk_live_9f3c...,navy-pmo:sk_live_1a7b..."

Environment rather than a database, deliberately. A key table needs a
bootstrapping route to mint the first key, and that route is the most attacked
surface an API of this kind has. Rotating a key here is an environment change
and a restart, which for an analyst tool is an acceptable trade for not
shipping a self-service credential endpoint.

**Fails closed.** With no keys configured every authenticated route returns 503.
A screening tool that drafts email to federal officials must not be reachable by
accident because someone deployed it before setting a variable.

**Public mode** (`FOCI_PUBLIC=true`) is the deliberate opposite: anyone with
the site's address can read, screen and set up alerts, as tenant `default`,
without a key. A key is then an admin key, needed only for what removes work
or changes the site for everyone, and for the one action that emails a federal
official — see `make_admin_dependency`. Alert emails need no key because each
address has to confirm before it receives one (`consent`).
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

CLOSED_DETAIL = (
    "FOCI_API_KEYS is not set, so the API is closed and no data can be read. "
    "Set it to one or more \"tenant:key\" pairs and restart. Locally, put it in "
    "a .env file beside the package; on Render, set it on the service. The CLI "
    "writes as tenant \"default\", so use default:<key> to see data screened "
    "from the command line."
)

# Printed once at startup, because a server that starts silently and then
# refuses everything is diagnosed in the browser, which is the wrong place.
STARTUP_WARNING = (
    "FOCI_API_KEYS is not set. The API has started but every authenticated "
    "route will return 503 and the web interface will show nothing. "
    "Generate a key with:\n"
    "    python -c \"import secrets; print('default:sk_' + secrets.token_urlsafe(24))\"\n"
    "then put it in FOCI_API_KEYS (a .env file beside the package works locally)."
)


DEFAULT_TENANT = "default"


def parse_keys(raw: str) -> dict[str, str]:
    """`"tenant:key,tenant2:key2"` -> {key: tenant}. Keys index the map.

    An entry with no colon is taken as a key for the `default` tenant. This is
    the single most likely way to configure this wrong — pasting the key
    without its tenant prefix — and the old behaviour was to discard the entry
    silently, leaving a server that starts cleanly and then refuses everything.
    Guessing "default" here is safe: it is the tenant the CLI writes as, so it
    is the one a single-tenant deployment wants anyway.
    """
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" in pair:
            tenant, _, key = pair.partition(":")
            tenant, key = tenant.strip(), key.strip()
        else:
            tenant, key = DEFAULT_TENANT, pair
        if tenant and key:
            out[key] = tenant
    return out


def resolve_tenant(keymap: dict[str, str], presented: str) -> str:
    """Constant-time lookup of a presented key. Returns "" if unknown."""
    match = ""
    for key, tenant in keymap.items():
        # Compare every entry so timing does not leak which prefix was close.
        if secrets.compare_digest(key, presented):
            match = tenant
    return match


ADMIN_NEEDED = (
    "This needs the site's admin key: it removes recorded work, changes how the "
    "site screens for everyone, or emails a federal official. Enter the key with "
    "\"Admin key\" at the top right.")

ADMIN_UNSET = (
    "This needs an admin key, and this site has none. It is open to anyone with "
    "its address (FOCI_PUBLIC), so removing recorded work, changing how the site "
    "screens and approving notices are switched off until FOCI_API_KEYS is set "
    "to a key for whoever looks after it.")


def _presented(authorization: str, x_api_key: str) -> str:
    presented = x_api_key.strip()
    if not presented and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    return presented


def make_dependency(keymap: dict[str, str], public: bool = False):
    """Build the FastAPI dependency that yields a tenant id."""

    async def require_tenant(
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> str:
        presented = _presented(authorization, x_api_key)
        if public:
            # No key needed. A known one still selects its tenant; an unknown
            # one — left in a browser from before the site was opened, say —
            # is ignored rather than refused, so it cannot lock anyone out.
            return (resolve_tenant(keymap, presented) if presented else "") \
                or DEFAULT_TENANT

        if not keymap:
            # Say how to fix it. The symptom otherwise is a web page that shows
            # nothing, which looks like missing data rather than missing config.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=CLOSED_DETAIL)

        if not presented:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Supply a key in Authorization: Bearer <key> or X-API-Key.",
                headers={"WWW-Authenticate": "Bearer"})

        tenant = resolve_tenant(keymap, presented)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Unknown API key.")
        return tenant

    return require_tenant


def make_admin_dependency(keymap: dict[str, str], public: bool = False):
    """The dependency for routes that remove recorded work, change how the
    site screens for everyone, or email a federal official.

    Outside public mode every caller already holds a key, and any key will
    do, as before. In public mode a key from FOCI_API_KEYS is required; with
    none configured these routes are closed (403), not opened.
    """
    if not public:
        return make_dependency(keymap)

    async def require_admin(
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> str:
        if not keymap:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ADMIN_UNSET)
        presented = _presented(authorization, x_api_key)
        tenant = resolve_tenant(keymap, presented) if presented else ""
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ADMIN_NEEDED if not presented else "That admin key is not "
                       "one this site knows. Check it under \"Admin key\".",
                headers={"WWW-Authenticate": "Bearer"})
        return tenant

    return require_admin


def make_admin_flag(keymap: dict[str, str], public: bool = False):
    """A dependency answering whether this request carries a known key.

    Always yes outside public mode, where a request without one never gets
    this far. In public mode it decides what a visitor is shown of other
    people's email addresses, and whether an address they add is trusted
    without confirming.
    """

    async def is_admin(
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> bool:
        if not public:
            return True
        presented = _presented(authorization, x_api_key)
        return bool(presented and resolve_tenant(keymap, presented))

    return is_admin
