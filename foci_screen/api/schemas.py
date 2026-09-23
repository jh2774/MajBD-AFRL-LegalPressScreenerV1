"""Request bodies for the API.

Responses are returned as plain dicts: a Finding's payload is already a
serialised dataclass tree, and re-declaring that shape in Pydantic would create
two definitions of the same thing that drift apart.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScreenRequest(BaseModel):
    agency: str = Field(..., min_length=2,
                        examples=["Department of Defense"])
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


class WatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    screen: ScreenRequest


class NoticeDecision(BaseModel):
    """An approve or reject. `body_text` lets a reviewer correct wording first."""
    note: str = ""
    body_text: str = ""
    decided_by: str = ""
