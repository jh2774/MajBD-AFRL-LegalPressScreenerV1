"""Polite, cached HTTP client.

Government endpoints throttle aggressively and several of them (SEC in
particular) will ban a User-Agent that doesn't carry a contact address. This
wraps requests with rate limiting, retry/backoff, and an on-disk response
cache so re-running a screen doesn't re-hammer the same URLs.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:  # corporate TLS-inspecting proxies need the OS trust store
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - optional
    pass

log = logging.getLogger("foci.http")

# Counted across every client in the process: most are made for one request
# and thrown away, so a per-client count would never reach the threshold.
_cache_writes = itertools.count(1)
PRUNE_EVERY = 25

RETRY_STATUS = (429, 500, 502, 503, 504)
BACKOFF_BASE = 1.5          # seconds; doubled each attempt, plus jitter
MAX_WAIT = 20.0             # the longest wait between attempts, even if asked for more
BREAK_AFTER = 4             # failed attempts in a row before a host is rested
BREAK_SECONDS = 120.0       # how long it is rested
MAX_PAUSE = 600.0           # however long it asks
STALE_MAX_SECONDS = 7 * 86400   # an old copy stands in for a source this long

# Per process, shared by every client: most clients are made for one screen,
# and a host that is down is down for all of them.
_breaker: dict[str, dict] = {}
_breaker_lock = threading.Lock()


class RateLimiter:
    def __init__(self, qps: float) -> None:
        self._min_interval = 1.0 / qps if qps > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class HttpClient:
    def __init__(self, config) -> None:
        self.cfg = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self.limiter = RateLimiter(config.rate_limit_qps)
        self.cache_dir = Path(config.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"hits": 0, "misses": 0, "errors": 0}
        # Per host: failed attempts, the last error, old copies used, requests
        # not tried. What a run's notes say about sources that did not answer.
        self.failures: dict[str, dict] = {}
        # time.monotonic() after which nothing is retried or started (0: none).
        self.deadline = 0.0

    # ------------------------------------------------------------------ cache
    def _cache_path(self, method: str, url: str, body: Any) -> Path:
        raw = f"{method}|{url}|{json.dumps(body, sort_keys=True) if body else ''}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path, ttl: int | None = None) -> dict | None:
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > (self.cfg.cache_ttl_seconds if ttl is None else ttl):
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def prune_cache(self) -> int:
        """Delete expired responses, then the oldest, until under the size cap.

        Nothing else ever removed a cache file: one past its lifetime was
        ignored when read and left where it was. On a disk that is an untidy
        directory. On Cloud Run there is no disk — files in the container are
        held in the instance's memory — so it is a leak that ends with the
        instance being killed for exceeding its limit, mid-screen.
        """
        limit = max(1, int(getattr(self.cfg, "cache_max_mb", 256) or 256)) * 1024 * 1024
        # Kept past their lifetime for a while: an old copy stands in for a
        # source that does not answer (`_stale`).
        keep_for = max(self.cfg.cache_ttl_seconds, STALE_MAX_SECONDS)
        now, kept, removed = time.time(), [], 0
        for path in self.cache_dir.glob("*.json"):
            try:
                stat = path.stat()
                if now - stat.st_mtime > keep_for:
                    path.unlink()
                    removed += 1
                else:
                    kept.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue            # another thread got there first
        total = sum(size for _, size, _ in kept)
        for _, size, path in sorted(kept):
            if total <= limit:
                break
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            total -= size
        return removed

    # ----------------------------------------------------------- source health
    def _host(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    def _failed(self, host: str, error: str, retry_after: float = 0.0) -> None:
        """Count a failed attempt. After `BREAK_AFTER` in a row the host is
        left alone for a while, so one site that is down cannot spend a whole
        run's time on retries — on Vercel a run has four minutes."""
        with _breaker_lock:
            state = _breaker.setdefault(host, {"fails": 0, "open_until": 0.0})
            state["fails"] += 1
            if state["fails"] >= BREAK_AFTER or retry_after > MAX_WAIT:
                pause = min(MAX_PAUSE, max(BREAK_SECONDS, retry_after))
                state["open_until"] = time.monotonic() + pause
        seen = self.failures.setdefault(host, {"count": 0, "last_error": "", "stale": 0,
                                               "skipped": 0})
        seen["count"] += 1
        seen["last_error"] = error

    def _succeeded(self, host: str) -> None:
        with _breaker_lock:
            _breaker.pop(host, None)

    def _resting(self, host: str) -> bool:
        with _breaker_lock:
            state = _breaker.get(host)
            return bool(state and state["open_until"] > time.monotonic())

    def failure_count(self) -> int:
        """Failed and skipped requests so far, all hosts: a step that raised it
        did not get everything it asked for."""
        return sum(f["count"] + f["skipped"] for f in self.failures.values())

    def failed_hosts_since(self, before: dict[str, int]) -> list[str]:
        return [h for h, f in self.failures.items()
                if f["count"] + f["skipped"] > before.get(h, 0)]

    def failure_marks(self) -> dict[str, int]:
        return {h: f["count"] + f["skipped"] for h, f in self.failures.items()}

    def _out_of_time(self, wait: float = 0.0) -> bool:
        return bool(self.deadline) and time.monotonic() + wait >= self.deadline

    @staticmethod
    def _retry_after(resp) -> float:
        """Seconds a 429 or 503 asks to be left, from Retry-After (seconds or
        an HTTP date), or 0."""
        value = (resp.headers.get("Retry-After") or "").strip()
        if not value:
            return 0.0
        if value.isdigit():
            return float(value)
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, when.timestamp() - time.time())
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(MAX_WAIT, BACKOFF_BASE * (2 ** attempt)) + random.uniform(0, 0.5)

    def _stale(self, path: Path, host: str) -> dict | None:
        """A cached copy past its lifetime but under `STALE_MAX_SECONDS` old,
        for when the source does not answer. Marked, and counted against the
        host, so the run says it used an old copy."""
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > STALE_MAX_SECONDS:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        payload["stale_days"] = round(age / 86400, 1)
        self.stats["stale"] = self.stats.get("stale", 0) + 1
        self.failures.setdefault(host, {"count": 0, "last_error": "", "stale": 0,
                                        "skipped": 0})["stale"] += 1
        return payload

    # ----------------------------------------------------------------- request
    def request(self, method: str, url: str, *, json_body: Any = None,
                params: dict | None = None, headers: dict | None = None,
                use_cache: bool = True, timeout: int | None = None,
                max_retries: int | None = None, cache_ttl: int | None = None) -> dict:
        """Return {'status', 'text', 'json', 'url'}; never raises on HTTP status.

        `timeout` and `max_retries` override the configured defaults per call.
        Government APIs are worth waiting and retrying for; arbitrary corporate
        websites are not — many stall or drop non-browser agents outright, and
        a full retry budget on each of a dozen candidate URLs turns one slow
        host into a run that never finishes. `cache_ttl`: how long this answer
        may be reused, where it differs from the default (a daily list).

        A source that does not answer is retried with growing, jittered waits
        — as long as it asks, if it says (Retry-After), up to a limit — and,
        failing, an older copy from the cache stands in if there is one under
        a week old. A host failing again and again is rested for a few
        minutes (`_failed`), and nothing is retried past the run's deadline.
        Every failure is counted against its host, for the run's notes.
        """
        cache_key_body = {"json": json_body, "params": params}
        path = self._cache_path(method, url, cache_key_body)
        if use_cache:
            cached = self._read_cache(path, cache_ttl)
            if cached is not None:
                self.stats["hits"] += 1
                return cached

        host = self._host(url)
        if self._resting(host) or self._out_of_time():
            reason = ("not tried: it failed repeatedly just before" if self._resting(host)
                      else "not tried: the run was out of time")
            stale = self._stale(path, host) if use_cache and method == "GET" else None
            if stale is not None:
                return stale
            self.failures.setdefault(host, {"count": 0, "last_error": reason, "stale": 0,
                                            "skipped": 0})["skipped"] += 1
            self.stats["errors"] += 1
            return {"status": 0, "url": url, "text": "", "json": None, "error": reason}

        attempts = max(1, self.cfg.max_retries if max_retries is None else max_retries)
        timeout = self.cfg.timeout if timeout is None else timeout

        last_err, last_status = "", 0
        for attempt in range(attempts):
            final = attempt + 1 >= attempts
            self.limiter.wait()
            try:
                resp = self.session.request(
                    method, url, json=json_body, params=params,
                    headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log.debug("network error %s (attempt %s)", last_err, attempt + 1)
                self._failed(host, last_err)
                wait = self._backoff(attempt)
                if final or self._resting(host) or self._out_of_time(wait):
                    break
                time.sleep(wait)
                continue

            if resp.status_code in RETRY_STATUS:
                last_err, last_status = f"HTTP {resp.status_code}", resp.status_code
                asked = self._retry_after(resp)
                self._failed(host, last_err, asked)
                wait = asked if asked else self._backoff(attempt)
                if final or wait > MAX_WAIT or self._resting(host) or self._out_of_time(wait):
                    break
                time.sleep(wait)
                continue

            self._succeeded(host)
            ctype = resp.headers.get("content-type", "")
            payload: dict[str, Any] = {
                "status": resp.status_code,
                "url": resp.url,
                "text": resp.text,
                "json": None,
                "error": "",
                # Carried because a caller may need to tell a feed from a page
                # that merely links to one. Absent from payloads cached by
                # earlier versions, so read it with .get().
                "content_type": ctype,
            }
            if "json" in ctype or resp.text[:1] in "{[":
                try:
                    payload["json"] = resp.json()
                except Exception:
                    pass
            self.stats["misses"] += 1
            if use_cache and resp.status_code == 200:
                try:
                    path.write_text(json.dumps(payload), encoding="utf-8")
                except Exception:
                    pass
                if next(_cache_writes) % PRUNE_EVERY == 0:
                    self.prune_cache()
            return payload

        stale = self._stale(path, host) if use_cache and method == "GET" else None
        if stale is not None:
            log.warning("%s did not answer (%s); using a copy %s days old", url, last_err,
                        stale["stale_days"])
            return stale
        self.stats["errors"] += 1
        log.warning("giving up on %s: %s", url, last_err)
        return {"status": last_status, "url": url, "text": "", "json": None,
                "error": last_err}

    def get(self, url: str, **kw) -> dict:
        return self.request("GET", url, **kw)

    def get_bytes(self, url: str, *, timeout: int = 60,
                  max_bytes: int = 40_000_000) -> tuple[int, bytes]:
        """(status, body) for a binary document. Not cached.

        `request` keeps `resp.text`, which decodes a PDF as though it were
        text and quietly corrupts it — the file still arrives, it just no
        longer parses. Same rate limiter and same identifying User-Agent as
        every other call. Nothing is written to the cache: a Form ADV runs to
        megabytes, and the store keeps what was read from it, not the file.
        """
        self.limiter.wait()
        try:
            resp = self.session.get(url, timeout=timeout, stream=True)
        except requests.RequestException as exc:
            self.stats["errors"] += 1
            log.warning("could not fetch %s: %s", url, exc)
            return (0, b"")
        body = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            body.extend(chunk)
            if len(body) > max_bytes:
                log.warning("%s is larger than %d bytes; stopped reading", url, max_bytes)
                resp.close()
                return (resp.status_code, b"")
        self.stats["misses"] += 1
        return (resp.status_code, bytes(body))

    def post(self, url: str, **kw) -> dict:
        return self.request("POST", url, **kw)
