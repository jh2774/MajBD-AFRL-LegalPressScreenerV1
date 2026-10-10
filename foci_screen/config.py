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
from urllib.parse import quote

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    return raw.lower() in _TRUE if raw else default


def _switch(name: str) -> bool | None:
    """A flag that can also be left alone: True, False, or None for not set."""
    raw = _env(name)
    return raw.lower() in _TRUE if raw else None


def _database_url() -> str:
    """Where Postgres is, from DATABASE_URL or from Cloud SQL's separate values.

    Render hands over one URL. Google Cloud's own convention for Cloud Run is
    four values — the instance's connection name and a user, password and
    database — with only the password kept as a secret. Both are accepted, so
    a password with a `/` or `@` in it never has to be percent-encoded by hand
    into a URL, which is the classic way that step goes wrong.

    Cloud Run mounts each attached Cloud SQL instance as a Unix socket under
    /cloudsql/<connection name>; `host=` with a path is how libpq is told to
    use one.
    """
    url = _env("DATABASE_URL")
    if url:
        return url
    instance = _env("INSTANCE_CONNECTION_NAME") or _env("CLOUD_SQL_CONNECTION_NAME")
    user, name = _env("DB_USER"), _env("DB_NAME")
    if not (instance and user and name):
        return ""
    socket = _env("INSTANCE_UNIX_SOCKET") or f"/cloudsql/{instance}"
    password = os.environ.get("DB_PASS", "") or os.environ.get("DB_PASSWORD", "")
    auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
    return f"postgresql://{auth}@/{quote(name, safe='')}?host={quote(socket, safe='/:')}"


def _default_cache_mb() -> str:
    # Cloud Run has no disk: files written inside the container are held in
    # the instance's memory and count against its limit.
    return "64" if os.environ.get("K_SERVICE") else "256"


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
    # The HTTP cache is a convenience, not storage, and is kept under this
    # size: expired responses are deleted, then the oldest, as it fills.
    cache_max_mb: int = field(
        default_factory=lambda: int(_env("FOCI_CACHE_MAX_MB", _default_cache_mb())))
    # Postgres for any deployment with more than one process or an ephemeral
    # filesystem. Empty means SQLite at `db_path`. Render injects DATABASE_URL;
    # on Google Cloud the Cloud SQL values are read instead — see _database_url.
    database_url: str = field(default_factory=_database_url)

    # --- job queue ---
    # Empty means run screens inline in a background thread — correct for the
    # CLI and local development, not for a deployment where a screen must
    # survive a web process restart.
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL"))
    job_timeout: int = field(default_factory=lambda: int(_env("FOCI_JOB_TIMEOUT", "3600")))
    # Set where a queue is not available — a single free-tier service, say —
    # to record that running screens in the web process is a decision rather
    # than an oversight. It changes nothing about how they run; it stops the
    # banner reporting a known trade-off as a fault. The risk does not go
    # away, so keep screens small enough to finish.
    inprocess_screens_ok: bool = field(
        default_factory=lambda: _flag("FOCI_INPROCESS_SCREENS", False))
    # While a screen runs inside the web process, keep one request open
    # against the service itself, so the host neither starves the screen of
    # processor time (Cloud Run) nor shuts the instance down as idle (Cloud
    # Run, Render's free plan). Unset: on wherever a managed host is detected.
    keep_awake: bool | None = field(default_factory=lambda: _switch("FOCI_KEEP_AWAKE"))

    # --- portfolio email alerts ---
    # Alerts go to the addresses typed into a portfolio as soon as a mail
    # service is connected below, and not before: with none, every alert is
    # still composed and shown in the app exactly as it would be sent.
    # ALERTS_SEND is the override — false keeps everything as a draft even
    # with a service connected; true insists on sending and reports the
    # missing service as a fault. Left unset, connecting a service is the
    # decision to send.
    alerts_send: bool | None = field(default_factory=lambda: _switch("ALERTS_SEND"))
    # A mail service reached over HTTPS. This is the route that works on a
    # free Render instance, which blocks the SMTP ports (25, 465, 587).
    brevo_api_key: str = field(default_factory=lambda: _env("BREVO_API_KEY"))
    resend_api_key: str = field(default_factory=lambda: _env("RESEND_API_KEY"))
    # brevo | resend | smtp. Only needed when more than one is configured.
    mail_provider: str = field(default_factory=lambda: _env("MAIL_PROVIDER"))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587") or 587))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    # starttls (587, 2587), ssl (465, 2465), or none (a local relay).
    smtp_security: str = field(default_factory=lambda: _env("SMTP_SECURITY", "starttls"))
    alerts_from: str = field(default_factory=lambda: _env("ALERTS_FROM"))
    # Where the links in an alert point. Without it, links use whatever
    # address the request that triggered the alerts came in on.
    public_url: str = field(default_factory=lambda: _env("FOCI_PUBLIC_URL"))

    # --- investor-relations email alerts ---
    # A directory of saved .eml files. The IR platforms that refuse an
    # identified crawler will happily mail the same releases to anyone who
    # subscribes, and a folder needs no credentials to read.
    alerts_dir: str = field(default_factory=lambda: _env("FOCI_ALERTS_DIR"))

    # --- recorded patent liens, without a USPTO key ---
    # The index built by `foci-screen import-patent-assignments` from the USPTO
    # Patent Assignment Dataset. Used only when USPTO_API_KEY is not set.
    patent_assignments_index: str = field(
        default_factory=lambda: _env("FOCI_PATENT_ASSIGNMENTS", "patent_assignments.db"))

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
    def managed_host(self) -> str:
        """The platform this is running on, if it is a managed one.

        Used only to decide whether a local-development default has been
        carried into somewhere it will hurt: SQLite on a platform with an
        ephemeral filesystem loses every snapshot on each deploy, and the
        snapshots *are* the baseline the change detection depends on.
        """
        for var, name in (("RENDER", "Render"), ("DYNO", "Heroku"),
                          ("FLY_APP_NAME", "Fly.io"), ("K_SERVICE", "Cloud Run"),
                          ("WEBSITE_INSTANCE_ID", "Azure App Service")):
            if os.environ.get(var):
                return name
        return ""

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
            "sam_exclusions": True,
            "webwatch": True,
            "ir_email_alerts": bool(self.alerts_dir
                                    and Path(self.alerts_dir).is_dir()),
            "samgov": bool(self.sam_api_key),
            "uspto": bool(self.uspto_api_key),
            "uspto_assignment_dataset": bool(self.patent_assignments_index
                                             and Path(self.patent_assignments_index).is_file()),
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
