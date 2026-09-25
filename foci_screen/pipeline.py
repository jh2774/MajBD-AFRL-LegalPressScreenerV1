"""Orchestration: contracts -> entities -> evidence -> changes -> findings.

The pipeline is a plain synchronous function on purpose. Everything it needs is
injected (config, store, http), so the same code path serves the CLI today and
a FastAPI request handler or a queue worker later without modification.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .connectors.fpds import FPDSConnector
from .connectors.registries import IAPDConnector, OFACConnector, SAMConnector, USPTOConnector
from .connectors.sec_edgar import EdgarConnector
from .connectors.usaspending import USASpendingConnector, looks_like_an_individual
from .connectors.webwatch import WebWatchConnector
from .models import Change, Contract, Document, Entity, Finding
from .risk import engine
from .store import Store

log = logging.getLogger("foci.pipeline")


@dataclass
class ScreenOptions:
    agency: str
    sub_agency: str = ""
    months_back: int = 12
    max_awards: int = 25
    max_entities: int = 5
    keyword: str = ""
    include_idv: bool = False
    # Subcontractors to screen on top of the primes, newest subaward first.
    # Off by default: it is extra requests and a different evidence quality.
    max_subaward_entities: int = 0
    domains: dict[str, str] = field(default_factory=dict)   # entity name -> domain
    fetch_filing_bodies: bool = True
    min_severity: str = "low"
    skip_web: bool = False


@dataclass
class ScreenResult:
    run_id: str
    options: ScreenOptions
    contracts: list[Contract] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class Screener:
    def __init__(self, config, http, store: Store, browser=None) -> None:
        self.cfg = config
        self.http = http
        self.store = store
        self.usaspending = USASpendingConnector(http)
        self.fpds = FPDSConnector(http)
        self.edgar = EdgarConnector(http)
        self.iapd = IAPDConnector(http)
        self.ofac = OFACConnector(http)
        self.uspto = USPTOConnector(http, config.uspto_api_key)
        self.sam = SAMConnector(http, config.sam_api_key)
        self.web = WebWatchConnector(http, browser=browser)

    # ------------------------------------------------------------------ run
    def run(self, opts: ScreenOptions, progress=lambda msg: None,
            run_id: str = "") -> ScreenResult:
        # A queued run already has its row — the API created it so it could
        # return an id before the work started. When it did, the caller owns the
        # run's lifecycle and this method must not close it out.
        owns_run = not run_id
        if owns_run:
            run_id = self.store.start_run(opts.agency, opts.__dict__)
        result = ScreenResult(run_id=run_id, options=opts)

        # --- step 1: contracts ------------------------------------------
        progress(f"Searching USAspending for {opts.agency} awards "
                 f"(last {opts.months_back} months)...")
        contracts = self.usaspending.search_awards(
            opts.agency, months_back=opts.months_back, limit=opts.max_awards,
            sub_agency=opts.sub_agency, include_idv=opts.include_idv,
            keyword=opts.keyword)
        if not contracts:
            result.notes.append(
                f"No awards returned for '{opts.agency}'. Check the agency name "
                f"against `foci-screen agencies`.")
            if owns_run:
                self.store.finish_run(run_id)
            return result
        progress(f"  {len(contracts)} award(s) found.")

        # --- group by contractor before enriching (saves duplicate calls) --
        by_entity: dict[str, list[Contract]] = {}
        for c in contracts:
            key = (c.recipient_uei or c.recipient_name).upper()
            by_entity.setdefault(key, []).append(c)

        ranked = sorted(by_entity.items(),
                        key=lambda kv: -sum(c.award_amount for c in kv[1]))
        selected = ranked[:opts.max_entities]
        progress(f"  screening top {len(selected)} contractor(s) by obligated value.")

        selected += self._subaward_entities(opts, by_entity, result, progress)

        for _key, ent_contracts in selected:
            name = ent_contracts[0].recipient_name
            progress(f"\n[{name}]")
            for c in ent_contracts[:4]:
                progress(f"  enriching {c.piid} (USAspending detail + FPDS)...")
                self.usaspending.enrich(c)
                self.fpds.enrich(c)
            entity = self._build_entity(name, ent_contracts, opts)
            # Indexed for search regardless of whether a finding results — an
            # award with no risk signal is still the record that answers "what
            # else does this contracting officer hold?"
            record_changes: list[dict] = []
            for c in ent_contracts:
                record_changes += self.store.save_contract(c, run_id, entity.key())
            if record_changes:
                progress(f"  {len(record_changes)} change(s) to the award record "
                         f"since the last screen.")
            finding = self._screen_entity(entity, ent_contracts, run_id, opts,
                                          progress, record_changes)
            if finding:
                result.findings.append(finding)
            result.contracts.extend(ent_contracts)

        result.findings.sort(key=lambda f: -f.total_score)
        for f in result.findings:
            self.store.save_finding(f)
        result.stats = dict(self.http.stats)
        result.stats["entities_screened"] = len(selected)
        result.stats["awards_examined"] = len(contracts)

        result.notes.extend(self.coverage_notes())

        if owns_run:
            self.store.finish_run(run_id, stats=result.stats)
        return result

    def _subaward_entities(self, opts: ScreenOptions,
                           already: dict[str, list[Contract]],
                           result: ScreenResult,
                           progress) -> list[tuple[str, list[Contract]]]:
        """Subcontractors to screen alongside the primes.

        Ranked by recency rather than value. Subaward amounts are self-reported
        by the prime and are wrong often enough that sorting on them would put
        the worst data at the top — and the premise of screening subcontractors
        at all is that the interesting ones are small, which a dollar ranking
        hides.
        """
        if opts.max_subaward_entities <= 0:
            return []

        progress(f"\nSearching subawards under {opts.agency} primes...")
        subs = self.usaspending.search_subawards(
            opts.agency, months_back=opts.months_back,
            limit=max(opts.max_awards, opts.max_subaward_entities * 4),
            sub_agency=opts.sub_agency, keyword=opts.keyword)
        if not subs:
            progress("  no subawards returned.")
            return []

        grouped: dict[str, list[Contract]] = {}
        individuals: set[str] = set()
        for c in subs:
            key = (c.recipient_uei or c.recipient_name).upper()
            if not key or key in already:      # already screened as a prime
                continue
            if looks_like_an_individual(c.recipient_name):
                # Sole proprietors appear in subaward reporting. Screening a
                # named person, and writing to their customer's contracting
                # officer about them, is not what this tool is for.
                individuals.add(c.recipient_name)
                continue
            grouped.setdefault(key, []).append(c)

        picked = list(grouped.items())[:opts.max_subaward_entities]
        progress(f"  {len(subs)} subaward(s); screening {len(picked)} "
                 f"subcontractor(s) not already covered.")
        if individuals:
            progress(f"  {len(individuals)} subawardee(s) skipped as individuals.")
            result.notes.append(
                f"Skipped {len(individuals)} subawardee(s) whose recipient looks like a "
                f"person rather than a company: {', '.join(sorted(individuals))}. "
                f"Subaward reporting includes sole proprietors, and screening a named "
                f"individual — then writing to their customer's contracting officer "
                f"about them — is not something this tool does by default. The test is "
                f"a heuristic, so a company with a two-word name and no 'Inc' can land "
                f"here; they are listed above so that is visible rather than silent.")
        if picked:
            result.notes.append(
                f"{len(picked)} subcontractor(s) screened. Subaward values are "
                f"self-reported by the prime through FSRS and are frequently wrong, "
                f"so they are not used for ranking and should not be quoted as "
                f"obligated amounts. Any notice about a subcontractor is addressed "
                f"to the contracting officer on the prime contract, who is the "
                f"official able to act on it.")
        return picked

    def coverage_notes(self) -> list[str]:
        """Say so when a website was not fully read.

        A silent gap reads as "nothing found on their website", which is a
        different claim entirely — and a site that asked not to be crawled has not
        thereby said it has nothing to disclose.
        """
        notes: list[str] = []
        if self.web.skipped_js_hosts:
            hosts = ", ".join(sorted(self.web.skipped_js_hosts))
            notes.append(
                f"Could not read {hosts} — the page requires a browser and none "
                f"is installed. Install the 'browser' extra and run "
                f"`playwright install chromium` to cover investor-relations pages.")

        # Where the website is closed to us, say where else the same disclosure
        # tends to surface, rather than leaving a reviewer with only a gap.
        elsewhere = ("Material announcements usually also appear as SEC 8-K filings, "
                     "which this screen reads, and on the company's main newsroom.")

        if self.web.unreadable_hosts:
            hosts = ", ".join(sorted(self.web.unreadable_hosts))
            notes.append(
                f"Could not read {hosts} — the site did not serve readable content to "
                f"this tool's browser, which identifies itself. Investor-relations "
                f"platforms behind bot management typically refuse automated clients, "
                f"and the tool does not disguise itself to get past that. {elsewhere}")

        by_host: dict[str, dict[str, int]] = {}
        for url, reason in self.web.skipped_robots.items():
            host = urlparse(url).netloc.lower()
            reasons = by_host.setdefault(host, {})
            reasons[reason] = reasons.get(reason, 0) + 1
        for host, reasons in sorted(by_host.items()):
            detail = "; ".join(f"{n} page(s): {why}" for why, n in reasons.items())
            unreachable = any("unreachable" in why for why in reasons)
            notes.append(
                f"Did not read {host} ({detail}). "
                + (f"The host did not answer, so its crawling rules could not be "
                   f"read and are treated as a refusal. {elsewhere}" if unreachable
                   else "Coverage of that site is incomplete by the site's own "
                        "request — check it by hand if it matters."))
        return notes

    # -------------------------------------------------------------- entity
    def _build_entity(self, name: str, contracts: list[Contract],
                      opts: ScreenOptions) -> Entity:
        first = contracts[0]
        entity_key = (first.recipient_uei or name).upper().strip()
        cik, matched = self._resolve_identity(entity_key, name, first)
        domain = opts.domains.get(name) or opts.domains.get(name.upper(), "")
        countries = sorted({c.recipient_country for c in contracts if c.recipient_country}
                           | {c.country_of_incorporation for c in contracts
                              if c.country_of_incorporation})
        return Entity(
            name=name, uei=first.recipient_uei,
            parent_name=first.parent_recipient_name,
            parent_uei=first.parent_recipient_uei,
            cik=cik, aliases=[matched] if matched else [],
            domains=[domain] if domain else [],
            countries=countries,
            contracts=[c.piid or c.award_id for c in contracts])

    def _resolve_identity(self, entity_key: str, name: str,
                          first: Contract) -> tuple[str, str]:
        """Which SEC registrant this contractor is, preferring a human answer.

        A wrong CIK is the failure this project has already made once: EDGAR
        full-text search is constrained by CIK, so a bad mapping does not
        return nothing, it returns another company's exhibits under this
        company's name. Name similarity alone is rerun every screen and can
        land differently as the ticker file changes, so the answer is stored
        and a reviewer's verdict outranks it.
        """
        link = self.store.get_entity_link(entity_key)
        if link and link["status"] == "confirmed":
            return link["cik"] or "", link["matched_title"] or ""
        if link and link["status"] == "rejected":
            # Someone looked and said this contractor is not that registrant.
            # Re-deciding it by similarity would reintroduce the misattribution
            # they just removed.
            return "", ""

        cik, matched, score = self.edgar.resolve_cik_scored(
            first.parent_recipient_name or name)
        self.store.record_auto_link(
            entity_key, entity_name=name, uei=first.recipient_uei, cik=cik,
            matched_title=matched, confidence=score)
        return cik, matched if cik else ""

    # ------------------------------------------------------------ evidence
    def _gather(self, entity: Entity, opts: ScreenOptions,
                progress) -> list[Document]:
        docs: list[Document] = []

        if entity.cik:
            progress(f"  SEC EDGAR: CIK {entity.cik} ({', '.join(entity.aliases)})")
            filings = self.edgar.recent_filings(entity.cik, limit=12)
            if opts.fetch_filing_bodies:
                for f in filings[:6]:
                    self.edgar.fetch_filing_text(f)
            docs.extend(filings)
        else:
            progress("  SEC EDGAR: no confident CIK match (skipping filings)")

        if entity.cik:
            progress("  SEC EDGAR full-text search for IP security agreements...")
            docs.extend(self.edgar.full_text_search(
                entity.parent_name or entity.name, cik=entity.cik))
        else:
            progress("  SEC EDGAR full-text: skipped (needs a CIK to attribute hits)")

        progress("  IAPD adviser search...")
        docs.extend(self.iapd.search_firm(entity.parent_name or entity.name, hits=5))

        progress("  OFAC SDN name screen...")
        docs.extend(self.ofac.screen(entity.name))
        if entity.parent_name and entity.parent_name != entity.name:
            docs.extend(self.ofac.screen(entity.parent_name))

        if self.uspto.available:
            progress("  USPTO assignment records...")
            docs.extend(self.uspto.assignments(entity.parent_name or entity.name))
        else:
            progress("  USPTO: skipped (no USPTO_API_KEY — IP liens NOT checked)")

        if self.sam.available and entity.uei:
            progress("  SAM.gov entity registration...")
            sam_doc = self.sam.entity(entity.uei)
            if sam_doc:
                docs.append(sam_doc)

        if entity.domains and not opts.skip_web:
            for domain in entity.domains:
                progress(f"  crawling {domain} (IR / press / legal)...")
                docs.extend(self.web.collect(domain, company=entity.name))

        return [d for d in docs if d.text.strip()]

    # ------------------------------------------------------------ screening
    def _screen_entity(self, entity: Entity, contracts: list[Contract], run_id: str,
                       opts: ScreenOptions, progress,
                       record_changes: list[dict] | None = None) -> Finding | None:
        docs = self._gather(entity, opts, progress)
        progress(f"  {len(docs)} document(s) collected; diffing against store...")

        pairs: list[tuple[Document, Change | None]] = []
        changed = 0
        for doc in docs:
            change = self.store.observe(doc)
            self.store.link_document(entity.key(), doc)
            if change.kind in ("new", "modified"):
                changed += 1
            pairs.append((doc, change))
        progress(f"  {changed} new/changed since last screen.")

        signals = engine.evaluate_documents(pairs, entity, contracts)
        signals += engine.evaluate_contracts(contracts, entity)
        # The award record moving is evidence in its own right, and unlike a
        # document it cannot be re-read later: the previous value is gone from
        # the contracts row the moment it is written over.
        signals += engine.evaluate_contract_changes(
            record_changes or [], entity, contracts)

        # Tenant overrides, applied before anything compounds: a retired rule
        # must not be able to escalate something by pairing with another.
        settings = self.store.rule_settings()
        before = len(signals)
        signals = engine.apply_rule_settings(signals, settings)
        if before != len(signals):
            progress(f"  {before - len(signals)} signal(s) dropped by rule settings.")

        # First time we see an entity everything looks "new"; that would mark a
        # baseline scan as urgent. Damp it unless the evidence is independently strong.
        seen_before = self.store.previous_signal_ids(entity.key(), exclude_run=run_id)
        if not seen_before:
            for s in signals:
                if s.severity not in ("critical",):
                    s.is_new = False

        finding = engine.build_finding(entity, contracts, signals, run_id=run_id)
        progress(f"  -> {finding.severity.upper()} (score {finding.total_score}, "
                 f"{len(finding.signals)} signals)")

        order = ["info", "low", "medium", "high", "critical"]
        if order.index(finding.severity) < order.index(opts.min_severity):
            return None
        return finding
