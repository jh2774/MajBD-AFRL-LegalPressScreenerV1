"""What a run says it could not read.

These notes are the tool's account of its own blind spots, and a wrong one is
worse than none: a reviewer who reads "could not read ir.hii.com" concludes
the company's releases went unseen, when in the same run they were read from
that host's feed.
"""
from __future__ import annotations

import pytest

from foci_screen.pipeline import Screener


class _Stub:
    """Only what coverage_notes touches."""

    def __init__(self, **kw):
        self.skipped_js_hosts = kw.get("skipped_js_hosts", set())
        self.unreadable_hosts = kw.get("unreadable_hosts", set())
        self.skipped_robots = kw.get("skipped_robots", {})


class _Feeds:
    def __init__(self, hosts_read=()):
        self.hosts_read = set(hosts_read)


def notes_for(**kw):
    screener = Screener.__new__(Screener)       # no network, no construction
    screener.web = _Stub(**kw)
    screener.feeds = _Feeds(kw.pop("hosts_read", ()))
    return screener.coverage_notes()


def test_a_host_whose_feed_was_read_is_not_reported_as_unread():
    """The defect this exists for, seen in a real run against HII."""
    notes = notes_for(unreadable_hosts={"ir.hii.com"}, hosts_read={"ir.hii.com"})
    joined = " ".join(notes)

    assert "Could not read ir.hii.com" not in joined
    assert "Read the release feed on ir.hii.com" in joined
    assert "not its pages" in joined


def test_a_host_that_gave_nothing_is_still_reported():
    notes = notes_for(unreadable_hosts={"investors.example.com"})
    joined = " ".join(notes)
    assert "Could not read investors.example.com" in joined
    assert "does not disguise itself" in joined


def test_the_two_kinds_are_reported_separately():
    notes = notes_for(unreadable_hosts={"ir.hii.com", "investors.example.com"},
                      hosts_read={"ir.hii.com"})
    joined = " ".join(notes)
    assert "Could not read investors.example.com" in joined
    assert "Read the release feed on ir.hii.com" in joined
    assert "Could not read ir.hii.com" not in joined


def test_a_subdomain_that_does_not_exist_is_not_reduced_coverage():
    """Probing five guesses per company would otherwise bury the refusals
    that mean something under hosts nobody claimed existed."""
    notes = notes_for(skipped_robots={
        "https://ir.example.com": "host does not exist",
        "https://news.example.com": "host does not exist",
    })
    assert notes == []


def test_a_real_refusal_is_kept():
    notes = notes_for(skipped_robots={
        "https://investors.example.com/news": "disallowed by robots.txt"})
    joined = " ".join(notes)
    assert "investors.example.com" in joined
    assert "by the site's own request" in joined


def test_an_unreachable_host_is_distinguished_from_a_refusal():
    notes = notes_for(skipped_robots={
        "https://investors.example.com/news": "robots.txt unreachable (no response)"})
    assert "could not be read and are treated as a refusal" in " ".join(notes)


def test_a_clean_run_says_nothing():
    assert notes_for() == []


@pytest.mark.parametrize("missing", ["feeds"])
def test_notes_survive_a_screener_without_the_feed_connector(missing):
    """Defensive: the note must not become an AttributeError on any path that
    builds a Screener differently."""
    screener = Screener.__new__(Screener)
    screener.web = _Stub(unreadable_hosts={"investors.example.com"})
    assert "investors.example.com" in " ".join(screener.coverage_notes())
