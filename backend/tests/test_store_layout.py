"""Tests for store.py's per-org layout glob + legacy fallback (NEW3-P0-A).

C3 storage layout:
  data_dir/
  ├── _index.json                      (skipped — not a UUID-named file)
  ├── 02971706-...json                 (legacy flat-layout file)
  └── by-org/
      ├── .migrated_v2                 (sentinel; absent until C4)
      └── ae24ae66-.../11111111-...json

Until the .migrated_v2 sentinel exists, store.py must include BOTH layouts
in its glob, deduped by UUID at the load layer (the by-org copy wins when a
UUID appears in both — prevents double-rendering during the migration window).

After the sentinel appears, the legacy glob returns nothing (migration moved
everything).
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.store import ConversationStore


def _write_conv(path: Path, uuid: str, name: str = "Test", organization_id: str | None = None) -> None:
    """Helper to write a minimal conversation JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "uuid": uuid,
        "name": name,
        "summary": "",
        "model": "claude-sonnet-4-6",
        "created_at": "2024-03-01T12:00:00Z",
        "updated_at": "2024-03-01T13:00:00Z",
        "is_starred": False,
        "chat_messages": [],
    }
    if organization_id:
        data["organization_id"] = organization_id
        data["organization_name"] = "Personal"
    with open(path, "w") as f:
        json.dump(data, f)


def test_legacy_flat_layout_visible_when_sentinel_absent(tmp_path: Path) -> None:
    """Pre-migration users must see their flat-layout files."""
    uuid = "11111111-2222-3333-4444-555555555555"
    _write_conv(tmp_path / f"{uuid}.json", uuid)

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    uuids = {c.uuid for c in convs}
    assert uuid in uuids


def test_by_org_layout_visible(tmp_path: Path) -> None:
    """New layout: data_dir/by-org/<org_uuid>/<uuid>.json"""
    uuid = "11111111-2222-3333-4444-555555555555"
    org = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    _write_conv(tmp_path / "by-org" / org / f"{uuid}.json", uuid, organization_id=org)

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    uuids = {c.uuid for c in convs}
    assert uuid in uuids


def test_dedup_when_same_uuid_in_both_layouts(tmp_path: Path) -> None:
    """NEW3-P0-A: dual-glob window must not render the same conversation twice.

    Scenario: top-level X.json predates migration; bulk_fetch wrote
    by-org/<org>/X.json before the migration sentinel landed. UI must show
    X exactly once (the by-org copy wins since it carries org metadata).
    """
    uuid = "11111111-2222-3333-4444-555555555555"
    org = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    _write_conv(tmp_path / f"{uuid}.json", uuid, name="Legacy")  # no org_id
    _write_conv(tmp_path / "by-org" / org / f"{uuid}.json", uuid, name="ByOrg", organization_id=org)

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    matches = [c for c in convs if c.uuid == uuid]
    assert len(matches) == 1, f"expected exactly 1 match for {uuid}, got {len(matches)}: {[c.name for c in matches]}"
    # by-org copy wins
    assert matches[0].name == "ByOrg"
    assert matches[0].organization_id == org


def test_legacy_fallback_dedupes_by_uuid(tmp_path: Path) -> None:
    """Same as above, asserted on the lower-level _get_conversation_files API."""
    uuid = "11111111-2222-3333-4444-555555555555"
    org = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    _write_conv(tmp_path / f"{uuid}.json", uuid)
    _write_conv(tmp_path / "by-org" / org / f"{uuid}.json", uuid, organization_id=org)

    store = ConversationStore(data_dir=tmp_path)
    files = store._get_conversation_files()
    file_uuids = [p.stem for p in files]
    # UUID appears in by-org path; legacy flat copy excluded.
    assert file_uuids.count(uuid) == 1
    # The retained path is under by-org/.
    matched = next(p for p in files if p.stem == uuid)
    assert "by-org" in str(matched), f"expected by-org copy to win, got {matched}"


def test_excludes_index_json_at_top_level(tmp_path: Path) -> None:
    """A top-level _index.json is not a conversation; UUID regex must reject it."""
    (tmp_path / "_index.json").write_text(json.dumps({"orgs": []}))
    uuid = "11111111-2222-3333-4444-555555555555"
    _write_conv(tmp_path / f"{uuid}.json", uuid)

    store = ConversationStore(data_dir=tmp_path)
    files = store._get_conversation_files()
    file_names = [p.name for p in files]
    assert "_index.json" not in file_names
    assert f"{uuid}.json" in file_names


def test_sentinel_present_skips_legacy_glob(tmp_path: Path) -> None:
    """After the migration sentinel appears, legacy top-level files are NOT loaded.

    (Migration would have moved them all into by-org/; if any orphans remain
    they should NOT be silently surfaced — they need user attention.)
    """
    uuid_legacy = "11111111-2222-3333-4444-555555555555"
    uuid_byorg = "22222222-2222-3333-4444-555555555555"
    org = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    _write_conv(tmp_path / f"{uuid_legacy}.json", uuid_legacy)
    _write_conv(tmp_path / "by-org" / org / f"{uuid_byorg}.json", uuid_byorg, organization_id=org)
    # Sentinel
    (tmp_path / "by-org" / ".migrated_v2").touch()

    store = ConversationStore(data_dir=tmp_path)
    files = store._get_conversation_files()
    file_uuids = {p.stem for p in files}
    assert uuid_byorg in file_uuids
    assert uuid_legacy not in file_uuids, "legacy top-level files must NOT be loaded once sentinel exists"


def test_summary_carries_organization_fields(tmp_path: Path) -> None:
    """ConversationSummary must surface organization_id and organization_name."""
    uuid = "11111111-2222-3333-4444-555555555555"
    org = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
    _write_conv(tmp_path / "by-org" / org / f"{uuid}.json", uuid, organization_id=org)

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    match = next(c for c in convs if c.uuid == uuid)
    assert match.organization_id == org
    assert match.organization_name == "Personal"


def test_summary_organization_id_null_for_legacy(tmp_path: Path) -> None:
    """Legacy untagged JSONs surface as organization_id=None (the 'Untagged' bucket)."""
    uuid = "11111111-2222-3333-4444-555555555555"
    _write_conv(tmp_path / f"{uuid}.json", uuid)  # no organization_id

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    match = next(c for c in convs if c.uuid == uuid)
    assert match.organization_id is None
    assert match.organization_name is None


def test_claude_code_files_under_by_org_synthetic_dir_filtered_from_claude_ai(tmp_path: Path) -> None:
    """JSONs under by-org/_claude_code/ have source=CLAUDE_CODE and are filtered
    out of the CLAUDE_AI source path (existing behavior, but verify it survives
    the layout move)."""
    uuid = "11111111-2222-3333-4444-555555555555"
    cc_path = tmp_path / "by-org" / "_claude_code" / f"{uuid}.json"
    cc_path.parent.mkdir(parents=True, exist_ok=True)
    cc_path.write_text(
        json.dumps(
            {
                "uuid": uuid,
                "name": "CC session",
                "summary": "",
                "model": "claude-sonnet-4-6",
                "created_at": "2024-03-01T12:00:00Z",
                "updated_at": "2024-03-01T13:00:00Z",
                "source": "CLAUDE_CODE",
                "chat_messages": [],
            }
        )
    )

    store = ConversationStore(data_dir=tmp_path)
    convs = store.list_conversations(source="CLAUDE_AI")
    # CLAUDE_CODE source is filtered out of CLAUDE_AI path.
    assert all(c.uuid != uuid for c in convs)
