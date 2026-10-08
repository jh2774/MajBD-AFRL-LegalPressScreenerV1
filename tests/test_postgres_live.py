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

from foci_screen import dbcopy, vehicles
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


def test_the_index_of_fund_filings_and_a_watch_behave_the_same_here(pg):
    """The tables behind "funds named after a contractor": a filing seen twice
    is kept as first recorded, a name is searched literally, and a watch is
    changed in place."""
    store = Store(pg, tenant_id="acme")
    rows = [{"accession": "0009000001-26-000001", "cik": "9000001",
             "name": "MW LSVC Northwind, LLC", "form": "D", "filed": "2026-02-01"},
            {"accession": "0009000002-26-000002", "cik": "9000002",
             "name": "Northwind Co-Invest II LP", "form": "D/A", "filed": "2026-05-02"}]
    store.add_formd_index(rows, "lookup")
    store.set_formd_facts(rows[0]["accession"], {"read_ok": True, "amount_sold": 4_500_000})
    store.add_formd_index(rows, "daily")                # seen again in the daily list
    found = store.formd_index_named("Northwind")
    assert [r["cik"] for r in found] == ["9000002", "9000001"], "newest first"
    assert found[1]["facts"] == {"read_ok": True, "amount_sold": 4_500_000}
    assert found[1]["source"] == "lookup" and found[0]["facts"] is None
    assert store.formd_index_named("northwind", since="2026-03-01") == [found[0]]
    assert store.formd_index_named("n_rthwind") == [], "an underscore is not a wildcard"

    store.mark_edgar_day("20261006", 2)
    store.mark_edgar_day("20261006", 2)
    assert store.edgar_days() == {"20261006"}
    store.add_edgar_entities([{"cik": "9000003", "name": "Northwind Fund 3 LLC"}] * 2)
    [entity] = store.edgar_entities_named("northwind")
    assert entity["listed_at"] is None
    store.mark_entity_listed("9000003")
    assert store.edgar_entities_named("northwind")[0]["listed_at"]

    watch = store.save_vehicle_watch("uei777", phrase="Northwind")
    assert (watch["watching"], watch["unrelated"], watch["watching_since"]) == (False, [], None)
    watch = store.save_vehicle_watch("UEI777", watching=True, unrelated=["9000001"],
                                     looked=True, more_exist=True)
    assert watch["watching"] is True and watch["more_exist"] is True
    assert watch["unrelated"] == ["9000001"] and watch["watching_since"] and watch["looked_at"]
    assert [w["entity_key"] for w in store.vehicle_watches()] == ["UEI777"]

    page = vehicles.view(store, "UEI777")
    assert [f["cik"] for f in page["vehicles"]] == ["9000002"]
    assert page["unrelated"] == [{"cik": "9000001", "name": "MW LSVC Northwind, LLC"}]
    assert page["index_through"] == "20261006"

    other = Store(pg, tenant_id="globex")
    assert other.vehicle_watch("UEI777") is None and other.vehicle_watches() == []
    other.close()
    store.close()


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
