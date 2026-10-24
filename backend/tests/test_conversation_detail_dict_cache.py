"""W3+W4 — Cached model_dump dict + ORJSONResponse on /api/conversations/<uuid>.

Per the 2026-05-23 LLM Council decision record, the conv detail route's
warm-hit cost was dominated by:

  1. ~186 ms rebuilding ``ConversationDetail`` (Pydantic) every call
     even when ``FileCache`` already had the parsed dict warm.
  2. ~70 ms re-running ``model_dump`` + stdlib ``json.dumps`` inside
     FastAPI's default response encoder.

Synergy: cache the OUTPUT of ``model_dump(mode='json')`` keyed by
(uuid, path, mtime). On warm hits, the route returns
``ORJSONResponse(content=cached_dict)`` which bypasses both Pydantic
rebuild AND FastAPI's encoder, dropping straight into orjson's C-level
serialization.

Contracts pinned:

  T1: ``store.get_conversation_dict(uuid)`` called twice with no mtime
      change returns the SAME dict instance (cache hit, `is` identity).
  T2: Writing to the underlying file (mtime change) invalidates the
      cache → next call rebuilds.
  T3: ``leaf_override`` is NEVER cached — every call with a non-None
      leaf_override produces a freshly-built dict.
  T4: The route returns ``ORJSONResponse`` (the response_class), not
      the default JSONResponse.

The mutability concern (the cached dict is shared across requests) is
addressed by: (a) treating the dict as immutable contract-wise and
(b) the route returns it directly to ORJSONResponse which serializes
without mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cache import clear_cache
from backend.store import ConversationStore


def _seed(tmp_path: Path, uuid: str) -> Path:
    """Write a minimal Desktop conversation JSON under by-org/."""
    by_org = tmp_path / "by-org" / "org-1"
    by_org.mkdir(parents=True, exist_ok=True)
    path = by_org / f"{uuid}.json"
    path.write_text(json.dumps({
        "uuid": uuid,
        "name": "dict-cache-test",
        "summary": "",
        "model": "claude-sonnet",
        "created_at": "2026-05-23T12:00:00Z",
        "updated_at": "2026-05-23T12:00:00Z",
        "source": "CLAUDE_AI",
        "chat_messages": [
            {
                "uuid": "m1",
                "parent_message_uuid": None,
                "sender": "human",
                "text": "hello",
                "created_at": "2026-05-23T12:00:00Z",
                "updated_at": "2026-05-23T12:00:00Z",
                "content": [{"type": "text", "text": "hello"}],
            },
        ],
        "current_leaf_message_uuid": "m1",
    }))
    # Place the migration sentinel so the store's `_get_conversation_files`
    # only walks by-org/ (deterministic for the test).
    (tmp_path / "by-org" / ".migrated_v2").write_text("")
    return path


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear all module-level caches between tests."""
    clear_cache()
    # Best-effort: clear the new detail-dict cache if it exists yet.
    try:
        from backend.store import _DETAIL_DICT_CACHE
        _DETAIL_DICT_CACHE.clear()
    except (ImportError, AttributeError):
        pass
    yield
    clear_cache()
    try:
        from backend.store import _DETAIL_DICT_CACHE
        _DETAIL_DICT_CACHE.clear()
    except (ImportError, AttributeError):
        pass


def test_get_conversation_dict_returns_same_instance_on_warm_hit(tmp_path):
    """T1: Two calls for the same uuid (no mtime change) return the
    `is`-identical dict instance — the cache stores the BUILT dict, not
    a re-derivation."""
    uuid = "00000000-0000-0000-0000-000000000001"
    _seed(tmp_path, uuid)
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=empty_claude)

    dict1 = store.get_conversation_dict(uuid)
    dict2 = store.get_conversation_dict(uuid)

    assert dict1 is not None, "First call should return a dict"
    assert dict2 is not None, "Second call should return a dict"
    assert dict1 is dict2, (
        "Second call should return the cached dict by identity, but got "
        "different instances. The W4 dict cache is missing or keyed wrong."
    )


def test_get_conversation_dict_invalidates_on_mtime_change(tmp_path):
    """T2: When the underlying file's mtime changes, the cache invalidates
    and the next call rebuilds (returns a NEW dict instance with the
    updated content)."""
    import time as _time

    uuid = "00000000-0000-0000-0000-000000000002"
    path = _seed(tmp_path, uuid)
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=empty_claude)

    dict1 = store.get_conversation_dict(uuid)
    assert dict1 is not None
    assert dict1["name"] == "dict-cache-test"

    # Sleep briefly so the mtime tick is unambiguous on filesystems with
    # 1s resolution. Then rewrite with a new name.
    _time.sleep(1.1)
    data = json.loads(path.read_text())
    data["name"] = "renamed-after-write"
    path.write_text(json.dumps(data))

    dict2 = store.get_conversation_dict(uuid)
    assert dict2 is not None
    assert dict2["name"] == "renamed-after-write", (
        f"mtime change should have invalidated the cache, but got stale "
        f"name={dict2['name']!r}. Cache key should include mtime."
    )
    assert dict1 is not dict2, "Post-invalidation should return a new instance"


def test_get_conversation_dict_does_not_cache_leaf_override(tmp_path):
    """T3: When a leaf_override is supplied, the result is NEVER cached —
    each call rebuilds. This keeps the cache key simple (uuid only) and
    avoids the cardinality explosion of caching every (uuid, leaf) pair."""
    uuid = "00000000-0000-0000-0000-000000000003"
    _seed(tmp_path, uuid)
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=empty_claude)

    # First, warm the default-leaf cache.
    default_dict = store.get_conversation_dict(uuid)
    assert default_dict is not None

    # Now call with a leaf_override — even if the override points to the
    # same leaf as the default, the call should NOT be a cache hit and
    # MUST NOT corrupt the default-cached entry.
    leaf_dict_a = store.get_conversation_dict(uuid, leaf_override="m1")
    leaf_dict_b = store.get_conversation_dict(uuid, leaf_override="m1")

    assert leaf_dict_a is not None
    assert leaf_dict_b is not None
    # Each leaf_override call is a fresh build (not cached).
    assert leaf_dict_a is not leaf_dict_b, (
        "leaf_override calls must NOT be cached (T3 invariant)."
    )

    # And the default-cache is still valid (subsequent default call returns same instance).
    default_dict_2 = store.get_conversation_dict(uuid)
    assert default_dict is default_dict_2, (
        "Default-leaf cache should remain intact after leaf_override calls."
    )


def test_get_conversation_dict_returns_none_for_unknown_uuid(tmp_path):
    """Negative path: unknown uuid returns None (not a cached negative).
    Caching None would risk masking newly-fetched conversations."""
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()
    store = ConversationStore(data_dir=tmp_path, claude_dir=empty_claude)

    result = store.get_conversation_dict("does-not-exist-uuid-0001")
    assert result is None


@pytest.mark.asyncio
async def test_route_returns_orjson_response_with_cached_dict_body(tmp_path, monkeypatch):
    """T4: The /api/conversations/<uuid> route returns an ORJSONResponse
    (bypassing FastAPI's default Pydantic encoder)."""
    from fastapi.responses import ORJSONResponse

    from backend.routers.conversations import get_conversation

    uuid = "00000000-0000-0000-0000-000000000004"
    _seed(tmp_path, uuid)
    empty_claude = tmp_path / "claude-empty"
    empty_claude.mkdir()

    store = ConversationStore(data_dir=tmp_path, claude_dir=empty_claude)
    response = await get_conversation(uuid, leaf=None, store=store)

    assert isinstance(response, ORJSONResponse), (
        f"Expected ORJSONResponse, got {type(response).__name__}. "
        f"The W3 route refactor should return ORJSONResponse directly."
    )
    assert response.status_code == 200
