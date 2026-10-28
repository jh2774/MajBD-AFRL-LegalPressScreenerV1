"""Whether a deployment can do its job, asked of the running site itself.

Made for moving the service between hosts. A new site that answers its health
check and draws its pages can still be unable to do the work: its database
user may read but not write; the host may refuse it a route to the SEC; it
may not be able to reach its own address, which is how a long screen keeps
its processor time on Cloud Run. None of that shows until the first screen or
the first alert fails, days later and half-way through. So the site can be
asked, and it tries each thing for real.

It also counts what is stored, table by table. Open this on the old site and
the new one after a copy: the same numbers on both is the evidence that
everything arrived, and a different number names the table to look at.

Nothing here reveals a secret. Settings are listed by name, never by value.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .connectors.adv import SEARCH_API as IAPD_SEARCH
from .connectors.formd import TYPEAHEAD_API
from .connectors.fpds import FEED as FPDS_FEED
from .connectors.vehicles import DAILY_DIR
from .dbcopy import TABLES, describe

log = logging.getLogger("foci.syscheck")

# Every setting the application reads (`config.py`), so the ones in use on an
# old site can be carried to a new one. A test holds this to the config.
SETTINGS = (
    "FOCI_API_KEYS", "FOCI_PUBLIC", "FOCI_SIGNUP", "FOCI_USER_AGENT", "FOCI_PUBLIC_URL",
    "FOCI_CORS_ORIGINS", "NOTICES_SEND",
    "DATABASE_URL", "INSTANCE_CONNECTION_NAME", "CLOUD_SQL_CONNECTION_NAME",
    "INSTANCE_UNIX_SOCKET", "DB_USER", "DB_NAME", "DB_PASS", "DB_PASSWORD",
    "DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", "POSTGRES_URL", "FOCI_DB_SCHEMA",
    "FOCI_DB", "FOCI_CACHE", "FOCI_OUT", "FOCI_CACHE_MAX_MB", "FOCI_CACHE_TTL",
    "REDIS_URL", "FOCI_JOB_TIMEOUT", "FOCI_INPROCESS_SCREENS", "FOCI_KEEP_AWAKE",
    "FOCI_SCREENS_IN_REQUEST", "FOCI_SCREEN_SECONDS",
    "ALERTS_SEND", "ALERTS_PAUSED", "ALERTS_FROM", "ALERTS_REPLY_TO", "RESEND_API_KEY",
    "BREVO_API_KEY",
    "MAIL_PROVIDER", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
    "SMTP_SECURITY", "FOCI_EMAIL_REDIRECT_TO", "MAIL_WEBHOOK_SECRET", "RESEND_WEBHOOK_SECRET",
    "SAM_API_KEY", "USPTO_API_KEY", "TRADE_GOV_API_KEY",
    "FOCI_ALERTS_DIR", "FOCI_PATENT_ASSIGNMENTS", "FOCI_BROWSER", "FOCI_BROWSER_TIMEOUT",
    "FOCI_TIMEOUT", "FOCI_QPS", "FOCI_RETRIES",
    "GMAIL_ENABLED", "GMAIL_SEND", "GMAIL_CREDENTIALS", "GMAIL_TOKEN", "GMAIL_SENDER",
)
# Settings that say where this particular site lives. They are meant to differ
# between two hosts, so they are not part of what to carry across.
HOST_SPECIFIC = {
    "DATABASE_URL", "INSTANCE_CONNECTION_NAME", "CLOUD_SQL_CONNECTION_NAME",
    "INSTANCE_UNIX_SOCKET", "DB_USER", "DB_NAME", "DB_PASS", "DB_PASSWORD", "FOCI_DB",
    "FOCI_CACHE", "FOCI_OUT", "FOCI_CACHE_MAX_MB", "FOCI_PUBLIC_URL", "REDIS_URL",
    "ALERTS_PAUSED", "FOCI_INPROCESS_SCREENS", "FOCI_KEEP_AWAKE",
    "DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", "POSTGRES_URL",
    "FOCI_DB_SCHEMA", "FOCI_SCREENS_IN_REQUEST", "FOCI_SCREEN_SECONDS",
}

# Reading the largest Form ADV filings peaks at about 230 MB on top of the
# application itself.
MEMORY_COMFORTABLE_MB = 900
MEMORY_TOO_SMALL_MB = 400

PROBE_TIMEOUT = 10


def revision(environ) -> str:
    """Which deploy is answering: Cloud Run's revision name, or on Render and
    Vercel the commit it was built from — so the old site can be shown to run
    the same code as GitHub before its data is copied."""
    commit = environ.get("RENDER_GIT_COMMIT") or environ.get("VERCEL_GIT_COMMIT_SHA") or ""
    return environ.get("K_REVISION") or commit[:7]


def settings_in_use(environ) -> dict:
    """The names of the settings that are set here, split by whether a move
    should carry them."""
    present = [name for name in SETTINGS if (environ.get(name) or "").strip()]
    return {"carry": [n for n in present if n not in HOST_SPECIFIC],
            "this_host": [n for n in present if n in HOST_SPECIFIC]}


def memory_limit_mb(root: str = "/sys/fs/cgroup") -> int | None:
    """What the host allows this container, in MB, or None if it does not say.

    Linux only, which is where it matters: Cloud Run and Render both run the
    site in a container with a hard limit, and going over it ends the
    instance without a word in the application's own log.
    """
    for name in ("memory.max", "memory/memory.limit_in_bytes"):
        try:
            raw = (Path(root) / name).read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 50:        # "max", or a no-limit sentinel
            return int(raw) // (1024 * 1024)
    return None


def table_counts(store) -> dict[str, int]:
    """Rows in every table the application defines, across all tenants — the
    same tables, in the same order, that `copy-db` copies."""
    counts = {}
    for table in TABLES:
        try:
            counts[table] = int(store._one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        except Exception as exc:        # noqa: BLE001 - say which table, keep counting
            log.warning("could not count %s: %s", table, exc)
            counts[table] = -1
    return counts


def _check(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    """`ok` is True, False, or None for something worth knowing that is not a fault."""
    return {"name": name, "ok": ok, "detail": detail, "fix": fix if ok is False else ""}


def database_check(store, dsn: str, managed_host: str) -> dict:
    """The database really is written to and read back, not merely reachable."""
    from .dburl import schema_for

    where = describe(dsn)
    if store.is_postgres and schema_for(dsn):
        where += f", tables in schema {schema_for(dsn)}"
    marker = ("system-check", f"written {datetime.now(timezone.utc).isoformat()}")
    try:
        with store._tx() as c:
            c.execute("DELETE FROM alert_sent WHERE subscription_id=?", (marker[0],))
            c.execute("INSERT INTO alert_sent (subscription_id, item_id, sent_at)"
                      " VALUES (?,?,?)", (*marker, marker[1]))
        back = store._one("SELECT item_id FROM alert_sent WHERE subscription_id=?",
                          (marker[0],))
        with store._tx() as c:
            c.execute("DELETE FROM alert_sent WHERE subscription_id=?", (marker[0],))
    except Exception as exc:            # noqa: BLE001 - the reason is the answer
        return _check("Database", False, f"{where}. Writing to it failed: "
                                         f"{type(exc).__name__}: {exc}",
                      "Check the database settings and that its user may write.")
    if not back or back["item_id"] != marker[1]:
        return _check("Database", False, f"{where}. A row written to it did not read back.",
                      "Check the database settings.")
    if not store.is_postgres and managed_host:
        return _check("Database", False,
                      f"{where}, on {managed_host}. It works, and it will be thrown away "
                      f"the next time the site restarts.",
                      "Give the site a Postgres database: DATABASE_URL, or on Google "
                      "Cloud INSTANCE_CONNECTION_NAME, DB_USER, DB_NAME and DB_PASS.")
    return _check("Database", True, f"{where}. A row was written, read back and removed.")


def probe(http, name: str, url: str, *, params: dict | None = None, why: str = "") -> dict:
    """One real request to something the site depends on."""
    started = time.monotonic()
    try:
        resp = http.request("GET", url, params=params, use_cache=False,
                            timeout=PROBE_TIMEOUT, max_retries=1)
    except Exception as exc:            # noqa: BLE001
        resp = {"status": 0, "error": f"{type(exc).__name__}: {exc}"}
    took = time.monotonic() - started
    status = resp.get("status") or 0
    if status == 200:
        return _check(name, True, f"Answered in {took:.1f}s. {why}".strip())
    said = f"answered {status}" if status else f"did not answer ({resp.get('error') or '?'})"
    return _check(name, False, f"{url.split('?')[0]} {said}. {why}".strip(),
                  "If this is the SEC answering 403, it is refusing either this host's "
                  "address or the contact in FOCI_USER_AGENT, which must be a real name "
                  "and email address. Anything else is usually temporary: try again.")


def self_check(http, base_url: str, needed: bool = True) -> dict:
    """Whether the site can call its own public address.

    A screen, and an alert check, runs for minutes after the request that
    started it has been answered. On Cloud Run the site keeps its processor
    for that work only by holding a request open to itself. On Vercel it does
    not do that, and nothing depends on the answer.
    """
    name = "The site can reach its own address"
    if not needed:
        return _check(name, None, "Not needed here: long work does not keep this site "
                                  "awake by calling itself on this host.")
    url = f"{base_url.rstrip('/')}/health"
    try:
        resp = http.request("GET", url, use_cache=False, timeout=PROBE_TIMEOUT,
                            max_retries=1)
    except Exception as exc:            # noqa: BLE001
        resp = {"status": 0, "error": f"{type(exc).__name__}: {exc}"}
    if resp.get("status") == 200 and (resp.get("json") or {}).get("status") == "ok":
        return _check(name, True, f"{url} answered. Long screens and alert checks use "
                                  f"this to keep running after the page that started "
                                  f"them has loaded.")
    return _check(name, False,
                  f"{url} answered {resp.get('status') or 'nothing'}"
                  f"{' (' + resp['error'] + ')' if resp.get('error') else ''}.",
                  "Set FOCI_PUBLIC_URL to the address people open this site at. Until "
                  "this works, a long screen or alert check can be slowed to a crawl or "
                  "cut off when the host decides the site is idle.")


def memory_check(limit_mb: int | None) -> dict:
    name = "Memory"
    if limit_mb is None:
        return _check(name, None, "This host does not say how much memory the site may "
                                  "use.")
    if limit_mb < MEMORY_TOO_SMALL_MB:
        return _check(name, False, f"{limit_mb:,} MB. Reading a large Form ADV needs about "
                                   f"230 MB on top of the site itself.",
                      "Give the site at least 1 GB (on Cloud Run: --memory 1Gi).")
    if limit_mb < MEMORY_COMFORTABLE_MB:
        return _check(name, None, f"{limit_mb:,} MB. Enough, with little to spare when a "
                                  f"large Form ADV is being read. 1 GB is comfortable.")
    return _check(name, True, f"{limit_mb:,} MB.")


def screens_check(queue_backend: str, limit_seconds: int) -> dict:
    if queue_backend == "rq":
        return _check("Screens", True, "Run by a separate worker.")
    if queue_backend == "request":
        limit = (f"stops after {limit_seconds // 60} min {limit_seconds % 60:02d} s and keeps "
                 f"what it finished" if limit_seconds else "has no time limit set here")
        return _check("Screens", None,
                      f"Each runs inside the request the page sends to start it, so the page "
                      f"has to stay open until it ends. A screen {limit}. Up to about 15 "
                      f"contractors fit comfortably; screen more in several goes.")
    return _check("Screens", None,
                  "Run inside the site itself, which holds a request open to stay awake while "
                  "one is running. A screen still running when the site is redeployed is cut "
                  "off and marked interrupted.")


def watchlist_check(store, queue_backend: str) -> dict:
    """Whether anything screens the saved watchlists on a schedule.

    The nightly sweep (`scheduler.py`) hands each watchlist to a job queue and
    refuses to run without one, because a screen started in a process that
    exits a second later would never finish. A site with no queue — a single
    Render web service, or Cloud Run as set up in docs/GOOGLE_CLOUD.md — has
    nothing that runs them on its own. Said here, so a watchlist with its own
    screening policy is not assumed to be screened every night.
    """
    name = "Watchlists"
    try:
        active = len(store.all_active_watchlists())
    except Exception as exc:            # noqa: BLE001
        return _check(name, None, f"Could not be counted: {type(exc).__name__}.")
    if queue_backend == "rq":
        return _check(name, True, f"{active:,} active. Screened each night by the "
                                  f"scheduler's own cron job, through the job queue.")
    if not active:
        return _check(name, None, "None saved.")
    return _check(name, None,
                  f"{active:,} active, and nothing screens them on a schedule here: the "
                  f"nightly sweep needs a job queue (REDIS_URL) and a worker, which this "
                  f"site does not have. Each is screened only when someone runs it.")


def mail_check(status: dict, paused: bool) -> list[dict]:
    mode = status.get("mode")
    sending = _check("Email alerts", {"send": True, "redirect": True,
                                      "misconfigured": False}.get(mode),
                     status.get("explanation") or f"Mode: {mode}.",
                     "Finish connecting the mail service: RESEND_API_KEY and ALERTS_FROM.")
    checking = _check(
        "Alert checks", None if paused else True,
        "Paused: ALERTS_PAUSED is set, so nothing is checked and nothing is emailed. "
        "Right for a site being moved from or still being tried; remove the setting on "
        "the site people use." if paused else "Running: not paused.")
    return [sending, checking]


def run(cfg, store, http, base_url: str, mail_status: dict, *, environ,
        queue_backend: str = "thread", version: str = "", today=None) -> dict:
    """Everything, as one answer. Takes a few seconds: each check is real."""
    day = today or datetime.now(timezone.utc).date()
    daily = DAILY_DIR.format(year=day.year, quarter=(day.month - 1) // 3 + 1) + "index.json"
    checks = [
        database_check(store, cfg.dsn, cfg.managed_host),
        self_check(http, base_url, needed=getattr(cfg, "keep_awake_wanted", True)),
        memory_check(memory_limit_mb()),
        probe(http, "SEC EDGAR: daily list of filings", daily,
              why="New funds named after a contractor are found here."),
        probe(http, "SEC EDGAR: a company's filings",
              "https://data.sec.gov/submissions/CIK0000320193.json",
              why="Form D notices and the filings a screen reads."),
        probe(http, "SEC EDGAR: company name lookup", TYPEAHEAD_API,
              params={"keysTyped": "apple"},
              why="Picking a contractor's SEC record, and looking up its funds."),
        probe(http, "SEC adviser database (IAPD)", IAPD_SEARCH,
              params={"query": "vanguard", "hl": "true", "nrows": 1, "start": 0, "r": 25,
                      "sort": "score+desc", "wt": "json"},
              why="Investment firms and their Form ADV."),
        probe(http, "USAspending", "https://api.usaspending.gov/api/v2/references/"
                                   "toptier_agencies/",
              why="The awards a screen starts from."),
        probe(http, "FPDS", FPDS_FEED,
              params={"FEEDNAME": "PUBLIC", "q": 'PIID:"FOCI-SYSTEM-CHECK"'},
              why="Each award's contracting officer, and the contractor's SAM "
                  "registration as FPDS records it."),
        *mail_check(mail_status, cfg.alerts_paused),
        screens_check(queue_backend, getattr(cfg, "screen_seconds", 0)),
        watchlist_check(store, queue_backend),
    ]
    counts = table_counts(store)
    return {
        "host": cfg.managed_host or "self-hosted", "version": version,
        "revision": revision(environ),
        "database": "postgres" if cfg.dsn.startswith("postgres") else "sqlite",
        "ok": not any(c["ok"] is False for c in checks),
        "checks": checks,
        "tables": counts,
        "rows": sum(n for n in counts.values() if n > 0),
        "settings": settings_in_use(environ),
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
