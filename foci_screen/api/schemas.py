"""Request bodies for the API.

Responses are returned as plain dicts: a Finding's payload is already a
serialised dataclass tree, and re-declaring that shape in Pydantic would create
two definitions of the same thing that drift apart.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ScreenRequest(BaseModel):
    """Either an agency or a named contractor. Both is allowed and narrows.

    `agency` was required, which meant the only way into the database was to
    screen a department and take whoever turned up in it. A contractor
    somebody already had in mind could not be reached at all.
    """
    agency: str = Field("", examples=["Department of Defense"])
    recipient: str = Field("", max_length=200,
                           examples=["LOCKHEED MARTIN CORPORATION"])
    sub_agency: str = ""
    months_back: int = Field(12, ge=1, le=60)
    max_awards: int = Field(25, ge=1, le=500)
    max_entities: int = Field(5, ge=1, le=100)
    keyword: str = ""
    include_idv: bool = False
    # Subcontractors to screen as well, newest subaward first. Subaward values
    # are self-reported by the prime and unreliable, so these are never ranked
    # by dollars and any notice goes to the prime's contracting officer.
    max_subaward_entities: int = Field(0, ge=0, le=50)
    # entity name -> domain, for the pages a company publishes itself
    domains: dict[str, str] = Field(default_factory=dict)
    fetch_filing_bodies: bool = True
    min_severity: str = Field("low", pattern="^(info|low|medium|high|critical)$")
    skip_web: bool = False

    @model_validator(mode="after")
    def needs_a_subject(self) -> ScreenRequest:
        agency = (self.agency or "").strip()
        recipient = (self.recipient or "").strip()
        if not agency and not recipient:
            raise ValueError(
                "Name an agency to screen its awards, or a recipient to screen "
                "one contractor wherever its awards come from.")
        if agency and len(agency) < 2:
            raise ValueError("That agency name is too short to match anything.")
        if recipient and len(recipient) < 2:
            raise ValueError("That contractor name is too short to match anything.")
        self.agency, self.recipient = agency, recipient
        return self


class WatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    screen: ScreenRequest


class PortfolioRequest(BaseModel):
    """Companies to mint a portfolio key for. Entity keys, or bare names."""
    name: str = Field("Portfolio", max_length=120)
    companies: list[str] = Field(..., min_length=1, max_length=500)


class PortfolioKey(BaseModel):
    key: str = Field(..., min_length=1, max_length=64_000)


class PortfolioEdit(BaseModel):
    """Add or drop companies, returning a new key.

    An empty `key` starts a portfolio, so the first "add to portfolio" on a
    contractor page does not need the reader to have built one first.
    """
    key: str = Field("", max_length=64_000)
    name: str = Field("Portfolio", max_length=120)
    add: list[str] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)


class NoticeDecision(BaseModel):
    """An approve or reject. `body_text` lets a reviewer correct wording first."""
    note: str = ""
    body_text: str = ""
    decided_by: str = ""


class RuleSetting(BaseModel):
    """A tenant's override for one rule.

    `weight` scales the rule's score; the severity band is recomputed from the
    result, so a rule scored down cannot keep a label it no longer earns.
    """
    enabled: bool = True
    weight: float = Field(1.0, ge=0.0, le=5.0)
    note: str = ""
    decided_by: str = ""


class IdentityDecision(BaseModel):
    """A human verdict on which SEC registrant a contractor is.

    `rejected` means no registrant has been identified, and the screen stops
    attributing filings to this contractor — not merely "unconfirmed".
    """
    status: Literal["auto", "confirmed", "rejected"]
    # 10 digits, zero-padded, as EDGAR returns them.
    cik: str | None = Field(None, pattern=r"^\d{10}$")
    matched_title: str = ""
    note: str = ""
    decided_by: str = ""


class SignalDisposition(BaseModel):
    """A reviewer's verdict on one signal — the tool's only labelled data."""
    signal_id: str = Field(..., min_length=4, max_length=64)
    entity_key: str = Field(..., min_length=1, max_length=128)
    rule_id: str = Field(..., min_length=1, max_length=64)
    verdict: Literal["true_positive", "false_positive", "unclear"]
    category: str = ""
    severity: str = ""
    note: str = ""
    decided_by: str = ""
    run_id: str = ""
