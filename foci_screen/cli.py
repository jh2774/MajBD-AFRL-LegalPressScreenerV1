"""Command line interface.

    foci-screen agencies
    foci-screen screen --agency "Department of the Navy" --months 6 --entities 3
    foci-screen screen --agency "..." --domain "LEIDOS INC=leidos.com"
    foci-screen notify --run <run_id>
    foci-screen history
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import get_config
from .connectors.usaspending import USASpendingConnector
from .httpclient import HttpClient
from .jobs import NOTICE_THRESHOLD, build_screener, queue_notice, severity_at_least
from .models import Finding
from .notify import render
from .notify.gmail import GmailNotifier, status_banner
from .pipeline import ScreenOptions
from .store import Store

SEV_COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m",
             "low": "\033[36m", "info": "\033[2m"}
RESET = "\033[0m"


def _c(severity: str) -> str:
    return f"{SEV_COLOR.get(severity, '')}{severity.upper()}{RESET}"


def _parse_domains(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        name, _, domain = item.partition("=")
        if domain:
            out[name.strip()] = domain.strip()
            out[name.strip().upper()] = domain.strip()
    return out


# ------------------------------------------------------------------ commands

def cmd_agencies(args, cfg) -> int:
    http = HttpClient(cfg)
    names = USASpendingConnector.list_agencies(http)
    needle = (args.filter or "").lower()
    for n in names:
        if not needle or needle in n.lower():
            print(n)
    return 0


def cmd_screen(args, cfg) -> int:
    store = Store(cfg.dsn)
    screener, browser = build_screener(cfg, store)

    opts = ScreenOptions(
        agency=args.agency, sub_agency=args.sub_agency or "",
        months_back=args.months, max_awards=args.awards,
        max_entities=args.entities, keyword=args.keyword or "",
        include_idv=args.include_idv, domains=_parse_domains(args.domain),
        fetch_filing_bodies=not args.fast, min_severity=args.min_severity,
        skip_web=args.no_web)

    def progress(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    print(f"Run configuration: agency={opts.agency!r} window={opts.months_back}mo "
          f"awards<={opts.max_awards} entities<={opts.max_entities}")
    avail = cfg.availability()
    off = [k for k, v in avail.items() if not v]
    if off:
        print(f"Connectors unavailable (no key): {', '.join(off)}")
    print(status_banner(cfg))
    print("-" * 78)

    try:
        result = screener.run(opts, progress=progress)
    finally:
        browser.close()

    print("\n" + "=" * 78)
    print(f"RUN {result.run_id}: {result.stats.get('awards_examined', 0)} awards, "
          f"{result.stats.get('entities_screened', 0)} contractors screened, "
          f"{len(result.findings)} finding(s)")
    for note in result.notes:
        print(f"  note: {note}")
    print("=" * 78)

    for f in result.findings:
        print(f"\n{_c(f.severity)}  {f.entity.name}  (score {f.total_score})")
        officer = f.top_officer()
        addr = (f"{officer.name} <{officer.email}> via {officer.source}"
                if officer.is_addressable else "no KO resolved")
        print(f"  obligated: ${f.obligated_total:,.0f} across {len(f.contracts)} action(s)"
              f" | KO: {addr}")
        for s in f.signals[:6]:
            flag = " [NEW]" if s.is_new else ""
            print(f"    - [{s.severity}] {s.title}{flag}")
            print(f"        {s.rationale[:150]}...")

    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": result.run_id, "agency": opts.agency,
               "stats": result.stats, "notes": result.notes,
               "findings": [f.to_dict() for f in result.findings]}
    json_path = out_dir / f"run_{result.run_id}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nFull results: {json_path}")

    # Queue the same notices the API would, so the review UI shows this run
    # whichever way it was started.
    queued = [queue_notice(store, f, result.run_id) for f in result.findings
              if severity_at_least(f.severity, NOTICE_THRESHOLD)]
    if queued:
        print(f"{len(queued)} notice(s) queued for review "
              f"(severity {NOTICE_THRESHOLD}+).")

    if args.notify and result.findings:
        _notify(cfg, store, result.findings, result.run_id)

    store.close()
    return 0


def _notify(cfg, store: Store, findings: list[Finding], run_id: str) -> None:
    notifier = GmailNotifier(cfg)
    print("\n" + status_banner(cfg))
    for f in findings:
        res = notifier.deliver(f, run_id=run_id)
        store.log_notification(run_id, f.entity.key(), res.recipient,
                               res.subject, res.status, res.detail)
        location = f" -> {res.path}" if res.path else ""
        print(f"  [{res.status}] {f.entity.name} -> {res.recipient or '(none)'}"
              f"{location}")
        if res.detail:
            print(f"      {res.detail}")


def cmd_notify(args, cfg) -> int:
    store = Store(cfg.dsn)
    path = Path(cfg.out_dir) / f"run_{args.run}.json"
    if not path.is_file():
        print(f"No stored run at {path}", file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    from .models import Contract, ContractingOfficer, Entity, Signal

    findings: list[Finding] = []
    for raw in data.get("findings", []):
        ent = Entity(**raw["entity"])
        contracts = []
        for c in raw.get("contracts", []):
            officer = ContractingOfficer(**c.pop("officer", {}) or {})
            contracts.append(Contract(officer=officer, **c))
        signals = [Signal(**s) for s in raw.get("signals", [])]
        findings.append(Finding(entity=ent, contracts=contracts, signals=signals,
                                total_score=raw.get("total_score", 0.0),
                                severity=raw.get("severity", "info"),
                                run_id=raw.get("run_id", args.run)))
    if args.preview:
        for f in findings:
            print("=" * 78)
            print(f"SUBJECT: {render.subject_for(f)}")
            print(f"TO:      {f.top_officer().email or '(unresolved)'}")
            print("-" * 78)
            print(render.render_text(f, args.run))
            print()
        return 0
    _notify(cfg, store, findings, args.run)
    store.close()
    return 0


def cmd_history(args, cfg) -> int:
    store = Store(cfg.dsn)
    rows = store.recent_findings(args.limit)
    if not rows:
        print("No findings recorded yet.")
    for r in rows:
        print(f"{r['created_at']}  {r['run_id']}  {_c(r['severity']):<22} "
              f"{r['score']:>7.1f}  {r['entity_name']}")
    store.close()
    return 0


# --------------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="foci-screen",
        description="Screen government contracts and contractor disclosures for "
                    "FOCI and intellectual-property risk changes.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("agencies", help="list awarding agency names accepted by --agency")
    a.add_argument("--filter", help="substring filter")
    a.set_defaults(func=cmd_agencies)

    s = sub.add_parser("screen", help="run a screen against one agency")
    s.add_argument("--agency", required=True, help='e.g. "Department of Defense"')
    s.add_argument("--sub-agency", help='e.g. "Department of the Navy"')
    s.add_argument("--months", type=int, default=12, help="lookback window (default 12)")
    s.add_argument("--awards", type=int, default=25, help="max awards to pull (default 25)")
    s.add_argument("--entities", type=int, default=5, help="max contractors to screen")
    s.add_argument("--keyword", help="restrict awards by keyword")
    s.add_argument("--include-idv", action="store_true", help="include IDV vehicles")
    s.add_argument("--domain", action="append", metavar='"NAME=domain.com"',
                   help="map a contractor to its website for IR/press/legal watching")
    s.add_argument("--min-severity", default="low",
                   choices=["info", "low", "medium", "high", "critical"])
    s.add_argument("--fast", action="store_true",
                   help="skip downloading filing bodies (labels only)")
    s.add_argument("--no-web", action="store_true", help="skip company website crawl")
    s.add_argument("--notify", action="store_true",
                   help="render notices (Gmail drafts only if GMAIL_ENABLED=true)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_screen)

    n = sub.add_parser("notify", help="render or draft notices for a stored run")
    n.add_argument("--run", required=True, help="run id from a previous screen")
    n.add_argument("--preview", action="store_true",
                   help="print the notices to stdout and exit")
    n.set_defaults(func=cmd_notify)

    h = sub.add_parser("history", help="recent findings")
    h.add_argument("--limit", type=int, default=25)
    h.set_defaults(func=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")
    cfg = get_config()
    if "example.com" in cfg.user_agent:
        print("WARNING: set FOCI_USER_AGENT to 'yourtool/1.0 (your.email@org)' — "
              "SEC blocks generic agents.\n", file=sys.stderr)
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
