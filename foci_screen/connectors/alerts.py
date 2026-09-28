"""Releases that arrive as email alerts.

Every investor-relations platform offers these to anybody who asks, and they
are the route those platforms intend for this. That matters because it is the
only one that works on the hosts that refuse an identified crawler: measured
across six primes, Lockheed, RTX, Northrop and Leidos publish no feed this
tool can find and serve their pages to a browser only. Their alerts arrive in
a mailbox regardless.

**No credentials.** This reads a directory of `.eml` files — what a mail
client writes when you save or export a message, and what a sync tool drops on
disk. Connecting to a mailbox means holding someone's password or an OAuth
token for the account their notices come from, and that is a much larger thing
to get right than it looks. A folder is enough to be useful today and cannot
leak anything.

Keys are the release URL, the same shape `feeds.py` produces, so an
announcement that arrives by both routes is one document with two sightings
rather than two documents that each look new.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import logging
import re
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from ..models import Document
from .feeds import classify

log = logging.getLogger("foci.alerts")

# Links in an alert are mostly furniture: an unsubscribe, a preferences page,
# a tracking redirect, the platform's own site. The release is the one that
# points at the company.
SKIP_LINK_RX = re.compile(
    r"(unsubscribe|preferences|optout|opt-out|privacy|manage-?(your-?)?"
    r"(subscription|alerts?)|twitter\.com|linkedin\.com|facebook\.com|"
    r"youtube\.com|\.(?:png|jpg|gif|css|js)(?:\?|$))", re.I)

HREF_RX = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
BARE_URL_RX = re.compile(r"""https?://[^\s<>"')\]]+""", re.I)

# Subject lines the platforms use. A mailbox holds more than alerts, and a
# folder someone points at this may hold anything at all.
ALERT_SUBJECT_RX = re.compile(
    r"(press release|news release|announces|announcement|投資|"
    r"reports? (?:first|second|third|fourth|q[1-4]|full[- ]year)|"
    r"results|8-k|sec filing|investor|alert|awarded|wins?\b)", re.I)


def _body_text(message: EmailMessage) -> tuple[str, str]:
    """(plain text, html). Either may be empty."""
    plain, html = "", ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            try:
                payload = part.get_content()
            except Exception:       # noqa: BLE001 - unknown charset, skip the part
                continue
            if part.get_content_type() == "text/plain" and not plain:
                plain = payload
            elif part.get_content_type() == "text/html" and not html:
                html = payload
    else:
        try:
            payload = message.get_content()
        except Exception:           # noqa: BLE001
            payload = ""
        if message.get_content_type() == "text/html":
            html = payload
        else:
            plain = payload
    return (plain or "", html or "")


def _strip_html(value: str) -> str:
    without = re.sub(r"(?is)<(script|style).*?</\1>", " ", value or "")
    without = re.sub(r"<[^>]+>", " ", without)
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        without = without.replace(entity, char)
    return " ".join(without.split())


def release_link(plain: str, html: str, domains: set[str]) -> str:
    """The link that points at the company, not at the mailing platform."""
    candidates = HREF_RX.findall(html or "") + BARE_URL_RX.findall(plain or "")
    for raw in candidates:
        url = raw.strip()
        if not url.lower().startswith("http") or SKIP_LINK_RX.search(url):
            continue
        host = urlparse(url).netloc.lower()
        if not host:
            continue
        registrable = ".".join(host.split(".")[-2:])
        if not domains or registrable in domains:
            return url
    return ""


def _registrable(domain: str) -> str:
    host = urlparse(domain if "://" in domain else f"https://{domain}").netloc.lower()
    return ".".join(host.split(".")[-2:])


class AlertConnector:
    """Turns saved investor-relations alert emails into release documents."""

    name = "alerts"
    MAX_MESSAGES = 400
    MIN_TEXT_CHARS = 80

    def __init__(self, directory: str) -> None:
        self.directory = Path(directory) if directory else None
        self.messages_read = 0
        self.messages_skipped = 0

    def available(self) -> bool:
        return bool(self.directory and self.directory.is_dir())

    def _parse(self, path: Path) -> EmailMessage | None:
        try:
            return email.message_from_bytes(path.read_bytes(),
                                            policy=email.policy.default)
        except Exception as exc:    # noqa: BLE001 - a bad file is not fatal
            log.debug("could not read %s: %s", path.name, exc)
            return None

    def document_for(self, message: EmailMessage, company: str,
                     domains: set[str]) -> Document | None:
        subject = " ".join(str(message.get("subject", "")).split())
        if not subject:
            return None

        plain, html = _body_text(message)
        text = plain.strip() or _strip_html(html)
        text = " ".join(text.split())

        # Belongs to this company, or it is somebody else's mail in the folder.
        blob = f"{subject} {text}".lower()
        company_words = [w for w in (company or "").lower().split()
                         if len(w) > 3][:2]
        mentions_company = all(w in blob for w in company_words) if company_words else False
        link = release_link(plain, html, domains)
        if not link and not mentions_company:
            self.messages_skipped += 1
            return None
        if not ALERT_SUBJECT_RX.search(subject) and not link:
            self.messages_skipped += 1
            return None

        body = f"{subject}. {text}".strip()
        if len(body) < self.MIN_TEXT_CHARS:
            self.messages_skipped += 1
            return None

        published = ""
        raw_date = message.get("date")
        if raw_date:
            try:
                published = parsedate_to_datetime(str(raw_date)).isoformat()
            except (TypeError, ValueError):
                published = str(raw_date)

        # Same key shape as the feed connector, so a release that arrives both
        # ways is one document rather than two that each read as new.
        if link:
            parsed = urlparse(link)
            key = f"feed:{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        else:
            digest = hashlib.sha256(subject.encode("utf-8", "replace")).hexdigest()[:16]
            key = f"alert:{digest}"

        kind = classify(subject, text[:600])
        return Document(
            source="web", key=key,
            title=f"{company or 'alert'} — {subject}"[:300],
            url=link, text=body[:20000], published=published,
            doc_type=f"{kind}_release",
            meta={"release_kind": kind, "via": "email alert",
                  "from": str(message.get("from", ""))[:200]})

    def collect(self, company: str = "", domains=()) -> list[Document]:
        if not self.available():
            return []

        wanted = {_registrable(d) for d in domains if d}
        seen: set[str] = set()
        docs: list[Document] = []

        paths = sorted(self.directory.glob("*.eml"))[:self.MAX_MESSAGES]
        for path in paths:
            message = self._parse(path)
            if message is None:
                continue
            self.messages_read += 1
            doc = self.document_for(message, company, wanted)
            if doc and doc.key not in seen:
                seen.add(doc.key)
                docs.append(doc)

        if docs:
            log.info("%d release(s) from %d saved alert(s) for %s",
                     len(docs), self.messages_read, company or "any company")
        return docs
