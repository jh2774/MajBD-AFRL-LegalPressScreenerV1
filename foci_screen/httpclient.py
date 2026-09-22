"""Polite, cached HTTP client.

Government endpoints throttle aggressively and several of them (SEC in
particular) will ban a User-Agent that doesn't carry a contact address. This
wraps requests with rate limiting, retry/backoff, and an on-disk response
cache so re-running a screen doesn't re-hammer the same URLs.
"""
from __future__ import annotations

import hashlib
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

            payload: dict[str, Any] = {
                "status": resp.status_code,
                "url": resp.url,
                "text": resp.text,
                "json": None,
                "error": "",
            }
            ctype = resp.headers.get("content-type", "")
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
            return payload

        self.stats["errors"] += 1
        log.warning("giving up on %s: %s", url, last_err)
        return {"status": 0, "url": url, "text": "", "json": None, "error": last_err}

    def get(self, url: str, **kw) -> dict:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> dict:
        return self.request("POST", url, **kw)
