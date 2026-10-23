"""Pin two new contracts on the CC-image-cache permanent-loss warning:

1. **Dedupe per walk**: when a conversation references the same
   missing image path from N different messages,
   :func:`cache_all_markers` emits at most ONE log record per unique
   ``(conv_uuid, abs_path)`` pair. Bidirectional pair: distinct paths
   STILL each get their own record (no over-aggregation).

2. **Level switch on watcher install**: when the supervised CC
   watcher is detected installed
   (``CLAUDE_EXPLORER_WATCHER_INSTALLED=1`` short-circuits the platform
   probe), the log record level drops from WARNING to INFO. The text
   is unchanged so log greps still work; only ``levelname`` changes.

Pre-change behavior (pinned by existing tests in
``test_cc_image_permanent_cache.py``):
* The warning emits at WARNING level.
* Same missing path referenced 3 times → 3 WARNING records.

This file flips both contracts via TDD-strict RED → GREEN.
"""

from __future__ import annotations


import pytest


# Use the existing fixtures from test_cc_image_permanent_cache.
pytest_plugins = []


TINY_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def cc_env(tmp_path, monkeypatch):
    """Minimal isolation: point CLAUDE_DIR + CLAUDE_EXPLORER_DATA_DIR
    at scratch dirs so the permanent cache and the live image-cache
    are both clean."""
    claude_dir = tmp_path / "claude-home"
    data_dir = tmp_path / "claude-explorer" / "conversations"
    (claude_dir / "image-cache").mkdir(parents=True)
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    # Re-load settings so the env vars take effect.
    from backend.config import get_settings
    get_settings.cache_clear()
    return {"claude_dir": claude_dir, "data_dir": data_dir}


def _make_conv_with_n_refs_to_one_missing_path(
    conv_uuid: str, sess: str, slot: str, n_refs: int
) -> dict:
    """Build a conversation_json shaped like a real CC dict where the
    same missing image path is referenced from ``n_refs`` separate
    messages.

    The reference shape is the literal ``[Image: source: ...]``
    marker that ``cc_image_cache._MARKER_RE`` matches.
    """
    marker = (
        f"[Image: source: /tmp/never-existed/{sess}/{slot}.png]"
    )
    return {
        "uuid": conv_uuid,
        "chat_messages": [
            {
                "content": [{"type": "text", "text": f"msg {i}: {marker}"}],
            }
            for i in range(n_refs)
        ],
    }


def _missing_path_records(caplog, abs_path_fragment: str):
    """Filter caplog to records that fired our "not on disk" message
    AND mention the specific missing path."""
    return [
        r for r in caplog.records
        if "not on disk" in r.message and abs_path_fragment in r.message
    ]


# ---------------------------------------------------------------------------
# Dedupe contract
# ---------------------------------------------------------------------------


def test_three_refs_to_same_missing_path_log_once(cc_env, caplog):
    """RED first: pre-change, this fails because each marker hit
    fires its own log record (3 records). The fix dedupes within one
    cache_all_markers walk to emit at most 1 record per unique
    (conv, abs_path)."""
    from backend import cc_image_cache

    conv = _make_conv_with_n_refs_to_one_missing_path(
        "conv-dedupe",
        "sess-dedupe",
        "11",
        n_refs=3,
    )

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)

    hits = _missing_path_records(caplog, "/tmp/never-existed/sess-dedupe/11.png")
    assert len(hits) == 1, (
        f"expected exactly 1 dedup'd record; got {len(hits)}: "
        f"{[(r.levelname, r.message) for r in hits]!r}"
    )


def test_two_distinct_missing_paths_log_separately(cc_env, caplog):
    """Bidirectional pair: distinct missing paths must NOT be merged
    into a single log record by an over-aggressive dedupe. Each
    unique ``(conv, abs_path)`` gets exactly one record."""
    from backend import cc_image_cache

    marker_a = "[Image: source: /tmp/never-existed/sess-A/11.png]"
    marker_b = "[Image: source: /tmp/never-existed/sess-A/16.png]"
    conv = {
        "uuid": "conv-distinct",
        "chat_messages": [
            {"content": [{"type": "text", "text": f"first: {marker_a}"}]},
            {"content": [{"type": "text", "text": f"second: {marker_b}"}]},
            {"content": [{"type": "text", "text": f"repeat A: {marker_a}"}]},
        ],
    }

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)

    hits_a = _missing_path_records(caplog, "/tmp/never-existed/sess-A/11.png")
    hits_b = _missing_path_records(caplog, "/tmp/never-existed/sess-A/16.png")
    assert len(hits_a) == 1, f"path A should log once; got {len(hits_a)}"
    assert len(hits_b) == 1, f"path B should log once; got {len(hits_b)}"


def test_dedupe_state_does_not_leak_between_walks(cc_env, caplog):
    """Each :func:`cache_all_markers` invocation gets a FRESH dedupe
    scope. If the user reads the same conversation a second time, the
    warning fires again on the second read — useful signal that the
    loss is still extant. Pin this to prevent the obvious mistake of
    a module-level set that silently silences forever-after."""
    from backend import cc_image_cache

    conv = _make_conv_with_n_refs_to_one_missing_path(
        "conv-no-leak",
        "sess-no-leak",
        "11",
        n_refs=2,
    )

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)
        cc_image_cache.cache_all_markers(conv)

    hits = _missing_path_records(caplog, "/tmp/never-existed/sess-no-leak/11.png")
    # 2 walks × 1 dedup'd record per walk == 2.
    assert len(hits) == 2, (
        f"expected 2 records (one per walk); got {len(hits)} — module-level "
        f"dedupe state must not silence the second walk"
    )


# ---------------------------------------------------------------------------
# Level-switch contract
# ---------------------------------------------------------------------------


def test_watcher_not_installed_logs_at_warning(cc_env, caplog, monkeypatch):
    """When the supervised watcher is not detected, the missing-image
    log fires at WARNING (the louder, action-required default). Pins
    the pre-existing contract from
    ``test_cc_image_permanent_cache.py`` survives the level-switch
    feature."""
    from backend import cc_image_cache, watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()

    conv = _make_conv_with_n_refs_to_one_missing_path(
        "conv-warn", "sess-warn", "11", n_refs=1,
    )

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)

    hits = _missing_path_records(caplog, "/tmp/never-existed/sess-warn/11.png")
    assert len(hits) == 1
    assert hits[0].levelname == "WARNING", (
        f"watcher-uninstalled → WARNING; got {hits[0].levelname}"
    )


def test_watcher_installed_logs_at_info(cc_env, caplog, monkeypatch):
    """Bidirectional pair: with the watcher installed, the same loss
    drops to INFO. The data WAS already lost, but no future loss is
    possible (supervised watcher running). WARNING-shouting forever
    after install would be log noise."""
    from backend import cc_image_cache, watcher_status

    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
    watcher_status.invalidate_cache()

    conv = _make_conv_with_n_refs_to_one_missing_path(
        "conv-info", "sess-info", "11", n_refs=1,
    )

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)

    hits = _missing_path_records(caplog, "/tmp/never-existed/sess-info/11.png")
    assert len(hits) == 1
    assert hits[0].levelname == "INFO", (
        f"watcher-installed → INFO; got {hits[0].levelname}"
    )


def test_log_message_text_is_identical_across_levels(cc_env, caplog, monkeypatch):
    """Only the ``levelname`` changes — the message text stays
    byte-identical so log greps and dashboards keyed on the string
    'not on disk' still work either way."""
    from backend import cc_image_cache, watcher_status

    # Run once with watcher OFF.
    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "0")
    watcher_status.invalidate_cache()
    conv = _make_conv_with_n_refs_to_one_missing_path(
        "conv-fmt", "sess-fmt", "11", n_refs=1,
    )
    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)
    off_hits = _missing_path_records(caplog, "/tmp/never-existed/sess-fmt/11.png")
    assert len(off_hits) == 1
    off_msg = off_hits[0].message

    caplog.clear()

    # Run again with watcher ON.
    monkeypatch.setenv("CLAUDE_EXPLORER_WATCHER_INSTALLED", "1")
    watcher_status.invalidate_cache()
    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        cc_image_cache.cache_all_markers(conv)
    on_hits = _missing_path_records(caplog, "/tmp/never-existed/sess-fmt/11.png")
    assert len(on_hits) == 1
    on_msg = on_hits[0].message

    assert off_msg == on_msg, (
        f"message text must be level-independent; got off={off_msg!r}, on={on_msg!r}"
    )
