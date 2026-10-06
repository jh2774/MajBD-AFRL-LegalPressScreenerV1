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
import threading
import time
from pathlib import Path
from typing import Any

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

    # ------------------------------------------------------------------ cache
    def _cache_path(self, method: str, url: str, body: Any) -> Path:
        raw = f"{method}|{url}|{json.dumps(body, sort_keys=True) if body else ''}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, path: Path) -> dict | None:
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cfg.cache_ttl_seconds:
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
        now, kept, removed = time.time(), [], 0
        for path in self.cache_dir.glob("*.json"):
            try:
                stat = path.stat()
                if now - stat.st_mtime > self.cfg.cache_ttl_seconds:
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

    # ----------------------------------------------------------------- request
    def request(self, method: str, url: str, *, json_body: Any = None,
                params: dict | None = None, headers: dict | None = None,
                use_cache: bool = True, timeout: int | None = None,
                max_retries: int | None = None) -> dict:
        """Return {'status', 'text', 'json', 'url'}; never raises on HTTP status.

        `timeout` and `max_retries` override the configured defaults per call.
        Government APIs are worth waiting and retrying for; arbitrary corporate
        websites are not — many stall or drop non-browser agents outright, and
        a full retry budget on each of a dozen candidate URLs turns one slow
        host into a run that never finishes.
        """
        cache_key_body = {"json": json_body, "params": params}
        path = self._cache_path(method, url, cache_key_body)
        if use_cache:
            cached = self._read_cache(path)
            if cached is not None:
                self.stats["hits"] += 1
                return cached

        attempts = self.cfg.max_retries if max_retries is None else max_retries
        timeout = self.cfg.timeout if timeout is None else timeout

        last_err = ""
        for attempt in range(max(1, attempts)):
            self.limiter.wait()
            try:
                resp = self.session.request(
                    method, url, json=json_body, params=params,
                    headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log.debug("network error %s (attempt %s)", last_err, attempt + 1)
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
                if attempt + 1 < max(1, attempts):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return {"status": resp.status_code, "url": resp.url, "text": "",
                        "json": None, "error": last_err}

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

        self.stats["errors"] += 1
        log.warning("giving up on %s: %s", url, last_err)
        return {"status": 0, "url": url, "text": "", "json": None, "error": last_err}

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
