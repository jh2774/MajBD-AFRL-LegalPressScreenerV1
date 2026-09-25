"""Rule engine.

Each rule looks at one Document (or one structured record) and emits Signals.
A Signal always carries: what fired, the literal text that made it fire, a URL a
human can open, and a plain-English rationale. Nothing is flagged without a
quotable basis — a KO will not act on "the model said so".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..models import SEVERITY_ORDER, Change, Contract, Document, Entity, Finding, Signal
from . import lexicon as lex

# Base weights per rule family, before jurisdiction and novelty multipliers.
BASE_WEIGHTS = {
    "FOCI": 6.0,
    "IP_COLLATERAL": 7.0,
    "IP_TRANSFER": 5.0,
    "SANCTIONS": 12.0,
    "STRUCTURE": 4.0,
}

SEVERITY_BANDS = [(30.0, "critical"), (18.0, "high"), (9.0, "medium"), (3.0, "low")]

NEW_EVIDENCE_MULTIPLIER = 1.6   # it changed since last screen -> that is the point
IP_CONTRACT_MULTIPLIER = 1.4    # the affected award carries data-rights clauses

# How close an ownership term must sit to a jurisdiction mention (characters)
# before the two are treated as describing the same transaction.
PROXIMITY_WINDOW = 800


def band(score: float) -> str:
    for threshold, name in SEVERITY_BANDS:
        if score >= threshold:
            return name
    return "info"


@dataclass
class RuleContext:
    entity: Entity
    contracts: list[Contract]
    is_new: bool = False
    source_url: str = ""

    @property
    def ip_sensitive(self) -> bool:
        return any(c.ip_sensitive for c in self.contracts)

    @property
    def ip_clauses(self) -> list[str]:
        seen: list[str] = []
        for c in self.contracts:
            for clause in c.ip_clause_hits:
                if clause not in seen:
                    seen.append(clause)
        return seen


def _score(category: str, ctx: RuleContext, multiplier: float = 1.0) -> float:
    score = BASE_WEIGHTS.get(category, 4.0) * multiplier
    if ctx.is_new:
        score *= NEW_EVIDENCE_MULTIPLIER
    if category in ("IP_COLLATERAL", "IP_TRANSFER") and ctx.ip_sensitive:
        score *= IP_CONTRACT_MULTIPLIER
    return round(score, 2)


def _sev(score: float) -> str:
    return band(score)


# --- evidence quality --------------------------------------------------------
# A phrase in an 8-K exhibit ("the Grantor hereby grants a security interest")
# and the same phrase in a 10-K risk factor ("if we were to become insolvent")
# are not equivalent evidence. Without this damping a large-cap annual report,
# which mentions nearly every adverse concept somewhere, scores like a distress
# event — the single largest source of false positives in early testing.

HEDGE_RX = re.compile(
    r"\b(?:if|may|might|could|would|whether|in the event|no assurance|"
    r"risk that|risks? relating|potential(?:ly)?|from time to time|"
    r"we expect|we anticipate|we believe|subject to|there can be no)\b", re.I)

PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F", "S-1", "S-4", "DEF 14A", "424")

# Credit agreements define "Event of Default" to include the borrower's own
# bankruptcy. Every investment-grade revolver contains that sentence, and it
# says nothing about the borrower's condition. Definitional text is describing
# a contingency the parties are drafting around, not an event that occurred.
DEFINITIONAL_RX = re.compile(
    r"(?:\bas defined in\b|\bshall mean\b|\bmeans,? with respect to\b|"
    r"\bfor purposes of this\b|\bEvents? of Default\b|\bthe following events\b|"
    r"\bhereinafter referred\b|\bshall have the meaning\b|\bas such term is\b)", re.I)


def _evidence_weight(doc: Document, snippet: str) -> tuple[float, str]:
    """Return (multiplier, human-readable caveat) for a prose match.

    The caveat text is surfaced in the notice, so a contracting officer can see
    not just that we scored something down but why.
    """
    weight = 1.0
    caveats: list[str] = []
    form = (doc.doc_type or "").upper()
    snippet = snippet or ""
    if form.startswith(PERIODIC_FORMS):
        weight *= 0.35
        caveats.append(
            f"appears in a periodic report ({form}), where risk-factor and MD&A "
            f"language describes hypotheticals as a matter of course")
    if HEDGE_RX.search(snippet):
        weight *= 0.5
        caveats.append("is phrased conditionally rather than as a completed transaction")
    if DEFINITIONAL_RX.search(snippet):
        weight *= 0.4
        caveats.append(
            "sits inside a definitional or event-of-default clause, which "
            "describes a contingency the parties drafted around rather than "
            "something that has happened")
    if not caveats:
        return 1.0, ""
    return weight, " Weight reduced: this match " + "; and it ".join(caveats) + "."


# --------------------------------------------------------------------- rules

def rule_foci_jurisdiction(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Foreign jurisdiction named alongside an ownership/investment event.

    When the document has been scoped down to a change delta, the jurisdiction
    may sit in the *unchanged* part of the page — a company that has always
    disclosed a Cayman parent and today announced a change of control. Falling
    back to the surrounding context catches that, at a reduced weight and
    labelled as context so the KO can see which half is new.
    """
    text = doc.text
    if not text.strip():
        return []
    context = (doc.meta or {}).get("_context_text") or ""
    events_pos = lex.find_terms_with_pos(text, lex.FOCI_EVENT_TERMS)

    juris_pos = lex.find_jurisdictions_with_pos(text)
    from_context = False
    if not juris_pos and context and events_pos:
        juris_pos = lex.find_jurisdictions_with_pos(context)
        from_context = bool(juris_pos)
    if not juris_pos:
        return []

    signals: list[Signal] = []
    for name, snippet, j_pos in juris_pos:
        # Proximity gate. A jurisdiction named on page 1 and an unrelated
        # ownership word on page 40 are not the same story: on a long page,
        # co-occurrence anywhere in the document is close to guaranteed and
        # says nothing. Only events near the mention corroborate it.
        events = ([(t, s) for t, s, e_pos in events_pos
                   if abs(e_pos - j_pos) <= PROXIMITY_WINDOW]
                  if not from_context else [(t, s) for t, s, _ in events_pos])
        if from_context:
            event_terms = ", ".join(sorted({t for t, _ in events})[:4])
            mult = lex.jurisdiction_multiplier(name) * 0.7
            score = _score("FOCI", ctx, mult)
            signals.append(Signal(
                rule_id="FOCI-JURIS-02", category="FOCI", severity=_sev(score),
                score=score,
                title=f"New ownership event at contractor with standing {name} nexus",
                rationale=(
                    f"New text in this source describes an ownership or control event "
                    f"({event_terms}). The {name} ({lex.jurisdiction_tier(name)}) "
                    f"connection is not itself new — it appears in the previously "
                    f"screened content — but a control event at an entity with that "
                    f"standing nexus is the combination worth checking."),
                evidence=events[0][1][:600], source=doc.source,
                source_url=doc.url or ctx.source_url, jurisdiction=name,
                is_new=ctx.is_new))
            continue
        tier = lex.jurisdiction_tier(name)
        mult = lex.jurisdiction_multiplier(name)
        if not events and tier == "conduit":
            continue  # a passing mention of Ireland is not a finding
        quality, caveat = _evidence_weight(doc, snippet)
        if events:
            event_terms = ", ".join(sorted({t for t, _ in events})[:4])
            rationale = (
                f"{name} ({tier} jurisdiction) appears in the same document as "
                f"ownership/investment language ({event_terms}). Under 32 CFR Part 117 "
                f"(NISPOM) a foreign person's ability to direct or decide matters "
                f"affecting the contractor is reportable FOCI, and a change in "
                f"ownership must be reported to the CSA.")
            score = _score("FOCI", ctx, mult * quality)
            rule_id = "FOCI-JURIS-01"
            title = f"{name} nexus in {doc.source} document"
        else:
            rationale = (
                f"{name} ({tier} jurisdiction) is named in this filing without an "
                f"explicit transaction nearby. Flagged for confirmation of beneficial "
                f"ownership rather than as a concluded FOCI determination — the "
                f"mention may be geographic or historical rather than an ownership "
                f"connection.")
            score = _score("FOCI", ctx, mult * 0.45 * quality)
            # Distinct id: a bare mention is not corroborating evidence and must
            # not be eligible to trigger the compound rule.
            rule_id = "FOCI-MENTION-01"
            title = f"{name} named in {doc.source} document (no transaction identified)"
        rationale += caveat
        signals.append(Signal(
            rule_id=rule_id, category="FOCI", severity=_sev(score), score=score,
            title=title,
            rationale=rationale, evidence=snippet[:600], source=doc.source,
            source_url=doc.url or ctx.source_url, jurisdiction=name, is_new=ctx.is_new))
    return signals


def rule_ip_collateral(doc: Document, ctx: RuleContext) -> list[Signal]:
    """IP pledged as loan collateral — the Government's data rights survive a
    foreclosure only if properly asserted, so this is worth a KO's attention."""
    hits = lex.find_terms(doc.text, lex.IP_COLLATERAL_TERMS)
    if not hits:
        return []
    strong = {"intellectual property security agreement", "patent security agreement",
              "trademark security agreement", "collateral assignment of patents",
              "security interest in the patents", "security interest in intellectual property",
              "pledge of intellectual property", "pledged intellectual property"}
    matched_terms = {t.lower() for t, _ in hits}
    is_strong = bool(matched_terms & strong)
    mult = 1.0 if is_strong else 0.45
    snippet = hits[0][1]
    quality, caveat = _evidence_weight(doc, snippet)
    score = _score("IP_COLLATERAL", ctx, mult * quality)
    term_list = ", ".join(sorted(matched_terms)[:5])
    clause_note = ""
    if ctx.ip_clauses:
        clause_note = (" The contractor's awards include " +
                       "; ".join(ctx.ip_clauses[:3]) +
                       ", so Government data/software rights are directly implicated.")
    rationale = (
        f"Language indicating intellectual property has been pledged as security "
        f"({term_list}). If the secured party forecloses, patents and technical data "
        f"underpinning contract performance can transfer to a third party the "
        f"Government never vetted.{clause_note}")
    if not is_strong:
        rationale += (" Weak match: financing vocabulary present without an explicit "
                      "IP security agreement — verify before escalating.")
    rationale += caveat
    return [Signal(
        rule_id="IPCOL-01", category="IP_COLLATERAL", severity=_sev(score), score=score,
        title="Intellectual property pledged as collateral",
        rationale=rationale, evidence=snippet[:600], source=doc.source,
        source_url=doc.url or ctx.source_url, is_new=ctx.is_new)]


def rule_ip_transfer(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Outright IP movement: assignment, exclusive licence, insolvency."""
    hits = lex.find_terms(doc.text, lex.IP_TRANSFER_TERMS)
    if not hits:
        return []
    terms = sorted({t for t, _ in hits})
    distress = {"chapter 11", "chapter 7", "receivership", "insolvency",
                "assignment for the benefit of creditors", "363 sale", "liquidation"}
    mult = 1.3 if set(terms) & distress else 0.8
    quality, caveat = _evidence_weight(doc, hits[0][1])
    score = _score("IP_TRANSFER", ctx, mult * quality)
    rationale = (
        f"Document references movement or encumbrance of intellectual property "
        f"({', '.join(terms[:5])}). Where the underlying award carries data-rights "
        f"clauses, the Government's licence should be confirmed to survive the "
        f"transaction and the contractor's ability to perform re-verified.")
    if set(terms) & distress:
        rationale += (" Distress vocabulary present — in insolvency, IP is frequently "
                      "sold free and clear, and Government rights must be asserted "
                      "in the proceeding to be preserved.")
    rationale += caveat
    return [Signal(
        rule_id="IPXFER-01", category="IP_TRANSFER", severity=_sev(score), score=score,
        title="Intellectual property transfer or distress indicator",
        rationale=rationale, evidence=hits[0][1][:600], source=doc.source,
        source_url=doc.url or ctx.source_url, is_new=ctx.is_new)]


def rule_uspto_security_interest(doc: Document, ctx: RuleContext) -> list[Signal]:
    """A recorded USPTO conveyance of type SECURITY INTEREST is hard evidence,
    not vocabulary matching — weight it accordingly."""
    if doc.source != "uspto":
        return []
    conveyance = (doc.meta or {}).get("conveyance", "").upper()
    if not any(k in conveyance for k in lex.USPTO_SECURITY_CONVEYANCES):
        return []
    assignee = (doc.meta or {}).get("assignee", "")
    address = (doc.meta or {}).get("assignee_address", "")
    juris = lex.find_jurisdictions(f"{assignee} {address}")
    mult = 1.5
    jname = ""
    if juris:
        jname = juris[0][0]
        mult *= lex.jurisdiction_multiplier(jname)
    score = _score("IP_COLLATERAL", ctx, mult)
    rationale = (
        f"USPTO assignment records show a recorded conveyance of "
        f"'{conveyance.title()}' to {assignee or 'an undisclosed party'} covering "
        f"{(doc.meta or {}).get('patent_count', 'one or more')} propert(ies). This is a "
        f"recorded encumbrance on the contractor's patent estate, not an inference.")
    if jname:
        rationale += (f" The secured party has a {jname} nexus, which raises a FOCI "
                      f"question in addition to the IP question.")
    return [Signal(
        rule_id="IPCOL-USPTO-01", category="IP_COLLATERAL", severity=_sev(score),
        score=score, title="Recorded USPTO security interest against patent estate",
        rationale=rationale, evidence=doc.text[:600], source="uspto",
        source_url=doc.url, jurisdiction=jname, is_new=ctx.is_new)]


def rule_contract_structural(contract: Contract, ctx: RuleContext) -> list[Signal]:
    """FOCI signals available from the contract record itself."""
    signals: list[Signal] = []
    for field_name, value, label in (
        ("recipient_country", contract.recipient_country, "registered address"),
        ("country_of_incorporation", contract.country_of_incorporation,
         "country of incorporation"),
    ):
        if not value:
            continue
        name = lex.COUNTRY_CODE_TO_JURISDICTION.get(value.upper())
        if not name:
            hits = lex.find_jurisdictions(value)
            name = hits[0][0] if hits else None
        if not name:
            continue
        mult = lex.jurisdiction_multiplier(name)
        score = _score("STRUCTURE", ctx, mult)
        signals.append(Signal(
            rule_id="STRUCT-COUNTRY-01", category="STRUCTURE", severity=_sev(score),
            score=score, title=f"Contractor {label}: {name}",
            rationale=(f"FPDS/USAspending records the contractor's {label} as {value} "
                       f"({name}, {lex.jurisdiction_tier(name)} tier) on award "
                       f"{contract.piid or contract.award_id}. Confirm the entity's "
                       f"ownership chain and any FOCI mitigation instrument on file."),
            evidence=f"{field_name}={value} on {contract.piid or contract.award_id}",
            source="fpds", source_url=contract.usaspending_url or contract.source_url,
            jurisdiction=name, is_new=ctx.is_new))

    if contract.foreign_owned_and_located:
        score = _score("STRUCTURE", ctx, 2.0)
        signals.append(Signal(
            rule_id="STRUCT-FOREIGN-OWNED-01", category="STRUCTURE", severity=_sev(score),
            score=score, title="FPDS flags contractor as foreign owned and located",
            rationale=("The contractor self-certified in FPDS as foreign owned and "
                       "located. Verify the FOCI mitigation instrument (SSA, proxy "
                       "agreement, board resolution) covering this award."),
            evidence=f"isForeignOwnedAndLocated=true on {contract.piid or contract.award_id}",
            source="fpds", source_url=contract.usaspending_url, is_new=ctx.is_new))

    if contract.foreign_funding and "not applicable" not in contract.foreign_funding.lower():
        score = _score("STRUCTURE", ctx, 1.2)
        signals.append(Signal(
            rule_id="STRUCT-FOREIGN-FUNDING-01", category="STRUCTURE", severity=_sev(score),
            score=score, title="Award carries a foreign-funding indicator",
            rationale=(f"FPDS foreign funding field reads '{contract.foreign_funding}'. "
                       f"Confirm whether foreign funds support performance and whether "
                       f"that was disclosed."),
            evidence=contract.foreign_funding, source="fpds",
            source_url=contract.usaspending_url, is_new=ctx.is_new))
    return signals


def rule_sanctions_hit(doc: Document, ctx: RuleContext) -> list[Signal]:
    if doc.source != "ofac":
        return []
    score = _score("SANCTIONS", ctx, 1.0)
    return [Signal(
        rule_id="SANCTION-01", category="SANCTIONS", severity="critical", score=score,
        title="Possible sanctions/screening-list name match",
        rationale=("A name on the OFAC SDN list is similar to the contractor or a "
                   "party named in its filings. This is a name match only and must be "
                   "adjudicated against identifiers before any action is taken."),
        evidence=doc.text[:600], source="ofac", source_url=doc.url, is_new=ctx.is_new)]


def rule_adviser_foreign_domicile(doc: Document, ctx: RuleContext) -> list[Signal]:
    """An SEC-registered adviser connected to the contractor is domiciled abroad."""
    if doc.source != "iapd":
        return []
    country = (doc.meta or {}).get("country", "")
    if not country or country.strip().lower() in ("united states", "usa", "us"):
        return []
    hits = lex.find_jurisdictions(country)
    if not hits:
        return []
    name, _ = hits[0]
    mult = lex.jurisdiction_multiplier(name)
    score = _score("FOCI", ctx, mult * 0.8)
    return [Signal(
        rule_id="FOCI-IAPD-01", category="FOCI", severity=_sev(score), score=score,
        title=f"Related investment adviser domiciled in {name}",
        rationale=(f"IAPD lists '{(doc.meta or {}).get('firm_name', '')}' "
                   f"(SEC# {(doc.meta or {}).get('sec_number', 'n/a')}) with an office in "
                   f"{country}. Where an adviser in a {lex.jurisdiction_tier(name)} "
                   f"jurisdiction holds or manages an interest in the contractor, the "
                   f"beneficial ownership behind that interest should be identified."),
        evidence=doc.text[:600], source="iapd", source_url=doc.url,
        jurisdiction=name, is_new=ctx.is_new)]


# 8-K item code -> (category, weight multiplier, what it means for this screen).
# These are structured facts asserted by the registrant to the SEC, so they are
# stronger evidence than any phrase match and are scored as such.
EIGHT_K_ITEMS = {
    "5.01": ("FOCI", 2.0, "Changes in Control of Registrant",
             "The registrant reported a change in control. Where the incoming "
             "controlling party is foreign, this is precisely the event that "
             "triggers a FOCI review and, potentially, a novation under FAR 42.12."),
    "1.03": ("IP_TRANSFER", 2.0, "Bankruptcy or Receivership",
             "The registrant reported bankruptcy or receivership. Intellectual "
             "property is routinely sold free and clear in these proceedings; "
             "Government licence rights must be asserted in the case to survive."),
    "2.01": ("IP_TRANSFER", 1.5, "Completion of Acquisition or Disposition of Assets",
             "Assets changed hands. Confirm whether any technical data, software "
             "or patents relied on for contract performance moved with them."),
    "2.03": ("IP_COLLATERAL", 1.4, "Creation of a Direct Financial Obligation",
             "The registrant took on a direct financial obligation. Secured "
             "facilities of this kind frequently carry an all-assets lien that "
             "captures the patent estate."),
    "2.04": ("IP_COLLATERAL", 1.3, "Triggering Event Accelerating a Financial Obligation",
             "An acceleration or default event was reported, which brings any "
             "security interest over intellectual property closer to enforcement."),
    "3.02": ("FOCI", 1.2, "Unregistered Sales of Equity Securities",
             "Equity was issued outside a registered offering. Private placements "
             "are a common route for foreign capital to take a position without "
             "an obvious public disclosure."),
    "1.01": ("IP_COLLATERAL", 0.7, "Entry into a Material Definitive Agreement",
             "A material agreement was entered into. Low weight on its own — it "
             "is the wrapper for both routine commercial deals and security "
             "agreements, so it warrants a look rather than a conclusion."),
}


def rule_edgar_8k_items(doc: Document, ctx: RuleContext) -> list[Signal]:
    """Score 8-K item codes as structured facts rather than prose.

    EDGAR's item codes are the highest-quality signal the filing index offers:
    the registrant itself is asserting "this filing is about a change in
    control". Reading them from metadata avoids inferring the same thing from
    English text, which is both weaker and easy to get wrong.
    """
    codes = (doc.meta or {}).get("item_codes") or []
    if not codes:
        return []
    company = (doc.meta or {}).get("company", "the registrant")
    signals: list[Signal] = []
    for code in codes:
        entry = EIGHT_K_ITEMS.get(str(code).strip())
        if not entry:
            continue
        category, mult, label, explanation = entry
        score = _score(category, ctx, mult)
        signals.append(Signal(
            rule_id=f"EDGAR-8K-{code}", category=category, severity=_sev(score),
            score=score, title=f"8-K Item {code}: {label}",
            rationale=(f"{company} filed an 8-K reporting Item {code} "
                       f"({label}) on {doc.published or 'an undisclosed date'}. "
                       f"{explanation}"),
            evidence=f"SEC Form 8-K, Item {code} — {label}",
            source="sec_edgar", source_url=doc.url, is_new=ctx.is_new))
    return signals


DOCUMENT_RULES = [
    rule_edgar_8k_items,
    rule_foci_jurisdiction,
    rule_ip_collateral,
    rule_ip_transfer,
    rule_uspto_security_interest,
    rule_sanctions_hit,
    rule_adviser_foreign_domicile,
]


# ----------------------------------------------------------------- evaluation

def evaluate_documents(docs: list[tuple[Document, Change | None]],
                       ctx_entity: Entity, contracts: list[Contract]) -> list[Signal]:
    """Run every document rule. When a Change is supplied, rules see only the
    newly-added text so we report movement rather than steady state."""
    signals: list[Signal] = []
    for doc, change in docs:
        is_new = bool(change and change.kind == "new")
        scoped = doc
        if change and change.kind == "modified" and change.added_text.strip():
            scoped = Document(source=doc.source, key=doc.key, title=doc.title,
                              url=doc.url, text=change.added_text,
                              published=doc.published, doc_type=doc.doc_type,
                              meta={**(doc.meta or {}), "_context_text": doc.text})
            is_new = True
        elif change and change.kind == "unchanged":
            is_new = False
        ctx = RuleContext(entity=ctx_entity, contracts=contracts, is_new=is_new,
                          source_url=doc.url)
        for rule in DOCUMENT_RULES:
            try:
                fired = rule(scoped, ctx)
            except Exception:  # a bad rule must not sink the run
                continue
            # Stamped here rather than in each rule: the document is already in
            # scope, and ten constructors that each have to remember a field is
            # ten chances to forget it.
            for signal in fired:
                signal.document_key = doc.key
            signals.extend(fired)
    return signals


def evaluate_contracts(contracts: list[Contract], entity: Entity) -> list[Signal]:
    ctx = RuleContext(entity=entity, contracts=contracts)
    out: list[Signal] = []
    for c in contracts:
        out.extend(rule_contract_structural(c, ctx))
    return out


def _jurisdiction_of(value: str) -> str:
    """Best name for a country code or country string, or "" if unflagged."""
    name = lex.COUNTRY_CODE_TO_JURISDICTION.get((value or "").upper())
    if name:
        return name
    hits = lex.find_jurisdictions(value or "")
    return hits[0][0] if hits else ""


def evaluate_contract_changes(changes: list[dict], entity: Entity,
                              contracts: list[Contract]) -> list[Signal]:
    """Signals from an award's record moving between screens.

    The document side of this tool has always treated a change as worth more
    than a standing fact — a clause that appeared last week is evidence in a
    way the same clause sitting in a filing for nine years is not. The contract
    record had no equivalent: an award that changed hands, or a contractor that
    re-registered abroad, was written over in place and read afterwards as
    though it had always been that way.

    Every signal here is `is_new` by construction. There is no baseline case:
    `save_contract` reports nothing for a first sighting.
    """
    ctx = RuleContext(entity=entity, contracts=contracts, is_new=True)
    out: list[Signal] = []
    seen_novation = False

    for ch in changes:
        field, old, new = ch.get("field"), ch.get("old", ""), ch.get("new", "")
        piid = ch.get("piid") or ch.get("contract_key") or ""
        url = contracts[0].usaspending_url if contracts else ""

        # A novation shows up as the UEI and the entity key moving together.
        # They are the same event, so it is reported once.
        if field in ("entity_key", "recipient_uei"):
            if seen_novation:
                continue
            seen_novation = True
            score = _score("STRUCTURE", ctx, 1.5)
            out.append(Signal(
                rule_id="CHANGE-NOVATION-01", category="STRUCTURE", severity=_sev(score),
                score=score, title="Award now recorded against a different contractor",
                rationale=(
                    f"Award {piid} was previously recorded against {old} and now "
                    f"reads {new}. A transfer of an award between entities is a "
                    f"novation, and the agreement behind it is where a change of "
                    f"ownership would be documented. Confirm the novation package "
                    f"(SF 30, successor-in-interest agreement) and whether a FOCI "
                    f"review accompanied it."),
                evidence=f"{field}: {old} -> {new} on {piid}",
                source="usaspending/fpds", source_url=url, is_new=True))

        elif field in ("country_of_incorporation", "recipient_country"):
            name = _jurisdiction_of(new)
            # An unflagged destination is still a structural change worth a
            # line; it just does not carry a jurisdiction's weight.
            mult = lex.jurisdiction_multiplier(name) if name else 0.8
            score = _score("STRUCTURE", ctx, mult)
            where = "country of incorporation" if field == "country_of_incorporation" \
                else "registered address country"
            tier = f", {lex.jurisdiction_tier(name)} tier" if name else ""
            out.append(Signal(
                rule_id="CHANGE-COUNTRY-01", category="STRUCTURE", severity=_sev(score),
                score=score,
                title=f"Contractor {where} changed: {old} → {new}",
                rationale=(
                    f"The {where} recorded for this contractor on award {piid} "
                    f"moved from {old} to {new}"
                    f"{f' ({name}{tier})' if name else ''}. A registered seat moving "
                    f"is the most direct structural indicator available in the "
                    f"contract record. Confirm the ownership chain and whether the "
                    f"change was reported to the cognisant security office."),
                evidence=f"{field}: {old} -> {new} on {piid}",
                source="usaspending/fpds", source_url=url,
                jurisdiction=name, is_new=True))

        elif field == "foreign_owned" and str(new) == "1":
            score = _score("STRUCTURE", ctx, 2.0)
            out.append(Signal(
                rule_id="CHANGE-FOREIGN-OWNED-01", category="STRUCTURE",
                severity=_sev(score), score=score,
                title="Contractor newly self-certifies as foreign owned and located",
                rationale=(
                    f"On award {piid} the FPDS foreign-owned-and-located flag was "
                    f"previously absent and now reads true. The certification is the "
                    f"contractor's own; it changing is a statement that something "
                    f"about the ownership did. Verify the mitigation instrument on "
                    f"file covers the award in its current form."),
                evidence=f"foreign_owned: {old or '0'} -> 1 on {piid}",
                source="fpds", source_url=url, is_new=True))

    return out


def correlate(signals: list[Signal], contracts: list[Contract]) -> list[Signal]:
    """Compound rule: foreign nexus AND an IP encumbrance is worse than either.

    This is the case the tool exists to catch — an offshore investor arriving at
    a contractor whose patents are already pledged.
    """
    # Both halves must stand on their own before they are allowed to compound.
    # Two weak observations do not make a strong one: a bare geographic mention
    # of a jurisdiction plus generic credit-agreement vocabulary describes an
    # ordinary company, and escalating that pair to CRITICAL is how the tool
    # would earn a reputation for crying wolf.
    MIN_RANK = SEVERITY_ORDER.index("medium")
    foci = [s for s in signals
            if s.category in ("FOCI", "STRUCTURE") and s.jurisdiction
            and s.rule_id != "FOCI-MENTION-01"
            and s.severity_rank() >= MIN_RANK]
    ip = [s for s in signals
          if s.category in ("IP_COLLATERAL", "IP_TRANSFER")
          and s.severity_rank() >= MIN_RANK]
    if not (foci and ip):
        return []
    worst = max(foci, key=lambda s: s.score)
    worst_ip = max(ip, key=lambda s: s.score)
    ip_clauses: list[str] = []
    for c in contracts:
        ip_clauses.extend(x for x in c.ip_clause_hits if x not in ip_clauses)
    score = round((worst.score + worst_ip.score) * 0.9, 2)
    rationale = (
        f"Two independent signal families overlap on this contractor: a "
        f"{worst.jurisdiction} nexus ({worst.rule_id}) and an intellectual-property "
        f"encumbrance or transfer ({worst_ip.rule_id}). Separately each warrants a "
        f"question; together they describe a path by which technology developed "
        f"under Government contract could come under foreign control without a "
        f"novation or FOCI action ever reaching the contracting officer.")
    if ip_clauses:
        rationale += (f" Affected awards cite {'; '.join(ip_clauses[:3])}.")
    return [Signal(
        rule_id="COMPOUND-01", category="FOCI", severity=_sev(score),
        score=score, title="Compound risk: foreign nexus overlapping IP encumbrance",
        rationale=rationale,
        evidence=f"{worst.title} + {worst_ip.title}",
        source="correlation", source_url=worst.source_url,
        jurisdiction=worst.jurisdiction,
        is_new=worst.is_new or worst_ip.is_new)]


def dedupe(signals: list[Signal]) -> list[Signal]:
    seen: set[tuple] = set()
    out: list[Signal] = []
    for s in sorted(signals, key=lambda x: -x.score):
        key = (s.rule_id, s.jurisdiction, s.evidence[:160])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


MIN_RULE_WEIGHT = 0.0
MAX_RULE_WEIGHT = 5.0


def apply_rule_settings(signals: list[Signal],
                        settings: dict[str, dict]) -> list[Signal]:
    """Drop signals from disabled rules and rescale the rest.

    Applied before `build_finding`, so a disabled rule cannot reach the
    compound rule either: a rule an analyst has retired should not be able to
    escalate something through the back door by pairing with another.

    Rescaling recomputes the severity band from the new score rather than
    keeping the old label. A signal scored down to 2.0 that still reads
    "critical" would be worse than not rescaling at all.
    """
    if not settings:
        return signals

    kept: list[Signal] = []
    for signal in signals:
        cfg = settings.get(signal.rule_id)
        if cfg and not cfg.get("enabled", True):
            continue
        weight = float((cfg or {}).get("weight", 1.0))
        if cfg and weight != 1.0:
            weight = max(MIN_RULE_WEIGHT, min(MAX_RULE_WEIGHT, weight))
            score = round(signal.score * weight, 2)
            signal = replace(signal, score=score, severity=band(score))
        kept.append(signal)
    return kept


def build_finding(entity: Entity, contracts: list[Contract], signals: list[Signal],
                  run_id: str = "") -> Finding:
    signals = dedupe(signals + correlate(signals, contracts))
    ordered = sorted(signals, key=lambda s: -s.score)
    if not ordered:
        return Finding(entity=entity, signals=[], contracts=contracts,
                       total_score=0.0, severity="info", run_id=run_id)

    # Diminishing returns: a pile of weak hits should not out-score one strong
    # one. Steep decay means the composite is dominated by the top signal.
    total = round(sum(s.score * (0.6 ** i) for i, s in enumerate(ordered)), 2)
    severity = band(total)

    # Corroboration cap. Accumulating many low-confidence signals must not
    # manufacture a high-severity notice: a prime whose worst individual finding
    # is "medium" should not arrive in a KO's inbox marked CRITICAL. Promotion
    # by one band is allowed only when at least two signals independently reach
    # the top band observed.
    top_rank = SEVERITY_ORDER.index(ordered[0].severity)
    corroborated = sum(
        1 for s in ordered if SEVERITY_ORDER.index(s.severity) >= top_rank) >= 2
    cap_rank = min(len(SEVERITY_ORDER) - 1, top_rank + (1 if corroborated else 0))
    if SEVERITY_ORDER.index(severity) > cap_rank:
        severity = SEVERITY_ORDER[cap_rank]

    return Finding(entity=entity, signals=ordered, contracts=contracts,
                   total_score=total, severity=severity, run_id=run_id)
