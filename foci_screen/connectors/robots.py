"""robots.txt compliance for contractor websites.

Government APIs are built for programmatic access and are out of scope here. A
contractor's newsroom is not: it is a website we crawl, on a schedule, without
being asked. This tool exists to support compliance work, and it should not be
the thing that ignores a site's stated rules to do it.

Semantics follow RFC 9309 rather than whatever is most convenient:

  * 2xx       parse and obey.
  * 4xx       no usable rules — the site has not restricted crawling, so allow.
              (401/403 on robots.txt included; the RFC is explicit about that.)
  * 5xx, or   the rules exist but cannot be read — assume everything is
    no answer disallowed until they can be. Crawling a site whose robots.txt is
              down, on the theory that it probably would have said yes, is
              exactly the guess the RFC rules out.

`Crawl-delay` is not in the RFC but is widely used, and honouring it costs a
few seconds per host. A delay longer than `MAX_CRAWL_DELAY` would stall the whole
run, so such a host is skipped for the run rather than crawled faster than it
asked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

log = logging.getLogger("foci.robots")

# The product token in the User-Agent. Rules addressed to it are obeyed.
AGENT = "foci-screen"
MAX_CRAWL_DELAY = 10.0
ROBOTS_TIMEOUT = 8


@dataclass
class HostRules:
    verdict: str                     # "parsed" | "allow_all" | "deny_all"
    reason: str = ""
    parser: RobotFileParser | None = None
    crawl_delay: float = 0.0
    last_fetch: float | None = None


# What a DNS failure looks like coming back through requests/urllib3. Matched
# on the text because the client flattens the exception to a string, and a
# false negative here only costs the old, vaguer reason.
_DNS_MARKERS = (
    "nameresolutionerror", "failed to resolve", "getaddrinfo failed",
    "name or service not known", "nodename nor servname",
    "temporary failure in name resolution",
)


def _is_dns_failure(error: str) -> bool:
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _DNS_MARKERS)


class RobotsPolicy:
    """Answers "may we fetch this URL?", fetching each host's robots.txt once."""

    def __init__(self, http, agent: str = AGENT,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self.http = http
        self.agent = agent
        self._clock = clock
        self._sleep = sleep
        self._hosts: dict[str, HostRules] = {}

    def rules_for(self, url: str) -> HostRules:
        parsed = urlparse(url)
        host = f"{parsed.scheme or 'https'}://{parsed.netloc.lower()}"
        if host not in self._hosts:
            self._hosts[host] = self._load(host)
        return self._hosts[host]

    def _load(self, host: str) -> HostRules:
        resp = self.http.get(f"{host}/robots.txt", timeout=ROBOTS_TIMEOUT, max_retries=1)
        status = int(resp.get("status") or 0)

        if 200 <= status < 300:
            parser = RobotFileParser()
            parser.parse((resp.get("text") or "").splitlines())
            delay = parser.crawl_delay(self.agent)
            return HostRules("parsed", parser=parser,
                             crawl_delay=float(delay) if delay else 0.0)
        if 400 <= status < 500:
            return HostRules("allow_all", reason=f"robots.txt returned {status}")

        # A name that does not resolve is not a server declining to answer —
        # there is no server. Both arrive here as status 0, and calling them
        # the same thing puts "refused by robots.txt" in a run's coverage notes
        # for a subdomain that was only ever a guess, which reads as a site
        # shutting us out. The fetch is still refused; only the reason differs,
        # and the reason is what a reader acts on.
        if _is_dns_failure(resp.get("error") or ""):
            return HostRules("no_such_host", reason="host does not exist")

        log.info("%s/robots.txt unreachable (status %s) — treating the host as "
                 "disallowed for this run", host, status or "no response")
        return HostRules("deny_all",
                         reason=f"robots.txt unreachable ({status or 'no response'})")

    def host_exists(self, url: str) -> bool:
        """False when the name does not resolve. Cached with the rules, so
        asking costs one lookup however many paths are tried on it."""
        return self.rules_for(url).verdict != "no_such_host"

    def allowed(self, url: str) -> tuple[bool, str]:
        """(may_fetch, reason). The reason is for the run notes when refused."""
        rules = self.rules_for(url)
        if rules.verdict == "allow_all":
            return True, ""
        if rules.verdict in ("deny_all", "no_such_host"):
            return False, rules.reason
        if rules.crawl_delay > MAX_CRAWL_DELAY:
            return False, (f"Crawl-delay of {rules.crawl_delay:g}s exceeds the "
                           f"{MAX_CRAWL_DELAY:g}s this tool will wait")
        if not rules.parser.can_fetch(self.agent, url):
            return False, "disallowed by robots.txt"
        return True, ""

    def wait_turn(self, url: str) -> None:
        """Sleep out any remaining Crawl-delay for this host, then record the fetch."""
        rules = self.rules_for(url)
        if rules.crawl_delay and rules.last_fetch is not None:
            remaining = rules.crawl_delay - (self._clock() - rules.last_fetch)
            if remaining > 0:
                self._sleep(remaining)
        rules.last_fetch = self._clock()
