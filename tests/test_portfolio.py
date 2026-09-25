"""Portfolio keys: one line of text that opens a saved dashboard.

The key carries the company list rather than pointing at a stored one, so the
tests that matter are about what happens to text after it leaves here — copied
out of an email, wrapped by a text editor, truncated by a careless selection.
"""
from __future__ import annotations

import pytest
from test_search import make_contract

from foci_screen import portfolio as pf

AUTH = {"X-API-Key": "secret-key"}


def a_portfolio(n=3):
    return pf.from_entity_keys(
        "Aerospace watchlist",
        [f"UEI{i:03d}" for i in range(n)],
        {f"UEI{i:03d}": f"COMPANY {i} INC" for i in range(n)})


# ------------------------------------------------------------ round tripping

def test_a_key_round_trips():
    original = a_portfolio()
    loaded = pf.decode(pf.encode(original))

    assert loaded.name == "Aerospace watchlist"
    assert [c.key for c in loaded.companies] == ["UEI000", "UEI001", "UEI002"]
    assert [c.name for c in loaded.companies] == [
        "COMPANY 0 INC", "COMPANY 1 INC", "COMPANY 2 INC"]


def test_the_same_portfolio_always_produces_the_same_key():
    """Two people who built the same list can see that they did."""
    one = a_portfolio()
    two = pf.Portfolio(name=one.name, companies=list(one.companies),
                       created_at=one.created_at)
    assert pf.encode(one) == pf.encode(two)


def test_a_key_is_one_unbroken_token():
    key = pf.encode(a_portfolio())
    assert key.startswith("FOCI-PORTFOLIO-1.")
    assert not any(ch.isspace() for ch in key)


def test_a_key_survives_being_kept_in_a_text_file():
    """Editors wrap long lines; a key pasted back with newlines still opens."""
    key = pf.encode(a_portfolio(12))
    wrapped = "\n".join(key[i:i + 40] for i in range(0, len(key), 40))
    assert len(pf.decode(f"  {wrapped}\n").companies) == 12


def test_order_is_preserved_and_duplicates_collapse():
    built = pf.from_entity_keys("p", ["uei9", "UEI1", "uei9", "", "  ", "UEI2"])
    assert [c.key for c in built.companies] == ["UEI9", "UEI1", "UEI2"]


# ------------------------------------------------------- refusing bad keys

def test_a_truncated_key_is_refused_rather_than_partly_loaded():
    """The failure this checksum exists for.

    A portfolio that quietly loads eight of its ten companies leaves two
    companies unwatched by someone who believes they are watching them.
    """
    key = pf.encode(a_portfolio(10))
    with pytest.raises(pf.PortfolioKeyError) as err:
        pf.decode(key[:-12] + key[-9:])       # bite out of the payload
    assert "checksum" in str(err.value) or "damaged" in str(err.value)


def test_a_key_missing_its_checksum_says_so():
    key = pf.encode(a_portfolio())
    with pytest.raises(pf.PortfolioKeyError) as err:
        pf.decode(key.rsplit(".", 1)[0])
    assert "checksum" in str(err.value)


def test_something_that_is_not_a_key_at_all():
    with pytest.raises(pf.PortfolioKeyError) as err:
        pf.decode("my portfolio: lockheed, boeing")
    assert "FOCI-PORTFOLIO-1." in str(err.value)


def test_a_key_from_a_later_version_names_the_version():
    key = pf.encode(a_portfolio()).replace("FOCI-PORTFOLIO-1.", "FOCI-PORTFOLIO-9.")
    with pytest.raises(pf.PortfolioKeyError) as err:
        pf.decode(key)
    assert "version 9" in str(err.value)


def test_an_empty_portfolio_has_no_key():
    with pytest.raises(pf.PortfolioKeyError):
        pf.encode(pf.Portfolio(name="empty"))


def test_an_oversized_portfolio_is_refused():
    too_many = pf.from_entity_keys("big", [f"UEI{i}" for i in range(pf.MAX_COMPANIES + 1)])
    with pytest.raises(pf.PortfolioKeyError) as err:
        pf.encode(too_many)
    assert str(pf.MAX_COMPANIES) in str(err.value)


def test_a_key_stays_a_reasonable_length():
    """It has to be copyable. 50 companies should not need a scrollbar war."""
    key = pf.encode(a_portfolio(50))
    assert len(key) < 2000, len(key)


# --------------------------------------------------------------- over the API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "pf.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    return TestClient(app_module.app), app_module


def test_minting_and_loading_over_the_api(client):
    c, _ = client
    minted = c.post("/v1/portfolio/key", headers=AUTH,
                    json={"name": "Primes", "companies": ["UEI123", "UEI999"]}).json()
    assert minted["companies"] == 2

    body = c.post("/v1/portfolio", headers=AUTH, json={"key": minted["key"]}).json()
    assert body["name"] == "Primes"
    assert body["totals"]["companies"] == 2
    assert body["totals"]["screened_here"] == 1
    assert body["totals"]["obligated"] == 5_000_000.0


def test_a_company_this_database_has_never_seen_is_listed_as_such(client):
    """Not dropped: it is the list of what to screen next."""
    c, _ = client
    minted = c.post("/v1/portfolio/key", headers=AUTH,
                    json={"name": "Primes", "companies": ["UEI123", "UEI999"]}).json()
    body = c.post("/v1/portfolio", headers=AUTH, json={"key": minted["key"]}).json()

    unseen = [r for r in body["companies"] if not r["screened_here"]]
    assert [r["entity_key"] for r in unseen] == ["UEI999"]
    assert unseen[0]["severity"] is None


def test_a_key_opens_against_a_database_that_never_had_the_portfolio(client, tmp_path,
                                                                     monkeypatch):
    """The reason the key carries its own list.

    This deployment's database has been rebuilt from nothing — which has
    already happened once in practice — and the saved dashboard still opens.
    """
    c, _ = client
    minted = c.post("/v1/portfolio/key", headers=AUTH,
                    json={"name": "Primes", "companies": ["UEI123"]}).json()

    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("FOCI_DB", str(tmp_path / "rebuilt.db"))
    from foci_screen.api import app as app_module
    importlib.reload(app_module)
    fresh = TestClient(app_module.app)

    body = fresh.post("/v1/portfolio", headers=AUTH, json={"key": minted["key"]}).json()
    assert body["totals"]["companies"] == 1
    assert body["companies"][0]["entity_name"] == "ACME DYNAMICS LLC"
    assert body["companies"][0]["screened_here"] is False


def test_a_bad_key_is_a_400_with_the_reason(client):
    c, _ = client
    r = c.post("/v1/portfolio", headers=AUTH, json={"key": "FOCI-PORTFOLIO-1.zzz.0000"})
    assert r.status_code == 400
    assert "damaged" in r.json()["detail"] or "checksum" in r.json()["detail"]


def test_portfolio_routes_need_a_key(client):
    c, _ = client
    assert c.post("/v1/portfolio", json={"key": "x"}).status_code == 401
    assert c.post("/v1/portfolio/key", json={"companies": ["X"]}).status_code == 401
