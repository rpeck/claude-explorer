"""Cowork sessions that exist in more than one org or deployment directory.

Earned 2026-10-21 (review of the remote Cowork task archive and the
Cowork list cache):

1. ``find_cowork_session_dir`` returned the first directory that held the
   session, and the detail path read only that one. When the first copy
   had an empty or user-less ``audit.jsonl``, the list showed the
   conversation (built from the good copy) but opening it returned 404.

Directory iteration order is not defined, so each duplicate test runs with
the good copy in each of the two directories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import store as store_mod
from backend.store import ConversationStore
from backend.tests.test_store_cowork_integration import (
    HAPPY_SESSION_DIR,
    HAPPY_SIDECAR,
)


LOCAL_UUID = "dup00000-2222-3333-4444-555566667777"


@pytest.fixture(autouse=True)
def _fresh_cache():
    store_mod.clear_cowork_summary_cache()
    yield
    store_mod.clear_cowork_summary_cache()


def _happy_lines() -> list[str]:
    return (HAPPY_SESSION_DIR / "audit.jsonl").read_text(encoding="utf-8").splitlines()


def _write_local(cowork_root: Path, deployment: str, audit_text: str) -> Path:
    org_dir = cowork_root / deployment / "o_org"
    session_dir = org_dir / f"local_{LOCAL_UUID}"
    session_dir.mkdir(parents=True)
    (session_dir / "audit.jsonl").write_text(audit_text, encoding="utf-8")
    sidecar = json.loads(HAPPY_SIDECAR.read_text(encoding="utf-8"))
    sidecar["sessionId"] = f"local_{LOCAL_UUID}"
    sidecar["title"] = f"Copy in {deployment}"
    (org_dir / f"local_{LOCAL_UUID}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return session_dir


def _bad_audit(kind: str) -> str:
    if kind == "empty":
        return ""
    # "no_user": every line except the user turns, so the reader returns None.
    lines = [line for line in _happy_lines() if json.loads(line).get("type") != "user"]
    assert lines, "the fixture must have non-user lines"
    return "\n".join(lines) + "\n"


def _store(tmp_path: Path, cowork_root: Path | None = None) -> ConversationStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return ConversationStore(
        data_dir=data_dir,
        claude_dir=tmp_path / "claude",
        cowork_root=cowork_root or tmp_path / "no-local-cowork",
    )


# ----- 1. Detail reads the good copy of a duplicated session ---------------


@pytest.mark.parametrize("bad_kind", ["empty", "no_user"])
@pytest.mark.parametrize("good_deployment", ["d_first", "d_second"])
def test_detail_opens_a_session_whose_other_copy_is_unreadable(
    tmp_path: Path, bad_kind: str, good_deployment: str
) -> None:
    cowork_root = tmp_path / "local-agent-mode-sessions"
    for deployment in ("d_first", "d_second"):
        text = (
            "\n".join(_happy_lines()) + "\n"
            if deployment == good_deployment
            else _bad_audit(bad_kind)
        )
        _write_local(cowork_root, deployment, text)
    store = _store(tmp_path, cowork_root)

    rows = [c for c in store.list_conversations(source="CLAUDE_COWORK") if c.uuid == LOCAL_UUID]
    assert len(rows) == 1, "the list must show the session once"

    detail = store.get_conversation(LOCAL_UUID)
    assert detail is not None, "the list shows the session, so opening it must not 404"
    assert detail.uuid == LOCAL_UUID
    assert detail.name == f"Copy in {good_deployment}"
    assert detail.messages

    as_dict = store.get_conversation_dict(LOCAL_UUID)
    assert as_dict is not None and as_dict["uuid"] == LOCAL_UUID

