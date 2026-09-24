"""Concatenate every source file into one readable Markdown document.

    python tools/bundle_source.py

For reading and review — a single file to scroll, search or hand to someone
who wants the whole thing at once. Not for running anything: it has no
directory structure. The output is generated, drifts the moment the source
changes, and is deliberately kept out of the repository; regenerate it instead
of committing it.

Sections are ordered to be read top to bottom rather than alphabetically, and
the header reports any tracked file that no section lists, so a new module
cannot go missing quietly.
"""
from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "foci-screen-all-code.md"

LANG = {".py": "python", ".ps1": "powershell", ".js": "javascript",
        ".css": "css", ".html": "html",
        ".toml": "toml", ".yml": "yaml", ".yaml": "yaml", ".json": "json",
        ".example": "bash", ".gitignore": "gitignore", ".dockerignore": "gitignore",
        ".gitattributes": "gitattributes"}

# Order matters: this is meant to be read top to bottom.
SECTIONS = [
    ("Package configuration", ["pyproject.toml"]),
    ("Core", ["foci_screen/__init__.py", "foci_screen/config.py", "foci_screen/models.py",
              "foci_screen/httpclient.py", "foci_screen/store.py",
              "foci_screen/pipeline.py", "foci_screen/jobs.py",
              "foci_screen/worker.py", "foci_screen/scheduler.py",
              "foci_screen/cli.py"]),
    ("Connectors (the data sources)", [
        "foci_screen/connectors/__init__.py",
        "foci_screen/connectors/usaspending.py", "foci_screen/connectors/fpds.py",
        "foci_screen/connectors/sec_edgar.py", "foci_screen/connectors/registries.py",
        "foci_screen/connectors/webwatch.py", "foci_screen/connectors/robots.py",
        "foci_screen/connectors/browser.py"]),
    ("Risk engine", ["foci_screen/risk/__init__.py", "foci_screen/risk/lexicon.py",
                     "foci_screen/risk/engine.py"]),
    ("Notices", ["foci_screen/notify/__init__.py", "foci_screen/notify/render.py",
                 "foci_screen/notify/gmail.py"]),
    ("HTTP API", ["foci_screen/api/__init__.py", "foci_screen/api/app.py",
                  "foci_screen/api/auth.py", "foci_screen/api/schemas.py"]),
    ("Web interface", ["foci_screen/web/index.html", "foci_screen/web/styles.css",
                       "foci_screen/web/app.js"]),
    ("Tests", sorted(f"tests/{p.name}" for p in (ROOT / "tests").glob("*.py"))),
    ("Tools", sorted(f"tools/{p.name}" for p in (ROOT / "tools").iterdir()
                     if p.suffix in {".py", ".ps1"})),
    ("Deployment", ["Dockerfile", "Dockerfile.worker", "render.yaml",
                    ".github/workflows/ci.yml", ".dockerignore", ".gitattributes",
                    ".gitignore", ".env.example"]),
]


def fence(path: pathlib.Path) -> str:
    return LANG.get(path.suffix, "") or LANG.get(path.name, "") or "text"


def main() -> int:
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "log", "--format=%h %s", "-1"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        head = "unknown"

    listed = [f for _, files in SECTIONS for f in files]
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                             capture_output=True, text=True).stdout.split()
    missed = [f for f in tracked
              if f not in listed and not f.startswith("docs/") and not f.endswith(".md")]

    body: list[str] = []
    toc: list[str] = []
    total_lines = 0

    for title, files in SECTIONS:
        toc.append(f"- **{title}**")
        body.append(f"\n---\n\n# {title}\n")
        for rel in files:
            path = ROOT / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip()
            lines = text.count("\n") + 1
            total_lines += lines
            anchor = rel.replace("/", "").replace(".", "").replace("_", "").lower()
            toc.append(f"  - [`{rel}`](#{anchor}) — {lines} lines")
            body.append(f"\n## `{rel}`\n\n<a id=\"{anchor}\"></a>\n")
            body.append(f"```{fence(path)}\n{text}\n```\n")

    header = [
        "# foci-screen — complete source",
        "",
        f"Every source file in one document. Commit `{head}`.",
        f"{len([f for _, fs in SECTIONS for f in fs if (ROOT / f).is_file()])} files, "
        f"{total_lines:,} lines.",
        "",
        "Prose documentation (README, ROADMAP, DEPLOY, the session log) is not included —",
        "it ships in the zip and the repository.",
        "",
    ]
    if missed:
        header += ["> Tracked files not listed below: "
                   + ", ".join(f"`{m}`" for m in missed), ""]
    header += ["## Contents", ""] + toc

    OUT.write_text("\n".join(header) + "\n" + "\n".join(body), encoding="utf-8")
    print(f"wrote {OUT.name}  ({OUT.stat().st_size / 1024:.0f} KB, "
          f"{total_lines:,} lines of code)")
    if missed:
        print("NOT INCLUDED:", missed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
