"""Headless-browser rendering for pages a plain HTTP fetch cannot read.

`investors.<company>.com` is almost always a third-party investor-relations
platform (Q4 Inc, Notified, EQS). Those serve a JavaScript application shell and
either stall, 403, or return an empty container to a non-browser client. Both
hosts tested while building this — `investors.lockheedmartin.com` and
`investors.leidos.com` — timed out against `requests`. They are also the highest
value pages the tool watches, because a strategic-investment announcement lands
there before it reaches a filing.

This module is the fallback, not the default. Launching Chromium costs roughly a
second and ~300MB of RSS; running it against every page would make a screen
unaffordable. `WebWatchConnector` calls it only for hosts that have already
failed the cheap path.

Playwright is an optional dependency. Absent it, `available` is False and the
connector degrades to plain fetching — the screen still runs, and says in its
notes that IR pages went unread rather than implying they held nothing.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("foci.browser")

# The engine tokens are true — this is Chromium — and some JavaScript apps pick
# their code path from them. The identity appended after them says what is
# actually visiting and how to reach whoever runs it.
#
# An earlier version presented a bare desktop-Chrome string and launched with
# --disable-blink-features=AutomationControlled, whose only purpose is to hide
# automation from bot detection. A tool built for compliance work has no
# business evading a site's controls to read it, so both are gone: if a site
# turns this client away, that is recorded as reduced coverage, not worked around.
CHROMIUM_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Container necessities only. Nothing here alters what a site can observe.
LAUNCH_ARGS = [
    "--disable-dev-shm-usage",   # small /dev/shm in containers
    "--no-sandbox",              # required as non-root in Docker
]


def usable(response) -> bool:
    """Did the page itself load, as opposed to an error page about it?

    Without this, a CDN's 403 "Access Denied" page renders as ordinary HTML,
    clears the length threshold, and is stored and screened as though it were
    the contractor's own disclosure.
    """
    return response is not None and 200 <= int(response.status) < 400


def user_agent(identity: str) -> str:
    """Chromium engine tokens followed by the tool's own identity and contact."""
    identity = (identity or "").strip() or "foci-screen (contact not configured)"
    return f"{CHROMIUM_UA} {identity}"

# We want text, not pixels. Blocking these cuts render time roughly in half and
# avoids pulling megabytes of hero imagery on every press-release page.
BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}


class BrowserRenderer:
    """Renders a URL after its JavaScript has run.

    One browser process is shared across calls and launched lazily on first use.
    Not safe to share across threads — each worker holds its own instance.
    """

    def __init__(self, *, timeout: int = 25, enabled: bool = True,
                 identity: str = "") -> None:
        self.timeout = timeout
        self.user_agent = user_agent(identity)
        self._enabled = enabled
        self._playwright = None
        self._browser = None
        self._failed = False        # sticky: don't retry a launch that already failed
        self._lock = threading.Lock()

    # ------------------------------------------------------------ capability
    @property
    def available(self) -> bool:
        if not self._enabled or self._failed:
            return False
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_browser(self):
        """Launch Chromium on first use. Returns None if unavailable."""
        if self._browser is not None:
            return self._browser
        if not self.available:
            return None
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True, args=list(LAUNCH_ARGS))
            log.info("headless browser started")
            return self._browser
        except Exception as exc:
            # Most common cause: `playwright install chromium` was never run.
            log.warning("headless browser unavailable (%s: %s) — "
                        "JavaScript-gated pages will be skipped",
                        type(exc).__name__, exc)
            self._failed = True
            self._playwright = None
            self._browser = None
            return None

    # ---------------------------------------------------------------- render
    def render(self, url: str) -> str:
        """Return post-JavaScript HTML for `url`, or "" if it cannot be read."""
        with self._lock:
            browser = self._ensure_browser()
            if browser is None:
                return ""

            context = page = None
            try:
                context = browser.new_context(
                    user_agent=self.user_agent,
                    viewport={"width": 1366, "height": 900},
                    locale="en-US",
                    java_script_enabled=True,
                )
                context.set_default_timeout(self.timeout * 1000)
                page = context.new_page()
                page.route("**/*", _block_heavy_resources)

                response = page.goto(url, wait_until="domcontentloaded",
                                     timeout=self.timeout * 1000)
                if not usable(response):
                    log.info("browser refused for %s (HTTP %s)", url,
                             response.status if response is not None else "none")
                    return ""
                # The shell arrives at domcontentloaded; the press releases we
                # care about arrive with the XHR after it. Wait for quiet, but
                # treat the deadline as good enough rather than an error —
                # analytics beacons keep some of these pages permanently busy.
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                return page.content()
            except Exception as exc:
                log.info("browser render failed for %s (%s)", url, type(exc).__name__)
                return ""
            finally:
                for closeable in (page, context):
                    try:
                        if closeable is not None:
                            closeable.close()
                    except Exception:
                        pass

    def close(self) -> None:
        with self._lock:
            for obj, stop in ((self._browser, "close"), (self._playwright, "stop")):
                try:
                    if obj is not None:
                        getattr(obj, stop)()
                except Exception:
                    pass
            self._browser = None
            self._playwright = None


def _block_heavy_resources(route, request) -> None:
    try:
        if request.resource_type in BLOCKED_RESOURCES:
            route.abort()
        else:
            route.continue_()
    except Exception:
        pass
