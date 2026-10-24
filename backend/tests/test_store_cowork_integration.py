"""ConversationStore integration tests for the CLAUDE_COWORK source.

Spec-driven (CLAUDE-TESTING §5.13): pins the user-observable contract
of source filtering, dedup, and detail-route resolution across three
sources (Desktop, Claude Code, Cowork) — never the implementation
details of how the store walks paths.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.store import ConversationStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cowork"
HAPPY_DEPLOYMENT = FIXTURE_ROOT / "d_deployment1"
HAPPY_ORG = HAPPY_DEPLOYMENT / "o_org1"
HAPPY_SESSION_DIR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777"
HAPPY_SIDECAR = HAPPY_ORG / "local_aaaa1111-2222-3333-4444-555566667777.json"


def _make_cowork_corpus(root: Path, count: int = 2) -> Path:
    """Copy the happy fixture N times under fresh uuids; return cowork root."""
    cowork_root = root / "local-agent-mode-sessions"
    dep = cowork_root / "d_dep_test"
    org = dep / "o_org_test"
    org.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        uuid = f"cccc{i:04d}-2222-3333-4444-555566667777"
        session_dir = org / f"local_{uuid}"
        session_dir.mkdir(exist_ok=True)
        shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", session_dir / "audit.jsonl")
        sidecar = json.loads(HAPPY_SIDECAR.read_text())
        sidecar["sessionId"] = f"local_{uuid}"
        sidecar["title"] = f"Synthetic Cowork {i}"
        (org / f"local_{uuid}.json").write_text(json.dumps(sidecar))
    return cowork_root


def _make_desktop_corpus(data_dir: Path, count: int = 2) -> None:
    """Drop N minimal Desktop JSON conversations into data_dir."""
    data_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        uuid = f"dddd{i:04d}-2222-3333-4444-555566667777"
        (data_dir / f"{uuid}.json").write_text(
            json.dumps(
                {
                    "uuid": uuid,
                    "name": f"Desktop Conv {i}",
                    "summary": "",
                    "model": "claude-3-5-sonnet",
                    "created_at": "2026-05-25T09:00:00Z",
                    "updated_at": "2026-05-25T09:05:00Z",
                    "is_starred": False,
                    "source": "CLAUDE_AI",
                    "chat_messages": [
                        {
                            "uuid": "m1",
                            "sender": "human",
                            "text": "desktop hello",
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


@pytest.fixture
def mixed_corpus_store(tmp_path: Path) -> ConversationStore:
    """Build a store across three sources: 2 Desktop + 0 CC + 2 Cowork.

    (CC kept at 0 to avoid touching the real ~/.claude/projects on the
    maintainer's machine; the CC suite has its own coverage.)
    """
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()  # empty -> 0 CC
    cowork_app_dir = tmp_path / "claude_desktop_app"
    cowork_app_dir.mkdir()

    _make_desktop_corpus(data_dir, count=2)
    _make_cowork_corpus(cowork_app_dir, count=2)

    store = ConversationStore(
        data_dir=data_dir,
        claude_dir=claude_dir,
        cowork_root=cowork_app_dir / "local-agent-mode-sessions",
    )
    return store


def test_source_all_returns_desktop_and_cowork(mixed_corpus_store):
    """``source='all'`` returns sessions from every available source."""
    convs = mixed_corpus_store.list_conversations(source="all")
    assert len(convs) == 4
    sources = sorted(c.source for c in convs)
    assert sources == ["CLAUDE_AI", "CLAUDE_AI", "CLAUDE_COWORK", "CLAUDE_COWORK"]


def test_source_cowork_filters_to_cowork_only(mixed_corpus_store):
    """``source='CLAUDE_COWORK'`` returns ONLY Cowork sessions — no
    Desktop or CC leakage."""
    convs = mixed_corpus_store.list_conversations(source="CLAUDE_COWORK")
    assert len(convs) == 2
    assert all(c.source == "CLAUDE_COWORK" for c in convs)


def test_source_claude_ai_excludes_cowork(mixed_corpus_store):
    """``source='CLAUDE_AI'`` MUST NOT leak Cowork sessions (Cowork is
    stamped CLAUDE_COWORK at ingest; a slipped filter would
    double-count Cowork as Desktop)."""
    convs = mixed_corpus_store.list_conversations(source="CLAUDE_AI")
    assert len(convs) == 2
    assert all(c.source == "CLAUDE_AI" for c in convs)


def test_detail_route_resolves_cowork_uuid(mixed_corpus_store):
    """Clicking a Cowork sidebar row must resolve to its conversation
    detail — without a Cowork branch in ``_find_conversation_data``
    this 404s."""
    convs = mixed_corpus_store.list_conversations(source="CLAUDE_COWORK")
    cowork_uuid = convs[0].uuid
    detail = mixed_corpus_store.get_conversation(cowork_uuid)
    assert detail is not None
    assert detail.uuid == cowork_uuid
    assert detail.source == "CLAUDE_COWORK"
    # Chronological-stream guard active — every chat_message survives.
    assert len(detail.messages) == 4
