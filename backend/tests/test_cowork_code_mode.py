"""Issue #90: a Cowork session folds its Code Mode runs as Claude Code does.

Cowork (Claude Desktop's agent mode) writes Claude Code's records into
``audit.jsonl``, with ``session_id`` and ``_audit_timestamp`` and no
``parentUuid``. A Code Mode run there must be one ``code_run`` block, not a
bare REPL call and scattered inner calls. Synthetic data
(``cc_code_mode_fake``, in the Cowork shape).
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.tests import cc_code_mode_fake as fake


def _cowork(records: list[dict]) -> list[dict]:
    out = []
    for i, r in enumerate(records):
        r = {k: v for k, v in r.items() if k not in ("sessionId", "parentUuid", "timestamp", "cwd", "version")}
        out.append({**r, "session_id": "cli-cm", "_audit_timestamp": f"2026-11-24T10:00:{i:02d}.000Z"})
    return out


def test_a_cowork_session_folds_its_runs(tmp_path: Path) -> None:
    from backend.cowork_reader import read_cowork_conversation

    session_dir = tmp_path / "local_cccc0000-0000-4000-8000-000000000090"
    session_dir.mkdir()
    (session_dir / "audit.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _cowork(fake.main_session())))
    conv = read_cowork_conversation(session_dir)
    assert conv is not None
    blocks = [b for m in conv["chat_messages"] for b in m["content"]]
    runs = {b["id"]: b["code_run"] for b in blocks if b["type"] == "code_run"}
    assert list(runs) == list(fake.ALL_RUNS)
    assert [c["name"] for c in runs[fake.RUN_DONE]["inner_calls"]] == ["Bash", "Read"]
    assert not any(b.get("name") == "REPL" and b["type"] == "tool_use" for b in blocks)
