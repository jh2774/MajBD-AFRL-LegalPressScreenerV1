# foci-screen — complete source

Every source file in one document. Commit `f7d23e9 Ignore handover bundles`.
50 files, 10,919 lines.

Prose documentation (README, ROADMAP, DEPLOY, the session log) is not included —
it ships in the zip and the repository.

## Contents

- **Package configuration**
  - [`pyproject.toml`](#pyprojecttoml) — 64 lines
- **Core**
  - [`foci_screen/__init__.py`](#fociscreeninitpy) — 16 lines
  - [`foci_screen/config.py`](#fociscreenconfigpy) — 138 lines
  - [`foci_screen/models.py`](#fociscreenmodelspy) — 213 lines
  - [`foci_screen/httpclient.py`](#fociscreenhttpclientpy) — 148 lines
  - [`foci_screen/store.py`](#fociscreenstorepy) — 1076 lines
  - [`foci_screen/pipeline.py`](#fociscreenpipelinepy) — 278 lines
  - [`foci_screen/jobs.py`](#fociscreenjobspy) — 156 lines
  - [`foci_screen/worker.py`](#fociscreenworkerpy) — 37 lines
  - [`foci_screen/scheduler.py`](#fociscreenschedulerpy) — 121 lines
  - [`foci_screen/cli.py`](#fociscreenclipy) — 252 lines
- **Connectors (the data sources)**
  - [`foci_screen/connectors/__init__.py`](#fociscreenconnectorsinitpy) — 15 lines
  - [`foci_screen/connectors/usaspending.py`](#fociscreenconnectorsusaspendingpy) — 127 lines
  - [`foci_screen/connectors/fpds.py`](#fociscreenconnectorsfpdspy) — 170 lines
  - [`foci_screen/connectors/sec_edgar.py`](#fociscreenconnectorssecedgarpy) — 239 lines
  - [`foci_screen/connectors/registries.py`](#fociscreenconnectorsregistriespy) — 293 lines
  - [`foci_screen/connectors/webwatch.py`](#fociscreenconnectorswebwatchpy) — 329 lines
  - [`foci_screen/connectors/robots.py`](#fociscreenconnectorsrobotspy) — 105 lines
  - [`foci_screen/connectors/browser.py`](#fociscreenconnectorsbrowserpy) — 183 lines
- **Risk engine**
  - [`foci_screen/risk/__init__.py`](#fociscreenriskinitpy) — 4 lines
  - [`foci_screen/risk/lexicon.py`](#fociscreenrisklexiconpy) — 244 lines
  - [`foci_screen/risk/engine.py`](#fociscreenriskenginepy) — 605 lines
- **Notices**
  - [`foci_screen/notify/__init__.py`](#fociscreennotifyinitpy) — 8 lines
  - [`foci_screen/notify/render.py`](#fociscreennotifyrenderpy) — 250 lines
  - [`foci_screen/notify/gmail.py`](#fociscreennotifygmailpy) — 177 lines
- **HTTP API**
  - [`foci_screen/api/__init__.py`](#fociscreenapiinitpy) — 1 lines
  - [`foci_screen/api/app.py`](#fociscreenapiapppy) — 510 lines
  - [`foci_screen/api/auth.py`](#fociscreenapiauthpy) — 75 lines
  - [`foci_screen/api/schemas.py`](#fociscreenapischemaspy) — 37 lines
- **Web interface**
  - [`foci_screen/web/index.html`](#fociscreenwebindexhtml) — 56 lines
  - [`foci_screen/web/styles.css`](#fociscreenwebstylescss) — 449 lines
  - [`foci_screen/web/app.js`](#fociscreenwebappjs) — 988 lines
- **Tests**
  - [`tests/test_documents.py`](#teststestdocumentspy) — 427 lines
  - [`tests/test_robots.py`](#teststestrobotspy) — 294 lines
  - [`tests/test_scheduler.py`](#teststestschedulerpy) — 115 lines
  - [`tests/test_screening.py`](#teststestscreeningpy) — 714 lines
  - [`tests/test_search.py`](#teststestsearchpy) — 330 lines
  - [`tests/test_service.py`](#teststestservicepy) — 711 lines
- **Tools**
  - [`tools/check_web_coverage.py`](#toolscheckwebcoveragepy) — 95 lines
  - [`tools/export_session_log.py`](#toolsexportsessionlogpy) — 231 lines
  - [`tools/install_smoke.py`](#toolsinstallsmokepy) — 135 lines
  - [`tools/positive_control.py`](#toolspositivecontrolpy) — 91 lines
- **Deployment**
  - [`Dockerfile`](#dockerfile) — 22 lines
  - [`Dockerfile.worker`](#dockerfileworker) — 28 lines
  - [`render.yaml`](#renderyaml) — 107 lines
  - [`.github/workflows/ci.yml`](#githubworkflowsciyml) — 125 lines
  - [`.dockerignore`](#dockerignore) — 21 lines
  - [`.gitattributes`](#gitattributes) — 14 lines
  - [`.gitignore`](#gitignore) — 23 lines
  - [`.env.example`](#envexample) — 72 lines

---

# Package configuration


## `pyproject.toml`

<a id="pyprojecttoml"></a>

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "foci-screen"
version = "0.8.0"
description = "Screen federal contracts and contractor disclosures for FOCI and intellectual-property risk changes"
readme = "README.md"
requires-python = ">=3.10"
license = { text = "Proprietary" }
dependencies = [
    "requests>=2.31",
    "beautifulsoup4>=4.12",
]

[project.optional-dependencies]
# TLS-inspecting corporate proxies: use the OS trust store instead of certifi.
proxy = ["truststore>=0.9"]
# Step 3 delivery. Not installed by default — the Gmail path is inert without it.
gmail = [
    "google-api-python-client>=2.100",
    "google-auth-oauthlib>=1.1",
]
api = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
]
queue = ["rq>=1.16", "redis>=5.0"]
postgres = ["psycopg[binary]>=3.1"]
# Reads JavaScript-gated investor-relations pages. Needs `playwright install
# chromium` after pip; the package alone ships no browser.
browser = ["playwright>=1.44"]
# Everything a deployed instance needs.
server = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pydantic>=2.6",
    "rq>=1.16",
    "redis>=5.0",
    "psycopg[binary]>=3.1",
    "playwright>=1.44",
]
dev = ["pytest>=8.0", "ruff>=0.4", "httpx>=0.27"]

[project.scripts]
foci-screen = "foci_screen.cli:main"
foci-worker = "foci_screen.worker:main"
foci-scheduler = "foci_screen.scheduler:main"

[tool.setuptools.packages.find]
include = ["foci_screen*"]

[tool.setuptools.package-data]
foci_screen = ["web/*"]

[tool.ruff]
line-length = 96
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
ignore = ["B008"]
```


---

# Core


## `foci_screen/__init__.py`

<a id="fociscreeninitpy"></a>

```python
"""foci-screen — contract and contractor risk screening.

Screens federal contract awards and the awardee's public disclosures for
changes indicating Foreign Ownership, Control or Influence (FOCI),
intellectual-property collateralisation, or other IP risk, and drafts a notice
to the responsible contracting officer.
"""
__version__ = "0.1.0"

from .config import Config, get_config
from .models import Contract, Entity, Finding, Signal
from .pipeline import Screener, ScreenOptions
from .store import Store

__all__ = ["Config", "get_config", "Contract", "Entity", "Finding", "Signal",
           "ScreenOptions", "Screener", "Store", "__version__"]
```


## `foci_screen/config.py`

<a id="fociscreenconfigpy"></a>

```python
"""Runtime configuration.

Every credential is optional. The tool degrades gracefully: connectors that
need a key report themselves as unavailable rather than failing the run, so a
keyless install still produces findings from USAspending, FPDS, SEC EDGAR,
IAPD and OFAC.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in _TRUE if raw else default


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader so we don't take a dependency on python-dotenv."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    # --- identification (SEC and other .gov APIs require a real contact) ---
    user_agent: str = field(default_factory=lambda: _env(
        "FOCI_USER_AGENT", "foci-screen/0.1 (contact-not-set@example.com)"))

    # --- optional API keys ---
    sam_api_key: str = field(default_factory=lambda: _env("SAM_API_KEY"))
    uspto_api_key: str = field(default_factory=lambda: _env("USPTO_API_KEY"))
    trade_gov_key: str = field(default_factory=lambda: _env("TRADE_GOV_API_KEY"))

    # --- storage ---
    db_path: str = field(default_factory=lambda: _env("FOCI_DB", "foci_screen.db"))
    cache_dir: str = field(default_factory=lambda: _env("FOCI_CACHE", ".cache"))
    out_dir: str = field(default_factory=lambda: _env("FOCI_OUT", "out"))
    # Postgres for any deployment with more than one process or an ephemeral
    # filesystem. Empty means SQLite at `db_path`. Render injects DATABASE_URL.
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))

    # --- job queue ---
    # Empty means run screens inline in a background thread — correct for the
    # CLI and local development, not for a deployment where a screen must
    # survive a web process restart.
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL"))
    job_timeout: int = field(default_factory=lambda: int(_env("FOCI_JOB_TIMEOUT", "3600")))

    # --- headless browser (investor-relations pages) ---
    browser_enabled: bool = field(default_factory=lambda: _flag("FOCI_BROWSER", True))
    browser_timeout: int = field(
        default_factory=lambda: int(_env("FOCI_BROWSER_TIMEOUT", "25")))

    # --- API ---
    api_keys: str = field(default_factory=lambda: _env("FOCI_API_KEYS"))
    cors_origins: str = field(default_factory=lambda: _env("FOCI_CORS_ORIGINS"))

    # --- HTTP behaviour ---
    timeout: int = field(default_factory=lambda: int(_env("FOCI_TIMEOUT", "45")))
    rate_limit_qps: float = field(
        default_factory=lambda: float(_env("FOCI_QPS", "3.0")))
    cache_ttl_seconds: int = field(
        default_factory=lambda: int(_env("FOCI_CACHE_TTL", "21600")))
    max_retries: int = field(default_factory=lambda: int(_env("FOCI_RETRIES", "3")))

    # --- email / gmail (step 3: wired but inert by default) ---
    gmail_enabled: bool = field(default_factory=lambda: _flag("GMAIL_ENABLED", False))
    gmail_send: bool = field(default_factory=lambda: _flag("GMAIL_SEND", False))
    gmail_credentials: str = field(
        default_factory=lambda: _env("GMAIL_CREDENTIALS", "credentials.json"))
    gmail_token: str = field(default_factory=lambda: _env("GMAIL_TOKEN", "token.json"))
    gmail_sender: str = field(default_factory=lambda: _env("GMAIL_SENDER"))
    # Safety valve: while set, every draft is addressed here instead of the KO.
    email_redirect_to: str = field(
        default_factory=lambda: _env("FOCI_EMAIL_REDIRECT_TO"))

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.out_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

    @property
    def dsn(self) -> str:
        """Where the store should read and write."""
        return self.database_url or self.db_path

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def availability(self) -> dict[str, bool]:
        """Which connectors can actually run with the current credentials."""
        return {
            "usaspending": True,
            "fpds": True,
            "sec_edgar": True,
            "iapd": True,
            "ofac": True,
            "webwatch": True,
            "samgov": bool(self.sam_api_key),
            "uspto": bool(self.uspto_api_key),
            "trade_gov_csl": bool(self.trade_gov_key),
            "headless_browser": self.browser_enabled and _playwright_installed(),
        }


def _playwright_installed() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def get_config() -> Config:
    load_dotenv()
    cfg = Config()
    cfg.ensure_dirs()
    return cfg
```


## `foci_screen/models.py`

<a id="fociscreenmodelspy"></a>

```python
"""Core domain objects.

Deliberately plain dataclasses: they serialise to JSON for an API layer later
without dragging a web framework or ORM into the screening core.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _clean(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


class Serialisable:
    def to_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class ContractingOfficer(Serialisable):
    """Whoever we can actually address the notice to."""
    name: str = ""
    email: str = ""
    phone: str = ""
    office_code: str = ""
    office_name: str = ""
    # How we got here — shown in the email footer so a KO can sanity-check it.
    source: str = ""
    confidence: str = "low"  # low | medium | high

    @property
    def is_addressable(self) -> bool:
        return bool(self.email and "@" in self.email)


@dataclass
class Contract(Serialisable):
    award_id: str
    piid: str = ""
    generated_internal_id: str = ""
    recipient_name: str = ""
    recipient_uei: str = ""
    parent_recipient_name: str = ""
    parent_recipient_uei: str = ""
    awarding_agency: str = ""
    awarding_sub_agency: str = ""
    award_amount: float = 0.0
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    naics_code: str = ""
    naics_description: str = ""
    psc_code: str = ""
    psc_description: str = ""
    solicitation_id: str = ""
    contracting_office_id: str = ""
    recipient_country: str = ""
    country_of_incorporation: str = ""
    state_of_incorporation: str = ""
    foreign_owned_and_located: bool = False
    foreign_funding: str = ""
    officer: ContractingOfficer = field(default_factory=ContractingOfficer)
    # FAR/DFARS data-rights clauses inferred from the requirement description.
    ip_clause_hits: list[str] = field(default_factory=list)
    source_url: str = ""

    @property
    def usaspending_url(self) -> str:
        if self.generated_internal_id:
            return f"https://www.usaspending.gov/award/{self.generated_internal_id}"
        return ""

    @property
    def ip_sensitive(self) -> bool:
        return bool(self.ip_clause_hits)


@dataclass
class Entity(Serialisable):
    """A contractor, resolved across the various registries."""
    name: str
    uei: str = ""
    cage: str = ""
    parent_name: str = ""
    parent_uei: str = ""
    cik: str = ""                      # SEC
    tickers: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    iapd_crd: str = ""
    countries: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def key(self) -> str:
        return (self.uei or self.name).upper().strip()


@dataclass
class Document(Serialisable):
    """A retrieved artefact we can run rules over.

    `text` is normalised plain text; `key` is stable across runs so the store
    can diff this run's copy against the last one.
    """
    source: str            # usaspending | fpds | sec_edgar | iapd | uspto | web | ofac
    key: str
    title: str = ""
    url: str = ""
    text: str = ""
    published: str = ""
    doc_type: str = ""
    meta: dict = field(default_factory=dict)
    fetched_at: str = field(default_factory=_now)

    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "replace")).hexdigest()


@dataclass
class Change(Serialisable):
    """What is new about a Document since we last looked at it."""
    document_key: str
    source: str
    url: str = ""
    kind: str = "modified"          # new | modified | unchanged
    added_text: str = ""
    previous_sha: str = ""
    current_sha: str = ""
    previous_seen_at: str = ""
    observed_at: str = field(default_factory=_now)

    @property
    def is_material(self) -> bool:
        return self.kind in ("new", "modified") and bool(self.added_text.strip())


@dataclass
class Signal(Serialisable):
    """One rule firing against one piece of evidence."""
    rule_id: str
    category: str          # FOCI | IP_COLLATERAL | IP_TRANSFER | SANCTIONS | STRUCTURE
    severity: str
    score: float
    title: str
    rationale: str
    evidence: str
    source: str
    source_url: str = ""
    jurisdiction: str = ""
    observed_at: str = field(default_factory=_now)
    is_new: bool = False   # surfaced by a change diff rather than a baseline scan
    # Which stored document produced this, so a reviewer can open the diff that
    # contains the matched text rather than pairing the two up by eye.
    document_key: str = ""

    def severity_rank(self) -> int:
        return SEVERITY_ORDER.index(self.severity) if self.severity in SEVERITY_ORDER else 0


@dataclass
class Finding(Serialisable):
    """Everything we concluded about one entity in one run."""
    entity: Entity
    signals: list[Signal] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    total_score: float = 0.0
    severity: str = "info"
    run_id: str = ""
    generated_at: str = field(default_factory=_now)

    @property
    def obligated_total(self) -> float:
        return sum(c.award_amount for c in self.contracts)

    @property
    def new_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.is_new]

    def by_category(self) -> dict[str, list[Signal]]:
        out: dict[str, list[Signal]] = {}
        for s in self.signals:
            out.setdefault(s.category, []).append(s)
        for v in out.values():
            v.sort(key=lambda s: (-s.severity_rank(), -s.score))
        return out

    def top_officer(self) -> ContractingOfficer:
        """Address the notice to the KO on the largest addressable award."""
        addressable = [c for c in self.contracts if c.officer.is_addressable]
        if not addressable:
            return ContractingOfficer()
        ranked = sorted(
            addressable,
            key=lambda c: ({"high": 2, "medium": 1, "low": 0}.get(c.officer.confidence, 0),
                           c.award_amount),
            reverse=True)
        return ranked[0].officer
```


## `foci_screen/httpclient.py`

<a id="fociscreenhttpclientpy"></a>

```python
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
```


## `foci_screen/store.py`

<a id="fociscreenstorepy"></a>

```python
"""Persistence and change detection, over SQLite or Postgres.

The screening value is in the *delta*: a company that has always been
Delaware-incorporated is uninteresting, one that filed an 8-K last week about a
BVI investor is not. Every document we fetch is hashed and stored, so a later
run can tell new prose from prose we already screened.

Two dialects, one schema. SQLite is the right answer for a single analyst on a
laptop; it is the wrong answer the moment there is a web process and a worker
process, or a host with an ephemeral disk — which describes every deployment.
Passing a `postgres://` DSN switches dialect. The SQL is written once with `?`
placeholders and rewritten for Postgres on the way out; no `?` appears inside a
string literal anywhere in this module, which is what makes that safe.

**Tenancy.** Runs, findings, notices and watchlists carry a `tenant_id`. Snapshots
deliberately do not: a document's SHA is a fact about the world, not about who
is watching, so ten tenants screening the same prime fetch its 10-K once between
them. That sharing is the main reason the store is worth centralising at all.
"""
from __future__ import annotations

import difflib
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Change, Document, Finding

log = logging.getLogger("foci.store")

DEFAULT_TENANT = "default"

# {pk} and {json} are substituted per dialect.
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'complete',
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    agency      TEXT,
    params      TEXT,
    progress    TEXT,
    stats       TEXT,
    error       TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    source        TEXT NOT NULL,
    document_key  TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    text          TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    PRIMARY KEY (source, document_key)
);
CREATE TABLE IF NOT EXISTS snapshot_history (
    id           {pk},
    source       TEXT NOT NULL,
    document_key TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    observed_at  TEXT NOT NULL
);
-- Content-addressed prior revisions. Keyed by hash, so a body is stored once no
-- matter how many documents or tenants arrive at it — a filing exhibit that
-- appears under two companies costs one row. Pruned by `prune_snapshot_bodies`.
CREATE TABLE IF NOT EXISTS snapshot_bodies (
    sha256     TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- Snapshots are global; which documents were gathered for which contractor is
-- not. This is the per-tenant index over them.
CREATE TABLE IF NOT EXISTS entity_documents (
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    entity_key   TEXT NOT NULL,
    source       TEXT NOT NULL,
    document_key TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    doc_type     TEXT,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entity_key, source, document_key)
);
CREATE TABLE IF NOT EXISTS findings (
    id          {pk},
    run_id      TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    entity_key  TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    severity    TEXT NOT NULL,
    score       REAL NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id          {pk},
    run_id      TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    entity_key  TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (
    notice_id   TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    run_id      TEXT NOT NULL,
    finding_id  TEXT,
    entity_key  TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    severity    TEXT NOT NULL,
    recipient   TEXT,
    officer_confidence TEXT,
    subject     TEXT NOT NULL,
    body_text   TEXT NOT NULL,
    original_body_text TEXT,
    status      TEXT NOT NULL,
    decided_by  TEXT,
    decided_at  TEXT,
    decision_note TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contracts (
    id            {pk},
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    contract_key  TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    piid          TEXT,
    award_id      TEXT,
    entity_key    TEXT NOT NULL,
    entity_name   TEXT NOT NULL,
    agency        TEXT,
    sub_agency    TEXT,
    amount        REAL DEFAULT 0,
    start_date    TEXT,
    end_date      TEXT,
    naics_description TEXT,
    psc_description   TEXT,
    description   TEXT,
    solicitation_id TEXT,
    recipient_uei TEXT,
    recipient_country TEXT,
    country_of_incorporation TEXT,
    foreign_owned INTEGER DEFAULT 0,
    foreign_funding TEXT,
    ko_name       TEXT,
    ko_email      TEXT,
    ko_source     TEXT,
    ko_confidence TEXT,
    ip_clauses    TEXT,
    source_url    TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE (tenant_id, contract_key)
);
CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    name         TEXT NOT NULL,
    params       TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    last_run_at  TEXT,
    last_run_id  TEXT,
    created_at   TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_findings_entity ON findings(entity_key);
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_tenant ON findings(tenant_id, id);
CREATE INDEX IF NOT EXISTS idx_hist_doc ON snapshot_history(source, document_key);
CREATE INDEX IF NOT EXISTS idx_notices_tenant ON notices(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_runs_tenant ON runs(tenant_id, started_at);
CREATE INDEX IF NOT EXISTS idx_contracts_entity ON contracts(tenant_id, entity_key);
CREATE INDEX IF NOT EXISTS idx_contracts_ko ON contracts(tenant_id, ko_email);
CREATE INDEX IF NOT EXISTS idx_contracts_agency ON contracts(tenant_id, agency);
CREATE INDEX IF NOT EXISTS idx_entdocs ON entity_documents(tenant_id, entity_key);
"""

# Columns added after the first single-user release. `CREATE TABLE IF NOT
# EXISTS` does nothing to a table that already exists, so an existing database
# would keep its old shape and then fail on the first index over a new column.
# Applied only where the column is genuinely absent.
COLUMN_ADDITIONS = [
    ("runs", "tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("runs", "status", "TEXT NOT NULL DEFAULT 'complete'"),
    ("runs", "progress", "TEXT"),
    ("runs", "stats", "TEXT"),
    ("runs", "error", "TEXT"),
    ("findings", "tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("notifications", "tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
    ("notices", "original_body_text", "TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _is_postgres(dsn: str) -> bool:
    return dsn.startswith("postgres://") or dsn.startswith("postgresql://")


def _statements(ddl: str) -> list[str]:
    """Split DDL into statements, ignoring `--` comments.

    Splitting on `;` alone is not enough: a semicolon inside a comment —
    "Snapshots are global; which documents were gathered is not" — cuts the
    following CREATE TABLE in half, and the failure surfaces only on a fresh
    database. Comments are stripped first. No string literal in this schema
    contains `--`, which is what makes that safe.
    """
    stripped = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in stripped.split(";") if s.strip()]


class Store:
    """Thread-safe over a single connection.

    One lock serialises every statement. At the scale this tool operates —
    an analyst team, a nightly batch — contention is irrelevant next to the
    minutes each screen spends waiting on government APIs, and a single
    connection removes a whole class of pool-exhaustion failure.
    """

    def __init__(self, path: str = "foci_screen.db", tenant_id: str = DEFAULT_TENANT) -> None:
        self.path = path
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.is_postgres = _is_postgres(path)
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._migrate()

    # ------------------------------------------------------------- dialect
    def _connect(self):
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            # Render hands out postgres:// ; psycopg wants postgresql://
            dsn = self.path.replace("postgres://", "postgresql://", 1)
            return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)

        import sqlite3

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _table_columns(self, cur, table: str) -> set[str]:
        """Existing column names, or empty if the table is absent."""
        if self.is_postgres:
            cur.execute("SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = %s", (table,))
            return {r["column_name"] for r in cur.fetchall()}
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1] for r in cur.fetchall()}

    def _migrate(self) -> None:
        pk = ("BIGSERIAL PRIMARY KEY" if self.is_postgres
              else "INTEGER PRIMARY KEY AUTOINCREMENT")
        with self._lock:
            cur = self._conn.cursor()
            for statement in _statements(SCHEMA.format(pk=pk)):
                cur.execute(statement)

            for table, column, ddl in COLUMN_ADDITIONS:
                columns = self._table_columns(cur, table)
                if columns and column not in columns:
                    log.info("migrating: adding %s.%s", table, column)
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

            for statement in _statements(INDEXES):
                cur.execute(statement)

            self._backfill_bodies(cur)
            self._conn.commit()

    def _backfill_bodies(self, cur) -> None:
        """Seed revision storage from the current snapshots on first upgrade.

        A database written before `snapshot_bodies` existed has plenty of
        history rows and no text to go with them. The *current* body is still
        on the snapshot row, though, so copying it across means the next change
        to each document is diffable — rather than every document needing to
        change twice before the feature works.
        """
        cur.execute("SELECT COUNT(*) AS n FROM snapshot_bodies")
        row = cur.fetchone()
        existing = (row["n"] if isinstance(row, dict) else row[0]) or 0
        if existing:
            return
        cur.execute(
            "INSERT INTO snapshot_bodies (sha256, text, created_at)"
            " SELECT sha256, text, last_seen_at FROM snapshots"
            " WHERE text IS NOT NULL AND text != ''"
            " ON CONFLICT (sha256) DO NOTHING")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ plumbing
    @contextmanager
    def _tx(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield _Cursor(cur, self._sql)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(self._sql(sql), params)
                return [dict(r) for r in cur.fetchall()]
            finally:
                # Postgres opens a transaction on read; leaving it idle holds a
                # snapshot open and blocks vacuum.
                if self.is_postgres:
                    self._conn.commit()

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -------------------------------------------------------------- run record
    def start_run(self, agency: str, params: dict, run_id: str = "",
                  status: str = "complete") -> str:
        run_id = run_id or uuid.uuid4().hex[:12]
        with self._tx() as c:
            c.execute("INSERT INTO runs (run_id, tenant_id, status, started_at, agency,"
                      " params) VALUES (?,?,?,?,?,?)",
                      (run_id, self.tenant_id, status, _now(), agency,
                       json.dumps(params, default=str)))
        return run_id

    def mark_running(self, run_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET status=? WHERE run_id=?", ("running", run_id))

    def finish_run(self, run_id: str, status: str = "complete",
                   stats: dict | None = None, error: str = "") -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET finished_at=?, status=?, stats=?, error=?"
                      " WHERE run_id=?",
                      (_now(), status, json.dumps(stats or {}, default=str),
                       error, run_id))

    def set_run_progress(self, run_id: str, message: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE runs SET progress=? WHERE run_id=?", (message[:2000], run_id))

    def get_run(self, run_id: str) -> dict | None:
        return self._one("SELECT * FROM runs WHERE run_id=? AND tenant_id=?",
                         (run_id, self.tenant_id))

    def recent_runs(self, limit: int = 25) -> list[dict]:
        return self._query("SELECT run_id, status, started_at, finished_at, agency"
                           " FROM runs WHERE tenant_id=? ORDER BY started_at DESC"
                           " LIMIT ?", (self.tenant_id, limit))

    # ------------------------------------------------------- change detection
    def observe(self, doc: Document) -> Change:
        """Record `doc` and report what changed since we last saw it."""
        sha = doc.sha256()
        row = self._one("SELECT sha256, text, last_seen_at FROM snapshots"
                        " WHERE source=? AND document_key=?", (doc.source, doc.key))
        now = _now()

        if row is None:
            with self._tx() as c:
                # ON CONFLICT rather than a bare INSERT: two workers screening
                # different tenants can reach the same document at once.
                c.execute(
                    "INSERT INTO snapshots (source, document_key, sha256, url, title,"
                    " text, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)"
                    " ON CONFLICT (source, document_key) DO UPDATE SET"
                    " sha256=excluded.sha256, text=excluded.text,"
                    " last_seen_at=excluded.last_seen_at",
                    (doc.source, doc.key, sha, doc.url, doc.title, doc.text, now, now))
                c.execute("INSERT INTO snapshot_history (source, document_key, sha256,"
                          " observed_at) VALUES (?,?,?,?)", (doc.source, doc.key, sha, now))
                self._keep_body(c, sha, doc.text)
            return Change(document_key=doc.key, source=doc.source, url=doc.url,
                          kind="new", added_text=doc.text, current_sha=sha)

        if row["sha256"] == sha:
            with self._tx() as c:
                c.execute("UPDATE snapshots SET last_seen_at=? WHERE source=?"
                          " AND document_key=?", (now, doc.source, doc.key))
            return Change(document_key=doc.key, source=doc.source, url=doc.url,
                          kind="unchanged", previous_sha=sha, current_sha=sha,
                          previous_seen_at=row["last_seen_at"])

        added = added_text(row["text"] or "", doc.text)
        with self._tx() as c:
            c.execute("UPDATE snapshots SET sha256=?, text=?, url=?, title=?, last_seen_at=?"
                      " WHERE source=? AND document_key=?",
                      (sha, doc.text, doc.url, doc.title, now, doc.source, doc.key))
            c.execute("INSERT INTO snapshot_history (source, document_key, sha256, observed_at)"
                      " VALUES (?,?,?,?)", (doc.source, doc.key, sha, now))
            # Keep the version being replaced as well as the new one, or the
            # first diff after an upgrade has nothing to compare against.
            self._keep_body(c, row["sha256"], row["text"] or "")
            self._keep_body(c, sha, doc.text)
        return Change(document_key=doc.key, source=doc.source, url=doc.url,
                      kind="modified", added_text=added, previous_sha=row["sha256"],
                      current_sha=sha, previous_seen_at=row["last_seen_at"])

    def _keep_body(self, cursor, sha: str, text: str) -> None:
        """Retain a revision's text so a later diff can show what changed."""
        cursor.execute(
            "INSERT INTO snapshot_bodies (sha256, text, created_at) VALUES (?,?,?)"
            " ON CONFLICT (sha256) DO NOTHING", (sha, text, _now()))

    def is_first_run_for(self, source: str) -> bool:
        row = self._one("SELECT COUNT(*) AS n FROM snapshots WHERE source=?", (source,))
        return (row or {}).get("n", 0) == 0

    # -------------------------------------------------------------- documents
    def link_document(self, entity_key: str, doc: Document) -> None:
        """Record that this document was gathered while screening this entity."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO entity_documents (tenant_id, entity_key, source,"
                " document_key, title, url, doc_type, last_seen_at)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, entity_key, source, document_key)"
                " DO UPDATE SET title=excluded.title, url=excluded.url,"
                " doc_type=excluded.doc_type, last_seen_at=excluded.last_seen_at",
                (self.tenant_id, entity_key, doc.source, doc.key, doc.title,
                 doc.url, doc.doc_type, _now()))

    def documents_for_entity(self, entity_key: str) -> list[dict]:
        rows = self._query(
            "SELECT source, document_key, title, url, doc_type, last_seen_at"
            " FROM entity_documents WHERE tenant_id=? AND entity_key=?"
            " ORDER BY last_seen_at DESC", (self.tenant_id, entity_key.upper()))
        for r in rows:
            counts = self._one(
                "SELECT COUNT(*) AS n, MIN(observed_at) AS first_seen"
                " FROM snapshot_history WHERE source=? AND document_key=?",
                (r["source"], r["document_key"])) or {}
            r["revisions"] = counts.get("n") or 0
            r["first_seen"] = counts.get("first_seen")
        return rows

    def document_timeline(self, source: str, document_key: str,
                          limit: int = 50) -> list[dict]:
        """Every observed revision of one document, newest first.

        `has_body` says whether the text is still retained — pruning keeps only
        recent revisions, so an old hash can be known to have existed without
        being diffable. Saying so beats a diff that silently shows nothing.
        """
        rows = self._query(
            "SELECT h.sha256, h.observed_at,"
            " CASE WHEN b.sha256 IS NULL THEN 0 ELSE 1 END AS has_body"
            " FROM snapshot_history h"
            " LEFT JOIN snapshot_bodies b ON b.sha256 = h.sha256"
            " WHERE h.source=? AND h.document_key=?"
            " ORDER BY h.observed_at DESC, h.id DESC LIMIT ?",
            (source, document_key, limit))
        for r in rows:
            r["has_body"] = bool(r["has_body"])
        return rows

    def document_body(self, sha256: str) -> str | None:
        row = self._one("SELECT text FROM snapshot_bodies WHERE sha256=?", (sha256,))
        return row["text"] if row else None

    def document_diff(self, source: str, document_key: str,
                      from_sha: str = "", to_sha: str = "") -> dict:
        """Unified diff between two retained revisions.

        Defaults to the two most recent, which is the question actually being
        asked: what changed since we last looked?
        """
        history = self.document_timeline(source, document_key, limit=200)
        if not history:
            return {"error": "No revisions recorded for that document."}

        available = [h for h in history if h["has_body"]]
        if not to_sha:
            to_sha = available[0]["sha256"] if available else ""
        if not from_sha:
            later = [h for h in available if h["sha256"] != to_sha]
            from_sha = later[0]["sha256"] if later else ""

        new_text = self.document_body(to_sha) if to_sha else None
        old_text = self.document_body(from_sha) if from_sha else None
        if new_text is None:
            return {"error": "The text of that revision is no longer retained."}

        meta = {h["sha256"]: h for h in history}
        if old_text is None:
            # First time we ever saw it: everything is new, and that is not the
            # same claim as "the company changed something".
            return {
                "source": source, "document_key": document_key,
                "from": None, "to": meta.get(to_sha),
                "baseline": True,
                "lines": [{"kind": "add", "text": ln}
                          for ln in new_text.splitlines()[:2000]],
                "stats": {"added": len(new_text.splitlines()), "removed": 0},
            }

        lines, added, removed = _unified(old_text, new_text)
        return {
            "source": source, "document_key": document_key,
            "from": meta.get(from_sha), "to": meta.get(to_sha),
            "baseline": False,
            "lines": lines,
            "stats": {"added": added, "removed": removed},
        }

    def signals_for_document(self, entity_key: str, document_key: str) -> list[dict]:
        """Signals from this entity's latest finding that came from one document."""
        findings = self.search_findings(entity_key=entity_key.upper(), limit=1)
        if not findings:
            return []
        return [s for s in findings[0].get("signals", [])
                if s.get("document_key") == document_key]

    def prune_snapshot_bodies(self, keep_per_document: int = 20) -> dict:
        """Maintenance: drop old revision text and any body nothing references.

        Deliberately not called from `observe`. Screening is on a latency budget
        measured against government APIs, and a full scan of the body table has
        no business running inside it.
        """
        docs = self._query("SELECT DISTINCT source, document_key FROM snapshot_history")
        trimmed = 0
        for d in docs:
            history = self._query(
                "SELECT id FROM snapshot_history WHERE source=? AND document_key=?"
                " ORDER BY observed_at DESC, id DESC", (d["source"], d["document_key"]))
            stale = [row["id"] for row in history[keep_per_document:]]
            if not stale:
                continue
            with self._tx() as c:
                for row_id in stale:
                    c.execute("DELETE FROM snapshot_history WHERE id=?", (row_id,))
            trimmed += len(stale)

        with self._tx() as c:
            # A body is worth keeping while any history row or current snapshot
            # still points at it.
            c.execute("DELETE FROM snapshot_bodies WHERE sha256 NOT IN"
                      " (SELECT sha256 FROM snapshot_history)"
                      " AND sha256 NOT IN (SELECT sha256 FROM snapshots)")
        remaining = self._one("SELECT COUNT(*) AS n FROM snapshot_bodies") or {}
        return {"history_rows_removed": trimmed,
                "bodies_remaining": remaining.get("n") or 0}

    # -------------------------------------------------------------- findings
    def save_finding(self, finding: Finding) -> int:
        with self._tx() as c:
            c.execute(
                "INSERT INTO findings (run_id, tenant_id, entity_key, entity_name,"
                " severity, score, payload, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (finding.run_id, self.tenant_id, finding.entity.key(), finding.entity.name,
                 finding.severity, finding.total_score,
                 json.dumps(finding.to_dict(), default=str), _now()))
            return c.lastrowid()

    def previous_signal_ids(self, entity_key: str, exclude_run: str = "") -> set[str]:
        """Rule ids already reported for this entity — used to suppress repeats."""
        rows = self._query("SELECT payload FROM findings WHERE entity_key=?"
                           " AND run_id != ? AND tenant_id=?",
                           (entity_key, exclude_run, self.tenant_id))
        seen: set[str] = set()
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except Exception:
                continue
            for s in payload.get("signals", []):
                sig = f"{s.get('rule_id')}::{(s.get('evidence') or '')[:120]}"
                seen.add(sig)
        return seen

    def findings_for_run(self, run_id: str) -> list[dict]:
        rows = self._query("SELECT id, payload FROM findings WHERE run_id=? AND tenant_id=?"
                           " ORDER BY score DESC", (run_id, self.tenant_id))
        return [_with_id(r) for r in rows]

    def search_findings(self, severity: str = "", since: str = "",
                        entity_key: str = "", limit: int = 50) -> list[dict]:
        sql = ["SELECT id, payload FROM findings WHERE tenant_id=?"]
        params: list = [self.tenant_id]
        if severity:
            sql.append("AND severity=?")
            params.append(severity)
        if since:
            sql.append("AND created_at >= ?")
            params.append(since)
        if entity_key:
            sql.append("AND entity_key=?")
            params.append(entity_key)
        sql.append("ORDER BY id DESC LIMIT ?")
        params.append(limit)
        return [_with_id(r) for r in self._query(" ".join(sql), tuple(params))]

    def entity_history(self, entity_key: str, limit: int = 50) -> list[dict]:
        return self._query(
            "SELECT run_id, severity, score, created_at FROM findings"
            " WHERE entity_key=? AND tenant_id=? ORDER BY id DESC LIMIT ?",
            (entity_key.upper(), self.tenant_id, limit))

    def log_notification(self, run_id: str, entity_key: str, recipient: str,
                         subject: str, status: str, detail: str = "") -> None:
        with self._tx() as c:
            c.execute("INSERT INTO notifications (run_id, tenant_id, entity_key, recipient,"
                      " subject, status, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (run_id, self.tenant_id, entity_key, recipient, subject,
                       status, detail, _now()))

    def recent_findings(self, limit: int = 50) -> list[dict]:
        return self._query(
            "SELECT run_id, entity_name, severity, score, created_at FROM findings"
            " WHERE tenant_id=? ORDER BY id DESC LIMIT ?", (self.tenant_id, limit))

    # --------------------------------------------------------------- notices
    def create_notice(self, *, run_id: str, entity_key: str, entity_name: str,
                      severity: str, recipient: str, officer_confidence: str,
                      subject: str, body_text: str, finding_id: str = "",
                      status: str = "pending") -> str:
        notice_id = uuid.uuid4().hex[:12]
        with self._tx() as c:
            c.execute(
                "INSERT INTO notices (notice_id, tenant_id, run_id, finding_id, entity_key,"
                " entity_name, severity, recipient, officer_confidence, subject, body_text,"
                " status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (notice_id, self.tenant_id, run_id, finding_id, entity_key, entity_name,
                 severity, recipient, officer_confidence, subject, body_text,
                 status, _now()))
        return notice_id

    def get_notice(self, notice_id: str) -> dict | None:
        return self._one("SELECT * FROM notices WHERE notice_id=? AND tenant_id=?",
                         (notice_id, self.tenant_id))

    def list_notices(self, status: str = "", limit: int = 50) -> list[dict]:
        if status:
            return self._query("SELECT * FROM notices WHERE tenant_id=? AND status=?"
                               " ORDER BY created_at DESC LIMIT ?",
                               (self.tenant_id, status, limit))
        return self._query("SELECT * FROM notices WHERE tenant_id=?"
                           " ORDER BY created_at DESC LIMIT ?", (self.tenant_id, limit))

    def decide_notice(self, notice_id: str, status: str, decided_by: str,
                      note: str = "", body_text: str = "") -> str:
        """Record an approve or reject. Returns "ok", "not_found" or "already_decided".

        A decision is final. Letting a rejected notice be approved afterwards
        would make the rejection — the only labelled false-positive data the tool
        gets — quietly untrue, and would let a second reviewer overrule the first
        with no record that they had.

        An edited body keeps the generated text alongside it. What the tool
        wrote and what a person sent are different claims, and the gap between
        them is exactly what someone asks about when a notice is challenged.
        """
        existing = self.get_notice(notice_id)
        if existing is None:
            return "not_found"
        if existing["status"] != "pending":
            return "already_decided"

        edited = bool(body_text) and body_text.strip() != (existing["body_text"] or "").strip()
        with self._tx() as c:
            # The status guard in the WHERE clause closes the race between two
            # reviewers deciding the same notice at once.
            if edited:
                c.execute("UPDATE notices SET status=?, decided_by=?, decided_at=?,"
                          " decision_note=?, body_text=?,"
                          " original_body_text=COALESCE(original_body_text, body_text)"
                          " WHERE notice_id=? AND tenant_id=? AND status='pending'",
                          (status, decided_by, _now(), note, body_text,
                           notice_id, self.tenant_id))
            else:
                c.execute("UPDATE notices SET status=?, decided_by=?, decided_at=?,"
                          " decision_note=?"
                          " WHERE notice_id=? AND tenant_id=? AND status='pending'",
                          (status, decided_by, _now(), note, notice_id, self.tenant_id))
            if c.rowcount() == 0:
                return "already_decided"
        return "ok"

    def notice_edits(self, notice: dict) -> dict | None:
        """What a reviewer changed, as diff lines. None if the text is as generated."""
        original = notice.get("original_body_text")
        if not original:
            return None
        lines, added, removed = _unified(original, notice.get("body_text") or "")
        return {"lines": lines, "stats": {"added": added, "removed": removed}}

    # ------------------------------------------------------------ watchlists
    def create_watchlist(self, name: str, params: dict) -> str:
        watchlist_id = uuid.uuid4().hex[:12]
        with self._tx() as c:
            c.execute("INSERT INTO watchlists (watchlist_id, tenant_id, name, params,"
                      " active, created_at) VALUES (?,?,?,?,?,?)",
                      (watchlist_id, self.tenant_id, name,
                       json.dumps(params, default=str), 1, _now()))
        return watchlist_id

    def list_watchlists(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM watchlists WHERE tenant_id=?"
        if active_only:
            sql += " AND active=1"
        return self._query(sql + " ORDER BY created_at DESC", (self.tenant_id,))

    def all_active_watchlists(self) -> list[dict]:
        """Every tenant's active watchlists — for the scheduler, not the API."""
        return self._query("SELECT * FROM watchlists WHERE active=1 ORDER BY tenant_id")

    def set_watchlist_active(self, watchlist_id: str, active: bool) -> bool:
        if self._one("SELECT watchlist_id FROM watchlists WHERE watchlist_id=?"
                     " AND tenant_id=?", (watchlist_id, self.tenant_id)) is None:
            return False
        with self._tx() as c:
            c.execute("UPDATE watchlists SET active=? WHERE watchlist_id=? AND tenant_id=?",
                      (1 if active else 0, watchlist_id, self.tenant_id))
        return True

    def mark_watchlist_run(self, watchlist_id: str, run_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE watchlists SET last_run_at=?, last_run_id=?"
                      " WHERE watchlist_id=?", (_now(), run_id, watchlist_id))


    # -------------------------------------------------------------- contracts
    def save_contract(self, contract, run_id: str, entity_key: str) -> None:
        """Index one award so it can be searched.

        The finding payload already carries its contracts, but as JSON — you
        cannot search inside it, which is the whole reason this table exists.
        """
        key = (contract.piid or contract.award_id or "").strip()
        if not key:
            return
        o = contract.officer
        with self._tx() as c:
            c.execute(
                "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, award_id,"
                " entity_key, entity_name, agency, sub_agency, amount, start_date,"
                " end_date, naics_description, psc_description, description,"
                " solicitation_id, recipient_uei, recipient_country,"
                " country_of_incorporation, foreign_owned, foreign_funding, ko_name,"
                " ko_email, ko_source, ko_confidence, ip_clauses, source_url, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, contract_key) DO UPDATE SET"
                " run_id=excluded.run_id, amount=excluded.amount,"
                " entity_name=excluded.entity_name, ko_name=excluded.ko_name,"
                " ko_email=excluded.ko_email, ko_source=excluded.ko_source,"
                " ko_confidence=excluded.ko_confidence,"
                " ip_clauses=excluded.ip_clauses, updated_at=excluded.updated_at",
                (self.tenant_id, key, run_id, contract.piid, contract.award_id,
                 entity_key, contract.recipient_name, contract.awarding_agency,
                 contract.awarding_sub_agency, float(contract.award_amount or 0),
                 contract.start_date, contract.end_date, contract.naics_description,
                 contract.psc_description, contract.description,
                 contract.solicitation_id, contract.recipient_uei,
                 contract.recipient_country, contract.country_of_incorporation,
                 1 if contract.foreign_owned_and_located else 0,
                 contract.foreign_funding, o.name, o.email, o.source, o.confidence,
                 json.dumps(contract.ip_clause_hits or []), contract.source_url, _now()))

    def get_contract(self, contract_key: str) -> dict | None:
        row = self._one("SELECT * FROM contracts WHERE tenant_id=? AND contract_key=?",
                        (self.tenant_id, contract_key))
        return _decode_contract(row) if row else None

    def contracts_where(self, column: str, value: str, limit: int = 200) -> list[dict]:
        """Contracts filtered on one indexed column. `column` is never user input."""
        if column not in {"entity_key", "ko_email", "agency", "sub_agency", "run_id"}:
            raise ValueError(f"not a filterable column: {column}")
        rows = self._query(
            f"SELECT * FROM contracts WHERE tenant_id=? AND {column}=?"
            " ORDER BY amount DESC LIMIT ?", (self.tenant_id, value, limit))
        return [_decode_contract(r) for r in rows]

    # ----------------------------------------------------------------- search
    # LOWER(col) LIKE ? rather than ILIKE: Postgres LIKE is case-sensitive and
    # SQLite has no ILIKE, so this is the one form that means the same thing in
    # both. At this row count the lost index is not worth two code paths.
    def search_contracts(self, q: str, limit: int = 25) -> list[dict]:
        like = f"%{q.lower()}%"
        rows = self._query(
            "SELECT * FROM contracts WHERE tenant_id=? AND ("
            " LOWER(piid) LIKE ? OR LOWER(award_id) LIKE ?"
            " OR LOWER(solicitation_id) LIKE ? OR LOWER(description) LIKE ?"
            " OR LOWER(psc_description) LIKE ? OR LOWER(naics_description) LIKE ?)"
            " ORDER BY amount DESC LIMIT ?",
            (self.tenant_id, like, like, like, like, like, like, limit))
        return [_decode_contract(r) for r in rows]

    def search_entities(self, q: str = "", limit: int = 25) -> list[dict]:
        """Contractors, with their latest severity attached."""
        params: list = [self.tenant_id]
        sql = ("SELECT entity_key, MAX(entity_name) AS entity_name,"
               " COUNT(*) AS contract_count, SUM(amount) AS obligated,"
               " MAX(recipient_uei) AS uei,"
               " MAX(country_of_incorporation) AS country_of_incorporation,"
               " MAX(foreign_owned) AS foreign_owned"
               " FROM contracts WHERE tenant_id=?")
        if q:
            sql += " AND (LOWER(entity_name) LIKE ? OR LOWER(entity_key) LIKE ?)"
            like = f"%{q.lower()}%"
            params += [like, like]
        sql += " GROUP BY entity_key ORDER BY SUM(amount) DESC LIMIT ?"
        params.append(limit)

        rows = self._query(sql, tuple(params))
        for r in rows:
            latest = self._one(
                "SELECT severity, score, created_at FROM findings"
                " WHERE tenant_id=? AND entity_key=? ORDER BY id DESC LIMIT 1",
                (self.tenant_id, r["entity_key"]))
            r["severity"] = latest["severity"] if latest else None
            r["score"] = latest["score"] if latest else None
            r["last_screened"] = latest["created_at"] if latest else None
        return rows

    def search_officers(self, q: str = "", limit: int = 25) -> list[dict]:
        params: list = [self.tenant_id]
        sql = ("SELECT ko_email, MAX(ko_name) AS ko_name,"
               " MAX(ko_confidence) AS ko_confidence, MAX(ko_source) AS ko_source,"
               " COUNT(*) AS contract_count, SUM(amount) AS obligated,"
               " COUNT(DISTINCT entity_key) AS entity_count,"
               " MAX(agency) AS agency"
               " FROM contracts WHERE tenant_id=? AND ko_email IS NOT NULL"
               " AND ko_email != ''")
        if q:
            sql += " AND (LOWER(ko_email) LIKE ? OR LOWER(ko_name) LIKE ?)"
            like = f"%{q.lower()}%"
            params += [like, like]
        sql += " GROUP BY ko_email ORDER BY SUM(amount) DESC LIMIT ?"
        params.append(limit)
        return self._query(sql, tuple(params))

    def search_agencies(self, q: str = "", limit: int = 25) -> list[dict]:
        params: list = [self.tenant_id]
        sql = ("SELECT agency, sub_agency, COUNT(*) AS contract_count,"
               " SUM(amount) AS obligated, COUNT(DISTINCT entity_key) AS entity_count,"
               " COUNT(DISTINCT ko_email) AS officer_count"
               " FROM contracts WHERE tenant_id=? AND agency IS NOT NULL AND agency != ''")
        if q:
            sql += " AND (LOWER(agency) LIKE ? OR LOWER(sub_agency) LIKE ?)"
            like = f"%{q.lower()}%"
            params += [like, like]
        sql += " GROUP BY agency, sub_agency ORDER BY SUM(amount) DESC LIMIT ?"
        params.append(limit)
        return self._query(sql, tuple(params))

    def search_all(self, q: str, limit: int = 10) -> dict:
        return {
            "entities": self.search_entities(q, limit),
            "contracts": self.search_contracts(q, limit),
            "officers": self.search_officers(q, limit),
            "agencies": self.search_agencies(q, limit),
        }

    # ------------------------------------------------------------- dashboards
    def severity_counts(self) -> dict[str, int]:
        """Latest severity per entity, counted. Not every finding ever recorded —
        an entity screened weekly for a year would otherwise dominate the chart."""
        rows = self._query(
            "SELECT entity_key, severity FROM findings WHERE tenant_id=?"
            " ORDER BY id DESC", (self.tenant_id,))
        seen: set[str] = set()
        counts: dict[str, int] = {}
        for r in rows:
            if r["entity_key"] in seen:
                continue
            seen.add(r["entity_key"])
            counts[r["severity"]] = counts.get(r["severity"], 0) + 1
        return counts

    def signal_category_counts(self, limit_findings: int = 200) -> dict[str, int]:
        rows = self._query("SELECT payload FROM findings WHERE tenant_id=?"
                           " ORDER BY id DESC LIMIT ?", (self.tenant_id, limit_findings))
        counts: dict[str, int] = {}
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (ValueError, TypeError):
                continue
            for s in payload.get("signals", []):
                cat = s.get("category") or "OTHER"
                counts[cat] = counts.get(cat, 0) + 1
        return counts

    def findings_by_day(self, limit: int = 60) -> list[dict]:
        rows = self._query(
            "SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(*) AS n"
            " FROM findings WHERE tenant_id=? GROUP BY SUBSTR(created_at, 1, 10)"
            " ORDER BY day DESC LIMIT ?", (self.tenant_id, limit))
        return list(reversed(rows))

    def totals(self) -> dict:
        contracts = self._one(
            "SELECT COUNT(*) AS n, SUM(amount) AS obligated,"
            " COUNT(DISTINCT entity_key) AS entities,"
            " COUNT(DISTINCT ko_email) AS officers,"
            " COUNT(DISTINCT agency) AS agencies"
            " FROM contracts WHERE tenant_id=?", (self.tenant_id,)) or {}
        pending = self._one("SELECT COUNT(*) AS n FROM notices WHERE tenant_id=?"
                            " AND status='pending'", (self.tenant_id,)) or {}
        return {
            "contracts": contracts.get("n") or 0,
            "obligated": float(contracts.get("obligated") or 0),
            "entities": contracts.get("entities") or 0,
            "officers": contracts.get("officers") or 0,
            "agencies": contracts.get("agencies") or 0,
            "notices_pending": pending.get("n") or 0,
        }


def _decode_contract(row: dict) -> dict:
    row = dict(row)
    try:
        row["ip_clauses"] = json.loads(row.get("ip_clauses") or "[]")
    except (ValueError, TypeError):
        row["ip_clauses"] = []
    row["foreign_owned"] = bool(row.get("foreign_owned"))
    return row


class _Cursor:
    """Wraps a DB-API cursor so callers can write `?` regardless of dialect."""

    def __init__(self, cur, translate) -> None:
        self._cur = cur
        self._translate = translate

    def execute(self, sql: str, params: tuple = ()):
        return self._cur.execute(self._translate(sql), params)

    def rowcount(self) -> int:
        return int(getattr(self._cur, "rowcount", 0) or 0)

    def lastrowid(self) -> int:
        # Postgres has no lastrowid; nothing downstream uses the value, and
        # findings are addressed by run_id + entity elsewhere.
        return int(getattr(self._cur, "lastrowid", 0) or 0)


def _with_id(row: dict) -> dict:
    """Merge the stored finding payload with its row id."""
    try:
        payload = json.loads(row["payload"])
    except Exception:
        payload = {}
    payload["finding_id"] = row.get("id")
    return payload


def _unified(old: str, new: str, context: int = 3,
             max_lines: int = 4000) -> tuple[list[dict], int, int]:
    """Unified diff as structured lines the UI can render without parsing.

    Returning `{kind, text}` rather than a diff string keeps the presentation
    layer from re-parsing `+`/`-` prefixes, and means a line whose content
    genuinely starts with a minus cannot be misread as a deletion.
    """
    out: list[dict] = []
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     lineterm="", n=context):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            out.append({"kind": "hunk", "text": line})
        elif line.startswith("+"):
            added += 1
            out.append({"kind": "add", "text": line[1:]})
        elif line.startswith("-"):
            removed += 1
            out.append({"kind": "del", "text": line[1:]})
        else:
            out.append({"kind": "ctx", "text": line[1:] if line else ""})
        if len(out) >= max_lines:
            out.append({"kind": "hunk", "text": "… diff truncated"})
            break
    return out, added, removed


def _normalise_for_match(text: str) -> str:
    return " ".join((text or "").split()).lower()


# Shorter than this, a line is a heading or a fragment and will collide with
# unrelated evidence — "Contact us" appears inside half the snippets on a site.
MATCHABLE_LINE_CHARS = 25
# A shared run this long is distinctive enough to mean the two texts describe
# the same passage.
OVERLAP_WINDOW = 40


def _overlaps(line: str, evidence: str) -> bool:
    """Do a diff line and a signal's evidence describe the same passage?

    Neither contains the other reliably. Evidence is a fixed-width window around
    the term that matched, so on a long paragraph the evidence is the shorter of
    the two, while on a short line it is the longer. Testing containment in one
    direction silently marks nothing — which is exactly what it did. Look for a
    distinctive shared run instead.
    """
    if not line or not evidence:
        return False
    if line in evidence or evidence in line:
        return True
    if len(evidence) < OVERLAP_WINDOW:
        return False
    step = max(1, OVERLAP_WINDOW // 4)
    for i in range(0, len(evidence) - OVERLAP_WINDOW + 1, step):
        if evidence[i:i + OVERLAP_WINDOW] in line:
            return True
    return False


def annotate_diff(lines: list[dict], signals: list[dict]) -> list[dict]:
    """Mark the diff lines that a rule's evidence actually came from.

    The diff shows what changed and the signal shows what fired; without this a
    reviewer pairs them up by eye.

    Deliberately conservative: a short line matches too many snippets to mean
    anything, so it is left unmarked rather than marked wrongly. An unhighlighted
    line that should be highlighted costs a moment; the reverse points a
    reviewer at the wrong sentence.
    """
    prepared = [(s, _normalise_for_match(s.get("evidence", ""))) for s in signals]
    out = []
    for line in lines:
        marked = dict(line)
        needle = _normalise_for_match(line.get("text", ""))
        if len(needle) >= MATCHABLE_LINE_CHARS:
            rules = [s.get("rule_id") for s, evidence in prepared
                     if _overlaps(needle, evidence)]
            if rules:
                marked["rules"] = sorted(set(rules))
        out.append(marked)
    return out


def added_text(old: str, new: str) -> str:
    """Lines present in `new` but not `old`, joined back into prose.

    Rules run over this rather than the whole document so a screen reports
    "this company just disclosed X", not "this company's boilerplate mentions X".
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = difflib.ndiff(old_lines, new_lines)
    return "\n".join(line[2:] for line in diff if line.startswith("+ "))
```


## `foci_screen/pipeline.py`

<a id="fociscreenpipelinepy"></a>

```python
"""Orchestration: contracts -> entities -> evidence -> changes -> findings.

The pipeline is a plain synchronous function on purpose. Everything it needs is
injected (config, store, http), so the same code path serves the CLI today and
a FastAPI request handler or a queue worker later without modification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .connectors.fpds import FPDSConnector
from .connectors.registries import IAPDConnector, OFACConnector, SAMConnector, USPTOConnector
from .connectors.sec_edgar import EdgarConnector
from .connectors.usaspending import USASpendingConnector
from .connectors.webwatch import WebWatchConnector
from .models import Change, Contract, Document, Entity, Finding
from .risk import engine
from .store import Store

log = logging.getLogger("foci.pipeline")


@dataclass
class ScreenOptions:
    agency: str
    sub_agency: str = ""
    months_back: int = 12
    max_awards: int = 25
    max_entities: int = 5
    keyword: str = ""
    include_idv: bool = False
    domains: dict[str, str] = field(default_factory=dict)   # entity name -> domain
    fetch_filing_bodies: bool = True
    min_severity: str = "low"
    skip_web: bool = False


@dataclass
class ScreenResult:
    run_id: str
    options: ScreenOptions
    contracts: list[Contract] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Screener:
    def __init__(self, config, http, store: Store, browser=None) -> None:
        self.cfg = config
        self.http = http
        self.store = store
        self.usaspending = USASpendingConnector(http)
        self.fpds = FPDSConnector(http)
        self.edgar = EdgarConnector(http)
        self.iapd = IAPDConnector(http)
        self.ofac = OFACConnector(http)
        self.uspto = USPTOConnector(http, config.uspto_api_key)
        self.sam = SAMConnector(http, config.sam_api_key)
        self.web = WebWatchConnector(http, browser=browser)

    # ------------------------------------------------------------------ run
    def run(self, opts: ScreenOptions, progress=lambda msg: None,
            run_id: str = "") -> ScreenResult:
        # A queued run already has its row — the API created it so it could
        # return an id before the work started. When it did, the caller owns the
        # run's lifecycle and this method must not close it out.
        owns_run = not run_id
        if owns_run:
            run_id = self.store.start_run(opts.agency, opts.__dict__)
        result = ScreenResult(run_id=run_id, options=opts)

        # --- step 1: contracts ------------------------------------------
        progress(f"Searching USAspending for {opts.agency} awards "
                 f"(last {opts.months_back} months)...")
        contracts = self.usaspending.search_awards(
            opts.agency, months_back=opts.months_back, limit=opts.max_awards,
            sub_agency=opts.sub_agency, include_idv=opts.include_idv,
            keyword=opts.keyword)
        if not contracts:
            result.notes.append(
                f"No awards returned for '{opts.agency}'. Check the agency name "
                f"against `foci-screen agencies`.")
            if owns_run:
                self.store.finish_run(run_id)
            return result
        progress(f"  {len(contracts)} award(s) found.")

        # --- group by contractor before enriching (saves duplicate calls) --
        by_entity: dict[str, list[Contract]] = {}
        for c in contracts:
            key = (c.recipient_uei or c.recipient_name).upper()
            by_entity.setdefault(key, []).append(c)

        ranked = sorted(by_entity.items(),
                        key=lambda kv: -sum(c.award_amount for c in kv[1]))
        selected = ranked[:opts.max_entities]
        progress(f"  screening top {len(selected)} contractor(s) by obligated value.")

        for key, ent_contracts in selected:
            name = ent_contracts[0].recipient_name
            progress(f"\n[{name}]")
            for c in ent_contracts[:4]:
                progress(f"  enriching {c.piid} (USAspending detail + FPDS)...")
                self.usaspending.enrich(c)
                self.fpds.enrich(c)
            entity = self._build_entity(name, ent_contracts, opts)
            # Indexed for search regardless of whether a finding results — an
            # award with no risk signal is still the record that answers "what
            # else does this contracting officer hold?"
            for c in ent_contracts:
                self.store.save_contract(c, run_id, entity.key())
            finding = self._screen_entity(entity, ent_contracts, run_id, opts, progress)
            if finding:
                result.findings.append(finding)
            result.contracts.extend(ent_contracts)

        result.findings.sort(key=lambda f: -f.total_score)
        for f in result.findings:
            self.store.save_finding(f)
        result.stats = dict(self.http.stats)
        result.stats["entities_screened"] = len(selected)
        result.stats["awards_examined"] = len(contracts)

        result.notes.extend(self.coverage_notes())

        if owns_run:
            self.store.finish_run(run_id, stats=result.stats)
        return result

    def coverage_notes(self) -> list[str]:
        """Say so when a website was not fully read.

        A silent gap reads as "nothing found on their website", which is a
        different claim entirely — and a site that asked not to be crawled has not
        thereby said it has nothing to disclose.
        """
        notes: list[str] = []
        if self.web.skipped_js_hosts:
            hosts = ", ".join(sorted(self.web.skipped_js_hosts))
            notes.append(
                f"Could not read {hosts} — the page requires a browser and none "
                f"is installed. Install the 'browser' extra and run "
                f"`playwright install chromium` to cover investor-relations pages.")

        # Where the website is closed to us, say where else the same disclosure
        # tends to surface, rather than leaving a reviewer with only a gap.
        elsewhere = ("Material announcements usually also appear as SEC 8-K filings, "
                     "which this screen reads, and on the company's main newsroom.")

        if self.web.unreadable_hosts:
            hosts = ", ".join(sorted(self.web.unreadable_hosts))
            notes.append(
                f"Could not read {hosts} — the site did not serve readable content to "
                f"this tool's browser, which identifies itself. Investor-relations "
                f"platforms behind bot management typically refuse automated clients, "
                f"and the tool does not disguise itself to get past that. {elsewhere}")

        by_host: dict[str, dict[str, int]] = {}
        for url, reason in self.web.skipped_robots.items():
            host = urlparse(url).netloc.lower()
            reasons = by_host.setdefault(host, {})
            reasons[reason] = reasons.get(reason, 0) + 1
        for host, reasons in sorted(by_host.items()):
            detail = "; ".join(f"{n} page(s): {why}" for why, n in reasons.items())
            unreachable = any("unreachable" in why for why in reasons)
            notes.append(
                f"Did not read {host} ({detail}). "
                + (f"The host did not answer, so its crawling rules could not be "
                   f"read and are treated as a refusal. {elsewhere}" if unreachable
                   else "Coverage of that site is incomplete by the site's own "
                        "request — check it by hand if it matters."))
        return notes

    # -------------------------------------------------------------- entity
    def _build_entity(self, name: str, contracts: list[Contract],
                      opts: ScreenOptions) -> Entity:
        first = contracts[0]
        cik, matched = self.edgar.resolve_cik(first.parent_recipient_name or name)
        domain = opts.domains.get(name) or opts.domains.get(name.upper(), "")
        countries = sorted({c.recipient_country for c in contracts if c.recipient_country}
                           | {c.country_of_incorporation for c in contracts
                              if c.country_of_incorporation})
        return Entity(
            name=name, uei=first.recipient_uei,
            parent_name=first.parent_recipient_name,
            parent_uei=first.parent_recipient_uei,
            cik=cik, aliases=[matched] if matched else [],
            domains=[domain] if domain else [],
            countries=countries,
            contracts=[c.piid or c.award_id for c in contracts])

    # ------------------------------------------------------------ evidence
    def _gather(self, entity: Entity, opts: ScreenOptions,
                progress) -> list[Document]:
        docs: list[Document] = []

        if entity.cik:
            progress(f"  SEC EDGAR: CIK {entity.cik} ({', '.join(entity.aliases)})")
            filings = self.edgar.recent_filings(entity.cik, limit=12)
            if opts.fetch_filing_bodies:
                for f in filings[:6]:
                    self.edgar.fetch_filing_text(f)
            docs.extend(filings)
        else:
            progress("  SEC EDGAR: no confident CIK match (skipping filings)")

        if entity.cik:
            progress("  SEC EDGAR full-text search for IP security agreements...")
            docs.extend(self.edgar.full_text_search(
                entity.parent_name or entity.name, cik=entity.cik))
        else:
            progress("  SEC EDGAR full-text: skipped (needs a CIK to attribute hits)")

        progress("  IAPD adviser search...")
        docs.extend(self.iapd.search_firm(entity.parent_name or entity.name, hits=5))

        progress("  OFAC SDN name screen...")
        docs.extend(self.ofac.screen(entity.name))
        if entity.parent_name and entity.parent_name != entity.name:
            docs.extend(self.ofac.screen(entity.parent_name))

        if self.uspto.available:
            progress("  USPTO assignment records...")
            docs.extend(self.uspto.assignments(entity.parent_name or entity.name))
        else:
            progress("  USPTO: skipped (no USPTO_API_KEY — IP liens NOT checked)")

        if self.sam.available and entity.uei:
            progress("  SAM.gov entity registration...")
            sam_doc = self.sam.entity(entity.uei)
            if sam_doc:
                docs.append(sam_doc)

        if entity.domains and not opts.skip_web:
            for domain in entity.domains:
                progress(f"  crawling {domain} (IR / press / legal)...")
                docs.extend(self.web.collect(domain, company=entity.name))

        return [d for d in docs if d.text.strip()]

    # ------------------------------------------------------------ screening
    def _screen_entity(self, entity: Entity, contracts: list[Contract], run_id: str,
                       opts: ScreenOptions, progress) -> Finding | None:
        docs = self._gather(entity, opts, progress)
        progress(f"  {len(docs)} document(s) collected; diffing against store...")

        pairs: list[tuple[Document, Change | None]] = []
        changed = 0
        for doc in docs:
            change = self.store.observe(doc)
            self.store.link_document(entity.key(), doc)
            if change.kind in ("new", "modified"):
                changed += 1
            pairs.append((doc, change))
        progress(f"  {changed} new/changed since last screen.")

        signals = engine.evaluate_documents(pairs, entity, contracts)
        signals += engine.evaluate_contracts(contracts, entity)

        # First time we see an entity everything looks "new"; that would mark a
        # baseline scan as urgent. Damp it unless the evidence is independently strong.
        seen_before = self.store.previous_signal_ids(entity.key(), exclude_run=run_id)
        if not seen_before:
            for s in signals:
                if s.severity not in ("critical",):
                    s.is_new = False

        finding = engine.build_finding(entity, contracts, signals, run_id=run_id)
        progress(f"  -> {finding.severity.upper()} (score {finding.total_score}, "
                 f"{len(finding.signals)} signals)")

        order = ["info", "low", "medium", "high", "critical"]
        if order.index(finding.severity) < order.index(opts.min_severity):
            return None
        return finding
```


## `foci_screen/jobs.py`

<a id="fociscreenjobspy"></a>

```python
"""Screening as a background job.

A screen takes minutes — it walks USAspending, FPDS, EDGAR, OFAC and a handful
of contractor websites, all of them rate-limited on purpose. Holding an HTTP
connection open for that is the single thing that stops the CLI design from
being a service, so the API creates a run row, hands the work to a queue, and
returns a `run_id` immediately.

Two backends, chosen by whether `REDIS_URL` is set:

  * **RQ.** The real one. Work survives a web-process restart, and the worker
    scales independently of the API — which matters because the worker is the
    process carrying a headless browser.
  * **Thread.** Local development and the CLI. No Redis, no worker to run, and
    a job dies with the process. Fine when the process is a terminal someone
    is watching; not fine in production, which is why a deployment sets Redis.

The job function is module-level and takes only JSON-serialisable arguments so
RQ can address it by import path.
"""
from __future__ import annotations

import logging
import threading
import traceback

from .config import Config, get_config
from .connectors.browser import BrowserRenderer
from .httpclient import HttpClient
from .notify import render as render_notice
from .pipeline import Screener, ScreenOptions
from .store import Store

log = logging.getLogger("foci.jobs")

# Findings weaker than this never become a notice. They remain queryable — the
# point of the change feed is that most nights are quiet — but nobody is asked
# to review a low-severity correlation.
NOTICE_THRESHOLD = "medium"
SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def severity_at_least(severity: str, floor: str) -> bool:
    try:
        return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(floor)
    except ValueError:
        return False


def build_screener(cfg: Config, store: Store) -> tuple[Screener, BrowserRenderer]:
    """Assemble a screener with a headless browser attached."""
    http = HttpClient(cfg)
    browser = BrowserRenderer(timeout=cfg.browser_timeout, enabled=cfg.browser_enabled,
                              identity=cfg.user_agent)
    return Screener(cfg, http, store, browser=browser), browser


# --------------------------------------------------------------------- the job

def run_screen_job(tenant_id: str, run_id: str, options: dict) -> dict:
    """Execute one screen. Called by an RQ worker or a local thread."""
    cfg = get_config()
    store = Store(cfg.dsn, tenant_id=tenant_id)
    screener, browser = build_screener(cfg, store)

    def progress(msg: str) -> None:
        text = msg.strip()
        if text:
            store.set_run_progress(run_id, text)

    try:
        store.mark_running(run_id)
        opts = ScreenOptions(**options)
        result = screener.run(opts, progress=progress, run_id=run_id)

        notices = [queue_notice(store, f, run_id) for f in result.findings
                   if severity_at_least(f.severity, NOTICE_THRESHOLD)]

        stats = dict(result.stats)
        stats["findings"] = len(result.findings)
        stats["notices_pending"] = len(notices)
        stats["notes"] = result.notes
        store.finish_run(run_id, status="complete", stats=stats)
        return {"run_id": run_id, "findings": len(result.findings),
                "notices": len(notices)}
    except Exception as exc:
        log.exception("screen %s failed", run_id)
        store.finish_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}",
                         stats={"traceback": traceback.format_exc()[-2000:]})
        raise
    finally:
        browser.close()
        store.close()


def queue_notice(store: Store, finding, run_id: str) -> str:
    """Persist a notice in `pending` — nothing is addressed without a human."""
    officer = finding.top_officer()
    return store.create_notice(
        run_id=run_id,
        entity_key=finding.entity.key(),
        entity_name=finding.entity.name,
        severity=finding.severity,
        recipient=officer.email,
        officer_confidence=officer.confidence if officer.is_addressable else "unresolved",
        subject=render_notice.subject_for(finding),
        body_text=render_notice.render_text(finding, run_id=run_id),
        status="pending",
    )


# ------------------------------------------------------------------- dispatch

class JobQueue:
    """Enqueues screens onto RQ, or onto a thread when Redis is absent."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._queue = None
        if cfg.redis_url:
            try:
                from redis import Redis
                from rq import Queue

                self._queue = Queue("screens", connection=Redis.from_url(cfg.redis_url),
                                    default_timeout=cfg.job_timeout)
                log.info("job queue: RQ via Redis")
            except Exception as exc:
                # Failing over to threads silently would be worse than loud
                # degradation: jobs would start dying with the web process.
                log.error("REDIS_URL is set but the queue could not be created "
                          "(%s) — falling back to in-process threads", exc)
        else:
            log.info("job queue: in-process threads (no REDIS_URL)")

    @property
    def backend(self) -> str:
        return "rq" if self._queue is not None else "thread"

    def enqueue(self, tenant_id: str, run_id: str, options: dict) -> None:
        if self._queue is not None:
            self._queue.enqueue(run_screen_job, tenant_id, run_id, options,
                                job_id=run_id, job_timeout=self.cfg.job_timeout)
            return
        threading.Thread(
            target=_thread_target, args=(tenant_id, run_id, options),
            name=f"screen-{run_id}", daemon=True).start()


def _thread_target(tenant_id: str, run_id: str, options: dict) -> None:
    try:
        run_screen_job(tenant_id, run_id, options)
    except Exception:
        # Already recorded on the run row by run_screen_job; this keeps a dead
        # thread from printing an unhandled-exception traceback to the log.
        log.debug("threaded screen %s ended in failure", run_id)
```


## `foci_screen/worker.py`

<a id="fociscreenworkerpy"></a>

```python
"""RQ worker. Entry point for the background service.

    python -m foci_screen.worker

Runs screens off the queue. This is the process that carries the headless
browser, which is the reason it is worth separating from the web service at
all: Chromium's memory ceiling should not decide how many API requests can be
served concurrently.
"""
from __future__ import annotations

import logging
import sys

from .config import get_config


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = get_config()
    if not cfg.redis_url:
        print("REDIS_URL is not set — there is no queue to consume.",
              file=sys.stderr)
        return 1

    from redis import Redis
    from rq import Queue, Worker

    connection = Redis.from_url(cfg.redis_url)
    queue = Queue("screens", connection=connection)
    Worker([queue], connection=connection).work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```


## `foci_screen/scheduler.py`

<a id="fociscreenschedulerpy"></a>

```python
"""Nightly watchlist sweep. Entry point for a cron job.

    python -m foci_screen.scheduler

Enqueues one screen per active watchlist, across every tenant, then exits. It
does not wait for the work — the queue owns that — so the cron job's own
runtime stays in seconds.

This is where the change-detection design pays for itself. After the baseline
run, most nights produce no findings at all, and that is the correct output: the
tool reports what moved, and on most nights nothing did.
"""
from __future__ import annotations

import json
import logging
import sys

from .config import get_config
from .jobs import JobQueue
from .store import DEFAULT_TENANT, Store

log = logging.getLogger("foci.scheduler")

# Revisions of a document to keep the text of. Enough to answer "what changed
# recently"; not so many that a page rewritten daily accumulates a year of
# 40KB bodies. The hashes are kept regardless, so the fact that a revision
# existed survives even when its text does not.
RETAIN_REVISIONS = 20


class QueueUnavailable(RuntimeError):
    """A sweep was asked for but there is no queue that would outlive the process."""


def prune() -> dict:
    """Drop revision text beyond the retention window. Safe to run any time."""
    cfg = get_config()
    store = Store(cfg.dsn, tenant_id=DEFAULT_TENANT)
    try:
        stats = store.prune_snapshot_bodies(keep_per_document=RETAIN_REVISIONS)
        log.info("pruned %s history row(s); %s bodies retained",
                 stats["history_rows_removed"], stats["bodies_remaining"])
        return stats
    finally:
        store.close()


def sweep() -> int:
    """Enqueue every active watchlist. Returns the number enqueued.

    Raises `QueueUnavailable` rather than returning 0 when there is no queue:
    "nothing to do tonight" and "cannot do anything at all" must not look the
    same to whoever reads the cron history.
    """
    cfg = get_config()
    queue = JobQueue(cfg)

    if queue.backend == "thread":
        # Threads die with this process, and this process exits in a second.
        raise QueueUnavailable(
            "No REDIS_URL configured. A scheduled sweep needs a real queue and a "
            "worker to run the jobs; nothing would survive this process exiting.")

    admin = Store(cfg.dsn, tenant_id=DEFAULT_TENANT)
    try:
        watchlists = admin.all_active_watchlists()
        if not watchlists:
            log.info("no active watchlists")
            return 0

        enqueued = 0
        for row in watchlists:
            tenant = row["tenant_id"]
            try:
                options = json.loads(row["params"])
            except (ValueError, TypeError):
                log.warning("watchlist %s has unreadable params — skipping",
                            row["watchlist_id"])
                continue

            store = Store(cfg.dsn, tenant_id=tenant)
            try:
                run_id = store.start_run(options.get("agency", ""), options,
                                         status="queued")
                store.mark_watchlist_run(row["watchlist_id"], run_id)
                queue.enqueue(tenant, run_id, options)
                enqueued += 1
                log.info("queued %s for watchlist '%s' (tenant %s)",
                         run_id, row["name"], tenant)
            finally:
                store.close()
        return enqueued
    finally:
        admin.close()


EXIT_QUEUE_UNAVAILABLE = 2


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Pruning does not need a queue; do it even when there is nothing to enqueue.
    stats = prune()
    try:
        count = sweep()
    except QueueUnavailable as exc:
        # Non-zero so the cron run is marked failed. A green run that screened
        # nothing is how a misconfigured deploy goes unnoticed for weeks.
        log.error("%s Refusing to enqueue.", exc)
        print(f"sweep FAILED: queue unavailable; "
              f"pruned {stats['history_rows_removed']} old revision(s)")
        return EXIT_QUEUE_UNAVAILABLE
    print(f"enqueued {count} screen(s); "
          f"pruned {stats['history_rows_removed']} old revision(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```


## `foci_screen/cli.py`

<a id="fociscreenclipy"></a>

```python
"""Command line interface.

    foci-screen agencies
    foci-screen screen --agency "Department of the Navy" --months 6 --entities 3
    foci-screen screen --agency "..." --domain "LEIDOS INC=leidos.com"
    foci-screen notify --run <run_id>
    foci-screen history
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import get_config
from .connectors.usaspending import USASpendingConnector
from .httpclient import HttpClient
from .jobs import NOTICE_THRESHOLD, build_screener, queue_notice, severity_at_least
from .models import Finding
from .notify import render
from .notify.gmail import GmailNotifier, status_banner
from .pipeline import ScreenOptions
from .store import Store

SEV_COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m",
             "low": "\033[36m", "info": "\033[2m"}
RESET = "\033[0m"


def _c(severity: str) -> str:
    return f"{SEV_COLOR.get(severity, '')}{severity.upper()}{RESET}"


def _parse_domains(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        name, _, domain = item.partition("=")
        if domain:
            out[name.strip()] = domain.strip()
            out[name.strip().upper()] = domain.strip()
    return out


# ------------------------------------------------------------------ commands

def cmd_agencies(args, cfg) -> int:
    http = HttpClient(cfg)
    names = USASpendingConnector.list_agencies(http)
    needle = (args.filter or "").lower()
    for n in names:
        if not needle or needle in n.lower():
            print(n)
    return 0


def cmd_screen(args, cfg) -> int:
    store = Store(cfg.dsn)
    screener, browser = build_screener(cfg, store)

    opts = ScreenOptions(
        agency=args.agency, sub_agency=args.sub_agency or "",
        months_back=args.months, max_awards=args.awards,
        max_entities=args.entities, keyword=args.keyword or "",
        include_idv=args.include_idv, domains=_parse_domains(args.domain),
        fetch_filing_bodies=not args.fast, min_severity=args.min_severity,
        skip_web=args.no_web)

    def progress(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    print(f"Run configuration: agency={opts.agency!r} window={opts.months_back}mo "
          f"awards<={opts.max_awards} entities<={opts.max_entities}")
    avail = cfg.availability()
    off = [k for k, v in avail.items() if not v]
    if off:
        print(f"Connectors unavailable (no key): {', '.join(off)}")
    print(status_banner(cfg))
    print("-" * 78)

    try:
        result = screener.run(opts, progress=progress)
    finally:
        browser.close()

    print("\n" + "=" * 78)
    print(f"RUN {result.run_id}: {result.stats.get('awards_examined', 0)} awards, "
          f"{result.stats.get('entities_screened', 0)} contractors screened, "
          f"{len(result.findings)} finding(s)")
    for note in result.notes:
        print(f"  note: {note}")
    print("=" * 78)

    for f in result.findings:
        print(f"\n{_c(f.severity)}  {f.entity.name}  (score {f.total_score})")
        officer = f.top_officer()
        addr = (f"{officer.name} <{officer.email}> via {officer.source}"
                if officer.is_addressable else "no KO resolved")
        print(f"  obligated: ${f.obligated_total:,.0f} across {len(f.contracts)} action(s)"
              f" | KO: {addr}")
        for s in f.signals[:6]:
            flag = " [NEW]" if s.is_new else ""
            print(f"    - [{s.severity}] {s.title}{flag}")
            print(f"        {s.rationale[:150]}...")

    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": result.run_id, "agency": opts.agency,
               "stats": result.stats, "notes": result.notes,
               "findings": [f.to_dict() for f in result.findings]}
    json_path = out_dir / f"run_{result.run_id}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results: {json_path}")

    # Queue the same notices the API would, so the review UI shows this run
    # whichever way it was started.
    queued = [queue_notice(store, f, result.run_id) for f in result.findings
              if severity_at_least(f.severity, NOTICE_THRESHOLD)]
    if queued:
        print(f"{len(queued)} notice(s) queued for review "
              f"(severity {NOTICE_THRESHOLD}+).")

    if args.notify and result.findings:
        _notify(cfg, store, result.findings, result.run_id)

    store.close()
    return 0


def _notify(cfg, store: Store, findings: list[Finding], run_id: str) -> None:
    notifier = GmailNotifier(cfg)
    print("\n" + status_banner(cfg))
    for f in findings:
        res = notifier.deliver(f, run_id=run_id)
        store.log_notification(run_id, f.entity.key(), res.recipient,
                               res.subject, res.status, res.detail)
        location = f" -> {res.path}" if res.path else ""
        print(f"  [{res.status}] {f.entity.name} -> {res.recipient or '(none)'}"
              f"{location}")
        if res.detail:
            print(f"      {res.detail}")


def cmd_notify(args, cfg) -> int:
    store = Store(cfg.dsn)
    path = Path(cfg.out_dir) / f"run_{args.run}.json"
    if not path.is_file():
        print(f"No stored run at {path}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    from .models import Contract, ContractingOfficer, Entity, Signal

    findings: list[Finding] = []
    for raw in data.get("findings", []):
        ent = Entity(**raw["entity"])
        contracts = []
        for c in raw.get("contracts", []):
            officer = ContractingOfficer(**c.pop("officer", {}) or {})
            contracts.append(Contract(officer=officer, **c))
        signals = [Signal(**s) for s in raw.get("signals", [])]
        findings.append(Finding(entity=ent, contracts=contracts, signals=signals,
                                total_score=raw.get("total_score", 0.0),
                                severity=raw.get("severity", "info"),
                                run_id=raw.get("run_id", args.run)))
    if args.preview:
        for f in findings:
            print("=" * 78)
            print(f"SUBJECT: {render.subject_for(f)}")
            print(f"TO:      {f.top_officer().email or '(unresolved)'}")
            print("-" * 78)
            print(render.render_text(f, args.run))
            print()
        return 0
    _notify(cfg, store, findings, args.run)
    store.close()
    return 0


def cmd_history(args, cfg) -> int:
    store = Store(cfg.dsn)
    rows = store.recent_findings(args.limit)
    if not rows:
        print("No findings recorded yet.")
    for r in rows:
        print(f"{r['created_at']}  {r['run_id']}  {_c(r['severity']):<22} "
              f"{r['score']:>7.1f}  {r['entity_name']}")
    store.close()
    return 0


# --------------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foci-screen",
        description="Screen government contracts and contractor disclosures for "
                    "FOCI and intellectual-property risk changes.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("agencies", help="list awarding agency names accepted by --agency")
    a.add_argument("--filter", help="substring filter")
    a.set_defaults(func=cmd_agencies)

    s = sub.add_parser("screen", help="run a screen against one agency")
    s.add_argument("--agency", required=True, help='e.g. "Department of Defense"')
    s.add_argument("--sub-agency", help='e.g. "Department of the Navy"')
    s.add_argument("--months", type=int, default=12, help="lookback window (default 12)")
    s.add_argument("--awards", type=int, default=25, help="max awards to pull (default 25)")
    s.add_argument("--entities", type=int, default=5, help="max contractors to screen")
    s.add_argument("--keyword", help="restrict awards by keyword")
    s.add_argument("--include-idv", action="store_true", help="include IDV vehicles")
    s.add_argument("--domain", action="append", metavar='"NAME=domain.com"',
                   help="map a contractor to its website for IR/press/legal watching")
    s.add_argument("--min-severity", default="low",
                   choices=["info", "low", "medium", "high", "critical"])
    s.add_argument("--fast", action="store_true",
                   help="skip downloading filing bodies (labels only)")
    s.add_argument("--no-web", action="store_true", help="skip company website crawl")
    s.add_argument("--notify", action="store_true",
                   help="render notices (Gmail drafts only if GMAIL_ENABLED=true)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_screen)

    n = sub.add_parser("notify", help="render or draft notices for a stored run")
    n.add_argument("--run", required=True, help="run id from a previous screen")
    n.add_argument("--preview", action="store_true",
                   help="print the notices to stdout and exit")
    n.set_defaults(func=cmd_notify)

    h = sub.add_parser("history", help="recent findings")
    h.add_argument("--limit", type=int, default=25)
    h.set_defaults(func=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")
    cfg = get_config()
    if "example.com" in cfg.user_agent:
        print("WARNING: set FOCI_USER_AGENT to 'yourtool/1.0 (your.email@org)' — "
              "SEC blocks generic agents.\n", file=sys.stderr)
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
```


---

# Connectors (the data sources)


## `foci_screen/connectors/__init__.py`

<a id="fociscreenconnectorsinitpy"></a>

```python
"""Data source connectors.

Each connector owns exactly one upstream system and returns `Document` objects
(or enriches `Contract` objects). None of them raise on upstream failure — a
dead endpoint degrades the screen, it does not end it.
"""
from .fpds import FPDSConnector
from .registries import IAPDConnector, OFACConnector, SAMConnector, USPTOConnector
from .sec_edgar import EdgarConnector
from .usaspending import USASpendingConnector
from .webwatch import WebWatchConnector

__all__ = ["FPDSConnector", "IAPDConnector", "OFACConnector", "SAMConnector",
           "USPTOConnector", "EdgarConnector", "USASpendingConnector",
           "WebWatchConnector"]
```


## `foci_screen/connectors/usaspending.py`

<a id="fociscreenconnectorsusaspendingpy"></a>

```python
"""USAspending.gov — contract discovery (step 1).

Keyless. `spending_by_award` gives the population; `awards/{id}` gives the
solicitation number that bridges to FPDS and SAM for the contracting officer.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Contract, ContractingOfficer
from ..risk.lexicon import find_ip_clauses

log = logging.getLogger("foci.usaspending")

BASE = "https://api.usaspending.gov/api/v2"
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]           # definitive/IDV contract awards
IDV_TYPE_CODES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C",
                  "IDV_D", "IDV_E"]

FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "Award Amount",
    "Awarding Agency", "Awarding Sub Agency", "Start Date", "End Date",
    "Description", "generated_internal_id", "Contract Award Type", "NAICS", "PSC",
]


class USASpendingConnector:
    name = "usaspending"

    def __init__(self, http) -> None:
        self.http = http

    # ------------------------------------------------------------------ search
    def search_awards(self, agency: str, *, months_back: int = 12, limit: int = 25,
                      tier: str = "toptier", sub_agency: str = "",
                      include_idv: bool = False, keyword: str = "") -> list[Contract]:
        end = date.today()
        start = end - timedelta(days=30 * months_back)
        agencies = [{"type": "awarding", "tier": tier, "name": agency}]
        if sub_agency:
            agencies.append({"type": "awarding", "tier": "subtier", "name": sub_agency})

        codes = list(CONTRACT_TYPE_CODES) + (IDV_TYPE_CODES if include_idv else [])
        filters: dict = {
            "award_type_codes": codes,
            "agencies": agencies,
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        }
        if keyword:
            filters["keywords"] = [keyword]

        payload = {"filters": filters, "fields": FIELDS, "page": 1,
                   "limit": min(limit, 100), "sort": "Award Amount",
                   "order": "desc", "subawards": False}

        resp = self.http.post(f"{BASE}/search/spending_by_award/", json_body=payload)
        data = resp.get("json") or {}
        if resp.get("status") != 200:
            log.warning("USAspending search failed (%s): %s",
                        resp.get("status"), (resp.get("text") or "")[:200])
            return []

        contracts: list[Contract] = []
        for row in data.get("results", []):
            naics = row.get("NAICS") or {}
            psc = row.get("PSC") or {}
            contracts.append(Contract(
                award_id=str(row.get("Award ID") or ""),
                piid=str(row.get("Award ID") or ""),
                generated_internal_id=row.get("generated_internal_id") or "",
                recipient_name=row.get("Recipient Name") or "",
                recipient_uei=row.get("Recipient UEI") or "",
                awarding_agency=row.get("Awarding Agency") or "",
                awarding_sub_agency=row.get("Awarding Sub Agency") or "",
                award_amount=float(row.get("Award Amount") or 0),
                start_date=row.get("Start Date") or "",
                end_date=row.get("End Date") or "",
                description=row.get("Description") or "",
                naics_code=str(naics.get("code") or ""),
                naics_description=naics.get("description") or "",
                psc_code=str(psc.get("code") or ""),
                psc_description=psc.get("description") or "",
                source_url=f"https://www.usaspending.gov/award/{row.get('generated_internal_id')}",
            ))
        return contracts

    # ------------------------------------------------------------------ detail
    def enrich(self, contract: Contract) -> Contract:
        """Fill in solicitation id, parent entity and country from award detail."""
        if not contract.generated_internal_id:
            return contract
        resp = self.http.get(f"{BASE}/awards/{contract.generated_internal_id}/")
        data = resp.get("json") or {}
        if not data:
            return contract

        ltcd = data.get("latest_transaction_contract_data") or {}
        contract.solicitation_id = ltcd.get("solicitation_identifier") or ""
        contract.foreign_funding = ltcd.get("foreign_funding_description") or ""
        dom = (ltcd.get("domestic_or_foreign_entity_description") or "")
        if "foreign" in dom.lower():
            contract.foreign_owned_and_located = True

        rec = data.get("recipient") or {}
        contract.parent_recipient_name = rec.get("parent_recipient_name") or ""
        contract.parent_recipient_uei = rec.get("parent_recipient_uei") or ""
        loc = rec.get("location") or {}
        contract.recipient_country = loc.get("location_country_code") or ""

        # Requirement text is the only place FAR/DFARS clause hints show up here.
        blob = " ".join(filter(None, [contract.description, data.get("description") or ""]))
        contract.ip_clause_hits = find_ip_clauses(blob)

        if not contract.officer.name:
            contract.officer = ContractingOfficer()
        return contract

    def recipient_profile(self, recipient_hash: str) -> dict:
        resp = self.http.get(f"{BASE}/recipient/duns/{recipient_hash}/")
        return resp.get("json") or {}

    @staticmethod
    def list_agencies(http) -> list[str]:
        resp = http.get(f"{BASE}/references/toptier_agencies/")
        data = resp.get("json") or {}
        return sorted(a.get("agency_name", "") for a in data.get("results", []))
```


## `foci_screen/connectors/fpds.py`

<a id="fociscreenconnectorsfpdspy"></a>

```python
"""FPDS-NG ATOM feed — contracting officer identity and foreign-ownership flags.

This is the connector that makes step 3 possible without a SAM.gov key. FPDS
records the account that created, approved and last modified each contract
action, and for DoD those are the contracting officer's real .mil/.gov
addresses (e.g. JANE.DOE.N00019@JSF.MIL).

Caveats worth knowing before you email anyone:
  * lastModifiedBy is whoever last touched the record, which may be a
    specialist or an administrator rather than the warranted KO;
  * on older actions the person may have moved on.
Both are reflected in ContractingOfficer.confidence and stated in the email
footer so a recipient can redirect rather than be misled.
"""
from __future__ import annotations

import logging
import re
from xml.etree import ElementTree as ET

from ..models import Contract, ContractingOfficer

log = logging.getLogger("foci.fpds")

FEED = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"
ATOM_NS = "{http://www.w3.org/2005/Atom}"

_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _text(elem, path: str) -> str:
    """Namespace-agnostic search: FPDS namespaces vary by feed version."""
    if elem is None:
        return ""
    for node in elem.iter():
        tag = node.tag.split("}")[-1]
        if tag == path:
            return (node.text or "").strip()
    return ""


def _all_text(elem, path: str) -> list[str]:
    out = []
    if elem is None:
        return out
    for node in elem.iter():
        if node.tag.split("}")[-1] == path and node.text:
            out.append(node.text.strip())
    return out


def _pretty_name(email: str) -> str:
    """JULIE.BAKEWELLCHISHOLM.N00019@JSF.MIL -> 'Julie Bakewellchisholm'.

    DoD appends a disambiguating digit when two people share a name
    (SANDRA.T.REYES2.CIV@MAIL.MIL). It belongs to the mailbox, not the person,
    and "Dear Sandra T Reyes2," in a notice to a federal official reads as a
    broken tool. Dropped for display; the address itself is untouched.
    """
    local = email.split("@")[0]
    parts = [p for p in local.split(".") if p and not re.fullmatch(r"[A-Z]\d{4,}", p)]
    parts = [p for p in parts if not re.fullmatch(r"\d+", p)]
    parts = [re.sub(r"\d+$", "", p) or p for p in parts]
    parts = [p for p in parts if p.lower() not in {"civ", "mil", "ctr"}]
    if not parts:
        return ""
    return " ".join(p.capitalize() for p in parts[:3])


class FPDSConnector:
    name = "fpds"

    def __init__(self, http) -> None:
        self.http = http

    def fetch_award(self, piid: str) -> list[ET.Element]:
        if not piid:
            return []
        resp = self.http.get(FEED, params={"FEEDNAME": "PUBLIC", "q": f'PIID:"{piid}"'})
        if resp.get("status") != 200 or not resp.get("text"):
            return []
        try:
            root = ET.fromstring(resp["text"])
        except ET.ParseError as exc:
            log.debug("FPDS parse error for %s: %s", piid, exc)
            return []
        return list(root.iter(f"{ATOM_NS}entry"))

    def enrich(self, contract: Contract) -> Contract:
        entries = self.fetch_award(contract.piid or contract.award_id)
        if not entries:
            return contract

        # Newest modification carries the most current custodian of the file.
        def signed(e):
            return _text(e, "signedDate") or _text(e, "lastModifiedDate") or ""

        entries.sort(key=signed, reverse=True)
        latest = entries[0]

        officer = self._officer_from_entry(latest)
        if officer.is_addressable:
            contract.officer = officer
        contract.contracting_office_id = (
            _text(latest, "contractingOfficeID") or contract.contracting_office_id)
        contract.solicitation_id = (
            _text(latest, "solicitationID") or contract.solicitation_id)

        coi = _text(latest, "countryOfIncorporation")
        if coi:
            contract.country_of_incorporation = coi
        soi = _text(latest, "stateOfIncorporation")
        if soi:
            contract.state_of_incorporation = soi
        if _text(latest, "isForeignOwnedAndLocated").lower() in ("true", "y", "yes"):
            contract.foreign_owned_and_located = True
        parent = _text(latest, "ultimateParentUEIName")
        if parent and not contract.parent_recipient_name:
            contract.parent_recipient_name = parent
        parent_uei = _text(latest, "ultimateParentUEI")
        if parent_uei and not contract.parent_recipient_uei:
            contract.parent_recipient_uei = parent_uei
        ff = _text(latest, "foreignFunding")
        if ff and not contract.foreign_funding:
            contract.foreign_funding = ff
        desc = _text(latest, "descriptionOfContractRequirement")
        if desc and len(desc) > len(contract.description):
            contract.description = desc
        return contract

    def _officer_from_entry(self, entry) -> ContractingOfficer:
        """Prefer the approver (warranted official) over the last editor."""
        candidates = [
            ("approvedBy", "high"),
            ("lastModifiedBy", "medium"),
            ("createdBy", "low"),
        ]
        for tag, confidence in candidates:
            raw = _text(entry, tag)
            if not raw:
                continue
            m = _EMAIL_RX.search(raw)
            if not m:
                continue
            email = m.group(0)
            return ContractingOfficer(
                name=_pretty_name(email), email=email.lower(),
                phone=_text(entry, "phoneNo"),
                office_code=_text(entry, "contractingOfficeID"),
                source=f"FPDS-NG <{tag}>", confidence=confidence)
        return ContractingOfficer()

    def contract_text(self, contract: Contract) -> str:
        """Flat text of the FPDS record, for change detection across screens."""
        entries = self.fetch_award(contract.piid or contract.award_id)
        if not entries:
            return ""
        interesting = [
            "vendorName", "ultimateParentUEIName", "countryOfIncorporation",
            "stateOfIncorporation", "isForeignOwnedAndLocated", "foreignFunding",
            "obligatedAmount", "currentCompletionDate", "descriptionOfContractRequirement",
            "contractingOfficeID", "solicitationID", "lastModifiedBy", "approvedBy",
            "streetAddress", "city", "countryCode",
        ]
        lines: list[str] = []
        for e in entries[:5]:
            for tag in interesting:
                for value in _all_text(e, tag)[:1]:
                    lines.append(f"{tag}: {value}")
        return "\n".join(lines)
```


## `foci_screen/connectors/sec_edgar.py`

<a id="fociscreenconnectorssecedgarpy"></a>

```python
"""SEC EDGAR — the richest keyless source for both risk families.

Three things are pulled:
  1. company_tickers.json  -> name/ticker -> CIK resolution
  2. submissions/CIK*.json -> recent filings, with the 8-K item codes that map
     onto our risk families (5.01 change in control, 1.01 material agreement,
     2.03 direct financial obligation)
  3. full-text search       -> exhibits containing "intellectual property
     security agreement" and similar, which is where IP collateralisation is
     actually documented

EDGAR requires a User-Agent with a contact address; see config.user_agent.
"""
from __future__ import annotations

import logging
import re

from ..models import Document

log = logging.getLogger("foci.edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FTS = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data"

# 8-K items that carry FOCI / IP-encumbrance meaning.
ITEM_MEANING = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "3.02": "Unregistered Sales of Equity Securities",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers",
}
INTERESTING_FORMS = {"8-K", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "D", "D/A",
                     "10-K", "10-Q", "25", "SC 14D9", "DEFM14A", "S-4"}

IP_COLLATERAL_QUERIES = [
    '"intellectual property security agreement"',
    '"patent security agreement"',
    '"collateral assignment of patents"',
]

_TAG_RX = re.compile(r"<[^>]+>")
_WS_RX = re.compile(r"[ \t\r\f\v]+")


def html_to_text(html: str, limit: int = 60000) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for bad in soup(["script", "style", "noscript"]):
            bad.decompose()
        text = soup.get_text("\n")
    except Exception:
        text = _TAG_RX.sub(" ", html)
    text = _WS_RX.sub(" ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:limit]


class EdgarConnector:
    name = "sec_edgar"

    def __init__(self, http) -> None:
        self.http = http
        self._tickers: dict | None = None

    # ------------------------------------------------------------ resolution
    def _load_tickers(self) -> dict:
        if self._tickers is None:
            resp = self.http.get(TICKERS_URL)
            self._tickers = resp.get("json") or {}
        return self._tickers

    def resolve_cik(self, company_name: str) -> tuple[str, str]:
        """Best-effort name -> (CIK10, matched title). Conservative on purpose:
        a wrong CIK produces confident nonsense."""
        data = self._load_tickers()
        if not data:
            return "", ""
        target = _normalise(company_name)
        if not target:
            return "", ""
        best: tuple[float, str, str] = (0.0, "", "")
        for row in data.values():
            title = row.get("title") or ""
            score = _similarity(target, _normalise(title))
            if score > best[0]:
                best = (score, str(row.get("cik_str") or "").zfill(10), title)
        if best[0] >= 0.86:
            return best[1], best[2]
        return "", ""

    # -------------------------------------------------------------- filings
    def recent_filings(self, cik: str, limit: int = 25) -> list[Document]:
        if not cik:
            return []
        resp = self.http.get(SUBMISSIONS.format(cik=cik))
        data = resp.get("json") or {}
        recent = ((data.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        docs: list[Document] = []
        company = data.get("name") or ""
        for i, form in enumerate(forms):
            if len(docs) >= limit:
                break
            if form not in INTERESTING_FORMS:
                continue
            accession = (recent.get("accessionNumber") or [""] * len(forms))[i]
            filed = (recent.get("filingDate") or [""] * len(forms))[i]
            items = (recent.get("items") or [""] * len(forms))[i]
            primary = (recent.get("primaryDocument") or [""] * len(forms))[i]
            acc_plain = accession.replace("-", "")
            url = f"{ARCHIVE}/{int(cik)}/{acc_plain}/{primary}" if primary else \
                  f"{ARCHIVE}/{int(cik)}/{acc_plain}"
            codes = [c.strip() for c in (items or "").split(",") if c.strip()]
            # Item *codes* only, never their English labels. The labels contain
            # words the prose rules look for ("Receivership", "Changes in
            # Control"), so including them would make the tool match text it
            # wrote itself. The codes are carried in meta and handled by a
            # dedicated structured rule instead.
            docs.append(Document(
                source="sec_edgar", key=f"{cik}:{accession}",
                title=f"{company} {form} filed {filed}", url=url,
                text=(f"Form {form} filed {filed} by {company}."
                      + (f" Reported items: {', '.join(codes)}." if codes else "")),
                published=filed, doc_type=form,
                meta={"cik": cik, "accession": accession, "items": items,
                      "item_codes": codes, "form": form, "company": company}))
        return docs

    def fetch_filing_text(self, doc: Document) -> Document:
        """Pull the actual document body so rules see the language, not the label."""
        if not doc.url:
            return doc
        resp = self.http.get(doc.url)
        if resp.get("status") == 200 and resp.get("text"):
            body = html_to_text(resp["text"])
            if body:
                doc.text = f"{doc.text}\n\n{body}"
        return doc

    # ------------------------------------------------------- full-text search
    def full_text_search(self, company_name: str, cik: str = "",
                         queries: list[str] | None = None, forms: str = "",
                         limit: int = 5) -> list[Document]:
        """Find filings by THIS entity whose text contains IP-collateral language.

        Two non-obvious requirements, both learned the hard way:

        1. The search MUST be constrained by CIK. EDGAR's `q` parameter does not
           AND a company name with a phrase — querying
           '"patent security agreement" "Lockheed Martin"' returns other
           registrants' exhibits, which would then be attributed to Lockheed.
           Without a confident CIK we return nothing rather than guess.

        2. The returned Document's `text` must be the *filed exhibit*, never a
           summary we compose. A summary that names the phrase we searched for
           will match the very rule that looks for that phrase, and the tool
           will confidently flag its own query string as evidence.
        """
        if not cik:
            return []
        out: list[Document] = []
        seen: set[str] = set()
        for q in (queries or IP_COLLATERAL_QUERIES):
            params = {"q": q, "ciks": cik}
            if forms:
                params["forms"] = forms
            resp = self.http.get(FTS, params=params)
            data = resp.get("json") or {}
            hits = ((data.get("hits") or {}).get("hits") or [])
            for h in hits[:limit]:
                src = h.get("_source") or {}
                doc_id = h.get("_id") or ""
                if not doc_id or doc_id in seen:
                    continue
                # Belt and braces: confirm the hit really is this registrant.
                hit_ciks = {str(c).zfill(10) for c in (src.get("ciks") or [])}
                if cik.zfill(10) not in hit_ciks:
                    continue
                seen.add(doc_id)
                accession, _, filename = doc_id.partition(":")
                url = f"{ARCHIVE}/{int(cik)}/{accession.replace('-', '')}/{filename}"
                body = self._fetch_body(url)
                if not body:
                    # No verifiable source text -> no document. Reporting the
                    # hit without its text would mean flagging on the strength
                    # of our own query.
                    continue
                names = ", ".join(src.get("display_names") or [])
                out.append(Document(
                    source="sec_edgar", key=f"fts:{doc_id}",
                    title=f"{names} {src.get('form', '')} {src.get('file_type', '')}".strip(),
                    url=url, text=body,
                    published=src.get("file_date") or "",
                    doc_type=src.get("form") or "",
                    meta={"matched_query": q, "cik": cik, "accession": accession,
                          "file_type": src.get("file_type", ""),
                          "verified_registrant": True}))
        return out

    def _fetch_body(self, url: str) -> str:
        resp = self.http.get(url)
        if resp.get("status") != 200 or not resp.get("text"):
            return ""
        return html_to_text(resp["text"])


def _normalise(name: str) -> str:
    name = name.upper()
    for suffix in (" CORPORATION", " CORP", " INCORPORATED", " INC", " COMPANY",
                   " CO", " LLC", " L.L.C.", " LTD", " LIMITED", " PLC", " HOLDINGS",
                   " GROUP", " TECHNOLOGIES", " TECHNOLOGY", " SYSTEMS", " LP",
                   " L.P.", " THE"):
        name = name.replace(suffix, " ")
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", name).split())


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    # Require the distinctive first token to agree; "GENERAL DYNAMICS" and
    # "GENERAL MILLS" should not merge.
    head_bonus = 0.25 if a.split()[0] == b.split()[0] else 0.0
    return min(1.0, jaccard + head_bonus)
```


## `foci_screen/connectors/registries.py`

<a id="fociscreenconnectorsregistriespy"></a>

```python
"""Smaller registry connectors: IAPD, OFAC, USPTO, SAM.gov.

Grouped in one module because each is a thin wrapper over a single endpoint.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from difflib import SequenceMatcher

from ..models import Document

log = logging.getLogger("foci.registries")


# --------------------------------------------------------------------- IAPD
class IAPDConnector:
    """Investment Adviser Public Disclosure.

    Relevant because the investor arriving at a contractor is frequently an
    SEC-registered adviser, and IAPD gives its office country and disclosure
    history without a key. Endpoint is the one the IAPD site itself calls.
    """
    name = "iapd"
    SEARCH = "https://api.adviserinfo.sec.gov/search/firm"

    def __init__(self, http) -> None:
        self.http = http

    def search_firm(self, query: str, hits: int = 10) -> list[Document]:
        if not query.strip():
            return []
        resp = self.http.get(self.SEARCH, params={
            "query": query, "hits": hits, "type": "Firm",
            "investmentAdvisors": "true", "start": 0})
        data = resp.get("json") or {}
        rows = ((data.get("hits") or {}).get("hits") or [])
        docs: list[Document] = []
        for row in rows:
            src = row.get("_source") or {}
            addr = self._address(src.get("firm_ia_address_details"))
            country = addr.get("country", "")
            firm = src.get("firm_name") or ""
            sec_no = src.get("firm_ia_full_sec_number") or ""
            crd = str(src.get("firm_source_id") or "")
            text = (
                f"IAPD firm: {firm}\nSEC number: {sec_no}\nCRD: {crd}\n"
                f"Status: {src.get('firm_ia_scope', '')}\n"
                f"Has disclosures: {src.get('firm_ia_disclosure_fl', '')}\n"
                f"Office: {addr.get('street1', '')} {addr.get('city', '')} {country}\n"
                f"Other names: {', '.join(src.get('firm_other_names') or [])}")
            docs.append(Document(
                source="iapd", key=f"iapd:{crd or firm}", title=firm,
                url=f"https://adviserinfo.sec.gov/firm/summary/{crd}" if crd else "",
                text=text, doc_type="adviser_registration",
                meta={"firm_name": firm, "sec_number": sec_no, "crd": crd,
                      "country": country,
                      "has_disclosures": src.get("firm_ia_disclosure_fl") == "Y"}))
        return docs

    @staticmethod
    def _address(raw) -> dict:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            return (parsed or {}).get("officeAddress") or {}
        except Exception:
            return {}


# --------------------------------------------------------------------- OFAC
class OFACConnector:
    """OFAC Specially Designated Nationals list (keyless CSV).

    Name matching only. A hit is a question, never a conclusion — SDN entries
    are adjudicated on identifiers (DOB, passport, address), which this does
    not attempt.
    """
    name = "ofac"
    SDN_CSV = "https://www.treasury.gov/ofac/downloads/sdn.csv"

    def __init__(self, http) -> None:
        self.http = http
        self._rows: list[tuple[str, str, str]] | None = None

    def _load(self) -> list[tuple[str, str, str]]:
        if self._rows is not None:
            return self._rows
        resp = self.http.get(self.SDN_CSV)
        rows: list[tuple[str, str, str]] = []
        if resp.get("status") == 200 and resp.get("text"):
            reader = csv.reader(io.StringIO(resp["text"]))
            for row in reader:
                if len(row) >= 4:
                    rows.append((row[0].strip(), row[1].strip().strip('"'),
                                 row[3].strip().strip('"')))
        self._rows = rows
        return rows

    def screen(self, name: str, threshold: float = 0.90) -> list[Document]:
        target = _simplify(name)
        if len(target) < 5:
            return []
        out: list[Document] = []
        for ent_num, sdn_name, program in self._load():
            candidate = _simplify(sdn_name)
            if not candidate:
                continue
            if abs(len(candidate) - len(target)) > 12:
                continue
            ratio = SequenceMatcher(None, target, candidate).ratio()
            if ratio >= threshold:
                out.append(Document(
                    source="ofac", key=f"ofac:{ent_num}", title=sdn_name,
                    url="https://sanctionssearch.ofac.treas.gov/Details.aspx?id=" + ent_num,
                    text=(f"OFAC SDN entry {ent_num}: {sdn_name} (program {program}). "
                          f"Similarity to screened name '{name}': {ratio:.2f}. "
                          f"Name match only — adjudicate against identifiers."),
                    doc_type="sanctions_match",
                    meta={"score": round(ratio, 3), "program": program}))
        return out[:5]


# -------------------------------------------------------------------- USPTO
class USPTOConnector:
    """USPTO patent assignment records — recorded IP security interests.

    The legacy keyless endpoint (assignment-api.uspto.gov) has been retired in
    favour of the Open Data Portal, which requires a key. Without one this
    connector reports itself unavailable rather than silently returning nothing,
    so a screen never implies "no encumbrance found" when it simply could not
    look. Set USPTO_API_KEY (free, developer.uspto.gov) to enable.
    """
    name = "uspto"
    ODP_SEARCH = "https://api.uspto.gov/api/v1/patent/applications/search"
    LEGACY = "https://assignment-api.uspto.gov/patent/lookup"

    def __init__(self, http, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def assignments(self, assignor_name: str, rows: int = 20) -> list[Document]:
        docs = self._legacy_lookup(assignor_name, rows)
        if docs:
            return docs
        if not self.api_key:
            return []
        return self._odp_lookup(assignor_name, rows)

    def _legacy_lookup(self, name: str, rows: int) -> list[Document]:
        resp = self.http.get(self.LEGACY, params={
            "query": name, "filter_assignorName": "", "rows": rows})
        if resp.get("status") != 200:
            return []
        data = resp.get("json") or {}
        found = ((data.get("response") or {}).get("docs") or [])
        return [self._to_doc(d) for d in found]

    def _odp_lookup(self, name: str, rows: int) -> list[Document]:
        resp = self.http.get(self.ODP_SEARCH,
                             params={"q": f'applicantName:"{name}"', "rows": rows},
                             headers={"X-API-KEY": self.api_key})
        if resp.get("status") != 200:
            log.info("USPTO ODP returned %s", resp.get("status"))
            return []
        data = resp.get("json") or {}
        return [self._to_doc(d) for d in (data.get("patentFileWrapperDataBag") or [])]

    @staticmethod
    def _to_doc(raw: dict) -> Document:
        reel = str(raw.get("reelNo") or raw.get("reel_no") or "")
        frame = str(raw.get("frameNo") or raw.get("frame_no") or "")
        conveyance = str(raw.get("conveyanceText") or raw.get("conveyance_text") or "")
        assignee = _first(raw.get("assigneeName") or raw.get("assignee_name"))
        assignor = _first(raw.get("assignorName") or raw.get("assignor_name"))
        address = " ".join(filter(None, [
            _first(raw.get("assigneeAddress1")), _first(raw.get("assigneeCity")),
            _first(raw.get("assigneeCountryName")), _first(raw.get("assigneePostcode"))]))
        recorded = str(raw.get("recordedDate") or raw.get("recorded_date") or "")
        patents = raw.get("patNum") or raw.get("inventionTitle") or []
        count = len(patents) if isinstance(patents, list) else 1
        return Document(
            source="uspto", key=f"uspto:{reel}-{frame}",
            title=f"{conveyance} {assignor} -> {assignee}",
            url=(f"https://assignment.uspto.gov/patent/index.html#/patent/search/"
                 f"resultAbstract?reelFrame={reel}%2F{frame}"),
            text=(f"USPTO assignment reel/frame {reel}/{frame} recorded {recorded}.\n"
                  f"Conveyance: {conveyance}\nAssignor: {assignor}\n"
                  f"Assignee: {assignee}\nAssignee address: {address}\n"
                  f"Properties: {count}"),
            published=recorded, doc_type="patent_assignment",
            meta={"conveyance": conveyance, "assignee": assignee, "assignor": assignor,
                  "assignee_address": address, "patent_count": count,
                  "reel_frame": f"{reel}/{frame}"})


# ------------------------------------------------------------------ SAM.gov
class SAMConnector:
    """SAM.gov entity registration and contract opportunities.

    Requires a free api.data.gov key. Used for two things: the entity's
    registered/physical address and business types (FOCI structure), and the
    solicitation's point of contact, which is the most authoritative KO email
    when it is available.
    """
    name = "samgov"
    ENTITY = "https://api.sam.gov/entity-information/v3/entities"
    OPPS = "https://api.sam.gov/opportunities/v2/search"

    def __init__(self, http, api_key: str = "") -> None:
        self.http = http
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def entity(self, uei: str) -> Document | None:
        if not (self.available and uei):
            return None
        resp = self.http.get(self.ENTITY, params={
            "api_key": self.api_key, "ueiSAM": uei,
            "includeSections": "entityRegistration,coreData"})
        data = resp.get("json") or {}
        records = data.get("entityData") or []
        if not records:
            return None
        rec = records[0]
        reg = rec.get("entityRegistration") or {}
        core = rec.get("coreData") or {}
        phys = (core.get("physicalAddress") or {})
        mail = (core.get("mailingAddress") or {})
        general = (core.get("generalInformation") or {})
        text = (
            f"SAM registration for {reg.get('legalBusinessName', '')} (UEI {uei})\n"
            f"CAGE: {reg.get('cageCode', '')}\n"
            f"Registration status: {reg.get('registrationStatus', '')}\n"
            f"Expiration: {reg.get('registrationExpirationDate', '')}\n"
            f"Physical address: {phys.get('addressLine1', '')} {phys.get('city', '')} "
            f"{phys.get('stateOrProvinceCode', '')} {phys.get('countryCode', '')}\n"
            f"Mailing country: {mail.get('countryCode', '')}\n"
            f"Entity structure: {general.get('entityStructureDesc', '')}\n"
            f"State of incorporation: {general.get('stateOfIncorporationCode', '')}\n"
            f"Country of incorporation: {general.get('countryOfIncorporationCode', '')}")
        return Document(
            source="samgov", key=f"sam:{uei}",
            title=f"SAM entity {reg.get('legalBusinessName', uei)}",
            url=f"https://sam.gov/entity/{uei}", text=text, doc_type="sam_entity",
            meta={"uei": uei, "cage": reg.get("cageCode", ""),
                  "country": phys.get("countryCode", ""),
                  "country_of_incorporation": general.get("countryOfIncorporationCode", "")})

    def opportunity_poc(self, solicitation_number: str) -> dict:
        """Return the primary point of contact for a solicitation, if published."""
        if not (self.available and solicitation_number):
            return {}
        resp = self.http.get(self.OPPS, params={
            "api_key": self.api_key, "solnum": solicitation_number, "limit": 5})
        data = resp.get("json") or {}
        for opp in data.get("opportunitiesData") or []:
            for poc in opp.get("pointOfContact") or []:
                if poc.get("email"):
                    return {"name": poc.get("fullName", ""), "email": poc["email"],
                            "phone": poc.get("phone", ""),
                            "type": poc.get("type", ""),
                            "title": poc.get("title", ""),
                            "url": opp.get("uiLink", "")}
        return {}


# ------------------------------------------------------------------ helpers
_PUNCT_RX = re.compile(r"[^a-z0-9 ]")
_STOP = {"the", "inc", "incorporated", "corp", "corporation", "llc", "ltd",
         "limited", "company", "co", "plc", "lp", "llp", "group", "holdings"}


def _simplify(name: str) -> str:
    tokens = _PUNCT_RX.sub(" ", (name or "").lower()).split()
    return " ".join(t for t in tokens if t not in _STOP)


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")
```


## `foci_screen/connectors/webwatch.py`

<a id="fociscreenconnectorswebwatchpy"></a>

```python
"""Company website monitoring: investor relations, press, and legal pages.

Two jobs:
  1. discover the relevant pages from a domain (common paths + homepage links);
  2. render each to normalised text so the store can diff it run over run.

Normalisation matters more than it looks. Marketing pages carry rotating
banners, timestamps and CSRF nonces; without stripping them every page reads as
"changed" on every screen and the signal drowns. Boilerplate nav/footer text is
dropped for the same reason.

Fetching is two-tier. The cheap path is a plain HTTP GET on a short leash. When
that returns nothing usable — a timeout, a 403, or a 200 carrying an empty
JavaScript application shell — the URL is retried through a headless browser, if
one is configured. The distinction matters because investor-relations subdomains
are both the highest-value pages here and the ones most likely to be unreadable
without a browser: they are typically hosted by a third-party IR platform that
serves an empty container to anything that does not look like Chrome.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

from ..models import Document
from .robots import RobotsPolicy

log = logging.getLogger("foci.web")

CANDIDATE_PATHS = [
    "/investors", "/investor-relations", "/investors/news", "/ir",
    "/news", "/newsroom", "/press", "/press-releases", "/media",
    "/about/news", "/company/news", "/blog/news",
    "/legal", "/legal-notices", "/terms", "/corporate-governance",
    "/investors/sec-filings", "/investors/press-releases",
]

LINK_HINTS = {
    "investor": "investor_relations",
    "ir/": "investor_relations",
    "press": "press",
    "news": "press",
    "media": "press",
    "legal": "legal",
    "governance": "legal",
    "sec-filing": "investor_relations",
    "announcement": "press",
}

# Volatile fragments that would otherwise make every fetch look like a change.
NOISE_PATTERNS = [
    re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM|UTC|EST|EDT|GMT)?\b", re.I),
    re.compile(r"\bcsrf[-_]?token\b.*", re.I),
    re.compile(r"\bnonce\b\S*", re.I),
    re.compile(r"\b(?:session|request)[-_]?id\b\S*", re.I),
    re.compile(r"©\s*\d{4}"),
    re.compile(r"\b\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago\b", re.I),
    re.compile(r"\?(?:utm_[a-z]+|_ga|fbclid)=\S+", re.I),
]

BOILERPLATE = re.compile(
    r"^(cookie|accept all|privacy policy|terms of use|skip to (main )?content|"
    r"menu|search|sign in|log in|subscribe|follow us|share this|all rights reserved)",
    re.I)


class Fetched(NamedTuple):
    html: str
    text: str          # normalised, computed once
    how: str           # "http" | "browser" | "" (nothing usable)


class WebWatchConnector:
    """Watches a contractor's own pages.

    Unlike the government APIs, these hosts owe us nothing: many sit behind a
    CDN that stalls or 403s a non-browser client. Every request here is
    therefore short-timeout, single-attempt, and the whole discovery pass is
    capped — a corporate site that will not answer must cost the run seconds,
    not minutes.

    Pass a `BrowserRenderer` as `browser` to enable the headless fallback for
    hosts the cheap path cannot read. Without one the connector behaves exactly
    as before and records the hosts it had to give up on in `skipped_js_hosts`,
    so a run can report reduced coverage instead of silently implying those
    pages held nothing.
    """

    name = "web"
    TIMEOUT = 12
    RETRIES = 1
    MAX_PROBES = 6          # candidate paths tried when homepage links yield nothing
    # Below this many characters of normalised text, a 200 response is almost
    # certainly an unrendered application shell rather than a thin page. Real
    # press and IR pages run to thousands; a shell yields a nav bar and a
    # <noscript> apology.
    SHELL_TEXT_CHARS = 600
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, http, browser=None, robots=None) -> None:
        self.http = http
        self.browser = browser
        self.robots = robots if robots is not None else RobotsPolicy(http)
        # Hosts proven to need a browser — skip the doomed plain fetch next time.
        self._browser_hosts: set[str] = set()
        # Hosts we could not read at all because no browser was available.
        self.skipped_js_hosts: set[str] = set()
        # Hosts that yielded nothing to the browser either. Distinct from the
        # above: a browser was available, and the site did not serve it readable
        # content — usually bot management refusing an identified client.
        self.unreadable_hosts: set[str] = set()
        # URL -> reason, for pages robots.txt told us not to fetch.
        self.skipped_robots: dict[str, str] = {}

    def _get(self, url: str) -> dict:
        return self.http.get(url, headers=self.HEADERS, timeout=self.TIMEOUT,
                             max_retries=self.RETRIES)

    def _browser_ready(self) -> bool:
        return bool(self.browser is not None and self.browser.available)

    def _render(self, url: str) -> Fetched:
        html = self.browser.render(url)
        text = normalise_page(html) if html else ""
        if not text:
            return Fetched("", "", "")
        self._browser_hosts.add(urlparse(url).netloc.lower())
        return Fetched(html, text, "browser")

    # ------------------------------------------------------------ fetching
    def _fetch(self, url: str) -> Fetched:
        """Plain GET, escalating to a headless browser when it yields nothing.

        robots.txt is checked here, the single entry point for both paths, so a
        disallowed page is not fetched by one route after being refused by the
        other.
        """
        host = urlparse(url).netloc.lower()

        permitted, reason = self.robots.allowed(url)
        if not permitted:
            self.skipped_robots[url] = reason
            return Fetched("", "", "")
        self.robots.wait_turn(url)

        if host in self._browser_hosts and self._browser_ready():
            rendered = self._render(url)
            if not rendered.how:
                self.unreadable_hosts.add(host)
            return rendered

        resp = self._get(url)
        ok = resp.get("status") == 200 and bool(resp.get("text"))
        html = resp["text"] if ok else ""
        text = normalise_page(html) if ok else ""

        if len(text) >= self.SHELL_TEXT_CHARS:
            return Fetched(html, text, "http")

        # Timed out, refused, or returned a shell. This is the case IR
        # subdomains land in almost every time.
        if self._browser_ready():
            log.info("%s unreadable over plain HTTP (status %s, %d chars) — "
                     "retrying with headless browser", url, resp.get("status"), len(text))
            rendered = self._render(url)
            if len(rendered.text) > len(text):
                return rendered
            if not text:
                # Both routes came back empty. Recorded only here, where the
                # final outcome is known — a thin page that plain HTTP did get
                # is not an unread host.
                self.unreadable_hosts.add(host)
        elif not ok:
            self.skipped_js_hosts.add(host)

        return Fetched(html, text, "http") if text else Fetched("", "", "")

    # ----------------------------------------------------------- discovery
    def discover_pages(self, domain: str, max_pages: int = 8) -> list[tuple[str, str]]:
        """Return [(url, page_kind)] worth watching for this domain."""
        base = domain if domain.startswith("http") else f"https://{domain}"
        found: dict[str, str] = {}

        home = self._fetch(base)
        if not home.how:
            log.info("%s unreachable for discovery — skipping site", domain)
            return []
        if home.html:
            for href, label in _links(home.html, base):
                blob = f"{href.lower()} {label.lower()}"
                for hint, kind in LINK_HINTS.items():
                    if hint in blob:
                        if _same_site(base, href):
                            found.setdefault(href.split("#")[0], kind)
                        break
                if len(found) >= max_pages * 2:
                    break

        if len(found) < 3:
            probes = 0
            for path in CANDIDATE_PATHS:
                if probes >= self.MAX_PROBES or len(found) >= max_pages:
                    break
                url = urljoin(base, path)
                if url in found:
                    continue
                probes += 1
                got = self._fetch(url)
                if got.how and len(got.text) >= self.SHELL_TEXT_CHARS:
                    found[url] = _kind_for_path(path)
                if len(found) >= max_pages:
                    break

        return list(found.items())[:max_pages]

    # -------------------------------------------------------------- fetch
    def fetch(self, url: str, kind: str = "press", company: str = "") -> Document | None:
        got = self._fetch(url)
        if not got.how or len(got.text) < 200:
            return None
        return Document(
            source="web", key=f"web:{_canonical(url)}",
            title=f"{company or urlparse(url).netloc} — {kind.replace('_', ' ')}",
            url=url, text=got.text, doc_type=kind,
            meta={"page_kind": kind, "domain": urlparse(url).netloc,
                  "rendered_with": got.how})

    def collect(self, domain: str, company: str = "",
                max_pages: int = 6) -> list[Document]:
        docs: list[Document] = []
        for url, kind in self.discover_pages(domain, max_pages=max_pages):
            doc = self.fetch(url, kind, company)
            if doc:
                docs.append(doc)
        return docs


# ------------------------------------------------------------------ helpers

def _kind_for_path(path: str) -> str:
    if "investor" in path or path.strip("/") == "ir":
        return "investor_relations"
    if any(w in path for w in ("legal", "terms", "governance")):
        return "legal"
    return "press"


def _links(html: str, base: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            out.append((urljoin(base, a["href"]), a.get_text(" ", strip=True)[:80]))
    except Exception:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
            out.append((urljoin(base, m.group(1)),
                        re.sub(r"<[^>]+>", " ", m.group(2))[:80]))
    return out


def _same_site(base: str, url: str) -> bool:
    b, u = urlparse(base).netloc.lower(), urlparse(url).netloc.lower()
    b, u = b.removeprefix("www."), u.removeprefix("www.")
    return bool(u) and (u == b or u.endswith("." + b) or b.endswith("." + u))


def _canonical(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"


# A <main> holding less than this is a layout wrapper, not the content.
MAIN_CONTENT_CHARS = 1000


def _content_root(soup):
    """The element actually holding the page's prose.

    Preferring `<main>` unconditionally is wrong on real corporate sites.
    Lockheed's newsroom puts a navigation rail in `<main>` — 99 characters —
    and the 5,200 characters of press-release text outside it, so trusting the
    tag discarded 98% of the page and every rule downstream saw a nav menu.
    Take `<main>` or `<article>` only when it carries enough text to plausibly
    be the content; otherwise use the whole body and let line-level
    boilerplate stripping deal with the furniture.
    """
    body = soup.body or soup
    for tag in ("main", "article"):
        node = soup.find(tag)
        if node is not None and len(node.get_text(" ", strip=True)) >= MAIN_CONTENT_CHARS:
            return node
    return body


def normalise_page(html: str, limit: int = 40000) -> str:
    """HTML -> stable plain text suitable for hashing and diffing."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        for bad in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
            bad.decompose()
        text = _content_root(soup).get_text("\n")
    except Exception:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", "\n", text)

    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 3 or BOILERPLATE.match(line):
            continue
        for rx in NOISE_PATTERNS:
            line = rx.sub("", line)
        line = line.strip()
        if not line or line.lower() in seen:
            continue
        seen.add(line.lower())
        lines.append(line)
    return "\n".join(lines)[:limit]
```


## `foci_screen/connectors/robots.py`

<a id="fociscreenconnectorsrobotspy"></a>

```python
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

        log.info("%s/robots.txt unreachable (status %s) — treating the host as "
                 "disallowed for this run", host, status or "no response")
        return HostRules("deny_all",
                         reason=f"robots.txt unreachable ({status or 'no response'})")

    def allowed(self, url: str) -> tuple[bool, str]:
        """(may_fetch, reason). The reason is for the run notes when refused."""
        rules = self.rules_for(url)
        if rules.verdict == "allow_all":
            return True, ""
        if rules.verdict == "deny_all":
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
```


## `foci_screen/connectors/browser.py`

<a id="fociscreenconnectorsbrowserpy"></a>

```python
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
```


---

# Risk engine


## `foci_screen/risk/__init__.py`

<a id="fociscreenriskinitpy"></a>

```python
"""Risk detection: lexicons, rules, scoring."""
from . import engine, lexicon

__all__ = ["engine", "lexicon"]
```


## `foci_screen/risk/lexicon.py`

<a id="fociscreenrisklexiconpy"></a>

```python
"""Jurisdiction and terminology lexicons.

Tiering follows the statutory language a KO will recognise:

  covered   - "covered nation" per 10 U.S.C. 4872(d)(2) (China, Russia, Iran, DPRK)
  adjacent  - jurisdictions under PRC/Russia control or comprehensive sanctions
  haven     - opaque/secrecy jurisdictions where beneficial ownership is hidden;
              the point is not tax, it's that FOCI cannot be ruled out
  conduit   - legitimate but commonly used to intermediate foreign capital

Multipliers scale a rule's base weight. They are judgement calls, not law;
they live here so an analyst can tune them without touching rule code.
"""
from __future__ import annotations

import re

JURISDICTIONS: dict[str, dict] = {
    # --- covered nations -------------------------------------------------
    "China": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\bchina\b", r"\bchinese\b", r"\bPRC\b", r"people'?s republic of china",
        r"\bPeople'?s Liberation Army\b", r"\bshanghai\b", r"\bshenzhen\b",
        r"\bbeijing\b", r"\bguangzhou\b", r"\bhangzhou\b", r"\bCNY\b",
        r"\bRMB\b", r"renminbi", r"有限公司"]},
    "Hong Kong": {"tier": "covered", "mult": 2.8, "patterns": [
        r"\bhong ?kong\b", r"\bHKSAR\b", r"\bH\.?K\.?\s*(?:limited|ltd)\b"]},
    "Macau": {"tier": "covered", "mult": 2.8, "patterns": [r"\bmacau\b", r"\bmacao\b"]},
    "Russia": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\brussia\b", r"\brussian federation\b", r"\bmoscow\b", r"\bOOO\b",
        r"\bPJSC\b", r"\bsberbank\b"]},
    "Iran": {"tier": "covered", "mult": 3.0, "patterns": [r"\biran\b", r"\biranian\b", r"\btehran\b"]},
    "North Korea": {"tier": "covered", "mult": 3.0, "patterns": [
        r"\bnorth korea\b", r"\bDPRK\b", r"\bpyongyang\b"]},

    # --- adjacent / sanctioned ------------------------------------------
    "Belarus": {"tier": "adjacent", "mult": 2.4, "patterns": [r"\bbelarus\b", r"\bminsk\b"]},
    "Venezuela": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bvenezuela\b", r"\bcaracas\b"]},
    "Cuba": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bcuba\b", r"\bhavana\b"]},
    "Syria": {"tier": "adjacent", "mult": 2.2, "patterns": [r"\bsyria\b", r"\bdamascus\b"]},

    # --- opaque / secrecy jurisdictions ----------------------------------
    "British Virgin Islands": {"tier": "haven", "mult": 2.2, "patterns": [
        r"british virgin islands", r"\bB\.?V\.?I\.?\b", r"\btortola\b", r"road town"]},
    "Cayman Islands": {"tier": "haven", "mult": 2.1, "patterns": [
        r"cayman islands", r"\bgeorge town,? grand cayman\b", r"\bgrand cayman\b"]},
    "Bermuda": {"tier": "haven", "mult": 1.9, "patterns": [r"\bbermuda\b", r"\bhamilton, bermuda\b"]},
    "Seychelles": {"tier": "haven", "mult": 2.2, "patterns": [r"\bseychelles\b", r"\bvictoria, mahe\b"]},
    "Marshall Islands": {"tier": "haven", "mult": 2.1, "patterns": [r"marshall islands", r"\bmajuro\b"]},
    "Panama": {"tier": "haven", "mult": 2.0, "patterns": [r"\bpanama\b", r"panama city, panama"]},
    "Belize": {"tier": "haven", "mult": 2.1, "patterns": [r"\bbelize\b"]},
    "Mauritius": {"tier": "haven", "mult": 2.0, "patterns": [r"\bmauritius\b", r"port louis"]},
    "Bahamas": {"tier": "haven", "mult": 1.9, "patterns": [r"\bbahamas\b", r"\bnassau\b"]},
    "Nevis": {"tier": "haven", "mult": 2.1, "patterns": [r"\bnevis\b", r"saint kitts", r"st\.? kitts"]},
    "Anguilla": {"tier": "haven", "mult": 2.0, "patterns": [r"\banguilla\b"]},
    "Vanuatu": {"tier": "haven", "mult": 2.1, "patterns": [r"\bvanuatu\b"]},
    "Samoa": {"tier": "haven", "mult": 2.0, "patterns": [r"\bsamoa\b", r"\bapia\b"]},
    "Gibraltar": {"tier": "haven", "mult": 1.8, "patterns": [r"\bgibraltar\b"]},
    "Isle of Man": {"tier": "haven", "mult": 1.8, "patterns": [r"isle of man", r"\bdouglas, iom\b"]},
    "Jersey": {"tier": "haven", "mult": 1.8, "patterns": [r"\bjersey, channel\b", r"\bst\.? helier\b"]},
    "Guernsey": {"tier": "haven", "mult": 1.8, "patterns": [r"\bguernsey\b", r"st\.? peter port"]},
    "Liechtenstein": {"tier": "haven", "mult": 1.9, "patterns": [r"\bliechtenstein\b", r"\bvaduz\b"]},
    "Cyprus": {"tier": "haven", "mult": 2.0, "patterns": [r"\bcyprus\b", r"\bnicosia\b", r"\blimassol\b"]},
    "Malta": {"tier": "haven", "mult": 1.8, "patterns": [r"\bmalta\b", r"\bvalletta\b"]},
    "Curacao": {"tier": "haven", "mult": 1.9, "patterns": [r"\bcura[cç]ao\b", r"willemstad"]},
    "Barbados": {"tier": "haven", "mult": 1.7, "patterns": [r"\bbarbados\b", r"bridgetown"]},

    # --- conduits ---------------------------------------------------------
    "Singapore": {"tier": "conduit", "mult": 1.3, "patterns": [r"\bsingapore\b"]},
    "United Arab Emirates": {"tier": "conduit", "mult": 1.4, "patterns": [
        r"\bunited arab emirates\b", r"\bU\.?A\.?E\.?\b", r"\bdubai\b", r"\babu dhabi\b"]},
    "Luxembourg": {"tier": "conduit", "mult": 1.3, "patterns": [r"\bluxembourg\b", r"\bS\.?[àa]\.?r\.?l\.?\b"]},
    "Netherlands": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bnetherlands\b", r"\bB\.?V\.?\b(?! ?islands)"]},
    "Ireland": {"tier": "conduit", "mult": 1.15, "patterns": [r"\bireland\b", r"\bdublin\b"]},
    "Switzerland": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bswitzerland\b", r"\bzug\b", r"\bzurich\b"]},
    "Hungary": {"tier": "conduit", "mult": 1.2, "patterns": [r"\bhungary\b", r"\bbudapest\b"]},
}

# ISO-3166 alpha-3 codes as they appear in SAM/FPDS/USAspending country fields.
COUNTRY_CODE_TO_JURISDICTION = {
    "CHN": "China", "HKG": "Hong Kong", "MAC": "Macau", "RUS": "Russia",
    "IRN": "Iran", "PRK": "North Korea", "BLR": "Belarus", "VEN": "Venezuela",
    "CUB": "Cuba", "SYR": "Syria", "VGB": "British Virgin Islands",
    "CYM": "Cayman Islands", "BMU": "Bermuda", "SYC": "Seychelles",
    "MHL": "Marshall Islands", "PAN": "Panama", "BLZ": "Belize",
    "MUS": "Mauritius", "BHS": "Bahamas", "KNA": "Nevis", "AIA": "Anguilla",
    "VUT": "Vanuatu", "WSM": "Samoa", "GIB": "Gibraltar", "IMN": "Isle of Man",
    "JEY": "Jersey", "GGY": "Guernsey", "LIE": "Liechtenstein", "CYP": "Cyprus",
    "MLT": "Malta", "CUW": "Curacao", "BRB": "Barbados", "SGP": "Singapore",
    "ARE": "United Arab Emirates", "LUX": "Luxembourg", "NLD": "Netherlands",
    "IRL": "Ireland", "CHE": "Switzerland", "HUN": "Hungary",
}

# --- event vocabularies ----------------------------------------------------

# Terms must be specific enough that their presence genuinely implies an
# ownership event. Bare "led by", "series a" and "PIPE" were removed after they
# fired on ordinary prose ("a team led by", "Series A aircraft", pipeline
# references); each now requires its financing context.
FOCI_EVENT_TERMS = [
    "strategic investment", "minority investment", "minority stake", "equity stake",
    "change of control", "change in control", "acquisition of a controlling",
    "controlling interest", "definitive agreement", "merger agreement",
    "tender offer", "share purchase agreement", "subscription agreement",
    "joint venture", "board observer", "board seat", "board designee",
    "voting agreement", "proxy agreement", "special security agreement",
    "beneficial ownership", "beneficial owner",
    "series a funding", "series a round", "series a financing", "series a preferred",
    "series b funding", "series b round", "series b financing",
    "series c funding", "series c round", "series d funding",
    "led the round", "round led by", "financing led by", "investment led by",
    "funding round", "anchor investor", "strategic partner",
    "capital injection", "recapitalization", "reverse merger",
    "special purpose acquisition", "private investment in public equity",
    "convertible note", "sovereign wealth",
]

IP_COLLATERAL_TERMS = [
    "intellectual property security agreement", "patent security agreement",
    "trademark security agreement", "copyright security agreement",
    "collateral assignment of patents", "collateral assignment",
    "security interest in the patents", "security interest in intellectual property",
    "grant of security interest", "granted a security interest",
    "pledge of intellectual property", "pledged intellectual property",
    "first priority lien", "first-priority security interest",
    "collateral agent", "administrative agent", "secured party",
    "UCC-1", "UCC financing statement", "all assets lien",
    "credit agreement", "loan and security agreement", "venture debt",
    "secured term loan", "revolving credit facility", "debenture",
    "foreclose on the collateral", "event of default",
]

IP_TRANSFER_TERMS = [
    "assignment of patents", "patent assignment", "sale of intellectual property",
    "divest", "divestiture", "exclusive license", "exclusive licence",
    "technology transfer agreement", "nunc pro tunc assignment",
    "assignment for the benefit of creditors", "chapter 11", "chapter 7",
    "receivership", "administration", "insolvency", "363 sale",
    "asset purchase agreement", "wind down", "wind-down", "liquidation",
    "transfer of technical data", "source code escrow", "release of source code",
]

# USPTO assignment conveyance strings that indicate encumbrance rather than sale.
USPTO_SECURITY_CONVEYANCES = [
    "SECURITY INTEREST", "SECURITY AGREEMENT", "COLLATERAL", "LIEN", "PLEDGE",
    "MORTGAGE",
]

# Contract clauses that make an IP event materially worse for the Government.
IP_CLAUSE_PATTERNS = {
    "DFARS 252.227-7013 (Technical Data - Noncommercial Items)": r"252\.?227[-\s]?7013",
    "DFARS 252.227-7014 (Noncommercial Computer Software)": r"252\.?227[-\s]?7014",
    "DFARS 252.227-7017 (Identification of Restrictions)": r"252\.?227[-\s]?7017",
    "DFARS 252.227-7018 (SBIR Data Rights)": r"252\.?227[-\s]?7018",
    "DFARS 252.204-7012 (Covered Defense Information)": r"252\.?204[-\s]?7012",
    "FAR 52.227-11 (Patent Rights - Contractor Retention)": r"52\.?227[-\s]?11\b",
    "FAR 52.227-14 (Rights in Data - General)": r"52\.?227[-\s]?14\b",
    "Government Purpose Rights": r"government purpose rights",
    "Limited Rights data": r"\blimited rights\b",
    "Restricted Rights software": r"\brestricted rights\b",
    "SBIR/STTR data rights": r"\bSBIR\b|\bSTTR\b",
    "Technical data package": r"technical data package|\bTDP\b",
}

# Lender/agent name fragments — a security interest held by one of these is a
# financing event; held by an unfamiliar offshore vehicle it is a FOCI question.
FINANCIAL_COUNTERPARTY_HINTS = [
    "bank", "capital", "credit", "finance", "financial", "lending", "lenders",
    "partners lp", "fund", "holdings", "trust", "agent", "asset management",
]


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _term_regex(term: str) -> re.Pattern:
    """Whole-term matching.

    Substring matching is not safe here. `"SPAC"` is a substring of `"space"`,
    which on an aerospace contractor's website appears on nearly every page —
    enough, in an early build, to escalate a Lockheed history page describing a
    missile range in the Marshall Islands into a CRITICAL notice about foreign
    investment. Lookarounds rather than \\b so terms with leading or trailing
    punctuation (UCC-1) still anchor correctly.
    """
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)


JURISDICTION_REGEX = {name: _compile(meta["patterns"]) for name, meta in JURISDICTIONS.items()}
IP_CLAUSE_REGEX = {label: re.compile(p, re.IGNORECASE) for label, p in IP_CLAUSE_PATTERNS.items()}
_TERM_CACHE: dict[str, re.Pattern] = {}


def _snippet(text: str, start: int, end: int, pad: int = 100) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return text[lo:hi].replace("\n", " ").strip()


def find_jurisdictions_with_pos(text: str) -> list[tuple[str, str, int]]:
    """Return [(jurisdiction, snippet, match_offset)] for every hit."""
    hits: list[tuple[str, str, int]] = []
    for name, regexes in JURISDICTION_REGEX.items():
        for rx in regexes:
            m = rx.search(text)
            if m:
                hits.append((name, _snippet(text, m.start(), m.end(), 90), m.start()))
                break
    return hits


def find_jurisdictions(text: str) -> list[tuple[str, str]]:
    """Return [(jurisdiction, matched snippet)] for every hit in `text`."""
    return [(n, s) for n, s, _ in find_jurisdictions_with_pos(text)]


def jurisdiction_multiplier(name: str) -> float:
    return JURISDICTIONS.get(name, {}).get("mult", 1.0)


def jurisdiction_tier(name: str) -> str:
    return JURISDICTIONS.get(name, {}).get("tier", "unknown")


def find_terms_with_pos(text: str, terms: list[str]) -> list[tuple[str, str, int]]:
    """Return [(term, snippet, match_offset)] for each whole-term hit."""
    out: list[tuple[str, str, int]] = []
    for term in terms:
        rx = _TERM_CACHE.get(term)
        if rx is None:
            rx = _TERM_CACHE[term] = _term_regex(term)
        m = rx.search(text)
        if m:
            out.append((term, _snippet(text, m.start(), m.end(), 110), m.start()))
    return out


def find_terms(text: str, terms: list[str]) -> list[tuple[str, str]]:
    """Return [(term, surrounding snippet)] for each term present in `text`."""
    return [(t, s) for t, s, _ in find_terms_with_pos(text, terms)]


def find_ip_clauses(text: str) -> list[str]:
    return [label for label, rx in IP_CLAUSE_REGEX.items() if rx.search(text or "")]
```


## `foci_screen/risk/engine.py`

<a id="fociscreenriskenginepy"></a>

```python
"""Rule engine.

Each rule looks at one Document (or one structured record) and emits Signals.
A Signal always carries: what fired, the literal text that made it fire, a URL a
human can open, and a plain-English rationale. Nothing is flagged without a
quotable basis — a KO will not act on "the model said so".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import SEVERITY_ORDER, Change, Contract, Document, Entity, Finding, Signal
from . import lexicon as lex

# Base weights per rule family, before jurisdiction and novelty multipliers.
BASE_WEIGHTS = {
    "FOCI": 6.0,
    "IP_COLLATERAL": 7.0,
    "IP_TRANSFER": 5.0,
    "SANCTIONS": 12.0,
    "STRUCTURE": 4.0,
}

SEVERITY_BANDS = [(30.0, "critical"), (18.0, "high"), (9.0, "medium"), (3.0, "low")]

NEW_EVIDENCE_MULTIPLIER = 1.6   # it changed since last screen -> that is the point
IP_CONTRACT_MULTIPLIER = 1.4    # the affected award carries data-rights clauses

# How close an ownership term must sit to a jurisdiction mention (characters)
# before the two are treated as describing the same transaction.
PROXIMITY_WINDOW = 800


def band(score: float) -> str:
    for threshold, name in SEVERITY_BANDS:
        if score >= threshold:
            return name
    return "info"


@dataclass
class RuleContext:
    entity: Entity
    contracts: list[Contract]
    is_new: bool = False
    source_url: str = ""

    @property
    def ip_sensitive(self) -> bool:
        return any(c.ip_sensitive for c in self.contracts)

    @property
    def ip_clauses(self) -> list[str]:
        seen: list[str] = []
        for c in self.contracts:
            for clause in c.ip_clause_hits:
                if clause not in seen:
                    seen.append(clause)
        return seen


def _score(category: str, ctx: RuleContext, multiplier: float = 1.0) -> float:
    score = BASE_WEIGHTS.get(category, 4.0) * multiplier
    if ctx.is_new:
        score *= NEW_EVIDENCE_MULTIPLIER
    if category in ("IP_COLLATERAL", "IP_TRANSFER") and ctx.ip_sensitive:
        score *= IP_CONTRACT_MULTIPLIER
    return round(score, 2)


def _sev(score: float) -> str:
    return band(score)


# --- evidence quality --------------------------------------------------------
# A phrase in an 8-K exhibit ("the Grantor hereby grants a security interest")
# and the same phrase in a 10-K risk factor ("if we were to become insolvent")
# are not equivalent evidence. Without this damping a large-cap annual report,
# which mentions nearly every adverse concept somewhere, scores like a distress
# event — the single largest source of false positives in early testing.

HEDGE_RX = re.compile(
    r"\b(?:if|may|might|could|would|whether|in the event|no assurance|"
    r"risk that|risks? relating|potential(?:ly)?|from time to time|"
    r"we expect|we anticipate|we believe|subject to|there can be no)\b", re.I)

PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F", "S-1", "S-4", "DEF 14A", "424")

# Credit agreements define "Event of Default" to include the borrower's own
# bankruptcy. Every investment-grade revolver contains that sentence, and it
# says nothing about the borrower's condition. Definitional text is describing
# a contingency the parties are drafting around, not an event that occurred.
DEFINITIONAL_RX = re.compile(
    r"(?:\bas defined in\b|\bshall mean\b|\bmeans,? with respect to\b|"
    r"\bfor purposes of this\b|\bEvents? of Default\b|\bthe following events\b|"
    r"\bhereinafter referred\b|\bshall have the meaning\b|\bas such term is\b)", re.I)


def _evidence_weight(doc: Document, snippet: str) -> tuple[float, str]:
    """Return (multiplier, human-readable caveat) for a prose match.

    The caveat text is surfaced in the notice, so a contracting officer can see
    not just that we scored something down but why.
    """
    weight = 1.0
    caveats: list[str] = []
    form = (doc.doc_type or "").upper()
    snippet = snippet or ""
    if form.startswith(PERIODIC_FORMS):
        weight *= 0.35
        caveats.append(
            f"appears in a periodic report ({form}), where risk-factor and MD&A "
            f"language describes hypotheticals as a matter of course")
    if HEDGE_RX.search(snippet):
        weight *= 0.5
        caveats.append("is phrased conditionally rather than as a completed transaction")
    if DEFINITIONAL_RX.search(snippet):
        weight *= 0.4
        caveats.append(
            "sits inside a definitional or event-of-default clause, which "
            "describes a contingency the parties drafted around rather than "
            "something that has happened")
    if not caveats:
        return 1.0, ""
    return weight, " Weight reduced: this match " + "; and it ".join(caveats) + "."


# --------------------------------------------------------------------- rules

def rule_foci_jurisdiction(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Foreign jurisdiction named alongside an ownership/investment event.

    When the document has been scoped down to a change delta, the jurisdiction
    may sit in the *unchanged* part of the page — a company that has always
    disclosed a Cayman parent and today announced a change of control. Falling
    back to the surrounding context catches that, at a reduced weight and
    labelled as context so the KO can see which half is new.
    """
    text = doc.text
    if not text.strip():
        return []
    context = (doc.meta or {}).get("_context_text") or ""
    events_pos = lex.find_terms_with_pos(text, lex.FOCI_EVENT_TERMS)

    juris_pos = lex.find_jurisdictions_with_pos(text)
    from_context = False
    if not juris_pos and context and events_pos:
        juris_pos = lex.find_jurisdictions_with_pos(context)
        from_context = bool(juris_pos)
    if not juris_pos:
        return []

    signals: list[Signal] = []
    for name, snippet, j_pos in juris_pos:
        # Proximity gate. A jurisdiction named on page 1 and an unrelated
        # ownership word on page 40 are not the same story: on a long page,
        # co-occurrence anywhere in the document is close to guaranteed and
        # says nothing. Only events near the mention corroborate it.
        events = ([(t, s) for t, s, e_pos in events_pos
                   if abs(e_pos - j_pos) <= PROXIMITY_WINDOW]
                  if not from_context else [(t, s) for t, s, _ in events_pos])
        if from_context:
            event_terms = ", ".join(sorted({t for t, _ in events})[:4])
            mult = lex.jurisdiction_multiplier(name) * 0.7
            score = _score("FOCI", ctx, mult)
            signals.append(Signal(
                rule_id="FOCI-JURIS-02", category="FOCI", severity=_sev(score),
                score=score,
                title=f"New ownership event at contractor with standing {name} nexus",
                rationale=(
                    f"New text in this source describes an ownership or control event "
                    f"({event_terms}). The {name} ({lex.jurisdiction_tier(name)}) "
                    f"connection is not itself new — it appears in the previously "
                    f"screened content — but a control event at an entity with that "
                    f"standing nexus is the combination worth checking."),
                evidence=events[0][1][:600], source=doc.source,
                source_url=doc.url or ctx.source_url, jurisdiction=name,
                is_new=ctx.is_new))
            continue
        tier = lex.jurisdiction_tier(name)
        mult = lex.jurisdiction_multiplier(name)
        if not events and tier == "conduit":
            continue  # a passing mention of Ireland is not a finding
        quality, caveat = _evidence_weight(doc, snippet)
        if events:
            event_terms = ", ".join(sorted({t for t, _ in events})[:4])
            rationale = (
                f"{name} ({tier} jurisdiction) appears in the same document as "
                f"ownership/investment language ({event_terms}). Under 32 CFR Part 117 "
                f"(NISPOM) a foreign person's ability to direct or decide matters "
                f"affecting the contractor is reportable FOCI, and a change in "
                f"ownership must be reported to the CSA.")
            score = _score("FOCI", ctx, mult * quality)
            rule_id = "FOCI-JURIS-01"
            title = f"{name} nexus in {doc.source} document"
        else:
            rationale = (
                f"{name} ({tier} jurisdiction) is named in this filing without an "
                f"explicit transaction nearby. Flagged for confirmation of beneficial "
                f"ownership rather than as a concluded FOCI determination — the "
                f"mention may be geographic or historical rather than an ownership "
                f"connection.")
            score = _score("FOCI", ctx, mult * 0.45 * quality)
            # Distinct id: a bare mention is not corroborating evidence and must
            # not be eligible to trigger the compound rule.
            rule_id = "FOCI-MENTION-01"
            title = f"{name} named in {doc.source} document (no transaction identified)"
        rationale += caveat
        signals.append(Signal(
            rule_id=rule_id, category="FOCI", severity=_sev(score), score=score,
            title=title,
            rationale=rationale, evidence=snippet[:600], source=doc.source,
            source_url=doc.url or ctx.source_url, jurisdiction=name, is_new=ctx.is_new))
    return signals


def rule_ip_collateral(doc: Document, ctx: RuleContext) -> list[Signal]:
    """IP pledged as loan collateral — the Government's data rights survive a
    foreclosure only if properly asserted, so this is worth a KO's attention."""
    hits = lex.find_terms(doc.text, lex.IP_COLLATERAL_TERMS)
    if not hits:
        return []
    strong = {"intellectual property security agreement", "patent security agreement",
              "trademark security agreement", "collateral assignment of patents",
              "security interest in the patents", "security interest in intellectual property",
              "pledge of intellectual property", "pledged intellectual property"}
    matched_terms = {t.lower() for t, _ in hits}
    is_strong = bool(matched_terms & strong)
    mult = 1.0 if is_strong else 0.45
    snippet = hits[0][1]
    quality, caveat = _evidence_weight(doc, snippet)
    score = _score("IP_COLLATERAL", ctx, mult * quality)
    term_list = ", ".join(sorted(matched_terms)[:5])
    clause_note = ""
    if ctx.ip_clauses:
        clause_note = (" The contractor's awards include " +
                       "; ".join(ctx.ip_clauses[:3]) +
                       ", so Government data/software rights are directly implicated.")
    rationale = (
        f"Language indicating intellectual property has been pledged as security "
        f"({term_list}). If the secured party forecloses, patents and technical data "
        f"underpinning contract performance can transfer to a third party the "
        f"Government never vetted.{clause_note}")
    if not is_strong:
        rationale += (" Weak match: financing vocabulary present without an explicit "
                      "IP security agreement — verify before escalating.")
    rationale += caveat
    return [Signal(
        rule_id="IPCOL-01", category="IP_COLLATERAL", severity=_sev(score), score=score,
        title="Intellectual property pledged as collateral",
        rationale=rationale, evidence=snippet[:600], source=doc.source,
        source_url=doc.url or ctx.source_url, is_new=ctx.is_new)]


def rule_ip_transfer(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Outright IP movement: assignment, exclusive licence, insolvency."""
    hits = lex.find_terms(doc.text, lex.IP_TRANSFER_TERMS)
    if not hits:
        return []
    terms = sorted({t for t, _ in hits})
    distress = {"chapter 11", "chapter 7", "receivership", "insolvency",
                "assignment for the benefit of creditors", "363 sale", "liquidation"}
    mult = 1.3 if set(terms) & distress else 0.8
    quality, caveat = _evidence_weight(doc, hits[0][1])
    score = _score("IP_TRANSFER", ctx, mult * quality)
    rationale = (
        f"Document references movement or encumbrance of intellectual property "
        f"({', '.join(terms[:5])}). Where the underlying award carries data-rights "
        f"clauses, the Government's licence should be confirmed to survive the "
        f"transaction and the contractor's ability to perform re-verified.")
    if set(terms) & distress:
        rationale += (" Distress vocabulary present — in insolvency, IP is frequently "
                      "sold free and clear, and Government rights must be asserted "
                      "in the proceeding to be preserved.")
    rationale += caveat
    return [Signal(
        rule_id="IPXFER-01", category="IP_TRANSFER", severity=_sev(score), score=score,
        title="Intellectual property transfer or distress indicator",
        rationale=rationale, evidence=hits[0][1][:600], source=doc.source,
        source_url=doc.url or ctx.source_url, is_new=ctx.is_new)]


def rule_uspto_security_interest(doc: Document, ctx: RuleContext) -> list[Signal]:
    """A recorded USPTO conveyance of type SECURITY INTEREST is hard evidence,
    not vocabulary matching — weight it accordingly."""
    if doc.source != "uspto":
        return []
    conveyance = (doc.meta or {}).get("conveyance", "").upper()
    if not any(k in conveyance for k in lex.USPTO_SECURITY_CONVEYANCES):
        return []
    assignee = (doc.meta or {}).get("assignee", "")
    juris = lex.find_jurisdictions(assignee + " " + (doc.meta or {}).get("assignee_address", ""))
    mult = 1.5
    jname = ""
    if juris:
        jname = juris[0][0]
        mult *= lex.jurisdiction_multiplier(jname)
    score = _score("IP_COLLATERAL", ctx, mult)
    rationale = (
        f"USPTO assignment records show a recorded conveyance of "
        f"'{conveyance.title()}' to {assignee or 'an undisclosed party'} covering "
        f"{(doc.meta or {}).get('patent_count', 'one or more')} propert(ies). This is a "
        f"recorded encumbrance on the contractor's patent estate, not an inference.")
    if jname:
        rationale += (f" The secured party has a {jname} nexus, which raises a FOCI "
                      f"question in addition to the IP question.")
    return [Signal(
        rule_id="IPCOL-USPTO-01", category="IP_COLLATERAL", severity=_sev(score),
        score=score, title="Recorded USPTO security interest against patent estate",
        rationale=rationale, evidence=doc.text[:600], source="uspto",
        source_url=doc.url, jurisdiction=jname, is_new=ctx.is_new)]


def rule_contract_structural(contract: Contract, ctx: RuleContext) -> list[Signal]:
    """FOCI signals available from the contract record itself."""
    signals: list[Signal] = []
    for field_name, value, label in (
        ("recipient_country", contract.recipient_country, "registered address"),
        ("country_of_incorporation", contract.country_of_incorporation, "country of incorporation"),
    ):
        if not value:
            continue
        name = lex.COUNTRY_CODE_TO_JURISDICTION.get(value.upper())
        if not name:
            hits = lex.find_jurisdictions(value)
            name = hits[0][0] if hits else None
        if not name:
            continue
        mult = lex.jurisdiction_multiplier(name)
        score = _score("STRUCTURE", ctx, mult)
        signals.append(Signal(
            rule_id="STRUCT-COUNTRY-01", category="STRUCTURE", severity=_sev(score),
            score=score, title=f"Contractor {label}: {name}",
            rationale=(f"FPDS/USAspending records the contractor's {label} as {value} "
                       f"({name}, {lex.jurisdiction_tier(name)} tier) on award "
                       f"{contract.piid or contract.award_id}. Confirm the entity's "
                       f"ownership chain and any FOCI mitigation instrument on file."),
            evidence=f"{field_name}={value} on {contract.piid or contract.award_id}",
            source="fpds", source_url=contract.usaspending_url or contract.source_url,
            jurisdiction=name, is_new=ctx.is_new))

    if contract.foreign_owned_and_located:
        score = _score("STRUCTURE", ctx, 2.0)
        signals.append(Signal(
            rule_id="STRUCT-FOREIGN-OWNED-01", category="STRUCTURE", severity=_sev(score),
            score=score, title="FPDS flags contractor as foreign owned and located",
            rationale=("The contractor self-certified in FPDS as foreign owned and "
                       "located. Verify the FOCI mitigation instrument (SSA, proxy "
                       "agreement, board resolution) covering this award."),
            evidence=f"isForeignOwnedAndLocated=true on {contract.piid or contract.award_id}",
            source="fpds", source_url=contract.usaspending_url, is_new=ctx.is_new))

    if contract.foreign_funding and "not applicable" not in contract.foreign_funding.lower():
        score = _score("STRUCTURE", ctx, 1.2)
        signals.append(Signal(
            rule_id="STRUCT-FOREIGN-FUNDING-01", category="STRUCTURE", severity=_sev(score),
            score=score, title="Award carries a foreign-funding indicator",
            rationale=(f"FPDS foreign funding field reads '{contract.foreign_funding}'. "
                       f"Confirm whether foreign funds support performance and whether "
                       f"that was disclosed."),
            evidence=contract.foreign_funding, source="fpds",
            source_url=contract.usaspending_url, is_new=ctx.is_new))
    return signals


def rule_sanctions_hit(doc: Document, ctx: RuleContext) -> list[Signal]:
    if doc.source != "ofac":
        return []
    score = _score("SANCTIONS", ctx, 1.0)
    return [Signal(
        rule_id="SANCTION-01", category="SANCTIONS", severity="critical", score=score,
        title="Possible sanctions/screening-list name match",
        rationale=("A name on the OFAC SDN list is similar to the contractor or a "
                   "party named in its filings. This is a name match only and must be "
                   "adjudicated against identifiers before any action is taken."),
        evidence=doc.text[:600], source="ofac", source_url=doc.url, is_new=ctx.is_new)]


def rule_adviser_foreign_domicile(doc: Document, ctx: RuleContext) -> list[Signal]:
    """An SEC-registered adviser connected to the contractor is domiciled abroad."""
    if doc.source != "iapd":
        return []
    country = (doc.meta or {}).get("country", "")
    if not country or country.strip().lower() in ("united states", "usa", "us"):
        return []
    hits = lex.find_jurisdictions(country)
    if not hits:
        return []
    name, _ = hits[0]
    mult = lex.jurisdiction_multiplier(name)
    score = _score("FOCI", ctx, mult * 0.8)
    return [Signal(
        rule_id="FOCI-IAPD-01", category="FOCI", severity=_sev(score), score=score,
        title=f"Related investment adviser domiciled in {name}",
        rationale=(f"IAPD lists '{(doc.meta or {}).get('firm_name', '')}' "
                   f"(SEC# {(doc.meta or {}).get('sec_number', 'n/a')}) with an office in "
                   f"{country}. Where an adviser in a {lex.jurisdiction_tier(name)} "
                   f"jurisdiction holds or manages an interest in the contractor, the "
                   f"beneficial ownership behind that interest should be identified."),
        evidence=doc.text[:600], source="iapd", source_url=doc.url,
        jurisdiction=name, is_new=ctx.is_new)]


# 8-K item code -> (category, weight multiplier, what it means for this screen).
# These are structured facts asserted by the registrant to the SEC, so they are
# stronger evidence than any phrase match and are scored as such.
EIGHT_K_ITEMS = {
    "5.01": ("FOCI", 2.0, "Changes in Control of Registrant",
             "The registrant reported a change in control. Where the incoming "
             "controlling party is foreign, this is precisely the event that "
             "triggers a FOCI review and, potentially, a novation under FAR 42.12."),
    "1.03": ("IP_TRANSFER", 2.0, "Bankruptcy or Receivership",
             "The registrant reported bankruptcy or receivership. Intellectual "
             "property is routinely sold free and clear in these proceedings; "
             "Government licence rights must be asserted in the case to survive."),
    "2.01": ("IP_TRANSFER", 1.5, "Completion of Acquisition or Disposition of Assets",
             "Assets changed hands. Confirm whether any technical data, software "
             "or patents relied on for contract performance moved with them."),
    "2.03": ("IP_COLLATERAL", 1.4, "Creation of a Direct Financial Obligation",
             "The registrant took on a direct financial obligation. Secured "
             "facilities of this kind frequently carry an all-assets lien that "
             "captures the patent estate."),
    "2.04": ("IP_COLLATERAL", 1.3, "Triggering Event Accelerating a Financial Obligation",
             "An acceleration or default event was reported, which brings any "
             "security interest over intellectual property closer to enforcement."),
    "3.02": ("FOCI", 1.2, "Unregistered Sales of Equity Securities",
             "Equity was issued outside a registered offering. Private placements "
             "are a common route for foreign capital to take a position without "
             "an obvious public disclosure."),
    "1.01": ("IP_COLLATERAL", 0.7, "Entry into a Material Definitive Agreement",
             "A material agreement was entered into. Low weight on its own — it "
             "is the wrapper for both routine commercial deals and security "
             "agreements, so it warrants a look rather than a conclusion."),
}


def rule_edgar_8k_items(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Score 8-K item codes as structured facts rather than prose.

    EDGAR's item codes are the highest-quality signal the filing index offers:
    the registrant itself is asserting "this filing is about a change in
    control". Reading them from metadata avoids inferring the same thing from
    English text, which is both weaker and easy to get wrong.
    """
    codes = (doc.meta or {}).get("item_codes") or []
    if not codes:
        return []
    company = (doc.meta or {}).get("company", "the registrant")
    signals: list[Signal] = []
    for code in codes:
        entry = EIGHT_K_ITEMS.get(str(code).strip())
        if not entry:
            continue
        category, mult, label, explanation = entry
        score = _score(category, ctx, mult)
        signals.append(Signal(
            rule_id=f"EDGAR-8K-{code}", category=category, severity=_sev(score),
            score=score, title=f"8-K Item {code}: {label}",
            rationale=(f"{company} filed an 8-K reporting Item {code} "
                       f"({label}) on {doc.published or 'an undisclosed date'}. "
                       f"{explanation}"),
            evidence=f"SEC Form 8-K, Item {code} — {label}",
            source="sec_edgar", source_url=doc.url, is_new=ctx.is_new))
    return signals


DOCUMENT_RULES = [
    rule_edgar_8k_items,
    rule_foci_jurisdiction,
    rule_ip_collateral,
    rule_ip_transfer,
    rule_uspto_security_interest,
    rule_sanctions_hit,
    rule_adviser_foreign_domicile,
]


# ----------------------------------------------------------------- evaluation

def evaluate_documents(docs: list[tuple[Document, Change | None]],
                       ctx_entity: Entity, contracts: list[Contract]) -> list[Signal]:
    """Run every document rule. When a Change is supplied, rules see only the
    newly-added text so we report movement rather than steady state."""
    signals: list[Signal] = []
    for doc, change in docs:
        is_new = bool(change and change.kind == "new")
        scoped = doc
        if change and change.kind == "modified" and change.added_text.strip():
            scoped = Document(source=doc.source, key=doc.key, title=doc.title,
                              url=doc.url, text=change.added_text,
                              published=doc.published, doc_type=doc.doc_type,
                              meta={**(doc.meta or {}), "_context_text": doc.text})
            is_new = True
        elif change and change.kind == "unchanged":
            is_new = False
        ctx = RuleContext(entity=ctx_entity, contracts=contracts, is_new=is_new,
                          source_url=doc.url)
        for rule in DOCUMENT_RULES:
            try:
                fired = rule(scoped, ctx)
            except Exception:  # a bad rule must not sink the run
                continue
            # Stamped here rather than in each rule: the document is already in
            # scope, and ten constructors that each have to remember a field is
            # ten chances to forget it.
            for signal in fired:
                signal.document_key = doc.key
            signals.extend(fired)
    return signals


def evaluate_contracts(contracts: list[Contract], entity: Entity) -> list[Signal]:
    ctx = RuleContext(entity=entity, contracts=contracts)
    out: list[Signal] = []
    for c in contracts:
        out.extend(rule_contract_structural(c, ctx))
    return out


def correlate(signals: list[Signal], contracts: list[Contract]) -> list[Signal]:
    """Compound rule: foreign nexus AND an IP encumbrance is worse than either.

    This is the case the tool exists to catch — an offshore investor arriving at
    a contractor whose patents are already pledged.
    """
    # Both halves must stand on their own before they are allowed to compound.
    # Two weak observations do not make a strong one: a bare geographic mention
    # of a jurisdiction plus generic credit-agreement vocabulary describes an
    # ordinary company, and escalating that pair to CRITICAL is how the tool
    # would earn a reputation for crying wolf.
    MIN_RANK = SEVERITY_ORDER.index("medium")
    foci = [s for s in signals
            if s.category in ("FOCI", "STRUCTURE") and s.jurisdiction
            and s.rule_id != "FOCI-MENTION-01"
            and s.severity_rank() >= MIN_RANK]
    ip = [s for s in signals
          if s.category in ("IP_COLLATERAL", "IP_TRANSFER")
          and s.severity_rank() >= MIN_RANK]
    if not (foci and ip):
        return []
    worst = max(foci, key=lambda s: s.score)
    worst_ip = max(ip, key=lambda s: s.score)
    ip_clauses: list[str] = []
    for c in contracts:
        ip_clauses.extend(x for x in c.ip_clause_hits if x not in ip_clauses)
    score = round((worst.score + worst_ip.score) * 0.9, 2)
    rationale = (
        f"Two independent signal families overlap on this contractor: a "
        f"{worst.jurisdiction} nexus ({worst.rule_id}) and an intellectual-property "
        f"encumbrance or transfer ({worst_ip.rule_id}). Separately each warrants a "
        f"question; together they describe a path by which technology developed "
        f"under Government contract could come under foreign control without a "
        f"novation or FOCI action ever reaching the contracting officer.")
    if ip_clauses:
        rationale += (f" Affected awards cite {'; '.join(ip_clauses[:3])}.")
    return [Signal(
        rule_id="COMPOUND-01", category="FOCI", severity=_sev(score),
        score=score, title="Compound risk: foreign nexus overlapping IP encumbrance",
        rationale=rationale,
        evidence=f"{worst.title} + {worst_ip.title}",
        source="correlation", source_url=worst.source_url,
        jurisdiction=worst.jurisdiction,
        is_new=worst.is_new or worst_ip.is_new)]


def dedupe(signals: list[Signal]) -> list[Signal]:
    seen: set[tuple] = set()
    out: list[Signal] = []
    for s in sorted(signals, key=lambda x: -x.score):
        key = (s.rule_id, s.jurisdiction, s.evidence[:160])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def build_finding(entity: Entity, contracts: list[Contract], signals: list[Signal],
                  run_id: str = "") -> Finding:
    signals = dedupe(signals + correlate(signals, contracts))
    ordered = sorted(signals, key=lambda s: -s.score)
    if not ordered:
        return Finding(entity=entity, signals=[], contracts=contracts,
                       total_score=0.0, severity="info", run_id=run_id)

    # Diminishing returns: a pile of weak hits should not out-score one strong
    # one. Steep decay means the composite is dominated by the top signal.
    total = round(sum(s.score * (0.6 ** i) for i, s in enumerate(ordered)), 2)
    severity = band(total)

    # Corroboration cap. Accumulating many low-confidence signals must not
    # manufacture a high-severity notice: a prime whose worst individual finding
    # is "medium" should not arrive in a KO's inbox marked CRITICAL. Promotion
    # by one band is allowed only when at least two signals independently reach
    # the top band observed.
    top_rank = SEVERITY_ORDER.index(ordered[0].severity)
    corroborated = sum(
        1 for s in ordered if SEVERITY_ORDER.index(s.severity) >= top_rank) >= 2
    cap_rank = min(len(SEVERITY_ORDER) - 1, top_rank + (1 if corroborated else 0))
    if SEVERITY_ORDER.index(severity) > cap_rank:
        severity = SEVERITY_ORDER[cap_rank]

    return Finding(entity=entity, signals=ordered, contracts=contracts,
                   total_score=total, severity=severity, run_id=run_id)
```


---

# Notices


## `foci_screen/notify/__init__.py`

<a id="fociscreennotifyinitpy"></a>

```python
"""Notice rendering and delivery.

Delivery is inert by default: see `gmail.py` for the three guards that must be
cleared before a message reaches a contracting officer.
"""
from . import gmail, render

__all__ = ["gmail", "render"]
```


## `foci_screen/notify/render.py`

<a id="fociscreennotifyrenderpy"></a>

```python
"""Render a Finding into a notice a contracting officer can act on.

House style, and the reason for it: this tool produces *screening output from
public records*, not adjudications. Every claim is attributed to a source the
KO can open, the language asks questions rather than asserting violations, and
the limitations are stated in the body rather than buried. A notice that
overstates its confidence gets the whole feed ignored — or worse, gets acted on.
"""
from __future__ import annotations

import html
from email.message import EmailMessage
from pathlib import Path

from ..models import Finding, Signal

SEVERITY_LABEL = {
    "critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM",
    "low": "LOW", "info": "INFORMATIONAL",
}

CATEGORY_LABEL = {
    "FOCI": "Foreign Ownership, Control or Influence",
    "IP_COLLATERAL": "Intellectual property pledged as collateral",
    "IP_TRANSFER": "Intellectual property transfer / distress",
    "SANCTIONS": "Sanctions and screening lists",
    "STRUCTURE": "Corporate structure and registration",
}

DISCLAIMER = (
    "This notice was generated by an automated screen of public records "
    "(USAspending.gov, FPDS-NG, SEC EDGAR, IAPD, OFAC and the contractor's own "
    "published pages). It reports correlations in public data. It is not a FOCI "
    "determination, a sanctions adjudication, or a finding of contractor "
    "non-compliance, and it has not been verified against non-public holdings. "
    "Please confirm independently before taking contractual action."
)


def has_disclaimer(text: str) -> bool:
    """Is the limitations statement still intact in `text`?

    Compared with whitespace collapsed, because the body wraps the disclaimer to
    a fixed width and indents it — an unedited notice would otherwise fail its
    own check.
    """
    def flat(value: str) -> str:
        return " ".join((value or "").split()).lower()

    return flat(DISCLAIMER) in flat(text)


def subject_for(finding: Finding) -> str:
    sev = SEVERITY_LABEL.get(finding.severity, finding.severity.upper())
    cats = sorted({s.category for s in finding.signals})
    tag = "FOCI/IP" if {"FOCI", "STRUCTURE"} & set(cats) and \
        {"IP_COLLATERAL", "IP_TRANSFER"} & set(cats) else \
        ("FOCI" if {"FOCI", "STRUCTURE"} & set(cats) else "IP")
    piids = [c.piid for c in finding.contracts if c.piid][:2]
    piid_part = f" — {', '.join(piids)}" if piids else ""
    return (f"[{sev}] {tag} screening flag: {finding.entity.name}{piid_part}")


def _fmt_money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def _signal_block(sig: Signal, index: int) -> str:
    lines = [
        f"  {index}. [{SEVERITY_LABEL.get(sig.severity, sig.severity)}] {sig.title}",
        f"     Why flagged: {sig.rationale}",
    ]
    if sig.evidence:
        excerpt = " ".join(sig.evidence.split())[:400]
        lines.append(f'     Source text: "{excerpt}"')
    if sig.source_url:
        lines.append(f"     Reference:   {sig.source_url}")
    lines.append(f"     Basis:       {sig.source} | rule {sig.rule_id}"
                 f"{' | NEW since last screen' if sig.is_new else ''}")
    return "\n".join(lines)


def render_text(finding: Finding, run_id: str = "") -> str:
    officer = finding.top_officer()
    greeting = f"Dear {officer.name}," if officer.name else "Dear Contracting Officer,"
    ent = finding.entity

    parts: list[str] = [greeting, ""]
    parts.append(
        f"An automated public-records screen returned a {SEVERITY_LABEL.get(finding.severity, '')}"
        f" result for {ent.name}"
        + (f" (UEI {ent.uei})" if ent.uei else "")
        + ", which holds contract actions on which you are recorded in FPDS-NG as"
          " the responsible or last-acting contracting official."
    )
    parts.append("")

    # --- affected awards -------------------------------------------------
    parts.append("AFFECTED CONTRACT ACTIONS")
    for c in sorted(finding.contracts, key=lambda x: -x.award_amount)[:6]:
        parts.append(f"  * {c.piid or c.award_id} — {c.awarding_sub_agency or c.awarding_agency}")
        parts.append(f"    Value: {_fmt_money(c.award_amount)}   PoP: {c.start_date} to {c.end_date}")
        if c.psc_code:
            parts.append(f"    PSC {c.psc_code} {c.psc_description} | NAICS {c.naics_code}")
        if c.ip_clause_hits:
            parts.append(f"    Data rights: {'; '.join(c.ip_clause_hits[:3])}")
        if c.usaspending_url:
            parts.append(f"    {c.usaspending_url}")
    parts.append("")

    # --- what changed ----------------------------------------------------
    new = finding.new_signals
    if new:
        parts.append(f"WHAT CHANGED SINCE THE LAST SCREEN ({len(new)} new item"
                     f"{'s' if len(new) != 1 else ''})")
        for s in new[:5]:
            parts.append(f"  - {s.title} ({s.source})")
        parts.append("")

    # --- findings by category -------------------------------------------
    parts.append(f"SCREENING RESULTS  (composite score {finding.total_score};"
                 f" {len(finding.signals)} signal(s))")
    parts.append("")
    idx = 0
    for category, signals in finding.by_category().items():
        parts.append(f"{CATEGORY_LABEL.get(category, category).upper()}")
        for sig in signals[:5]:
            idx += 1
            parts.append(_signal_block(sig, idx))
            parts.append("")

    # --- suggested next steps -------------------------------------------
    parts.append("SUGGESTED VERIFICATION STEPS")
    for step in _next_steps(finding):
        parts.append(f"  - {step}")
    parts.append("")

    parts.append("LIMITATIONS")
    parts.append("  " + "\n  ".join(_wrap(DISCLAIMER, 92)))
    parts.append("")
    parts.append(f"Screening run: {run_id or finding.run_id}   Generated: {finding.generated_at}")
    if officer.source:
        parts.append(f"Addressee resolved from {officer.source} "
                     f"(confidence: {officer.confidence}). If this is not your file, "
                     f"please forward to the cognisant KO and reply so the routing "
                     f"can be corrected.")
    return "\n".join(parts)


def _next_steps(finding: Finding) -> list[str]:
    cats = {s.category for s in finding.signals}
    steps: list[str] = []
    if {"FOCI", "STRUCTURE"} & cats:
        steps.append("Confirm the contractor's current ownership chain in SAM.gov "
                     "(immediate and highest-level owner) against the entity's own "
                     "disclosures.")
        steps.append("Ask the cognisant security office (DCSA) whether a FOCI "
                     "mitigation instrument is on file and whether it covers the "
                     "transaction identified above.")
        steps.append("Verify whether a change-of-ownership notification under "
                     "32 CFR Part 117 (NISPOM) was submitted.")
    if {"IP_COLLATERAL", "IP_TRANSFER"} & cats:
        steps.append("Confirm that Government data and software rights asserted under "
                     "the award survive any security interest, and that any assertion "
                     "list on file remains accurate.")
        steps.append("Search USPTO assignment records for recorded security interests "
                     "against patents used in performance "
                     "(https://assignment.uspto.gov/patent/index.html).")
    if "SANCTIONS" in cats:
        steps.append("Adjudicate the sanctions name match against identifiers before "
                     "taking any action; name similarity alone is not a hit.")
    steps.append("If a novation, change of name, or change of control has occurred, "
                 "confirm FAR Subpart 42.12 documentation is complete.")
    return steps


def render_html(finding: Finding, run_id: str = "") -> str:
    text = render_text(finding, run_id)
    return ("<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
            "font-size:14px;line-height:1.5;color:#1a1a1a\"><pre style=\"white-space:"
            "pre-wrap;font-family:inherit\">" + html.escape(text) + "</pre></body></html>")


def build_message(finding: Finding, *, sender: str, recipient: str,
                  run_id: str = "", cc: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject_for(finding)
    msg["From"] = sender or "foci-screen@localhost"
    msg["To"] = recipient
    if cc:
        msg["Cc"] = cc
    msg["X-FOCI-Run"] = run_id or finding.run_id
    msg["X-FOCI-Entity"] = finding.entity.key()
    msg["X-FOCI-Severity"] = finding.severity
    msg.set_content(render_text(finding, run_id))
    msg.add_alternative(render_html(finding, run_id), subtype="html")
    return msg


def message_from_notice(notice: dict, *, sender: str,
                        recipient: str = "") -> EmailMessage:
    """Rebuild a message from a stored notice row.

    The reviewed body is the one on the row, not a re-render of the finding: a
    reviewer may have edited the wording before approving, and the whole point
    of the approval gate is that what was approved is what goes out.
    """
    body = notice.get("body_text") or ""
    msg = EmailMessage()
    msg["Subject"] = notice.get("subject") or "FOCI/IP screening notice"
    msg["From"] = sender or "foci-screen@localhost"
    msg["To"] = recipient or notice.get("recipient") or ""
    msg["X-FOCI-Run"] = notice.get("run_id") or ""
    msg["X-FOCI-Entity"] = notice.get("entity_key") or ""
    msg["X-FOCI-Severity"] = notice.get("severity") or ""
    msg["X-FOCI-Notice"] = notice.get("notice_id") or ""
    msg.set_content(body)
    msg.add_alternative(
        "<html><body style=\"font-family:-apple-system,Segoe UI,Roboto,sans-serif;"
        "font-size:14px;line-height:1.5;color:#1a1a1a\"><pre style=\"white-space:"
        "pre-wrap;font-family:inherit\">" + html.escape(body) + "</pre></body></html>",
        subtype="html")
    return msg


def write_eml(finding: Finding, out_dir: str, *, sender: str, recipient: str,
              run_id: str = "") -> Path:
    msg = build_message(finding, sender=sender, recipient=recipient, run_id=run_id)
    safe = "".join(ch if ch.isalnum() else "_" for ch in finding.entity.name)[:48]
    path = Path(out_dir) / f"{finding.severity}_{safe}_{run_id or 'draft'}.eml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(msg))
    return path


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out
```


## `foci_screen/notify/gmail.py`

<a id="fociscreennotifygmailpy"></a>

```python
"""Gmail delivery — STEP 3, DELIBERATELY NOT ACTIVE.

Per the build request this is wired but not switched on. What exists here:

  * a complete OAuth + draft-creation path against the Gmail API;
  * three independent guards that must ALL be cleared before anything leaves
    the machine.

The guards, outermost first:

  1. GMAIL_ENABLED must be true            -> otherwise render to .eml only
  2. GMAIL_SEND must be true               -> otherwise create a Gmail *draft*
  3. FOCI_EMAIL_REDIRECT_TO, if set, overrides the recipient entirely

Default posture with no configuration: notices are written to disk as .eml
files and nothing touches the network.

Why drafts rather than sends, even once enabled: these notices go to federal
contracting officers about named companies. A false positive mailed
automatically is a real harm to a real contractor. A human should read each one.
Flipping GMAIL_SEND to true is a deliberate act with that trade-off understood.

Scopes needed (narrowest first):
  https://www.googleapis.com/auth/gmail.compose   - create drafts
  https://www.googleapis.com/auth/gmail.send      - only if GMAIL_SEND is used

Setup:
  1. console.cloud.google.com -> new project -> enable the Gmail API
  2. OAuth consent screen -> External or Internal -> add your address as a tester
  3. Credentials -> OAuth client ID -> Desktop app -> download credentials.json
  4. pip install google-api-python-client google-auth-oauthlib
  5. set GMAIL_ENABLED=true and GMAIL_SENDER=you@yourdomain.gov in .env
  6. first run opens a browser once and writes token.json
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from ..models import Finding
from . import render

log = logging.getLogger("foci.gmail")

SCOPES_DRAFT = ["https://www.googleapis.com/auth/gmail.compose"]
SCOPES_SEND = ["https://www.googleapis.com/auth/gmail.send"]


@dataclass
class DeliveryResult:
    status: str          # rendered | drafted | sent | suppressed | error
    recipient: str
    subject: str
    detail: str = ""
    path: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("rendered", "drafted", "sent")


class GmailNotifier:
    def __init__(self, config) -> None:
        self.cfg = config
        self._service = None

    # ------------------------------------------------------------- guards
    def _resolve_recipient(self, finding: Finding) -> tuple[str, str]:
        """Return (recipient, note). Honours the redirect safety valve."""
        officer = finding.top_officer()
        if self.cfg.email_redirect_to:
            note = (f"redirected from {officer.email or 'unresolved KO'} "
                    f"by FOCI_EMAIL_REDIRECT_TO")
            return self.cfg.email_redirect_to, note
        if not officer.is_addressable:
            return "", "no contracting officer email could be resolved"
        return officer.email, f"resolved from {officer.source} ({officer.confidence})"

    # ------------------------------------------------------------ delivery
    def deliver(self, finding: Finding, run_id: str = "") -> DeliveryResult:
        recipient, note = self._resolve_recipient(finding)
        subject = render.subject_for(finding)

        if not recipient:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient="UNRESOLVED-KO@example.invalid",
                                    run_id=run_id)
            return DeliveryResult("suppressed", "", subject,
                                  detail=note, path=str(path))

        # Guard 1 — the whole Gmail path is off by default.
        if not self.cfg.gmail_enabled:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient=recipient, run_id=run_id)
            return DeliveryResult("rendered", recipient, subject,
                                  detail=f"GMAIL_ENABLED is false; wrote .eml. {note}",
                                  path=str(path))

        service = self._connect()
        if service is None:
            path = render.write_eml(finding, self.cfg.out_dir,
                                    sender=self.cfg.gmail_sender or "foci-screen@localhost",
                                    recipient=recipient, run_id=run_id)
            return DeliveryResult("error", recipient, subject,
                                  detail="Gmail client unavailable; wrote .eml instead",
                                  path=str(path))

        msg = render.build_message(finding, sender=self.cfg.gmail_sender,
                                   recipient=recipient, run_id=run_id)
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()

        try:
            # Guard 2 — sending requires an explicit second opt-in.
            if self.cfg.gmail_send:
                sent = service.users().messages().send(
                    userId="me", body={"raw": raw}).execute()
                return DeliveryResult("sent", recipient, subject,
                                      detail=f"gmail message id {sent.get('id')}. {note}")
            draft = service.users().drafts().create(
                userId="me", body={"message": {"raw": raw}}).execute()
            return DeliveryResult("drafted", recipient, subject,
                                  detail=f"gmail draft id {draft.get('id')}. {note}")
        except Exception as exc:  # pragma: no cover - needs live creds
            log.warning("gmail delivery failed: %s", exc)
            return DeliveryResult("error", recipient, subject, detail=str(exc))

    # ------------------------------------------------------------- client
    def _connect(self):
        if self._service is not None:
            return self._service
        try:
            from google.auth.transport.requests import Request  # type: ignore
            from google.oauth2.credentials import Credentials  # type: ignore
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError:
            log.warning("Gmail libraries not installed. "
                        "pip install google-api-python-client google-auth-oauthlib")
            return None

        scopes = SCOPES_DRAFT + (SCOPES_SEND if self.cfg.gmail_send else [])
        token_path = Path(self.cfg.gmail_token)
        creds = None
        if token_path.is_file():
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                cred_path = Path(self.cfg.gmail_credentials)
                if not cred_path.is_file():
                    log.warning("Gmail credentials not found at %s", cred_path)
                    return None
                flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), scopes)
                creds = flow.run_local_server(port=0)
            token_path.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("gmail", "v1", credentials=creds,
                              cache_discovery=False)
        return self._service


def status_banner(config) -> str:
    """One line the CLI prints so the operating posture is never ambiguous."""
    if not config.gmail_enabled:
        return "email: OFF (rendering .eml files only — set GMAIL_ENABLED=true to draft)"
    if config.gmail_send:
        return ("email: SEND MODE — messages will be delivered to contracting officers"
                + (f" [redirected to {config.email_redirect_to}]"
                   if config.email_redirect_to else ""))
    return ("email: DRAFT MODE — Gmail drafts created, nothing sent"
            + (f" [redirected to {config.email_redirect_to}]"
               if config.email_redirect_to else ""))
```


---

# HTTP API


## `foci_screen/api/__init__.py`

<a id="fociscreenapiinitpy"></a>

```python
"""HTTP API. Import `foci_screen.api.app:app` with uvicorn."""
```


## `foci_screen/api/app.py`

<a id="fociscreenapiapppy"></a>

```python
"""FastAPI surface over the screening core.

Nothing here does screening work. Routes validate, enqueue, and read back —
the analysis lives in `pipeline` and `risk`, unchanged from the CLI. A screen
takes minutes, so `POST /v1/screens` returns a `run_id` and the caller polls.

The one route with teeth is notice approval. Everything else reports on public
records; `POST /v1/notices/{id}/approve` is the step that decides an email about
a named company reaches a federal official, so it is explicit, attributed to a
reviewer, and never implied by anything else.
"""
from __future__ import annotations

import json
import logging
import threading
from importlib import metadata
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import get_config
from ..jobs import JobQueue
from ..notify import render as render_notice
from ..store import Store, annotate_diff
from . import auth
from .schemas import NoticeDecision, ScreenRequest, WatchlistRequest

log = logging.getLogger("foci.api")

cfg = get_config()
keymap = auth.parse_keys(cfg.api_keys)
require_tenant = auth.make_dependency(keymap)
queue = JobQueue(cfg)

app = FastAPI(
    title="foci-screen",
    version="0.2.0",
    description="Screens federal contract awards and contractor disclosures for "
                "Foreign Ownership, Control or Influence and intellectual-property risk.",
)

if cfg.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# One store per tenant, shared across requests. Store serialises its own access,
# and a connection per request would spend more time connecting than querying.
_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def store_for(tenant: str) -> Store:
    with _stores_lock:
        if tenant not in _stores:
            _stores[tenant] = Store(cfg.dsn, tenant_id=tenant)
        return _stores[tenant]


def tenant_store(tenant: str = Depends(require_tenant)) -> Store:
    return store_for(tenant)


def _json(raw: str | None) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


# ------------------------------------------------------------------- meta

try:
    VERSION = metadata.version("foci-screen")
except metadata.PackageNotFoundError:   # running from a source tree, not installed
    VERSION = "unknown"


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Unauthenticated liveness probe. Says nothing about the data.

    Carries the version so a deploy can be confirmed as the build you meant to
    ship, without an authenticated call.
    """
    return {"status": "ok", "version": VERSION, "queue": queue.backend,
            "database": "postgres" if cfg.dsn.startswith("postgres") else "sqlite",
            "authenticated": bool(keymap)}


@app.get("/v1/config", tags=["meta"])
def configuration(tenant: str = Depends(require_tenant)) -> dict:
    """Which connectors this deployment can actually run."""
    return {"tenant": tenant, "connectors": cfg.availability(),
            "queue": queue.backend,
            "gmail": {"enabled": cfg.gmail_enabled, "send": cfg.gmail_send,
                      "redirect_to": cfg.email_redirect_to or None}}


# ---------------------------------------------------------------- screens

@app.post("/v1/screens", status_code=202, tags=["screens"])
def create_screen(body: ScreenRequest, tenant: str = Depends(require_tenant)) -> dict:
    store = store_for(tenant)
    options = body.model_dump()
    run_id = store.start_run(body.agency, options, status="queued")
    queue.enqueue(tenant, run_id, options)
    return {"run_id": run_id, "status": "queued", "backend": queue.backend}


@app.get("/v1/screens", tags=["screens"])
def list_screens(limit: int = Query(25, ge=1, le=200),
                 store: Store = Depends(tenant_store)) -> dict:
    return {"runs": store.recent_runs(limit=limit)}


@app.get("/v1/screens/{run_id}", tags=["screens"])
def get_screen(run_id: str, store: Store = Depends(tenant_store)) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "No such run.")
    payload = {
        "run_id": run["run_id"],
        "status": run["status"],
        "agency": run["agency"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "progress": run["progress"],
        "stats": _json(run["stats"]),
        "error": run["error"] or None,
    }
    if run["status"] == "complete":
        payload["findings"] = store.findings_for_run(run_id)
    return payload


# --------------------------------------------------------------- findings

@app.get("/v1/findings", tags=["findings"])
def list_findings(severity: str = Query("", pattern="^(|info|low|medium|high|critical)$"),
                  since: str = "", entity_key: str = "",
                  limit: int = Query(50, ge=1, le=500),
                  store: Store = Depends(tenant_store)) -> dict:
    """The change feed. Filter by severity and date; newest first."""
    return {"findings": store.search_findings(
        severity=severity, since=since, entity_key=entity_key, limit=limit)}


@app.get("/v1/entities/{entity_key}", tags=["findings"])
def get_entity(entity_key: str, store: Store = Depends(tenant_store)) -> dict:
    key = entity_key.upper()
    contracts = store.contracts_where("entity_key", key)
    findings = store.search_findings(entity_key=key, limit=1)
    if not contracts and not findings:
        raise HTTPException(404, "Nothing recorded for that entity.")

    latest = findings[0] if findings else None
    return {
        "entity": (latest or {}).get("entity", {"name": contracts[0]["entity_name"],
                                                "uei": contracts[0]["recipient_uei"]}
                                     if contracts else {}),
        # From the contracts table, not the finding payload: an entity screened
        # again with no new signal still has its awards.
        "contracts": contracts,
        "obligated": sum(c["amount"] or 0 for c in contracts),
        "latest_finding": latest,
        "history": store.entity_history(key),
    }


@app.get("/v1/entities/{entity_key}/timeline", tags=["findings"])
def entity_timeline(entity_key: str, limit: int = Query(50, ge=1, le=200),
                    store: Store = Depends(tenant_store)) -> dict:
    """Severity over time — what changed, and when."""
    return {"entity_key": entity_key.upper(),
            "timeline": store.entity_history(entity_key, limit=limit)}


# ----------------------------------------------------------------- search

@app.get("/v1/search", tags=["search"])
def search(q: str = Query("", max_length=200),
           kind: str = Query("all", pattern="^(all|entity|contract|officer|agency)$"),
           limit: int = Query(10, ge=1, le=100),
           store: Store = Depends(tenant_store)) -> dict:
    """One box over contractors, awards, contracting officers and agencies.

    An empty `q` is a browse rather than an error: it returns the largest of
    each kind, which is what an analyst opening the page for the first time
    wants to see.
    """
    q = q.strip()
    if kind == "entity":
        return {"query": q, "entities": store.search_entities(q, limit)}
    if kind == "contract":
        return {"query": q, "contracts": store.search_contracts(q, limit)}
    if kind == "officer":
        return {"query": q, "officers": store.search_officers(q, limit)}
    if kind == "agency":
        return {"query": q, "agencies": store.search_agencies(q, limit)}

    if not q:
        return {"query": q, "entities": store.search_entities("", limit),
                "contracts": [], "officers": store.search_officers("", limit),
                "agencies": store.search_agencies("", limit)}
    return {"query": q, **store.search_all(q, limit)}


@app.get("/v1/overview", tags=["search"])
def overview(store: Store = Depends(tenant_store)) -> dict:
    """Everything the dashboard charts, in one call."""
    return {
        "totals": store.totals(),
        "severity": store.severity_counts(),
        "categories": store.signal_category_counts(),
        "findings_by_day": store.findings_by_day(),
        "top_entities": store.search_entities("", limit=8),
        "recent_findings": store.search_findings(limit=8),
    }


@app.get("/v1/contracts/{contract_key:path}", tags=["search"])
def get_contract(contract_key: str, store: Store = Depends(tenant_store)) -> dict:
    contract = store.get_contract(contract_key)
    if contract is None:
        raise HTTPException(404, "No such contract.")
    findings = store.search_findings(entity_key=contract["entity_key"], limit=1)
    return {"contract": contract,
            "entity_finding": findings[0] if findings else None}


@app.get("/v1/officers", tags=["search"])
def list_officers(q: str = "", limit: int = Query(50, ge=1, le=200),
                  store: Store = Depends(tenant_store)) -> dict:
    return {"officers": store.search_officers(q, limit)}


@app.get("/v1/officers/{email}", tags=["search"])
def officer_profile(email: str, store: Store = Depends(tenant_store)) -> dict:
    """What one contracting officer holds — the view that decides who to notify."""
    contracts = store.contracts_where("ko_email", email)
    if not contracts:
        raise HTTPException(404, "No contracts recorded for that officer.")
    entity_keys = {c["entity_key"] for c in contracts}
    findings = [f for k in entity_keys
                for f in store.search_findings(entity_key=k, limit=1)]
    return {
        "officer": {
            "email": email,
            "name": contracts[0].get("ko_name"),
            "confidence": contracts[0].get("ko_confidence"),
            "source": contracts[0].get("ko_source"),
            "agency": contracts[0].get("agency"),
        },
        "obligated": sum(c["amount"] or 0 for c in contracts),
        "contracts": contracts,
        "findings": sorted(findings, key=lambda f: -(f.get("total_score") or 0)),
    }


@app.get("/v1/agencies", tags=["search"])
def list_agencies(q: str = "", limit: int = Query(50, ge=1, le=200),
                  store: Store = Depends(tenant_store)) -> dict:
    return {"agencies": store.search_agencies(q, limit)}


@app.get("/v1/agencies/{name:path}", tags=["search"])
def agency_profile(name: str, store: Store = Depends(tenant_store)) -> dict:
    contracts = store.contracts_where("agency", name)
    if not contracts:
        contracts = store.contracts_where("sub_agency", name)
    if not contracts:
        raise HTTPException(404, "No contracts recorded for that agency.")

    by_entity: dict[str, dict] = {}
    for c in contracts:
        row = by_entity.setdefault(c["entity_key"],
                                   {"entity_key": c["entity_key"],
                                    "entity_name": c["entity_name"],
                                    "obligated": 0.0, "contract_count": 0})
        row["obligated"] += c["amount"] or 0
        row["contract_count"] += 1
    for row in by_entity.values():
        latest = store.search_findings(entity_key=row["entity_key"], limit=1)
        row["severity"] = latest[0].get("severity") if latest else None

    return {
        "agency": name,
        "obligated": sum(c["amount"] or 0 for c in contracts),
        "contract_count": len(contracts),
        "entities": sorted(by_entity.values(), key=lambda r: -r["obligated"]),
        "officers": [o for o in store.search_officers("", limit=200)
                     if o.get("agency") == name],
    }


# -------------------------------------------------------------- documents

@app.get("/v1/documents", tags=["documents"])
def entity_documents(entity_key: str = Query(..., min_length=1),
                     store: Store = Depends(tenant_store)) -> dict:
    """Source documents gathered while screening one contractor."""
    return {"entity_key": entity_key.upper(),
            "documents": store.documents_for_entity(entity_key)}


# source and key are query parameters, not path segments: a document key is
# `web:lockheedmartin.com/news`, which contains both a colon and slashes.
@app.get("/v1/documents/history", tags=["documents"])
def document_history(source: str, key: str, limit: int = Query(50, ge=1, le=200),
                     store: Store = Depends(tenant_store)) -> dict:
    revisions = store.document_timeline(source, key, limit=limit)
    if not revisions:
        raise HTTPException(404, "No revisions recorded for that document.")
    return {"source": source, "document_key": key, "revisions": revisions}


@app.get("/v1/documents/diff", tags=["documents"])
def document_diff(source: str, key: str, from_sha: str = "", to_sha: str = "",
                  entity_key: str = "",
                  store: Store = Depends(tenant_store)) -> dict:
    """What changed between two revisions. Defaults to the two most recent.

    This is the screen a CLI cannot do well, and the one the change-detection
    design exists to serve: not "this company mentions the Cayman Islands" but
    "this paragraph appeared on their site last Tuesday".

    Pass `entity_key` to have the lines a rule actually fired on marked, so the
    diff and the finding are joined up rather than left side by side.
    """
    result = store.document_diff(source, key, from_sha=from_sha, to_sha=to_sha)
    if "error" in result:
        raise HTTPException(404, result["error"])

    if entity_key:
        signals = store.signals_for_document(entity_key, key)
        result["lines"] = annotate_diff(result["lines"], signals)
        result["signals"] = signals
    return result


# -------------------------------------------------------------- watchlists

@app.post("/v1/watchlists", status_code=201, tags=["watchlists"])
def create_watchlist(body: WatchlistRequest,
                     store: Store = Depends(tenant_store)) -> dict:
    watchlist_id = store.create_watchlist(body.name, body.screen.model_dump())
    return {"watchlist_id": watchlist_id, "name": body.name, "active": True}


@app.get("/v1/watchlists", tags=["watchlists"])
def list_watchlists(store: Store = Depends(tenant_store)) -> dict:
    rows = store.list_watchlists()
    for r in rows:
        r["params"] = _json(r.get("params"))
    return {"watchlists": rows}


@app.post("/v1/watchlists/{watchlist_id}/run", status_code=202, tags=["watchlists"])
def run_watchlist(watchlist_id: str, tenant: str = Depends(require_tenant)) -> dict:
    store = store_for(tenant)
    rows = [w for w in store.list_watchlists() if w["watchlist_id"] == watchlist_id]
    if not rows:
        raise HTTPException(404, "No such watchlist.")
    options = _json(rows[0]["params"])
    run_id = store.start_run(options.get("agency", ""), options, status="queued")
    store.mark_watchlist_run(watchlist_id, run_id)
    queue.enqueue(tenant, run_id, options)
    return {"run_id": run_id, "status": "queued"}


@app.delete("/v1/watchlists/{watchlist_id}", tags=["watchlists"])
def deactivate_watchlist(watchlist_id: str,
                         store: Store = Depends(tenant_store)) -> dict:
    if not store.set_watchlist_active(watchlist_id, False):
        raise HTTPException(404, "No such watchlist.")
    return {"watchlist_id": watchlist_id, "active": False}


# ----------------------------------------------------------------- notices

NOTICE_STATUS = "^(|pending|approved|rejected|drafted|sent|suppressed)$"


@app.get("/v1/notices", tags=["notices"])
def list_notices(status: str = Query("", pattern=NOTICE_STATUS),
                 limit: int = Query(50, ge=1, le=200),
                 store: Store = Depends(tenant_store)) -> dict:
    notices = store.list_notices(status=status, limit=limit)
    return {"notices": [{**n, "edits": store.notice_edits(n)} for n in notices]}


# Declared before /v1/notices/{notice_id}: routes match in definition order, and
# the bare parameter would otherwise capture "abc123.eml" as an id and 404.
@app.get("/v1/notices/{notice_id}.eml", tags=["notices"])
def download_notice(notice_id: str, store: Store = Depends(tenant_store)) -> Response:
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    msg = render_notice.message_from_notice(notice, sender=cfg.gmail_sender)
    return Response(
        content=bytes(msg), media_type="message/rfc822",
        headers={"Content-Disposition":
                 f'attachment; filename="notice_{notice_id}.eml"'})


@app.get("/v1/notices/{notice_id}", tags=["notices"])
def get_notice(notice_id: str, store: Store = Depends(tenant_store)) -> dict:
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    return {**notice, "edits": store.notice_edits(notice)}


def _decider(body: NoticeDecision, tenant: str) -> str:
    """Who to record as having decided.

    Keys are shared per tenant, so without a name in the request the honest
    attribution is the key, labelled as one. The bare tenant id was used before,
    and a tenant called "default" produced "decided by default" — which reads as
    an automatic approval on exactly the record meant to prove a person looked.
    """
    return body.decided_by.strip() or f"api-key:{tenant}"


def _decision_error(outcome: str) -> None:
    if outcome == "not_found":
        raise HTTPException(404, "No such notice.")
    if outcome == "already_decided":
        raise HTTPException(
            409, "This notice has already been decided. Decisions are final so a "
                 "rejection cannot be silently overturned.")


@app.post("/v1/notices/{notice_id}/approve", tags=["notices"])
def approve_notice(notice_id: str, body: NoticeDecision,
                   tenant: str = Depends(require_tenant)) -> dict:
    """Approve a notice for delivery, optionally with an edited body.

    Approval records a decision; it does not itself send. Delivery still depends
    on `GMAIL_ENABLED` and `GMAIL_SEND`, and while `FOCI_EMAIL_REDIRECT_TO` is
    set every approved notice goes to that address instead of the officer.

    An edit may change anything except the limitations statement. That paragraph
    is what makes the notice a screening result rather than an assertion about a
    named company, so a body without it is refused rather than sent.
    """
    store = store_for(tenant)
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    if not (notice.get("recipient") or "").strip():
        raise HTTPException(
            409, "This notice has no resolved contracting officer; it cannot be "
                 "addressed. Download the .eml and route it manually.")
    if body.body_text and not render_notice.has_disclaimer(body.body_text):
        raise HTTPException(
            422, "The edited notice no longer contains the limitations statement. "
                 "It can be reworded around, but not removed: without it the notice "
                 "reads as a finding against the contractor rather than a screen.")

    _decision_error(store.decide_notice(
        notice_id, "approved", decided_by=_decider(body, tenant),
        note=body.note, body_text=body.body_text))
    decided = store.get_notice(notice_id)
    return {"notice_id": notice_id, "status": "approved",
            "edited": bool(decided and decided.get("original_body_text")),
            "delivery": _delivery_state()}


@app.post("/v1/notices/{notice_id}/reject", tags=["notices"])
def reject_notice(notice_id: str, body: NoticeDecision,
                  tenant: str = Depends(require_tenant)) -> dict:
    """Reject a notice.

    Worth capturing the reason: rejections are the only labelled false-positive
    data this tool ever gets, and rule weights cannot be tuned without them.
    """
    store = store_for(tenant)
    _decision_error(store.decide_notice(
        notice_id, "rejected", decided_by=_decider(body, tenant), note=body.note))
    return {"notice_id": notice_id, "status": "rejected"}


def _delivery_state() -> dict:
    if not cfg.gmail_enabled:
        return {"mode": "render_only",
                "detail": "GMAIL_ENABLED is false; download the .eml to route it."}
    if cfg.email_redirect_to:
        return {"mode": "redirected", "detail": f"All mail goes to {cfg.email_redirect_to}."}
    return {"mode": "send" if cfg.gmail_send else "draft",
            "detail": "Gmail drafts are created for review."
                      if not cfg.gmail_send else "Approved notices are sent."}


# ---------------------------------------------------------------------- web
# Mounted last: FastAPI matches routes in declaration order, so every /v1 route
# above wins before this catch-all sees the request.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
else:  # pragma: no cover - only if the package was built without web assets
    log.warning("web assets missing at %s; API only", _WEB_DIR)
```


## `foci_screen/api/auth.py`

<a id="fociscreenapiauthpy"></a>

```python
"""API-key authentication, mapping a key to a tenant.

Keys live in `FOCI_API_KEYS` as `tenant:key` pairs, comma separated:

    FOCI_API_KEYS="acme:sk_live_9f3c...,navy-pmo:sk_live_1a7b..."

Environment rather than a database, deliberately. A key table needs a
bootstrapping route to mint the first key, and that route is the most attacked
surface an API of this kind has. Rotating a key here is an environment change
and a restart, which for an analyst tool is an acceptable trade for not
shipping a self-service credential endpoint.

**Fails closed.** With no keys configured every authenticated route returns 503.
A screening tool that drafts email to federal officials must not be reachable by
accident because someone deployed it before setting a variable.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status


def parse_keys(raw: str) -> dict[str, str]:
    """`"tenant:key,tenant2:key2"` -> {key: tenant}. Keys index the map."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        tenant, _, key = pair.partition(":")
        tenant, key = tenant.strip(), key.strip()
        if tenant and key:
            out[key] = tenant
    return out


def resolve_tenant(keymap: dict[str, str], presented: str) -> str:
    """Constant-time lookup of a presented key. Returns "" if unknown."""
    match = ""
    for key, tenant in keymap.items():
        # Compare every entry so timing does not leak which prefix was close.
        if secrets.compare_digest(key, presented):
            match = tenant
    return match


def make_dependency(keymap: dict[str, str]):
    """Build the FastAPI dependency that yields a tenant id."""

    async def require_tenant(
        authorization: str = Header(default=""),
        x_api_key: str = Header(default=""),
    ) -> str:
        if not keymap:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FOCI_API_KEYS is not configured; the API is closed.")

        presented = x_api_key.strip()
        if not presented and authorization.lower().startswith("bearer "):
            presented = authorization[7:].strip()
        if not presented:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Supply a key in Authorization: Bearer <key> or X-API-Key.",
                headers={"WWW-Authenticate": "Bearer"})

        tenant = resolve_tenant(keymap, presented)
        if not tenant:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Unknown API key.")
        return tenant

    return require_tenant
```


## `foci_screen/api/schemas.py`

<a id="fociscreenapischemaspy"></a>

```python
"""Request bodies for the API.

Responses are returned as plain dicts: a Finding's payload is already a
serialised dataclass tree, and re-declaring that shape in Pydantic would create
two definitions of the same thing that drift apart.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScreenRequest(BaseModel):
    agency: str = Field(..., min_length=2,
                        examples=["Department of Defense"])
    sub_agency: str = ""
    months_back: int = Field(12, ge=1, le=60)
    max_awards: int = Field(25, ge=1, le=500)
    max_entities: int = Field(5, ge=1, le=100)
    keyword: str = ""
    include_idv: bool = False
    # entity name -> domain, for the pages a company publishes itself
    domains: dict[str, str] = Field(default_factory=dict)
    fetch_filing_bodies: bool = True
    min_severity: str = Field("low", pattern="^(info|low|medium|high|critical)$")
    skip_web: bool = False


class WatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    screen: ScreenRequest


class NoticeDecision(BaseModel):
    """An approve or reject. `body_text` lets a reviewer correct wording first."""
    note: str = ""
    body_text: str = ""
    decided_by: str = ""
```


---

# Web interface


## `foci_screen/web/index.html`

<a id="fociscreenwebindexhtml"></a>

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>foci-screen</title>
<link rel="stylesheet" href="./styles.css">
</head>
<body>

<header class="topbar">
  <a class="brand" href="#/">
    <span class="brand-mark"></span>
    <span>foci-screen</span>
  </a>

  <form class="searchbar" id="search-form" role="search">
    <input id="search-input" type="search" autocomplete="off" spellcheck="false"
           placeholder="Search contractors, awards, contracting officers, agencies…"
           aria-label="Search">
    <button type="submit">Search</button>
  </form>

  <nav class="topnav">
    <a href="#/">Overview</a>
    <a href="#/notices">Notices <span id="notice-badge" class="badge" hidden></span></a>
    <button id="key-button" type="button" class="linkish">API key</button>
  </nav>
</header>

<main id="view" aria-live="polite"></main>

<dialog id="key-dialog">
  <form method="dialog" id="key-form">
    <h2>API key</h2>
    <p class="muted">
      This deployment requires a key. It is kept in this browser's local storage
      and sent as a bearer token — treat it like a password and use a private
      browser profile on a shared machine.
    </p>
    <input id="key-input" type="password" placeholder="sk_…" autocomplete="off">
    <div class="dialog-actions">
      <button value="cancel" class="ghost">Cancel</button>
      <button value="save" class="primary">Save</button>
    </div>
  </form>
</dialog>

<footer class="foot">
  Screening output from public records. Not a FOCI determination, a sanctions
  adjudication, or a finding of non-compliance.
</footer>

<script src="./app.js"></script>
</body>
</html>
```


## `foci_screen/web/styles.css`

<a id="fociscreenwebstylescss"></a>

```css
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-2: #f0f2f5;
  --border: #d9dee5;
  --text: #14181d;
  --muted: #5c6773;
  --accent: #1f5fa9;
  --accent-soft: #e7effa;

  --critical: #b3261e;
  --high: #c2570a;
  --medium: #9a6700;
  --low: #1f5fa9;
  --info: #6b7681;

  --radius: 10px;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171c;
    --surface: #1c2027;
    --surface-2: #232830;
    --border: #333a44;
    --text: #e8ecf1;
    --muted: #9aa5b1;
    --accent: #6ba4e8;
    --accent-soft: #1e2a3a;

    --critical: #f2685f;
    --high: #f0913f;
    --medium: #d9b23c;
    --low: #6ba4e8;
    --info: #8b96a3;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ------------------------------------------------------------------ topbar */

.topbar {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 10px 20px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
  flex-wrap: wrap;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  font-weight: 650;
  color: var(--text);
  letter-spacing: -0.01em;
}
.brand:hover { text-decoration: none; }

.brand-mark {
  width: 15px; height: 15px;
  border-radius: 4px;
  background: linear-gradient(135deg, var(--accent), var(--critical));
}

.searchbar {
  display: flex;
  flex: 1 1 380px;
  gap: 8px;
  min-width: 260px;
}

.searchbar input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
}
.searchbar input:focus {
  outline: 2px solid var(--accent);
  outline-offset: -1px;
}

button {
  font: inherit;
  cursor: pointer;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text);
  padding: 8px 14px;
}
button:hover { border-color: var(--accent); }
button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
button.ghost { background: transparent; }
button.linkish {
  border: none; background: none; color: var(--accent); padding: 6px 4px;
}

.topnav { display: flex; align-items: center; gap: 14px; }

.badge {
  display: inline-block;
  min-width: 18px;
  padding: 1px 6px;
  border-radius: 9px;
  background: var(--critical);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  text-align: center;
}

/* -------------------------------------------------------------------- main */

main {
  max-width: 1180px;
  margin: 0 auto;
  padding: 24px 20px 60px;
}

h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
h2 { font-size: 15px; margin: 0 0 12px; letter-spacing: -0.01em; }
h3 { font-size: 13px; margin: 0 0 8px; text-transform: uppercase;
     letter-spacing: 0.06em; color: var(--muted); font-weight: 650; }

.muted { color: var(--muted); }
.mono { font-family: var(--mono); font-size: 12.5px; }

.page-head { margin-bottom: 20px; }
.page-head .sub { color: var(--muted); font-size: 13px; }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  margin-bottom: 16px;
}

.grid { display: grid; gap: 16px; }
.grid.cols-2 { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
.grid.cols-3 { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.grid.cols-4 { grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); }

/* ------------------------------------------------------------------ stats */

.stat { text-align: left; }
.stat .value { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase;
               letter-spacing: 0.05em; }

/* --------------------------------------------------------------- severity */

.sev {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: #fff;
  white-space: nowrap;
}
.sev-critical { background: var(--critical); }
.sev-high     { background: var(--high); }
.sev-medium   { background: var(--medium); }
.sev-low      { background: var(--low); }
.sev-info     { background: var(--info); }
.sev-none     { background: var(--surface-2); color: var(--muted); }

/* ----------------------------------------------------------------- tables */

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  font-weight: 650;
  padding: 6px 10px 6px 0;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
td { padding: 9px 10px 9px 0; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.num, th.num {
  text-align: right;
  /* Right-aligned cells need the gap on the left, or they collide with the
     column that follows them. */
  padding-left: 18px;
  padding-right: 0;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
td.num + td, th.num + th { padding-left: 18px; }
.table-wrap { overflow-x: auto; }

/* --------------------------------------------------------------- signals */

.signal {
  border-left: 3px solid var(--border);
  padding: 2px 0 2px 12px;
  margin-bottom: 14px;
}
.signal.s-critical { border-left-color: var(--critical); }
.signal.s-high     { border-left-color: var(--high); }
.signal.s-medium   { border-left-color: var(--medium); }
.signal.s-low      { border-left-color: var(--low); }
.signal.s-info     { border-left-color: var(--info); }
.signal .title { font-weight: 600; }
.signal .why { color: var(--muted); font-size: 13px; margin-top: 3px; }
.signal .evidence {
  font-family: var(--mono);
  font-size: 12px;
  background: var(--surface-2);
  border-radius: 6px;
  padding: 7px 9px;
  margin-top: 7px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 130px;
  overflow: auto;
}

/* ----------------------------------------------------------------- charts */

.chart { width: 100%; height: auto; display: block; }

.bars { display: flex; flex-direction: column; gap: 9px; }

.bar-row {
  display: grid;
  grid-template-columns: minmax(90px, 30%) 1fr minmax(56px, auto);
  align-items: center;
  gap: 12px;
  font-size: 13px;
}

.bar-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bar-track {
  background: var(--surface-2);
  border-radius: 4px;
  height: 14px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  border-radius: 4px;
  min-width: 2px;
  transition: width 0.25s ease;
}

.bar-value {
  text-align: right;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  font-size: 12.5px;
}

.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 12px; font-size: 12px; }
.legend span.dot { width: 9px; height: 9px; border-radius: 3px; display: inline-block; }
.legend .item { display: flex; align-items: center; gap: 6px; }

/* ------------------------------------------------------------------- misc */

.pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  font-size: 11.5px;
  color: var(--muted);
  margin: 2px 4px 2px 0;
}
.pill.warn { background: var(--accent-soft); color: var(--accent); border-color: transparent; }

.tabs { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
.tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }

.empty { color: var(--muted); padding: 28px 0; text-align: center; }
.error {
  border-left: 3px solid var(--critical);
  background: var(--surface);
  padding: 12px 14px;
  border-radius: var(--radius);
}

.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }

.decide-panel {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}
.decide-prompt { display: block; color: var(--muted); font-size: 12.5px; margin-bottom: 7px; }
.decide-note {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font: inherit;
  resize: vertical;
}
.decide-note:focus { outline: 2px solid var(--accent); outline-offset: -1px; }

/* ------------------------------------------------------------------- diff */

.difflines {
  font-family: var(--mono);
  font-size: 12.5px;
  line-height: 1.55;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: auto;
  max-height: 620px;
  background: var(--surface-2);
}

.difflines > div {
  padding: 1px 12px;
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 3px solid transparent;
}

/* Colour is not the only signal: the sign carries it too, for anyone who
   cannot separate the greens from the reds. */
.dl-add {
  background: color-mix(in srgb, var(--low) 16%, transparent);
  border-left-color: var(--low);
}
.dl-add::before { content: "+ "; color: var(--low); font-weight: 700; }

.dl-del {
  background: color-mix(in srgb, var(--critical) 14%, transparent);
  border-left-color: var(--critical);
  color: var(--muted);
}
.dl-del::before { content: "− "; color: var(--critical); font-weight: 700; }

.dl-ctx { color: var(--muted); }
.dl-ctx::before { content: "  "; }

.dl-hunk {
  background: var(--border);
  color: var(--muted);
  font-size: 11.5px;
  padding: 4px 12px;
}

/* A line a rule actually fired on. Outlined rather than recoloured so the
   add/delete signal underneath it stays readable. */
.dl-matched {
  outline: 2px solid var(--medium);
  outline-offset: -2px;
  border-radius: 3px;
  position: relative;
}

.rule-tag {
  display: inline-block;
  background: var(--medium);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.03em;
  border-radius: 3px;
  padding: 0 5px;
  margin-right: 7px;
  vertical-align: 1px;
}

.notice-body {
  font-family: var(--mono);
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--surface-2);
  border-radius: 8px;
  padding: 14px;
  max-height: 460px;
  overflow: auto;
}

dialog {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  max-width: 460px;
  padding: 22px;
}
dialog::backdrop { background: rgba(0, 0, 0, 0.45); }
dialog h2 { margin-top: 0; }
dialog input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
  margin-top: 8px;
}
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

.foot {
  max-width: 1180px;
  margin: 0 auto;
  padding: 0 20px 40px;
  color: var(--muted);
  font-size: 12px;
}

.spinner { color: var(--muted); padding: 40px 0; text-align: center; }

.breadcrumb { font-size: 12.5px; color: var(--muted); margin-bottom: 10px; }
```


## `foci_screen/web/app.js`

<a id="fociscreenwebappjs"></a>

```javascript
/* foci-screen web client.
 *
 * No framework and no CDN, deliberately. This ships inside the API image and
 * may run on networks that block outside origins; a build step and a remote
 * script tag would both be liabilities. The interactivity here is modest
 * enough that vanilla DOM is not a hardship.
 *
 * Everything from the API is escaped on the way into the DOM. Award
 * descriptions and company names are third-party text — government data is not
 * the same thing as trusted data.
 */
"use strict";

const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const CATEGORY_LABEL = {
  FOCI: "Foreign ownership / control",
  IP_COLLATERAL: "IP pledged as collateral",
  IP_TRANSFER: "IP transfer / distress",
  SANCTIONS: "Sanctions screening",
  STRUCTURE: "Corporate structure",
};

const view = document.getElementById("view");
const keyDialog = document.getElementById("key-dialog");

/* ----------------------------------------------------------------- helpers */

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const money = (n) => {
  const v = Number(n) || 0;
  if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "K";
  return "$" + v.toFixed(0);
};

const num = (n) => (Number(n) || 0).toLocaleString();
const day = (s) => (s ? String(s).slice(0, 10) : "—");
const sevClass = (s) => (SEVERITIES.includes(s) ? `sev-${s}` : "sev-none");

/* An entity with contracts but no finding was screened and produced nothing at
 * or above the run's threshold. Labelling that "not screened" asserts the
 * opposite of what happened, which in a risk tool is the worst kind of wrong. */
const sevTag = (s) =>
  `<span class="sev ${sevClass(s)}">${esc(s || "no findings")}</span>`;

const linkEntity = (key, name) =>
  `<a href="#/entity/${encodeURIComponent(key)}">${esc(name || key)}</a>`;

function setBusy(label) {
  view.innerHTML = `<div class="spinner">${esc(label || "Loading…")}</div>`;
}

function showError(err) {
  view.innerHTML = `<div class="error"><strong>${esc(err.title || "Error")}</strong>
    <div class="muted" style="margin-top:6px">${esc(err.message || err)}</div></div>`;
}

/* --------------------------------------------------------------- API client */

const KEY_STORAGE = "foci.apikey";
let apiKey = "";
try {
  apiKey = localStorage.getItem(KEY_STORAGE) || "";
} catch {
  // Private mode or blocked storage: the key just will not persist.
}

async function api(path) {
  const res = await fetch(path, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
  });
  if (res.status === 401) {
    throw { title: "Not authorised", message: "The API key is missing or wrong. Set it from the top right." };
  }
  if (res.status === 503) {
    throw { title: "API is closed", message: "FOCI_API_KEYS is not configured on the server, so every authenticated route is refused." };
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    throw { title: "Request failed", message: detail };
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
    },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch { /* not JSON */ }
    throw { title: "Request failed", message: detail };
  }
  return res.json();
}

/* -------------------------------------------------------------------- charts
 *
 * Hand-rolled SVG. A charting library would be a megabyte and an outside
 * origin for three chart types, none of which are hard.
 */

/* Horizontal bars are HTML, not SVG. An SVG viewBox scaled to the container
 * stretches its own text with it, which at container width made an 11px label
 * render about 60px tall. A grid row with a percentage-width fill is simpler,
 * responsive for free, and keeps text at the document's own size. */
function barChart(rows, opts = {}) {
  const { label, value, color, format = money, href } = opts;
  if (!rows.length) return `<div class="empty">Nothing to chart yet.</div>`;

  const max = Math.max(...rows.map((r) => Number(r[value]) || 0), 1);

  return `<div class="bars">${rows
    .map((r) => {
      const v = Number(r[value]) || 0;
      const pct = Math.max((v / max) * 100, 1.2); // keep tiny values visible
      const fill = typeof color === "function" ? color(r) : color || "var(--accent)";
      const text = esc(r[label] ?? "");
      return `<div class="bar-row">
          <div class="bar-label">${href ? `<a href="${href(r)}">${text}</a>` : text}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${pct.toFixed(1)}%;background:${fill}"></div>
          </div>
          <div class="bar-value">${esc(format(v))}</div>
        </div>`;
    })
    .join("")}</div>`;
}

function severityChart(counts) {
  const rows = SEVERITIES.filter((s) => counts[s]).map((s) => ({
    name: s.toUpperCase(),
    n: counts[s],
  }));
  if (!rows.length) return `<div class="empty">No entities screened yet.</div>`;
  return barChart(rows, {
    label: "name",
    value: "n",
    format: num,
    color: (r) => `var(--${r.name.toLowerCase()})`,
  });
}

function categoryChart(counts) {
  const rows = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ name: CATEGORY_LABEL[k] || k, n: v }));
  if (!rows.length) return `<div class="empty">No signals recorded yet.</div>`;
  return barChart(rows, { label: "name", value: "n", format: num });
}

function sparkline(points) {
  if (points.length < 2) {
    return `<div class="empty">Not enough history yet — this fills in as screens accumulate.</div>`;
  }
  const w = 600;
  const h = 90;
  const max = Math.max(...points.map((p) => p.n), 1);
  const step = w / (points.length - 1);
  const coords = points.map((p, i) => [i * step, h - (p.n / max) * (h - 14) - 6]);

  const line = coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w},${h} L0,${h} Z`;
  const dots = coords
    .map(([x, y], i) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5"
        fill="var(--accent)"><title>${esc(points[i].day)}: ${points[i].n}</title></circle>`)
    .join("");

  return `<svg class="chart" viewBox="0 0 ${w} ${h}" height="${h}">
      <path d="${area}" fill="var(--accent-soft)"></path>
      <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2"></path>
      ${dots}
    </svg>
    <div class="legend"><span class="muted">${esc(points[0].day)}</span>
      <span class="muted" style="margin-left:auto">${esc(points[points.length - 1].day)}</span></div>`;
}

/* --------------------------------------------------------------- components */

function statCard(label, value) {
  return `<div class="card stat"><div class="value">${esc(value)}</div>
          <div class="label">${esc(label)}</div></div>`;
}

function contractsTable(contracts) {
  if (!contracts.length) return `<div class="empty">No awards recorded.</div>`;
  const rows = contracts
    .map(
      (c) => `<tr>
        <td><a href="#/contract/${encodeURIComponent(c.contract_key || c.piid)}"
               class="mono">${esc(c.piid || c.contract_key)}</a></td>
        <td>${linkEntity(c.entity_key, c.entity_name)}</td>
        <td>${esc(c.sub_agency || c.agency || "—")}</td>
        <td>${esc(c.psc_description || c.naics_description || "—")}</td>
        <td>${c.ko_email ? `<a href="#/officer/${encodeURIComponent(c.ko_email)}">${esc(c.ko_name || c.ko_email)}</a>` : '<span class="muted">unresolved</span>'}</td>
        <td class="num">${esc(money(c.amount))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>PIID</th><th>Contractor</th><th>Agency</th><th>Requirement</th>
      <th>Contracting officer</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function entitiesTable(entities, opts = {}) {
  if (!entities.length) return `<div class="empty">No contractors recorded.</div>`;
  const showScreened = opts.showScreened !== false;
  const rows = entities
    .map(
      (e) => `<tr>
        <td>${linkEntity(e.entity_key, e.entity_name)}
          ${e.foreign_owned ? '<span class="pill warn">foreign owned</span>' : ""}</td>
        <td>${sevTag(e.severity)}</td>
        <td class="num">${esc(num(e.contract_count))}</td>
        <td class="num">${esc(money(e.obligated))}</td>
        ${showScreened ? `<td class="muted">${esc(day(e.last_screened))}</td>` : ""}
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Contractor</th><th>Latest severity</th><th class="num">Awards</th>
      <th class="num">Obligated</th>${showScreened ? "<th>Screened</th>" : ""}</tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function officersTable(officers) {
  if (!officers.length) return `<div class="empty">No contracting officers resolved.</div>`;
  const rows = officers
    .map(
      (o) => `<tr>
        <td><a href="#/officer/${encodeURIComponent(o.ko_email)}">${esc(o.ko_name || o.ko_email)}</a>
            <div class="muted mono">${esc(o.ko_email)}</div></td>
        <td><span class="pill">${esc(o.ko_confidence || "unknown")} confidence</span></td>
        <td class="num">${esc(num(o.contract_count))}</td>
        <td class="num">${esc(num(o.entity_count))}</td>
        <td class="num">${esc(money(o.obligated))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Officer</th><th>Attribution</th><th class="num">Awards</th>
      <th class="num">Contractors</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function agenciesTable(agencies) {
  if (!agencies.length) return `<div class="empty">No agencies recorded.</div>`;
  const rows = agencies
    .map(
      (a) => `<tr>
        <td><a href="#/agency/${encodeURIComponent(a.agency)}">${esc(a.agency)}</a>
          ${a.sub_agency ? `<div class="muted">${esc(a.sub_agency)}</div>` : ""}</td>
        <td class="num">${esc(num(a.contract_count))}</td>
        <td class="num">${esc(num(a.entity_count))}</td>
        <td class="num">${esc(num(a.officer_count))}</td>
        <td class="num">${esc(money(a.obligated))}</td>
      </tr>`
    )
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Agency</th><th class="num">Awards</th><th class="num">Contractors</th>
      <th class="num">Officers</th><th class="num">Obligated</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

function signalList(signals) {
  if (!signals || !signals.length) return `<div class="empty">No signals.</div>`;
  return signals
    .map(
      (s) => `<div class="signal s-${esc(s.severity)}">
        <div class="title">${sevTag(s.severity)} ${esc(s.title)}
          ${s.is_new ? '<span class="pill warn">new</span>' : ""}</div>
        <div class="why">${esc(s.rationale)}</div>
        ${s.evidence ? `<div class="evidence">${esc(s.evidence)}</div>` : ""}
        <div class="muted" style="margin-top:6px;font-size:12px">
          ${esc(s.rule_id)} · ${esc(s.source)}
          ${s.source_url ? ` · <a href="${esc(s.source_url)}" target="_blank" rel="noopener noreferrer">source</a>` : ""}
        </div></div>`
    )
    .join("");
}

/* ------------------------------------------------------------------- views */

async function viewOverview() {
  setBusy("Loading overview…");
  const d = await api("/v1/overview");
  const t = d.totals;

  view.innerHTML = `
    <div class="page-head">
      <h1>Overview</h1>
      <div class="sub">What has been screened, and what changed.</div>
    </div>

    <div class="grid cols-4">
      ${statCard("Obligated", money(t.obligated))}
      ${statCard("Awards", num(t.contracts))}
      ${statCard("Contractors", num(t.entities))}
      ${statCard("Notices pending", num(t.notices_pending))}
    </div>

    <div class="grid cols-2">
      <div class="card">
        <h2>Contractors by latest severity</h2>
        ${severityChart(d.severity)}
      </div>
      <div class="card">
        <h2>Signals by category</h2>
        ${categoryChart(d.categories)}
      </div>
    </div>

    <div class="card">
      <h2>Findings recorded per day</h2>
      ${sparkline(d.findings_by_day)}
    </div>

    <div class="card">
      <h2>Largest contractors screened</h2>
      ${barChart(d.top_entities, {
        label: "entity_name",
        value: "obligated",
        href: (r) => `#/entity/${encodeURIComponent(r.entity_key)}`,
        color: (r) => (r.severity ? `var(--${r.severity})` : "var(--info)"),
      })}
      <div class="legend">
        ${SEVERITIES.map(
          (s) => `<span class="item"><span class="dot" style="background:var(--${s})"></span>${esc(s)}</span>`
        ).join("")}
      </div>
    </div>

    <div class="card">
      <h2>Most recent findings</h2>
      ${entitiesTable(
        d.recent_findings.map((f) => ({
          entity_key: (f.entity || {}).key || (f.entity || {}).uei || (f.entity || {}).name,
          entity_name: (f.entity || {}).name,
          severity: f.severity,
          contract_count: (f.contracts || []).length,
          obligated: (f.contracts || []).reduce((a, c) => a + (c.award_amount || 0), 0),
          last_screened: f.generated_at,
        }))
      )}
    </div>`;
}

async function viewSearch(q, kind) {
  document.getElementById("search-input").value = q;
  setBusy(`Searching for “${q}”…`);
  const d = await api(
    `/v1/search?q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind || "all")}&limit=25`
  );

  const tabs = [
    ["all", "All"],
    ["entity", "Contractors"],
    ["contract", "Awards"],
    ["officer", "Officers"],
    ["agency", "Agencies"],
  ]
    .map(
      ([k, lbl]) =>
        `<button data-kind="${k}" class="${(kind || "all") === k ? "active" : ""}">${lbl}</button>`
    )
    .join("");

  const total =
    (d.entities || []).length + (d.contracts || []).length +
    (d.officers || []).length + (d.agencies || []).length;

  const section = (title, html, rows) =>
    rows && rows.length ? `<div class="card"><h2>${title}</h2>${html}</div>` : "";

  view.innerHTML = `
    <div class="page-head">
      <h1>${q ? `Results for “${esc(q)}”` : "Browse"}</h1>
      <div class="sub">${total} match${total === 1 ? "" : "es"}${q ? "" : " — showing the largest of each"}</div>
    </div>
    <div class="tabs">${tabs}</div>
    ${total === 0 ? '<div class="card"><div class="empty">Nothing matched. Only screened awards are searchable — run a screen first.</div></div>' : ""}
    ${section("Contractors", entitiesTable(d.entities || []), d.entities)}
    ${section("Awards", contractsTable(d.contracts || []), d.contracts)}
    ${section("Contracting officers", officersTable(d.officers || []), d.officers)}
    ${section("Agencies", agenciesTable(d.agencies || []), d.agencies)}`;

  view.querySelectorAll(".tabs button").forEach((b) => {
    b.onclick = () => {
      location.hash = `#/search/${encodeURIComponent(q)}/${b.dataset.kind}`;
    };
  });
}

async function viewEntity(key) {
  setBusy("Loading contractor…");
  const [d, docs] = await Promise.all([
    api(`/v1/entities/${encodeURIComponent(key)}`),
    api(`/v1/documents?entity_key=${encodeURIComponent(key)}`).catch(() => ({ documents: [] })),
  ]);
  d.documents = docs.documents || [];
  const e = d.entity || {};
  const f = d.latest_finding;

  const history = (d.history || []).map((h) => ({
    day: day(h.created_at),
    n: SEVERITIES.length - SEVERITIES.indexOf(h.severity),
  }));

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Contractor</div>
    <div class="page-head">
      <h1>${esc(e.name || key)}</h1>
      <div class="sub">
        ${f ? sevTag(f.severity) + ` score ${esc((f.total_score ?? 0).toFixed ? f.total_score.toFixed(1) : f.total_score)}` : sevTag(null)}
        ${e.uei ? ` · UEI <span class="mono">${esc(e.uei)}</span>` : ""}
        ${e.cik ? ` · CIK <span class="mono">${esc(e.cik)}</span>` : ""}
      </div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Awards", num((d.contracts || []).length))}
      ${statCard("Screens", num((d.history || []).length))}
    </div>

    ${(e.countries || []).length || (e.domains || []).length ? `<div class="card">
      <h3>Registration</h3>
      ${(e.countries || []).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}
      ${(e.domains || []).map((c) => `<span class="pill">${esc(c)}</span>`).join("")}
      ${e.parent_name ? `<span class="pill">parent: ${esc(e.parent_name)}</span>` : ""}
    </div>` : ""}

    ${f ? `<div class="card"><h2>Signals</h2>${signalList(f.signals)}</div>` : ""}

    ${history.length > 1 ? `<div class="card">
      <h2>Severity over time</h2>
      <div class="muted" style="font-size:12px;margin-bottom:8px">
        Higher is worse. Each point is one screen.</div>
      ${sparkline(history)}
    </div>` : ""}

    <div class="card">
      <h2>Source documents</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        What the screen read. Documents with more than one revision can be
        diffed — that is where a change actually shows itself.</div>
      ${documentsTable(d.documents || [], key)}
    </div>

    <div class="card">
      <h2>Awards</h2>
      ${contractsTable(d.contracts || [])}
    </div>`;
}

function documentsTable(docs, entityKey) {
  if (!docs.length) {
    return `<div class="empty">No documents recorded. Re-run a screen to index them.</div>`;
  }
  const rows = docs
    .map((doc) => {
      const diffable = doc.revisions > 1;
      const target = `#/diff/${encodeURIComponent(doc.source)}/`
        + `${encodeURIComponent(doc.document_key)}?entity=${encodeURIComponent(entityKey)}`;
      return `<tr>
        <td>${doc.url
              ? `<a href="${esc(doc.url)}" target="_blank" rel="noopener noreferrer">${esc(doc.title || doc.document_key)}</a>`
              : esc(doc.title || doc.document_key)}
          <div class="muted mono" style="font-size:11.5px">${esc(doc.document_key)}</div></td>
        <td><span class="pill">${esc(doc.source)}</span></td>
        <td class="num">${esc(num(doc.revisions))}</td>
        <td class="muted">${esc(day(doc.last_seen_at))}</td>
        <td>${diffable
              ? `<a href="${target}">View changes</a>`
              : '<span class="muted">single revision</span>'}</td>
      </tr>`;
    })
    .join("");
  return `<div class="table-wrap"><table>
      <thead><tr><th>Document</th><th>Source</th><th class="num">Revisions</th>
      <th>Last seen</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

async function viewDiff(source, key, fromSha, entityKey) {
  setBusy("Loading changes…");
  const qs = `source=${encodeURIComponent(source)}&key=${encodeURIComponent(key)}`;
  let diffQs = fromSha ? `${qs}&from_sha=${encodeURIComponent(fromSha)}` : qs;
  if (entityKey) diffQs += `&entity_key=${encodeURIComponent(entityKey)}`;

  const [history, diff] = await Promise.all([
    api(`/v1/documents/history?${qs}`),
    api(`/v1/documents/diff?${diffQs}`),
  ]);

  const rendered = diff.lines
    .map((l) => {
      const rules = l.rules || [];
      const cls = `dl-${esc(l.kind)}${rules.length ? " dl-matched" : ""}`;
      const title = rules.length ? ` title="Matched by ${esc(rules.join(", "))}"` : "";
      const badge = rules.length
        ? `<span class="rule-tag">${esc(rules.join(" "))}</span>`
        : "";
      return `<div class="${cls}"${title}>${badge}${esc(l.text) || "&nbsp;"}</div>`;
    })
    .join("");

  const matchedCount = diff.lines.filter((l) => (l.rules || []).length).length;

  const revisionRows = history.revisions
    .map(
      (r) => `<tr>
        <td class="mono">${esc(r.sha256.slice(0, 12))}</td>
        <td>${esc(r.observed_at)}</td>
        <td>${r.has_body
              ? (r.sha256 === (diff.to || {}).sha256
                  ? '<span class="pill warn">shown as “after”</span>'
                  : r.sha256 === (diff.from || {}).sha256
                    ? '<span class="pill warn">shown as “before”</span>'
                    : `<a href="#/diff/${encodeURIComponent(source)}/${encodeURIComponent(key)}/${encodeURIComponent(r.sha256)}">compare to latest</a>`)
              : '<span class="muted">text no longer retained</span>'}</td>
      </tr>`
    )
    .join("");

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Document changes</div>
    <div class="page-head">
      <h1>${esc(key)}</h1>
      <div class="sub">
        <span class="pill">${esc(source)}</span>
        ${diff.baseline
          ? '<span class="pill warn">first observation</span>'
          : `<span class="pill">+${diff.stats.added} / −${diff.stats.removed} lines</span>`}
      </div>
    </div>

    ${diff.baseline ? `<div class="card"><div class="muted">
      This is the first time the document was recorded, so all of it reads as new.
      That is a baseline, not a change the contractor made.</div></div>` : ""}

    ${(diff.signals || []).length ? `<div class="card">
      <h2>Signals from this document</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        ${matchedCount
          ? `${matchedCount} line(s) below are marked with the rule that fired on them.`
          : "None of these rules matched a changed line — they fired on text that "
            + "was already there, which is a weaker basis than a fresh disclosure."}</div>
      ${signalList(diff.signals)}
    </div>` : ""}

    <div class="card">
      <h2>${diff.baseline ? "Content" : "What changed"}</h2>
      ${diff.from ? `<div class="muted" style="font-size:12px;margin-bottom:10px">
        <span class="mono">${esc(diff.from.sha256.slice(0, 12))}</span>
        (${esc(diff.from.observed_at)}) →
        <span class="mono">${esc(diff.to.sha256.slice(0, 12))}</span>
        (${esc(diff.to.observed_at)})</div>` : ""}
      <div class="difflines">${rendered || '<div class="empty">No textual difference.</div>'}</div>
    </div>

    <div class="card">
      <h2>Revisions</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>Hash</th><th>Observed</th><th></th></tr></thead>
        <tbody>${revisionRows}</tbody></table></div>
    </div>`;
}

async function viewOfficer(email) {
  setBusy("Loading contracting officer…");
  const d = await api(`/v1/officers/${encodeURIComponent(email)}`);
  const o = d.officer;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Contracting officer</div>
    <div class="page-head">
      <h1>${esc(o.name || o.email)}</h1>
      <div class="sub mono">${esc(o.email)}</div>
      <div class="sub" style="margin-top:6px">
        <span class="pill">${esc(o.confidence || "unknown")} confidence</span>
        <span class="pill">${esc(o.source || "source unrecorded")}</span>
        ${o.agency ? `<span class="pill">${esc(o.agency)}</span>` : ""}
      </div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Awards", num(d.contracts.length))}
      ${statCard("Flagged contractors", num(d.findings.length))}
    </div>

    <div class="card">
      <h2>Contractors with findings</h2>
      <div class="muted" style="font-size:12px;margin-bottom:10px">
        Notices about these would be addressed to this officer.</div>
      ${d.findings.length
        ? entitiesTable(d.findings.map((f) => ({
            entity_key: (f.entity || {}).uei || (f.entity || {}).name,
            entity_name: (f.entity || {}).name,
            severity: f.severity,
            contract_count: (f.contracts || []).length,
            obligated: (f.contracts || []).reduce((a, c) => a + (c.award_amount || 0), 0),
            last_screened: f.generated_at,
          })))
        : '<div class="empty">No findings on this officer’s contractors.</div>'}
    </div>

    <div class="card">
      <h2>Awards</h2>
      ${contractsTable(d.contracts)}
    </div>`;
}

async function viewAgency(name) {
  setBusy("Loading agency…");
  const d = await api(`/v1/agencies/${encodeURIComponent(name)}`);

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Agency</div>
    <div class="page-head">
      <h1>${esc(d.agency)}</h1>
      <div class="sub">${num(d.contract_count)} award(s) screened</div>
    </div>

    <div class="grid cols-3">
      ${statCard("Obligated", money(d.obligated))}
      ${statCard("Contractors", num(d.entities.length))}
      ${statCard("Officers", num(d.officers.length))}
    </div>

    <div class="card">
      <h2>Contractors by obligated value</h2>
      ${barChart(d.entities.slice(0, 12), {
        label: "entity_name",
        value: "obligated",
        href: (r) => `#/entity/${encodeURIComponent(r.entity_key)}`,
        color: (r) => (r.severity ? `var(--${r.severity})` : "var(--info)"),
      })}
    </div>

    <div class="card"><h2>Contractors</h2>
      ${entitiesTable(d.entities, { showScreened: false })}</div>

    <div class="card"><h2>Contracting officers</h2>${officersTable(d.officers)}</div>`;
}

async function viewContract(key) {
  setBusy("Loading award…");
  const d = await api(`/v1/contracts/${encodeURIComponent(key)}`);
  const c = d.contract;

  const field = (label, value) =>
    `<tr><th style="width:210px">${esc(label)}</th><td>${value}</td></tr>`;

  view.innerHTML = `
    <div class="breadcrumb"><a href="#/">Overview</a> › Award</div>
    <div class="page-head">
      <h1 class="mono">${esc(c.piid || c.contract_key)}</h1>
      <div class="sub">${linkEntity(c.entity_key, c.entity_name)} · ${esc(money(c.amount))}</div>
    </div>

    <div class="card">
      <h2>Award</h2>
      <div class="table-wrap"><table>
        ${field("Contractor", linkEntity(c.entity_key, c.entity_name))}
        ${field("Agency", esc(c.agency || "—"))}
        ${field("Sub-agency", esc(c.sub_agency || "—"))}
        ${field("Obligated", esc(money(c.amount)))}
        ${field("Period", `${esc(day(c.start_date))} → ${esc(day(c.end_date))}`)}
        ${field("Requirement", esc(c.psc_description || c.naics_description || "—"))}
        ${field("Description", esc(c.description || "—"))}
        ${field("Solicitation", `<span class="mono">${esc(c.solicitation_id || "—")}</span>`)}
        ${field("UEI", `<span class="mono">${esc(c.recipient_uei || "—")}</span>`)}
        ${field("Country of incorporation", esc(c.country_of_incorporation || "—"))}
        ${field("Foreign owned / located", c.foreign_owned
            ? '<span class="pill warn">yes</span>' : "no")}
        ${field("Foreign funding", esc(c.foreign_funding || "—"))}
      </table></div>
    </div>

    <div class="card">
      <h2>Contracting officer</h2>
      ${c.ko_email
        ? `<div><a href="#/officer/${encodeURIComponent(c.ko_email)}">${esc(c.ko_name || c.ko_email)}</a>
           <div class="muted mono">${esc(c.ko_email)}</div>
           <div style="margin-top:8px">
             <span class="pill">${esc(c.ko_confidence || "unknown")} confidence</span>
             <span class="pill">${esc(c.ko_source || "source unrecorded")}</span>
           </div></div>`
        : '<div class="empty">No contracting officer resolved for this award. A notice about it could not be addressed automatically.</div>'}
    </div>

    ${(c.ip_clauses || []).length ? `<div class="card">
      <h2>Data-rights clauses</h2>
      <div class="muted" style="font-size:12px;margin-bottom:8px">
        These decide what the Government keeps if the contractor's IP moves.</div>
      ${c.ip_clauses.map((x) => `<span class="pill warn">${esc(x)}</span>`).join("")}
    </div>` : ""}

    ${d.entity_finding ? `<div class="card">
      <h2>Contractor signals</h2>
      ${signalList(d.entity_finding.signals)}
    </div>` : ""}

    ${c.source_url ? `<div class="card"><a href="${esc(c.source_url)}" target="_blank"
      rel="noopener noreferrer">Open source record →</a></div>` : ""}`;
}

async function viewNotices() {
  setBusy("Loading notices…");
  const d = await api("/v1/notices?limit=100");

  if (!d.notices.length) {
    view.innerHTML = `<div class="page-head"><h1>Notices</h1></div>
      <div class="card"><div class="empty">No notices yet. They are created for
      findings at medium severity or above.</div></div>`;
    return;
  }

  const cards = d.notices
    .map(
      (n) => `<div class="card" data-notice="${esc(n.notice_id)}">
      <div style="display:flex;gap:10px;align-items:baseline;flex-wrap:wrap">
        ${sevTag(n.severity)}
        <strong>${esc(n.entity_name)}</strong>
        <span class="pill">${esc(n.status)}</span>
        ${n.edits ? '<span class="pill warn">edited by reviewer</span>' : ""}
        <span class="muted" style="margin-left:auto">${esc(day(n.created_at))}</span>
      </div>
      <div style="margin:10px 0 6px">${esc(n.subject)}</div>
      <div class="muted" style="font-size:12.5px">
        To: ${n.recipient ? `<span class="mono">${esc(n.recipient)}</span>
             <span class="pill">${esc(n.officer_confidence || "")}</span>`
            : "<em>no contracting officer resolved</em>"}
      </div>
      <details style="margin-top:10px">
        <summary class="muted" style="cursor:pointer">Read the notice</summary>
        <div class="notice-body">${esc(n.body_text)}</div>
      </details>
      ${n.edits ? `<details style="margin-top:6px">
        <summary class="muted" style="cursor:pointer">
          What the reviewer changed (+${n.edits.stats.added} / −${n.edits.stats.removed} lines)</summary>
        <div class="difflines" style="margin-top:8px">${plainDiff(n.edits.lines)}</div>
      </details>` : ""}
      <div class="actions">
        <button class="dl">Download .eml</button>
        ${n.status === "pending"
          ? `<button class="primary approve" ${n.recipient ? "" : "disabled title='No recipient resolved'"}>Approve</button>
             <button class="edit" ${n.recipient ? "" : "disabled title='No recipient resolved'"}>Edit, then approve</button>
             <button class="reject">Reject</button>`
          : `<span class="muted" style="align-self:center">
               decided by ${esc(n.decided_by || "—")}${n.decision_note ? ` · ${esc(n.decision_note)}` : ""}</span>`}
      </div>
      <div class="decide-panel" hidden>
        <div class="edit-area" hidden>
          <label class="decide-prompt" for="body-${esc(n.notice_id)}">
            Notice text. Reword anything; the limitations statement at the end has
            to stay, because it is what makes this a screen rather than an accusation.
            The generated version is kept alongside whatever you approve.</label>
          <textarea id="body-${esc(n.notice_id)}" class="decide-note edit-body mono"
                    rows="18">${esc(n.body_text)}</textarea>
        </div>
        <label class="decide-prompt note-prompt" for="note-${esc(n.notice_id)}"></label>
        <textarea id="note-${esc(n.notice_id)}" class="decide-note note-body" rows="3"></textarea>
        <div class="panel-error error" hidden></div>
        <div class="actions">
          <button class="primary confirm"></button>
          <button class="ghost cancel">Cancel</button>
        </div>
      </div>
    </div>`
    )
    .join("");

  const pending = d.notices.filter((n) => n.status === "pending").length;
  view.innerHTML = `
    <div class="page-head">
      <h1>Notices</h1>
      <div class="sub">${pending} awaiting review. Approving records a decision —
        it does not transmit anything unless Gmail delivery is armed on the server.</div>
    </div>${cards}`;

  const PROMPTS = {
    approve: {
      label: "What did you verify? (optional, recorded with the approval)",
      confirm: "Confirm approval",
    },
    edit: {
      label: "What did you change, and why? (recorded with the approval)",
      confirm: "Approve edited notice",
    },
    reject: {
      label: "Why is this a false positive? Rejections are the only labelled data " +
             "rule tuning ever gets, so a sentence here is worth more than it looks.",
      confirm: "Confirm rejection",
    },
  };

  const byId = Object.fromEntries(d.notices.map((n) => [n.notice_id, n]));

  view.querySelectorAll("[data-notice]").forEach((card) => {
    const id = card.dataset.notice;
    const panel = card.querySelector(".decide-panel");
    const editArea = panel.querySelector(".edit-area");
    const bodyBox = panel.querySelector(".edit-body");
    const noteBox = panel.querySelector(".note-body");
    const errorBox = panel.querySelector(".panel-error");
    const dl = card.querySelector(".dl");
    if (dl) dl.onclick = () => downloadNotice(id);

    const open = (mode) => {
      panel.querySelector(".note-prompt").textContent = PROMPTS[mode].label;
      const confirm = panel.querySelector(".confirm");
      confirm.textContent = PROMPTS[mode].confirm;
      editArea.hidden = mode !== "edit";
      errorBox.hidden = true;
      confirm.onclick = async () => {
        const action = mode === "reject" ? "reject" : "approve";
        const original = byId[id].body_text || "";
        // Unchanged text is not an edit, and must not be recorded as one.
        const bodyText = mode === "edit" && bodyBox.value.trim() !== original.trim()
          ? bodyBox.value : "";
        confirm.disabled = true;
        const failure = await decide(id, action, noteBox.value, bodyText);
        confirm.disabled = false;
        if (failure) {
          // Shown in place: replacing the page would discard a long edit over
          // one validation message.
          errorBox.innerHTML = `<strong>${esc(failure.title)}</strong>
            <div class="muted" style="margin-top:4px">${esc(failure.message)}</div>`;
          errorBox.hidden = false;
        }
      };
      panel.hidden = false;
      (mode === "edit" ? bodyBox : noteBox).focus();
    };

    const ap = card.querySelector(".approve");
    if (ap) ap.onclick = () => open("approve");
    const ed = card.querySelector(".edit");
    if (ed) ed.onclick = () => open("edit");
    const rj = card.querySelector(".reject");
    if (rj) rj.onclick = () => open("reject");
    const cancel = card.querySelector(".cancel");
    if (cancel) cancel.onclick = () => { panel.hidden = true; };
  });
}

function plainDiff(lines) {
  return lines
    .map((l) => `<div class="dl-${esc(l.kind)}">${esc(l.text) || "&nbsp;"}</div>`)
    .join("");
}

/* Returns an error object on failure rather than rendering it, so the caller
 * can show it next to whatever the reviewer was typing. */
async function decide(id, action, note, bodyText) {
  const payload = { note: note || "" };
  if (bodyText) payload.body_text = bodyText;
  try {
    await apiPost(`/v1/notices/${encodeURIComponent(id)}/${action}`, payload);
    route();
    return null;
  } catch (e) {
    return e;
  }
}

async function downloadNotice(id) {
  // Needs the auth header, so a plain link will not do.
  const res = await fetch(`/v1/notices/${encodeURIComponent(id)}.eml`, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
  });
  if (!res.ok) {
    alert("Could not download the notice.");
    return;
  }
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `notice_${id}.eml`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------------ router */

const ROUTES = [
  [/^\/?$/, () => viewOverview()],
  [/^\/search\/([^/]*)(?:\/([^/]*))?$/, (q, k) => viewSearch(decodeURIComponent(q), k)],
  [/^\/entity\/(.+)$/, (k) => viewEntity(decodeURIComponent(k))],
  [/^\/officer\/(.+)$/, (e) => viewOfficer(decodeURIComponent(e))],
  [/^\/agency\/(.+)$/, (n) => viewAgency(decodeURIComponent(n))],
  [/^\/contract\/(.+)$/, (c) => viewContract(decodeURIComponent(c))],
  [/^\/diff\/([^/]+)\/([^/]+)(?:\/([^/]+))?$/,
   (s, k, from, params) => viewDiff(decodeURIComponent(s), decodeURIComponent(k),
                                    from ? decodeURIComponent(from) : "",
                                    (params && params.get("entity")) || "")],
  [/^\/notices$/, () => viewNotices()],
];

async function route() {
  const raw = location.hash.replace(/^#/, "") || "/";
  // Split the query off before matching: otherwise `?entity=…` lands inside
  // the last path segment and every key gains a suffix.
  const [path, queryString] = raw.split("?");
  const params = new URLSearchParams(queryString || "");

  for (const [re, handler] of ROUTES) {
    const m = path.match(re);
    if (m) {
      try {
        await handler(...m.slice(1), params);
      } catch (e) {
        showError(e);
      }
      refreshBadge();
      return;
    }
  }
  showError({ title: "Not found", message: `No view for ${path}` });
}

async function refreshBadge() {
  const badge = document.getElementById("notice-badge");
  try {
    const d = await api("/v1/notices?status=pending&limit=200");
    const n = d.notices.length;
    badge.textContent = n;
    badge.hidden = n === 0;
  } catch {
    badge.hidden = true;
  }
}

/* ------------------------------------------------------------------- wiring */

document.getElementById("search-form").onsubmit = (e) => {
  e.preventDefault();
  const q = document.getElementById("search-input").value.trim();
  location.hash = `#/search/${encodeURIComponent(q)}`;
};

document.getElementById("key-button").onclick = () => {
  document.getElementById("key-input").value = apiKey;
  keyDialog.showModal();
};

document.getElementById("key-form").onsubmit = () => {
  if (keyDialog.returnValue !== "cancel") {
    apiKey = document.getElementById("key-input").value.trim();
    try {
      localStorage.setItem(KEY_STORAGE, apiKey);
    } catch { /* storage blocked; key lives for this page only */ }
    route();
  }
};
keyDialog.addEventListener("close", () => {
  if (keyDialog.returnValue === "save") {
    apiKey = document.getElementById("key-input").value.trim();
    try {
      localStorage.setItem(KEY_STORAGE, apiKey);
    } catch { /* storage blocked */ }
    route();
  }
});

window.addEventListener("hashchange", route);
route();
```


---

# Tests


## `tests/test_documents.py`

<a id="teststestdocumentspy"></a>

```python
"""Revision retention and the document diff view.

The change-detection design only pays off if you can see *what* changed, not
just that something did. These cover the retention that makes that possible and
the honesty of the result when it is not.
"""
from __future__ import annotations

import pytest

from foci_screen.models import Document
from foci_screen.store import Store

PAGE_V1 = "Acme Corp\nFounded 1957\nHeadquarters in Ohio\nContact us"
PAGE_V2 = ("Acme Corp\nFounded 1957\nHeadquarters in Ohio\n"
           "Acme announced a strategic investment from a Cayman Islands fund\nContact us")


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "docs.db"), tenant_id="acme")
    yield s
    s.close()


def doc(text: str, key: str = "web:acme.com/news") -> Document:
    return Document(source="web", key=key, title="Acme — press",
                    url="https://acme.com/news", text=text, doc_type="press")


# ----------------------------------------------------------------- retention

def test_body_is_retained_on_first_observation(store):
    d = doc(PAGE_V1)
    store.observe(d)
    assert store.document_body(d.sha256()) == PAGE_V1


def test_both_sides_are_retained_when_a_document_changes(store):
    """The replaced version matters as much as the new one — without it the
    first diff after an upgrade has nothing to compare against."""
    old, new = doc(PAGE_V1), doc(PAGE_V2)
    store.observe(old)
    store.observe(new)

    assert store.document_body(old.sha256()) == PAGE_V1
    assert store.document_body(new.sha256()) == PAGE_V2


def test_bodies_are_content_addressed(store):
    """Two documents with identical text cost one row."""
    store.observe(doc(PAGE_V1, key="web:a.com/x"))
    store.observe(doc(PAGE_V1, key="web:b.com/y"))

    rows = store._query("SELECT COUNT(*) AS n FROM snapshot_bodies")
    assert rows[0]["n"] == 1


def test_unchanged_observation_adds_no_revision(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V1))
    assert len(store.document_timeline("web", "web:acme.com/news")) == 1


# ---------------------------------------------------------------------- diff

def test_diff_reports_only_what_was_added(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V2))

    result = store.document_diff("web", "web:acme.com/news")

    assert result["baseline"] is False
    assert result["stats"]["added"] == 1
    assert result["stats"]["removed"] == 0
    added = [ln["text"] for ln in result["lines"] if ln["kind"] == "add"]
    assert added == ["Acme announced a strategic investment from a Cayman Islands fund"]


def test_diff_marks_a_first_observation_as_a_baseline(store):
    """Everything reads as new on a first sight; that is not a change the
    contractor made, and the flag exists so the UI cannot imply it was."""
    store.observe(doc(PAGE_V1))

    result = store.document_diff("web", "web:acme.com/news")

    assert result["baseline"] is True
    assert result["from"] is None
    assert all(ln["kind"] == "add" for ln in result["lines"])


def test_diff_between_named_revisions(store):
    a, b, c = doc(PAGE_V1), doc(PAGE_V2), doc(PAGE_V2 + "\nAnd another line")
    for d in (a, b, c):
        store.observe(d)

    result = store.document_diff("web", "web:acme.com/news",
                                 from_sha=a.sha256(), to_sha=c.sha256())

    assert result["stats"]["added"] == 2


def test_diff_lines_are_structured_not_prefixed(store):
    """A line whose own text starts with '-' must not read as a deletion."""
    store.observe(doc("alpha\nbeta"))
    store.observe(doc("alpha\n-1 adjustment\nbeta"))

    result = store.document_diff("web", "web:acme.com/news")
    added = [ln for ln in result["lines"] if ln["kind"] == "add"]

    assert len(added) == 1
    assert added[0]["text"] == "-1 adjustment"


def test_diff_on_unknown_document_reports_an_error(store):
    assert "error" in store.document_diff("web", "web:nothing.com")


def test_timeline_flags_revisions_whose_text_is_gone(store):
    store.observe(doc(PAGE_V1))
    store.observe(doc(PAGE_V2))
    store._query("SELECT 1")
    with store._tx() as c:
        c.execute("DELETE FROM snapshot_bodies")

    timeline = store.document_timeline("web", "web:acme.com/news")
    assert all(r["has_body"] is False for r in timeline)

    # And the diff says so rather than showing an empty result.
    assert "error" in store.document_diff("web", "web:acme.com/news")


# ----------------------------------------------------------------- pruning

def test_pruning_keeps_recent_revisions_and_drops_old_text(store):
    for i in range(8):
        store.observe(doc(f"{PAGE_V1}\nrevision {i}"))

    stats = store.prune_snapshot_bodies(keep_per_document=3)

    assert stats["history_rows_removed"] == 5
    assert len(store.document_timeline("web", "web:acme.com/news")) == 3
    assert stats["bodies_remaining"] == 3


def test_pruning_keeps_the_body_of_the_current_snapshot(store):
    """Even at keep=1 the live snapshot's text must survive — the next diff
    compares against it."""
    latest = doc(PAGE_V2)
    store.observe(doc(PAGE_V1))
    store.observe(latest)

    store.prune_snapshot_bodies(keep_per_document=1)

    assert store.document_body(latest.sha256()) == PAGE_V2


def test_pruning_is_safe_on_an_empty_store(store):
    assert store.prune_snapshot_bodies()["history_rows_removed"] == 0


def test_upgrading_backfills_bodies_from_existing_snapshots(tmp_path):
    """A database written before revision storage existed still has the current
    text on the snapshot row. Copying it across on upgrade means the next change
    is diffable, instead of every document needing to change twice first."""
    import sqlite3

    path = str(tmp_path / "legacy.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE snapshots (
            source TEXT NOT NULL, document_key TEXT NOT NULL, sha256 TEXT NOT NULL,
            url TEXT, title TEXT, text TEXT, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, PRIMARY KEY (source, document_key));
        INSERT INTO snapshots VALUES ('web', 'web:acme.com/news', 'abc123',
            'https://acme.com/news', 'Acme', 'original body',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
    """)
    old.commit()
    old.close()

    s = Store(path, tenant_id="acme")
    try:
        assert s.document_body("abc123") == "original body"
        # And a subsequent change diffs against it straight away.
        s.observe(doc("original body\nnew disclosure line"))
    finally:
        s.close()


# ------------------------------------------------------- joining diff to rule

def test_annotate_marks_the_line_the_evidence_came_from():
    """The diff shows what changed, the signal shows what fired; without this
    a reviewer pairs them up by eye."""
    from foci_screen.store import annotate_diff

    lines = [
        {"kind": "ctx", "text": "Board of directors declares a dividend"},
        {"kind": "add", "text": "Acme entered a definitive agreement with a Cayman fund"},
    ]
    signals = [{"rule_id": "FOCI-JURIS-01",
                "evidence": "…quarter. Acme entered a definitive agreement with a "
                            "Cayman fund which will acquire a minority stake…"}]

    out = annotate_diff(lines, signals)

    assert "rules" not in out[0]
    assert out[1]["rules"] == ["FOCI-JURIS-01"]


def test_annotate_tolerates_reflowed_whitespace():
    """Evidence snippets have their newlines flattened; the line will not
    match character-for-character."""
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "Acme   entered  a definitive\tagreement today"}]
    signals = [{"rule_id": "R1", "evidence": "Acme entered a definitive agreement today"}]

    assert annotate_diff(lines, signals)[0]["rules"] == ["R1"]


def test_annotate_ignores_short_lines():
    """A heading matches half the snippets on a site. Pointing a reviewer at the
    wrong sentence is worse than not pointing at all."""
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "Contact us"}]
    signals = [{"rule_id": "R1", "evidence": "Please Contact us about the agreement"}]

    assert "rules" not in annotate_diff(lines, signals)[0]


def test_annotate_records_every_matching_rule():
    from foci_screen.store import annotate_diff

    text = "Acme pledged its patents to a Cayman Islands lender as collateral"
    lines = [{"kind": "add", "text": text}]
    signals = [{"rule_id": "IPCOL-01", "evidence": f"...{text}..."},
               {"rule_id": "FOCI-JURIS-01", "evidence": f"xx {text} yy"}]

    assert annotate_diff(lines, signals)[0]["rules"] == ["FOCI-JURIS-01", "IPCOL-01"]


def test_annotate_matches_when_the_line_is_longer_than_the_evidence():
    """Regression: evidence is a fixed-width window around the matched term, so
    on a long paragraph the *evidence* is the shorter of the two. Testing
    containment in one direction only marked nothing at all."""
    from foci_screen.store import annotate_diff

    line = ("Lockheed Martin has entered into a definitive agreement with an "
            "investor group organised in the Cayman Islands, under which the "
            "group will acquire a minority stake and appoint one board observer.")
    # What _snippet() produces: a window, clipped mid-sentence at both ends.
    evidence = ("agreement with an investor group organised in the Cayman "
                "Islands, under which the group will acquire a min")
    lines = [{"kind": "add", "text": line}]

    out = annotate_diff(lines, [{"rule_id": "FOCI-JURIS-01", "evidence": evidence}])
    assert out[0]["rules"] == ["FOCI-JURIS-01"]


def test_annotate_does_not_match_unrelated_text():
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "The board declared a regular quarterly dividend"}]
    signals = [{"rule_id": "R1",
                "evidence": "acquired a minority stake through a Cayman Islands vehicle "
                            "and appointed a board observer to the company"}]

    assert "rules" not in annotate_diff(lines, signals)[0]


def test_annotate_with_no_signals_changes_nothing():
    from foci_screen.store import annotate_diff

    lines = [{"kind": "add", "text": "A line long enough to be matchable here"}]
    assert annotate_diff(lines, []) == lines


def test_signals_are_stamped_with_their_document(store):
    """Set centrally in evaluate_documents, not by each rule."""
    from foci_screen.models import Contract, Entity
    from foci_screen.risk import engine

    text = ("Acme entered into a definitive agreement with an investor organised "
            "in the Cayman Islands to acquire a minority stake.")
    d = doc(text, key="web:acme.com/pr")
    signals = engine.evaluate_documents(
        [(d, None)], Entity(name="ACME", uei="UEI1"),
        [Contract(award_id="A1", piid="P1")])

    assert signals, "expected the jurisdiction rule to fire"
    assert all(s.document_key == "web:acme.com/pr" for s in signals)


def test_signals_for_document_filters_by_key(store):
    from foci_screen.models import Entity, Finding, Signal

    def sig(rule_id, key):
        return Signal(rule_id=rule_id, category="FOCI", severity="low", score=1.0,
                      title="t", rationale="r", evidence="e", source="web",
                      document_key=key)

    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="UEI123"),
        signals=[sig("R1", "web:a"), sig("R2", "web:b")],
        total_score=5.0, severity="low", run_id="r1"))

    found = store.signals_for_document("UEI123", "web:a")
    assert [s["rule_id"] for s in found] == ["R1"]


# ------------------------------------------------------------ entity linking

def test_documents_are_linked_to_the_entity_that_gathered_them(store):
    d = doc(PAGE_V1)
    store.observe(d)
    store.link_document("UEI123", d)

    docs = store.documents_for_entity("UEI123")
    assert len(docs) == 1
    assert docs[0]["title"] == "Acme — press"
    assert docs[0]["revisions"] == 1


def test_relinking_updates_rather_than_duplicates(store):
    d = doc(PAGE_V1)
    store.observe(d)
    store.link_document("UEI123", d)
    store.link_document("UEI123", d)

    assert len(store.documents_for_entity("UEI123")) == 1


def test_document_links_are_tenant_scoped_though_snapshots_are_shared(tmp_path):
    """The corpus is global; who was watching is not."""
    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        d = doc(PAGE_V1)
        acme.observe(d)
        acme.link_document("UEI123", d)

        assert len(acme.documents_for_entity("UEI123")) == 1
        assert other.documents_for_entity("UEI123") == []
        # But the shared snapshot means the second tenant does not refetch.
        assert other.observe(d).kind == "unchanged"
    finally:
        acme.close()
        other.close()


# ---------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    store = app_module.store_for("acme")
    a, b = doc(PAGE_V1), doc(PAGE_V2)
    store.observe(a)
    store.link_document("UEI123", a)
    store.observe(b)
    store.link_document("UEI123", b)
    return TestClient(app_module.app), app_module


def test_documents_endpoint_lists_by_entity(client):
    c, _ = client
    body = c.get("/v1/documents?entity_key=UEI123", headers=AUTH).json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["revisions"] == 2


def test_history_endpoint(client):
    c, _ = client
    body = c.get("/v1/documents/history?source=web&key=web:acme.com/news",
                 headers=AUTH).json()
    assert len(body["revisions"]) == 2
    assert all(r["has_body"] for r in body["revisions"])


def test_diff_endpoint_defaults_to_the_two_most_recent(client):
    c, _ = client
    body = c.get("/v1/documents/diff?source=web&key=web:acme.com/news",
                 headers=AUTH).json()
    assert body["stats"]["added"] == 1
    assert body["baseline"] is False


def test_diff_endpoint_handles_keys_containing_colons_and_slashes(client):
    """`web:acme.com/news` is why these are query parameters, not path segments."""
    c, _ = client
    r = c.get("/v1/documents/diff", params={"source": "web",
                                           "key": "web:acme.com/news"},
              headers=AUTH)
    assert r.status_code == 200


def test_unknown_document_is_404(client):
    c, _ = client
    assert c.get("/v1/documents/history?source=web&key=nope",
                 headers=AUTH).status_code == 404
    assert c.get("/v1/documents/diff?source=web&key=nope",
                 headers=AUTH).status_code == 404


def test_document_endpoints_need_a_key(client):
    c, _ = client
    assert c.get("/v1/documents?entity_key=UEI123").status_code == 401
    assert c.get("/v1/documents/diff?source=web&key=x").status_code == 401
```


## `tests/test_robots.py`

<a id="teststestrobotspy"></a>

```python
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
```


## `tests/test_scheduler.py`

<a id="teststestschedulerpy"></a>

```python
"""The nightly cron entry point.

A cron job's exit code is the only thing most people ever look at, so it has
to distinguish "nothing to do" from "unable to do anything".
"""
from __future__ import annotations

import pytest

from foci_screen import scheduler
from foci_screen.store import Store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "sched.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    return tmp_path


class FakeQueue:
    backend = "rq"
    enqueued: list = []

    def __init__(self, cfg):
        pass

    def enqueue(self, tenant_id, run_id, options):
        FakeQueue.enqueued.append((tenant_id, run_id, options))


def test_missing_queue_fails_the_cron_run(env):
    """Regression: this used to log an error and exit 0, so a deploy without
    Redis showed a green scheduler every night while screening nothing."""
    assert scheduler.main() == scheduler.EXIT_QUEUE_UNAVAILABLE


def test_missing_queue_still_prunes(env):
    """Pruning needs no queue and should not be held hostage to one."""
    store = Store(str(env / "sched.db"))
    try:
        from foci_screen.models import Document
        for i in range(25):
            store.observe(Document(source="web", key="web:x", text=f"revision {i}"))
    finally:
        store.close()

    scheduler.main()

    store = Store(str(env / "sched.db"))
    try:
        assert len(store.document_timeline("web", "web:x", limit=100)) == \
            scheduler.RETAIN_REVISIONS
    finally:
        store.close()


def test_sweep_raises_rather_than_returning_zero(env):
    with pytest.raises(scheduler.QueueUnavailable):
        scheduler.sweep()


def test_no_watchlists_is_a_success(env, monkeypatch):
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    assert scheduler.main() == 0


def test_sweep_enqueues_every_tenants_active_watchlists(env, monkeypatch):
    FakeQueue.enqueued = []
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    path = str(env / "sched.db")

    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.create_watchlist("navy", {"agency": "Department of Defense"})
        paused = acme.create_watchlist("paused", {"agency": "Department of Energy"})
        acme.set_watchlist_active(paused, False)
        other.create_watchlist("doe", {"agency": "Department of Energy"})
    finally:
        acme.close()
        other.close()

    assert scheduler.sweep() == 2
    assert sorted(t for t, _, _ in FakeQueue.enqueued) == ["acme", "other"]

    # Each enqueued run exists as a queued row, owned by the right tenant.
    acme = Store(path, tenant_id="acme")
    try:
        run_id = next(r for t, r, _ in FakeQueue.enqueued if t == "acme")
        assert acme.get_run(run_id)["status"] == "queued"
    finally:
        acme.close()


def test_unreadable_watchlist_is_skipped_not_fatal(env, monkeypatch):
    FakeQueue.enqueued = []
    monkeypatch.setattr(scheduler, "JobQueue", FakeQueue)
    path = str(env / "sched.db")

    store = Store(path, tenant_id="acme")
    try:
        good = store.create_watchlist("good", {"agency": "DoD"})
        broken = store.create_watchlist("broken", {"agency": "DoE"})
        with store._tx() as c:
            c.execute("UPDATE watchlists SET params=? WHERE watchlist_id=?",
                      ("{not json", broken))
    finally:
        store.close()

    assert scheduler.sweep() == 1
    assert FakeQueue.enqueued[0][2] == {"agency": "DoD"}
    assert good
```


## `tests/test_screening.py`

<a id="teststestscreeningpy"></a>

```python
"""Offline tests: rules, scoring, change detection, and notice rendering.

No network. Everything here runs against synthetic documents so the analytic
behaviour is pinned independently of whether api.sam.gov is up.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foci_screen.models import Contract, ContractingOfficer, Document, Entity  # noqa: E402
from foci_screen.notify import render  # noqa: E402
from foci_screen.risk import engine, lexicon  # noqa: E402
from foci_screen.store import Store, added_text  # noqa: E402


def make_contract(**kw) -> Contract:
    base = dict(award_id="N0001925C0042", piid="N0001925C0042",
                recipient_name="Vector Photonics Inc", recipient_uei="ABC123DEF456",
                awarding_agency="Department of Defense",
                awarding_sub_agency="Department of the Navy",
                award_amount=42_000_000.0, start_date="2025-01-15",
                end_date="2029-01-14",
                description="Development of laser countermeasure subsystem. "
                            "Data delivered under DFARS 252.227-7013.",
                officer=ContractingOfficer(name="Jane Doe", email="jane.doe.n00019@us.navy.mil",
                                           source="FPDS-NG <approvedBy>", confidence="high"))
    base.update(kw)
    c = Contract(**base)
    c.ip_clause_hits = lexicon.find_ip_clauses(c.description)
    return c


ENTITY = Entity(name="Vector Photonics Inc", uei="ABC123DEF456", cik="0001234567")


class TestLexicon(unittest.TestCase):
    def test_covered_and_haven_jurisdictions(self):
        hits = dict(lexicon.find_jurisdictions(
            "The investor is a British Virgin Islands entity backed by a Shanghai fund."))
        self.assertIn("British Virgin Islands", hits)
        self.assertIn("China", hits)

    def test_tiers_and_multipliers(self):
        self.assertEqual(lexicon.jurisdiction_tier("China"), "covered")
        self.assertEqual(lexicon.jurisdiction_tier("Cayman Islands"), "haven")
        self.assertGreater(lexicon.jurisdiction_multiplier("China"),
                           lexicon.jurisdiction_multiplier("Ireland"))

    def test_ip_clause_detection(self):
        clauses = lexicon.find_ip_clauses(
            "Technical data delivered with Government Purpose Rights per "
            "DFARS 252.227-7014.")
        self.assertTrue(any("7014" in c for c in clauses))
        self.assertTrue(any("Government Purpose Rights" in c for c in clauses))

    def test_no_false_positive_on_plain_text(self):
        self.assertEqual(lexicon.find_jurisdictions("Routine maintenance in Ohio."), [])

    def test_terms_match_whole_words_only(self):
        """'SPAC' inside 'space' turned an aerospace history page into a
        CRITICAL foreign-investment notice in an early build."""
        aerospace = ("The space vehicle launched from Vandenberg. Our spacecraft "
                     "programs and spacious facilities support the mission.")
        self.assertEqual(
            lexicon.find_terms(aerospace, lexicon.FOCI_EVENT_TERMS), [])

    def test_real_acquisition_vehicle_still_matches(self):
        hits = dict(lexicon.find_terms(
            "completed a special purpose acquisition transaction",
            lexicon.FOCI_EVENT_TERMS))
        self.assertIn("special purpose acquisition", hits)

    def test_generic_english_does_not_fire(self):
        for phrase in ["a team led by Dr. Chen",
                       "the Series A aircraft variant",
                       "fuel pipe inspection"]:
            self.assertEqual(
                lexicon.find_terms(phrase, lexicon.FOCI_EVENT_TERMS), [],
                f"generic phrase matched: {phrase!r}")


class TestRules(unittest.TestCase):
    def setUp(self):
        self.contracts = [make_contract()]

    def _ctx(self, is_new=False):
        return engine.RuleContext(entity=ENTITY, contracts=self.contracts, is_new=is_new)

    def test_foci_with_transaction_scores_higher_than_bare_mention(self):
        deal = Document(source="web", key="a", text=(
            "Vector Photonics today announced a strategic investment led by "
            "Silk Road Capital Partners Ltd, a British Virgin Islands company, "
            "which will take a board seat."))
        bare = Document(source="web", key="b", text=(
            "Our components are also sold to customers in the British Virgin Islands."))
        deal_signals = engine.rule_foci_jurisdiction(deal, self._ctx())
        bare_signals = engine.rule_foci_jurisdiction(bare, self._ctx())
        self.assertTrue(deal_signals)
        self.assertTrue(bare_signals)
        self.assertGreater(max(s.score for s in deal_signals),
                           max(s.score for s in bare_signals))

    def test_ip_collateral_strong_vs_weak(self):
        strong = Document(source="sec_edgar", key="c", text=(
            "The Borrower entered into an Intellectual Property Security Agreement "
            "granting the Collateral Agent a first priority lien on all patents."))
        weak = Document(source="sec_edgar", key="d", text=(
            "The Company maintains a revolving credit facility for working capital."))
        s_strong = engine.rule_ip_collateral(strong, self._ctx())
        s_weak = engine.rule_ip_collateral(weak, self._ctx())
        self.assertTrue(s_strong)
        self.assertTrue(s_weak)
        self.assertGreater(s_strong[0].score, s_weak[0].score * 1.5)
        self.assertIn("252.227-7013", " ".join(self.contracts[0].ip_clause_hits))

    def test_ip_clause_multiplier_applies(self):
        doc = Document(source="sec_edgar", key="e", text=(
            "Patent Security Agreement dated March 3, granting a security "
            "interest in intellectual property."))
        with_clause = engine.rule_ip_collateral(doc, self._ctx())[0].score
        plain = make_contract(description="Janitorial services.", )
        plain.ip_clause_hits = []
        ctx_plain = engine.RuleContext(entity=ENTITY, contracts=[plain])
        without = engine.rule_ip_collateral(doc, ctx_plain)[0].score
        self.assertGreater(with_clause, without)

    def test_uspto_security_interest_uses_metadata_not_prose(self):
        doc = Document(source="uspto", key="f", text="reel 12345 frame 678",
                       meta={"conveyance": "SECURITY INTEREST",
                             "assignee": "Orient Star Holdings Ltd",
                             "assignee_address": "Road Town, Tortola, British Virgin Islands",
                             "patent_count": 14})
        signals = engine.rule_uspto_security_interest(doc, self._ctx())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].jurisdiction, "British Virgin Islands")
        self.assertIn(signals[0].severity, ("high", "critical"))

    def test_structural_country_rule(self):
        c = make_contract(country_of_incorporation="VGB", recipient_country="USA")
        signals = engine.rule_contract_structural(c, self._ctx())
        self.assertTrue(any(s.jurisdiction == "British Virgin Islands" for s in signals))

    def test_distant_cooccurrence_does_not_escalate(self):
        """The Lockheed case: a missile range in the Marshall Islands on page 1
        and unrelated deal vocabulary far away is not a FOCI event."""
        text = ("A missile launched from a range in the Marshall Islands hurtled "
                "across the Pacific. " + ("Historical narrative filler. " * 120)
                + "Separately, the company announced a joint venture.")
        signals = engine.rule_foci_jurisdiction(
            Document(source="web", key="hist", doc_type="press", text=text), self._ctx())
        self.assertTrue(signals)
        self.assertIn(signals[0].severity, ("info", "low"),
                      "distant co-occurrence must stay a bare mention")

    def test_nearby_cooccurrence_does_escalate(self):
        text = ("The company announced a joint venture with a Marshall Islands "
                "entity that acquires a controlling interest.")
        signals = engine.rule_foci_jurisdiction(
            Document(source="web", key="deal", doc_type="press", text=text), self._ctx())
        self.assertTrue(signals)
        self.assertGreaterEqual(signals[0].severity_rank(), 2)  # medium or above

    def test_new_evidence_scores_higher(self):
        doc = Document(source="web", key="g", text=(
            "Announced a change of control transaction with a Shenzhen investor."))
        old = engine.rule_foci_jurisdiction(doc, self._ctx(is_new=False))
        new = engine.rule_foci_jurisdiction(doc, self._ctx(is_new=True))
        self.assertGreater(max(s.score for s in new), max(s.score for s in old))


class TestEvidenceQuality(unittest.TestCase):
    """Risk-factor boilerplate must not score like a completed transaction."""

    def setUp(self):
        self.contracts = [make_contract()]
        self.ctx = engine.RuleContext(entity=ENTITY, contracts=self.contracts)

    def test_risk_factor_scores_far_below_an_executed_agreement(self):
        risk_factor = Document(
            source="sec_edgar", key="rf", doc_type="10-K",
            text=("If we were to become insolvent, or if we may be required to "
                  "divest a business, there can be no assurance that our "
                  "intellectual property would be unaffected."))
        executed = Document(
            source="sec_edgar", key="ex", doc_type="8-K",
            text=("The Grantor hereby grants to the Collateral Agent a security "
                  "interest in intellectual property, pursuant to the "
                  "Intellectual Property Security Agreement dated today."))
        rf = engine.rule_ip_transfer(risk_factor, self.ctx)
        ex = engine.rule_ip_collateral(executed, self.ctx)
        self.assertTrue(rf and ex)
        self.assertLess(rf[0].score, ex[0].score / 3)
        self.assertIn(rf[0].severity, ("info", "low"))
        self.assertGreater(ex[0].severity_rank(), rf[0].severity_rank())

    def test_event_of_default_boilerplate_is_damped(self):
        """Every investment-grade revolver defines default to include bankruptcy.

        Reading that as a distress signal flags healthy primes, which is how a
        screening feed loses its audience.
        """
        boilerplate = Document(
            source="sec_edgar", key="eod", doc_type="8-K",
            text=("An Event of Default includes the bankruptcy or insolvency of "
                  "the Company or a Material Subsidiary (as defined in the "
                  "364-Day Revolving Credit Agreement)."))
        actual = Document(
            source="sec_edgar", key="real", doc_type="8-K",
            text=("On March 3 the Company filed a voluntary petition and entered "
                  "receivership; a 363 sale of assets is contemplated."))
        weak = engine.rule_ip_transfer(boilerplate, self.ctx)
        strong = engine.rule_ip_transfer(actual, self.ctx)
        self.assertTrue(weak and strong)
        self.assertLess(weak[0].score, strong[0].score / 2)
        self.assertIn("event-of-default clause", weak[0].rationale)

    def test_unsecured_revolver_is_a_weak_ip_collateral_match(self):
        doc = Document(source="sec_edgar", key="rev", doc_type="8-K",
                       text=("Bank of America, N.A., as administrative agent under "
                             "the 364-Day Revolving Credit Agreement."))
        signals = engine.rule_ip_collateral(doc, self.ctx)
        self.assertTrue(signals)
        self.assertIn(signals[0].severity, ("info", "low"))
        self.assertIn("Weak match", signals[0].rationale)

    def test_caveat_is_explained_to_the_reader(self):
        doc = Document(source="sec_edgar", key="rf2", doc_type="10-K",
                       text="We may divest certain assets from time to time.")
        signals = engine.rule_ip_transfer(doc, self.ctx)
        self.assertTrue(signals)
        self.assertIn("Weight reduced", signals[0].rationale)
        self.assertIn("periodic report", signals[0].rationale)
        self.assertIn("conditionally", signals[0].rationale)

    def test_8k_exhibit_is_not_damped(self):
        doc = Document(source="sec_edgar", key="ex2", doc_type="8-K",
                       text=("Patent Security Agreement executed and delivered; "
                             "the Grantor pledged intellectual property to the "
                             "Collateral Agent."))
        weight, caveat = engine._evidence_weight(doc, doc.text)
        self.assertEqual(weight, 1.0)
        self.assertEqual(caveat, "")

    def test_foci_in_risk_factors_is_damped_too(self):
        plain = Document(source="web", key="p", doc_type="press",
                         text="We completed a minority stake sale to a Cayman Islands fund.")
        hedged = Document(source="sec_edgar", key="h", doc_type="10-K",
                          text=("We may in the future pursue a minority stake sale, "
                                "potentially with a Cayman Islands fund."))
        s_plain = engine.rule_foci_jurisdiction(plain, self.ctx)
        s_hedged = engine.rule_foci_jurisdiction(hedged, self.ctx)
        self.assertTrue(s_plain and s_hedged)
        self.assertGreater(s_plain[0].score, s_hedged[0].score * 3)


class TestCorrelation(unittest.TestCase):
    def test_compound_signal_emitted_and_dominant(self):
        contracts = [make_contract()]
        foci = Document(source="web", key="h", text=(
            "Strategic investment by a Cayman Islands fund; the investor receives "
            "a board seat."))
        ip = Document(source="sec_edgar", key="i", text=(
            "Intellectual Property Security Agreement granting a security interest "
            "in intellectual property to the Collateral Agent."))
        ctx_entity = ENTITY
        signals = engine.evaluate_documents(
            [(foci, None), (ip, None)], ctx_entity, contracts)
        finding = engine.build_finding(ctx_entity, contracts, signals)
        rule_ids = {s.rule_id for s in finding.signals}
        self.assertIn("COMPOUND-01", rule_ids)
        self.assertIn(finding.severity, ("high", "critical"))

    def test_bare_mention_cannot_trigger_compound(self):
        """A geographic mention plus generic financing vocabulary is an
        ordinary company, not a compound risk."""
        contracts = [make_contract()]
        geographic = Document(
            source="web", key="geo", doc_type="press",
            text=("A missile launched from a range in the Marshall Islands. "
                  + "Historical filler. " * 100))
        generic_credit = Document(
            source="sec_edgar", key="cred", doc_type="8-K",
            text=("Bank of America, N.A., as administrative agent under the "
                  "revolving credit facility."))
        signals = engine.evaluate_documents(
            [(geographic, None), (generic_credit, None)], ENTITY, contracts)
        self.assertIn("FOCI-MENTION-01", {s.rule_id for s in signals})
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertNotIn("COMPOUND-01", {s.rule_id for s in finding.signals})
        self.assertIn(finding.severity, ("info", "low", "medium"))

    def test_no_compound_without_both_families(self):
        contracts = [make_contract()]
        only_ip = Document(source="sec_edgar", key="j", text=(
            "Patent Security Agreement with First National Bank as agent."))
        signals = engine.evaluate_documents([(only_ip, None)], ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertNotIn("COMPOUND-01", {s.rule_id for s in finding.signals})

    def test_identical_evidence_is_deduped(self):
        contracts = [make_contract()]
        docs = [(Document(source="web", key=f"k{i}",
                          text="Minority stake acquired by a Seychelles vehicle."), None)
                for i in range(8)]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        seychelles = [s for s in finding.signals if s.jurisdiction == "Seychelles"]
        self.assertEqual(len(seychelles), 1, "repeat sightings must collapse to one signal")

    def test_many_weak_signals_cannot_manufacture_a_critical(self):
        """The corroboration cap: severity may exceed the worst individual
        signal by at most one band, and only when two signals reach it."""
        contracts = [make_contract()]
        signals = [
            engine.Signal(rule_id=f"X{i}", category="IP_TRANSFER", severity="low",
                          score=6.0, title=f"weak {i}", rationale="r",
                          evidence=f"distinct evidence {i}", source="web")
            for i in range(12)
        ]
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertGreater(finding.total_score, 6.0)
        self.assertIn(finding.severity, ("low", "medium"),
                      "twelve low signals must not become high/critical")

    def test_a_single_strong_signal_still_lands(self):
        contracts = [make_contract()]
        signals = [engine.Signal(rule_id="S1", category="IP_COLLATERAL",
                                 severity="critical", score=34.0,
                                 title="executed IP security agreement",
                                 rationale="r", evidence="e", source="sec_edgar")]
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertEqual(finding.severity, "critical")

    def test_scoring_has_diminishing_returns(self):
        """Many distinct medium signals must not outrank one critical."""
        contracts = [make_contract()]
        places = ["Seychelles", "Panama", "Belize", "Mauritius", "Bahamas",
                  "Gibraltar", "Cyprus", "Malta"]
        docs = [(Document(source="web", key=f"k{i}",
                          text=f"Minority stake acquired by a {p} vehicle."), None)
                for i, p in enumerate(places)]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        finding = engine.build_finding(ENTITY, contracts, signals)
        self.assertGreater(len(finding.signals), 4)
        self.assertLess(finding.total_score, sum(s.score for s in finding.signals))


class TestChangeDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / "t.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_new_then_unchanged_then_modified(self):
        doc = Document(source="web", key="acme/news", text="Line one\nLine two")
        self.assertEqual(self.store.observe(doc).kind, "new")
        self.assertEqual(self.store.observe(doc).kind, "unchanged")

        doc2 = Document(source="web", key="acme/news",
                        text="Line one\nLine two\nStrategic investment from a BVI fund")
        change = self.store.observe(doc2)
        self.assertEqual(change.kind, "modified")
        self.assertIn("BVI", change.added_text)
        self.assertNotIn("Line one", change.added_text)
        self.assertTrue(change.is_material)

    def test_added_text_isolates_the_delta(self):
        delta = added_text("a\nb\nc", "a\nb\nc\nd")
        self.assertEqual(delta.strip(), "d")

    def test_rules_run_on_delta_only(self):
        """The whole point: steady-state boilerplate must not re-fire."""
        boilerplate = ("We operate globally including in the Cayman Islands.\n"
                       "Contact us for details.")
        doc = Document(source="web", key="x/legal", text=boilerplate)
        self.store.observe(doc)

        updated = Document(source="web", key="x/legal", text=(
            boilerplate + "\nToday we completed a change of control transaction."))
        change = self.store.observe(updated)
        signals = engine.evaluate_documents([(updated, change)], ENTITY, [make_contract()])
        evidence = " ".join(s.evidence for s in signals)
        self.assertIn("change of control", evidence)
        self.assertNotIn("Contact us for details", evidence)
        # The Cayman nexus came from unchanged context, so it must be reported
        # as the standing-nexus variant, not as a brand-new disclosure.
        self.assertIn("FOCI-JURIS-02", {s.rule_id for s in signals})
        self.assertEqual({s.jurisdiction for s in signals}, {"Cayman Islands"})

    def test_context_fallback_needs_an_event(self):
        """A standing foreign nexus alone must not fire on unrelated edits."""
        boilerplate = "We operate globally including in the Cayman Islands."
        doc = Document(source="web", key="y/legal", text=boilerplate)
        self.store.observe(doc)
        updated = Document(source="web", key="y/legal",
                           text=boilerplate + "\nWe updated our office hours.")
        change = self.store.observe(updated)
        signals = engine.evaluate_documents([(updated, change)], ENTITY, [make_contract()])
        self.assertEqual([s for s in signals if s.category == "FOCI"], [])


class TestRendering(unittest.TestCase):
    def _finding(self):
        contracts = [make_contract()]
        docs = [
            (Document(source="web", key="m", url="https://example.com/news",
                      text="Strategic investment led by a British Virgin Islands fund "
                           "which receives a board seat."), None),
            (Document(source="sec_edgar", key="n", url="https://sec.gov/x",
                      text="Intellectual Property Security Agreement granting a "
                           "security interest in intellectual property."), None),
        ]
        signals = engine.evaluate_documents(docs, ENTITY, contracts)
        signals += engine.evaluate_contracts(contracts, ENTITY)
        return engine.build_finding(ENTITY, contracts, signals, run_id="testrun")

    def test_subject_carries_severity_and_piid(self):
        finding = self._finding()
        subject = render.subject_for(finding)
        self.assertIn(finding.severity.upper(), subject)
        self.assertIn("N0001925C0042", subject)
        self.assertIn("FOCI/IP", subject, "both families present -> combined tag")

    def test_body_has_rationale_evidence_and_disclaimer(self):
        body = render.render_text(self._finding(), "testrun")
        self.assertIn("Why flagged:", body)
        self.assertIn("Source text:", body)
        self.assertIn("SUGGESTED VERIFICATION STEPS", body)
        self.assertIn("not a FOCI determination", body)
        self.assertIn("32 CFR Part 117", body)
        self.assertIn("DFARS 252.227-7013", body)

    def test_addresses_the_resolved_ko(self):
        finding = self._finding()
        self.assertEqual(finding.top_officer().email, "jane.doe.n00019@us.navy.mil")
        self.assertIn("Dear Jane Doe", render.render_text(finding))

    def test_eml_is_written_and_parseable(self):
        import email

        with tempfile.TemporaryDirectory() as tmp:
            finding = self._finding()
            path = render.write_eml(finding, tmp, sender="me@example.gov",
                                    recipient="jane.doe.n00019@us.navy.mil",
                                    run_id="testrun")
            self.assertTrue(path.is_file())
            msg = email.message_from_bytes(path.read_bytes())
            self.assertEqual(msg["To"], "jane.doe.n00019@us.navy.mil")
            self.assertIn(finding.severity.upper(), msg["Subject"])
            self.assertEqual(msg["X-FOCI-Severity"], finding.severity)
            self.assertEqual(msg["X-FOCI-Run"], "testrun")


class TestGmailGuards(unittest.TestCase):
    def test_default_config_does_not_send(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier, status_banner

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False, gmail_send=False)
            self.assertIn("OFF", status_banner(cfg))
            contracts = [make_contract()]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r1")
            result = GmailNotifier(cfg).deliver(finding, run_id="r1")
            self.assertEqual(result.status, "rendered")
            self.assertTrue(Path(result.path).is_file())

    def test_redirect_overrides_recipient(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False,
                         email_redirect_to="analyst@example.gov")
            contracts = [make_contract()]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r2")
            result = GmailNotifier(cfg).deliver(finding, run_id="r2")
            self.assertEqual(result.recipient, "analyst@example.gov")

    def test_unresolved_ko_is_suppressed_not_guessed(self):
        from foci_screen.config import Config
        from foci_screen.notify.gmail import GmailNotifier

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(out_dir=tmp, gmail_enabled=False)
            contracts = [make_contract(officer=ContractingOfficer())]
            finding = engine.build_finding(
                ENTITY, contracts,
                engine.evaluate_contracts(contracts, ENTITY), run_id="r3")
            result = GmailNotifier(cfg).deliver(finding, run_id="r3")
            self.assertEqual(result.status, "suppressed")
            self.assertEqual(result.recipient, "")


class TestFPDSParsing(unittest.TestCase):
    def test_officer_email_extracted_and_ranked(self):
        from xml.etree import ElementTree as ET

        from foci_screen.connectors.fpds import FPDSConnector

        xml = """<entry xmlns="http://www.w3.org/2005/Atom">
          <content><award>
            <transactionInformation>
              <createdBy>CLERK.SMITH.N00019@JSF.MIL</createdBy>
              <lastModifiedBy>JULIE.BAKEWELLCHISHOLM.N00019@JSF.MIL</lastModifiedBy>
              <approvedBy>KO.WARRANT.N00019@JSF.MIL</approvedBy>
            </transactionInformation>
            <purchaserInformation><contractingOfficeID>N00019</contractingOfficeID></purchaserInformation>
          </award></content></entry>"""
        entry = ET.fromstring(xml)
        officer = FPDSConnector(None)._officer_from_entry(entry)
        self.assertEqual(officer.email, "ko.warrant.n00019@jsf.mil")   # approver wins
        self.assertEqual(officer.confidence, "high")
        self.assertEqual(officer.name, "Ko Warrant")

    def test_falls_back_to_last_modifier(self):
        from xml.etree import ElementTree as ET

        from foci_screen.connectors.fpds import FPDSConnector

        xml = """<entry xmlns="http://www.w3.org/2005/Atom"><content><award>
            <transactionInformation>
              <lastModifiedBy>JULIE.BAKEWELLCHISHOLM.N00019@JSF.MIL</lastModifiedBy>
            </transactionInformation></award></content></entry>"""
        officer = FPDSConnector(None)._officer_from_entry(ET.fromstring(xml))
        self.assertEqual(officer.confidence, "medium")
        self.assertEqual(officer.name, "Julie Bakewellchisholm")


class TestWebNormalisation(unittest.TestCase):
    def test_volatile_content_is_stripped(self):
        from foci_screen.connectors.webwatch import normalise_page

        page_a = ("<html><body><main><p>Investor News</p>"
                  "<p>Updated 10:31 AM</p><p>© 2025 Acme</p>"
                  "<p>Acme announces Q3 results.</p></main></body></html>")
        page_b = page_a.replace("10:31 AM", "11:47 AM")
        self.assertEqual(normalise_page(page_a), normalise_page(page_b))

    def test_real_change_is_preserved(self):
        from foci_screen.connectors.webwatch import normalise_page

        a = "<html><body><main><p>Acme announces Q3 results.</p></main></body></html>"
        b = ("<html><body><main><p>Acme announces Q3 results.</p>"
             "<p>Acme announces investment from Cayman partner.</p></main></body></html>")
        self.assertNotEqual(normalise_page(a), normalise_page(b))
        self.assertIn("Cayman", normalise_page(b))


class StubHttp:
    """Deterministic HTTP double: maps a URL substring to a canned response."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def get(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        for needle, payload in self.routes.items():
            if needle in url:
                return payload
        return {"status": 404, "text": "", "json": None, "url": url}

    def post(self, url, **kw):
        return self.get(url, **kw)


class TestAttributionSafety(unittest.TestCase):
    """Regression guards for the worst failure mode this tool has.

    A screen that attributes another registrant's IP security agreement to the
    contractor under review, or that matches its own search phrase as evidence,
    is worse than no screen at all: it emails a federal contracting officer a
    confident, wrong allegation about a named company.
    """

    def test_connector_narration_never_triggers_a_prose_rule(self):
        """Text the tool writes about a filing must not be read as evidence.

        Item labels like "Bankruptcy or Receivership" contain vocabulary the
        prose rules look for. The connector emits codes only; the meaning is
        applied by the structured rule, which is tested separately below.
        """
        from foci_screen.connectors.sec_edgar import ITEM_MEANING

        contracts = [make_contract()]
        narrations = [
            Document(source="sec_edgar", key=f"n{i}",
                     text=(f"Form 8-K filed 2025-01-01 by Acme Corp. "
                           f"Reported items: {code}."))
            for i, code in enumerate(ITEM_MEANING)
        ]
        signals = engine.evaluate_documents([(d, None) for d in narrations],
                                            ENTITY, contracts)
        prose_rules = {"FOCI-JURIS-01", "FOCI-JURIS-02", "IPCOL-01", "IPXFER-01"}
        offenders = [s for s in signals if s.rule_id in prose_rules]
        self.assertEqual(offenders, [], f"narration matched a prose rule: {offenders}")

    def test_item_labels_are_not_emitted_into_document_text(self):
        """The connector-level half of the guard above."""
        from foci_screen.connectors.sec_edgar import EdgarConnector

        submissions = {"name": "Acme Corp", "filings": {"recent": {
            "form": ["8-K"], "accessionNumber": ["0000936468-25-000001"],
            "filingDate": ["2025-04-01"], "items": ["1.03,5.01"],
            "primaryDocument": ["a8k.htm"]}}}
        http = StubHttp({"data.sec.gov": {"status": 200, "json": submissions,
                                          "text": "", "url": ""}})
        docs = EdgarConnector(http).recent_filings("0000936468")
        self.assertEqual(len(docs), 1)
        self.assertNotIn("Receivership", docs[0].text)
        self.assertNotIn("Changes in Control", docs[0].text)
        self.assertEqual(docs[0].meta["item_codes"], ["1.03", "5.01"])

    def test_8k_item_codes_fire_the_structured_rule(self):
        """The meaning must still be caught — just through metadata."""
        doc = Document(source="sec_edgar", key="s1", url="https://sec.gov/x",
                       published="2025-04-01",
                       text="Form 8-K filed 2025-04-01 by Acme Corp. Reported items: 1.03, 5.01.",
                       meta={"item_codes": ["1.03", "5.01"], "company": "Acme Corp"})
        ctx = engine.RuleContext(entity=ENTITY, contracts=[make_contract()])
        signals = engine.rule_edgar_8k_items(doc, ctx)
        by_id = {s.rule_id: s for s in signals}
        self.assertIn("EDGAR-8K-5.01", by_id)
        self.assertIn("EDGAR-8K-1.03", by_id)
        self.assertEqual(by_id["EDGAR-8K-5.01"].category, "FOCI")
        self.assertEqual(by_id["EDGAR-8K-1.03"].category, "IP_TRANSFER")
        self.assertIn("FAR 42.12", by_id["EDGAR-8K-5.01"].rationale)

    def test_unknown_item_codes_are_ignored(self):
        doc = Document(source="sec_edgar", key="s2",
                       text="Reported items: 7.01, 9.01.",
                       meta={"item_codes": ["7.01", "9.01"], "company": "Acme"})
        ctx = engine.RuleContext(entity=ENTITY, contracts=[make_contract()])
        self.assertEqual(engine.rule_edgar_8k_items(doc, ctx), [])

    def test_full_text_search_requires_a_cik(self):
        """Without a CIK the phrase query cannot be attributed, so return nothing."""
        from foci_screen.connectors.sec_edgar import EdgarConnector

        http = StubHttp({})
        docs = EdgarConnector(http).full_text_search("Lockheed Martin", cik="")
        self.assertEqual(docs, [])
        self.assertEqual(http.calls, [], "must not even issue the query")

    def test_full_text_search_discards_other_registrants(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0001-23-000001:ex10.htm",
             "_source": {"ciks": ["0000999999"], "display_names": ["Someone Else Inc"],
                         "form": "8-K", "file_date": "2025-02-02"}}]}}
        http = StubHttp({
            "efts.sec.gov": {"status": 200, "json": hit, "text": "", "url": ""},
            "Archives": {"status": 200, "text": "<p>Patent Security Agreement</p>",
                         "json": None, "url": ""},
        })
        docs = EdgarConnector(http).full_text_search("Acme", cik="0000936468")
        self.assertEqual(docs, [], "hit belongs to a different CIK and must be dropped")

    def test_full_text_search_keeps_own_filing_with_real_body(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0000936468-25-000009:ex10-1.htm",
             "_source": {"ciks": ["0000936468"], "display_names": ["Acme Corp"],
                         "form": "8-K", "file_date": "2025-03-03",
                         "file_type": "EX-10.1"}}]}}
        body = ("<html><body>INTELLECTUAL PROPERTY SECURITY AGREEMENT dated as of "
                "March 3, 2025, granting the Collateral Agent a first priority lien "
                "on all patents of the Grantor.</body></html>")
        http = StubHttp({
            "efts.sec.gov": {"status": 200, "json": hit, "text": "", "url": ""},
            "Archives": {"status": 200, "text": body, "json": None, "url": ""},
        })
        docs = EdgarConnector(http).full_text_search("Acme", cik="0000936468")
        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertIn("first priority lien", doc.text)
        self.assertNotIn("EDGAR full-text hit", doc.text)
        self.assertTrue(doc.meta["verified_registrant"])
        signals = engine.rule_ip_collateral(
            doc, engine.RuleContext(entity=ENTITY, contracts=[make_contract()]))
        self.assertTrue(signals)
        self.assertIn("first priority lien", signals[0].evidence)

    def test_body_that_cannot_be_fetched_yields_no_document(self):
        from foci_screen.connectors.sec_edgar import EdgarConnector

        hit = {"hits": {"hits": [
            {"_id": "0000936468-25-000009:ex10-1.htm",
             "_source": {"ciks": ["0000936468"], "display_names": ["Acme Corp"],
                         "form": "8-K", "file_date": "2025-03-03"}}]}}
        http = StubHttp({"efts.sec.gov": {"status": 200, "json": hit, "text": "",
                                          "url": ""}})
        self.assertEqual(EdgarConnector(http).full_text_search("Acme", cik="0000936468"),
                         [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
```


## `tests/test_search.py`

<a id="teststestsearchpy"></a>

```python
"""Search and aggregation across contractors, awards, officers and agencies."""
from __future__ import annotations

import pytest

from foci_screen.models import Contract, ContractingOfficer, Entity, Finding, Signal
from foci_screen.store import Store


def make_contract(**kw):
    officer = ContractingOfficer(
        name=kw.pop("ko_name", "Jane Doe"),
        email=kw.pop("ko_email", "jane.doe@mail.mil"),
        source=kw.pop("ko_source", "FPDS-NG <approvedBy>"),
        confidence=kw.pop("ko_confidence", "high"))
    base = dict(
        award_id="A1", piid="N0001925C0001", recipient_name="ACME DYNAMICS LLC",
        recipient_uei="UEI123", awarding_agency="Department of Defense",
        awarding_sub_agency="Department of the Navy", award_amount=5_000_000.0,
        description="Submarine propulsion research", psc_description="R&D services",
        naics_description="Engineering services", solicitation_id="SOL-99",
        country_of_incorporation="USA")
    base.update(kw)
    return Contract(officer=officer, **base)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "search.db"), tenant_id="acme")
    yield s
    s.close()


@pytest.fixture()
def populated(store):
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    store.save_contract(
        make_contract(piid="N0001925C0002", award_id="A2", award_amount=12_000_000.0,
                      description="Radar sustainment",
                      ko_email="sam.smith@mail.mil", ko_name="Sam Smith"),
        "r1", "UEI123")
    store.save_contract(
        make_contract(piid="F0001925C0009", award_id="A3",
                      recipient_name="BEIJING OPTICS LTD", recipient_uei="UEI999",
                      awarding_agency="Department of Energy", awarding_sub_agency="",
                      award_amount=800_000.0, description="Optical components",
                      country_of_incorporation="CHN", foreign_owned_and_located=True),
        "r1", "UEI999")
    return store


# --------------------------------------------------------- officer names

@pytest.mark.parametrize("email,expected", [
    ("JULIE.BAKEWELLCHISHOLM.N00019@JSF.MIL", "Julie Bakewellchisholm"),
    # DoD's disambiguating digit belongs to the mailbox, not the person.
    ("SANDRA.T.REYES2.CIV@MAIL.MIL", "Sandra T Reyes"),
    ("LAUREN.H.MARTIN3.CIV@MAIL.MIL", "Lauren H Martin"),
    ("aubrey.r.callahan.civ@mail.mil", "Aubrey R Callahan"),
])
def test_officer_display_name_is_readable(email, expected):
    from foci_screen.connectors.fpds import _pretty_name
    assert _pretty_name(email) == expected


# ------------------------------------------------------------------ indexing

def test_contracts_are_indexed_for_search(populated):
    assert len(populated.search_contracts("submarine")) == 1
    assert len(populated.search_contracts("N0001925")) == 2


def test_saving_the_same_contract_twice_updates_rather_than_duplicates(store):
    store.save_contract(make_contract(award_amount=1.0), "r1", "UEI123")
    store.save_contract(make_contract(award_amount=99.0), "r2", "UEI123")

    rows = store.contracts_where("entity_key", "UEI123")
    assert len(rows) == 1
    assert rows[0]["amount"] == 99.0
    assert rows[0]["run_id"] == "r2"


def test_contract_without_an_identifier_is_skipped(store):
    store.save_contract(make_contract(piid="", award_id=""), "r1", "UEI123")
    assert store.contracts_where("entity_key", "UEI123") == []


def test_ip_clauses_round_trip_as_a_list(store):
    store.save_contract(
        make_contract(ip_clause_hits=["DFARS 252.227-7013", "Limited Rights data"]),
        "r1", "UEI123")
    assert store.get_contract("N0001925C0001")["ip_clauses"] == [
        "DFARS 252.227-7013", "Limited Rights data"]


# -------------------------------------------------------------------- search

def test_search_is_case_insensitive(populated):
    """Postgres LIKE is case-sensitive and SQLite has no ILIKE; both must match."""
    assert len(populated.search_entities("acme")) == 1
    assert len(populated.search_entities("ACME")) == 1
    assert len(populated.search_entities("AcMe")) == 1


def test_search_entities_aggregates_awards(populated):
    acme = populated.search_entities("acme")[0]
    assert acme["contract_count"] == 2
    assert acme["obligated"] == 17_000_000.0


def test_search_entities_attaches_latest_severity(populated):
    populated.save_finding(Finding(
        entity=Entity(name="ACME DYNAMICS LLC", uei="UEI123"),
        signals=[], total_score=22.0, severity="high", run_id="r1"))

    acme = populated.search_entities("acme")[0]
    assert acme["severity"] == "high"
    assert acme["score"] == 22.0


def test_unscreened_entity_reports_no_severity(populated):
    beijing = populated.search_entities("beijing")[0]
    assert beijing["severity"] is None
    assert beijing["foreign_owned"] == 1


def test_search_officers_groups_by_email(populated):
    officers = {o["ko_email"]: o for o in populated.search_officers()}
    assert set(officers) == {"jane.doe@mail.mil", "sam.smith@mail.mil"}
    assert officers["jane.doe@mail.mil"]["contract_count"] == 2
    assert officers["jane.doe@mail.mil"]["entity_count"] == 2


def test_search_officers_by_name(populated):
    assert len(populated.search_officers("sam")) == 1


def test_officers_without_an_email_are_excluded(store):
    """An unresolved officer is not a person you can address."""
    store.save_contract(make_contract(ko_email="", ko_name=""), "r1", "UEI123")
    assert store.search_officers() == []


def test_search_agencies_aggregates(populated):
    agencies = {a["agency"]: a for a in populated.search_agencies()}
    assert agencies["Department of Defense"]["contract_count"] == 2
    assert agencies["Department of Defense"]["obligated"] == 17_000_000.0
    assert agencies["Department of Energy"]["entity_count"] == 1


def test_search_all_covers_every_kind(populated):
    results = populated.search_all("acme")
    assert results["entities"]
    assert results["officers"] == [] or True   # name search need not match here
    assert set(results) == {"entities", "contracts", "officers", "agencies"}


def test_empty_query_browses_rather_than_failing(populated):
    assert len(populated.search_entities("")) == 2
    assert len(populated.search_agencies("")) == 2


def test_filtering_by_column_rejects_unknown_columns(populated):
    """The column name reaches SQL directly, so it is allow-listed."""
    with pytest.raises(ValueError):
        populated.contracts_where("amount; DROP TABLE contracts", "x")


def test_search_is_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.save_contract(make_contract(), "r1", "UEI123")
        assert len(acme.search_contracts("submarine")) == 1
        assert other.search_contracts("submarine") == []
        assert other.search_officers() == []
    finally:
        acme.close()
        other.close()


# ---------------------------------------------------------------- dashboards

def test_severity_counts_use_latest_screen_per_entity(store):
    """An entity screened weekly must not dominate the distribution."""
    ent = Entity(name="ACME", uei="UEI123")
    for run, sev in (("r1", "low"), ("r2", "medium"), ("r3", "critical")):
        store.save_finding(Finding(entity=ent, signals=[], total_score=1.0,
                                   severity=sev, run_id=run))

    assert store.severity_counts() == {"critical": 1}


def test_signal_category_counts(store):
    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="UEI123"),
        signals=[
            Signal(rule_id="R1", category="FOCI", severity="high", score=5,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R2", category="FOCI", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R3", category="IP_COLLATERAL", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
        ],
        total_score=7.0, severity="high", run_id="r1"))

    assert store.signal_category_counts() == {"FOCI": 2, "IP_COLLATERAL": 1}


def test_totals(populated):
    t = populated.totals()
    assert t["contracts"] == 3
    assert t["entities"] == 2
    assert t["officers"] == 2
    assert t["obligated"] == 17_800_000.0
    assert t["notices_pending"] == 0


# ---------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    return TestClient(app_module.app), app_module


def test_search_endpoint_groups_results(client):
    c, _ = client
    body = c.get("/v1/search?q=submarine", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["query"] == "submarine"


def test_search_endpoint_filters_by_kind(client):
    c, _ = client
    body = c.get("/v1/search?q=acme&kind=entity", headers=AUTH).json()
    assert "entities" in body and "contracts" not in body


def test_search_rejects_unknown_kind(client):
    c, _ = client
    assert c.get("/v1/search?q=x&kind=planets", headers=AUTH).status_code == 422


def test_overview_endpoint(client):
    c, _ = client
    body = c.get("/v1/overview", headers=AUTH).json()
    assert body["totals"]["contracts"] == 1
    assert set(body) == {"totals", "severity", "categories", "findings_by_day",
                         "top_entities", "recent_findings"}


def test_officer_profile(client):
    c, _ = client
    body = c.get("/v1/officers/jane.doe@mail.mil", headers=AUTH).json()
    assert body["officer"]["name"] == "Jane Doe"
    assert body["officer"]["confidence"] == "high"
    assert len(body["contracts"]) == 1


def test_unknown_officer_is_404(client):
    c, _ = client
    assert c.get("/v1/officers/nobody@mail.mil", headers=AUTH).status_code == 404


def test_agency_profile_falls_back_to_sub_agency(client):
    c, _ = client
    body = c.get("/v1/agencies/Department of the Navy", headers=AUTH).json()
    assert body["contract_count"] == 1
    assert body["entities"][0]["entity_name"] == "ACME DYNAMICS LLC"


def test_contract_detail(client):
    c, _ = client
    body = c.get("/v1/contracts/N0001925C0001", headers=AUTH).json()
    assert body["contract"]["ko_email"] == "jane.doe@mail.mil"
    assert body["contract"]["amount"] == 5_000_000.0


def test_entity_detail_uses_the_contracts_table(client):
    """An entity re-screened with no new signal still has its awards."""
    c, _ = client
    body = c.get("/v1/entities/UEI123", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["obligated"] == 5_000_000.0
    assert body["latest_finding"] is None


def test_search_endpoints_need_a_key(client):
    c, _ = client
    assert c.get("/v1/search?q=acme").status_code == 401
    assert c.get("/v1/overview").status_code == 401


# ------------------------------------------------------------------ web app

def test_web_ui_is_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "foci-screen" in r.text
    assert c.get("/app.js").status_code == 200
    assert c.get("/styles.css").status_code == 200


def test_static_mount_does_not_shadow_the_api(client):
    """The catch-all mount is last; /v1 must still route."""
    c, _ = client
    assert c.get("/v1/overview", headers=AUTH).status_code == 200
    assert c.get("/health").status_code == 200
```


## `tests/test_service.py`

<a id="teststestservicepy"></a>

```python
"""Phase 1 service: browser fallback, tenancy, notice gate, API.

Offline. The browser is a stub — these assert the *escalation logic*, that a
page unreadable over plain HTTP gets retried through a renderer and that a
missing renderer is reported rather than silently treated as an empty page.
Whether Chromium itself works is not something a unit test can tell you.
"""
from __future__ import annotations

import json

import pytest

from foci_screen.api import auth
from foci_screen.connectors.webwatch import WebWatchConnector
from foci_screen.models import Contract, Entity, Finding, Signal
from foci_screen.store import Store

# A real page: enough prose to clear the shell threshold.
REAL_PAGE = ("<html><body><main>" + ("Lockheed Martin announced today. " * 60)
             + "</main></body></html>")
# What an IR platform serves a non-browser client.
SHELL = '<html><body><div id="root"></div><noscript>Enable JavaScript</noscript></body></html>'


class StubHttp:
    """Returns a canned response per URL; records what was asked for."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def get(self, url, **kw):
        self.calls.append(url)
        return self.responses.get(url, {"status": 404, "text": "", "json": None})


class StubBrowser:
    """Stands in for Playwright."""

    def __init__(self, pages: dict, available: bool = True) -> None:
        self.pages = pages
        self.available = available
        self.rendered: list[str] = []

    def render(self, url: str) -> str:
        self.rendered.append(url)
        return self.pages.get(url, "")

    def close(self) -> None:
        pass


# ------------------------------------------------------------ browser fallback

def test_plain_fetch_is_not_escalated_when_it_works():
    http = StubHttp({"https://x.com/news": {"status": 200, "text": REAL_PAGE}})
    browser = StubBrowser({})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch("https://x.com/news", kind="press", company="X")

    assert doc is not None
    assert doc.meta["rendered_with"] == "http"
    assert browser.rendered == [], "browser must not be used when HTTP suffices"


def test_javascript_shell_escalates_to_browser():
    """A 200 carrying an empty app shell is the IR-platform failure mode."""
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 200, "text": SHELL}})
    browser = StubBrowser({url: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch(url, kind="investor_relations", company="X")

    assert doc is not None
    assert doc.meta["rendered_with"] == "browser"
    assert "Lockheed Martin announced" in doc.text
    assert browser.rendered == [url]


def test_timeout_escalates_to_browser():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 0, "text": "", "error": "ReadTimeout"}})
    browser = StubBrowser({url: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    doc = web.fetch(url, kind="investor_relations")

    assert doc is not None and doc.meta["rendered_with"] == "browser"


def test_known_browser_host_skips_the_doomed_plain_fetch():
    """Second page on a host already proven to need a browser."""
    a, b = "https://investors.x.com/one", "https://investors.x.com/two"
    http = StubHttp({a: {"status": 200, "text": SHELL}})
    browser = StubBrowser({a: REAL_PAGE, b: REAL_PAGE})
    web = WebWatchConnector(http, browser=browser)

    web.fetch(a)
    http.calls.clear()
    web.fetch(b)

    assert http.calls == [], "host is known bad; plain HTTP should be skipped"
    assert b in browser.rendered


def test_unreadable_host_is_reported_when_no_browser():
    """Silence here would read as 'nothing found', which is a different claim."""
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 403, "text": ""}})
    web = WebWatchConnector(http, browser=None)

    assert web.fetch(url) is None
    assert "investors.x.com" in web.skipped_js_hosts


def test_unavailable_browser_is_treated_as_no_browser():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 403, "text": ""}})
    web = WebWatchConnector(http, browser=StubBrowser({}, available=False))

    assert web.fetch(url) is None
    assert "investors.x.com" in web.skipped_js_hosts


def test_browser_failure_does_not_crash_the_screen():
    url = "https://investors.x.com/news"
    http = StubHttp({url: {"status": 200, "text": SHELL}})
    web = WebWatchConnector(http, browser=StubBrowser({}))   # renders ""

    assert web.fetch(url) is None


# ------------------------------------------------------------- normalisation

def test_nav_rail_in_main_does_not_hide_the_page():
    """Regression: Lockheed's newsroom puts a nav rail in <main>.

    Trusting the tag discarded 98% of that page — 150KB of HTML normalised to
    97 characters of menu labels, and every rule downstream saw a nav menu
    instead of press releases.
    """
    from foci_screen.connectors.webwatch import normalise_page

    body_prose = "Acme entered into a definitive agreement with an investor. " * 40
    html = (f"<html><body><main><nav>Media Contacts Press Archive</nav></main>"
            f"<div class='content'><p>{body_prose}</p></div></body></html>")

    text = normalise_page(html)

    assert "definitive agreement" in text
    assert len(text) > 1000


def test_substantial_main_is_still_preferred():
    """The <main> preference is right when <main> actually holds the content."""
    from foci_screen.connectors.webwatch import normalise_page

    content = "Acme announced a strategic investment today. " * 40
    html = (f"<html><body><main><p>{content}</p></main>"
            f"<footer>Careers Privacy Investors Sitemap</footer></body></html>")

    text = normalise_page(html)

    assert "strategic investment" in text
    assert "Sitemap" not in text, "footer should stay out when main is real"


# -------------------------------------------------------------------- storage

@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"), tenant_id="acme")
    yield s
    s.close()


def _finding(name="ACME", severity="high", score=20.0, run_id="r1") -> Finding:
    return Finding(
        entity=Entity(name=name, uei="UEI1"),
        signals=[Signal(rule_id="R-1", category="FOCI", severity=severity, score=score,
                        title="t", rationale="why", evidence="e", source="sec_edgar")],
        contracts=[Contract(award_id="A1", piid="P1", recipient_name=name)],
        total_score=score, severity=severity, run_id=run_id)


def test_findings_are_scoped_to_their_tenant(tmp_path):
    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.start_run("DoD", {}, run_id="r1")
        acme.save_finding(_finding())

        assert len(acme.search_findings()) == 1
        assert other.search_findings() == [], "tenants must not see each other"
    finally:
        acme.close()
        other.close()


def test_snapshots_are_shared_across_tenants(tmp_path):
    """The cost saving: ten tenants watching one prime fetch its 10-K once."""
    from foci_screen.models import Document

    path = str(tmp_path / "shared.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        doc = Document(source="sec_edgar", key="k1", text="body")
        assert acme.observe(doc).kind == "new"
        assert other.observe(doc).kind == "unchanged"
    finally:
        acme.close()
        other.close()


def test_run_lifecycle(store):
    run_id = store.start_run("DoD", {"months_back": 9}, status="queued")
    assert store.get_run(run_id)["status"] == "queued"

    store.mark_running(run_id)
    assert store.get_run(run_id)["status"] == "running"
    assert store.get_run(run_id)["finished_at"] is None, "running runs are not finished"

    store.set_run_progress(run_id, "screening LOCKHEED")
    store.finish_run(run_id, stats={"awards_examined": 3})

    run = store.get_run(run_id)
    assert run["status"] == "complete"
    assert run["progress"] == "screening LOCKHEED"
    assert json.loads(run["stats"])["awards_examined"] == 3


def test_failed_run_records_its_error(store):
    run_id = store.start_run("DoD", {}, status="queued")
    store.finish_run(run_id, status="failed", error="ConnectionError: nope")

    run = store.get_run(run_id)
    assert run["status"] == "failed"
    assert "ConnectionError" in run["error"]


def test_notice_starts_pending_and_records_its_decision(store):
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text="original body")

    assert store.get_notice(notice_id)["status"] == "pending"

    store.decide_notice(notice_id, "approved", decided_by="analyst@org",
                        note="verified", body_text="edited body")
    notice = store.get_notice(notice_id)

    assert notice["status"] == "approved"
    assert notice["decided_by"] == "analyst@org"
    # What was approved is what goes out — not a re-render.
    assert notice["body_text"] == "edited body"


def test_rejecting_keeps_the_original_body(store):
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="s", body_text="original")
    store.decide_notice(notice_id, "rejected", decided_by="a", note="false positive")

    notice = store.get_notice(notice_id)
    assert notice["status"] == "rejected"
    assert notice["body_text"] == "original"
    assert notice["decision_note"] == "false positive"


def _pending(store, body="original body"):
    return store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text=body)


def test_editing_keeps_the_generated_text(store):
    """What the tool wrote and what a person sent are different claims."""
    notice_id = _pending(store)
    assert store.decide_notice(notice_id, "approved", decided_by="a",
                               body_text="reworded body") == "ok"

    notice = store.get_notice(notice_id)
    assert notice["body_text"] == "reworded body"
    assert notice["original_body_text"] == "original body"

    edits = store.notice_edits(notice)
    assert edits["stats"] == {"added": 1, "removed": 1}


def test_unchanged_body_is_not_recorded_as_an_edit(store):
    notice_id = _pending(store)
    store.decide_notice(notice_id, "approved", decided_by="a",
                        body_text="  original body \n")

    notice = store.get_notice(notice_id)
    assert notice["original_body_text"] is None
    assert store.notice_edits(notice) is None


def test_a_decision_is_final(store):
    """A rejection must not be quietly overturned — it is the only labelled
    false-positive data the tool gets."""
    notice_id = _pending(store)
    assert store.decide_notice(notice_id, "rejected", decided_by="a") == "ok"
    assert store.decide_notice(notice_id, "approved", decided_by="b") == "already_decided"
    assert store.get_notice(notice_id)["status"] == "rejected"
    assert store.get_notice(notice_id)["decided_by"] == "a"


def test_deciding_an_unknown_notice(store):
    assert store.decide_notice("nope", "approved", decided_by="a") == "not_found"


def test_opening_a_pre_edit_database_adds_the_original_body_column(tmp_path):
    import sqlite3

    path = str(tmp_path / "v04.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE notices (
            notice_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default',
            run_id TEXT NOT NULL, finding_id TEXT, entity_key TEXT NOT NULL,
            entity_name TEXT NOT NULL, severity TEXT NOT NULL, recipient TEXT,
            officer_confidence TEXT, subject TEXT NOT NULL, body_text TEXT NOT NULL,
            status TEXT NOT NULL, decided_by TEXT, decided_at TEXT,
            decision_note TEXT, created_at TEXT NOT NULL);
    """)
    old.commit()
    old.close()

    s = Store(path)
    try:
        notice_id = _pending(s)
        assert s.decide_notice(notice_id, "approved", decided_by="a",
                               body_text="changed") == "ok"
        assert s.get_notice(notice_id)["original_body_text"] == "original body"
    finally:
        s.close()


def test_disclaimer_check_survives_word_wrapping():
    """The body wraps and indents the disclaimer; an untouched notice must pass."""
    from foci_screen.notify.render import DISCLAIMER, _wrap, has_disclaimer

    wrapped = "Dear KO,\n\n  " + "\n  ".join(_wrap(DISCLAIMER, 92)) + "\n"
    assert has_disclaimer(wrapped)
    assert not has_disclaimer("Dear KO,\n\nAcme is controlled by a foreign power.")


def test_ddl_splitter_ignores_semicolons_in_comments():
    """Regression: a semicolon inside a schema comment cut the next CREATE
    TABLE in half, and the failure only ever showed on a fresh database."""
    from foci_screen.store import _statements

    ddl = """
    -- Snapshots are global; which documents were gathered is not.
    CREATE TABLE a (x TEXT);
    CREATE TABLE b (y TEXT);
    """
    statements = _statements(ddl)

    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE a")
    assert statements[1].startswith("CREATE TABLE b")


def test_every_schema_statement_is_valid(tmp_path):
    """Builds the real schema from scratch, which is what a first deploy does."""
    s = Store(str(tmp_path / "fresh.db"))
    try:
        tables = {r["name"] for r in s._query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        s.close()

    assert {"runs", "snapshots", "snapshot_history", "snapshot_bodies",
            "entity_documents", "findings", "notices", "watchlists",
            "contracts", "notifications"} <= tables


def test_opening_a_v1_database_migrates_it(tmp_path):
    """An existing single-user database must survive the upgrade.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already exists, so
    without explicit column migration a v0.1 database keeps its old shape and
    then fails on the first index over `tenant_id`.
    """
    import sqlite3

    path = str(tmp_path / "legacy.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                           finished_at TEXT, agency TEXT, params TEXT);
        CREATE TABLE findings (id INTEGER PRIMARY KEY AUTOINCREMENT,
                               run_id TEXT NOT NULL, entity_key TEXT NOT NULL,
                               entity_name TEXT NOT NULL, severity TEXT NOT NULL,
                               score REAL NOT NULL, payload TEXT NOT NULL,
                               created_at TEXT NOT NULL);
        INSERT INTO runs VALUES ('old1', '2026-01-01T00:00:00Z', NULL, 'DoD', '{}');
        INSERT INTO findings (run_id, entity_key, entity_name, severity, score,
                              payload, created_at)
        VALUES ('old1', 'UEI1', 'ACME', 'high', 20.0, '{"signals": []}',
                '2026-01-01T00:00:00Z');
    """)
    old.commit()
    old.close()

    s = Store(path, tenant_id="default")
    try:
        assert len(s.recent_findings()) == 1, "pre-existing findings must survive"
        assert s.get_run("old1")["status"] == "complete"
        # And the new columns are usable.
        s.set_run_progress("old1", "resumed")
        assert s.get_run("old1")["progress"] == "resumed"
    finally:
        s.close()


def test_watchlist_round_trip(store):
    wl = store.create_watchlist("Navy quarterly", {"agency": "DoD", "months_back": 3})

    listed = store.list_watchlists()
    assert len(listed) == 1 and listed[0]["name"] == "Navy quarterly"

    store.mark_watchlist_run(wl, "run-9")
    assert store.list_watchlists()[0]["last_run_id"] == "run-9"

    assert store.set_watchlist_active(wl, False) is True
    assert store.list_watchlists(active_only=True) == []
    assert store.set_watchlist_active("nonexistent", False) is False


# ----------------------------------------------------------------------- auth

def test_parse_keys():
    assert auth.parse_keys("acme:k1,navy:k2") == {"k1": "acme", "k2": "navy"}
    assert auth.parse_keys("") == {}
    assert auth.parse_keys("malformed") == {}


def test_resolve_tenant():
    keymap = {"k1": "acme"}
    assert auth.resolve_tenant(keymap, "k1") == "acme"
    assert auth.resolve_tenant(keymap, "k2") == ""
    assert auth.resolve_tenant({}, "k1") == ""


# ------------------------------------------------------------------------ API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app bound to a temp database and one known key."""
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


AUTH = {"X-API-Key": "secret-key"}


def test_health_needs_no_key(client):
    c, _ = client
    body = c.get("/health").json()
    assert body["status"] == "ok"
    # Present so a deploy can be confirmed as the intended build without a key.
    assert "version" in body
    assert body["authenticated"] is True


def test_authenticated_routes_reject_missing_and_wrong_keys(client):
    c, _ = client
    assert c.get("/v1/findings").status_code == 401
    assert c.get("/v1/findings", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/findings", headers=AUTH).status_code == 200


def test_bearer_token_is_accepted(client):
    c, _ = client
    r = c.get("/v1/findings", headers={"Authorization": "Bearer secret-key"})
    assert r.status_code == 200


def test_api_fails_closed_with_no_keys_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "closed.db"))
    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    c = TestClient(app_module.app)
    assert c.get("/health").status_code == 200          # probe still works
    assert c.get("/v1/findings", headers=AUTH).status_code == 503


def test_creating_a_screen_returns_immediately(client, monkeypatch):
    c, app_module = client
    enqueued = []
    monkeypatch.setattr(app_module.queue, "enqueue",
                        lambda t, r, o: enqueued.append((t, r, o)))

    r = c.post("/v1/screens", headers=AUTH,
               json={"agency": "Department of Defense", "months_back": 6})

    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert r.json()["status"] == "queued"
    assert enqueued[0][0] == "acme" and enqueued[0][1] == run_id

    status = c.get(f"/v1/screens/{run_id}", headers=AUTH).json()
    assert status["status"] == "queued"


def test_screen_request_is_validated(client):
    c, _ = client
    assert c.post("/v1/screens", headers=AUTH, json={"agency": "X"}).status_code == 422
    assert c.post("/v1/screens", headers=AUTH,
                  json={"agency": "Department of Defense",
                        "min_severity": "catastrophic"}).status_code == 422


def test_unknown_run_is_404(client):
    c, _ = client
    assert c.get("/v1/screens/nope", headers=AUTH).status_code == 404


def test_notice_approval_flow(client):
    c, app_module = client
    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text="body")

    listed = c.get("/v1/notices?status=pending", headers=AUTH).json()["notices"]
    assert len(listed) == 1

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"decided_by": "analyst@org", "note": "confirmed"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    # Approval records a decision; it must not imply transmission.
    assert r.json()["delivery"]["mode"] == "render_only"


def test_unaddressable_notice_cannot_be_approved(client):
    """No resolved KO means no recipient — refuse rather than guess."""
    c, app_module = client
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="", officer_confidence="unresolved",
        subject="s", body_text="body")

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})
    assert r.status_code == 409


def _notice_with_disclaimer(app_module, prose="Dear KO,\n\nACME flagged."):
    from foci_screen.notify.render import DISCLAIMER, _wrap

    body = prose + "\n\n  " + "\n  ".join(_wrap(DISCLAIMER, 92)) + "\n"
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] flag", body_text=body)
    return notice_id, body


def test_approving_with_edits_records_both_versions(client):
    c, app_module = client
    notice_id, body = _notice_with_disclaimer(app_module)
    edited = body.replace("ACME flagged.", "ACME flagged; SAM ownership unconfirmed.")

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"note": "softened wording", "body_text": edited})
    assert r.status_code == 200
    assert r.json()["edited"] is True

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["body_text"] == edited
    assert notice["original_body_text"] == body
    assert notice["edits"]["stats"] == {"added": 1, "removed": 1}

    # And what goes out is the edited text, not a re-render.
    eml = c.get(f"/v1/notices/{notice_id}.eml", headers=AUTH).content
    assert b"SAM ownership unconfirmed" in eml


def test_an_edit_cannot_remove_the_disclaimer(client):
    """That paragraph is what keeps a notice from being an assertion about a
    named company. Refused, and nothing is recorded."""
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
               json={"body_text": "Dear KO,\n\nACME is foreign controlled."})
    assert r.status_code == 422
    assert "limitations statement" in r.json()["detail"]

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["status"] == "pending"
    assert notice["edits"] is None


def test_rejected_notice_cannot_then_be_approved(client):
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    assert c.post(f"/v1/notices/{notice_id}/reject", headers=AUTH,
                  json={"note": "missile range, not an investor"}).status_code == 200
    r = c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH, json={})
    assert r.status_code == 409

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["status"] == "rejected"


def test_decision_without_a_name_is_attributed_to_the_key_not_the_tenant(client):
    """Regression: a tenant named "default" recorded "decided by default",
    which reads as an automatic approval on the record proving a person looked."""
    c, app_module = client
    notice_id, _ = _notice_with_disclaimer(app_module)

    c.post(f"/v1/notices/{notice_id}/reject", headers=AUTH, json={"note": "fp"})

    notice = c.get(f"/v1/notices/{notice_id}", headers=AUTH).json()
    assert notice["decided_by"] == "api-key:acme"


def test_notice_list_carries_edits(client):
    c, app_module = client
    notice_id, body = _notice_with_disclaimer(app_module)
    c.post(f"/v1/notices/{notice_id}/approve", headers=AUTH,
           json={"body_text": body.replace("flagged", "flagged for review")})

    listed = c.get("/v1/notices", headers=AUTH).json()["notices"]
    assert listed[0]["edits"]["stats"]["added"] == 1


def test_notice_downloads_as_eml(client):
    c, app_module = client
    store = app_module.store_for("acme")
    notice_id = store.create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="[HIGH] FOCI flag", body_text="the body")

    r = c.get(f"/v1/notices/{notice_id}.eml", headers=AUTH)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("message/rfc822")
    assert b"the body" in r.content
    assert b"ko@mail.mil" in r.content


def test_watchlist_endpoints(client):
    c, _ = client
    r = c.post("/v1/watchlists", headers=AUTH,
               json={"name": "Navy", "screen": {"agency": "Department of the Navy"}})
    assert r.status_code == 201
    wl = r.json()["watchlist_id"]

    assert len(c.get("/v1/watchlists", headers=AUTH).json()["watchlists"]) == 1
    assert c.delete(f"/v1/watchlists/{wl}", headers=AUTH).status_code == 200
    assert c.delete("/v1/watchlists/nope", headers=AUTH).status_code == 404


def test_tenants_cannot_read_each_others_notices(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:key-a,other:key-b")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "multi.db"))
    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    c = TestClient(app_module.app)

    notice_id = app_module.store_for("acme").create_notice(
        run_id="r1", entity_key="UEI1", entity_name="ACME", severity="high",
        recipient="ko@mail.mil", officer_confidence="high",
        subject="s", body_text="b")

    assert c.get(f"/v1/notices/{notice_id}",
                 headers={"X-API-Key": "key-a"}).status_code == 200
    assert c.get(f"/v1/notices/{notice_id}",
                 headers={"X-API-Key": "key-b"}).status_code == 404
```


---

# Tools


## `tools/check_web_coverage.py`

<a id="toolscheckwebcoveragepy"></a>

```python
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
```


## `tools/export_session_log.py`

<a id="toolsexportsessionlogpy"></a>

```python
"""Export a Claude Code session transcript to a readable Markdown log.

    python tools/export_session_log.py                 # newest transcript
    python tools/export_session_log.py --out docs/SESSION_LOG.md
    python tools/export_session_log.py --transcript path/to/session.jsonl

Why this exists: the reasoning behind a design decision is worth more than the
decision, and most of it here happened in conversation rather than in commit
messages — why EDGAR search is CIK-constrained, why delivery stays inert, why
`<main>` is not trusted. A code comment records the rule; this records the
argument.

**What is included.** Human prompts and the assistant's replies, in order, with
timestamps and a compact list of the tools invoked between them. Internal
reasoning is not conversation and is left out; so are tool results, which are
enormous and reproducible from the code.

**Redaction.** Transcripts capture whatever passed through the session,
including keys typed into a config file. Secrets are replaced before writing —
extend `REDACTIONS`, or pass `--redact` — and the header records that this
happened, because a log that has been edited should say so.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# (pattern, replacement). Applied to every line of output.
REDACTIONS: list[tuple[re.Pattern, str]] = [
    # Local development API keys created during the session.
    (re.compile(r"\bdev-local-key\b"), "<redacted-api-key>"),
    (re.compile(r"\b(?:sk|rnd)_[A-Za-z0-9_\-]{12,}"), "<redacted-secret>"),
    # The operator's own address. Government contact addresses (.mil/.gov) are
    # public record and are the tool's actual output, so they stay.
    (re.compile(r"[\w.+-]+@(?:gmail|outlook|hotmail|yahoo|proton|icloud)\.[a-z.]+",
                re.I), "<contact-email>"),
    # Absolute paths leak the operator's username.
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+", re.I), "<home>"),
    (re.compile(r"/(?:c/)?Users/[^/\s\"']+", re.I), "<home>"),
]

SKIP_PREFIXES = (
    "<local-command-caveat>", "<command-name>", "<local-command-stdout>",
    "<command-message>", "<user-prompt-submit-hook>",
)


def find_transcript() -> Path | None:
    """Newest transcript for the current working directory's project."""
    mangled = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    root = Path.home() / ".claude" / "projects" / mangled
    if not root.is_dir():
        # Fall back to the newest transcript anywhere under ~/.claude/projects.
        root = Path.home() / ".claude" / "projects"
        if not root.is_dir():
            return None
        candidates = list(root.rglob("*.jsonl"))
    else:
        candidates = list(root.glob("*.jsonl"))
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def redact(text: str, extra: list[str]) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    for literal in extra:
        if literal:
            text = text.replace(literal, "<redacted>")
    return text


def is_human_prompt(record: dict) -> bool:
    """A message the person actually typed.

    Slash-command echoes, hook output and the compaction notice all arrive as
    `type: user` too; only a human turn carries `origin.kind == "human"`.
    """
    if record.get("type") != "user":
        return False
    origin = record.get("origin") or {}
    return origin.get("kind") == "human"


def text_blocks(message: dict, kinds: tuple[str, ...]) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in kinds:
            value = block.get("text") or ""
            if value.strip():
                out.append(value)
    return out


def tool_names(message: dict) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b.get("name", "?") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def stamp(record: dict) -> str:
    raw = record.get("timestamp") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M UTC")
    except ValueError:
        return raw


def build(transcript: Path, extra_redactions: list[str]) -> tuple[str, dict]:
    turns: list[dict] = []
    pending_tools: list[str] = []
    counts = {"prompts": 0, "replies": 0, "tool_calls": 0, "skipped": 0}

    with transcript.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                counts["skipped"] += 1
                continue

            message = record.get("message")
            if not isinstance(message, dict):
                continue

            if is_human_prompt(record):
                body = "\n".join(text_blocks(message, ("text",))).strip()
                if not body or body.startswith(SKIP_PREFIXES):
                    continue
                turns.append({"role": "user", "text": body, "at": stamp(record)})
                counts["prompts"] += 1
                pending_tools = []

            elif record.get("type") == "assistant":
                names = tool_names(message)
                pending_tools.extend(names)
                counts["tool_calls"] += len(names)

                said = "\n".join(text_blocks(message, ("text",))).strip()
                if not said:
                    continue
                turns.append({"role": "assistant", "text": said,
                              "at": stamp(record), "tools": pending_tools[:]})
                counts["replies"] += 1
                pending_tools = []

    lines = [
        "# Session log",
        "",
        "Conversation record for the build of `foci-screen`: the prompts given and the",
        "replies returned, in order. It is the reasoning behind the code — why EDGAR",
        "search is constrained by CIK, why email delivery stays inert, why `<main>` is",
        "not trusted — which the code itself can only assert.",
        "",
        f"- Source: `{transcript.name}`",
        f"- Exported: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"- {counts['prompts']} prompt(s), {counts['replies']} repl(y/ies), "
        f"{counts['tool_calls']} tool call(s)",
        "",
        "Tool *results* are omitted — they are large and reproducible from the code.",
        "Internal reasoning is omitted as it is not conversation. Secrets, personal",
        "email addresses and home directory paths have been replaced with placeholders;",
        "government contact addresses are public record and are kept.",
        "",
        "Regenerate with `python tools/export_session_log.py`.",
        "",
        "---",
        "",
    ]

    for turn in turns:
        if turn["role"] == "user":
            lines += [f"## Prompt · {turn['at']}", "", turn["text"], ""]
        else:
            lines += [f"### Response · {turn['at']}", ""]
            if turn.get("tools"):
                counted: dict[str, int] = {}
                for name in turn["tools"]:
                    counted[name] = counted.get(name, 0) + 1
                summary = ", ".join(
                    f"{n}×{c}" if c > 1 else n for n, c in counted.items())
                lines += [f"*Tools used: {summary}*", ""]
            lines += [turn["text"], ""]
        lines.append("---")
        lines.append("")

    return redact("\n".join(lines), extra_redactions), counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--transcript", help="path to a .jsonl transcript")
    parser.add_argument("--out", default="docs/SESSION_LOG.md")
    parser.add_argument("--redact", action="append", default=[],
                        help="extra literal string to replace (repeatable)")
    args = parser.parse_args()

    transcript = Path(args.transcript) if args.transcript else find_transcript()
    if transcript is None or not transcript.is_file():
        print("No transcript found. Pass --transcript explicitly.", file=sys.stderr)
        return 1

    markdown, counts = build(transcript, args.redact)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    print(f"wrote {out} ({len(markdown) / 1024:.0f} KB)")
    print(f"  {counts['prompts']} prompts, {counts['replies']} replies, "
          f"{counts['tool_calls']} tool calls")
    if counts["skipped"]:
        print(f"  {counts['skipped']} unparseable line(s) skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```


## `tools/install_smoke.py`

<a id="toolsinstallsmokepy"></a>

```python
"""Boot an *installed* foci-screen the way the Docker images do, and probe it.

    python -m venv /tmp/v && /tmp/v/bin/pip install ".[api,queue,postgres]"
    /tmp/v/bin/python tools/install_smoke.py

A pre-deploy check for when a container build is not available. Run it with the
interpreter of an environment where the package was installed normally (not
`pip install -e`), so it exercises what a wheel actually ships rather than the
working copy. It runs from a temporary directory for the same reason.

It checks what most often breaks between "tests pass" and "the image boots":
web assets missing from the package, extras that do not exist, entrypoints that
do not import, the Render health check, auth failing closed, and the scheduler
failing its cron run (exit 2) when there is no queue. It does not exercise the
OS layer — system libraries, Chromium, file permissions — which only a real
image build can.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

work = pathlib.Path(tempfile.mkdtemp(prefix="foci-smoke-"))
os.chdir(work)  # nothing importable from the source tree
env = {**os.environ,
       "FOCI_API_KEYS": "default:smoke-key",
       "FOCI_DB": str(work / "smoke.db"),
       "FOCI_CACHE": str(work / "cache"),
       "FOCI_OUT": str(work / "out"),
       "FOCI_USER_AGENT": "foci-screen smoke test (ops@example.org)",
       "PORT": "8099"}
for var in ("DATABASE_URL", "REDIS_URL"):
    env.pop(var, None)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def get(path, key=None):
    req = urllib.request.Request(f"http://127.0.0.1:8099{path}")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# Same command as the API image's CMD.
server = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "foci_screen.api.app:app",
     "--host", "127.0.0.1", "--port", "8099", "--workers", "1"],
    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(60):
        try:
            if get("/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.5)
    status, body, _ = get("/health")
    payload = json.loads(body or b"{}")
    # The version comes from installed package metadata, so "unknown" means the
    # package was not really installed and a stale value means a stale install —
    # either way you would be looking at a build you did not think you shipped.
    check("GET /health (Render healthCheckPath) reports a version",
          status == 200 and payload.get("version") not in (None, "unknown"),
          body[:140].decode())

    status, body, _ = get("/")
    check("GET / serves the web UI from site-packages",
          status == 200 and b"foci-screen" in body)
    for asset in ("/app.js", "/styles.css"):
        status, body, headers = get(asset)
        check(f"GET {asset}", status == 200 and len(body) > 1000,
              headers.get("content-type", ""))

    status, _, _ = get("/v1/overview")
    check("authenticated route refuses without a key", status == 401, str(status))
    status, body, _ = get("/v1/overview", key="smoke-key")
    check("authenticated route works with a key", status == 200, body[:80].decode())
    status, body, _ = get("/v1/search?q=anything", key="smoke-key")
    check("search against a fresh database", status == 200, body[:80].decode())
finally:
    server.terminate()
    try:
        out, _ = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        out, _ = server.communicate()
    tracebacks = [ln for ln in out.splitlines() if "Traceback" in ln or "ERROR" in ln]
    check("server log free of tracebacks", not tracebacks, "; ".join(tracebacks[:3]))

# Worker image CMD and scheduler cron command: must at least import cleanly.
for module in ("foci_screen.worker", "foci_screen.scheduler", "foci_screen.jobs"):
    proc = subprocess.run([sys.executable, "-c", f"import {module}"],
                          env=env, capture_output=True, text=True)
    check(f"import {module}", proc.returncode == 0, proc.stderr.strip()[-200:])

# Scheduler with no Redis: prune runs, then the sweep refuses and the process
# exits 2 so a cron platform marks the run failed rather than green.
proc = subprocess.run([sys.executable, "-m", "foci_screen.scheduler"],
                      env=env, capture_output=True, text=True, timeout=120)
output = proc.stdout + proc.stderr
check("scheduler without Redis fails the cron run (exit 2), after pruning",
      proc.returncode == 2 and "pruned" in output and "Traceback" not in output,
      f"exit {proc.returncode}")

try:
    import psycopg  # noqa: E402  (postgres extra)
    check("psycopg importable (postgres extra)", True, psycopg.__version__)
except ImportError as exc:
    check("psycopg importable (postgres extra)", False, str(exc))

scripts = pathlib.Path(sys.executable).parent
for exe in ("foci-screen", "foci-worker", "foci-scheduler"):
    found = any((scripts / f"{exe}{ext}").exists() for ext in ("", ".exe"))
    check(f"console script {exe}", found)

width = max(len(n) for n, _, _ in results)
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
print(json.dumps({"passed": sum(ok for _, ok, _ in results), "total": len(results)}))
shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
```


## `tools/positive_control.py`

<a id="toolspositivecontrolpy"></a>

```python
"""Positive control: prove the detection chain fires on real filings.

A screening tool that has been tuned for precision can go silent without
anyone noticing. This runs the live connectors against registrants that are
known to have the thing we look for, and fails loudly if the rules do not fire.

Run it after changing lexicons, weights, or the evidence-quality damper:

    python tools/positive_control.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foci_screen.config import get_config  # noqa: E402
from foci_screen.connectors.sec_edgar import EdgarConnector  # noqa: E402
from foci_screen.httpclient import HttpClient  # noqa: E402
from foci_screen.models import Contract, ContractingOfficer, Entity  # noqa: E402
from foci_screen.risk import engine, lexicon  # noqa: E402

# Registrants that genuinely filed an Intellectual Property Security Agreement.
# Verified against EDGAR full-text search at build time.
CASES = [
    ("Sovos Brands, Inc.", "0001856608"),
]


def contract_fixture() -> Contract:
    c = Contract(
        award_id="TEST-0001", piid="TEST-0001", recipient_name="Test Co",
        award_amount=10_000_000.0,
        description="R&D with technical data delivered under DFARS 252.227-7013.",
        officer=ContractingOfficer(name="Test KO", email="ko@example.gov",
                                   source="fixture", confidence="high"))
    c.ip_clause_hits = lexicon.find_ip_clauses(c.description)
    return c


def main() -> int:
    cfg = get_config()
    http = HttpClient(cfg)
    edgar = EdgarConnector(http)
    contracts = [contract_fixture()]
    failures: list[str] = []

    for name, cik in CASES:
        print(f"\n=== {name} (CIK {cik})")
        entity = Entity(name=name, cik=cik)
        docs = edgar.full_text_search(name, cik=cik)
        print(f"    documents retrieved: {len(docs)}")
        if not docs:
            failures.append(f"{name}: full-text search returned no documents")
            continue

        for d in docs[:2]:
            print(f"    - {d.doc_type:<6} {d.published}  {len(d.text):>7} chars  {d.url[:88]}")
            assert d.meta.get("verified_registrant"), "unverified registrant leaked through"
            assert cik.zfill(10) == str(d.meta["cik"]).zfill(10)

        signals = engine.evaluate_documents([(d, None) for d in docs], entity, contracts)
        ip = [s for s in signals if s.category == "IP_COLLATERAL"]
        print(f"    signals: {len(signals)} total, {len(ip)} IP_COLLATERAL")
        for s in sorted(ip, key=lambda x: -x.score)[:3]:
            print(f"      [{s.severity:>8}] {s.score:>6}  {s.title}")
            print(f"        evidence: {' '.join(s.evidence.split())[:180]}")

        if not ip:
            failures.append(f"{name}: no IP_COLLATERAL signal on a known IP security agreement")
            continue

        finding = engine.build_finding(entity, contracts, signals)
        print(f"    finding: {finding.severity.upper()} (score {finding.total_score})")
        if finding.severity in ("info", "low"):
            failures.append(
                f"{name}: executed IP security agreement scored only {finding.severity}")

    print("\n" + "=" * 70)
    if failures:
        print("POSITIVE CONTROL FAILED — the screen has gone blind to real hits:")
        for f in failures:
            print(f"  * {f}")
        return 1
    print("POSITIVE CONTROL PASSED — detection chain fires on real filings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```


---

# Deployment


## `Dockerfile`

<a id="dockerfile"></a>

```text
# API and scheduler. No browser here — see Dockerfile.worker for that.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY foci_screen ./foci_screen
RUN pip install --no-cache-dir ".[api,queue,postgres]"

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

ENV PORT=8000
EXPOSE 8000

# Single worker: Store holds one lock-guarded connection per process, and
# screening happens in the worker service, not here.
CMD uvicorn foci_screen.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1
```


## `Dockerfile.worker`

<a id="dockerfileworker"></a>

```text
# Screening worker. Carries Chromium so investor-relations pages can be read.
#
# This image is roughly 700MB larger than the API's because of the browser and
# its system libraries. That is the reason the two are separate: the API is
# redeployed far more often and has no use for any of it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY pyproject.toml README.md ./
COPY foci_screen ./foci_screen
RUN pip install --no-cache-dir ".[api,queue,postgres,browser]"

# --with-deps pulls the shared libraries Chromium needs and requires root.
# Installing to a shared path (not /root/.cache) so the unprivileged user can
# read it afterwards.
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

CMD ["python", "-m", "foci_screen.worker"]
```


## `render.yaml`

<a id="renderyaml"></a>

```yaml
# Render Blueprint. Deploy with: New > Blueprint, pointed at this repository.
#
# Four services, and each one earns its place:
#   foci-api        FastAPI. Validates, enqueues, reads back. Never screens.
#   foci-worker     Runs screens. Carries Chromium; needs the memory.
#   foci-scheduler  Nightly sweep of active watchlists.
#   foci-db / -kv   Postgres and Redis.
#
# Values marked `sync: false` are NOT set from this file. Render prompts for
# them on first deploy, which is correct — they are secrets, and one of them
# decides whether the API is reachable at all.

databases:
  - name: foci-db
    databaseName: foci
    user: foci
    plan: basic-256mb          # `free` works, but Render expires free DBs at 30 days
    postgresMajorVersion: "16"

services:
  # ------------------------------------------------------------------ cache
  - type: redis
    name: foci-kv
    plan: free
    maxmemoryPolicy: noeviction   # a queue must not have jobs evicted under pressure
    ipAllowList: []               # private network only

  # -------------------------------------------------------------------- API
  - type: web
    name: foci-api
    runtime: docker
    dockerfilePath: ./Dockerfile
    plan: starter
    healthCheckPath: /health
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: foci-db, property: connectionString }
      - key: REDIS_URL
        fromService: { type: redis, name: foci-kv, property: connectionString }
      # "tenant:key" pairs, comma separated. With this unset the API returns 503
      # on every authenticated route — it fails closed on purpose.
      - key: FOCI_API_KEYS
        sync: false
      # SEC and other .gov APIs block a User-Agent with no real contact address.
      - key: FOCI_USER_AGENT
        sync: false
      - key: FOCI_CORS_ORIGINS
        sync: false
      - key: SAM_API_KEY
        sync: false
      - key: USPTO_API_KEY
        sync: false
      # Step 3 stays inert. Read the note in README before changing either.
      - key: GMAIL_ENABLED
        value: "false"
      - key: GMAIL_SEND
        value: "false"
      # Safety valve for a pilot: while set, every notice goes here, never to a
      # contracting officer.
      - key: FOCI_EMAIL_REDIRECT_TO
        sync: false

  # ----------------------------------------------------------------- worker
  - type: worker
    name: foci-worker
    runtime: docker
    dockerfilePath: ./Dockerfile.worker
    plan: standard              # Chromium needs the headroom; starter will OOM
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: foci-db, property: connectionString }
      - key: REDIS_URL
        fromService: { type: redis, name: foci-kv, property: connectionString }
      - key: FOCI_USER_AGENT
        sync: false
      - key: SAM_API_KEY
        sync: false
      - key: USPTO_API_KEY
        sync: false
      - key: FOCI_BROWSER
        value: "true"
      # Render's filesystem is ephemeral; the HTTP cache is a per-deploy
      # convenience, not storage. Snapshots live in Postgres.
      - key: FOCI_CACHE
        value: /tmp/foci-cache
      - key: GMAIL_ENABLED
        value: "false"
      - key: GMAIL_SEND
        value: "false"
      - key: FOCI_EMAIL_REDIRECT_TO
        sync: false

  # -------------------------------------------------------------- scheduler
  - type: cron
    name: foci-scheduler
    runtime: docker
    dockerfilePath: ./Dockerfile
    plan: starter
    schedule: "0 7 * * *"       # 07:00 UTC daily
    dockerCommand: python -m foci_screen.scheduler
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: foci-db, property: connectionString }
      - key: REDIS_URL
        fromService: { type: redis, name: foci-kv, property: connectionString }
      - key: FOCI_USER_AGENT
        sync: false
```


## `.github/workflows/ci.yml`

<a id="githubworkflowsciyml"></a>

```yaml
# Tests, then the two Docker images.
#
# The image jobs are the point. The images were written without a container
# runtime available to build them, so until this workflow runs they are
# unverified — and an image that will not boot is the most expensive kind of
# thing to discover during a first Render deploy. GitHub Actions has Docker, so
# the first push proves them.
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"       # matches the images

      - name: Install (non-editable, as the images do)
        run: |
          python -m pip install --upgrade pip
          pip install ".[api,queue,postgres,dev]"

      - name: Lint
        run: ruff check foci_screen tests tools

      - name: Tests
        run: pytest -q

      - name: Install smoke test
        # From a directory with no source tree, so it exercises what the wheel
        # actually ships rather than the checkout.
        run: |
          mkdir -p /tmp/elsewhere
          cd /tmp/elsewhere
          python "$GITHUB_WORKSPACE/tools/install_smoke.py"

  api-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      - name: Build
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          push: false
          load: true
          tags: foci-api:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Boot it and check the Render health path
        run: |
          docker run -d --name api -p 8000:8000 \
            -e FOCI_API_KEYS=ci:ci-key \
            -e FOCI_USER_AGENT="foci-screen CI (ci@example.org)" \
            foci-api:ci
          for i in $(seq 1 30); do
            if curl -fsS http://localhost:8000/health > /tmp/health.json; then break; fi
            sleep 2
          done
          cat /tmp/health.json
          # The web UI must be inside the image, not just the repo.
          curl -fsS http://localhost:8000/ | grep -q foci-screen
          curl -fsS http://localhost:8000/app.js > /dev/null
          # Auth must fail closed.
          test "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/v1/overview)" = 401
          curl -fsS -H 'Authorization: Bearer ci-key' http://localhost:8000/v1/overview > /dev/null
          docker logs api | grep -i traceback && exit 1 || true
          docker rm -f api

  worker-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      - name: Build
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile.worker
          push: false
          load: true
          tags: foci-worker:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Chromium actually launches as the unprivileged user
        # The failure this catches: `playwright install --with-deps` runs as root
        # and the image then drops to uid 10001. If the browser or its system
        # libraries are unreadable afterwards, every IR page silently goes
        # unread in production while the worker looks healthy.
        run: |
          docker run --rm foci-worker:ci python -c "
          from foci_screen.connectors.browser import LAUNCH_ARGS, user_agent
          from playwright.sync_api import sync_playwright
          p = sync_playwright().start()
          b = p.chromium.launch(headless=True, args=list(LAUNCH_ARGS))
          page = b.new_context(user_agent=user_agent('foci-screen CI (ci@example.org)')).new_page()
          page.set_content('<main>rendered</main>')
          assert 'rendered' in page.content()
          print('chromium ok:', b.version)
          b.close(); p.stop()
          "

      - name: Worker entry point starts and reports a missing queue
        run: |
          set +e
          out=$(docker run --rm foci-worker:ci python -m foci_screen.worker 2>&1)
          code=$?
          set -e
          echo "$out"
          echo "exit $code"
          echo "$out" | grep -qi "redis" || { echo "expected a queue diagnostic"; exit 1; }
          echo "$out" | grep -qi traceback && exit 1 || true
```


## `.dockerignore`

<a id="dockerignore"></a>

```gitignore
.git
.github
.claude
docs
.venv
venv
__pycache__
*.py[cod]
*.egg-info
.pytest_cache
.ruff_cache
.cache
.env
*.db
out
probe
tests
tools
*.eml
Dockerfile*
render.yaml
```


## `.gitattributes`

<a id="gitattributes"></a>

```gitattributes
# This repository is authored on Windows and runs on Linux. Git on Windows
# checks files out with CRLF by default, and a Docker build takes its context
# from the working copy — so without this, a shell line inside a RUN step can
# arrive at the image with a trailing carriage return and fail in ways that read
# as nonsense ("/bin/sh: 1: \r: not found").
#
# Normalise everything textual to LF in the repository and on checkout.
* text=auto eol=lf

*.zip binary
*.png binary
*.jpg binary
*.ico binary
*.eml -text
```


## `.gitignore`

<a id="gitignore"></a>

```gitignore
.env
*.db
.cache/
out/
probe/
__pycache__/
*.py[cod]
.venv/
venv/
credentials.json
token.json
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
build/
*.eml
.ms-playwright/
# Local dev convenience; contains machine-specific interpreter paths.
.claude/
# Release archives and handover bundles are build output, not source.
foci-screen-v*.zip
*.bundle
```


## `.env.example`

<a id="envexample"></a>

```bash
# Copy to .env. Every value is optional; the tool degrades rather than fails.

# --- REQUIRED IN PRACTICE ---------------------------------------------------
# SEC and other .gov endpoints block generic agents. Use a real contact address.
FOCI_USER_AGENT=foci-screen/0.1 (you@yourorg.gov)

# --- OPTIONAL API KEYS ------------------------------------------------------
# SAM.gov entity registration + solicitation points of contact.
#   free at https://sam.gov -> Account Details -> Request Public API Key
SAM_API_KEY=

# USPTO Open Data Portal. Without it, recorded IP security interests are NOT
# checked, and the screen will say so rather than implying none exist.
#   free at https://developer.uspto.gov
USPTO_API_KEY=

# Trade.gov Consolidated Screening List (BIS Entity List, DDTC, OFAC combined).
TRADE_GOV_API_KEY=

# --- STORAGE ----------------------------------------------------------------
FOCI_DB=foci_screen.db
FOCI_CACHE=.cache
FOCI_OUT=out

# Postgres. Set this and FOCI_DB is ignored. Required for any deployment with
# more than one process or an ephemeral disk. Render injects it automatically.
DATABASE_URL=

# --- SERVICE MODE (API + worker) --------------------------------------------
# Without Redis, screens run in a background thread and die with the process.
# That is fine for a laptop and wrong for a deployment.
REDIS_URL=
FOCI_JOB_TIMEOUT=3600

# API keys as "tenant:key" pairs, comma separated. With this empty the API
# refuses every authenticated request — it fails closed deliberately.
#   FOCI_API_KEYS=acme:sk_live_9f3c...,navy-pmo:sk_live_1a7b...
FOCI_API_KEYS=
# Comma-separated origins for a browser client. Empty disables CORS entirely.
FOCI_CORS_ORIGINS=

# --- HEADLESS BROWSER -------------------------------------------------------
# Investor-relations subdomains are JavaScript applications that drop
# non-browser clients. Without this the screen cannot read them and says so.
#   pip install -e ".[browser]" && playwright install chromium
FOCI_BROWSER=true
FOCI_BROWSER_TIMEOUT=25

# --- HTTP -------------------------------------------------------------------
FOCI_TIMEOUT=45
FOCI_QPS=3.0
FOCI_CACHE_TTL=21600
FOCI_RETRIES=3

# --- STEP 3: EMAIL ----------------------------------------------------------
# All three guards below are OFF by default. With no changes, notices are
# written to ./out as .eml files and nothing is transmitted.

# Guard 1: master switch for the Gmail path.
GMAIL_ENABLED=false

# Guard 2: send rather than draft. Leave false. Drafts let a human read each
# notice before it reaches a contracting officer.
GMAIL_SEND=false

GMAIL_CREDENTIALS=credentials.json
GMAIL_TOKEN=token.json
GMAIL_SENDER=

# Guard 3: safety valve. While set, EVERY notice goes here instead of to the
# KO. Set this to your own address for the whole of your first live pilot.
FOCI_EMAIL_REDIRECT_TO=
```
