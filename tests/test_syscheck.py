"""The system check: can this site do its job, and what does it hold.

It exists for moving between hosts, where "the page loads" proves very little.
Each check here has to fail when the thing it names is really broken, and the
answer must never carry a secret.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from foci_screen import config, dbcopy, syscheck
from foci_screen.store import Store

AUTH = {"X-API-Key": "secret-key"}


class Http:
    """Every source answers 200 unless told otherwise, by a part of its URL."""

    def __init__(self, **answers):
        self.answers = {k.replace("_", "."): v for k, v in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def request(self, method, url, **kw):
        self.calls.append((url, kw))
        for part, answer in self.answers.items():
            if part in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {"status": 200, "json": {"status": "ok"}, "text": "{}", "error": ""}


class Cfg:
    dsn = ""
    managed_host = ""
    alerts_paused = False


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "check.db"), tenant_id="acme")
    yield s
    s.close()


def named(result, name):
    return next(c for c in result["checks"] if c["name"].startswith(name))


# ---------------------------------------------------------------- the pieces

def test_every_setting_the_application_reads_is_one_the_check_knows():
    """Or a setting in use on the old site would be missing from the list of
    what to carry to the new one."""
    source = Path(config.__file__).read_text(encoding="utf-8")
    read = set(re.findall(r'(?:_env|_flag|_switch|os\.environ\.get)\(\s*"([A-Z][A-Z0-9_]+)"',
                          source))
    # Set by the platform, not by whoever deploys.
    hosts = {"RENDER", "DYNO", "FLY_APP_NAME", "K_SERVICE", "WEBSITE_INSTANCE_ID", "VERCEL",
             "VERCEL_PROJECT_PRODUCTION_URL"}
    assert read - hosts - set(syscheck.SETTINGS) == set()
    assert syscheck.HOST_SPECIFIC <= set(syscheck.SETTINGS)


def test_settings_are_named_never_shown_and_split_by_what_a_move_carries():
    env = {"FOCI_API_KEYS": "default:hunter2", "BREVO_API_KEY": "xkeysib-secret",
           "ALERTS_FROM": "alerts@example.org", "DATABASE_URL": "postgresql://u:pw@h/db",
           "ALERTS_PAUSED": "true", "SAM_API_KEY": "   ", "UNRELATED": "x"}
    listed = syscheck.settings_in_use(env)
    assert listed == {"carry": ["FOCI_API_KEYS", "ALERTS_FROM", "BREVO_API_KEY"],
                      "this_host": ["DATABASE_URL", "ALERTS_PAUSED"]}
    assert not any(v in json.dumps(listed) for v in ("hunter2", "xkeysib", "pw@h"))


def test_the_memory_limit_is_read_from_the_container_where_there_is_one(tmp_path):
    assert syscheck.memory_limit_mb(str(tmp_path)) is None, "a developer's machine"
    (tmp_path / "memory.max").write_text("1073741824\n")
    assert syscheck.memory_limit_mb(str(tmp_path)) == 1024
    (tmp_path / "memory.max").write_text("max\n")
    assert syscheck.memory_limit_mb(str(tmp_path)) is None, "no limit set"
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text("536870912")
    assert syscheck.memory_limit_mb(str(tmp_path)) == 512, "the older layout"


def test_too_little_memory_is_a_fault_and_a_tight_fit_is_a_note():
    assert syscheck.memory_check(1024)["ok"] is True
    tight = syscheck.memory_check(512)
    assert tight["ok"] is None and tight["fix"] == ""
    small = syscheck.memory_check(256)
    assert small["ok"] is False and "--memory 1Gi" in small["fix"]
    assert syscheck.memory_check(None)["ok"] is None


def test_the_database_is_written_to_and_read_back_and_left_as_it_was(store):
    check = syscheck.database_check(store, store.dsn if hasattr(store, "dsn") else "x.db", "")
    assert check["ok"] is True and "written, read back and removed" in check["detail"]
    assert store._one("SELECT COUNT(*) AS n FROM alert_sent")["n"] == 0


def test_a_database_that_cannot_be_written_to_fails_with_the_reason(store, monkeypatch):
    def refuse():
        raise PermissionError("permission denied for table alert_sent")

    monkeypatch.setattr(store, "_tx", refuse)
    cloud_sql = "postgresql://foci:pw@/foci?host=/cloudsql/p:r:i"
    check = syscheck.database_check(store, cloud_sql, "")
    assert check["ok"] is False and "permission denied" in check["detail"]
    assert "pw" not in check["detail"], "where it is, without the password"


def test_a_file_database_on_a_managed_host_works_today_and_is_gone_tomorrow(store):
    check = syscheck.database_check(store, "foci_screen.db", "Cloud Run")
    assert check["ok"] is False and "thrown away" in check["detail"]
    assert "INSTANCE_CONNECTION_NAME" in check["fix"]


def test_a_source_that_refuses_or_does_not_answer_is_a_fault():
    ok = syscheck.probe(Http(), "SEC", "https://www.sec.gov/x", why="Needed.")
    assert ok["ok"] is True and ok["detail"].endswith("Needed.")

    refused = syscheck.probe(Http(sec_gov={"status": 403}), "SEC", "https://www.sec.gov/x?a=1")
    assert refused["ok"] is False and "https://www.sec.gov/x answered 403" in refused["detail"]
    assert "FOCI_USER_AGENT" in refused["fix"]

    silent = syscheck.probe(Http(sec_gov={"status": 0, "error": "ConnectTimeout"}),
                            "SEC", "https://www.sec.gov/x")
    assert silent["ok"] is False and "did not answer (ConnectTimeout)" in silent["detail"]

    broken = syscheck.probe(Http(sec_gov=RuntimeError("boom")), "SEC", "https://www.sec.gov/x")
    assert broken["ok"] is False, "a check that raises is a failed check, not a failed page"


def test_a_probe_does_not_wait_long_or_use_the_cache():
    http = Http()
    syscheck.probe(http, "SEC", "https://www.sec.gov/x")
    [(_, kw)] = http.calls
    assert kw["use_cache"] is False and kw["max_retries"] == 1
    assert kw["timeout"] == syscheck.PROBE_TIMEOUT <= 10


def test_the_site_reaching_its_own_address():
    assert syscheck.self_check(Http(), "https://site.example/")["ok"] is True
    lost = syscheck.self_check(Http(site_example={"status": 404}), "https://site.example")
    assert lost["ok"] is False and "FOCI_PUBLIC_URL" in lost["fix"]
    # Something answered 200 there, and it was not this application.
    other = syscheck.self_check(Http(site_example={"status": 200, "json": None}),
                                "https://site.example")
    assert other["ok"] is False


def test_every_table_the_copy_moves_is_counted(store):
    counts = syscheck.table_counts(store)
    assert list(counts) == dbcopy.TABLES and set(counts.values()) == {0}
    store.add_formd_index([{"accession": "0000000001-26-000001", "cik": "1", "name": "A",
                            "form": "D", "filed": "2026-01-01"}])
    assert syscheck.table_counts(store)["formd_index"] == 1


def test_which_deploy_is_answering_on_either_host():
    assert syscheck.revision({"K_REVISION": "foci-00007-abc"}) == "foci-00007-abc"
    assert syscheck.revision({"RENDER_GIT_COMMIT": "250a418d0c4f9e2b"}) == "250a418"
    assert syscheck.revision({}) == ""


def test_saved_watchlists_that_nothing_screens_on_a_schedule_are_said(store):
    """Each can now carry its own screening policy, which makes it easy to
    assume it is screened every night. Without a queue, it is not."""
    assert syscheck.watchlist_check(store, "thread") == {
        "name": "Watchlists", "ok": None, "detail": "None saved.", "fix": ""}
    store.create_watchlist("Navy shipbuilders", {"agency": "Department of the Navy"},
                           policy={"notice_threshold": "high"})
    idle = syscheck.watchlist_check(store, "thread")
    assert idle["ok"] is None and idle["detail"].startswith("1 active, and nothing screens")
    assert "REDIS_URL" in idle["detail"]
    swept = syscheck.watchlist_check(store, "rq")
    assert swept["ok"] is True and "each night" in swept["detail"]


# --------------------------------------------------------------- all together

def run(store, http=None, env=None, **cfg):
    settings = Cfg()
    settings.dsn = "check.db"
    for name, value in cfg.items():
        setattr(settings, name, value)
    return syscheck.run(settings, store, http or Http(), "https://site.example",
                        {"mode": "draft", "explanation": "No mail service is connected."},
                        environ=env or {}, version="0.8.0")


def test_a_site_that_can_do_everything_says_so(store):
    result = run(store, env={"K_REVISION": "foci-00007-abc"})
    assert result["ok"] is True and result["revision"] == "foci-00007-abc"
    assert not [c["name"] for c in result["checks"] if c["ok"] is False]
    assert named(result, "Email alerts")["ok"] is None, "drafts are a choice, not a fault"
    assert named(result, "Alert checks")["ok"] is True
    assert result["rows"] == 0 and list(result["tables"]) == dbcopy.TABLES


def test_one_broken_dependency_fails_the_whole_and_names_itself(store):
    result = run(store, http=Http(adviserinfo={"status": 0, "error": "ConnectionError"}))
    assert result["ok"] is False
    assert [c["name"] for c in result["checks"] if c["ok"] is False] == [
        "SEC adviser database (IAPD)"]


def test_a_paused_site_is_noted_not_failed_and_the_answer_holds_no_secret(store):
    env = {"FOCI_API_KEYS": "default:hunter2", "DB_PASS": "s3cret", "ALERTS_PAUSED": "1"}
    result = run(store, env=env, alerts_paused=True)
    paused = named(result, "Alert checks")
    assert paused["ok"] is None and "ALERTS_PAUSED" in paused["detail"]
    assert result["ok"] is True
    assert result["settings"] == {"carry": ["FOCI_API_KEYS"],
                                  "this_host": ["DB_PASS", "ALERTS_PAUSED"]}
    assert "hunter2" not in json.dumps(result) and "s3cret" not in json.dumps(result)


# ----------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    for var in ("DATABASE_URL", "REDIS_URL", "ALERTS_PAUSED", "K_SERVICE", "RENDER"):
        monkeypatch.delenv(var, raising=False)
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    http = Http()
    monkeypatch.setattr(app_module, "_probe_client", lambda: http)
    return TestClient(app_module.app), app_module, http


def test_the_check_is_for_people_with_a_key_and_asks_the_site_for_itself(client):
    c, app_module, http = client
    assert c.get("/v1/system/check").status_code == 401

    body = c.get("/v1/system/check", headers=AUTH).json()
    assert body["ok"] is True and body["host"] == "self-hosted"
    assert body["database"] == "sqlite" and "secret-key" not in json.dumps(body)
    assert "FOCI_API_KEYS" in body["settings"]["carry"]
    asked = [url for url, _ in http.calls]
    assert "http://testserver/health" not in asked, \
        "nothing keeps itself awake on a developer's machine, so nothing depends on it"
    assert named(body, "The site can reach")["ok"] is None
    assert sum("sec.gov" in url for url in asked) == 4
    assert any("fpds.gov" in url for url in asked), "the SAM registration comes from FPDS"
    assert [c["name"] for c in body["checks"]][-1] == "Watchlists"


def test_the_counts_follow_what_is_stored(client):
    c, app_module, _ = client
    store = app_module.store_for("acme")
    store.save_vehicle_watch("UEI777", phrase="Northwind")
    store.mark_edgar_day("20261006", 2)
    body = c.get("/v1/system/check", headers=AUTH).json()
    assert (body["tables"]["vehicle_watches"], body["tables"]["edgar_days"]) == (1, 1)
    assert body["rows"] == 2
