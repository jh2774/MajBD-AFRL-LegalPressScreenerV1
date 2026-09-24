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


def parse_keys(raw: str) -> dict[str, str]:
    """`"tenant:key,tenant2:key2"` -> {key: tenant}. Keys index the map."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        tenant, _, key = pair.partition(":")
        tenant, key = tenant.strip(), key.strip()
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


def make_dependency(keymap: dict[str, str]):
    """Build the FastAPI dependency that yields a tenant id."""

    async def require_tenant(
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> str:
        if not keymap:
            # Say how to fix it. The symptom otherwise is a web page that shows
            # nothing, which looks like missing data rather than missing config.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=CLOSED_DETAIL)

        presented = x_api_key.strip()
        if not presented and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()
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
