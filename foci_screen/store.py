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

from .models import Change, Document, Finding, signal_key

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
    is_subaward   INTEGER DEFAULT 0,
    prime_award_id TEXT,
    prime_recipient_name TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE (tenant_id, contract_key)
);
-- What changed about an award between one screen and the next. The contracts
-- row holds the present state; this holds the transition, which is the part a
-- reviewer needs to see. An award moving to a different UEI is a novation, and
-- a contractor's incorporation country moving is the most direct structural
-- FOCI signal in the record — neither is visible from the current value alone.
CREATE TABLE IF NOT EXISTS contract_changes (
    id            {pk},
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    contract_key  TEXT NOT NULL,
    entity_key    TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    field         TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    observed_at   TEXT NOT NULL
);
-- One reviewer's verdict on one signal. The only labelled data the tool ever
-- gets: without it, "is this rule earning its place?" is unanswerable and
-- weight tuning is guesswork.
CREATE TABLE IF NOT EXISTS dispositions (
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    signal_id   TEXT NOT NULL,
    entity_key  TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    category    TEXT,
    severity    TEXT,
    verdict     TEXT NOT NULL,
    note        TEXT,
    decided_by  TEXT,
    decided_at  TEXT NOT NULL,
    run_id      TEXT,
    PRIMARY KEY (tenant_id, signal_id, entity_key)
);
-- Which SEC registrant (and eventually which CAGE, which CRD) a contractor is.
-- Name matching across USAspending, SEC, IAPD and USPTO is the weakest link in
-- the chain, and a wrong CIK does not fail loudly: it attributes another
-- company's filings with full confidence. Resolutions are therefore remembered
-- rather than recomputed each run, and a human verdict outranks the matcher
-- permanently in both directions.
CREATE TABLE IF NOT EXISTS entity_links (
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    entity_key    TEXT NOT NULL,
    entity_name   TEXT,
    uei           TEXT,
    cik           TEXT,
    matched_title TEXT,
    confidence    REAL,
    status        TEXT NOT NULL DEFAULT 'auto',   -- auto | confirmed | rejected
    source        TEXT,
    note          TEXT,
    decided_by    TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entity_key)
);
-- Per-tenant rule configuration. The weights in risk/engine.py are defaults,
-- not law; an analyst who can see from the precision report that a rule does
-- not earn its place should be able to retire it without a deploy.
CREATE TABLE IF NOT EXISTS rule_settings (
    tenant_id  TEXT NOT NULL DEFAULT 'default',
    rule_id    TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    weight     REAL NOT NULL DEFAULT 1.0,
    note       TEXT,
    decided_by TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, rule_id)
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
CREATE INDEX IF NOT EXISTS idx_disp_rule ON dispositions(tenant_id, rule_id);
CREATE INDEX IF NOT EXISTS idx_disp_entity ON dispositions(tenant_id, entity_key);
CREATE INDEX IF NOT EXISTS idx_cchanges ON contract_changes(tenant_id, entity_key);
CREATE INDEX IF NOT EXISTS idx_cchanges_run ON contract_changes(tenant_id, run_id);
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
    ("contracts", "is_subaward", "INTEGER DEFAULT 0"),
    ("contracts", "prime_award_id", "TEXT"),
    ("contracts", "prime_recipient_name", "TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _is_postgres(dsn: str) -> bool:
    return dsn.startswith("postgres://") or dsn.startswith("postgresql://")


DRIVER_MISSING = (
    "DATABASE_URL points at Postgres but the psycopg driver is not installed. "
    "Install the extra — pip install \".[api,queue,postgres]\" — and redeploy. "
    "Without it the service starts and then fails every request that touches "
    "the database."
)


def postgres_driver_available() -> bool:
    """Whether a Postgres DSN can actually be opened.

    Worth asking before anything tries: a build command that installed the
    package without the `postgres` extra produces a service that boots, serves
    the web UI, and then 500s on the first query.
    """
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False
    return True


# SQLite has no default LIKE escape character, so it has to be named in the
# statement. Postgres defaults to backslash and accepts the clause as written.
ESC = "ESCAPE '\\'"


# Columns an award carries that a partial upstream response may simply omit.
# Writing the blank over a known value loses data no later screen recovers, so
# the stored value stands until something replaces it with a real one. The cost
# is that a value genuinely cleared upstream persists here; for a screen, a
# stale country of incorporation is a far better failure than a missing one.
_KEEP_IF_BLANK = (
    "piid", "award_id", "entity_name", "agency", "sub_agency", "start_date",
    "end_date", "naics_description", "psc_description", "description",
    "solicitation_id", "recipient_uei", "recipient_country",
    "country_of_incorporation", "foreign_funding", "ko_name", "ko_email",
    "ko_source", "ko_confidence", "source_url", "prime_award_id",
    "prime_recipient_name",
)
# Written unconditionally. `entity_key` is always supplied by the caller;
# `foreign_owned` and `is_subaward` are 0/1 with no "unknown" to protect, so a
# correction has to be able to clear them.
_ALWAYS_WRITE = ("run_id", "entity_key", "foreign_owned", "is_subaward",
                 "ip_clauses", "updated_at")


def _contract_update_clause() -> str:
    """The DO UPDATE SET list: every column except the row's identity."""
    parts = [f"{col}=excluded.{col}" for col in _ALWAYS_WRITE]
    parts += [f"{col}=COALESCE(NULLIF(excluded.{col}, ''), contracts.{col})"
              for col in _KEEP_IF_BLANK]
    # Zero here means the detail call did not return a figure; a real award of
    # nothing is rare, and silently zeroing one corrupts every total above it.
    parts.append("amount=COALESCE(NULLIF(excluded.amount, 0), contracts.amount)")
    return ", ".join(parts)


CONTRACT_UPDATE_CLAUSE = _contract_update_clause()


def _like(q: str) -> str:
    r"""A LIKE pattern that matches `q` literally.

    `%` and `_` are wildcards, so an unescaped search term quietly means
    something other than what was typed. The backslash is escaped first, or it
    would escape the escapes. Pair every pattern with `ESC`.
    """
    escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:      # say which extra, not "no module named"
                raise RuntimeError(DRIVER_MISSING) from exc

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

    def reap_interrupted_runs(self, note: str) -> int:
        """Close out runs whose process is gone, and say why.

        A screen marked `running` is only running while something is running
        it. When the process dies mid-screen — a free instance spinning down
        after its idle window, a deploy, an OOM — nothing ever writes the
        closing row, and the run sits at `running` for good. A client polling
        it waits on a screen that ended hours ago, which is a worse answer than
        "this was interrupted, start another".

        Only safe to call where no *other* process could own the run. The
        caller decides that; see the startup sweep in the API, which runs it
        only when screening happens in-process.
        """
        stuck = self._query(
            "SELECT run_id FROM runs WHERE tenant_id=? AND status='running'",
            (self.tenant_id,))
        if not stuck:
            return 0
        with self._tx() as c:
            c.execute(
                "UPDATE runs SET status='interrupted', finished_at=?, error=?"
                " WHERE tenant_id=? AND status='running'",
                (_now(), note, self.tenant_id))
        return len(stuck)

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

    # Tables that carry a run_id and belong to the tenant that made the run.
    # `snapshots` is deliberately absent: a document's hash is a fact about the
    # world, shared between tenants, and deleting it would take the change
    # baseline away from everyone else watching the same company.
    _RUN_OWNED = ("notices", "contract_changes", "contracts", "findings")

    def run_footprint(self, run_id: str) -> dict:
        """What deleting this run would remove, counted before anything goes."""
        out: dict[str, int] = {}
        for table in self._RUN_OWNED:
            row = self._one(
                f"SELECT COUNT(*) AS n FROM {table} WHERE tenant_id=? AND run_id=?",
                (self.tenant_id, run_id))
            out[table] = int((row or {}).get("n") or 0)
        return out

    def delete_run(self, run_id: str) -> dict:
        """Remove a run and everything it recorded. Not reversible.

        For undoing a screen somebody did not want — one run against the wrong
        agency puts contractors on the dashboard that nobody chose to watch,
        and there was no way to take them off.
        """
        removed = self.run_footprint(run_id)
        with self._tx() as c:
            for table in self._RUN_OWNED:
                c.execute(f"DELETE FROM {table} WHERE tenant_id=? AND run_id=?",
                          (self.tenant_id, run_id))
            c.execute("DELETE FROM runs WHERE tenant_id=? AND run_id=?",
                      (self.tenant_id, run_id))
        return removed

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

    # ---------------------------------------------------------- dispositions
    VERDICTS = ("true_positive", "false_positive", "unclear")

    def record_disposition(self, *, signal_id: str, entity_key: str, rule_id: str,
                           verdict: str, category: str = "", severity: str = "",
                           note: str = "", decided_by: str = "", run_id: str = "") -> None:
        """Record what a reviewer concluded about one signal.

        Re-deciding replaces the previous verdict: a reviewer who looks again
        and changes their mind is producing better data, not a second data
        point. Notice decisions are final because they are actions; these are
        judgements, and judgements can be revised.
        """
        if verdict not in self.VERDICTS:
            raise ValueError(f"verdict must be one of {self.VERDICTS}, got {verdict!r}")
        with self._tx() as c:
            c.execute(
                "INSERT INTO dispositions (tenant_id, signal_id, entity_key, rule_id,"
                " category, severity, verdict, note, decided_by, decided_at, run_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, signal_id, entity_key) DO UPDATE SET"
                " verdict=excluded.verdict, note=excluded.note,"
                " decided_by=excluded.decided_by, decided_at=excluded.decided_at",
                (self.tenant_id, signal_id, entity_key.upper(), rule_id, category,
                 severity, verdict, note, decided_by, _now(), run_id))

    def dispositions_for_entity(self, entity_key: str) -> dict[str, dict]:
        rows = self._query("SELECT * FROM dispositions WHERE tenant_id=? AND entity_key=?",
                           (self.tenant_id, entity_key.upper()))
        return {r["signal_id"]: r for r in rows}

    def rule_precision(self) -> list[dict]:
        """Per-rule precision, for deciding which rules earn their place.

        `unclear` is counted but kept out of the precision denominator: a
        reviewer who could not tell has not said the rule was wrong, and
        folding that into a score would quietly punish rules that raise
        genuinely hard questions.
        """
        fired = {r["rule_id"]: r["n"] for r in self._query(
            "SELECT rule_id, COUNT(*) AS n FROM dispositions WHERE tenant_id=?"
            " GROUP BY rule_id", (self.tenant_id,))}
        rows = self._query(
            "SELECT rule_id, verdict, COUNT(*) AS n, MAX(category) AS category"
            " FROM dispositions WHERE tenant_id=? GROUP BY rule_id, verdict",
            (self.tenant_id,))

        by_rule: dict[str, dict] = {}
        for r in rows:
            entry = by_rule.setdefault(r["rule_id"], {
                "rule_id": r["rule_id"], "category": r["category"],
                "true_positive": 0, "false_positive": 0, "unclear": 0})
            entry[r["verdict"]] = r["n"]

        out = []
        for entry in by_rule.values():
            judged = entry["true_positive"] + entry["false_positive"]
            entry["reviewed"] = fired.get(entry["rule_id"], 0)
            entry["precision"] = (round(entry["true_positive"] / judged, 3)
                                  if judged else None)
            out.append(entry)
        # Worst first: the point of the page is finding rules to retire.
        return sorted(out, key=lambda e: (e["precision"] if e["precision"] is not None
                                          else 2, -e["reviewed"]))

    # ------------------------------------------------------------ rule settings
    def rule_settings(self) -> dict[str, dict]:
        """Tenant overrides, keyed by rule id. Absent means engine defaults."""
        rows = self._query("SELECT * FROM rule_settings WHERE tenant_id=?",
                           (self.tenant_id,))
        return {r["rule_id"]: {"enabled": bool(r["enabled"]),
                               "weight": float(r["weight"]),
                               "note": r["note"] or "",
                               "decided_by": r["decided_by"] or "",
                               "updated_at": r["updated_at"]} for r in rows}

    def set_rule_setting(self, rule_id: str, *, enabled: bool = True,
                         weight: float = 1.0, note: str = "",
                         decided_by: str = "") -> dict:
        weight = max(0.0, min(5.0, float(weight)))
        with self._tx() as c:
            c.execute(
                "INSERT INTO rule_settings (tenant_id, rule_id, enabled, weight, note,"
                " decided_by, updated_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, rule_id) DO UPDATE SET"
                " enabled=excluded.enabled, weight=excluded.weight, note=excluded.note,"
                " decided_by=excluded.decided_by, updated_at=excluded.updated_at",
                (self.tenant_id, rule_id, 1 if enabled else 0, weight, note,
                 decided_by, _now()))
        return self.rule_settings().get(rule_id, {})

    def clear_rule_setting(self, rule_id: str) -> None:
        """Back to the engine default, which is not the same as weight 1.0 —
        it removes the row, so a later change to the default takes effect."""
        with self._tx() as c:
            c.execute("DELETE FROM rule_settings WHERE tenant_id=? AND rule_id=?",
                      (self.tenant_id, rule_id))

    def rules_seen(self, limit_findings: int = 300) -> dict[str, str]:
        """Rule ids that have actually fired here, mapped to their category.

        The engine's rule ids are string literals inside the rule functions and
        cannot be enumerated reliably, so the catalogue is built from what has
        been observed rather than from a hand-maintained list that would drift.
        """
        rows = self._query("SELECT payload FROM findings WHERE tenant_id=?"
                           " ORDER BY id DESC LIMIT ?", (self.tenant_id, limit_findings))
        seen: dict[str, str] = {}
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (ValueError, TypeError):
                continue
            for s in payload.get("signals", []):
                rule_id = s.get("rule_id")
                if rule_id:
                    seen.setdefault(rule_id, s.get("category") or "")
        return seen

    # --------------------------------------------------------- entity identity
    LINK_STATUSES = ("auto", "confirmed", "rejected")

    def get_entity_link(self, entity_key: str) -> dict | None:
        return self._one("SELECT * FROM entity_links WHERE tenant_id=? AND entity_key=?",
                         (self.tenant_id, entity_key.upper()))

    def record_auto_link(self, entity_key: str, *, entity_name: str = "", uei: str = "",
                         cik: str = "", matched_title: str = "",
                         confidence: float = 0.0) -> None:
        """Remember what the name matcher concluded.

        Never overwrites a human decision. A reviewer who has confirmed or
        rejected a mapping has supplied better information than a similarity
        score, and a later run finding a different name match must not quietly
        undo that.
        """
        existing = self.get_entity_link(entity_key)
        if existing and existing["status"] in ("confirmed", "rejected"):
            return
        with self._tx() as c:
            c.execute(
                "INSERT INTO entity_links (tenant_id, entity_key, entity_name, uei,"
                " cik, matched_title, confidence, status, source, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, entity_key) DO UPDATE SET"
                " entity_name=excluded.entity_name, uei=excluded.uei,"
                " cik=excluded.cik, matched_title=excluded.matched_title,"
                " confidence=excluded.confidence, updated_at=excluded.updated_at",
                (self.tenant_id, entity_key.upper(), entity_name, uei, cik,
                 matched_title, float(confidence or 0.0), "auto",
                 "edgar_name_match", _now()))

    def set_entity_link(self, entity_key: str, *, status: str, cik: str | None = None,
                        note: str = "", decided_by: str = "",
                        matched_title: str = "") -> dict:
        """Record a human decision about who this contractor is.

        `rejected` is not merely "unconfirmed": it means no SEC registrant has
        been identified, and the screen must stop attributing filings to this
        contractor until someone says otherwise.
        """
        if status not in self.LINK_STATUSES:
            raise ValueError(f"status must be one of {self.LINK_STATUSES}, got {status!r}")

        existing = self.get_entity_link(entity_key) or {}
        new_cik = "" if status == "rejected" else (
            cik if cik is not None else existing.get("cik") or "")
        with self._tx() as c:
            c.execute(
                "INSERT INTO entity_links (tenant_id, entity_key, entity_name, uei,"
                " cik, matched_title, confidence, status, source, note, decided_by,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (tenant_id, entity_key) DO UPDATE SET"
                " cik=excluded.cik, status=excluded.status, note=excluded.note,"
                " matched_title=excluded.matched_title,"
                " decided_by=excluded.decided_by, source=excluded.source,"
                " updated_at=excluded.updated_at",
                (self.tenant_id, entity_key.upper(), existing.get("entity_name", ""),
                 existing.get("uei", ""), new_cik,
                 matched_title or existing.get("matched_title", ""),
                 existing.get("confidence") or 0.0, status, "manual", note,
                 decided_by, _now()))
        return self.get_entity_link(entity_key)

    def entity_links(self, status: str = "", limit: int = 200) -> list[dict]:
        """Resolutions, least confident first — that is the review queue."""
        if status:
            return self._query(
                "SELECT * FROM entity_links WHERE tenant_id=? AND status=?"
                " ORDER BY confidence ASC, entity_name LIMIT ?",
                (self.tenant_id, status, limit))
        return self._query(
            "SELECT * FROM entity_links WHERE tenant_id=?"
            " ORDER BY status, confidence ASC LIMIT ?", (self.tenant_id, limit))

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
    # Fields whose movement between screens is worth recording. Amounts and
    # dates move on ordinary modifications and would drown the interesting
    # ones; these describe *who* holds the award and *where they are*.
    WATCHED_FIELDS = (
        ("entity_key", "contractor"),
        ("entity_name", "contractor name"),
        ("recipient_uei", "UEI"),
        ("country_of_incorporation", "country of incorporation"),
        ("recipient_country", "registered address country"),
        ("foreign_owned", "FPDS foreign-owned flag"),
        ("ko_email", "contracting officer"),
    )

    def save_contract(self, contract, run_id: str, entity_key: str) -> list[dict]:
        """Index one award so it can be searched, and report what moved.

        The finding payload already carries its contracts, but as JSON — you
        cannot search inside it, which is the whole reason this table exists.

        Returns the changes observed against the stored row, oldest state to
        newest. A first sighting returns nothing: there is no transition, and
        calling a baseline a change is the mistake the document side of this
        tool already takes care to avoid.
        """
        key = (contract.piid or contract.award_id or "").strip()
        if not key:
            return []
        o = contract.officer
        changes = self._contract_changes(key, contract, entity_key, run_id)
        with self._tx() as c:
            c.execute(
                "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, award_id,"
                " entity_key, entity_name, agency, sub_agency, amount, start_date,"
                " end_date, naics_description, psc_description, description,"
                " solicitation_id, recipient_uei, recipient_country,"
                " country_of_incorporation, foreign_owned, foreign_funding, ko_name,"
                " ko_email, ko_source, ko_confidence, ip_clauses, source_url,"
                " is_subaward, prime_award_id, prime_recipient_name, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                # Every column except the identity of the row — see
                # CONTRACT_UPDATE_CLAUSE. The list used to carry entity_name
                # but not entity_key, uei or country_of_incorporation, so an
                # award that changed hands kept the old contractor's key under
                # the new contractor's name, and an entity whose incorporation
                # country moved to a covered nation went on reading as it did
                # the first time it was seen. Those are the fields a FOCI
                # screen exists to watch.
                " ON CONFLICT (tenant_id, contract_key) DO UPDATE SET "
                + CONTRACT_UPDATE_CLAUSE,
                (self.tenant_id, key, run_id, contract.piid, contract.award_id,
                 entity_key, contract.recipient_name, contract.awarding_agency,
                 contract.awarding_sub_agency, float(contract.award_amount or 0),
                 contract.start_date, contract.end_date, contract.naics_description,
                 contract.psc_description, contract.description,
                 contract.solicitation_id, contract.recipient_uei,
                 contract.recipient_country, contract.country_of_incorporation,
                 1 if contract.foreign_owned_and_located else 0,
                 contract.foreign_funding, o.name, o.email, o.source, o.confidence,
                 json.dumps(contract.ip_clause_hits or []), contract.source_url,
                 1 if contract.is_subaward else 0, contract.prime_award_id,
                 contract.prime_recipient_name, _now()))
        return changes

    def _contract_changes(self, key: str, contract, entity_key: str,
                          run_id: str) -> list[dict]:
        """Diff the incoming award against the stored one, and record it."""
        existing = self._one(
            "SELECT * FROM contracts WHERE tenant_id=? AND contract_key=?",
            (self.tenant_id, key))
        if not existing:
            return []

        incoming = {
            "entity_key": entity_key,
            "entity_name": contract.recipient_name,
            "recipient_uei": contract.recipient_uei,
            "country_of_incorporation": contract.country_of_incorporation,
            "recipient_country": contract.recipient_country,
            "foreign_owned": 1 if contract.foreign_owned_and_located else 0,
            "ko_email": contract.officer.email,
        }

        changes: list[dict] = []
        now = _now()
        for field_name, label in self.WATCHED_FIELDS:
            old, new = existing.get(field_name), incoming.get(field_name)
            old_s = "" if old is None else str(old).strip()
            new_s = "" if new is None else str(new).strip()
            # A field the new record simply does not carry is missing data, not
            # a change. Reporting "country of incorporation: USA → (blank)" as
            # a movement would put a connector outage in front of a reviewer as
            # though the contractor had done something.
            if not new_s or old_s == new_s:
                continue
            changes.append({"contract_key": key, "field": field_name,
                            "label": label, "old": old_s, "new": new_s,
                            "piid": contract.piid or contract.award_id,
                            "observed_at": now})

        if changes:
            with self._tx() as c:
                for ch in changes:
                    c.execute(
                        "INSERT INTO contract_changes (tenant_id, contract_key,"
                        " entity_key, run_id, field, old_value, new_value, observed_at)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (self.tenant_id, key, entity_key, run_id, ch["field"],
                         ch["old"], ch["new"], now))
        return changes

    def contract_changes(self, entity_key: str = "", run_id: str = "",
                         limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM contract_changes WHERE tenant_id=?"
        params: list = [self.tenant_id]
        if entity_key:
            sql += " AND entity_key=?"
            params.append(entity_key)
        if run_id:
            sql += " AND run_id=?"
            params.append(run_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._query(sql, tuple(params))
        labels = dict(self.WATCHED_FIELDS)
        for r in rows:
            r["label"] = labels.get(r["field"], r["field"])
        return rows

    def get_contract(self, contract_key: str) -> dict | None:
        row = self._one("SELECT * FROM contracts WHERE tenant_id=? AND contract_key=?",
                        (self.tenant_id, contract_key))
        return _decode_contract(row) if row else None

    FILTERABLE = {"entity_key", "ko_email", "agency", "sub_agency", "run_id"}

    def contracts_where(self, column: str, value: str, limit: int = 200) -> list[dict]:
        """Contracts filtered on one indexed column. `column` is never user input."""
        if column not in self.FILTERABLE:
            raise ValueError(f"not a filterable column: {column}")
        rows = self._query(
            f"SELECT * FROM contracts WHERE tenant_id=? AND {column}=?"
            " ORDER BY amount DESC LIMIT ?", (self.tenant_id, value, limit))
        return [_decode_contract(r) for r in rows]

    def contract_totals(self, column: str, value: str) -> dict:
        """COUNT and SUM over *every* matching row, not the page that was read.

        `contracts_where` returns the top 200 by amount. Summing that list and
        calling the result an entity's obligated total is wrong the moment a
        contractor has more than 200 awards, and wrong quietly — a dollar
        figure that looks authoritative and understates by however much the
        tail holds. The totals come from the database; the list stays capped.
        """
        if column not in self.FILTERABLE:
            raise ValueError(f"not a filterable column: {column}")
        row = self._one(
            f"SELECT COUNT(*) AS contract_count, COALESCE(SUM(amount), 0) AS obligated"
            f" FROM contracts WHERE tenant_id=? AND {column}=?",
            (self.tenant_id, value))
        return {"contract_count": int(row["contract_count"] or 0),
                "obligated": float(row["obligated"] or 0.0)}

    # ----------------------------------------------------------------- search
    # LOWER(col) LIKE ? rather than ILIKE: Postgres LIKE is case-sensitive and
    # SQLite has no ILIKE, so this is the one form that means the same thing in
    # both. At this row count the lost index is not worth two code paths.
    #
    # Every pattern goes through `_like`, and every clause carries {ESC}. The
    # query is parameterised, so a wildcard in the search box was never an
    # injection — it was a wrong answer: "SPACE_SYSTEMS" matched
    # "SPACEXSYSTEMS", and a search for "%" returned the whole table as though
    # everything in it matched.
    def search_contracts(self, q: str, limit: int = 25) -> list[dict]:
        like = _like(q)
        rows = self._query(
            "SELECT * FROM contracts WHERE tenant_id=? AND ("
            f" LOWER(piid) LIKE ? {ESC} OR LOWER(award_id) LIKE ? {ESC}"
            f" OR LOWER(solicitation_id) LIKE ? {ESC} OR LOWER(description) LIKE ? {ESC}"
            f" OR LOWER(psc_description) LIKE ? {ESC}"
            f" OR LOWER(naics_description) LIKE ? {ESC})"
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
            sql += f" AND (LOWER(entity_name) LIKE ? {ESC} OR LOWER(entity_key) LIKE ? {ESC})"
            like = _like(q)
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
            sql += f" AND (LOWER(ko_email) LIKE ? {ESC} OR LOWER(ko_name) LIKE ? {ESC})"
            like = _like(q)
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
            sql += f" AND (LOWER(agency) LIKE ? {ESC} OR LOWER(sub_agency) LIKE ? {ESC})"
            like = _like(q)
            params += [like, like]
        sql += " GROUP BY agency, sub_agency ORDER BY SUM(amount) DESC LIMIT ?"
        params.append(limit)
        return self._query(sql, tuple(params))

    # An agency page is reached by either name, so both have to match. Doing
    # this in Python over `search_officers("", limit=200)` had two faults: it
    # saw only the 200 best-funded officers in the tenant, and it compared
    # against `MAX(agency)`, which for an officer working across two agencies
    # is whichever sorted higher. A sub-agency matched nothing at all.
    def officers_for_agency(self, name: str, limit: int = 200) -> list[dict]:
        return self._query(
            "SELECT ko_email, MAX(ko_name) AS ko_name,"
            " MAX(ko_confidence) AS ko_confidence, MAX(ko_source) AS ko_source,"
            " COUNT(*) AS contract_count, SUM(amount) AS obligated,"
            " COUNT(DISTINCT entity_key) AS entity_count,"
            " MAX(agency) AS agency"
            " FROM contracts WHERE tenant_id=? AND (agency=? OR sub_agency=?)"
            " AND ko_email IS NOT NULL AND ko_email != ''"
            " GROUP BY ko_email ORDER BY SUM(amount) DESC LIMIT ?",
            (self.tenant_id, name, name, limit))

    def entities_for_agency(self, name: str, limit: int = 200) -> list[dict]:
        """Contractors under an agency, aggregated in SQL over every award."""
        rows = self._query(
            "SELECT entity_key, MAX(entity_name) AS entity_name,"
            " COUNT(*) AS contract_count, SUM(amount) AS obligated"
            " FROM contracts WHERE tenant_id=? AND (agency=? OR sub_agency=?)"
            " GROUP BY entity_key ORDER BY SUM(amount) DESC LIMIT ?",
            (self.tenant_id, name, name, limit))
        for r in rows:
            latest = self._one(
                "SELECT severity FROM findings WHERE tenant_id=? AND entity_key=?"
                " ORDER BY id DESC LIMIT 1", (self.tenant_id, r["entity_key"]))
            r["severity"] = latest["severity"] if latest else None
        return rows

    def agency_totals(self, name: str) -> dict:
        row = self._one(
            "SELECT COUNT(*) AS contract_count, COALESCE(SUM(amount), 0) AS obligated,"
            " COUNT(DISTINCT entity_key) AS entity_count"
            " FROM contracts WHERE tenant_id=? AND (agency=? OR sub_agency=?)",
            (self.tenant_id, name, name))
        return {"contract_count": int(row["contract_count"] or 0),
                "obligated": float(row["obligated"] or 0.0),
                "entity_count": int(row["entity_count"] or 0)}

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
    row["is_subaward"] = bool(row.get("is_subaward"))
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
    """Merge the stored finding payload with its row id.

    Signals stored before `signal_id` existed get one computed here, from the
    same rule-and-evidence hash, so a verdict can be attached to a finding
    recorded by an older build.
    """
    try:
        payload = json.loads(row["payload"])
    except Exception:
        payload = {}
    payload["finding_id"] = row.get("id")
    for signal in payload.get("signals", []):
        if not signal.get("signal_id"):
            signal["signal_id"] = signal_key(signal.get("rule_id", ""),
                                             signal.get("evidence", ""))
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
