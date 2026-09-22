"""Report what the website connector can and cannot read, and why.

    python tools/check_web_coverage.py
    python tools/check_web_coverage.py https://example.com/news ...

Run after changing `connectors/webwatch.py`, `connectors/robots.py` or
`connectors/browser.py`. Live network; a diagnostic, not a test.

Every request goes through `WebWatchConnector`, which enforces robots.txt and
identifies itself. This script deliberately never calls the HTTP client or the
browser directly: a diagnostic that bypasses the compliance layer would hit, over
and over, exactly the hosts that have refused this tool.

It replaces `check_ir_pages.py`, which did bypass it, and which counted a refused
investor-relations host as a regression to fix. A refusal is not a regression.
The only failures here are:

  * an error page stored as though it were content, and
  * a site that permits crawling and still produced nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from foci_screen.config import get_config  # noqa: E402
from foci_screen.connectors.browser import BrowserRenderer  # noqa: E402
from foci_screen.connectors.webwatch import WebWatchConnector  # noqa: E402
from foci_screen.httpclient import HttpClient  # noqa: E402

DEFAULT_TARGETS = [
    "https://www.lockheedmartin.com/en-us/news.html",
    "https://investors.lockheedmartin.com/",
    "https://investors.leidos.com/",
]

ERROR_PAGE_MARKERS = ("access denied", "you don't have permission", "errors.edgesuite.net",
                      "request blocked", "attention required")


def main(argv: list[str]) -> int:
    cfg = get_config()
    browser = BrowserRenderer(timeout=cfg.browser_timeout, enabled=cfg.browser_enabled,
                              identity=cfg.user_agent)
    web = WebWatchConnector(HttpClient(cfg), browser=browser)
    targets = argv or DEFAULT_TARGETS

    print(f"identity: {browser.user_agent}")
    print(f"browser available: {browser.available}\n")
    print(f"{'url':<56} {'robots':<10} {'chars':>6}  outcome")
    print("-" * 100)

    failures = 0
    try:
        for url in targets:
            permitted, reason = web.robots.allowed(url)
            verdict = web.robots.rules_for(url).verdict
            doc = web.fetch(url, kind="press", company="coverage check")
            host = urlparse(url).netloc.lower()

            if doc is not None:
                if any(m in doc.text.lower() for m in ERROR_PAGE_MARKERS):
                    outcome = "FAIL: error page stored as content"
                    failures += 1
                else:
                    outcome = f"read via {doc.meta['rendered_with']}"
                chars = len(doc.text)
            else:
                chars = 0
                if not permitted:
                    outcome = f"not read: {reason}"
                elif host in web.unreadable_hosts:
                    outcome = "not read: site refused the identified browser"
                elif host in web.skipped_js_hosts:
                    outcome = "not read: needs a browser and none is installed"
                else:
                    outcome = "FAIL: crawling permitted but nothing usable came back"
                    failures += 1
            print(f"{url:<56} {verdict:<10} {chars:>6}  {outcome}")
    finally:
        browser.close()

    print()
    if failures:
        print(f"{failures} failure(s).")
        return 1
    print("No failures. Hosts marked 'not read' are reported in run notes, not worked around.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
