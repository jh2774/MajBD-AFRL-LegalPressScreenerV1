"""The Postgres-only behaviour, against a real Postgres.

Skipped unless FOCI_TEST_DATABASE_URL points at a server this may write to
and drop tables in. CI provides one (see the `postgres` job); nothing here
has run on a machine without it, which is the reason it exists — the copy's
id counters, a connection killed by the server, and a lock shared between two
connections cannot be shown with stand-ins.

    FOCI_TEST_DATABASE_URL=postgresql://foci:foci@localhost:5432/foci \\
        pytest tests/test_postgres_live.py
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import pytest

from foci_screen import dbcopy
from foci_screen.store import Store

DSN = os.environ.get("FOCI_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="FOCI_TEST_DATABASE_URL is not set")

SECOND = "foci_copy_target"


def _admin(dsn=None):
    import psycopg

    return psycopg.connect(dsn or DSN, autocommit=True)


def _wipe(dsn):
    with _admin(dsn) as conn:
        for table in dbcopy.TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")


def _other_database() -> str:
    parts = urlsplit(DSN)
    return urlunsplit(parts._replace(path=f"/{SECOND}"))


@pytest.fixture()
def pg():
    _wipe(DSN)
    yield DSN
    _wipe(DSN)


@pytest.fixture()
def second_pg():
    """A second, empty database on the same server: the other end of a move."""
    with _admin() as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {SECOND}")
        conn.execute(f"CREATE DATABASE {SECOND}")
    yield _other_database()
    with _admin() as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {SECOND} WITH (FORCE)")


def seed(store: Store, awards: int = 3) -> None:
    store.start_run("DoD", {}, run_id="r1")
    with store._tx() as c:
        for i in range(awards):
            c.execute(
                "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, entity_key,"
                " entity_name, amount, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (store.tenant_id, f"P{i}", "r1", f"P{i}", "UEI777",
                 "NORTHWIND ROBOTICS INC", 1000.0, "2026-01-01T00:00:00Z"))
    store.save_adv_snapshot("999001", {"crd": "999001", "name": "HARBORLIGHT"}, "03/31/2026")
    store.set_entity_link("UEI777", status="confirmed", cik="0004400123", decided_by="t")


def add_award(store: Store, piid: str) -> int:
    with store._tx() as c:
        c.execute(
            "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, entity_key,"
            " entity_name, amount, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (store.tenant_id, piid, "r1", piid, "UEI777", "NORTHWIND ROBOTICS INC",
             5.0, "2026-02-01T00:00:00Z"))
    return store._one("SELECT id FROM contracts WHERE contract_key=?", (piid,))["id"]


def test_the_schema_is_created_and_survives_being_applied_twice(pg):
    Store(pg, tenant_id="acme").close()
    store = Store(pg, tenant_id="acme")
    seed(store)
    assert store.contract_totals("entity_key", "UEI777")["contract_count"] == 3
    store.close()


def test_sqlite_to_postgres_and_the_next_new_row_does_not_collide(pg, tmp_path):
    """The failure the id counters exist to prevent: copied rows hold ids 1-3,
    and a database that still thinks the next id is 1 refuses the first award
    screened after the move."""
    old = Store(str(tmp_path / "old.db"), tenant_id="acme")
    seed(old)
    old.close()

    report = dbcopy.copy_database(str(tmp_path / "old.db"), pg)
    assert dbcopy.mismatches(report) == [] and report["contracts"]["target"] == 3

    moved = Store(pg, tenant_id="acme")
    assert add_award(moved, "AFTER-THE-MOVE") == 4
    assert moved.confirmed_ciks(["UEI777"]) == {"4400123": "UEI777"}
    moved.close()


def test_postgres_to_postgres_is_the_move_itself(pg, second_pg):
    """Render's Postgres to Cloud SQL, in miniature: read with a server-side
    cursor, written in batches, counted on both sides."""
    old = Store(pg, tenant_id="acme")
    seed(old, awards=dbcopy.BATCH + 7)          # more than one batch
    old.close()

    report = dbcopy.copy_database(pg, second_pg)
    assert dbcopy.mismatches(report) == []
    assert report["contracts"] == {"source": dbcopy.BATCH + 7, "copied": dbcopy.BATCH + 7,
                                   "target": dbcopy.BATCH + 7}

    moved = Store(second_pg, tenant_id="acme")
    assert add_award(moved, "AFTER-THE-MOVE") == dbcopy.BATCH + 8
    moved.close()

    with pytest.raises(dbcopy.CopyRefused):
        dbcopy.copy_database(pg, second_pg)
    assert dbcopy.mismatches(dbcopy.copy_database(pg, second_pg, allow_nonempty=True)) == []


def test_a_connection_the_server_closes_is_replaced(pg):
    """What Cloud SQL maintenance, or a long freeze on Cloud Run, does."""
    store = Store(pg, tenant_id="acme")
    seed(store)

    def kill():
        pid = store._one("SELECT pg_backend_pid() AS pid")["pid"]
        with _admin() as conn:
            conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        return pid

    # Killed and used straight away: the read fails, reconnects, and is repeated.
    first = kill()
    assert store.contract_totals("entity_key", "UEI777")["contract_count"] == 3
    assert store._one("SELECT pg_backend_pid() AS pid")["pid"] != first

    # Killed while idle: noticed by the check before anything is asked of it.
    kill()
    store._last_used -= Store.IDLE_CHECK_SECONDS + 1
    assert add_award(store, "AFTER-A-RECONNECT") == 4, "a write works first time"
    store.close()


def test_a_lock_is_shared_between_two_connections(pg):
    """Two instances of the service: only one may run the alert check."""
    one, two = Store(pg, tenant_id="acme"), Store(pg, tenant_id="acme")
    assert one.try_lock("alerts") is True
    assert two.try_lock("alerts") is False
    assert two.try_lock("something-else") is True, "locks are per name"
    one.unlock("alerts")
    assert two.try_lock("alerts") is True
    two.unlock("alerts")
    two.unlock("something-else")
    one.close()
    two.close()
