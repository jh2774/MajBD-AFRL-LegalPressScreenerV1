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
