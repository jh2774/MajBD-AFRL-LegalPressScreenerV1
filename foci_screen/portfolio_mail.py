"""A portfolio, as an email: to keep for yourself, or to pass to a colleague.

What it carries is what the dashboard shows, in words: the companies, the
latest severity the screen gave each, and how many have not been screened
here. And two ways back in — a link that opens the same portfolio on the site,
and the key itself, which opens it anywhere, for as long as the email is kept.

Like an alert, it reports and does not judge: a severity is the screen's
reading of public records, and the footer says so.
"""
from __future__ import annotations

import html as html_lib
from urllib.parse import quote, urlsplit

# Longer links are broken by some mail programs; past this, the key alone.
MAX_LINK_CHARS = 1800

DISCLAIMER = ("Drawn from public records. A severity is the screen's reading of them, not "
              "a FOCI determination or a finding of wrongdoing.")


def open_link(base_url: str, key: str) -> str:
    """A link that opens the portfolio on the site, or "" if too long to send."""
    url = f"{base_url.rstrip('/')}/#/portfolio/open/{quote(key, safe='')}"
    return url if base_url and len(url) <= MAX_LINK_CHARS else ""


def _line(company: dict) -> str:
    name = company.get("entity_name") or company.get("entity_key") or ""
    if company.get("kind") == "adviser":
        return f"{name} — investment firm (CRD {company.get('crd')})"
    if not company.get("screened_here"):
        return f"{name} — not screened on this site yet"
    severity = company.get("severity")
    return f"{name} — {severity}" if severity else f"{name} — screened, nothing found"


def compose(view: dict, key: str, base_url: str, *, sent_by: str = "",
            note: str = "") -> tuple[str, str, str]:
    """(subject, text, html). `sent_by` is set when it goes to someone else."""
    name = view.get("name") or "Portfolio"
    site = urlsplit(base_url).netloc or "FOCI-Screener"
    totals = view.get("totals") or {}
    n = totals.get("companies", 0)
    severity = view.get("severity") or {}
    order = ["critical", "high", "medium", "low", "info"]
    by_severity = ", ".join(f"{severity[s]} {s}" for s in order if severity.get(s))
    unscreened = sum(1 for c in view.get("companies", [])
                     if c.get("kind") != "adviser" and not c.get("screened_here"))
    link = open_link(base_url, key)

    subject = (f"{sent_by} shared a portfolio: {name}" if sent_by
               else f"Your portfolio: {name}")
    intro = (f"{sent_by} sent you this portfolio from FOCI-Screener ({site})."
             if sent_by else f"Your portfolio from FOCI-Screener ({site}), as you asked.")
    summary = (f"\"{name}\" watches {n} compan{'y' if n == 1 else 'ies'}."
               + (f" Latest severity: {by_severity}." if by_severity else "")
               + (f" {unscreened} not screened on the site yet." if unscreened else ""))

    lines = [intro, ""]
    if note:
        lines += [f"{'Their' if sent_by else 'Your'} note: {note}", ""]
    lines += [summary, ""]
    lines += [f"- {_line(c)}" for c in view.get("companies", [])]
    lines += [""]
    if link:
        lines += [f"Open it on the site: {link}", ""]
    lines += ["Or paste this key on the site's Portfolio page — it opens the same list on "
              "any machine:", key, "", "—", DISCLAIMER]
    text = "\n".join(lines)

    e = html_lib.escape
    items = "".join(f"<li>{e(_line(c))}</li>" for c in view.get("companies", []))
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'line-height:1.5;color:#1c1c1c;max-width:640px">'
        f"<p>{e(intro)}</p>"
        + (f"<p><em>{'Their' if sent_by else 'Your'} note:</em> {e(note)}</p>" if note else "")
        + f"<p><strong>{e(summary)}</strong></p><ul>{items}</ul>"
        + (f'<p><a href="{e(link)}" style="display:inline-block;background:#c8a24a;'
           f'color:#1c1c1c;padding:9px 16px;border-radius:4px;text-decoration:none">'
           f"Open it on the site</a></p>" if link else "")
        + '<p style="font-size:13px;color:#555">Or paste this key on the site\'s Portfolio '
          "page — it opens the same list on any machine:</p>"
        f'<p style="font-family:Consolas,monospace;font-size:12px;word-break:break-all;'
        f'background:#f4f2ec;padding:8px">{e(key)}</p>'
        f'<p style="color:#777;font-size:12.5px">{e(DISCLAIMER)}</p></div>')
    return subject, text, html
