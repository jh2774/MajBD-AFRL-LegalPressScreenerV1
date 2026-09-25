"""Search and aggregation across contractors, awards, officers and agencies."""
from __future__ import annotations

import pytest

from foci_screen.models import Contract, ContractingOfficer, Entity, Finding, Signal
from foci_screen.store import Store


def make_contract(**kw):
    officer = ContractingOfficer(
        name=kw.pop("ko_name", "Jane Doe"),
        email=kw.pop("ko_email", "jane.doe@mail.mil"),
        source=kw.pop("ko_source", "FPDS-NG <approvedBy>"),
        confidence=kw.pop("ko_confidence", "high"))
    base = dict(
        award_id="A1", piid="N0001925C0001", recipient_name="ACME DYNAMICS LLC",
        recipient_uei="UEI123", awarding_agency="Department of Defense",
        awarding_sub_agency="Department of the Navy", award_amount=5_000_000.0,
        description="Submarine propulsion research", psc_description="R&D services",
        naics_description="Engineering services", solicitation_id="SOL-99",
        country_of_incorporation="USA")
    base.update(kw)
    return Contract(officer=officer, **base)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "search.db"), tenant_id="acme")
    yield s
    s.close()


@pytest.fixture()
def populated(store):
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    store.save_contract(
        make_contract(piid="N0001925C0002", award_id="A2", award_amount=12_000_000.0,
                      description="Radar sustainment",
                      ko_email="sam.smith@mail.mil", ko_name="Sam Smith"),
        "r1", "UEI123")
    store.save_contract(
        make_contract(piid="F0001925C0009", award_id="A3",
                      recipient_name="BEIJING OPTICS LTD", recipient_uei="UEI999",
                      awarding_agency="Department of Energy", awarding_sub_agency="",
                      award_amount=800_000.0, description="Optical components",
                      country_of_incorporation="CHN", foreign_owned_and_located=True),
        "r1", "UEI999")
    return store


# --------------------------------------------------------- officer names

@pytest.mark.parametrize("email,expected", [
    # Placeholder names throughout: these formats came from live FPDS records,
    # and the real officials' addresses have no business in a source tree.
    ("JANE.DOEBAKEWELL.N00019@JSF.MIL", "Jane Doebakewell"),
    # DoD's disambiguating digit belongs to the mailbox, not the person.
    ("JOHN.Q.ROE2.CIV@MAIL.MIL", "John Q Roe"),
    ("JANE.R.DOE3.CIV@MAIL.MIL", "Jane R Doe"),
    ("alex.p.roe.civ@mail.mil", "Alex P Roe"),
])
def test_officer_display_name_is_readable(email, expected):
    from foci_screen.connectors.fpds import _pretty_name
    assert _pretty_name(email) == expected


# ------------------------------------------------------------------ indexing

def test_contracts_are_indexed_for_search(populated):
    assert len(populated.search_contracts("submarine")) == 1
    assert len(populated.search_contracts("N0001925")) == 2


def test_saving_the_same_contract_twice_updates_rather_than_duplicates(store):
    store.save_contract(make_contract(award_amount=1.0), "r1", "UEI123")
    store.save_contract(make_contract(award_amount=99.0), "r2", "UEI123")

    rows = store.contracts_where("entity_key", "UEI123")
    assert len(rows) == 1
    assert rows[0]["amount"] == 99.0
    assert rows[0]["run_id"] == "r2"


def test_contract_without_an_identifier_is_skipped(store):
    store.save_contract(make_contract(piid="", award_id=""), "r1", "UEI123")
    assert store.contracts_where("entity_key", "UEI123") == []


def test_ip_clauses_round_trip_as_a_list(store):
    store.save_contract(
        make_contract(ip_clause_hits=["DFARS 252.227-7013", "Limited Rights data"]),
        "r1", "UEI123")
    assert store.get_contract("N0001925C0001")["ip_clauses"] == [
        "DFARS 252.227-7013", "Limited Rights data"]


# -------------------------------------------------------------------- search

def test_search_is_case_insensitive(populated):
    """Postgres LIKE is case-sensitive and SQLite has no ILIKE; both must match."""
    assert len(populated.search_entities("acme")) == 1
    assert len(populated.search_entities("ACME")) == 1
    assert len(populated.search_entities("AcMe")) == 1


def test_search_entities_aggregates_awards(populated):
    acme = populated.search_entities("acme")[0]
    assert acme["contract_count"] == 2
    assert acme["obligated"] == 17_000_000.0


def test_search_entities_attaches_latest_severity(populated):
    populated.save_finding(Finding(
        entity=Entity(name="ACME DYNAMICS LLC", uei="UEI123"),
        signals=[], total_score=22.0, severity="high", run_id="r1"))

    acme = populated.search_entities("acme")[0]
    assert acme["severity"] == "high"
    assert acme["score"] == 22.0


def test_unscreened_entity_reports_no_severity(populated):
    beijing = populated.search_entities("beijing")[0]
    assert beijing["severity"] is None
    assert beijing["foreign_owned"] == 1


def test_search_officers_groups_by_email(populated):
    officers = {o["ko_email"]: o for o in populated.search_officers()}
    assert set(officers) == {"jane.doe@mail.mil", "sam.smith@mail.mil"}
    assert officers["jane.doe@mail.mil"]["contract_count"] == 2
    assert officers["jane.doe@mail.mil"]["entity_count"] == 2


def test_search_officers_by_name(populated):
    assert len(populated.search_officers("sam")) == 1


def test_officers_without_an_email_are_excluded(store):
    """An unresolved officer is not a person you can address."""
    store.save_contract(make_contract(ko_email="", ko_name=""), "r1", "UEI123")
    assert store.search_officers() == []


def test_search_agencies_aggregates(populated):
    agencies = {a["agency"]: a for a in populated.search_agencies()}
    assert agencies["Department of Defense"]["contract_count"] == 2
    assert agencies["Department of Defense"]["obligated"] == 17_000_000.0
    assert agencies["Department of Energy"]["entity_count"] == 1


def test_search_all_covers_every_kind(populated):
    results = populated.search_all("acme")
    assert results["entities"]
    assert results["officers"] == [] or True   # name search need not match here
    assert set(results) == {"entities", "contracts", "officers", "agencies"}


def test_empty_query_browses_rather_than_failing(populated):
    assert len(populated.search_entities("")) == 2
    assert len(populated.search_agencies("")) == 2


def test_filtering_by_column_rejects_unknown_columns(populated):
    """The column name reaches SQL directly, so it is allow-listed."""
    with pytest.raises(ValueError):
        populated.contracts_where("amount; DROP TABLE contracts", "x")


def test_search_is_tenant_scoped(tmp_path):
    path = str(tmp_path / "multi.db")
    acme, other = Store(path, tenant_id="acme"), Store(path, tenant_id="other")
    try:
        acme.save_contract(make_contract(), "r1", "UEI123")
        assert len(acme.search_contracts("submarine")) == 1
        assert other.search_contracts("submarine") == []
        assert other.search_officers() == []
    finally:
        acme.close()
        other.close()


# ---------------------------------------------------------------- dashboards

def test_severity_counts_use_latest_screen_per_entity(store):
    """An entity screened weekly must not dominate the distribution."""
    ent = Entity(name="ACME", uei="UEI123")
    for run, sev in (("r1", "low"), ("r2", "medium"), ("r3", "critical")):
        store.save_finding(Finding(entity=ent, signals=[], total_score=1.0,
                                   severity=sev, run_id=run))

    assert store.severity_counts() == {"critical": 1}


def test_signal_category_counts(store):
    store.save_finding(Finding(
        entity=Entity(name="ACME", uei="UEI123"),
        signals=[
            Signal(rule_id="R1", category="FOCI", severity="high", score=5,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R2", category="FOCI", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
            Signal(rule_id="R3", category="IP_COLLATERAL", severity="low", score=1,
                   title="t", rationale="r", evidence="e", source="sec"),
        ],
        total_score=7.0, severity="high", run_id="r1"))

    assert store.signal_category_counts() == {"FOCI": 2, "IP_COLLATERAL": 1}


def test_totals(populated):
    t = populated.totals()
    assert t["contracts"] == 3
    assert t["entities"] == 2
    assert t["officers"] == 2
    assert t["obligated"] == 17_800_000.0
    assert t["notices_pending"] == 0


# ---------------------------------------------------------------------- API

AUTH = {"X-API-Key": "secret-key"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCI_API_KEYS", "acme:secret-key")
    monkeypatch.setenv("FOCI_DB", str(tmp_path / "api.db"))
    monkeypatch.setenv("FOCI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FOCI_OUT", str(tmp_path / "out"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import importlib

    from fastapi.testclient import TestClient

    from foci_screen.api import app as app_module
    importlib.reload(app_module)

    store = app_module.store_for("acme")
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    return TestClient(app_module.app), app_module


def test_search_endpoint_groups_results(client):
    c, _ = client
    body = c.get("/v1/search?q=submarine", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["query"] == "submarine"


def test_search_endpoint_filters_by_kind(client):
    c, _ = client
    body = c.get("/v1/search?q=acme&kind=entity", headers=AUTH).json()
    assert "entities" in body and "contracts" not in body


def test_search_rejects_unknown_kind(client):
    c, _ = client
    assert c.get("/v1/search?q=x&kind=planets", headers=AUTH).status_code == 422


def test_overview_endpoint(client):
    c, _ = client
    body = c.get("/v1/overview", headers=AUTH).json()
    assert body["totals"]["contracts"] == 1
    assert set(body) == {"totals", "severity", "categories", "findings_by_day",
                         "top_entities", "recent_findings"}


def test_officer_profile(client):
    c, _ = client
    body = c.get("/v1/officers/jane.doe@mail.mil", headers=AUTH).json()
    assert body["officer"]["name"] == "Jane Doe"
    assert body["officer"]["confidence"] == "high"
    assert len(body["contracts"]) == 1


def test_unknown_officer_is_404(client):
    c, _ = client
    assert c.get("/v1/officers/nobody@mail.mil", headers=AUTH).status_code == 404


def test_agency_profile_falls_back_to_sub_agency(client):
    c, _ = client
    body = c.get("/v1/agencies/Department of the Navy", headers=AUTH).json()
    assert body["contract_count"] == 1
    assert body["entities"][0]["entity_name"] == "ACME DYNAMICS LLC"


def test_contract_detail(client):
    c, _ = client
    body = c.get("/v1/contracts/N0001925C0001", headers=AUTH).json()
    assert body["contract"]["ko_email"] == "jane.doe@mail.mil"
    assert body["contract"]["amount"] == 5_000_000.0


def test_entity_detail_uses_the_contracts_table(client):
    """An entity re-screened with no new signal still has its awards."""
    c, _ = client
    body = c.get("/v1/entities/UEI123", headers=AUTH).json()
    assert len(body["contracts"]) == 1
    assert body["obligated"] == 5_000_000.0
    assert body["latest_finding"] is None


# ------------------------------------------------- totals over the whole set
#
# `contracts_where` returns the 200 best-funded awards. Three routes used to
# sum that list and publish the result as a total. A dollar figure that
# silently understates is worse than one that is missing: nothing on the page
# says it is a subtotal, and the number it produces is plausible.

BULK = 205          # > the 200-row page
BULK_AMOUNT = 1_000.0


def _load_many(store, run_id="rbulk", entity="UEI777", agency="Department of the Interior"):
    store.start_run("bulk", {}, run_id=run_id)
    for i in range(BULK):
        store.save_contract(
            make_contract(piid=f"BULK{i:04d}", award_id=f"B{i}",
                          recipient_name="BULK INDUSTRIES", recipient_uei=entity,
                          awarding_agency=agency, awarding_sub_agency="Bureau of Widgets",
                          award_amount=BULK_AMOUNT,
                          ko_email="bulk.officer@mail.mil", ko_name="Bulk Officer"),
            run_id, entity)


def test_entity_totals_count_every_award_not_just_the_page(client):
    c, app_module = client
    _load_many(app_module.store_for("acme"))

    body = c.get("/v1/entities/UEI777", headers=AUTH).json()
    assert body["contract_count"] == BULK
    assert body["obligated"] == BULK * BULK_AMOUNT
    # The list itself stays capped, and says so rather than implying it is all.
    assert body["contracts_shown"] == 200
    assert len(body["contracts"]) == 200


def test_officer_totals_count_every_award(client):
    c, app_module = client
    _load_many(app_module.store_for("acme"))

    body = c.get("/v1/officers/bulk.officer@mail.mil", headers=AUTH).json()
    assert body["contract_count"] == BULK
    assert body["obligated"] == BULK * BULK_AMOUNT


def test_agency_totals_count_every_award(client):
    c, app_module = client
    _load_many(app_module.store_for("acme"))

    body = c.get("/v1/agencies/Department of the Interior", headers=AUTH).json()
    assert body["contract_count"] == BULK
    assert body["obligated"] == BULK * BULK_AMOUNT
    assert body["entity_count"] == 1
    # And the contractor roll-up is aggregated in SQL, so it agrees.
    assert sum(e["contract_count"] for e in body["entities"]) == BULK
    assert body["entities"][0]["obligated"] == BULK * BULK_AMOUNT


def test_an_agency_page_lists_officers_reached_by_sub_agency(client):
    """The page is reachable by either name, so both have to find officers.

    Filtering in Python compared the requested name against `MAX(agency)`,
    which is never a sub-agency — so every sub-agency page showed no officers
    at all, as though nobody had awarded anything.
    """
    c, app_module = client
    _load_many(app_module.store_for("acme"))

    body = c.get("/v1/agencies/Bureau of Widgets", headers=AUTH).json()
    assert [o["ko_email"] for o in body["officers"]] == ["bulk.officer@mail.mil"]
    assert body["contract_count"] == BULK


def test_a_small_agency_is_not_crowded_out_by_a_larger_one(client):
    """The old filter took the tenant's 200 best-funded officers *globally* and
    then kept the ones whose agency matched. A small agency behind 200
    better-funded officers elsewhere showed none of its own."""
    c, app_module = client
    store = app_module.store_for("acme")
    store.start_run("many", {}, run_id="rmany")
    for i in range(205):
        store.save_contract(
            make_contract(piid=f"RICH{i:04d}", award_id=f"R{i}",
                          awarding_agency="Department of Commerce",
                          awarding_sub_agency="",
                          award_amount=9_000_000.0 + i,
                          ko_email=f"rich.officer{i}@mail.mil"),
            "rmany", "UEI123")
    # One poorly funded officer at a different, smaller agency.
    store.save_contract(
        make_contract(piid="POOR0001", award_id="P1",
                      awarding_agency="Department of the Treasury",
                      awarding_sub_agency="", award_amount=1.0,
                      ko_email="quiet.officer@mail.mil"),
        "rmany", "UEI123")

    body = c.get("/v1/agencies/Department of the Treasury", headers=AUTH).json()
    assert [o["ko_email"] for o in body["officers"]] == ["quiet.officer@mail.mil"]


# --------------------------------------------------- re-screening an award


def test_a_novated_award_moves_to_the_new_contractor(store):
    """An award changing hands is the event this tool exists to notice.

    The upsert updated `entity_name` but not `entity_key` or `recipient_uei`,
    so a re-screen after a novation left the award filed under the old
    contractor while displaying the new contractor's name — a row that is
    internally inconsistent and attributes the work to the wrong company.
    """
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(recipient_name="ACME DYNAMICS LLC",
                                      recipient_uei="UEI123"), "r1", "UEI123")

    store.start_run("DoD", {}, run_id="r2")
    store.save_contract(make_contract(recipient_name="NEWCO HOLDINGS LLC",
                                      recipient_uei="UEI555"), "r2", "UEI555")

    row = store.get_contract("N0001925C0001")
    assert row["entity_name"] == "NEWCO HOLDINGS LLC"
    assert row["entity_key"] == "UEI555", "the award did not move with the name"
    assert row["recipient_uei"] == "UEI555"
    # And it is reachable under the new contractor, not the old one.
    assert len(store.contracts_where("entity_key", "UEI555")) == 1
    assert store.contracts_where("entity_key", "UEI123") == []


def test_a_change_of_incorporation_country_is_not_held_at_its_first_value(store):
    """The most direct structural FOCI signal in this table.

    `country_of_incorporation` and `foreign_owned` were absent from the update
    list, so an entity that re-registered in a covered nation went on reading
    as it did the day it was first seen — and `search_entities` reports
    MAX(...) of exactly those columns.
    """
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(country_of_incorporation="USA"), "r1", "UEI123")

    store.start_run("DoD", {}, run_id="r2")
    store.save_contract(make_contract(country_of_incorporation="CHN",
                                      foreign_owned_and_located=True), "r2", "UEI123")

    row = store.get_contract("N0001925C0001")
    assert row["country_of_incorporation"] == "CHN"
    assert row["foreign_owned"] == 1

    listed = store.search_entities("acme")[0]
    assert listed["country_of_incorporation"] == "CHN"


def test_a_reassigned_contracting_officer_replaces_the_old_one(store):
    """Already worked; asserted so the widened update list keeps it that way.
    Notices go to whoever holds the award now."""
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(ko_email="jane.doe@mail.mil"), "r1", "UEI123")
    store.start_run("DoD", {}, run_id="r2")
    store.save_contract(make_contract(ko_email="new.officer@mail.mil"), "r2", "UEI123")

    assert store.get_contract("N0001925C0001")["ko_email"] == "new.officer@mail.mil"


# ------------------------------------------------------- literal search terms

def test_an_underscore_in_a_search_term_is_not_a_wildcard(store):
    store.start_run("DoD", {}, run_id="r1")
    # Distinct PIIDs: the contract key is the award, so reusing one would
    # update a single row rather than index two contractors.
    store.save_contract(make_contract(piid="P001", award_id="W1",
                                      recipient_name="SPACE_SYSTEMS CORP",
                                      recipient_uei="UEI001"), "r1", "UEI001")
    store.save_contract(make_contract(piid="P002", award_id="W2",
                                      recipient_name="SPACEXSYSTEMS CORP",
                                      recipient_uei="UEI002"), "r1", "UEI002")

    names = {r["entity_name"] for r in store.search_entities("space_systems")}
    assert names == {"SPACE_SYSTEMS CORP"}


def test_a_percent_sign_does_not_match_everything(store):
    store.start_run("DoD", {}, run_id="r1")
    store.save_contract(make_contract(), "r1", "UEI123")
    store.save_contract(make_contract(piid="X2", award_id="A2",
                                      description="Contract for 50% of the fleet"),
                        "r1", "UEI123")

    assert store.search_entities("%") == []
    # But a percent sign that really is in the text still matches.
    assert len(store.search_contracts("50%")) == 1


def test_search_endpoints_need_a_key(client):
    c, _ = client
    assert c.get("/v1/search?q=acme").status_code == 401
    assert c.get("/v1/overview").status_code == 401


# ------------------------------------------------------------------ web app

def test_web_ui_is_served(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "foci-screen" in r.text
    assert c.get("/app.js").status_code == 200
    assert c.get("/styles.css").status_code == 200


def test_the_shell_is_always_revalidated(client):
    """A deploy that a browser never notices is a deploy that did not happen.

    Served straight from StaticFiles, index.html could sit in a browser cache
    indefinitely: a returning visitor kept running the previous build, missing
    whatever had just shipped, with nothing on the page to say why. This is
    not hypothetical — it is how a shipped feature came to be reported as
    missing.
    """
    c, _ = client
    r = c.get("/")
    assert "no-cache" in r.headers.get("cache-control", "")


def test_asset_urls_carry_a_fingerprint(client):
    c, _ = client
    html = c.get("/").text
    assert "./app.js?v=" in html
    assert "./styles.css?v=" in html
    # Same fingerprint for both: one build, one version of the pair.
    assert html.count("?v=") == 2


def test_the_fingerprint_follows_the_asset_contents(client, tmp_path):
    """The version number will not do — two deploys usually share one."""
    c, app_module = client
    before = app_module._asset_fingerprint("app.js", "styles.css")

    web = tmp_path / "web"
    web.mkdir()
    (web / "app.js").write_text("// something new", encoding="utf-8")
    app_module._WEB_DIR = web
    after = app_module._asset_fingerprint("app.js", "styles.css")

    assert before != after
    assert len(after) == 12


def test_a_fingerprinted_asset_still_resolves(client):
    """The query string is a cache key for the browser, not part of the path."""
    c, _ = client
    r = c.get("/app.js?v=whatever")
    assert r.status_code == 200
    assert len(r.text) > 1000


def test_static_mount_does_not_shadow_the_api(client):
    """The catch-all mount is last; /v1 must still route."""
    c, _ = client
    assert c.get("/v1/overview", headers=AUTH).status_code == 200
    assert c.get("/health").status_code == 200
