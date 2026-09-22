"""Export a Claude Code session transcript to a readable Markdown log.

    python tools/export_session_log.py                 # newest transcript
    python tools/export_session_log.py --out docs/SESSION_LOG.md
    python tools/export_session_log.py --transcript path/to/session.jsonl

Why this exists: the reasoning behind a design decision is worth more than the
decision, and most of it here happened in conversation rather than in commit
messages — why EDGAR search is CIK-constrained, why delivery stays inert, why
`<main>` is not trusted. A code comment records the rule; this records the
argument.

**What is included.** Human prompts and the assistant's replies, in order, with
timestamps and a compact list of the tools invoked between them. Internal
reasoning is not conversation and is left out; so are tool results, which are
enormous and reproducible from the code.

**Redaction.** Transcripts capture whatever passed through the session,
including keys typed into a config file. Secrets are replaced before writing —
extend `REDACTIONS`, or pass `--redact` — and the header records that this
happened, because a log that has been edited should say so.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# (pattern, replacement). Applied to every line of output.
REDACTIONS: list[tuple[re.Pattern, str]] = [
    # Local development API keys created during the session.
    (re.compile(r"\bdev-local-key\b"), "<redacted-api-key>"),
    (re.compile(r"\b(?:sk|rnd)_[A-Za-z0-9_\-]{12,}"), "<redacted-secret>"),
    # The operator's own address. Government contact addresses (.mil/.gov) are
    # public record and are the tool's actual output, so they stay.
    (re.compile(r"[\w.+-]+@(?:gmail|outlook|hotmail|yahoo|proton|icloud)\.[a-z.]+",
                re.I), "<contact-email>"),
    # Absolute paths leak the operator's username.
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+", re.I), "<home>"),
    (re.compile(r"/(?:c/)?Users/[^/\s\"']+", re.I), "<home>"),
]

SKIP_PREFIXES = (
    "<local-command-caveat>", "<command-name>", "<local-command-stdout>",
    "<command-message>", "<user-prompt-submit-hook>",
)


def find_transcript() -> Path | None:
    """Newest transcript for the current working directory's project."""
    mangled = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    root = Path.home() / ".claude" / "projects" / mangled
    if not root.is_dir():
        # Fall back to the newest transcript anywhere under ~/.claude/projects.
        root = Path.home() / ".claude" / "projects"
        if not root.is_dir():
            return None
        candidates = list(root.rglob("*.jsonl"))
    else:
        candidates = list(root.glob("*.jsonl"))
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def redact(text: str, extra: list[str]) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    for literal in extra:
        if literal:
            text = text.replace(literal, "<redacted>")
    return text


def is_human_prompt(record: dict) -> bool:
    """A message the person actually typed.

    Slash-command echoes, hook output and the compaction notice all arrive as
    `type: user` too; only a human turn carries `origin.kind == "human"`.
    """
    if record.get("type") != "user":
        return False
    origin = record.get("origin") or {}
    return origin.get("kind") == "human"


def text_blocks(message: dict, kinds: tuple[str, ...]) -> list[str]:
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in kinds:
            value = block.get("text") or ""
            if value.strip():
                out.append(value)
    return out


def tool_names(message: dict) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b.get("name", "?") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def stamp(record: dict) -> str:
    raw = record.get("timestamp") or ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M UTC")
    except ValueError:
        return raw


def build(transcript: Path, extra_redactions: list[str]) -> tuple[str, dict]:
    turns: list[dict] = []
    pending_tools: list[str] = []
    counts = {"prompts": 0, "replies": 0, "tool_calls": 0, "skipped": 0}

    with transcript.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                counts["skipped"] += 1
                continue

            message = record.get("message")
            if not isinstance(message, dict):
                continue

            if is_human_prompt(record):
                body = "\n".join(text_blocks(message, ("text",))).strip()
                if not body or body.startswith(SKIP_PREFIXES):
                    continue
                turns.append({"role": "user", "text": body, "at": stamp(record)})
                counts["prompts"] += 1
                pending_tools = []

            elif record.get("type") == "assistant":
                names = tool_names(message)
                pending_tools.extend(names)
                counts["tool_calls"] += len(names)

                said = "\n".join(text_blocks(message, ("text",))).strip()
                if not said:
                    continue
                turns.append({"role": "assistant", "text": said,
                              "at": stamp(record), "tools": pending_tools[:]})
                counts["replies"] += 1
                pending_tools = []

    lines = [
        "# Session log",
        "",
        "Conversation record for the build of `foci-screen`: the prompts given and the",
        "replies returned, in order. It is the reasoning behind the code — why EDGAR",
        "search is constrained by CIK, why email delivery stays inert, why `<main>` is",
        "not trusted — which the code itself can only assert.",
        "",
        f"- Source: `{transcript.name}`",
        f"- Exported: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"- {counts['prompts']} prompt(s), {counts['replies']} repl(y/ies), "
        f"{counts['tool_calls']} tool call(s)",
        "",
        "Tool *results* are omitted — they are large and reproducible from the code.",
        "Internal reasoning is omitted as it is not conversation. Secrets, personal",
        "email addresses and home directory paths have been replaced with placeholders;",
        "government contact addresses are public record and are kept.",
        "",
        "Regenerate with `python tools/export_session_log.py`.",
        "",
        "---",
        "",
    ]

    for turn in turns:
        if turn["role"] == "user":
            lines += [f"## Prompt · {turn['at']}", "", turn["text"], ""]
        else:
            lines += [f"### Response · {turn['at']}", ""]
            if turn.get("tools"):
                counted: dict[str, int] = {}
                for name in turn["tools"]:
                    counted[name] = counted.get(name, 0) + 1
                summary = ", ".join(
                    f"{n}×{c}" if c > 1 else n for n, c in counted.items())
                lines += [f"*Tools used: {summary}*", ""]
            lines += [turn["text"], ""]
        lines.append("---")
        lines.append("")

    return redact("\n".join(lines), extra_redactions), counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--transcript", help="path to a .jsonl transcript")
    parser.add_argument("--out", default="docs/SESSION_LOG.md")
    parser.add_argument("--redact", action="append", default=[],
                        help="extra literal string to replace (repeatable)")
    args = parser.parse_args()

    transcript = Path(args.transcript) if args.transcript else find_transcript()
    if transcript is None or not transcript.is_file():
        print("No transcript found. Pass --transcript explicitly.", file=sys.stderr)
        return 1

    markdown, counts = build(transcript, args.redact)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    print(f"wrote {out} ({len(markdown) / 1024:.0f} KB)")
    print(f"  {counts['prompts']} prompts, {counts['replies']} replies, "
          f"{counts['tool_calls']} tool calls")
    if counts["skipped"]:
        print(f"  {counts['skipped']} unparseable line(s) skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
