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
