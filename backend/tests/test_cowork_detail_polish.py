"""D9 (error banner) + D10 (sandbox path) — Cowork detail-view
contract pinned at the backend/Pydantic boundary.

The frontend banner / label are pinned by Playwright; here we pin
that the SHAPE of the detail response carries the fields the frontend
will read.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from backend.store import ConversationStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cowork"
HAPPY_DEPLOYMENT = FIXTURE_ROOT / "d_deployment1"
HAPPY_ORG = HAPPY_DEPLOYMENT / "o_org1"
HAPPY_SESSION_DIR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777"
HAPPY_SIDECAR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777.json"


def _build_session(root: Path, uuid: str, sidecar_overrides: dict) -> Path:
    cowork_root = root / "local-agent-mode-sessions"
    org = cowork_root / "d_test" / "o_test"
    org.mkdir(parents=True, exist_ok=True)
    sess = org / f"local_{uuid}"
    sess.mkdir(exist_ok=True)
    shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", sess / "audit.jsonl")
    sidecar = json.loads(HAPPY_SIDECAR.read_text())
    sidecar["sessionId"] = f"local_{uuid}"
    sidecar.update(sidecar_overrides)
    (org / f"local_{uuid}.json").write_text(json.dumps(sidecar))
    return cowork_root


def _build_store(tmp_path: Path, cowork_root: Path) -> ConversationStore:
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    return ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )


def test_error_field_surfaces_in_detail(tmp_path: Path):
    """D9: sidecar.error becomes ConversationDetail.error."""
    uuid = "ee000000-0000-0000-0000-000000000001"
    cowork_root = _build_session(
        tmp_path, uuid, {"error": "The session ended unexpectedly."}
    )
    store = _build_store(tmp_path, cowork_root)

    detail = store.get_conversation(uuid)
    assert detail is not None
    assert detail.error == "The session ended unexpectedly."


def test_error_field_is_none_when_sidecar_clean(tmp_path: Path):
    """Inverse: a sidecar without `error` (or with error=null)
    leaves ConversationDetail.error == None (no false-positive
    banner on healthy sessions)."""
    uuid = "ee000000-0000-0000-0000-000000000002"
    cowork_root = _build_session(tmp_path, uuid, {})
    store = _build_store(tmp_path, cowork_root)

    detail = store.get_conversation(uuid)
    assert detail is not None
    assert detail.error is None


def test_sandbox_path_surfaces_in_detail(tmp_path: Path):
    """D10: sidecar.cwd becomes ConversationDetail.sandbox_path.
    Distinct from project_path (which also carries cwd for CC
    parity)."""
    uuid = "ee000000-0000-0000-0000-000000000003"
    cowork_root = _build_session(tmp_path, uuid, {"cwd": "/sessions/test-vm"})
    store = _build_store(tmp_path, cowork_root)

    detail = store.get_conversation(uuid)
    assert detail is not None
    assert detail.sandbox_path == "/sessions/test-vm"
    assert detail.project_path == "/sessions/test-vm"


def test_non_cowork_sources_have_no_sandbox_path(tmp_path: Path):
    """Inverse: a Desktop conversation must have sandbox_path=None
    (Cowork-specific field, defaulting cleanly for other sources)."""
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    cowork_root = tmp_path / "claude_desktop_app" / "local-agent-mode-sessions"
    data_dir.mkdir(parents=True)
    claude_dir.mkdir()
    cowork_root.mkdir(parents=True)

    uuid = "dd000000-0000-0000-0000-000000000001"
    (data_dir / f"{uuid}.json").write_text(
        json.dumps(
            {
                "uuid": uuid,
                "name": "Desktop",
                "summary": "",
                "model": "claude-3-5",
                "created_at": "2026-05-25T09:00:00Z",
                "updated_at": "2026-05-25T09:05:00Z",
                "is_starred": False,
                "source": "CLAUDE_AI",
                "chat_messages": [
                    {
                        "uuid": "m1",
                        "sender": "human",
                        "text": "hi",
                        "content": [],
                        "created_at": "2026-05-25T09:00:00Z",
                        "updated_at": "2026-05-25T09:00:00Z",
                        "truncated": False,
                        "attachments": [],
                        "files": [],
                    }
                ],
            }
        )
    )
    store = ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )
    detail = store.get_conversation(uuid)
    assert detail is not None
    assert detail.sandbox_path is None
    assert detail.error is None
