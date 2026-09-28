"""Press, financial and legal releases from a company's own feed.

The scraping route to these pages is mostly closed. Investor-relations
subdomains sit behind bot management that refuses an honestly identified
client, and this project does not disguise itself to get past that. What those
same platforms publish willingly is a feed: Q4, Notified and EQS all expose
RSS or Atom for exactly this purpose, and a feed is an invitation rather than
something to be worked around.

It is also the better shape for what this tool does. Watching a newsroom page
means diffing a document whose navigation, cookie banner and "related items"
rail change for reasons that have nothing to do with the company. A feed gives
one entry per release, each with its own identity, so a new announcement
arrives as a *new document* rather than as a diff against a page that has been
rearranged — which is the difference the risk engine already weights, since
evidence that appeared since the last screen scores above evidence that has
sat there for years.

Every request goes through `WebWatchConnector`, which enforces robots.txt,
honours crawl delay and identifies the tool. There is no separate HTTP path
here, deliberately: a diagnostic or a connector that bypassed the compliance
layer would hit, repeatedly, exactly the hosts that have refused this tool.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from ..models import Document

log = logging.getLogger("foci.feeds")

# Where feeds live when a page does not advertise one. Ordered by how often
# they pay off on corporate sites; the probe budget usually runs out first.
CANDIDATE_FEEDS = (
    "/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml",
    "/news/rss", "/news/feed", "/news/rss.xml",
    "/press/rss", "/press-releases/rss", "/newsroom/rss",
    "/media/rss", "/investors/rss", "/investor-relations/rss",
    # Q4 Inc and Notified, the two platforms most large contractors use.
    "/rss/news-releases.xml", "/rss/pressreleases.aspx",
    "/feed/press-release", "/api/rss/news",
)

# Hosts a newsroom or investor-relations site is normally served from. Ordered
# by how often they exist at all; `ir.` is last because it is the rarest of
# the five on the companies measured.
NEWSROOM_SUBDOMAINS = ("news", "investors", "media", "investor", "ir")

# The same paths, reordered for a host that exists to serve news. An
# off-the-shelf investor-relations site answers on a platform path, so those
# go first — with a budget of six probes, leaving them at the end of the
# general list meant they were never reached on the host most likely to have
# one.
IR_CANDIDATE_FEEDS = (
    "/rss/news-releases.xml", "/rss/pressreleases.aspx",
    "/rss/pressrelease.aspx", "/feed/press-release",
    "/rss", "/rss.xml", "/feed", "/feed.xml", "/atom.xml",
    "/api/rss/news", "/news/rss.xml", "/press-releases/rss",
)

FEED_LINK_RX = re.compile(
    r"""<link\b[^>]*?
        (?=[^>]*?\brel\s*=\s*["']?alternate)
        (?=[^>]*?\btype\s*=\s*["']?application/(?:rss|atom)\+xml)
        [^>]*?\bhref\s*=\s*["']([^"']+)["']""",
    re.I | re.X)

# What the body of a feed starts with, whatever the server calls it. Content
# type is unreliable: feeds are served as text/xml, application/xml,
# text/html and occasionally octet-stream.
FEED_BODY_RX = re.compile(r"<\s*(?:rss|feed|rdf:RDF)\b", re.I)

ATOM = "{http://www.w3.org/2005/Atom}"

# --- what kind of release this is -------------------------------------------
# Ordered by consequence: an announcement that is both a results release and a
# lawsuit disclosure is the lawsuit. Matched on whole words, because "suit"
# inside "pursuit" and "merger" inside "emerger" are the class of mistake that
# made an early build flag Lockheed for the word "space".
LEGAL_TERMS = (
    "lawsuit", "litigation", "settlement", "settles", "consent decree",
    "injunction", "subpoena", "indictment", "plea agreement", "false claims",
    "qui tam", "debarment", "suspension", "investigation", "inquiry",
    "class action", "arbitration", "judgment", "verdict", "cfius",
    "deferred prosecution", "non-prosecution", "monitorship",
)
FINANCIAL_TERMS = (
    "earnings", "results", "quarter", "quarterly", "fiscal year", "dividend",
    "guidance", "outlook", "offering", "notes due", "credit facility",
    "refinancing", "refinance", "impairment", "restructuring", "going concern",
    "bankruptcy", "chapter 11", "acquisition", "merger", "divestiture",
    "tender offer", "share repurchase", "buyback", "recapitalization",
    "private placement", "convertible", "term loan", "covenant",
)


def _whole_word(term: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.I)


LEGAL_RX = [_whole_word(t) for t in LEGAL_TERMS]
FINANCIAL_RX = [_whole_word(t) for t in FINANCIAL_TERMS]


def classify(title: str, summary: str = "") -> str:
    """press | financial | legal, from the headline and standfirst."""
    blob = f"{title or ''} {summary or ''}"
    if any(rx.search(blob) for rx in LEGAL_RX):
        return "legal"
    if any(rx.search(blob) for rx in FINANCIAL_RX):
        return "financial"
    return "press"


def _text(node) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _strip_tags(value: str) -> str:
    """Feed summaries are HTML inside XML. Rules run over prose."""
    without = re.sub(r"<[^>]+>", " ", value or "")
    without = (without.replace("&nbsp;", " ").replace("&amp;", "&")
               .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
               .replace("&#39;", "'"))
    return " ".join(without.split())


class FeedItem:
    __slots__ = ("title", "link", "published", "summary")

    def __init__(self, title: str, link: str, published: str, summary: str) -> None:
        self.title, self.link = title, link
        self.published, self.summary = published, summary

    @property
    def kind(self) -> str:
        return classify(self.title, self.summary)


def parse_feed(body: str) -> list[FeedItem]:
    """Items from an RSS 2.0, RDF or Atom document. Never raises."""
    if not body or not FEED_BODY_RX.search(body[:2000]):
        return []
    try:
        root = ElementTree.fromstring(body.strip())
    except ElementTree.ParseError as exc:
        log.debug("feed did not parse: %s", exc)
        return []

    items: list[FeedItem] = []

    # RSS 2.0 and RDF both use <item>; namespaces vary, so match on the tag.
    for node in root.iter():
        tag = node.tag.split("}")[-1].lower()
        if tag == "item":
            link = _text(node.find("link")) or _text(node.find("guid"))
            items.append(FeedItem(
                title=_text(node.find("title")), link=link,
                published=(_text(node.find("pubDate"))
                           or _text(node.find("{http://purl.org/dc/elements/1.1/}date"))),
                summary=_strip_tags(_text(node.find("description")))))
        elif tag == "entry":
            link_node = node.find(f"{ATOM}link")
            link = (link_node.get("href") if link_node is not None else "") or ""
            summary = (_text(node.find(f"{ATOM}summary"))
                       or _text(node.find(f"{ATOM}content")))
            items.append(FeedItem(
                title=_text(node.find(f"{ATOM}title")), link=link,
                published=(_text(node.find(f"{ATOM}updated"))
                           or _text(node.find(f"{ATOM}published"))),
                summary=_strip_tags(summary)))

    return [i for i in items if i.title or i.link]


MIN_ITEMS_FOR_A_FEED = 2
MIN_MEDIAN_TITLE_WORDS = 4


def looks_like_releases(items: list[FeedItem]) -> bool:
    """Whether a feed carries announcements or a site's own furniture.

    A content management system will happily serve `/rss.xml` listing every
    page on the site — "Homepage", "Insights", "Climate Solutions" — with
    perfectly good publication dates attached. Observed on a real prime's
    domain, where it would have filled a contractor's document history with
    navigation and diffed it forever after.

    Dates do not separate the two; both have them, which is worth recording
    because it was the obvious first guess. Title length does: a release
    headline is a sentence with a verb in it, while a page title is a label.
    The median is used rather than the mean so one long title cannot carry a
    feed of labels.

    A heuristic, and biased towards rejecting: missing a feed costs coverage
    that the page crawl may still get, while accepting the wrong one puts
    furniture in front of a reviewer as though it were evidence.
    """
    if len(items) < MIN_ITEMS_FOR_A_FEED:
        return False
    lengths = sorted(len((i.title or "").split()) for i in items)
    median = lengths[len(lengths) // 2]
    return median >= MIN_MEDIAN_TITLE_WORDS


class FeedConnector:
    """Finds a company's release feed and turns entries into documents."""

    name = "feeds"
    MAX_PROBES = 6          # candidate paths tried when nothing is advertised
    MAX_FEEDS = 3
    MAX_ITEMS = 25
    # An entry with nothing but a headline is a link, not evidence. Rules need
    # prose to match against, and a bare title produces neither a useful
    # snippet nor a meaningful diff.
    MIN_TEXT_CHARS = 80

    def __init__(self, web) -> None:
        # The compliance layer. Not an HTTP client — that is the point.
        self.web = web
        self.feeds_found: dict[str, list[str]] = {}
        # Hosts that gave us releases. A host can refuse its pages to an
        # identified client and serve its feed to the same client in the same
        # run — ir.hii.com does exactly that — and a run that reported it as
        # unread would be describing the opposite of what happened.
        self.hosts_read: set[str] = set()

    # ------------------------------------------------------------ discovery
    def _feeds_on(self, base: str, paths=CANDIDATE_FEEDS) -> list[str]:
        """Feeds one host advertises, else the usual paths tried on it."""
        found: list[str] = []

        home = self.web._fetch(base)
        if home.html:
            for href in FEED_LINK_RX.findall(home.html):
                url = urljoin(base, href.strip())
                if url not in found and _same_host(base, url):
                    found.append(url)
        if found:
            return found

        probes = 0
        for path in paths:
            if probes >= self.MAX_PROBES:
                break
            probes += 1
            url = urljoin(base, path)
            body, _ = self.web.fetch_raw(url)
            if looks_like_releases(parse_feed(body)):
                return [url]
        return []

    def discover_feeds(self, domain: str) -> list[str]:
        """Feeds on the domain, then on the hosts a newsroom usually sits on.

        The subdomains are tried because that is where an investor-relations
        platform's feed normally lives — Q4 and Notified serve them from
        `investors.` — not because the primes tested have one. Measured
        against eight of them, none did: the newsroom subdomains that answer
        advertise no feed, and the IR subdomains either do not resolve or
        stall behind bot management. It is kept for the smaller contractors
        that make up most of what this tool screens, where an off-the-shelf IR
        site is the norm rather than a bespoke one.

        A name that does not resolve costs one cached DNS failure and is then
        skipped, so guessing five subdomains on a company that has none is
        cheap and says nothing in the run notes.
        """
        base = domain if domain.startswith("http") else f"https://{domain}"
        found = list(self._feeds_on(base))

        if not found:
            host = urlparse(base).netloc
            for sub in NEWSROOM_SUBDOMAINS:
                if len(found) >= self.MAX_FEEDS:
                    break
                candidate = f"https://{sub}.{host}"
                if not self.web.robots.host_exists(candidate):
                    continue
                found.extend(f for f in self._feeds_on(candidate, IR_CANDIDATE_FEEDS)
                             if f not in found)

        self.feeds_found[domain] = found[:self.MAX_FEEDS]
        return self.feeds_found[domain]

    # ---------------------------------------------------------------- read
    def read_feed(self, url: str, company: str = "") -> list[Document]:
        body, _ = self.web.fetch_raw(url)
        items = parse_feed(body)
        if not looks_like_releases(items):
            if items:
                log.info("%s parses but reads as site navigation, not releases "
                         "— ignoring", url)
            return []

        docs: list[Document] = []
        for item in items[:self.MAX_ITEMS]:
            text = " ".join(x for x in (item.title, item.summary) if x).strip()
            if len(text) < self.MIN_TEXT_CHARS:
                continue
            target = item.link or url
            kind = item.kind
            docs.append(Document(
                source="web",
                # One key per release, so a new announcement is a new document
                # rather than a diff against a rearranged page.
                key=f"feed:{_canonical(target)}",
                title=f"{company or urlparse(url).netloc} — {item.title}"[:300],
                url=target, text=text, published=item.published,
                doc_type=f"{kind}_release",
                meta={"release_kind": kind, "feed_url": url,
                      "domain": urlparse(url).netloc}))
        if docs:
            self.hosts_read.add(urlparse(url).netloc.lower())
        return docs

    def collect(self, domain: str, company: str = "") -> list[Document]:
        docs: list[Document] = []
        for feed_url in self.discover_feeds(domain):
            docs.extend(self.read_feed(feed_url, company))
        if docs:
            log.info("%s: %d release(s) from %d feed(s)", domain, len(docs),
                     len(self.feeds_found.get(domain, [])))
        return docs


def _same_host(base: str, url: str) -> bool:
    """Feeds may sit on a sibling host (investors.example.com); the
    registrable tail has to match so a syndication link does not walk us off
    to a wire service's own site."""
    a, b = urlparse(base).netloc.lower(), urlparse(url).netloc.lower()
    if not b:
        return False
    return ".".join(a.split(".")[-2:]) == ".".join(b.split(".")[-2:])


def _canonical(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}" or url
