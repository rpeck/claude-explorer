#!/usr/bin/env python3
"""Make a committed test fixture from a recording of a synthetic session (issue #90).

Record a session with synthetic prompts on synthetic files, then run:

    uv run python scripts/scrub_fixture.py --kind cc|codex --in <recording> --out <fixture> \
        --home <your home> --scratch <the folder the session ran in>

What it does:

* Keeps only the records that Explorer reads (an allowlist per kind), with
  their field names and enum values.
* Drops model context: system prompts, developer messages, tool and skill
  lists, request records, account ids, rate limits, encrypted reasoning,
  and the text of thinking blocks.
* Rewrites the scratch folder to ``/home/fixture/<its name>`` and the home
  folder to ``/home/fixture``, in both the slash and the dashed
  (project-folder) forms.
* Refuses to write the fixture when a forbidden pattern is left: a home
  path, a ``/private`` path, the home folder's name, or an email address.

Read the fixture before you commit it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Codex: the record kinds and payload fields that Explorer reads.
CODEX_META_KEYS = ("id", "timestamp", "cwd", "history_mode", "cli_version", "originator", "source", "model_provider")
CODEX_TURN_KEYS = ("turn_id", "cwd", "model")
CODEX_EVENTS = {"item_completed", "task_started", "task_complete", "turn_aborted", "thread_rolled_back", "context_compacted"}
CODEX_DROP_ITEMS = {"additional_tools"}
CODEX_DROP_FIELDS = {"internal_chat_message_metadata_passthrough", "encrypted_content"}

# Claude Code: the record kinds, attachments, and fields to keep or drop.
CC_TYPES = {"user", "assistant", "data-flow-records"}
CC_ATTACHMENTS = {"poll_events"}
CC_DROP_FIELDS = {"serverClassifierContext", "serverClassifierRequest", "requestRef", "rendered", "slug"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _codex(record: dict[str, Any]) -> dict[str, Any] | None:
    kind, p = record.get("type"), record.get("payload") or {}
    if kind == "session_meta":
        return {**record, "payload": {k: p[k] for k in CODEX_META_KEYS if k in p}}
    if kind == "turn_context":
        return {**record, "payload": {k: p[k] for k in CODEX_TURN_KEYS if k in p}}
    if kind == "response_item":
        if p.get("type") in CODEX_DROP_ITEMS or (p.get("type") == "message" and p.get("role") == "developer"):
            return None
        return {**record, "payload": {k: v for k, v in p.items() if k not in CODEX_DROP_FIELDS}}
    if kind == "event_msg" and p.get("type") in CODEX_EVENTS:
        return record
    return None


def _cc(record: dict[str, Any], called: set[str]) -> dict[str, Any] | None:
    kind = record.get("type")
    if kind == "attachment":
        if (record.get("attachment") or {}).get("type") not in CC_ATTACHMENTS:
            return None
    elif kind not in CC_TYPES:
        return None
    out = {k: v for k, v in record.items() if k not in CC_DROP_FIELDS}
    message = out.get("message")
    if isinstance(message, dict):
        message = {k: v for k, v in message.items() if k != "usage"}
        if isinstance(message.get("content"), list):
            message["content"] = [
                {"type": "thinking", "thinking": "..."} if isinstance(b, dict) and b.get("type") == "thinking" else b
                for b in message["content"]
            ]
        out["message"] = message
    # A tool search result lists every installed tool: keep only the ones the session calls.
    found = out.get("toolUseResult")
    if isinstance(found, list) and found and all(isinstance(t, dict) and "name" in t for t in found):
        out["toolUseResult"] = [t for t in found if t["name"] in called]
    return out


def _called_tools(records: list[dict[str, Any]]) -> set[str]:
    return {
        b["name"]
        for r in records
        for b in ((r.get("message") or {}).get("content") or [] if isinstance(r.get("message"), dict) else [])
        if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("name"), str)
    }


def _rewrite(text: str, home: str, scratch: str) -> str:
    target = f"/home/fixture/{Path(scratch).name}"
    pairs = [
        (scratch, target),
        (scratch.replace("/", "-"), target.replace("/", "-")),
        (home, "/home/fixture"),
        (home.replace("/", "-"), "-home-fixture"),
    ]
    for old, new in pairs:
        text = text.replace(old, new)
    # Codex's environment context names the machine's time zone.
    text = re.sub(r"<timezone>[^<]*</timezone>", "<timezone>UTC</timezone>", text)
    # Claude Code's own temp folder (task output files): /private/tmp/claude-<uid>/...
    return re.sub(r"/private/tmp/claude-\d+", "/tmp/claude", text)


def _leaks(text: str, home: str) -> list[str]:
    found = [p for p in ("/Users/", "/private/", "/var/folders") if p in text]
    name = Path(home).name
    if name and name in text:
        found.append(name)
    found += sorted(set(EMAIL_RE.findall(text)))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=("cc", "codex"), required=True)
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--home", required=True, help="the home folder of the machine that recorded")
    ap.add_argument("--scratch", required=True, help="the folder the session ran in")
    args = ap.parse_args(argv)

    records = [json.loads(raw) for raw in args.src.read_text(encoding="utf-8").splitlines() if raw.strip()]
    called = _called_tools(records)
    kept: list[dict[str, Any]] = []
    dropped: dict[str, Any] = {}  # uuid -> parentUuid of each dropped record
    for raw_record in records:
        record = _codex(raw_record) if args.kind == "codex" else _cc(raw_record, called)
        if record is None:
            if raw_record.get("uuid"):
                dropped[raw_record["uuid"]] = raw_record.get("parentUuid")
            continue
        kept.append(record)
    lines = []
    for record in kept:
        parent, seen = record.get("parentUuid"), set()
        while parent in dropped and parent not in seen:  # skip to the nearest kept ancestor
            seen.add(parent)
            parent = dropped[parent]
        if "parentUuid" in record:
            record = {**record, "parentUuid": parent}
        lines.append(_rewrite(json.dumps(record, ensure_ascii=False), args.home, args.scratch))
    text = "\n".join(lines) + "\n"
    leaks = _leaks(text, args.home)
    if leaks:
        print(f"scrub_fixture: refused; the output still holds: {', '.join(leaks)}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"scrub_fixture: wrote {len(lines)} records to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
