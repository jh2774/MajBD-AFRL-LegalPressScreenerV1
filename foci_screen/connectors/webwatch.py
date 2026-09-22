"""Company website monitoring: investor relations, press, and legal pages.

Two jobs:
  1. discover the relevant pages from a domain (common paths + homepage links);
  2. render each to normalised text so the store can diff it run over run.

Normalisation matters more than it looks. Marketing pages carry rotating
banners, timestamps and CSRF nonces; without stripping them every page reads as
"changed" on every screen and the signal drowns. Boilerplate nav/footer text is
dropped for the same reason.

Fetching is two-tier. The cheap path is a plain HTTP GET on a short leash. When
that returns nothing usable — a timeout, a 403, or a 200 carrying an empty
JavaScript application shell — the URL is retried through a headless browser, if
one is configured. The distinction matters because investor-relations subdomains
are both the highest-value pages here and the ones most likely to be unreadable
without a browser: they are typically hosted by a third-party IR platform that
serves an empty container to anything that does not look like Chrome.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

from ..models import Document
from .robots import RobotsPolicy

log = logging.getLogger("foci.web")

CANDIDATE_PATHS = [
    "/investors", "/investor-relations", "/investors/news", "/ir",
    "/news", "/newsroom", "/press", "/press-releases", "/media",
    "/about/news", "/company/news", "/blog/news",
    "/legal", "/legal-notices", "/terms", "/corporate-governance",
    "/investors/sec-filings", "/investors/press-releases",
]

LINK_HINTS = {
    "investor": "investor_relations",
    "ir/": "investor_relations",
    "press": "press",
    "news": "press",
    "media": "press",
    "legal": "legal",
    "governance": "legal",
    "sec-filing": "investor_relations",
    "announcement": "press",
}

# Volatile fragments that would otherwise make every fetch look like a change.
NOISE_PATTERNS = [
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|UTC|EST|EDT|GMT)?\b", re.I),
    re.compile(r"\bcsrf[-_]?token\b.*", re.I),
    re.compile(r"\bnonce\b\S*", re.I),
    re.compile(r"\b(?:session|request)[-_]?id\b\S*", re.I),
    re.compile(r"©\s*\d{4}"),
    re.compile(r"\b\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago\b", re.I),
    re.compile(r"\?(?:utm_[a-z]+|_ga|fbclid)=\S+", re.I),
]

BOILERPLATE = re.compile(
    r"^(cookie|accept all|privacy policy|terms of use|skip to (main )?content|"
    r"menu|search|sign in|log in|subscribe|follow us|share this|all rights reserved)",
    re.I)


class Fetched(NamedTuple):
    html: str
    text: str          # normalised, computed once
    how: str           # "http" | "browser" | "" (nothing usable)


class WebWatchConnector:
    """Watches a contractor's own pages.

    Unlike the government APIs, these hosts owe us nothing: many sit behind a
    CDN that stalls or 403s a non-browser client. Every request here is
    therefore short-timeout, single-attempt, and the whole discovery pass is
    capped — a corporate site that will not answer must cost the run seconds,
    not minutes.

    Pass a `BrowserRenderer` as `browser` to enable the headless fallback for
    hosts the cheap path cannot read. Without one the connector behaves exactly
    as before and records the hosts it had to give up on in `skipped_js_hosts`,
    so a run can report reduced coverage instead of silently implying those
    pages held nothing.
    """

    name = "web"
    TIMEOUT = 12
    RETRIES = 1
    MAX_PROBES = 6          # candidate paths tried when homepage links yield nothing
    # Below this many characters of normalised text, a 200 response is almost
    # certainly an unrendered application shell rather than a thin page. Real
    # press and IR pages run to thousands; a shell yields a nav bar and a
    # <noscript> apology.
    SHELL_TEXT_CHARS = 600
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, http, browser=None, robots=None) -> None:
        self.http = http
        self.browser = browser
        self.robots = robots if robots is not None else RobotsPolicy(http)
        # Hosts proven to need a browser — skip the doomed plain fetch next time.
        self._browser_hosts: set[str] = set()
        # Hosts we could not read at all because no browser was available.
        self.skipped_js_hosts: set[str] = set()
        # Hosts that yielded nothing to the browser either. Distinct from the
        # above: a browser was available, and the site did not serve it readable
        # content — usually bot management refusing an identified client.
        self.unreadable_hosts: set[str] = set()
        # URL -> reason, for pages robots.txt told us not to fetch.
        self.skipped_robots: dict[str, str] = {}

    def _get(self, url: str) -> dict:
        return self.http.get(url, headers=self.HEADERS, timeout=self.TIMEOUT,
                             max_retries=self.RETRIES)

    def _browser_ready(self) -> bool:
        return bool(self.browser is not None and self.browser.available)

    def _render(self, url: str) -> Fetched:
        html = self.browser.render(url)
        text = normalise_page(html) if html else ""
        if not text:
            return Fetched("", "", "")
        self._browser_hosts.add(urlparse(url).netloc.lower())
        return Fetched(html, text, "browser")

    # ------------------------------------------------------------ fetching
    def _fetch(self, url: str) -> Fetched:
        """Plain GET, escalating to a headless browser when it yields nothing.

        robots.txt is checked here, the single entry point for both paths, so a
        disallowed page is not fetched by one route after being refused by the
        other.
        """
        host = urlparse(url).netloc.lower()

        permitted, reason = self.robots.allowed(url)
        if not permitted:
            self.skipped_robots[url] = reason
            return Fetched("", "", "")
        self.robots.wait_turn(url)

        if host in self._browser_hosts and self._browser_ready():
            rendered = self._render(url)
            if not rendered.how:
                self.unreadable_hosts.add(host)
            return rendered

        resp = self._get(url)
        ok = resp.get("status") == 200 and bool(resp.get("text"))
        html = resp["text"] if ok else ""
        text = normalise_page(html) if ok else ""

        if len(text) >= self.SHELL_TEXT_CHARS:
            return Fetched(html, text, "http")

        # Timed out, refused, or returned a shell. This is the case IR
        # subdomains land in almost every time.
        if self._browser_ready():
            log.info("%s unreadable over plain HTTP (status %s, %d chars) — "
                     "retrying with headless browser", url, resp.get("status"), len(text))
            rendered = self._render(url)
            if len(rendered.text) > len(text):
                return rendered
            if not text:
                # Both routes came back empty. Recorded only here, where the
                # final outcome is known — a thin page that plain HTTP did get
                # is not an unread host.
                self.unreadable_hosts.add(host)
        elif not ok:
            self.skipped_js_hosts.add(host)

        return Fetched(html, text, "http") if text else Fetched("", "", "")

    # ----------------------------------------------------------- discovery
    def discover_pages(self, domain: str, max_pages: int = 8) -> list[tuple[str, str]]:
        """Return [(url, page_kind)] worth watching for this domain."""
        base = domain if domain.startswith("http") else f"https://{domain}"
        found: dict[str, str] = {}

        home = self._fetch(base)
        if not home.how:
            log.info("%s unreachable for discovery — skipping site", domain)
            return []
        if home.html:
            for href, label in _links(home.html, base):
                blob = f"{href.lower()} {label.lower()}"
                for hint, kind in LINK_HINTS.items():
                    if hint in blob:
                        if _same_site(base, href):
                            found.setdefault(href.split("#")[0], kind)
                        break
                if len(found) >= max_pages * 2:
                    break

        if len(found) < 3:
            probes = 0
            for path in CANDIDATE_PATHS:
                if probes >= self.MAX_PROBES or len(found) >= max_pages:
                    break
                url = urljoin(base, path)
                if url in found:
                    continue
                probes += 1
                got = self._fetch(url)
                if got.how and len(got.text) >= self.SHELL_TEXT_CHARS:
                    found[url] = _kind_for_path(path)
                if len(found) >= max_pages:
                    break

        return list(found.items())[:max_pages]

    # -------------------------------------------------------------- fetch
    def fetch(self, url: str, kind: str = "press", company: str = "") -> Document | None:
        got = self._fetch(url)
        if not got.how or len(got.text) < 200:
            return None
        return Document(
            source="web", key=f"web:{_canonical(url)}",
            title=f"{company or urlparse(url).netloc} — {kind.replace('_', ' ')}",
            url=url, text=got.text, doc_type=kind,
            meta={"page_kind": kind, "domain": urlparse(url).netloc,
                  "rendered_with": got.how})

    def collect(self, domain: str, company: str = "",
                max_pages: int = 6) -> list[Document]:
        docs: list[Document] = []
        for url, kind in self.discover_pages(domain, max_pages=max_pages):
            doc = self.fetch(url, kind, company)
            if doc:
                docs.append(doc)
        return docs


# ------------------------------------------------------------------ helpers

def _kind_for_path(path: str) -> str:
    if "investor" in path or path.strip("/") == "ir":
        return "investor_relations"
    if any(w in path for w in ("legal", "terms", "governance")):
        return "legal"
    return "press"


def _links(html: str, base: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            out.append((urljoin(base, a["href"]), a.get_text(" ", strip=True)[:80]))
    except Exception:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            out.append((urljoin(base, m.group(1)),
                        re.sub(r"<[^>]+>", " ", m.group(2))[:80]))
    return out


def _same_site(base: str, url: str) -> bool:
    b, u = urlparse(base).netloc.lower(), urlparse(url).netloc.lower()
    b, u = b.removeprefix("www."), u.removeprefix("www.")
    return bool(u) and (u == b or u.endswith("." + b) or b.endswith("." + u))


def _canonical(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"


# A <main> holding less than this is a layout wrapper, not the content.
MAIN_CONTENT_CHARS = 1000


def _content_root(soup):
    """The element actually holding the page's prose.

    Preferring `<main>` unconditionally is wrong on real corporate sites.
    Lockheed's newsroom puts a navigation rail in `<main>` — 99 characters —
    and the 5,200 characters of press-release text outside it, so trusting the
    tag discarded 98% of the page and every rule downstream saw a nav menu.
    Take `<main>` or `<article>` only when it carries enough text to plausibly
    be the content; otherwise use the whole body and let line-level
    boilerplate stripping deal with the furniture.
    """
    body = soup.body or soup
    for tag in ("main", "article"):
        node = soup.find(tag)
        if node is not None and len(node.get_text(" ", strip=True)) >= MAIN_CONTENT_CHARS:
            return node
    return body


def normalise_page(html: str, limit: int = 40000) -> str:
    """HTML -> stable plain text suitable for hashing and diffing."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for bad in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
            bad.decompose()
        text = _content_root(soup).get_text("\n")
    except Exception:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "\n", text)

    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 3 or BOILERPLATE.match(line):
            continue
        for rx in NOISE_PATTERNS:
            line = rx.sub("", line)
        line = line.strip()
        if not line or line.lower() in seen:
            continue
        seen.add(line.lower())
        lines.append(line)
    return "\n".join(lines)[:limit]
