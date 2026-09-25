"""FastAPI surface over the screening core.

Nothing here does screening work. Routes validate, enqueue, and read back —
the analysis lives in `pipeline` and `risk`, unchanged from the CLI. A screen
takes minutes, so `POST /v1/screens` returns a `run_id` and the caller polls.

The one route with teeth is notice approval. Everything else reports on public
records; `POST /v1/notices/{id}/approve` is the step that decides an email about
a named company reaches a federal official, so it is explicit, attributed to a
reviewer, and never implied by anything else.
"""
from __future__ import annotations

import json
import logging
import threading
from importlib import metadata
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import get_config
from ..jobs import JobQueue
from ..notify import render as render_notice
from ..store import DRIVER_MISSING, Store, annotate_diff, postgres_driver_available
from . import auth
from .schemas import (
    IdentityDecision,
    NoticeDecision,
    RuleSetting,
    ScreenRequest,
    SignalDisposition,
    WatchlistRequest,
)

log = logging.getLogger("foci.api")

cfg = get_config()
keymap = auth.parse_keys(cfg.api_keys)
require_tenant = auth.make_dependency(keymap)
queue = JobQueue(cfg)

app = FastAPI(
    title="foci-screen",
    version="0.2.0",
    description="Screens federal contract awards and contractor disclosures for "
                "Foreign Ownership, Control or Influence and intellectual-property risk.",
)

if cfg.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# One store per tenant, shared across requests. Store serialises its own access,
# and a connection per request would spend more time connecting than querying.
_stores: dict[str, Store] = {}
_stores_lock = threading.Lock()


def store_for(tenant: str) -> Store:
    with _stores_lock:
        if tenant not in _stores:
            _stores[tenant] = Store(cfg.dsn, tenant_id=tenant)
        return _stores[tenant]


def tenant_store(tenant: str = Depends(require_tenant)) -> Store:
    return store_for(tenant)


def _json(raw: str | None) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}


# ------------------------------------------------------------------- meta

try:
    VERSION = metadata.version("foci-screen")
except metadata.PackageNotFoundError:   # running from a source tree, not installed
    VERSION = "unknown"


def storage_is_ephemeral() -> bool:
    """True when what is written here does not survive a restart.

    A caller cannot tell an empty database from a discarded one, and the
    difference decides whether the answer is "run a screen" or "this will
    happen again every time".
    """
    return not cfg.dsn.startswith("postgres") and bool(cfg.managed_host)


def deployment_warnings() -> list[str]:
    """Configuration that will lose data or silently do nothing.

    None of these stop the service answering requests, which is exactly why
    they need saying: a deployment holding its data on a disk that is about to
    be discarded looks identical, from the outside, to one that is fine.
    """
    warnings: list[str] = []
    host = cfg.managed_host
    on_sqlite = not cfg.dsn.startswith("postgres")

    if not keymap:
        warnings.append(
            "FOCI_API_KEYS is not set: every authenticated route returns 503 and "
            "the web interface will show nothing.")
    if storage_is_ephemeral():
        warnings.append(
            f"This database will not survive a restart. It is SQLite on {host}'s "
            f"own filesystem, which is replaced on every deploy — and on an idle "
            f"instance that spins down and back up. Anything screened into it is "
            f"gone, including the snapshots change detection compares against. "
            f"Two fixes: attach a persistent disk and point FOCI_DB at it "
            f"(simplest — one service, needs a paid instance type), or create a "
            f"Postgres instance and set DATABASE_URL (works on any plan). "
            f"DEPLOY.md has both.")
    if queue.backend == "thread" and host:
        warnings.append(
            f"No REDIS_URL on {host}: screens would run inside the web process and "
            f"die with it mid-run. Add a Redis instance and a worker service.")
    if not on_sqlite and not postgres_driver_available():
        warnings.append(DRIVER_MISSING)
    return warnings


INTERRUPTED_NOTE = (
    "The process running this screen stopped before it finished — a deploy, a "
    "restart, or an instance spinning down while idle. Nothing was recorded "
    "beyond the point it reached. Start another screen.")


def reap_interrupted_runs() -> int:
    """Close out runs left `running` by a process that no longer exists.

    Only when screening happens in this process. With a queue, a run marked
    running may be owned by a worker that is alive and working, and closing it
    from here would report a screen as interrupted while it is still going.
    """
    if queue.backend != "thread":
        return 0
    reaped = 0
    for tenant in sorted(set(keymap.values()) | {auth.DEFAULT_TENANT}):
        reaped += store_for(tenant).reap_interrupted_runs(INTERRUPTED_NOTE)
    return reaped


try:
    _reaped = reap_interrupted_runs()
    if _reaped:
        log.warning("Closed %d screen(s) left running by a previous process.", _reaped)
except Exception as exc:   # noqa: BLE001 - never let tidying up stop the boot
    # This is the first thing that touches the database, so a bad DATABASE_URL
    # or a missing driver surfaces here. Crashing would take down the health
    # endpoint and the banner that explain exactly that, leaving an operator
    # with a service that will not start and no page to read.
    log.error("Could not sweep interrupted screens: %s", exc)

for _warning in deployment_warnings():
    # logging may not be configured yet under some servers, so print as well.
    log.error(_warning)
    print(f"\n*** {_warning}\n", flush=True)
if not keymap:
    # The long form says how to set it; the health warning stays short enough
    # to read in a banner.
    print(f"*** {auth.STARTUP_WARNING}\n", flush=True)


@app.get("/health", tags=["meta"])
def health() -> dict:
    """Unauthenticated liveness probe. Says nothing about the data.

    Carries the version so a deploy can be confirmed as the build you meant to
    ship, and any configuration warnings, so a misconfigured deployment can be
    diagnosed from outside without a key.
    """
    return {"status": "ok", "version": VERSION, "queue": queue.backend,
            "database": "postgres" if cfg.dsn.startswith("postgres") else "sqlite",
            "authenticated": bool(keymap),
            "ephemeral_storage": storage_is_ephemeral(),
            "warnings": deployment_warnings()}


@app.get("/v1/config", tags=["meta"])
def configuration(tenant: str = Depends(require_tenant)) -> dict:
    """Which connectors this deployment can actually run."""
    return {"tenant": tenant, "connectors": cfg.availability(),
            "queue": queue.backend,
            "gmail": {"enabled": cfg.gmail_enabled, "send": cfg.gmail_send,
                      "redirect_to": cfg.email_redirect_to or None}}


# ---------------------------------------------------------------- screens

@app.post("/v1/screens", status_code=202, tags=["screens"])
def create_screen(body: ScreenRequest, tenant: str = Depends(require_tenant)) -> dict:
    store = store_for(tenant)
    options = body.model_dump()
    run_id = store.start_run(body.agency, options, status="queued")
    queue.enqueue(tenant, run_id, options)
    return {"run_id": run_id, "status": "queued", "backend": queue.backend}


@app.get("/v1/screens", tags=["screens"])
def list_screens(limit: int = Query(25, ge=1, le=200),
                 store: Store = Depends(tenant_store)) -> dict:
    return {"runs": store.recent_runs(limit=limit)}


@app.get("/v1/screens/{run_id}", tags=["screens"])
def get_screen(run_id: str, store: Store = Depends(tenant_store)) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "No such run.")
    payload = {
        "run_id": run["run_id"],
        "status": run["status"],
        "agency": run["agency"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "progress": run["progress"],
        "stats": _json(run["stats"]),
        "error": run["error"] or None,
    }
    if run["status"] == "complete":
        payload["findings"] = store.findings_for_run(run_id)
    return payload


# --------------------------------------------------------------- findings

@app.get("/v1/findings", tags=["findings"])
def list_findings(severity: str = Query("", pattern="^(|info|low|medium|high|critical)$"),
                  since: str = "", entity_key: str = "",
                  limit: int = Query(50, ge=1, le=500),
                  store: Store = Depends(tenant_store)) -> dict:
    """The change feed. Filter by severity and date; newest first."""
    return {"findings": store.search_findings(
        severity=severity, since=since, entity_key=entity_key, limit=limit)}


@app.get("/v1/entities/{entity_key}", tags=["findings"])
def get_entity(entity_key: str, store: Store = Depends(tenant_store)) -> dict:
    key = entity_key.upper()
    contracts = store.contracts_where("entity_key", key)
    findings = store.search_findings(entity_key=key, limit=1)
    if not contracts and not findings:
        raise HTTPException(404, "Nothing recorded for that entity.")

    latest = findings[0] if findings else None
    totals = store.contract_totals("entity_key", key)
    return {
        "entity": (latest or {}).get("entity", {"name": contracts[0]["entity_name"],
                                                "uei": contracts[0]["recipient_uei"]}
                                     if contracts else {}),
        # From the contracts table, not the finding payload: an entity screened
        # again with no new signal still has its awards.
        "contracts": contracts,
        # Over every award, not over the page above — see `contract_totals`.
        "obligated": totals["obligated"],
        "contract_count": totals["contract_count"],
        "contracts_shown": len(contracts),
        "latest_finding": latest,
        "history": store.entity_history(key),
        # What moved in the award record since the last screen. The contracts
        # rows above hold only the present state.
        "record_changes": store.contract_changes(entity_key=key, limit=50),
    }


@app.get("/v1/entities/{entity_key}/timeline", tags=["findings"])
def entity_timeline(entity_key: str, limit: int = Query(50, ge=1, le=200),
                    store: Store = Depends(tenant_store)) -> dict:
    """Severity over time — what changed, and when."""
    return {"entity_key": entity_key.upper(),
            "timeline": store.entity_history(entity_key, limit=limit)}


# ----------------------------------------------------------------- search

@app.get("/v1/search", tags=["search"])
def search(q: str = Query("", max_length=200),
           kind: str = Query("all", pattern="^(all|entity|contract|officer|agency)$"),
           limit: int = Query(10, ge=1, le=100),
           store: Store = Depends(tenant_store)) -> dict:
    """One box over contractors, awards, contracting officers and agencies.

    An empty `q` is a browse rather than an error: it returns the largest of
    each kind, which is what an analyst opening the page for the first time
    wants to see.
    """
    q = q.strip()
    if kind == "entity":
        return {"query": q, "entities": store.search_entities(q, limit)}
    if kind == "contract":
        return {"query": q, "contracts": store.search_contracts(q, limit)}
    if kind == "officer":
        return {"query": q, "officers": store.search_officers(q, limit)}
    if kind == "agency":
        return {"query": q, "agencies": store.search_agencies(q, limit)}

    if not q:
        return {"query": q, "entities": store.search_entities("", limit),
                "contracts": [], "officers": store.search_officers("", limit),
                "agencies": store.search_agencies("", limit)}
    return {"query": q, **store.search_all(q, limit)}


@app.get("/v1/overview", tags=["search"])
def overview(store: Store = Depends(tenant_store)) -> dict:
    """Everything the dashboard charts, in one call."""
    return {
        "totals": store.totals(),
        "severity": store.severity_counts(),
        "categories": store.signal_category_counts(),
        "findings_by_day": store.findings_by_day(),
        "top_entities": store.search_entities("", limit=8),
        "recent_findings": store.search_findings(limit=8),
    }


@app.get("/v1/contracts/{contract_key:path}", tags=["search"])
def get_contract(contract_key: str, store: Store = Depends(tenant_store)) -> dict:
    contract = store.get_contract(contract_key)
    if contract is None:
        raise HTTPException(404, "No such contract.")
    findings = store.search_findings(entity_key=contract["entity_key"], limit=1)
    return {"contract": contract,
            "entity_finding": findings[0] if findings else None}


@app.get("/v1/officers", tags=["search"])
def list_officers(q: str = "", limit: int = Query(50, ge=1, le=200),
                  store: Store = Depends(tenant_store)) -> dict:
    return {"officers": store.search_officers(q, limit)}


@app.get("/v1/officers/{email}", tags=["search"])
def officer_profile(email: str, store: Store = Depends(tenant_store)) -> dict:
    """What one contracting officer holds — the view that decides who to notify."""
    contracts = store.contracts_where("ko_email", email)
    if not contracts:
        raise HTTPException(404, "No contracts recorded for that officer.")
    entity_keys = {c["entity_key"] for c in contracts}
    findings = [f for k in entity_keys
                for f in store.search_findings(entity_key=k, limit=1)]
    return {
        "officer": {
            "email": email,
            "name": contracts[0].get("ko_name"),
            "confidence": contracts[0].get("ko_confidence"),
            "source": contracts[0].get("ko_source"),
            "agency": contracts[0].get("agency"),
        },
        **store.contract_totals("ko_email", email),
        "contracts": contracts,
        "contracts_shown": len(contracts),
        "findings": sorted(findings, key=lambda f: -(f.get("total_score") or 0)),
    }


@app.get("/v1/agencies", tags=["search"])
def list_agencies(q: str = "", limit: int = Query(50, ge=1, le=200),
                  store: Store = Depends(tenant_store)) -> dict:
    return {"agencies": store.search_agencies(q, limit)}


@app.get("/v1/agencies/{name:path}", tags=["search"])
def agency_profile(name: str, store: Store = Depends(tenant_store)) -> dict:
    contracts = store.contracts_where("agency", name)
    if not contracts:
        contracts = store.contracts_where("sub_agency", name)
    if not contracts:
        raise HTTPException(404, "No contracts recorded for that agency.")

    # Totals, contractors and officers all come from SQL over the whole agency.
    # Aggregating the 200-row page above understated every figure on the page,
    # and the officer list was filtered from the tenant's 200 best-funded
    # officers on a column that holds only one of an officer's agencies.
    return {
        "agency": name,
        **store.agency_totals(name),
        "contracts_shown": len(contracts),
        "entities": store.entities_for_agency(name),
        "officers": store.officers_for_agency(name),
    }


# --------------------------------------------------------------- identity

@app.get("/v1/identity", tags=["identity"])
def list_identities(status: str = Query("", pattern="^(|auto|confirmed|rejected)$"),
                    limit: int = Query(200, ge=1, le=500),
                    store: Store = Depends(tenant_store)) -> dict:
    """Resolved identities, least confident first — the review queue."""
    return {"links": store.entity_links(status=status, limit=limit)}


@app.get("/v1/entities/{entity_key}/identity", tags=["identity"])
def get_identity(entity_key: str, store: Store = Depends(tenant_store)) -> dict:
    link = store.get_entity_link(entity_key)
    if link is None:
        raise HTTPException(404, "No identity resolution recorded for that entity.")
    return link


@app.post("/v1/entities/{entity_key}/identity", tags=["identity"])
def set_identity(entity_key: str, body: IdentityDecision,
                 tenant: str = Depends(require_tenant)) -> dict:
    """Confirm, correct or reject which SEC registrant a contractor is.

    This is the highest-leverage correction in the tool. EDGAR full-text search
    is constrained by CIK, so a wrong mapping does not return nothing — it
    returns another registrant's exhibits under this contractor's name. A
    decision here is remembered and outranks the name matcher permanently.
    """
    store = store_for(tenant)
    known_cik = (store.get_entity_link(entity_key) or {}).get("cik")
    if body.status == "confirmed" and not (body.cik or known_cik):
        raise HTTPException(
            422, "Confirming an identity needs a CIK — either already resolved for "
                 "this entity or supplied here.")
    return store.set_entity_link(
        entity_key, status=body.status, cik=body.cik, note=body.note,
        matched_title=body.matched_title,
        decided_by=body.decided_by.strip() or f"api-key:{tenant}")


# ------------------------------------------------------------ dispositions

@app.post("/v1/dispositions", tags=["dispositions"], status_code=201)
def record_disposition(body: SignalDisposition,
                       tenant: str = Depends(require_tenant)) -> dict:
    """Record whether a signal was right.

    Rule weights were set by judgement and have never been measured against
    anything. This is the measurement: every verdict here is one labelled
    example, and `GET /v1/rules/precision` is what they add up to.
    """
    store = store_for(tenant)
    store.record_disposition(
        signal_id=body.signal_id, entity_key=body.entity_key, rule_id=body.rule_id,
        verdict=body.verdict, category=body.category, severity=body.severity,
        note=body.note, decided_by=body.decided_by.strip() or f"api-key:{tenant}",
        run_id=body.run_id)
    return {"signal_id": body.signal_id, "verdict": body.verdict}


@app.get("/v1/rules", tags=["rules"])
def list_rules(store: Store = Depends(tenant_store)) -> dict:
    """Every rule seen here, with its precision and its tenant settings.

    One view rather than two, because the decision is one decision: a rule's
    measured precision is the reason to change its weight or switch it off.
    """
    precision = {r["rule_id"]: r for r in store.rule_precision()}
    settings = store.rule_settings()
    catalogue = store.rules_seen()

    rows = []
    for rule_id in sorted(set(catalogue) | set(precision) | set(settings)):
        measured = precision.get(rule_id, {})
        override = settings.get(rule_id)
        rows.append({
            "rule_id": rule_id,
            "category": measured.get("category") or catalogue.get(rule_id, ""),
            "enabled": override["enabled"] if override else True,
            "weight": override["weight"] if override else 1.0,
            "overridden": override is not None,
            "note": (override or {}).get("note", ""),
            "true_positive": measured.get("true_positive", 0),
            "false_positive": measured.get("false_positive", 0),
            "unclear": measured.get("unclear", 0),
            "precision": measured.get("precision"),
        })
    return {"rules": rows}


@app.put("/v1/rules/{rule_id}", tags=["rules"])
def set_rule(rule_id: str, body: RuleSetting,
             tenant: str = Depends(require_tenant)) -> dict:
    """Override a rule for this tenant. Takes effect on the next screen."""
    store = store_for(tenant)
    store.set_rule_setting(
        rule_id, enabled=body.enabled, weight=body.weight, note=body.note,
        decided_by=body.decided_by.strip() or f"api-key:{tenant}")
    return {"rule_id": rule_id, **store.rule_settings().get(rule_id, {})}


@app.delete("/v1/rules/{rule_id}", status_code=204, tags=["rules"])
def clear_rule(rule_id: str, tenant: str = Depends(require_tenant)) -> Response:
    """Drop the override and go back to the engine default."""
    store_for(tenant).clear_rule_setting(rule_id)
    return Response(status_code=204)


@app.get("/v1/rules/precision", tags=["dispositions"])
def rule_precision(store: Store = Depends(tenant_store)) -> dict:
    """Per-rule precision from reviewer verdicts, worst first.

    A rule with no verdicts has `precision: null` — unmeasured, which is not
    the same as perfect, and the UI says so rather than showing a hopeful 100%.
    """
    rules = store.rule_precision()
    judged = sum(r["true_positive"] + r["false_positive"] for r in rules)
    return {
        "rules": rules,
        "totals": {
            "rules_with_verdicts": len(rules),
            "verdicts": sum(r["reviewed"] for r in rules),
            "judged": judged,
            "overall_precision": (
                round(sum(r["true_positive"] for r in rules) / judged, 3)
                if judged else None),
        },
    }


@app.get("/v1/entities/{entity_key}/dispositions", tags=["dispositions"])
def entity_dispositions(entity_key: str,
                        store: Store = Depends(tenant_store)) -> dict:
    return {"entity_key": entity_key.upper(),
            "dispositions": store.dispositions_for_entity(entity_key)}


# -------------------------------------------------------------- documents

@app.get("/v1/documents", tags=["documents"])
def entity_documents(entity_key: str = Query(..., min_length=1),
                     store: Store = Depends(tenant_store)) -> dict:
    """Source documents gathered while screening one contractor."""
    return {"entity_key": entity_key.upper(),
            "documents": store.documents_for_entity(entity_key)}


# source and key are query parameters, not path segments: a document key is
# `web:lockheedmartin.com/news`, which contains both a colon and slashes.
@app.get("/v1/documents/history", tags=["documents"])
def document_history(source: str, key: str, limit: int = Query(50, ge=1, le=200),
                     store: Store = Depends(tenant_store)) -> dict:
    revisions = store.document_timeline(source, key, limit=limit)
    if not revisions:
        raise HTTPException(404, "No revisions recorded for that document.")
    return {"source": source, "document_key": key, "revisions": revisions}


@app.get("/v1/documents/diff", tags=["documents"])
def document_diff(source: str, key: str, from_sha: str = "", to_sha: str = "",
                  entity_key: str = "",
                  store: Store = Depends(tenant_store)) -> dict:
    """What changed between two revisions. Defaults to the two most recent.

    This is the screen a CLI cannot do well, and the one the change-detection
    design exists to serve: not "this company mentions the Cayman Islands" but
    "this paragraph appeared on their site last Tuesday".

    Pass `entity_key` to have the lines a rule actually fired on marked, so the
    diff and the finding are joined up rather than left side by side.
    """
    result = store.document_diff(source, key, from_sha=from_sha, to_sha=to_sha)
    if "error" in result:
        raise HTTPException(404, result["error"])

    if entity_key:
        signals = store.signals_for_document(entity_key, key)
        result["lines"] = annotate_diff(result["lines"], signals)
        result["signals"] = signals
    return result


# -------------------------------------------------------------- watchlists

@app.post("/v1/watchlists", status_code=201, tags=["watchlists"])
def create_watchlist(body: WatchlistRequest,
                     store: Store = Depends(tenant_store)) -> dict:
    watchlist_id = store.create_watchlist(body.name, body.screen.model_dump())
    return {"watchlist_id": watchlist_id, "name": body.name, "active": True}


@app.get("/v1/watchlists", tags=["watchlists"])
def list_watchlists(store: Store = Depends(tenant_store)) -> dict:
    rows = store.list_watchlists()
    for r in rows:
        r["params"] = _json(r.get("params"))
    return {"watchlists": rows}


@app.post("/v1/watchlists/{watchlist_id}/run", status_code=202, tags=["watchlists"])
def run_watchlist(watchlist_id: str, tenant: str = Depends(require_tenant)) -> dict:
    store = store_for(tenant)
    rows = [w for w in store.list_watchlists() if w["watchlist_id"] == watchlist_id]
    if not rows:
        raise HTTPException(404, "No such watchlist.")
    options = _json(rows[0]["params"])
    run_id = store.start_run(options.get("agency", ""), options, status="queued")
    store.mark_watchlist_run(watchlist_id, run_id)
    queue.enqueue(tenant, run_id, options)
    return {"run_id": run_id, "status": "queued"}


@app.delete("/v1/watchlists/{watchlist_id}", tags=["watchlists"])
def deactivate_watchlist(watchlist_id: str,
                         store: Store = Depends(tenant_store)) -> dict:
    if not store.set_watchlist_active(watchlist_id, False):
        raise HTTPException(404, "No such watchlist.")
    return {"watchlist_id": watchlist_id, "active": False}


# ----------------------------------------------------------------- notices

NOTICE_STATUS = "^(|pending|approved|rejected|drafted|sent|suppressed)$"


@app.get("/v1/notices", tags=["notices"])
def list_notices(status: str = Query("", pattern=NOTICE_STATUS),
                 limit: int = Query(50, ge=1, le=200),
                 store: Store = Depends(tenant_store)) -> dict:
    notices = store.list_notices(status=status, limit=limit)
    return {"notices": [{**n, "edits": store.notice_edits(n)} for n in notices]}


# Declared before /v1/notices/{notice_id}: routes match in definition order, and
# the bare parameter would otherwise capture "abc123.eml" as an id and 404.
@app.get("/v1/notices/{notice_id}.eml", tags=["notices"])
def download_notice(notice_id: str, store: Store = Depends(tenant_store)) -> Response:
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    msg = render_notice.message_from_notice(notice, sender=cfg.gmail_sender)
    return Response(
        content=bytes(msg), media_type="message/rfc822",
        headers={"Content-Disposition":
                 f'attachment; filename="notice_{notice_id}.eml"'})


@app.get("/v1/notices/{notice_id}", tags=["notices"])
def get_notice(notice_id: str, store: Store = Depends(tenant_store)) -> dict:
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    return {**notice, "edits": store.notice_edits(notice)}


def _decider(body: NoticeDecision, tenant: str) -> str:
    """Who to record as having decided.

    Keys are shared per tenant, so without a name in the request the honest
    attribution is the key, labelled as one. The bare tenant id was used before,
    and a tenant called "default" produced "decided by default" — which reads as
    an automatic approval on exactly the record meant to prove a person looked.
    """
    return body.decided_by.strip() or f"api-key:{tenant}"


def _decision_error(outcome: str) -> None:
    if outcome == "not_found":
        raise HTTPException(404, "No such notice.")
    if outcome == "already_decided":
        raise HTTPException(
            409, "This notice has already been decided. Decisions are final so a "
                 "rejection cannot be silently overturned.")


@app.post("/v1/notices/{notice_id}/approve", tags=["notices"])
def approve_notice(notice_id: str, body: NoticeDecision,
                   tenant: str = Depends(require_tenant)) -> dict:
    """Approve a notice for delivery, optionally with an edited body.

    Approval records a decision; it does not itself send. Delivery still depends
    on `GMAIL_ENABLED` and `GMAIL_SEND`, and while `FOCI_EMAIL_REDIRECT_TO` is
    set every approved notice goes to that address instead of the officer.

    An edit may change anything except the limitations statement. That paragraph
    is what makes the notice a screening result rather than an assertion about a
    named company, so a body without it is refused rather than sent.
    """
    store = store_for(tenant)
    notice = store.get_notice(notice_id)
    if notice is None:
        raise HTTPException(404, "No such notice.")
    if not (notice.get("recipient") or "").strip():
        raise HTTPException(
            409, "This notice has no resolved contracting officer; it cannot be "
                 "addressed. Download the .eml and route it manually.")
    if body.body_text and not render_notice.has_disclaimer(body.body_text):
        raise HTTPException(
            422, "The edited notice no longer contains the limitations statement. "
                 "It can be reworded around, but not removed: without it the notice "
                 "reads as a finding against the contractor rather than a screen.")

    _decision_error(store.decide_notice(
        notice_id, "approved", decided_by=_decider(body, tenant),
        note=body.note, body_text=body.body_text))
    decided = store.get_notice(notice_id)
    return {"notice_id": notice_id, "status": "approved",
            "edited": bool(decided and decided.get("original_body_text")),
            "delivery": _delivery_state()}


@app.post("/v1/notices/{notice_id}/reject", tags=["notices"])
def reject_notice(notice_id: str, body: NoticeDecision,
                  tenant: str = Depends(require_tenant)) -> dict:
    """Reject a notice.

    Worth capturing the reason: rejections are the only labelled false-positive
    data this tool ever gets, and rule weights cannot be tuned without them.
    """
    store = store_for(tenant)
    _decision_error(store.decide_notice(
        notice_id, "rejected", decided_by=_decider(body, tenant), note=body.note))
    return {"notice_id": notice_id, "status": "rejected"}


def _delivery_state() -> dict:
    if not cfg.gmail_enabled:
        return {"mode": "render_only",
                "detail": "GMAIL_ENABLED is false; download the .eml to route it."}
    if cfg.email_redirect_to:
        return {"mode": "redirected", "detail": f"All mail goes to {cfg.email_redirect_to}."}
    return {"mode": "send" if cfg.gmail_send else "draft",
            "detail": "Gmail drafts are created for review."
                      if not cfg.gmail_send else "Approved notices are sent."}


# ---------------------------------------------------------------------- web
# Mounted last: FastAPI matches routes in declaration order, so every /v1 route
# above wins before this catch-all sees the request.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
else:  # pragma: no cover - only if the package was built without web assets
    log.warning("web assets missing at %s; API only", _WEB_DIR)
