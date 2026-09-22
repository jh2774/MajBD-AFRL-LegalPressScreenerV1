"""Core domain objects.

Deliberately plain dataclasses: they serialise to JSON for an API layer later
without dragging a web framework or ORM into the screening core.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _clean(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


class Serialisable:
    def to_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class ContractingOfficer(Serialisable):
    """Whoever we can actually address the notice to."""
    name: str = ""
    email: str = ""
    phone: str = ""
    office_code: str = ""
    office_name: str = ""
    # How we got here — shown in the email footer so a KO can sanity-check it.
    source: str = ""
    confidence: str = "low"  # low | medium | high

    @property
    def is_addressable(self) -> bool:
        return bool(self.email and "@" in self.email)


@dataclass
class Contract(Serialisable):
    award_id: str
    piid: str = ""
    generated_internal_id: str = ""
    recipient_name: str = ""
    recipient_uei: str = ""
    parent_recipient_name: str = ""
    parent_recipient_uei: str = ""
    awarding_agency: str = ""
    awarding_sub_agency: str = ""
    award_amount: float = 0.0
    start_date: str = ""
    end_date: str = ""
    description: str = ""
    naics_code: str = ""
    naics_description: str = ""
    psc_code: str = ""
    psc_description: str = ""
    solicitation_id: str = ""
    contracting_office_id: str = ""
    recipient_country: str = ""
    country_of_incorporation: str = ""
    state_of_incorporation: str = ""
    foreign_owned_and_located: bool = False
    foreign_funding: str = ""
    officer: ContractingOfficer = field(default_factory=ContractingOfficer)
    # FAR/DFARS data-rights clauses inferred from the requirement description.
    ip_clause_hits: list[str] = field(default_factory=list)
    source_url: str = ""

    @property
    def usaspending_url(self) -> str:
        if self.generated_internal_id:
            return f"https://www.usaspending.gov/award/{self.generated_internal_id}"
        return ""

    @property
    def ip_sensitive(self) -> bool:
        return bool(self.ip_clause_hits)


@dataclass
class Entity(Serialisable):
    """A contractor, resolved across the various registries."""
    name: str
    uei: str = ""
    cage: str = ""
    parent_name: str = ""
    parent_uei: str = ""
    cik: str = ""                      # SEC
    tickers: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    iapd_crd: str = ""
    countries: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    def key(self) -> str:
        return (self.uei or self.name).upper().strip()


@dataclass
class Document(Serialisable):
    """A retrieved artefact we can run rules over.

    `text` is normalised plain text; `key` is stable across runs so the store
    can diff this run's copy against the last one.
    """
    source: str            # usaspending | fpds | sec_edgar | iapd | uspto | web | ofac
    key: str
    title: str = ""
    url: str = ""
    text: str = ""
    published: str = ""
    doc_type: str = ""
    meta: dict = field(default_factory=dict)
    fetched_at: str = field(default_factory=_now)

    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "replace")).hexdigest()


@dataclass
class Change(Serialisable):
    """What is new about a Document since we last looked at it."""
    document_key: str
    source: str
    url: str = ""
    kind: str = "modified"          # new | modified | unchanged
    added_text: str = ""
    previous_sha: str = ""
    current_sha: str = ""
    previous_seen_at: str = ""
    observed_at: str = field(default_factory=_now)

    @property
    def is_material(self) -> bool:
        return self.kind in ("new", "modified") and bool(self.added_text.strip())


@dataclass
class Signal(Serialisable):
    """One rule firing against one piece of evidence."""
    rule_id: str
    category: str          # FOCI | IP_COLLATERAL | IP_TRANSFER | SANCTIONS | STRUCTURE
    severity: str
    score: float
    title: str
    rationale: str
    evidence: str
    source: str
    source_url: str = ""
    jurisdiction: str = ""
    observed_at: str = field(default_factory=_now)
    is_new: bool = False   # surfaced by a change diff rather than a baseline scan
    # Which stored document produced this, so a reviewer can open the diff that
    # contains the matched text rather than pairing the two up by eye.
    document_key: str = ""

    def severity_rank(self) -> int:
        return SEVERITY_ORDER.index(self.severity) if self.severity in SEVERITY_ORDER else 0


@dataclass
class Finding(Serialisable):
    """Everything we concluded about one entity in one run."""
    entity: Entity
    signals: list[Signal] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    total_score: float = 0.0
    severity: str = "info"
    run_id: str = ""
    generated_at: str = field(default_factory=_now)

    @property
    def obligated_total(self) -> float:
        return sum(c.award_amount for c in self.contracts)

    @property
    def new_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.is_new]

    def by_category(self) -> dict[str, list[Signal]]:
        out: dict[str, list[Signal]] = {}
        for s in self.signals:
            out.setdefault(s.category, []).append(s)
        for v in out.values():
            v.sort(key=lambda s: (-s.severity_rank(), -s.score))
        return out

    def top_officer(self) -> ContractingOfficer:
        """Address the notice to the KO on the largest addressable award."""
        addressable = [c for c in self.contracts if c.officer.is_addressable]
        if not addressable:
            return ContractingOfficer()
        ranked = sorted(
            addressable,
            key=lambda c: ({"high": 2, "medium": 1, "low": 0}.get(c.officer.confidence, 0),
                           c.award_amount),
            reverse=True)
        return ranked[0].officer
