"""Supabase as the database, connected to Vercel.

The addresses below have the shapes Supabase and its Vercel integration hand
out (project `abcdefghijklmnop`, password `pw`). What matters is which one is
used and in what form: Vercel cannot reach IPv6, and this application cannot
work through a transaction-mode pooler.
"""
from __future__ import annotations

import pytest

from foci_screen import dburl
from foci_screen.config import Config

PROJECT = "abcdefghijklmnop"
DIRECT = f"postgresql://postgres:pw@db.{PROJECT}.supabase.co:5432/postgres"
POOLER = "aws-0-us-east-1.pooler.supabase.com"
TRANSACTION = (f"postgres://postgres.{PROJECT}:pw@{POOLER}:6543/postgres"
               f"?sslmode=require&supa=base-pooler.x")
SESSION = f"postgresql://postgres.{PROJECT}:pw@{POOLER}:5432/postgres?sslmode=require"


def test_the_integrations_addresses_end_up_at_the_session_pooler():
    """What Vercel's Supabase integration sets: the pooler in transaction
    mode, with a parameter libpq refuses, and the direct host, IPv6 only."""
    env = {"POSTGRES_URL": TRANSACTION, "POSTGRES_URL_NON_POOLING": DIRECT,
           "POSTGRES_PRISMA_URL": TRANSACTION + "&pgbouncer=true"}
    assert dburl.choose(env) == SESSION


def test_each_supabase_address_on_its_own():
    assert dburl.choose({"POSTGRES_URL": TRANSACTION}) == SESSION
    assert dburl.choose({"DATABASE_URL": SESSION}) == SESSION, "already right: unchanged"
    assert dburl.choose({"DATABASE_URL": DIRECT}) == DIRECT, \
        "no pooler given: the direct address is all there is (it works where IPv6 does)"


def test_a_direct_address_is_only_swapped_for_the_same_projects_pooler():
    other = TRANSACTION.replace(PROJECT, "zzzzzzzzzzzzzzzz")
    assert dburl.choose({"DATABASE_URL": DIRECT, "POSTGRES_URL": other}) == DIRECT


def test_an_address_set_by_hand_comes_first():
    mine = f"postgresql://postgres.{PROJECT}:other@{POOLER}:5432/postgres"
    assert dburl.choose({"DATABASE_URL": mine, "POSTGRES_URL": TRANSACTION}) == mine
    assert dburl.choose({}) == ""


def test_other_hosts_are_left_exactly_as_given():
    render = "postgres://u:p@dpg-x.oregon-postgres.render.com/foci?supa=kept"
    assert dburl.choose({"DATABASE_URL": render}) == render


def test_only_parameters_libpq_understands_are_kept():
    cleaned = dburl.clean("postgres://u:p@h:5432/db?sslmode=require&supa=x&pgbouncer=true"
                          "&connect_timeout=10&workaround=supabase-pooler.vercel")
    assert cleaned == "postgresql://u:p@h:5432/db?sslmode=require&connect_timeout=10"


def test_the_configuration_reads_it_the_same_way(monkeypatch):
    for name in dburl.CANDIDATES + ("INSTANCE_CONNECTION_NAME", "DB_USER", "DB_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POSTGRES_URL", TRANSACTION)
    monkeypatch.setenv("POSTGRES_URL_NON_POOLING", DIRECT)
    assert Config().database_url == SESSION


def test_on_supabase_the_tables_go_in_a_schema_the_data_api_does_not_publish():
    assert dburl.schema_for(SESSION, {}) == "foci"
    assert dburl.schema_for(DIRECT, {}) == "foci"
    assert dburl.schema_for("postgresql://u:p@localhost/foci", {}) == "", \
        "any other Postgres: as before"
    assert dburl.schema_for(SESSION, {"FOCI_DB_SCHEMA": "screening"}) == "screening"
    assert dburl.schema_for(SESSION, {"FOCI_DB_SCHEMA": ""}) == "", "empty: public"
    with pytest.raises(ValueError, match="lower-case Postgres name"):
        dburl.schema_for(SESSION, {"FOCI_DB_SCHEMA": "foci; drop table runs"})


class FakeConnection:
    def __init__(self):
        self.executed: list[str] = []
        self.commits = 0

    def cursor(self):
        conn = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, statement, params=None):
                conn.executed.append(repr(statement))

        return Cursor()

    def commit(self):
        self.commits += 1


def test_each_connection_puts_its_tables_in_that_schema(monkeypatch):
    """Every statement here names its tables bare, so the schema is set once,
    on the connection, before anything else is asked of it."""
    psycopg = pytest.importorskip("psycopg")
    from foci_screen import store as store_mod

    made: list[FakeConnection] = []

    def connect(dsn, **kw):
        made.append(FakeConnection())
        return made[-1]

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.delenv("FOCI_DB_SCHEMA", raising=False)
    store_mod.open_connection(SESSION)
    [conn] = made
    assert len(conn.executed) == 2 and conn.commits == 1
    assert "CREATE SCHEMA IF NOT EXISTS" in conn.executed[0] and "'foci'" in conn.executed[0]
    assert "SET search_path TO" in conn.executed[1] and "'foci'" in conn.executed[1]

    store_mod.open_connection("postgresql://u:p@localhost/foci")
    assert made[1].executed == [], "any other Postgres: nothing changed"
