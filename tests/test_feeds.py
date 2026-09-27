"""Release feeds: the legitimate route to company announcements.

Offline. The compliance layer is stubbed, not bypassed — these assert that the
connector asks it for everything, that a feed is parsed into one document per
release, and that releases are sorted into press, financial and legal.
"""
from __future__ import annotations

import pytest

from foci_screen.connectors.feeds import (
    FeedConnector,
    classify,
    looks_like_releases,
    parse_feed,
)

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Meridian Photonics — News</title>
  <item>
    <title>Meridian Photonics reports fourth quarter and full year results</title>
    <link>https://meridian.example/news/q4-results</link>
    <pubDate>Mon, 15 Sep 2026 12:00:00 GMT</pubDate>
    <description>&lt;p&gt;Revenue of $412 million, and the board declared a
      dividend payable in October.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Meridian settles False Claims Act matter with the Department of Justice</title>
    <link>https://meridian.example/news/fca-settlement</link>
    <pubDate>Tue, 16 Sep 2026 09:30:00 GMT</pubDate>
    <description>The company has reached a settlement resolving allegations
      relating to pricing on a defence contract.</description>
  </item>
  <item>
    <title>Meridian Photonics opens a new facility in Huntsville</title>
    <link>https://meridian.example/news/huntsville</link>
    <pubDate>Wed, 17 Sep 2026 08:00:00 GMT</pubDate>
    <description>The site will employ 120 people building photonic sensing
      subsystems for defence and commercial customers.</description>
  </item>
  <item>
    <title>Short one</title>
    <link>https://meridian.example/news/short</link>
    <description>Too brief.</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Corp</title>
  <entry>
    <title>Example Corp completes acquisition of Orbital Optics</title>
    <link href="https://example.test/press/acquisition"/>
    <updated>2026-09-20T10:00:00Z</updated>
    <summary>The transaction closed following regulatory clearance, and the
      business will operate as a wholly owned subsidiary.</summary>
  </entry>
</feed>"""

HOME_WITH_FEED = """<html><head>
  <link rel="alternate" type="application/rss+xml" title="News"
        href="/news/rss.xml">
</head><body>Corporate homepage.</body></html>"""


class StubWeb:
    """Stands in for WebWatchConnector, recording what was asked for."""

    def __init__(self, pages=None, raw=None):
        self.pages = pages or {}
        self.raw = raw or {}
        self.raw_calls: list[str] = []
        self.fetch_calls: list[str] = []

    class _Fetched:
        def __init__(self, html):
            self.html, self.text, self.how = html, html, "http" if html else ""

    def _fetch(self, url):
        self.fetch_calls.append(url)
        return self._Fetched(self.pages.get(url, ""))

    def fetch_raw(self, url):
        self.raw_calls.append(url)
        return (self.raw.get(url, ""), "application/rss+xml")


# ------------------------------------------------------------------ parsing

def test_rss_items_become_one_entry_each():
    items = parse_feed(RSS)
    assert len(items) == 4
    assert items[0].title.startswith("Meridian Photonics reports")
    assert items[0].link == "https://meridian.example/news/q4-results"


def test_atom_entries_are_read_too():
    items = parse_feed(ATOM)
    assert len(items) == 1
    assert items[0].link == "https://example.test/press/acquisition"
    assert "regulatory clearance" in items[0].summary


def test_html_inside_a_summary_is_stripped():
    """Summaries are HTML inside XML; rules run over prose."""
    summary = parse_feed(RSS)[0].summary
    assert "<p>" not in summary
    assert "Revenue of $412 million" in summary


def test_a_page_that_is_not_a_feed_yields_nothing():
    assert parse_feed("<html><body>Not a feed</body></html>") == []
    assert parse_feed("") == []


def test_malformed_xml_does_not_raise():
    assert parse_feed("<rss><channel><item><title>unclosed") == []


# ----------------------------------------------------------- classification

@pytest.mark.parametrize("title,expected", [
    ("Fourth quarter results and dividend declared", "financial"),
    ("Company completes acquisition of a rival", "financial"),
    ("Board approves share repurchase programme", "financial"),
    ("Settles False Claims Act matter with DOJ", "legal"),
    ("Receives subpoena relating to export controls", "legal"),
    ("CFIUS clears investment by overseas shareholder", "legal"),
    ("Opens a new facility in Huntsville", "press"),
    ("Names a new chief technology officer", "press"),
])
def test_releases_are_sorted_by_what_they_are(title, expected):
    assert classify(title) == expected


def test_a_legal_matter_outranks_the_financial_wording_around_it():
    """An announcement is often both. The lawsuit is the reportable half."""
    assert classify("Q3 results; company settles litigation over pricing") == "legal"


def test_classification_matches_whole_words_only():
    """"suit" inside "pursuit" is how a screen earns a reputation for crying
    wolf — the same mistake that once flagged a prime for "space" in "SPAC"."""
    assert classify("In pursuit of new markets") == "press"
    assert classify("An emerger of ideas") == "press"


# ------------------------------------------- telling releases from furniture

# Observed live on a prime's domain: a CMS serving every page on the site as
# an RSS feed, complete with real publication dates.
SITE_INVENTORY = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Example — site</title>
  <item><title>Homepage</title><link>https://example.test/</link>
    <pubDate>Thu, 19 Mar 2026 14:49:58 +0000</pubDate>
    <description>Homepage editor1 Thu, 03/19/2026 - 10:49 Lead content</description></item>
  <item><title>Insights</title><link>https://example.test/insights</link>
    <pubDate>Tue, 14 Jan 2025 17:25:07 +0000</pubDate>
    <description>Insights editor2 Tue, 01/14/2025 - 12:25 Lead where leaders
      share</description></item>
  <item><title>Climate Solutions</title><link>https://example.test/climate</link>
    <pubDate>Wed, 05 Jul 2023 19:52:16 +0000</pubDate>
    <description>Climate Solutions editor3 Wed, 07/05/2023 Lead global
      challenges</description></item>
  <item><title>News Releases</title><link>https://example.test/news</link>
    <pubDate>Tue, 04 May 2021 13:41:47 +0000</pubDate>
    <description>News Releases editor4 Tue, 05/04/2021 Lead find latest
      news</description></item>
</channel></rss>"""


def test_a_feed_of_page_titles_is_not_a_release_feed():
    """The failure this guard exists for, taken from a live domain: a content
    management system serving the whole site as RSS would have filled a
    contractor's history with navigation and diffed it forever."""
    assert parse_feed(SITE_INVENTORY), "it does parse — that is the problem"
    assert looks_like_releases(parse_feed(SITE_INVENTORY)) is False


def test_real_release_headlines_are_accepted():
    assert looks_like_releases(parse_feed(RSS)) is True
    assert looks_like_releases(parse_feed(ATOM)) is False, "one item is not a feed"


def test_dates_do_not_separate_the_two():
    """Recorded because it was the obvious first guess and it is wrong: the
    site-inventory feed carries perfectly good publication dates."""
    inventory = parse_feed(SITE_INVENTORY)
    assert all(i.published for i in inventory)


def test_a_site_inventory_feed_is_not_adopted(caplog):
    web = StubWeb(pages={"https://example.test": "<html></html>"},
                  raw={"https://example.test/rss": SITE_INVENTORY})
    assert FeedConnector(web).discover_feeds("example.test") == []


def test_reading_an_inventory_feed_directly_yields_nothing():
    web = StubWeb(raw={"https://example.test/rss.xml": SITE_INVENTORY})
    assert FeedConnector(web).read_feed("https://example.test/rss.xml") == []


# -------------------------------------------------------------- the connector

def test_a_feed_advertised_by_the_homepage_is_used():
    web = StubWeb(pages={"https://meridian.example": HOME_WITH_FEED},
                  raw={"https://meridian.example/news/rss.xml": RSS})
    found = FeedConnector(web).discover_feeds("meridian.example")
    assert found == ["https://meridian.example/news/rss.xml"]


def test_candidate_paths_are_tried_when_nothing_is_advertised():
    web = StubWeb(pages={"https://meridian.example": "<html>no feed link</html>"},
                  raw={"https://meridian.example/rss": RSS})
    found = FeedConnector(web).discover_feeds("meridian.example")
    assert found == ["https://meridian.example/rss"]


def test_probing_stops_at_the_budget():
    """A site with no feed must cost a run a handful of requests, not a sweep
    of every path anyone has ever used."""
    web = StubWeb(pages={"https://nothing.example": "<html></html>"})
    conn = FeedConnector(web)
    assert conn.discover_feeds("nothing.example") == []
    assert len(web.raw_calls) <= conn.MAX_PROBES


def test_a_feed_on_another_company_is_not_followed():
    """A syndication link must not walk the crawler off to a wire service."""
    page = ('<link rel="alternate" type="application/rss+xml" '
            'href="https://prnewswire.example/rss/everything.xml">')
    web = StubWeb(pages={"https://meridian.example": page})
    assert FeedConnector(web).discover_feeds("meridian.example") == []


def test_each_release_becomes_its_own_document():
    web = StubWeb(raw={"https://meridian.example/rss": RSS})
    docs = FeedConnector(web).read_feed("https://meridian.example/rss",
                                        company="MERIDIAN PHOTONICS INC")

    assert len(docs) == 3, "the headline-only item has too little to match on"
    keys = [d.key for d in docs]
    assert len(set(keys)) == 3, "one key per release, so a new one reads as new"
    assert all(k.startswith("feed:") for k in keys)
    assert {d.meta["release_kind"] for d in docs} == {"financial", "legal", "press"}


def test_the_document_carries_the_release_kind_and_date():
    web = StubWeb(raw={"https://meridian.example/rss": RSS})
    docs = FeedConnector(web).read_feed("https://meridian.example/rss")
    legal = [d for d in docs if d.meta["release_kind"] == "legal"][0]

    assert legal.doc_type == "legal_release"
    assert legal.published.startswith("Tue, 16 Sep 2026")
    assert legal.url == "https://meridian.example/news/fca-settlement"


def test_everything_goes_through_the_compliance_layer():
    """No HTTP client of its own. A connector that fetched directly would
    bypass robots.txt and hammer the hosts that have already refused us."""
    web = StubWeb(pages={"https://meridian.example": HOME_WITH_FEED},
                  raw={"https://meridian.example/news/rss.xml": RSS})
    conn = FeedConnector(web)
    conn.collect("meridian.example", company="MERIDIAN")

    assert not hasattr(conn, "http")
    assert web.fetch_calls and web.raw_calls
    assert all(u.startswith("https://meridian.example") for u in web.raw_calls)


def test_a_refused_feed_is_simply_no_documents():
    """robots.txt disallowing it returns ("", "") — not an error, not a retry."""
    web = StubWeb(pages={"https://meridian.example": HOME_WITH_FEED}, raw={})
    assert FeedConnector(web).collect("meridian.example") == []
