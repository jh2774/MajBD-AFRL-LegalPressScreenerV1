"""Portfolio keys: a dashboard you can keep in a text file.

A portfolio is a named list of companies somebody wants to watch. The key is
that list, encoded into one line of text — not a pointer to a row in a
database. That is the whole design decision, and it follows from how this tool
gets deployed: on a free instance whose database has already been discarded
once, a saved dashboard that lives server-side is a saved dashboard that
disappears. A key in a file on your own machine does not.

It also means a portfolio moves. The same line of text opens the same
dashboard on a colleague's browser, against a different deployment, after the
database has been rebuilt from nothing.

    FOCI-PORTFOLIO-1.<base64url(deflate(json))>.<checksum>

The checksum is not security. It is there because the realistic failure is a
key that got truncated copying it out of an email or a text file, and a
portfolio that silently loads eight of its ten companies is worse than one
that refuses to load: you would go on believing you were watching two
companies that nobody was watching. Anyone who can read a key can read the
company names inside it, which is the point — there is nothing secret in a
list of contractors, and a key is not a credential.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

PREFIX = "FOCI-PORTFOLIO"
VERSION = 1
CHECKSUM_CHARS = 8
MAX_COMPANIES = 500
MAX_KEY_CHARS = 64_000


class PortfolioKeyError(ValueError):
    """A key that cannot be read, with a reason a person can act on."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Company:
    key: str            # UEI where known, otherwise the name uppercased
    name: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name}


@dataclass
class Portfolio:
    name: str = "Portfolio"
    companies: list[Company] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {"name": self.name,
                "companies": [c.to_dict() for c in self.companies],
                "created_at": self.created_at}


def _checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:CHECKSUM_CHARS]


def _canonical(portfolio: Portfolio) -> bytes:
    """Stable bytes for a portfolio: same content, same key, every time.

    Sorted keys and no incidental whitespace, so two people who built the same
    portfolio can see that they did.
    """
    body = {
        "v": VERSION,
        "n": portfolio.name.strip() or "Portfolio",
        "t": portfolio.created_at,
        # A list of pairs rather than objects: this is the bulk of the payload
        # and the key is read by humans copying it around, so it stays short.
        "c": [[c.key, c.name] for c in portfolio.companies],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode(portfolio: Portfolio) -> str:
    if not portfolio.companies:
        raise PortfolioKeyError("A portfolio needs at least one company.")
    if len(portfolio.companies) > MAX_COMPANIES:
        raise PortfolioKeyError(
            f"A portfolio holds at most {MAX_COMPANIES} companies; this one has "
            f"{len(portfolio.companies)}.")

    raw = _canonical(portfolio)
    packed = zlib.compress(raw, 9)
    body = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
    return f"{PREFIX}-{VERSION}.{body}.{_checksum(raw)}"


def decode(text: str) -> Portfolio:
    """Read a key, or say precisely what is wrong with it."""
    if not text or not text.strip():
        raise PortfolioKeyError("No key given.")
    if len(text) > MAX_KEY_CHARS:
        raise PortfolioKeyError("That key is too long to be one of ours.")

    # Keys get saved in text files, pasted out of emails and wrapped by
    # whatever did the wrapping. None of that changes the content.
    cleaned = "".join(text.split())

    head, _, rest = cleaned.partition(".")
    if not head.startswith(f"{PREFIX}-"):
        raise PortfolioKeyError(
            "That does not look like a portfolio key. One starts with "
            f"\"{PREFIX}-{VERSION}.\".")
    version_text = head[len(PREFIX) + 1:]
    if version_text != str(VERSION):
        raise PortfolioKeyError(
            f"That key is version {version_text or '?'} and this build reads "
            f"version {VERSION}. Open it with the version that wrote it.")

    body, _, checksum = rest.rpartition(".")
    if not body or not checksum:
        raise PortfolioKeyError(
            "That key is incomplete — it is missing its checksum. Keys are one "
            "unbroken line; check nothing was cut off when it was copied.")

    padding = "=" * (-len(body) % 4)
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(body + padding))
    except (binascii.Error, ValueError, zlib.error) as exc:
        raise PortfolioKeyError(
            "That key is damaged and cannot be read. If it was copied out of a "
            "document, check the whole line came with it.") from exc

    if _checksum(raw) != checksum:
        raise PortfolioKeyError(
            "That key does not match its checksum, which means it was altered "
            "or cut short. Loading it could leave companies out of the "
            "dashboard without saying so, so it is refused.")

    try:
        body_obj = json.loads(raw.decode("utf-8"))
        companies = [Company(key=str(k), name=str(n))
                     for k, n in body_obj.get("c", [])]
    except (ValueError, TypeError) as exc:
        raise PortfolioKeyError("That key's contents are not readable.") from exc

    if not companies:
        raise PortfolioKeyError("That key holds no companies.")

    return Portfolio(name=str(body_obj.get("n") or "Portfolio"),
                     companies=companies,
                     created_at=str(body_obj.get("t") or _now()))


def from_entity_keys(name: str, keys: list[str],
                     names: dict[str, str] | None = None) -> Portfolio:
    """Build a portfolio, dropping blanks and keeping the order given."""
    names = names or {}
    seen: set[str] = set()
    companies: list[Company] = []
    for raw in keys:
        key = (raw or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        companies.append(Company(key=key, name=names.get(key, "")))
    return Portfolio(name=name.strip() or "Portfolio", companies=companies)
