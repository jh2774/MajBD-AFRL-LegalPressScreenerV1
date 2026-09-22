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
