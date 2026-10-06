"""Copy everything from one database to another, and check it arrived.

For moving the service between hosts: Render's Postgres to Cloud SQL, or a
local SQLite file to either. The usual tool for that is `pg_dump`, and it is
the usual place a migration stalls — it refuses to run against a server newer
than itself, and the machine doing the copy rarely has the matching version.
This needs only what the application already has: its own schema, and a
driver for each end.

How it works, in the order that matters:

  1. The **destination** is opened as a `Store`, which creates the current
     schema there. The **source** is opened read-only and is never written to;
     an older schema is fine, and columns it lacks keep their defaults.
  2. Every table the application defines is copied, a batch at a time, with
     each row's own id.
  3. On Postgres the id counters are moved past the copied rows. Skipping
     this is the classic silent failure: everything looks right until the
     first new row collides with a copied one.
  4. The row counts on both sides are compared, table by table.

It refuses to copy into a database that already holds rows, because ids from
two histories would be interleaved and rows silently skipped. Run it before
the new site is used. Running it twice is harmless.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from .store import SCHEMA, Store, _is_postgres, open_connection

log = logging.getLogger("foci.dbcopy")

TABLES = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
BATCH = 500


class CopyRefused(RuntimeError):
    """The copy was not started, and nothing was changed."""


def describe(dsn: str) -> str:
    """Where a DSN points, without its password."""
    if not _is_postgres(dsn):
        return f"SQLite file {dsn}"
    parts = urlsplit(dsn)
    host = parts.hostname or ""
    if not host and "host=" in parts.query:
        host = re.search(r"host=([^&]+)", parts.query).group(1)
    return f"Postgres database {parts.path.lstrip('/') or '?'} at {host or '?'}"


def _columns(cur, table: str) -> list[str] | None:
    """A table's column names in the source, or None if it has no such table."""
    try:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
    except Exception:       # noqa: BLE001 - absent in an older schema
        return None
    return [d[0] for d in cur.description]


def _count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
    row = cur.fetchone()
    return int((row["n"] if hasattr(row, "keys") else row[0]) or 0)


def copy_database(source_dsn: str, target_dsn: str, *, allow_nonempty: bool = False,
                  progress: Callable[[str], None] = lambda _: None) -> dict:
    """Copy every table. Returns {table: {"source": n, "target": n, "copied": n}}."""
    if source_dsn.strip() == target_dsn.strip():
        raise CopyRefused("The source and the destination are the same database.")

    source = open_connection(source_dsn)
    target = Store(target_dsn)          # creates the schema at the destination
    report: dict[str, dict] = {}
    try:
        src = source.cursor()
        with target._lock:
            dst = target._conn.cursor()

            if not allow_nonempty:
                occupied = {t: n for t in TABLES if (n := _count(dst, t))}
                target._conn.commit()
                if occupied:
                    listing = ", ".join(f"{t} ({n:,})" for t, n in occupied.items())
                    raise CopyRefused(
                        "The destination already holds rows: " + listing + ". Copying "
                        "into it would interleave two histories. Use an empty database, "
                        "or pass --allow-nonempty if these rows are from an earlier run "
                        "of this same copy.")

            for table in TABLES:
                columns = _columns(src, table)
                if columns is None:
                    source.rollback()
                    progress(f"  {table}: not in the source, skipped")
                    continue
                have = target._table_columns(dst, table)
                shared = [c for c in columns if c in have]
                marks = ", ".join("%s" if target.is_postgres else "?" for _ in shared)
                insert = (f"INSERT INTO {table} ({', '.join(shared)}) VALUES ({marks})"
                          f" ON CONFLICT DO NOTHING")

                # A server-side cursor on Postgres, so a table of stored documents
                # is streamed a batch at a time instead of loaded whole.
                reader = (source.cursor(name=f"copy_{table}") if _is_postgres(source_dsn)
                          else source.cursor())
                reader.execute(f"SELECT {', '.join(shared)} FROM {table}")
                copied = 0
                while True:
                    rows = reader.fetchmany(BATCH)
                    if not rows:
                        break
                    dst.executemany(insert, [tuple(r[c] for c in shared) for r in rows])
                    copied += len(rows)
                reader.close()
                target._conn.commit()
                source.rollback()       # end the read transaction; nothing was written

                if target.is_postgres and "id" in shared:
                    # Move the counter past the copied ids, or the next insert
                    # here would be handed one that is already taken.
                    dst.execute(
                        f"SELECT setval(pg_get_serial_sequence(%s, 'id'),"
                        f" COALESCE((SELECT MAX(id) FROM {table}), 1),"
                        f" (SELECT COUNT(*) FROM {table}) > 0)", (table,))
                    target._conn.commit()

                report[table] = {"source": _count(src, table), "copied": copied,
                                 "target": _count(dst, table)}
                source.rollback()
                target._conn.commit()
                progress(f"  {table}: {report[table]['target']:,} of "
                         f"{report[table]['source']:,} row(s)")
    finally:
        source.close()
        target.close()
    return report


def mismatches(report: dict) -> list[str]:
    """Tables where the destination ended up with fewer rows than the source."""
    return [f"{t}: source {r['source']:,}, destination {r['target']:,}"
            for t, r in report.items() if r["target"] < r["source"]]
