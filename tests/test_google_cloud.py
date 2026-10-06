"""What changes when the service runs on Cloud Run with Cloud SQL.

Three things about that host are different from a long-lived server, and each
one breaks something that worked before:

  * The database connection can be closed underneath the process, which is
    frozen between requests. The store must notice and reconnect.
  * Processor time is given only while a request is in flight, and an idle
    instance is shut down. A screen running in a background thread needs a
    request held open for as long as it runs.
  * There is no disk. Files written in the container live in its memory, so
    a cache that is never pruned is a leak.

Nothing here needs Google Cloud or a database server; the real-Postgres checks
are in test_postgres_live.py and run in CI.
"""
from __future__ import annotations

import importlib
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from foci_screen import jobs
from foci_screen.config import Config
from foci_screen.httpclient import HttpClient
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}
HOSTS = ("RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID")
DB_VARS = ("DATABASE_URL", "INSTANCE_CONNECTION_NAME", "CLOUD_SQL_CONNECTION_NAME",
           "INSTANCE_UNIX_SOCKET", "DB_USER", "DB_PASS", "DB_PASSWORD", "DB_NAME")


@pytest.fixture()
def clean_env(monkeypatch):
    for var in HOSTS + DB_VARS + ("K_REVISION", "FOCI_KEEP_AWAKE", "FOCI_CACHE_MAX_MB",
                                  "REDIS_URL", "FOCI_INPROCESS_SCREENS", "FOCI_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ------------------------------------------------------- finding the database

def test_cloud_sql_is_found_from_its_four_separate_values(clean_env):
    """Google's convention for Cloud Run: connection name, user, database, and
    the password on its own as the one secret. No URL to assemble by hand."""
    clean_env.setenv("INSTANCE_CONNECTION_NAME", "concord-foci:us-central1:foci-db")
    clean_env.setenv("DB_USER", "foci")
    clean_env.setenv("DB_NAME", "foci")
    clean_env.setenv("DB_PASS", "p@ss/w0rd:with#marks")

    dsn = Config().dsn
    assert dsn.startswith("postgresql://foci:")
    assert dsn.endswith("@/foci?host=/cloudsql/concord-foci:us-central1:foci-db")
    assert "p@ss" not in dsn, "the password is escaped, not pasted into the URL"

    conninfo = pytest.importorskip("psycopg.conninfo")
    assert conninfo.conninfo_to_dict(dsn) == {
        "user": "foci", "password": "p@ss/w0rd:with#marks", "dbname": "foci",
        "host": "/cloudsql/concord-foci:us-central1:foci-db"}


def test_a_database_url_still_wins_and_half_a_setup_is_not_guessed_at(clean_env):
    clean_env.setenv("INSTANCE_CONNECTION_NAME", "p:r:i")
    assert Config().database_url == "", "no user or database name: fall back to SQLite"

    clean_env.setenv("DB_USER", "foci")
    clean_env.setenv("DB_NAME", "foci")
    clean_env.setenv("DATABASE_URL", "postgres://u:p@host.example/db")
    assert Config().database_url == "postgres://u:p@host.example/db"


# ------------------------------------------------------ a lost connection

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        self.conn.statements.append(sql)
        if self.conn.dead:
            raise self.conn.error("server closed the connection unexpectedly")

    def fetchall(self):
        return [{"n": self.conn.name}]


class FakeConnection:
    """Enough of a psycopg connection to be lost and replaced."""

    def __init__(self, name, error=RuntimeError):
        self.name, self.error = name, error
        self.dead = self.closed = self.broken = False
        self.statements: list[str] = []
        self.commits = self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def execute(self, sql):
        FakeCursor(self).execute(sql)

    def commit(self):
        if self.dead:
            raise self.error("connection is closed")
        self.commits += 1

    def rollback(self):
        if self.dead:
            raise self.error("connection is closed")
        self.rollbacks += 1

    def close(self):
        self.closed = True


def postgres_store(monkeypatch, error=RuntimeError):
    """A Store that believes it is on Postgres, over fake connections."""
    made: list[FakeConnection] = []

    def connect(self):
        made.append(FakeConnection(f"conn{len(made) + 1}", error))
        return made[-1]

    monkeypatch.setattr(Store, "_connect", connect)
    monkeypatch.setattr(Store, "_migrate", lambda self: None)
    return Store("postgresql://u:p@db.example/foci"), made


def test_a_connection_dropped_while_idle_is_replaced_before_it_is_used(monkeypatch):
    """Cloud Run freezes the process between requests; minutes later the
    socket is gone. The first request back must not be the one that finds out."""
    store, made = postgres_store(monkeypatch)
    assert store._query("SELECT 1") == [{"n": "conn1"}]

    made[0].dead = True
    store._last_used -= Store.IDLE_CHECK_SECONDS + 1      # ...time passes
    assert store._query("SELECT 1") == [{"n": "conn2"}], "answered by a new connection"
    assert made[0].closed and len(made) == 2


def test_a_connection_used_a_moment_ago_is_not_checked_every_time(monkeypatch):
    store, made = postgres_store(monkeypatch)
    for _ in range(5):
        store._query("SELECT x")
    assert made[0].statements == ["SELECT x"] * 5, "no extra round trips on a busy page"


def test_a_read_that_hits_a_dead_connection_is_asked_again(monkeypatch):
    """Died between two statements of the same request: too recent for the
    idle check. A read is safe to repeat, so it is, once."""
    psycopg = pytest.importorskip("psycopg")
    store, made = postgres_store(monkeypatch, error=psycopg.OperationalError)
    store._query("SELECT 1")
    made[0].dead = True
    assert store._query("SELECT 2") == [{"n": "conn2"}]


def test_a_write_is_not_silently_repeated_but_the_next_call_recovers(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    store, made = postgres_store(monkeypatch, error=psycopg.OperationalError)
    made[0].dead = True
    with pytest.raises(psycopg.OperationalError):
        with store._tx() as c:
            c.execute("INSERT INTO t VALUES (?)", (1,))
    assert len(made) == 1, "a half-done transaction is not replayed on a new connection"

    made[0].broken = True                       # what the driver reports afterwards
    with store._tx() as c:
        c.execute("INSERT INTO t VALUES (?)", (1,))
    assert made[1].statements == ["INSERT INTO t VALUES (%s)"] and made[1].commits == 1


def test_the_connection_is_never_swapped_in_the_middle_of_a_transaction(monkeypatch):
    store, made = postgres_store(monkeypatch)
    with store._tx() as c:
        c.execute("INSERT INTO t VALUES (?)", (1,))
        store._last_used -= Store.IDLE_CHECK_SECONDS + 1
        store._query("SELECT 1")                # a read inside the transaction
    assert len(made) == 1 and "SELECT 1" in made[0].statements


def test_the_cross_instance_lock_asks_the_database(monkeypatch):
    store, made = postgres_store(monkeypatch)
    monkeypatch.setattr(Store, "_read", lambda self, sql, params: (
        made[0].statements.append((sql, params)) or [{"locked": False}]))
    assert store.try_lock("alerts") is False, "another instance holds it"
    sql, params = made[0].statements[-1]
    assert "pg_try_advisory_lock" in sql and params == ("foci:alerts",)


def test_sqlite_needs_no_database_lock(tmp_path):
    store = Store(str(tmp_path / "one-process.db"))
    assert store.try_lock("alerts") is True
    store.unlock("alerts")                      # and unlocking is not an error
    store.close()


# --------------------------------------------------- memory used as a disk

class CacheCfg:
    user_agent = "test (test@example.org)"
    rate_limit_qps = 0
    cache_ttl_seconds = 3600
    cache_max_mb = 1
    timeout = 5
    max_retries = 1

    def __init__(self, cache_dir):
        self.cache_dir = str(cache_dir)


def test_the_cache_is_smaller_by_default_where_files_live_in_memory(clean_env):
    assert Config().cache_max_mb == 256
    clean_env.setenv("K_SERVICE", "foci")
    assert Config().cache_max_mb == 64
    clean_env.setenv("FOCI_CACHE_MAX_MB", "20")
    assert Config().cache_max_mb == 20


def test_expired_responses_are_deleted_and_the_oldest_go_first_when_full(tmp_path):
    http = HttpClient(CacheCfg(tmp_path))
    now = time.time()

    def cached(name, kb, age_seconds):
        path = tmp_path / f"{name}.json"
        path.write_bytes(b"x" * kb * 1024)
        os.utime(path, (now - age_seconds, now - age_seconds))
        return path

    expired = cached("expired", 10, 7200)
    oldest = cached("oldest", 500, 3000)
    middle = cached("middle", 400, 2000)
    newest = cached("newest", 400, 1000)

    assert http.prune_cache() == 2
    assert not expired.exists(), "past its lifetime: nothing would ever read it again"
    assert not oldest.exists(), "1.3 MB against a 1 MB cap: the oldest makes room"
    assert middle.exists() and newest.exists()


def test_pruning_happens_as_the_cache_is_written(tmp_path, monkeypatch):
    import foci_screen.httpclient as module

    class Response:
        status_code, url, text = 200, "https://example.org/x", '{"ok": true}'
        headers = {"content-type": "application/json"}

        def json(self):
            return {"ok": True}

    http = HttpClient(CacheCfg(tmp_path))
    monkeypatch.setattr(http.session, "request", lambda *a, **kw: Response())
    pruned = []
    monkeypatch.setattr(http, "prune_cache", lambda: pruned.append(1))
    for i in range(module.PRUNE_EVERY * 2):
        http.get(f"https://example.org/{i}")
    assert len(pruned) == 2


# -------------------------------------------- staying awake during a screen

def boot(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "q.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app), app_module


def test_health_says_which_host_answered(tmp_path, clean_env):
    c, _ = boot(tmp_path, clean_env)
    assert c.get("/health").json()["host"] == "self-hosted"

    c, _ = boot(tmp_path, clean_env, K_SERVICE="foci", K_REVISION="foci-00007-abc",
                FOCI_INPROCESS_SCREENS="true")
    body = c.get("/health").json()
    assert (body["host"], body["revision"]) == ("Cloud Run", "foci-00007-abc")


def test_a_screen_started_on_a_managed_host_is_given_a_request_to_hold(tmp_path, clean_env):
    c, app_module = boot(tmp_path, clean_env, K_SERVICE="foci")
    started = {}
    clean_env.setattr(app_module.queue, "enqueue",
                      lambda tenant, run_id, options, **kw: started.update(kw, run=run_id))

    r = c.post("/v1/screens", headers=AUTH, json={"recipient": "ACME DYNAMICS LLC"})
    assert r.status_code == 202
    assert started["hold_url"] == f"http://testserver/internal/hold/{started['run']}"
    assert started["hold_token"] == app_module._hold_token


def test_nothing_is_held_on_a_developer_machine_or_when_switched_off(tmp_path, clean_env):
    for env in ({}, {"K_SERVICE": "foci", "FOCI_KEEP_AWAKE": "false"}):
        c, app_module = boot(tmp_path, clean_env, **env)
        started: dict = {}
        clean_env.setattr(app_module.queue, "enqueue",
                          lambda tenant, run_id, options, seen=started, **kw: seen.update(kw))
        c.post("/v1/screens", headers=AUTH, json={"recipient": "ACME DYNAMICS LLC"})
        assert started == {}, env


def test_the_hold_endpoint_is_no_use_without_the_token(tmp_path, clean_env):
    c, app_module = boot(tmp_path, clean_env)
    assert c.get("/internal/hold/abc").status_code == 404
    assert c.get("/internal/hold/abc", headers={"X-Hold-Token": "guess"}).status_code == 404
    answer = c.get("/internal/hold/abc", headers={"X-Hold-Token": app_module._hold_token})
    assert answer.json() == {"running": False}, "nothing running: answers at once"


def test_the_hold_request_stays_open_exactly_as_long_as_the_screen(tmp_path, clean_env):
    c, app_module = boot(tmp_path, clean_env)
    finished = threading.Event()
    clean_env.setattr(app_module.queue, "is_running", lambda run_id: not finished.is_set())
    threading.Timer(0.3, finished.set).start()

    began = time.monotonic()
    answer = c.get("/internal/hold/r1", headers={"X-Hold-Token": app_module._hold_token})
    assert answer.json() == {"running": False}
    assert 0.25 < time.monotonic() - began < 5


def test_one_hold_request_gives_way_before_the_hosts_timeout(tmp_path, clean_env):
    c, app_module = boot(tmp_path, clean_env)
    clean_env.setattr(app_module, "HOLD_SECONDS", 0.2)
    clean_env.setattr(app_module.queue, "is_running", lambda run_id: True)
    answer = c.get("/internal/hold/r1", headers={"X-Hold-Token": app_module._hold_token})
    assert answer.json() == {"running": True}, "still going: the caller asks again"


def test_the_queue_knows_which_screens_it_is_running(clean_env, tmp_path):
    clean_env.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    clean_env.setenv("FOCI_OUT", str(tmp_path / "out"))
    release, held = threading.Event(), []
    clean_env.setattr(jobs, "run_screen_job", lambda tenant, run_id, options: release.wait(5))
    clean_env.setattr(jobs, "_hold_open", lambda url, token, alive: held.append((url, token)))

    queue = jobs.JobQueue(Config())
    queue.enqueue("acme", "r1", {}, hold_url="https://site.example/internal/hold/r1",
                  hold_token="t")
    assert queue.is_running("r1") and not queue.is_running("other")
    release.set()
    queue._threads["r1"].join(5)
    assert not queue.is_running("r1")
    for _ in range(50):                         # the hold thread starts just after
        if held:
            break
        time.sleep(0.02)
    assert held == [("https://site.example/internal/hold/r1", "t")]


def test_holding_stops_with_the_screen_and_gives_up_if_it_cannot_reach_itself(monkeypatch):
    import requests

    pauses: list = []
    monkeypatch.setattr("time.sleep", pauses.append)
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(headers["X-Hold-Token"])

    monkeypatch.setattr("requests.get", get)
    remaining = iter([True] * 5)
    jobs._hold_open("https://site.example/internal/hold/r1", "t",
                    lambda: next(remaining, False))
    assert calls == ["t", "t", "t"], "asks again for as long as the screen runs"
    # These answers came straight back, so nothing was held: it waits before
    # asking again rather than spinning. A hold that lasted would not pause.
    assert pauses == [15, 15]

    def unreachable(url, headers=None, timeout=None):
        calls.append("failed")
        raise requests.ConnectionError("no route")

    calls.clear()
    monkeypatch.setattr("requests.get", unreachable)
    jobs._hold_open("https://site.example/internal/hold/r1", "t", lambda: True)
    assert calls == ["failed"] * 5, "five tries, then the screen carries on without it"


# ------------------------------------------------ one check across instances

def test_a_check_running_on_another_instance_is_respected(tmp_path, clean_env):
    c, app_module = boot(tmp_path, clean_env)
    store = app_module.store_for("acme")
    clean_env.setattr(store, "try_lock", lambda name: False)
    busy = c.post("/v1/alerts/run", headers=AUTH).json()
    assert busy["status"] == "already running"
    assert app_module._alerts_running.acquire(blocking=False), "this process's lock was freed"
    app_module._alerts_running.release()
