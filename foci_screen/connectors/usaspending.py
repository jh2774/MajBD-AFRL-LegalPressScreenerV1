"""USAspending.gov — contract discovery (step 1).

Keyless. `spending_by_award` gives the population; `awards/{id}` gives the
solicitation number that bridges to FPDS and SAM for the contracting officer.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from ..models import Contract, ContractingOfficer
from ..risk.lexicon import find_ip_clauses

log = logging.getLogger("foci.usaspending")

BASE = "https://api.usaspending.gov/api/v2"
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]           # definitive/IDV contract awards
IDV_TYPE_CODES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C",
                  "IDV_D", "IDV_E"]

FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI", "Award Amount",
    "Awarding Agency", "Awarding Sub Agency", "Start Date", "End Date",
    "Description", "generated_internal_id", "Contract Award Type", "NAICS", "PSC",
]

# Subawards are a separate field vocabulary; the prime-award names are rejected.
# This list is what the API itself reports as valid.
SUBAWARD_FIELDS = [
    "Sub-Award ID", "Sub-Award Type", "Sub-Awardee Name", "Sub-Recipient UEI",
    "Sub-Award Amount", "Sub-Award Date", "Sub-Award Description",
    "Awarding Agency", "Awarding Sub Agency",
    "Prime Award ID", "Prime Recipient Name", "Prime Award Recipient UEI",
]


# Tokens that mark a recipient as an organisation rather than a person.
ORG_MARKERS = {
    "inc", "inc.", "llc", "l.l.c.", "llp", "ltd", "ltd.", "corp", "corp.",
    "corporation", "company", "co", "co.", "incorporated", "limited", "plc",
    "gmbh", "ag", "sa", "nv", "bv", "pty", "group", "holdings", "technologies",
    "systems", "services", "solutions", "industries", "associates", "partners",
    "enterprises", "international", "laboratories", "labs", "university",
    "institute", "foundation", "trust", "manufacturing", "engineering",
    "consulting", "construction", "logistics", "works", "&", "and",
}


def looks_like_an_individual(name: str) -> bool:
    """Is this recipient a natural person rather than a business?

    Subaward reporting includes sole proprietors and individual consultants —
    a live Navy query returned "JOSHUA D GOODWIN" among the suppliers. Screening
    a named private individual is a different act from screening a company: it
    means running sanctions and securities searches against a person and
    drafting a notice about them to their customer's contracting officer. The
    tool declines to do that by default.

    Deliberately cautious in the direction of *not* screening: a two-or-three
    word name with no organisational token is treated as a person even though
    some real firms are named that way — "Blue Origin" and "Shield AI" both
    trip it. Missing a supplier costs coverage and is visible in the run notes,
    which name what was skipped so the gap can be seen and overridden; the
    opposite error profiles a private individual and is not visible at all.

    The reliable fix is SAM.gov's entity registration, which states whether a
    registrant is a sole proprietor. That needs `SAM_API_KEY`; until then this
    is a heuristic and is described as one.
    """
    tokens = [t for t in (name or "").lower().replace(",", " ").split() if t]
    if not tokens or len(tokens) > 4:
        return False
    if any(t.strip(".") in ORG_MARKERS or t in ORG_MARKERS for t in tokens):
        return False
    # A single-letter token is a middle initial and is close to conclusive:
    # "JOSHUA D GOODWIN", "SMITH JOHN A".
    if any(len(t.strip(".")) == 1 and t.strip(".").isalpha() for t in tokens):
        return True
    return len(tokens) in (2, 3)


class USASpendingConnector:
    name = "usaspending"

    def __init__(self, http) -> None:
        self.http = http

    # ------------------------------------------------------------------ search
    def search_awards(self, agency: str, *, months_back: int = 12, limit: int = 25,
                      tier: str = "toptier", sub_agency: str = "",
                      include_idv: bool = False, keyword: str = "") -> list[Contract]:
        end = date.today()
        start = end - timedelta(days=30 * months_back)
        agencies = [{"type": "awarding", "tier": tier, "name": agency}]
        if sub_agency:
            agencies.append({"type": "awarding", "tier": "subtier", "name": sub_agency})

        codes = list(CONTRACT_TYPE_CODES) + (IDV_TYPE_CODES if include_idv else [])
        filters: dict = {
            "award_type_codes": codes,
            "agencies": agencies,
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        }
        if keyword:
            filters["keywords"] = [keyword]

        payload = {"filters": filters, "fields": FIELDS, "page": 1,
                   "limit": min(limit, 100), "sort": "Award Amount",
                   "order": "desc", "subawards": False}

        resp = self.http.post(f"{BASE}/search/spending_by_award/", json_body=payload)
        data = resp.get("json") or {}
        if resp.get("status") != 200:
            log.warning("USAspending search failed (%s): %s",
                        resp.get("status"), (resp.get("text") or "")[:200])
            return []

        contracts: list[Contract] = []
        for row in data.get("results", []):
            naics = row.get("NAICS") or {}
            psc = row.get("PSC") or {}
            contracts.append(Contract(
                award_id=str(row.get("Award ID") or ""),
                piid=str(row.get("Award ID") or ""),
                generated_internal_id=row.get("generated_internal_id") or "",
                recipient_name=row.get("Recipient Name") or "",
                recipient_uei=row.get("Recipient UEI") or "",
                awarding_agency=row.get("Awarding Agency") or "",
                awarding_sub_agency=row.get("Awarding Sub Agency") or "",
                award_amount=float(row.get("Award Amount") or 0),
                start_date=row.get("Start Date") or "",
                end_date=row.get("End Date") or "",
                description=row.get("Description") or "",
                naics_code=str(naics.get("code") or ""),
                naics_description=naics.get("description") or "",
                psc_code=str(psc.get("code") or ""),
                psc_description=psc.get("description") or "",
                source_url=f"https://www.usaspending.gov/award/{row.get('generated_internal_id')}",
            ))
        return contracts

    # --------------------------------------------------------------- subawards
    def search_subawards(self, agency: str, *, months_back: int = 12, limit: int = 25,
                         tier: str = "toptier", sub_agency: str = "",
                         keyword: str = "") -> list[Contract]:
        """Recent subcontracts under prime awards for this agency.

        Two things about this endpoint, both established by querying it rather
        than by reading the documentation:

        **The sub-agency filter is ignored.** Asking for Department of Defense
        plus a Navy subtier returns byte-identical results to asking for the
        Department of Defense alone — 42 Navy, 26 Air Force, 19 Army out of 100
        in the sample used to check. The filter is therefore applied here, after
        the fact, or a screen scoped to one command would quietly report another
        command's suppliers as its own.

        **Ordering is by date, not amount.** Subaward values come from the
        prime's own FSRS reporting and are often wrong by orders of magnitude,
        so the largest-first ordering used for prime awards would rank the
        worst data first. Most-recent-first also matches what the tool is for:
        what changed lately.
        """
        end = date.today()
        start = end - timedelta(days=30 * months_back)
        filters: dict = {
            "award_type_codes": list(CONTRACT_TYPE_CODES),
            "agencies": [{"type": "awarding", "tier": tier, "name": agency}],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
        }
        if keyword:
            filters["keywords"] = [keyword]

        # Over-fetch: the sub-agency filter has to be applied here, so ask for
        # more than is needed and narrow afterwards.
        want = min(limit, 100)
        payload = {"filters": filters, "fields": SUBAWARD_FIELDS, "page": 1,
                   "limit": 100 if sub_agency else want,
                   "sort": "Sub-Award Date", "order": "desc", "subawards": True}

        resp = self.http.post(f"{BASE}/search/spending_by_award/", json_body=payload)
        if resp.get("status") != 200:
            log.warning("USAspending subaward search failed (%s): %s",
                        resp.get("status"), (resp.get("text") or "")[:200])
            return []

        wanted_sub = sub_agency.strip().lower()
        out: list[Contract] = []
        for row in (resp.get("json") or {}).get("results", []):
            row_sub = (row.get("Awarding Sub Agency") or "").strip().lower()
            if wanted_sub and row_sub != wanted_sub:
                continue
            if (row.get("Sub-Award Type") or "sub-contract") != "sub-contract":
                continue
            sub_id = str(row.get("Sub-Award ID") or "")
            out.append(Contract(
                award_id=sub_id,
                piid=sub_id,
                generated_internal_id="",
                recipient_name=row.get("Sub-Awardee Name") or "",
                recipient_uei=row.get("Sub-Recipient UEI") or "",
                awarding_agency=row.get("Awarding Agency") or "",
                awarding_sub_agency=row.get("Awarding Sub Agency") or "",
                award_amount=float(row.get("Sub-Award Amount") or 0),
                start_date=row.get("Sub-Award Date") or "",
                description=row.get("Sub-Award Description") or "",
                is_subaward=True,
                amount_is_self_reported=True,
                prime_award_id=str(row.get("Prime Award ID") or ""),
                prime_recipient_name=row.get("Prime Recipient Name") or "",
                prime_generated_internal_id=row.get("prime_award_generated_internal_id") or "",
                source_url=(f"https://www.usaspending.gov/award/"
                            f"{row.get('prime_award_generated_internal_id') or ''}"),
            ))
            if len(out) >= want:
                break
        return out

    # ------------------------------------------------------------------ detail
    def enrich(self, contract: Contract) -> Contract:
        """Fill in solicitation id, parent entity and country from award detail."""
        if not contract.generated_internal_id:
            return contract
        resp = self.http.get(f"{BASE}/awards/{contract.generated_internal_id}/")
        data = resp.get("json") or {}
        if not data:
            return contract

        ltcd = data.get("latest_transaction_contract_data") or {}
        contract.solicitation_id = ltcd.get("solicitation_identifier") or ""
        contract.foreign_funding = ltcd.get("foreign_funding_description") or ""
        dom = (ltcd.get("domestic_or_foreign_entity_description") or "")
        if "foreign" in dom.lower():
            contract.foreign_owned_and_located = True

        rec = data.get("recipient") or {}
        contract.parent_recipient_name = rec.get("parent_recipient_name") or ""
        contract.parent_recipient_uei = rec.get("parent_recipient_uei") or ""
        loc = rec.get("location") or {}
        contract.recipient_country = loc.get("location_country_code") or ""

        # Requirement text is the only place FAR/DFARS clause hints show up here.
        blob = " ".join(filter(None, [contract.description, data.get("description") or ""]))
        contract.ip_clause_hits = find_ip_clauses(blob)

        if not contract.officer.name:
            contract.officer = ContractingOfficer()
        return contract

    def recipient_profile(self, recipient_hash: str) -> dict:
        resp = self.http.get(f"{BASE}/recipient/duns/{recipient_hash}/")
        return resp.get("json") or {}

    @staticmethod
    def list_agencies(http) -> list[str]:
        resp = http.get(f"{BASE}/references/toptier_agencies/")
        data = resp.get("json") or {}
        return sorted(a.get("agency_name", "") for a in data.get("results", []))
