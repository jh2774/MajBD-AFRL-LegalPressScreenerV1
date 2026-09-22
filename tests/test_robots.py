"""robots.txt compliance and an identifiable browser.

The crawler reads contractors' own websites without being asked. These pin down
that it obeys a site's stated rules on both fetch paths, that it follows RFC 9309
when those rules cannot be read, and that it says what it is.
"""
from __future__ import annotations

from foci_screen.connectors import browser as browser_module
from foci_screen.connectors.robots import MAX_CRAWL_DELAY, RobotsPolicy
from foci_screen.connectors.webwatch import WebWatchConnector

PAGE = ("<html><body><main>" + ("Acme Corp announced a new facility today. " * 60)
        + "</main></body></html>")
SHELL = '<html><body><div id="root"></div></body></html>'


class StubHttp:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        return self.responses.get(url, {"status": 404, "text": ""})


class StubBrowser:
    available = True

    def __init__(self, pages: dict) -> None:
        self.pages = pages
        self.rendered: list[str] = []

    def render(self, url):
        self.rendered.append(url)
        return self.pages.get(url, "")


def robots(body: str, status: int = 200) -> dict:
    return {"status": status, "text": body}


# ------------------------------------------------------------------ policy

def test_disallowed_page_is_never_requested():
    http = StubHttp({
        "https://acme.com/robots.txt": robots("User-agent: *\nDisallow: /investors\n"),
        "https://acme.com/investors": {"status": 200, "text": PAGE},
    })
    web = WebWatchConnector(http, browser=None)

    assert web.fetch("https://acme.com/investors") is None
    assert "https://acme.com/investors" not in http.calls
    assert web.skipped_robots["https://acme.com/investors"] == "disallowed by robots.txt"


def test_disallowed_page_is_not_fetched_through_the_browser_either():
    """One entry point for both paths, so the fallback cannot route around a refusal."""
    http = StubHttp({
        "https://investors.acme.com/robots.txt": robots("User-agent: *\nDisallow: /\n"),
    })
    browser = StubBrowser({"https://investors.acme.com/news": PAGE})
    web = WebWatchConnector(http, browser=browser)

    assert web.fetch("https://investors.acme.com/news") is None
    assert browser.rendered == []


def test_allowed_page_is_fetched():
    http = StubHttp({
        "https://acme.com/robots.txt": robots("User-agent: *\nDisallow: /admin\n"),
        "https://acme.com/news": {"status": 200, "text": PAGE},
    })
    doc = WebWatchConnector(http).fetch("https://acme.com/news")
    assert doc is not None


def test_rules_addressed_to_this_tool_are_obeyed():
    http = StubHttp({
        "https://acme.com/robots.txt": robots(
            "User-agent: foci-screen\nDisallow: /\n\nUser-agent: *\nAllow: /\n"),
        "https://acme.com/news": {"status": 200, "text": PAGE},
    })
    assert WebWatchConnector(http).fetch("https://acme.com/news") is None


def test_missing_robots_file_allows_crawling():
    """RFC 9309: 4xx means no rules were published."""
    http = StubHttp({"https://acme.com/news": {"status": 200, "text": PAGE}})
    assert WebWatchConnector(http).fetch("https://acme.com/news") is not None


def test_forbidden_robots_file_allows_crawling():
    """RFC 9309 names 401 and 403 explicitly: still 'unavailable', not 'disallowed'."""
    http = StubHttp({
        "https://acme.com/robots.txt": robots("", status=403),
        "https://acme.com/news": {"status": 200, "text": PAGE},
    })
    assert WebWatchConnector(http).fetch("https://acme.com/news") is not None


def test_unreachable_robots_file_disallows_everything():
    """RFC 9309: when the rules exist but cannot be read, assume complete
    disallow — not "it probably would have said yes"."""
    for status in (500, 503, 0):
        http = StubHttp({
            "https://acme.com/robots.txt": robots("", status=status),
            "https://acme.com/news": {"status": 200, "text": PAGE},
        })
        web = WebWatchConnector(http)

        assert web.fetch("https://acme.com/news") is None, status
        assert "https://acme.com/news" not in http.calls
        assert "unreachable" in web.skipped_robots["https://acme.com/news"]


def test_robots_file_is_fetched_once_per_host():
    http = StubHttp({
        "https://acme.com/robots.txt": robots("User-agent: *\nAllow: /\n"),
        "https://acme.com/a": {"status": 200, "text": PAGE},
        "https://acme.com/b": {"status": 200, "text": PAGE},
    })
    web = WebWatchConnector(http)
    web.fetch("https://acme.com/a")
    web.fetch("https://acme.com/b")

    assert http.calls.count("https://acme.com/robots.txt") == 1


def test_subdomains_have_their_own_rules():
    http = StubHttp({
        "https://acme.com/robots.txt": robots("User-agent: *\nAllow: /\n"),
        "https://investors.acme.com/robots.txt": robots("User-agent: *\nDisallow: /\n"),
        "https://acme.com/news": {"status": 200, "text": PAGE},
        "https://investors.acme.com/news": {"status": 200, "text": PAGE},
    })
    web = WebWatchConnector(http)

    assert web.fetch("https://acme.com/news") is not None
    assert web.fetch("https://investors.acme.com/news") is None


def test_discovery_respects_robots_too():
    http = StubHttp({
        "https://acme.com/robots.txt": robots("User-agent: *\nDisallow: /\n"),
        "https://acme.com": {"status": 200, "text": PAGE},
    })
    assert WebWatchConnector(http).discover_pages("acme.com") == []
    assert "https://acme.com" not in http.calls


# ------------------------------------------------------------- crawl-delay

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_crawl_delay_is_honoured_between_requests_to_one_host():
    clock = FakeClock()
    http = StubHttp({"https://acme.com/robots.txt":
                     robots("User-agent: *\nCrawl-delay: 5\n")})
    policy = RobotsPolicy(http, clock=clock, sleep=clock.sleep)

    policy.wait_turn("https://acme.com/a")      # first request: no wait
    clock.now += 2
    policy.wait_turn("https://acme.com/b")      # 2s later: wait the other 3

    assert clock.slept == [3.0]


def test_excessive_crawl_delay_skips_the_host_rather_than_crawling_faster():
    http = StubHttp({"https://acme.com/robots.txt":
                     robots(f"User-agent: *\nCrawl-delay: {MAX_CRAWL_DELAY * 6:g}\n")})
    permitted, reason = RobotsPolicy(http).allowed("https://acme.com/news")

    assert permitted is False
    assert "Crawl-delay" in reason


# ------------------------------------------------------------ run notes

def test_robots_skips_are_reported_in_the_run_notes(tmp_path):
    """A site that asked not to be crawled has not told us it has nothing to disclose."""
    from foci_screen.pipeline import Screener
    from foci_screen.store import Store

    class Cfg:
        uspto_api_key = ""
        sam_api_key = ""

    http = StubHttp({
        "https://investors.acme.com/robots.txt": robots("User-agent: *\nDisallow: /\n"),
        "https://acme.com/robots.txt": robots("", status=503),
    })
    store = Store(str(tmp_path / "notes.db"))
    try:
        screener = Screener(Cfg(), http, store)
        for url in ("https://investors.acme.com/news", "https://investors.acme.com/events",
                    "https://acme.com/news"):
            screener.web.fetch(url)
        notes = screener.coverage_notes()
    finally:
        store.close()

    investors = next(n for n in notes if "investors.acme.com" in n)
    assert "2 page(s): disallowed by robots.txt" in investors
    main_site = next(n for n in notes if n.startswith("Did not read acme.com"))
    assert "unreachable" in main_site


# ----------------------------------------------------- refusal is not content

class Resp:
    def __init__(self, status):
        self.status = status


def test_error_responses_are_not_usable_renders():
    """Regression: a CDN's 403 page rendered as ordinary HTML, cleared the length
    threshold, and would have been stored as the contractor's own disclosure."""
    assert browser_module.usable(Resp(200))
    assert browser_module.usable(Resp(304))
    for status in (401, 403, 404, 429, 500, 503):
        assert not browser_module.usable(Resp(status)), status
    assert not browser_module.usable(None)


def test_host_that_refuses_the_browser_is_reported_not_silently_dropped():
    url = "https://investors.acme.com/news"
    http = StubHttp({url: {"status": 0, "text": "", "error": "ReadTimeout"}})
    web = WebWatchConnector(http, browser=StubBrowser({}))    # refuses: renders ""

    assert web.fetch(url) is None
    assert web.unreadable_hosts == {"investors.acme.com"}
    assert web.skipped_js_hosts == set(), "a browser was available; this is a refusal"


def test_thin_page_that_http_did_read_is_not_marked_unreadable():
    url = "https://acme.com/about"
    thin = "<html><body><main>" + ("Short about page. " * 20) + "</main></body></html>"
    http = StubHttp({url: {"status": 200, "text": thin}})
    web = WebWatchConnector(http, browser=StubBrowser({}))

    web.fetch(url)
    assert web.unreadable_hosts == set()


def test_unreadable_host_note_points_at_where_else_to_look(tmp_path):
    from foci_screen.pipeline import Screener
    from foci_screen.store import Store

    class Cfg:
        uspto_api_key = ""
        sam_api_key = ""

    store = Store(str(tmp_path / "n.db"))
    try:
        screener = Screener(Cfg(), StubHttp({}), store, browser=StubBrowser({}))
        screener.web.unreadable_hosts = {"investors.acme.com"}
        note = screener.coverage_notes()[0]
    finally:
        store.close()

    assert "investors.acme.com" in note
    assert "does not disguise itself" in note
    assert "8-K" in note


# ------------------------------------------------------------------ browser

def test_browser_does_not_hide_that_it_is_automated():
    assert not any("AutomationControlled" in arg for arg in browser_module.LAUNCH_ARGS)


def test_browser_user_agent_identifies_the_tool_and_its_contact():
    renderer = browser_module.BrowserRenderer(
        enabled=False, identity="foci-screen/0.7 (ops@example.org)")

    assert renderer.user_agent.endswith("foci-screen/0.7 (ops@example.org)")
    assert "Chrome/" in renderer.user_agent    # engine tokens kept: it is Chromium


def test_browser_user_agent_never_goes_out_anonymous():
    assert "foci-screen" in browser_module.user_agent("")
