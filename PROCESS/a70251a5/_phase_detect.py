"""Phase C: Detect phase boundaries in session a70251a5.

Reads outline.jsonl, filters to real human prompts (non-empty summary),
emits candidate segmentation data.

Usage:
    uv run python PROCESS/a70251a5/_phase_detect.py list
    uv run python PROCESS/a70251a5/_phase_detect.py dump > /tmp/prompts.tsv
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUTLINE = Path(
    "/Users/rpeck/Source/claude-desktop-message-exporter/PROCESS/a70251a5/outline.jsonl"
)


def load_rows() -> list[dict]:
    rows: list[dict] = []
    with OUTLINE.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


_SLASH_CMD = re.compile(r"^<command-(name|message|args)>.*?</command-\1>", re.DOTALL)
_INTERRUPT = re.compile(r"^\[Request interrupted by user\]")
_CONTINUE_CMD = re.compile(r"^(please\s+)?(continue|commit this\.?)$", re.IGNORECASE)


def is_real_prompt(summary: str) -> bool:
    s = (summary or "").strip()
    if not s:
        return False
    # skip slash commands / exit / clear
    if s.startswith("<command-"):
        return False
    return not _INTERRUPT.match(s)


def real_prompts(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        if r.get("sender") != "human":
            continue
        if not is_real_prompt(r.get("summary", "")):
            continue
        out.append(r)
    return out


def dump_tsv() -> None:
    rows = load_rows()
    for r in real_prompts(rows):
        ts = (r.get("timestamp") or "")[:10]
        s = (r.get("summary") or "").strip().replace("\n", " ").replace("\t", " ")
        # truncate for readable TSV
        if len(s) > 140:
            s = s[:137] + "..."
        print(f"{r['pos']}\t{r['message_uuid']}\t{ts}\t{r['char_count']}\t{s}")


def list_summary() -> None:
    rows = load_rows()
    rp = real_prompts(rows)
    print(f"total rows: {len(rows)}")
    print(f"human rows: {sum(1 for r in rows if r['sender']=='human')}")
    print(f"real prompts: {len(rp)}")
    # date histogram
    from collections import Counter

    by_day = Counter((r.get("timestamp") or "")[:10] for r in rp)
    for day in sorted(by_day):
        print(f"  {day}: {by_day[day]}")


def windows() -> None:
    """Print real prompts with date transitions flagged."""
    rows = load_rows()
    rp = real_prompts(rows)
    prev_day = None
    for r in rp:
        day = (r.get("timestamp") or "")[:10]
        s = (r.get("summary") or "").strip().replace("\n", " ")
        marker = " *** DAY" if day != prev_day else ""
        if len(s) > 100:
            s = s[:97] + "..."
        print(f"pos={r['pos']:>4}  {day}  len={r['char_count']:>5}{marker}  {s}")
        prev_day = day


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "dump":
        dump_tsv()
    elif cmd == "windows":
        windows()
    else:
        list_summary()
