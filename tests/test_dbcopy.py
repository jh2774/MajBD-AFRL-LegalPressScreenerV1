"""Moving the data to another database, and knowing it all arrived.

These run SQLite to SQLite, which exercises the copy itself: every table, row
ids kept, the refusals, an older source schema, the command line. The parts
only Postgres has — the id counters, streaming a large table — are checked
against a real server in test_postgres_live.py.
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from foci_screen import dbcopy
from foci_screen import portfolio as pf
from foci_screen.cli import main
from foci_screen.store import Store


def seeded(path) -> str:
    """A database with something in tables of each kind: per-tenant rows,
    shared rows, and rows whose id the database hands out."""
    store = Store(str(path), tenant_id="acme")
    store.start_run("DoD", {"months": 12}, run_id="r1")
    with store._tx() as c:
        for piid in ("P1", "P2", "P3"):
            c.execute(
                "INSERT INTO contracts (tenant_id, contract_key, run_id, piid, entity_key,"
                " entity_name, amount, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                ("acme", piid, "r1", piid, "UEI777", "NORTHWIND ROBOTICS INC", 1000.0,
                 "2026-01-01T00:00:00Z"))
    store.create_notice(run_id="r1", entity_key="UEI777", entity_name="NORTHWIND ROBOTICS INC",
                        severity="high", recipient="x@mail.mil", officer_confidence="high",
                        subject="s", body_text="b", trigger_reason="Always notify.")
    key = pf.encode(pf.from_entity_keys("Watch", ["UEI777", "CRD:999001"]))
    store.save_alert_subscription(name="Watch", emails=["ko@agency.gov"],
                                  companies=["UEI777", "CRD:999001"], portfolio_key=key)
    store.save_adv_snapshot("999001", {"crd": "999001", "name": "HARBORLIGHT"}, "03/31/2026")
    store.set_entity_link("UEI777", status="confirmed", cik="0004400123", decided_by="t")
    store.close()
    return str(path)


def rows(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    finally:
        conn.close()


def test_every_table_is_copied_with_its_rows_and_ids(tmp_path):
    source, target = seeded(tmp_path / "old.db"), str(tmp_path / "new.db")
    report = dbcopy.copy_database(source, target)

    assert set(report) == set(dbcopy.TABLES), "every table the application defines"
    assert dbcopy.mismatches(report) == []
    assert report["contracts"] == {"source": 3, "copied": 3, "target": 3}
    for table in ("runs", "contracts", "notices", "alert_subscriptions", "adv_snapshots",
                  "entity_links"):
        assert rows(target, table) == rows(source, table), table

    # And the copy is a working database, not just matching rows.
    moved = Store(target, tenant_id="acme")
    assert [s["emails"] for s in moved.alert_subscriptions()] == [["ko@agency.gov"]]
    assert moved.confirmed_ciks(["UEI777"]) == {"4400123": "UEI777"}
    moved.close()


def test_the_source_is_only_read(tmp_path):
    source = seeded(tmp_path / "old.db")
    before = hashlib.sha256(open(source, "rb").read()).hexdigest()
    dbcopy.copy_database(source, str(tmp_path / "new.db"))
    assert hashlib.sha256(open(source, "rb").read()).hexdigest() == before


def test_a_destination_already_in_use_is_refused_untouched(tmp_path):
    """Ids from two histories would interleave, and rows would be skipped
    without a word. Better to stop before the first one."""
    source, target = seeded(tmp_path / "old.db"), seeded(tmp_path / "in-use.db")
    before = rows(target, "contracts")
    with pytest.raises(dbcopy.CopyRefused, match="already holds rows"):
        dbcopy.copy_database(source, target)
    assert rows(target, "contracts") == before

    with pytest.raises(dbcopy.CopyRefused, match="same database"):
        dbcopy.copy_database(source, source)


def test_running_it_again_is_harmless(tmp_path):
    source, target = seeded(tmp_path / "old.db"), str(tmp_path / "new.db")
    dbcopy.copy_database(source, target)
    again = dbcopy.copy_database(source, target, allow_nonempty=True)
    assert dbcopy.mismatches(again) == []
    assert again["contracts"]["target"] == 3, "nothing doubled"


def test_an_older_source_schema_still_copies(tmp_path):
    """The old site may be a version behind: a table it never had is skipped,
    and a column it never had is left at its default."""
    source = seeded(tmp_path / "old.db")
    conn = sqlite3.connect(source)
    conn.execute("DROP TABLE formd_changes")
    conn.execute("ALTER TABLE notices DROP COLUMN trigger_reason")
    conn.commit()
    conn.close()

    target = str(tmp_path / "new.db")
    said: list[str] = []
    report = dbcopy.copy_database(source, target, progress=said.append)
    assert "formd_changes" not in report
    assert any("formd_changes: not in the source" in line for line in said)
    assert report["notices"]["target"] == 1

    moved = sqlite3.connect(target)
    assert moved.execute("SELECT trigger_reason, subject FROM notices").fetchall() == [
        (None, "s")]
    moved.close()


def test_a_database_is_described_without_its_password():
    render = "postgres://foci:s3cret@dpg-abc.oregon-postgres.render.com/foci_db"
    assert dbcopy.describe(render) == \
        "Postgres database foci_db at dpg-abc.oregon-postgres.render.com"
    socket = "postgresql://foci:s3cret@/foci?host=/cloudsql/concord:us-central1:foci-db"
    assert dbcopy.describe(socket) == \
        "Postgres database foci at /cloudsql/concord:us-central1:foci-db"
    assert "s3cret" not in dbcopy.describe(socket)
    assert dbcopy.describe("foci_screen.db") == "SQLite file foci_screen.db"


def test_the_command_copies_and_reports(tmp_path, monkeypatch, capsys):
    source, target = seeded(tmp_path / "old.db"), str(tmp_path / "new.db")
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("FOCI_USER_AGENT", "test (test@example.org)")

    # The old database's address comes from the environment, so its password
    # is never typed where a shell keeps history.
    monkeypatch.setenv("SOURCE_DATABASE_URL", source)
    assert main(["copy-db", "--to", target]) == 0
    out = capsys.readouterr().out
    assert "every table's count matches the source" in out
    assert "contracts: 3 of 3 row(s)" in out

    assert main(["copy-db", "--to", target]) == 2, "the destination is no longer empty"
    assert "already holds rows" in capsys.readouterr().err

    monkeypatch.delenv("SOURCE_DATABASE_URL")
    assert main(["copy-db", "--to", str(tmp_path / "third.db")]) == 2
    assert "SOURCE_DATABASE_URL" in capsys.readouterr().err
