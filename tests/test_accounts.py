"""Email-and-password accounts, each with a workspace of its own.

An account owns a tenant, so its screens, notices, watchlists, screening
settings and alert lists are its own without any change to how those are
stored. Its portfolio is kept on the account. Keys keep working beside it.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from foci_screen import accounts, cli
from foci_screen import portfolio as pf
from foci_screen.store import Store

WEB = {"X-Foci-Client": "web"}
PASSWORD = "correct horse battery"


# ------------------------------------------------------------------ hashing

def test_a_password_verifies_and_a_wrong_one_does_not():
    stored = accounts.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert accounts.verify_password(PASSWORD, stored)
    assert not accounts.verify_password(PASSWORD + "!", stored)
    assert not accounts.verify_password(PASSWORD, "garbage")


def test_each_hash_is_salted():
    assert accounts.hash_password(PASSWORD) != accounts.hash_password(PASSWORD)


def test_weaker_parameters_are_marked_for_rehash():
    assert not accounts.needs_rehash(accounts.hash_password(PASSWORD))
    assert accounts.needs_rehash("scrypt$1024$8$1$c2FsdA==$ZGlnZXN0")


@pytest.mark.parametrize("setting,email,allowed", [
    ("", "a@agency.gov", False),
    ("open", "a@agency.gov", True),
    ("a@agency.gov, b@navy.mil", "a@agency.gov", True),
    ("a@agency.gov", "c@agency.gov", False),
    ("@agency.gov", "c@agency.gov", True),
    ("@agency.gov", "c@notagency.gov", False),
])
def test_who_may_sign_up(setting, email, allowed):
    assert accounts.signup_allowed(setting, email) is allowed


# ---------------------------------------------------------------------- API

@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    def make(**env):
        base = {"FOCI_API_KEYS": "acme:secret-key", "FOCI_DB": str(tmp_path / "api.db"),
                "FOCI_CACHE": str(tmp_path / "cache"), "FOCI_OUT": str(tmp_path / "out"),
                "FOCI_SIGNUP": "open", "FOCI_PUBLIC": ""}
        for k, v in {**base, **env}.items():
            monkeypatch.setenv(k, v)
        for var in ("DATABASE_URL", "REDIS_URL", "FOCI_PUBLIC_URL"):
            monkeypatch.delenv(var, raising=False)
        from foci_screen.api import app as app_module
        importlib.reload(app_module)
        return TestClient(app_module.app), app_module
    return make


def signup(c, email="analyst@agency.gov", password=PASSWORD):
    return c.post("/v1/auth/signup", headers=WEB, json={"email": email, "password": password})


def test_signing_up_signs_in_and_makes_an_empty_workspace(make_client):
    c, _ = make_client()
    r = signup(c)
    assert r.status_code == 201
    account = r.json()["account"]
    assert account["email"] == "analyst@agency.gov"
    assert account["workspace"].startswith("u-") and "password_hash" not in account
    assert "httponly" in r.headers["set-cookie"].lower()

    status = c.get("/v1/auth/status").json()
    assert status["signed_in"] and status["account"]["email"] == "analyst@agency.gov"
    assert c.get("/v1/notices").json()["notices"] == []


def test_each_account_sees_only_its_own_workspace(make_client):
    c, app_module = make_client()
    mine = signup(c).json()["account"]["workspace"]
    app_module.store_for(mine).create_notice(
        run_id="r1", entity_key="UEI1", entity_name="MINE LLC", severity="high",
        recipient="ko@mail.mil", officer_confidence="high", subject="s", body_text="b")
    app_module.store_for("acme").create_notice(
        run_id="r1", entity_key="UEI2", entity_name="KEY TENANT LLC", severity="high",
        recipient="ko@mail.mil", officer_confidence="high", subject="s", body_text="b")

    assert [n["entity_name"] for n in c.get("/v1/notices").json()["notices"]] == ["MINE LLC"]

    other = TestClient(app_module.app)
    signup(other, "colleague@agency.gov")
    assert other.get("/v1/notices").json()["notices"] == []

    # A key still selects its own tenant.
    keyed = TestClient(app_module.app).get("/v1/notices", headers={"X-API-Key": "secret-key"})
    assert [n["entity_name"] for n in keyed.json()["notices"]] == ["KEY TENANT LLC"]


def test_a_change_made_with_the_cookie_must_come_from_the_site(make_client):
    """Another site's page can make the browser send the cookie, but cannot
    add the client header without a CORS preflight this server refuses."""
    c, app_module = make_client()
    workspace = signup(c).json()["account"]["workspace"]
    notice_id = app_module.store_for(workspace).create_notice(
        run_id="r1", entity_key="UEI1", entity_name="X", severity="high",
        recipient="ko@mail.mil", officer_confidence="high", subject="s", body_text="b")

    forged = c.post(f"/v1/notices/{notice_id}/reject", json={"note": "x"})
    assert forged.status_code == 403
    assert app_module.store_for(workspace).get_notice(notice_id)["status"] == "pending"

    ok = c.post(f"/v1/notices/{notice_id}/reject", headers=WEB, json={"note": "fp"})
    assert ok.status_code == 200
    assert c.post("/v1/auth/login", json={"email": "a@b.co", "password": "x"}
                  ).status_code == 403


def test_sign_up_is_closed_unless_the_site_says_otherwise(make_client):
    c, _ = make_client(FOCI_SIGNUP="")
    assert signup(c).status_code == 403
    assert c.get("/v1/auth/status").json()["signup"] == "closed"

    c, _ = make_client(FOCI_SIGNUP="@agency.gov")
    assert signup(c, "someone@elsewhere.example").status_code == 403
    assert signup(c, "someone@agency.gov").status_code == 201


def test_bad_sign_ups_are_refused_with_a_reason(make_client):
    c, _ = make_client()
    assert "10 characters" in signup(c, password="short").json()["detail"]
    assert "valid email" in signup(c, email="not-an-email").json()["detail"]
    signup(c)
    assert signup(c).status_code == 409


def test_signing_in_and_out(make_client):
    c, app_module = make_client()
    signup(c)
    c.post("/v1/auth/logout", headers=WEB)
    assert c.get("/v1/auth/status").json()["signed_in"] is False

    fresh = TestClient(app_module.app)
    r = fresh.post("/v1/auth/login", headers=WEB,
                   json={"email": "Analyst@Agency.gov ", "password": PASSWORD})
    assert r.status_code == 200
    assert fresh.get("/v1/me").json()["account"]["email"] == "analyst@agency.gov"


def test_a_wrong_address_and_a_wrong_password_read_the_same(make_client):
    c, _ = make_client()
    signup(c)
    wrong_pw = c.post("/v1/auth/login", headers=WEB,
                      json={"email": "analyst@agency.gov", "password": "nope-nope-nope"})
    no_user = c.post("/v1/auth/login", headers=WEB,
                     json={"email": "nobody@agency.gov", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


def test_ten_wrong_passwords_lock_the_account(make_client):
    c, _ = make_client()
    signup(c)
    c.post("/v1/auth/logout", headers=WEB)
    for _ in range(accounts.MAX_FAILURES):
        c.post("/v1/auth/login", headers=WEB,
               json={"email": "analyst@agency.gov", "password": "wrong-wrong-wrong"})
    locked = c.post("/v1/auth/login", headers=WEB,
                    json={"email": "analyst@agency.gov", "password": PASSWORD})
    assert locked.status_code == 429


def test_the_portfolio_is_kept_on_the_account(make_client):
    c, app_module = make_client()
    signup(c)
    key = pf.encode(pf.from_entity_keys("Watch", ["UEI123"]))
    assert c.put("/v1/me/portfolio", headers=WEB, json={"portfolio_key": key}
                 ).status_code == 200
    assert c.put("/v1/me/portfolio", headers=WEB, json={"portfolio_key": "junk"}
                 ).status_code == 400

    elsewhere = TestClient(app_module.app)
    elsewhere.post("/v1/auth/login", headers=WEB,
                   json={"email": "analyst@agency.gov", "password": PASSWORD})
    assert elsewhere.get("/v1/me").json()["account"]["portfolio_key"] == key


def test_changing_the_password_ends_every_session(make_client):
    c, app_module = make_client()
    signup(c)
    other = TestClient(app_module.app)
    other.post("/v1/auth/login", headers=WEB,
               json={"email": "analyst@agency.gov", "password": PASSWORD})

    assert c.post("/v1/me/password", headers=WEB, json={
        "current_password": "wrong", "new_password": "a whole new phrase"}
    ).status_code == 403
    assert c.post("/v1/me/password", headers=WEB, json={
        "current_password": PASSWORD, "new_password": "a whole new phrase"}
    ).status_code == 204

    assert other.get("/v1/auth/status").json()["signed_in"] is False
    assert other.post("/v1/auth/login", headers=WEB, json={
        "email": "analyst@agency.gov", "password": "a whole new phrase"}).status_code == 200


def test_a_site_with_accounts_but_no_keys_asks_for_sign_in_not_configuration(make_client):
    c, _ = make_client(FOCI_API_KEYS="")
    r = c.get("/v1/notices")
    assert r.status_code == 401 and "Sign in" in r.json()["detail"]

    closed, _ = make_client(FOCI_API_KEYS="", FOCI_SIGNUP="")
    assert closed.get("/v1/notices").status_code == 503


def test_on_a_public_site_an_account_is_admin_of_its_own_workspace(make_client):
    c, app_module = make_client(FOCI_PUBLIC="true")
    workspace = signup(c).json()["account"]["workspace"]
    notice_id = app_module.store_for(workspace).create_notice(
        run_id="r1", entity_key="UEI1", entity_name="X", severity="high",
        recipient="ko@mail.mil", officer_confidence="high", subject="s", body_text="b")
    # Rejecting is admin-only on a public site.
    assert c.post(f"/v1/notices/{notice_id}/reject", headers=WEB,
                  json={"note": "fp"}).status_code == 200

    anonymous = TestClient(app_module.app)
    assert anonymous.post(f"/v1/notices/{notice_id}/reject", headers=WEB,
                          json={"note": "x"}).status_code in (401, 404)


# ---------------------------------------------------------------------- CLI

def test_add_user_can_attach_an_account_to_existing_data(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")

    class Cfg:
        dsn = db
        user_agent = "test/1.0 (ops@agency.gov)"

    monkeypatch.setattr(cli, "get_config", lambda: Cfg())
    monkeypatch.setenv("FOCI_NEW_PASSWORD", PASSWORD)
    assert cli.main(["add-user", "Lead@Agency.gov", "--workspace", "default"]) == 0
    assert "default" in capsys.readouterr().out

    s = Store(db)
    user = s.user_by_email("lead@agency.gov")
    assert user["tenant_id"] == "default"
    assert accounts.verify_password(PASSWORD, user["password_hash"])
    s.close()
    assert cli.main(["add-user", "lead@agency.gov"]) == 1        # already exists

    monkeypatch.setenv("FOCI_NEW_PASSWORD", "short")
    assert cli.main(["add-user", "new@agency.gov"]) == 2
