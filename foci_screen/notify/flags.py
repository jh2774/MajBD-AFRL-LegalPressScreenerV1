"""How an alert email sets out what it found: one line per flag, then the
detail under it.

    [HIGH] FLAG 1: ACME LLC — Russia named beside ownership language in an SEC
     filing (SEC EDGAR, FPDS)
         What the screen found:
           • [HIGH] Russia nexus beside ownership language
             Found: "…the Company entered into a share purchase agreement with…"
             Why it counts: …
             Document: Form 8-K filed 2026-10-02 — https://www.sec.gov/…
         …

Each line says how serious it is, what was found, where, and which sources
said so. Under it: the evidence word for word, why the rule counts it, the
document to open, the contractor's awards, and what could not be checked —
so a clean source and an unread one are never confused. Severity is the
screen's own (CRITICAL to LOW); a change to a filing that the screen does not
rate is an UPDATE, marked INFO. None of it is a determination: the reader
decides, from the sources.
"""
from __future__ import annotations

import html as html_lib
import re

SEVERITIES = ["critical", "high", "medium", "low", "info"]
LABEL = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW",
         "info": "INFO"}
COLOUR = {"critical": ("#7b1414", "#ffffff"), "high": ("#b03a2e", "#ffffff"),
          "medium": ("#c8a24a", "#1c1c1c"), "low": ("#5d6d7e", "#ffffff"),
          "info": ("#d5d8dc", "#1c1c1c")}

# A source as the reader knows it: (in a sentence, in a list of sources).
SOURCES = {
    "sec_edgar": ("an SEC filing", "SEC EDGAR"),
    "web": ("the company's website", "company website"),
    "feed": ("a company release", "company releases"),
    "email_alert": ("a saved release email", "release emails"),
    "fpds": ("the federal procurement record", "FPDS"),
    "usaspending": ("USAspending", "USAspending"),
    "usaspending/fpds": ("the award record", "USAspending, FPDS"),
    "iapd": ("the SEC's adviser register", "IAPD"),
    "ofac": ("Treasury's sanctions list", "OFAC"),
    "screening_lists": ("a U.S. screening list", "U.S. screening lists"),
    "uspto": ("USPTO assignment records", "USPTO"),
    "uspto_dataset": ("the USPTO assignment dataset", "USPTO"),
    "sam_exclusions": ("SAM.gov's exclusion list", "SAM.gov exclusions"),
    "samgov": ("the SAM.gov registration", "SAM.gov"),
    "correlation": ("", ""),
}
_SOURCE_WORD = re.compile(r"\b(?:in|on) (" + "|".join(
    re.escape(k) for k in SOURCES if k != "correlation") + r") document\b")

# What a kind of finding is, in a few words, for the flag line.
CATEGORY = {"FOCI": "foreign ownership or influence",
            "IP_COLLATERAL": "IP pledged as collateral",
            "IP_TRANSFER": "IP transfer or distress",
            "SANCTIONS": "sanctions or exclusions",
            "STRUCTURE": "corporate structure"}
# A trailing "(SAM.gov)" in a rule's title says what the source list says.
_SOURCE_NOTE = re.compile(r"\s*\((?:SAM\.gov|SEC|FPDS|USAspending|OFAC|USPTO)\)$")

INDENT = " " * 5
POINTS_SHOWN = 5


def rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def from_importance(importance: int) -> str:
    """A rating for a filing change the screen does not score itself, from
    the order its reader gives it (see the `importance` of each change)."""
    if importance >= 6:
        return "high"
    if importance >= 4:
        return "medium"
    if importance >= 2:
        return "low"
    return "info"


def readable(title: str) -> str:
    """A rule's title, with its source's code name in words ("in sec_edgar
    document" -> "in an SEC filing")."""
    return _SOURCE_WORD.sub(lambda m: "in " + SOURCES[m.group(1)][0], title or "")


def source_label(signal: dict) -> str:
    source = signal.get("source", "")
    if source in ("screening_lists", "ofac"):
        listed = re.match(r"Name on the (.+?):", signal.get("title", ""))
        if listed:
            return listed.group(1)
    return SOURCES.get(source, ("", source))[1]


def excerpt(text: str, limit: int = 320) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + "…"


# -------------------------------------------------------------- the items

def notice_parts(notice: dict, finding: dict | None, titles: dict[str, str],
                 base_url: str) -> dict:
    """What a flagged contractor's item says: its flag line and the detail."""
    company = notice.get("entity_name", "")
    signals = sorted((finding or {}).get("signals") or [],
                     key=lambda s: (rank(s.get("severity", "")), -float(s.get("score") or 0)))
    shown = [s for s in signals if s.get("severity") != "info"] or signals
    # One point per kind of evidence: the same rule on three passages of one
    # filing is one thing found, in three places.
    distinct: dict[str, list[dict]] = {}
    for s in shown:
        distinct.setdefault(_SOURCE_NOTE.sub("", readable(s.get("title", ""))), []).append(s)
    if distinct:
        first = next(iter(distinct))
        others = list(dict.fromkeys(
            CATEGORY.get(group[0].get("category", ""), "")
            for title, group in distinct.items() if title != first))
        others = [o for o in others if o]
        more = len(shown) - len(distinct[first])
        flag = f"{company} — {first.rstrip('.')}"
        if more:
            flag += (f", and {more} more finding{'s' if more != 1 else ''}"
                     + (f": {', '.join(others)}" if others else ""))
    else:
        flag = f"{company} was flagged"
    sources = list(dict.fromkeys(x for x in (source_label(s) for s in shown) if x))
    points = []
    for title, group in list(distinct.items())[:POINTS_SHOWN]:
        s = group[0]
        key = s.get("document_key", "")
        points.append({
            "severity": s.get("severity", ""),
            "title": title,
            "found": excerpt(s.get("evidence", "")),
            "why": excerpt(s.get("rationale", ""), 600),
            "document": titles.get(key, ""),
            "url": s.get("source_url", ""),
            "new": any(x.get("is_new") for x in group),
            "also": len(group) - 1,
        })
    facts = []
    contracts = (finding or {}).get("contracts") or []
    if contracts:
        total = sum(float(c.get("award_amount") or 0) for c in contracts)
        agencies = sorted({c.get("awarding_sub_agency") or c.get("awarding_agency") or ""
                           for c in contracts} - {""})
        facts.append(f"{len(contracts)} award{'s' if len(contracts) != 1 else ''} looked at, "
                     f"{_money(total)} in all" + (f", from {', '.join(agencies[:3])}"
                                                    if agencies else "") + ".")
    entity = (finding or {}).get("entity") or {}
    if entity.get("uei"):
        facts.append(f"UEI {entity['uei']}"
                     + (f"; parent {entity['parent_name']}" if entity.get("parent_name")
                        and entity.get("parent_name") != company else "") + ".")
    coverage = [f"{label}: {said}" for label, said in
                ((finding or {}).get("coverage") or {}).items() if said != "checked"]
    key = notice.get("entity_key", "")
    links = [("Open the contractor in FOCI-Screener",
              f"{base_url.rstrip('/')}/#/entity/{key}")] if base_url and key else []
    return {"flag": flag, "sources": sources, "points": points, "facts": facts,
            "coverage": coverage, "links": links,
            "more": sum(len(g) for g in list(distinct.values())[POINTS_SHOWN:])}


def _money(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


# ---------------------------------------------------------------- the email

def heading(item: dict, number: int) -> str:
    severity = item.get("severity", "info")
    what = "UPDATE" if severity == "info" else "FLAG"
    sources = item.get("sources") or []
    return (f"[{LABEL.get(severity, severity.upper())}] {what} {number}: {item['flag']}"
            + (f" ({', '.join(sources)})" if sources else ""))


def _wrap(text: str, indent: str, hang: str = "") -> list[str]:
    """One line, indented. Not wrapped by hand: a mail program wraps it to
    the reader's window, and a sentence or a link broken across lines can no
    longer be searched for or followed."""
    return [indent + " ".join((text or "").split())]


def detail_lines(item: dict) -> list[str]:
    """The detailed report under a flag, indented, as plain text."""
    out: list[str] = []
    if item.get("points"):
        out.append(f"{INDENT}What the screen found:")
        for p in item["points"]:
            label = LABEL.get(p["severity"], p["severity"].upper())
            out += _wrap(f"• [{label}] {p['title']}"
                         + (" — new since the last screen" if p.get("new") else ""),
                         INDENT + "  ", "  ")
            if p.get("found"):
                out += _wrap(f'Found: "{p["found"]}"', INDENT + "    ", "  ")
            if p.get("why"):
                out += _wrap(f"Why it counts: {p['why']}", INDENT + "    ", "  ")
            if p.get("document") or p.get("url"):
                out += _wrap("Document: " + " — ".join(x for x in (p.get("document"),
                                                                  p.get("url")) if x),
                             INDENT + "    ", "  ")
            if p.get("also"):
                out += _wrap(f"Also found in {p['also']} other passage"
                             f"{'s' if p['also'] != 1 else ''}; all are on the "
                             f"contractor's page.", INDENT + "    ", "  ")
        if item.get("more"):
            out.append(f"{INDENT}  …and {item['more']} more on the contractor's page.")
    for line in (item.get("detail"),):
        if line:
            out += _wrap(line, INDENT)
    for fact in item.get("facts") or []:
        out += _wrap(fact, INDENT)
    if item.get("explainer"):
        out += _wrap(f"What this is: {item['explainer']}", INDENT, "  ")
    if item.get("where"):
        out += _wrap(f"Where to look: {item['where']}", INDENT, "  ")
    if item.get("coverage"):
        out.append(f"{INDENT}Not fully checked:")
        for c in item["coverage"]:
            out += _wrap(f"• {c}", INDENT + "  ", "  ")
    for label, url in item.get("links") or []:
        out += _wrap(f"{label}: {url}", INDENT, "  ")
    return out


def counts(items: list[dict]) -> str:
    flags = sum(1 for i in items if i.get("severity") != "info")
    updates = len(items) - flags
    parts = []
    if flags:
        parts.append(f"{flags} flag{'s' if flags != 1 else ''}")
    if updates:
        parts.append(f"{updates} update{'s' if updates != 1 else ''}")
    return " and ".join(parts)


def by_severity(items: list[dict]) -> str:
    tally = {}
    for i in items:
        if i.get("severity") != "info":
            tally[i["severity"]] = tally.get(i["severity"], 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(tally.items(), key=lambda x: rank(x[0])))


def worst(items: list[dict]) -> str:
    return min((i.get("severity", "info") for i in items), key=rank, default="info")


INTRO = ("Each flag is a lead to check, not a finding of wrongdoing. Under each one: "
         "what was found, word for word where there is text, why it counts, and the "
         "document to open.")


def text_body(name: str, items: list[dict], footer: str) -> str:
    tally = by_severity(items)
    lines = [f"FOCI-Screener found {counts(items)} for the companies in \"{name}\""
             + (f": {tally}." if tally else "."), ""]
    # Headings stay on one line each, as a list to scan: a mail program wraps
    # them. The detail under each is wrapped, to keep its indent.
    if len(items) > 1:
        lines += [heading(item, n) for n, item in enumerate(items, 1)]
        lines.append("")
    lines += [INTRO, "", "—" * 24, ""]
    for n, item in enumerate(items, 1):
        lines.append(heading(item, n))
        lines += detail_lines(item)
        lines.append("")
    lines += ["—", footer]
    return "\n".join(lines)


def _pill(severity: str) -> str:
    bg, fg = COLOUR.get(severity, COLOUR["info"])
    return (f'<span style="display:inline-block;background:{bg};color:{fg};'
            f'font-size:11.5px;font-weight:bold;letter-spacing:.04em;padding:1px 7px;'
            f'border-radius:3px">{LABEL.get(severity, severity.upper())}</span>')


def _heading_html(item: dict, number: int, anchor: bool = False) -> str:
    e = html_lib.escape
    severity = item.get("severity", "info")
    what = "UPDATE" if severity == "info" else "FLAG"
    sources = item.get("sources") or []
    title = f"{what} {number}: {e(item['flag'])}"
    if anchor:
        title = f'<a href="#flag-{number}" style="color:#1c1c1c">{title}</a>'
    return (f"{_pill(severity)} <strong>{title}</strong>"
            + (f' <span style="color:#555">({e(", ".join(sources))})</span>'
               if sources else ""))


def html_body(name: str, items: list[dict], footer_html: str) -> str:
    e = html_lib.escape
    tally = by_severity(items)
    parts = ['<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
             'line-height:1.5;color:#1c1c1c;max-width:680px">',
             f"<p>FOCI-Screener found <strong>{e(counts(items))}</strong> for the "
             f"companies in <strong>{e(name)}</strong>"
             + (f": {e(tally)}." if tally else ".") + "</p>"]
    if len(items) > 1:
        parts.append('<ul style="list-style:none;padding:0;margin:0 0 14px">'
                     + "".join(f'<li style="margin:4px 0">{_heading_html(i, n, True)}</li>'
                               for n, i in enumerate(items, 1)) + "</ul>")
    parts.append(f'<p style="color:#555;font-size:13.5px">{e(INTRO)}</p>')
    for n, item in enumerate(items, 1):
        parts.append(f'<div id="flag-{n}" style="margin:20px 0 0">'
                     f"<div>{_heading_html(item, n)}</div>"
                     '<div style="border-left:3px solid #c8a24a;margin:6px 0 0 4px;'
                     'padding:2px 0 2px 14px;font-size:14px">' + _details_html(item)
                     + "</div></div>")
    parts.append(f'<p style="color:#777;font-size:12.5px;margin-top:24px">{footer_html}'
                 "</p></div>")
    return "".join(parts)


def _details_html(item: dict) -> str:
    e = html_lib.escape
    out = []
    if item.get("points"):
        out.append('<div style="margin:4px 0 2px"><em>What the screen found:</em></div>'
                   '<ul style="margin:0 0 6px;padding-left:18px">')
        for p in item["points"]:
            doc = " — ".join(x for x in (e(p.get("document") or ""),
                                         (f'<a href="{e(p["url"])}">{e(p["url"])}</a>'
                                          if p.get("url") else "")) if x)
            out.append(
                '<li style="margin:6px 0">' + _pill(p["severity"]) + f" {e(p['title'])}"
                + (' <em style="color:#555">— new since the last screen</em>'
                   if p.get("new") else "")
                + (f'<div style="color:#333">Found: &ldquo;{e(p["found"])}&rdquo;</div>'
                   if p.get("found") else "")
                + (f'<div style="color:#555">Why it counts: {e(p["why"])}</div>'
                   if p.get("why") else "")
                + (f'<div style="font-size:13px">Document: {doc}</div>' if doc else "")
                + (f'<div style="font-size:13px;color:#555">Also found in {p["also"]} '
                   f'other passage{"s" if p["also"] != 1 else ""}; all are on the '
                   "contractor's page.</div>" if p.get("also") else "")
                + "</li>")
        out.append("</ul>")
        if item.get("more"):
            out.append(f'<div style="color:#555">…and {item["more"]} more on the '
                       "contractor's page.</div>")
    if item.get("detail"):
        out.append(f"<div>{e(item['detail'])}</div>")
    for fact in item.get("facts") or []:
        out.append(f"<div>{e(fact)}</div>")
    if item.get("explainer"):
        out.append(f'<div style="color:#555;font-size:13.5px"><em>What this is:</em> '
                   f"{e(item['explainer'])}</div>")
    if item.get("where"):
        out.append(f'<div style="font-size:13.5px"><em>Where to look:</em> '
                   f"{e(item['where'])}</div>")
    if item.get("coverage"):
        out.append('<div style="margin-top:4px;font-size:13.5px"><em>Not fully checked:'
                   '</em><ul style="margin:0;padding-left:18px">'
                   + "".join(f"<li>{e(c)}</li>" for c in item["coverage"]) + "</ul></div>")
    links = " · ".join(f'<a href="{e(url)}">{e(label)}</a>'
                       for label, url in item.get("links") or [])
    if links:
        out.append(f'<div style="font-size:13.5px;margin-top:4px">{links}</div>')
    return "".join(out)
