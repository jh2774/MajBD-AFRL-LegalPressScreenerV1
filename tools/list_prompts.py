"""List the prompts a person actually typed in a session.

    python tools/list_prompts.py
    python tools/list_prompts.py --out prompts.txt --full

The session log interleaves prompts with replies and tool calls, which is the
right shape for reading the argument back but the wrong one for answering
"what did I ask for?". This is the prompts alone, numbered and timestamped.

Shares `is_human_prompt` with the exporter, so slash-command echoes, hook
output, compaction notices and tool results are excluded the same way here as
there — they arrive as `type: user` too, and counting them would inflate the
list with things nobody typed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.export_session_log import (  # noqa: E402
    find_transcript,
    is_human_prompt,
    redact,
    stamp,
    text_blocks,
)

SUMMARY_CHARS = 100


def collect(path: pathlib.Path) -> list[dict]:
    prompts: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not is_human_prompt(record):
                continue
            body = "\n".join(text_blocks(record.get("message") or {}, ("text",))).strip()
            if body:
                prompts.append({"at": stamp(record), "text": redact(body, [])})
    return prompts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript")
    ap.add_argument("--out")
    ap.add_argument("--full", action="store_true",
                    help="print each prompt in full rather than one line each")
    args = ap.parse_args()

    path = pathlib.Path(args.transcript) if args.transcript else find_transcript()
    if path is None or not path.is_file():
        print("No transcript found.")
        return 1

    prompts = collect(path)
    lines = [f"Prompts in this session: {len(prompts)}",
             f"Transcript: {path.name}", ""]

    for i, p in enumerate(prompts, 1):
        if args.full:
            lines.append(f"--- {i}. {p['at']} " + "-" * 40)
            lines.append(p["text"])
            lines.append("")
        else:
            one = " ".join(p["text"].split())
            if len(one) > SUMMARY_CHARS:
                one = one[:SUMMARY_CHARS - 1].rstrip() + "…"
            lines.append(f"{i:3}. [{p['at']}] {one}")

    text = "\n".join(lines) + "\n"
    if args.out:
        (ROOT / args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(prompts)} prompts)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
