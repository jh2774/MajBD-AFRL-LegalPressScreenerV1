"""Command line interface.

    foci-screen agencies
    foci-screen screen --agency "Department of the Navy" --months 6 --entities 3
    foci-screen screen --agency "..." --domain "LEIDOS INC=leidos.com"
    foci-screen notify --run <run_id>
    foci-screen gmail-auth
    foci-screen deliver-notices
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
from .jobs import build_screener, raise_notices
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

    if not (args.agency or args.recipient):
        print("Name --agency, --recipient, or both.")
        return 2

    opts = ScreenOptions(
        agency=args.agency or "", recipient=args.recipient or "",
        sub_agency=args.sub_agency or "",
        months_back=args.months, max_awards=args.awards,
        max_entities=args.entities, keyword=args.keyword or "",
        include_idv=args.include_idv, max_subaward_entities=args.subcontractors,
        domains=_parse_domains(args.domain),
        fetch_filing_bodies=not args.fast, min_severity=args.min_severity,
        skip_web=args.no_web)

    def progress(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    subject = (f"recipient={opts.recipient!r}" if opts.recipient
               else f"agency={opts.agency!r}")
    if opts.recipient and opts.agency:
        subject += f" agency={opts.agency!r}"
    print(f"Run configuration: {subject} window={opts.months_back}mo "
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

    # Queue the same notices the API would, under the same policy, so the
    # review queue shows this run whichever way it was started. This used to
    # carry its own copy of the old threshold, which meant a screen from the
    # command line ignored the tenant's settings and re-notified everything.
    queued, held = raise_notices(store, result.findings, result.run_id)
    if queued:
        print(f"{len(queued)} notice(s) queued for review.")
    for reason, count in held.items():
        print(f"  {count} finding(s) held back: {reason}")

    if args.notify and result.findings:
        _notify(cfg, store, result.findings, result.run_id)

    store.close()
    return 0


def _notify(cfg, store: Store, findings: list[Finding], run_id: str) -> None:
    """Write each finding's notice to disk for reading. Nothing reaches Gmail.

    Gmail only ever receives a notice a person has approved in the review
    queue; `deliver-notices` hands those over.
    """
    notifier = GmailNotifier(cfg)
    print("\nWriting notices as .eml files to read. Nothing is drafted or sent from "
          "here: approve them in the review queue, then `foci-screen deliver-notices`.")
    for f in findings:
        res = notifier.render_finding(f, run_id=run_id)
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


def cmd_gmail_auth(args, cfg) -> int:
    """Sign in to Gmail once, here, so the service never has to."""
    if not cfg.gmail_enabled:
        print("GMAIL_ENABLED is false. Set it (and GMAIL_SENDER) first, so the "
              "sign-in asks for the right permissions.", file=sys.stderr)
        return 2
    notifier = GmailNotifier(cfg)
    if notifier._connect(interactive=True) is None:
        print(f"Not signed in: {notifier.connect_error}", file=sys.stderr)
        return 1
    print(f"Signed in. The sign-in is saved at {cfg.gmail_token}; on a server, put "
          f"that file at the path GMAIL_TOKEN names.")
    print(status_banner(cfg))
    return 0


def cmd_deliver_notices(args, cfg) -> int:
    """Hand every approved, undelivered notice to Gmail."""
    store = Store(cfg.dsn, tenant_id=args.tenant)
    notices = store.undelivered_notices(args.limit)
    print(status_banner(cfg))
    if not notices:
        print("No approved notices are waiting to be delivered.")
        store.close()
        return 0
    notifier = GmailNotifier(cfg)
    failed = 0
    for notice in notices:
        res = notifier.deliver_notice(notice, interactive=False)
        store.record_notice_delivery(notice["notice_id"], res.status,
                                     delivered_to=res.recipient, detail=res.detail)
        failed += res.status == "error"
        print(f"  [{res.status}] {notice['entity_name']} -> {res.recipient or '(none)'}")
        if res.detail:
            print(f"      {res.detail}")
    store.close()
    return 1 if failed else 0


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


def cmd_mail_suppressions(args, cfg) -> int:
    """Addresses alerts are not sent to, because the mail service reported them."""
    from .notify.bounces import describe

    store = Store(cfg.dsn)
    try:
        if args.clear:
            outcome = store.clear_suppression(args.clear, allow_complaint=args.complaint)
            if outcome == "complaint":
                print(f"{args.clear} marked an alert as spam. Clear it only if they have "
                      f"asked to be mailed again, with --complaint.", file=sys.stderr)
                return 2
            if outcome == "not_found":
                print(f"{args.clear} is not suppressed.", file=sys.stderr)
                return 1
            print(f"Cleared. Alerts will go to {args.clear} again.")
            return 0
        rows = store.suppressed()
        if not rows:
            print("No addresses are suppressed.")
        for row in rows.values():
            print(f"  [{row['kind']}] {describe(row)}")
        return 0
    finally:
        store.close()


def cmd_add_user(args, cfg) -> int:
    """Create a sign-in account. The way in when FOCI_SIGNUP is closed."""
    import getpass
    import os

    from . import accounts

    try:
        email = accounts.normalise_email(args.email)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # From the environment for scripts; otherwise asked for, never an
    # argument, where the shell would keep it in its history.
    password = os.environ.get("FOCI_NEW_PASSWORD") or getpass.getpass(
        f"Password for {email}: ")
    try:
        accounts.check_password(password)
    except accounts.AccountError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    tenant = args.workspace or accounts.new_tenant_id()
    store = Store(cfg.dsn)
    try:
        store.create_user(email=email, password_hash=accounts.hash_password(password),
                          tenant_id=tenant, role="owner", organisation=args.organisation,
                          verified=True)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"Created {email}, with workspace \"{tenant}\""
          + (" — the data already screened under that name is theirs to see."
             if args.workspace else " (new and empty)."))
    return 0


def cmd_copy_db(args, cfg) -> int:
    """Copy every table from one database to another — for moving hosts."""
    import os

    from . import dbcopy

    # From the environment by default, so a database password is never typed
    # where the shell keeps a history of it.
    source = args.source or os.environ.get("SOURCE_DATABASE_URL", "").strip()
    target = args.target or cfg.dsn
    if not source:
        print("Nothing to copy from. Set SOURCE_DATABASE_URL to the old database's "
              "address (or pass --from).", file=sys.stderr)
        return 2

    print(f"From: {dbcopy.describe(source)}")
    print(f"To:   {dbcopy.describe(target)}")
    try:
        report = dbcopy.copy_database(source, target, allow_nonempty=args.allow_nonempty,
                                      progress=print)
    except dbcopy.CopyRefused as exc:
        print(f"\nNot started: {exc}", file=sys.stderr)
        return 2

    rows = sum(r["target"] for r in report.values())
    short = dbcopy.mismatches(report)
    if short:
        print("\nINCOMPLETE — the destination has fewer rows than the source in:",
              file=sys.stderr)
        for line in short:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"\nDone. {rows:,} row(s) across {len(report)} table(s); every table's count "
          f"matches the source.")
    return 0


def cmd_import_patent_assignments(args, cfg) -> int:
    """Build the patent-lien index from the USPTO Patent Assignment Dataset."""
    from .connectors import uspto_dataset

    target = args.out or cfg.patent_assignments_index
    print(f"From: {args.source}\nTo:   {target}")
    try:
        report = uspto_dataset.import_dataset(args.source, target)
    except (uspto_dataset.DatasetError, FileNotFoundError) as exc:
        print(f"\nNot imported: {exc}", file=sys.stderr)
        return 2
    print(f"\nDone. {report['records']:,} security interest and release record(s), "
          f"{report['parties']:,} part(ies), {report['properties']:,} patent link(s).")
    print(f"Recorded through {report['latest_recorded'] or 'an unknown date'}: liens "
          f"recorded after that are not in this dataset.")
    if target == cfg.patent_assignments_index:
        print("Screens will use it from now on.")
    else:
        print(f"Set FOCI_PATENT_ASSIGNMENTS={target} so screens use it.")
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
    s.add_argument("--agency", help='e.g. "Department of Defense"')
    s.add_argument("--recipient", metavar="NAME",
                   help='screen one contractor wherever its awards come from, '
                        'e.g. "LOCKHEED MARTIN CORPORATION" or its UEI')
    s.add_argument("--sub-agency", help='e.g. "Department of the Navy"')
    s.add_argument("--months", type=int, default=12, help="lookback window (default 12)")
    s.add_argument("--awards", type=int, default=25, help="max awards to pull (default 25)")
    s.add_argument("--entities", type=int, default=5, help="max contractors to screen")
    s.add_argument("--keyword", help="restrict awards by keyword")
    s.add_argument("--include-idv", action="store_true", help="include IDV vehicles")
    s.add_argument("--subcontractors", type=int, default=0, metavar="N",
                   help="also screen N subcontractors, newest subaward first "
                        "(notices go to the prime's contracting officer)")
    s.add_argument("--domain", action="append", metavar='"NAME=domain.com"',
                   help="map a contractor to its website for IR/press/legal watching")
    s.add_argument("--min-severity", default="low",
                   choices=["info", "low", "medium", "high", "critical"])
    s.add_argument("--fast", action="store_true",
                   help="skip downloading filing bodies (labels only)")
    s.add_argument("--no-web", action="store_true", help="skip company website crawl")
    s.add_argument("--notify", action="store_true",
                   help="also write each notice as an .eml file to read "
                        "(never drafted or sent; approve in the review queue)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_screen)

    n = sub.add_parser("notify", help="write a stored run's notices as .eml files to read")
    n.add_argument("--run", required=True, help="run id from a previous screen")
    n.add_argument("--preview", action="store_true",
                   help="print the notices to stdout and exit")
    n.set_defaults(func=cmd_notify)

    g = sub.add_parser("gmail-auth", help="sign in to Gmail once and save token.json")
    g.set_defaults(func=cmd_gmail_auth)

    d = sub.add_parser("deliver-notices",
                       help="hand approved notices that have not been delivered to Gmail")
    d.add_argument("--tenant", default="default", help="whose notices (default: default)")
    d.add_argument("--limit", type=int, default=200)
    d.set_defaults(func=cmd_deliver_notices)

    h = sub.add_parser("history", help="recent findings")
    h.add_argument("--limit", type=int, default=25)
    h.set_defaults(func=cmd_history)

    m = sub.add_parser("mail-suppressions",
                       help="list addresses alerts skip because they bounced or complained")
    m.add_argument("--clear", metavar="EMAIL", help="mail this address again")
    m.add_argument("--complaint", action="store_true",
                   help="allow --clear to lift a spam complaint (only if they asked)")
    m.set_defaults(func=cmd_mail_suppressions)

    u = sub.add_parser("add-user", help="create a sign-in account (email and password)")
    u.add_argument("email")
    u.add_argument("--workspace", metavar="TENANT",
                   help='attach the account to existing data, e.g. "default" for what '
                        "the command line and API keys have screened (default: a new, "
                        "empty workspace)")
    u.add_argument("--organisation", metavar="NAME", default="",
                   help="name of the organisation, when it is a new one (default: the "
                        "address's domain)")
    u.set_defaults(func=cmd_add_user)

    c = sub.add_parser(
        "copy-db", help="copy every table to another database, for moving hosts",
        description="Copies all data from one database to another and checks the row "
                    "counts. The source is only read. The destination gets the current "
                    "schema and must be empty.")
    c.add_argument("--from", dest="source", metavar="URL",
                   help="the database to copy from (default: $SOURCE_DATABASE_URL)")
    c.add_argument("--to", dest="target", metavar="URL",
                   help="the database to copy into (default: the configured one — "
                        "DATABASE_URL, or the Cloud SQL settings)")
    c.add_argument("--allow-nonempty", action="store_true",
                   help="continue although the destination already has rows; only for "
                        "re-running an interrupted copy")
    c.set_defaults(func=cmd_copy_db)

    u = sub.add_parser(
        "import-patent-assignments",
        help="index recorded patent liens from the USPTO Patent Assignment Dataset",
        description="Reads the USPTO Patent Assignment Dataset, downloaded by hand from "
                    "uspto.gov, and keeps its security interests and releases so screens "
                    "can check for patent liens without a USPTO API key. Give it the "
                    "combined csv.zip, or a folder of the per-table downloads.")
    u.add_argument("source", help="csv.zip, or a folder of *.csv.zip / *.csv files")
    u.add_argument("--out", metavar="PATH",
                   help="where to write the index (default: $FOCI_PATENT_ASSIGNMENTS, "
                        "or patent_assignments.db)")
    u.set_defaults(func=cmd_import_patent_assignments)
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
