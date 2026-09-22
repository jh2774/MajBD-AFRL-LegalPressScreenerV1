"""Positive control: prove the detection chain fires on real filings.

A screening tool that has been tuned for precision can go silent without
anyone noticing. This runs the live connectors against registrants that are
known to have the thing we look for, and fails loudly if the rules do not fire.

Run it after changing lexicons, weights, or the evidence-quality damper:

    python tools/positive_control.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from foci_screen.config import get_config  # noqa: E402
from foci_screen.connectors.sec_edgar import EdgarConnector  # noqa: E402
from foci_screen.httpclient import HttpClient  # noqa: E402
from foci_screen.models import Contract, ContractingOfficer, Entity  # noqa: E402
from foci_screen.risk import engine, lexicon  # noqa: E402

# Registrants that genuinely filed an Intellectual Property Security Agreement.
# Verified against EDGAR full-text search at build time.
CASES = [
    ("Sovos Brands, Inc.", "0001856608"),
]


def contract_fixture() -> Contract:
    c = Contract(
        award_id="TEST-0001", piid="TEST-0001", recipient_name="Test Co",
        award_amount=10_000_000.0,
        description="R&D with technical data delivered under DFARS 252.227-7013.",
        officer=ContractingOfficer(name="Test KO", email="ko@example.gov",
                                   source="fixture", confidence="high"))
    c.ip_clause_hits = lexicon.find_ip_clauses(c.description)
    return c


def main() -> int:
    cfg = get_config()
    http = HttpClient(cfg)
    edgar = EdgarConnector(http)
    contracts = [contract_fixture()]
    failures: list[str] = []

    for name, cik in CASES:
        print(f"\n=== {name} (CIK {cik})")
        entity = Entity(name=name, cik=cik)
        docs = edgar.full_text_search(name, cik=cik)
        print(f"    documents retrieved: {len(docs)}")
        if not docs:
            failures.append(f"{name}: full-text search returned no documents")
            continue

        for d in docs[:2]:
            print(f"    - {d.doc_type:<6} {d.published}  {len(d.text):>7} chars  {d.url[:88]}")
            assert d.meta.get("verified_registrant"), "unverified registrant leaked through"
            assert cik.zfill(10) == str(d.meta["cik"]).zfill(10)

        signals = engine.evaluate_documents([(d, None) for d in docs], entity, contracts)
        ip = [s for s in signals if s.category == "IP_COLLATERAL"]
        print(f"    signals: {len(signals)} total, {len(ip)} IP_COLLATERAL")
        for s in sorted(ip, key=lambda x: -x.score)[:3]:
            print(f"      [{s.severity:>8}] {s.score:>6}  {s.title}")
            print(f"        evidence: {' '.join(s.evidence.split())[:180]}")

        if not ip:
            failures.append(f"{name}: no IP_COLLATERAL signal on a known IP security agreement")
            continue

        finding = engine.build_finding(entity, contracts, signals)
        print(f"    finding: {finding.severity.upper()} (score {finding.total_score})")
        if finding.severity in ("info", "low"):
            failures.append(
                f"{name}: executed IP security agreement scored only {finding.severity}")

    print("\n" + "=" * 70)
    if failures:
        print("POSITIVE CONTROL FAILED — the screen has gone blind to real hits:")
        for f in failures:
            print(f"  * {f}")
        return 1
    print("POSITIVE CONTROL PASSED — detection chain fires on real filings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
