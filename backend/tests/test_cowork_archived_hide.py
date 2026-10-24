"""D8 — archived Cowork sessions hidden from sidebar by default; shown
when the caller passes ``show_archived=true``.

Bidirectional pinning per CLAUDE-TESTING §2: the same setup yields
TWO inverse assertions (hidden vs visible) so a trivially-broken
filter (always-true OR always-false) fails ONE of them.
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


def _make_corpus(root: Path, archived_uuids: list[str], active_uuids: list[str]) -> Path:
    """Build a Cowork corpus where the listed uuids carry sidecar.isArchived."""
    cowork_root = root / "local-agent-mode-sessions"
    org = cowork_root / "d_test" / "o_test"
    org.mkdir(parents=True, exist_ok=True)

    base_sidecar = json.loads(HAPPY_SIDECAR.read_text())

    def _drop(uuid: str, archived: bool) -> None:
        sess = org / f"local_{uuid}"
        sess.mkdir(exist_ok=True)
        shutil.copy(HAPPY_SESSION_DIR / "audit.jsonl", sess / "audit.jsonl")
        sidecar = dict(base_sidecar)
        sidecar["sessionId"] = f"local_{uuid}"
        sidecar["title"] = f"{'Archived' if archived else 'Active'} {uuid[:8]}"
        sidecar["isArchived"] = archived
        (org / f"local_{uuid}.json").write_text(json.dumps(sidecar))

    for uuid in archived_uuids:
        _drop(uuid, archived=True)
    for uuid in active_uuids:
        _drop(uuid, archived=False)
    return cowork_root


@pytest.fixture
def mixed_corpus_store(tmp_path: Path) -> ConversationStore:
    """1 archived + 2 active Cowork sessions."""
    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    data_dir.mkdir()
    claude_dir.mkdir()
    cowork_root = _make_corpus(
        tmp_path,
        archived_uuids=["aaaa1111-1111-1111-1111-111111111111"],
        active_uuids=[
            "bbbb2222-2222-2222-2222-222222222222",
            "cccc3333-3333-3333-3333-333333333333",
        ],
    )
    return ConversationStore(
        data_dir=data_dir, claude_dir=claude_dir, cowork_root=cowork_root
    )


def test_default_request_hides_archived(mixed_corpus_store):
    """Default ``list_conversations`` call (no show_archived) hides the
    archived session — only the 2 active ones come through."""
    convs = mixed_corpus_store.list_conversations(source="CLAUDE_COWORK")
    uuids = sorted(c.uuid for c in convs)
    assert uuids == [
        "bbbb2222-2222-2222-2222-222222222222",
        "cccc3333-3333-3333-3333-333333333333",
    ]
    # And every returned summary is is_archived=False.
    assert all(c.is_archived is False for c in convs)


def test_show_archived_true_reveals_archived(mixed_corpus_store):
    """``show_archived=True`` exposes the archived session alongside
    active ones. The summary's is_archived flag must be True so the
    UI can render an "Archived" badge."""
    convs = mixed_corpus_store.list_conversations(
        source="CLAUDE_COWORK", show_archived=True
    )
    uuids = sorted(c.uuid for c in convs)
    assert uuids == [
        "aaaa1111-1111-1111-1111-111111111111",
        "bbbb2222-2222-2222-2222-222222222222",
        "cccc3333-3333-3333-3333-333333333333",
    ]
    archived = [c for c in convs if c.is_archived]
    assert len(archived) == 1
    assert archived[0].uuid == "aaaa1111-1111-1111-1111-111111111111"


def test_archive_hide_applies_across_sources(mixed_corpus_store):
    """``source='all'`` honors the same filter — an archived Cowork
    session doesn't leak through the broader query either."""
    convs = mixed_corpus_store.list_conversations(source="all")
    assert all(c.uuid != "aaaa1111-1111-1111-1111-111111111111" for c in convs)
