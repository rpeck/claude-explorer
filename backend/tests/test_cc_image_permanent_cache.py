"""P4a — Backend permanent CC image-cache.

Claude Code stores image attachments under
``~/.claude/image-cache/<session-uuid>/<N>.<ext>`` and references them
inside the message text as a literal ``[Image: source: <abs-path>]``
marker. Claude Code rotates / deletes those files; the explorer's
viewer then breaks.

We copy each referenced file at fetch time into a permanent cache:
    ``~/.claude-explorer/cc-images/<conv-uuid>/<sess>--<N>.<sha8>.png``

The ``sha8`` suffix prevents collisions if a re-fetch produces different
bytes for the same ``<sess>--<N>`` slot — both copies survive; the
conversation marker resolves to the most recent one (the fallback
endpoint is task P4b).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

# 1x1 transparent PNG bytes — same payload used by other tests.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAA"
    "YAAjCB0C8AAAAASUVORK5CYII="
)
TINY_PNG_BYTES = base64.b64decode(TINY_PNG_B64)

# A different-byte payload for the re-fetch test.
OTHER_PNG_BYTES = TINY_PNG_BYTES + b"\x00extra-bytes"


@pytest.fixture
def cc_env(tmp_path, monkeypatch):
    """Stand up isolated CLAUDE_DIR + CLAUDE_EXPLORER_DATA_DIR.

    Mirrors the fixture pattern in test_search_scope.py: monkeypatch
    both env vars, then clear the lru_cache so the new settings are
    actually picked up.
    """
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    from backend import config as cfg

    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]
    yield {"claude_dir": claude_dir, "data_dir": data_dir}
    cfg.get_settings.cache_clear()  # type: ignore[attr-defined]


def _write_cc_image(claude_dir: Path, sess: str, name: str, payload: bytes) -> Path:
    image_dir = claude_dir / "image-cache" / sess
    image_dir.mkdir(parents=True, exist_ok=True)
    path = image_dir / name
    path.write_bytes(payload)
    return path


def _conv_with_marker(uuid: str, marker_path: Path) -> dict:
    return {
        "uuid": uuid,
        "name": "CC test",
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "content": [
                    {"type": "text", "text": f"Look:\n[Image: source: {marker_path}]"},
                ],
            },
        ],
    }


def _conv_no_marker(uuid: str) -> dict:
    return {
        "uuid": uuid,
        "name": "no images here",
        "chat_messages": [
            {
                "uuid": f"{uuid}-m1",
                "sender": "human",
                "content": [{"type": "text", "text": "just plain text, no markers"}],
            },
        ],
    }


def test_fetch_copies_referenced_cc_images_to_permanent_cache(cc_env):
    from backend import cc_image_cache

    sess = "abc123"
    conv_uuid = "conv-xyz"
    marker_path = _write_cc_image(cc_env["claude_dir"], sess, "14.png", TINY_PNG_BYTES)
    conv = _conv_with_marker(conv_uuid, marker_path)

    written = cc_image_cache.cache_all_markers(conv)
    assert len(written) == 1, written

    # Permanent location: <data_dir>/cc-images/<conv-uuid>/<sess>--<N>.<sha8>.<ext>
    cc_images_root = cc_env["data_dir"] / "cc-images" / conv_uuid
    assert cc_images_root.exists()
    files = list(cc_images_root.iterdir())
    assert len(files) == 1, files
    fname = files[0].name
    # Filename shape: "abc123--14.<8 hex chars>.png"
    assert fname.startswith("abc123--14.")
    assert fname.endswith(".png")
    sha_part = fname.removeprefix("abc123--14.").removesuffix(".png")
    assert len(sha_part) == 8
    assert all(c in "0123456789abcdef" for c in sha_part)

    # And the bytes survived intact.
    assert files[0].read_bytes() == TINY_PNG_BYTES


def test_cache_survives_original_deletion(cc_env):
    from backend import cc_image_cache

    sess = "sess-del"
    conv_uuid = "conv-del"
    marker_path = _write_cc_image(cc_env["claude_dir"], sess, "7.png", TINY_PNG_BYTES)
    conv = _conv_with_marker(conv_uuid, marker_path)

    written = cc_image_cache.cache_all_markers(conv)
    assert len(written) == 1
    cached = written[0]
    assert cached.exists()

    # Now delete the original — simulates Claude Code rotation/cleanup.
    marker_path.unlink()
    assert not marker_path.exists()

    # Permanent copy is unaffected.
    assert cached.exists()
    assert cached.read_bytes() == TINY_PNG_BYTES


def test_re_fetch_with_different_bytes_creates_new_filename(cc_env):
    from backend import cc_image_cache

    sess = "sess-refetch"
    conv_uuid = "conv-refetch"
    marker_path = _write_cc_image(cc_env["claude_dir"], sess, "3.png", TINY_PNG_BYTES)
    conv = _conv_with_marker(conv_uuid, marker_path)

    first = cc_image_cache.cache_all_markers(conv)
    assert len(first) == 1
    first_path = first[0]

    # Replace original bytes — same slot (sess--3) but different sha.
    marker_path.write_bytes(OTHER_PNG_BYTES)

    second = cc_image_cache.cache_all_markers(conv)
    assert len(second) == 1
    second_path = second[0]

    # Different filenames (different sha8 suffix); both still on disk.
    assert first_path != second_path
    assert first_path.exists(), "old copy must NOT be deleted on re-fetch"
    assert second_path.exists()
    assert first_path.read_bytes() == TINY_PNG_BYTES
    assert second_path.read_bytes() == OTHER_PNG_BYTES

    # Both copies live under the same conversation directory.
    cc_images_root = cc_env["data_dir"] / "cc-images" / conv_uuid
    assert sorted(p.name for p in cc_images_root.iterdir()) == sorted(
        [first_path.name, second_path.name]
    )


def test_no_image_marker_no_cache_write(cc_env):
    from backend import cc_image_cache

    conv = _conv_no_marker("conv-empty")
    written = cc_image_cache.cache_all_markers(conv)
    assert written == []

    # The conversation subdir should NOT have been created.
    conv_dir = cc_env["data_dir"] / "cc-images" / "conv-empty"
    assert not conv_dir.exists()


def test_marker_with_missing_file_logged_not_raised(cc_env, caplog):
    """A marker that resolves to a path missing in BOTH the live cache
    and the permanent cache is the genuine permanent-data-loss case —
    must log at WARNING so dashboard tails surface it."""
    from backend import cc_image_cache

    bogus = cc_env["claude_dir"] / "image-cache" / "ghost" / "99.png"
    # Note: deliberately do NOT create this file. And do NOT populate
    # the permanent cache — this is the "missing in both" case.
    conv = _conv_with_marker("conv-missing", bogus)

    with caplog.at_level("WARNING", logger="backend.cc_image_cache"):
        written = cc_image_cache.cache_all_markers(conv)

    assert written == []
    # A warning was emitted referencing the missing path. Filter to the
    # WARNING level explicitly so a stray DEBUG record can't satisfy the
    # assertion (would defeat the bidirectional check).
    warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and ("conv-missing" in r.message or str(bogus) in r.message)
    ]
    assert warnings, (
        "missing-in-both case must WARN; got records: "
        f"{[(r.levelname, r.message) for r in caplog.records]!r}"
    )


def test_marker_missing_live_but_present_in_permanent_cache_does_not_warn(
    cc_env, caplog
):
    """Steady-state case: Claude Code rotated the live file off disk
    but our earlier eager/lazy/watcher pass already copied it. The
    bytes are recovered — log at DEBUG (not WARNING) so dashboard tails
    aren't polluted with false-alarm signals.

    Bidirectional: the sibling test above ('logged_not_raised') proves
    a WARNING IS emitted when the bytes are missing in both places. If
    a regression dropped the permanent-cache check, this test would
    flip from passing to failing (because a WARNING would appear).
    """
    from backend import cc_image_cache

    sess = "sess-recovered"
    conv_uuid = "conv-recovered"
    bogus_live = (
        cc_env["claude_dir"] / "image-cache" / sess / "5.png"
    )
    # Note: deliberately do NOT create the live file.

    # Pre-populate the permanent cache by hand — simulates "an earlier
    # process already cached these bytes, then CC rotated them away".
    # Filename shape must match cc_image_cache.cache_path_for():
    # <cache_dir>/<conv_uuid>/<sess>--<N>.<sha8>.<ext>
    cache_root = cc_image_cache.cache_dir() / conv_uuid
    cache_root.mkdir(parents=True, exist_ok=True)
    (cache_root / f"{sess}--5.deadbeef.png").write_bytes(TINY_PNG_BYTES)

    with caplog.at_level("DEBUG", logger="backend.cc_image_cache"):
        result = cc_image_cache.copy_marker_image_to_cache(
            str(bogus_live), conv_uuid
        )

    # Return value contract unchanged: None when nothing was copied.
    assert result is None

    # No WARNING for the recovered case. Filter on level to avoid
    # accidentally matching a DEBUG record's message text.
    bad_warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and ("not on disk" in r.message or str(bogus_live) in r.message)
    ]
    assert not bad_warnings, (
        "recovered case (permanent-cache hit) must NOT WARN; got: "
        f"{[(r.levelname, r.message) for r in bad_warnings]!r}"
    )

    # And the existing permanent-cache copy was NOT touched (idempotent
    # — we didn't re-write the same sha8 or invent a new one).
    surviving = list(cache_root.iterdir())
    assert len(surviving) == 1, surviving
    assert surviving[0].name == f"{sess}--5.deadbeef.png"


def test_marker_missing_live_with_wrong_conv_uuid_in_permanent_cache_still_warns(
    cc_env, caplog
):
    """The permanent-cache check is scoped to the *exact* conv_uuid
    subdirectory. A copy under a *different* conv's directory does NOT
    satisfy 'recovered' for this conv — the warning must still fire.

    This pins the scoping behavior: globbing the wrong directory tree
    (e.g. <cache_dir>/* instead of <cache_dir>/<conv_uuid>/) would let
    cross-conversation hits silently pass, which is a real correctness
    bug if the same sess/N pair ever recurs across conversations.
    """
    from backend import cc_image_cache

    sess = "sess-isolated"
    bogus_live = (
        cc_env["claude_dir"] / "image-cache" / sess / "9.png"
    )
    # Different conv has the bytes; queried conv does NOT.
    other_conv = cc_image_cache.cache_dir() / "some-other-conv"
    other_conv.mkdir(parents=True, exist_ok=True)
    (other_conv / f"{sess}--9.cafef00d.png").write_bytes(TINY_PNG_BYTES)

    with caplog.at_level("WARNING", logger="backend.cc_image_cache"):
        result = cc_image_cache.copy_marker_image_to_cache(
            str(bogus_live), "conv-asking"
        )

    assert result is None
    warnings = [
        r for r in caplog.records
        if r.levelname == "WARNING"
        and ("conv-asking" in r.message or str(bogus_live) in r.message)
    ]
    assert warnings, (
        "cross-conv permanent-cache hit must NOT count as recovery for "
        "the queried conv; expected a WARNING. Got: "
        f"{[(r.levelname, r.message) for r in caplog.records]!r}"
    )


# ----------------------------------------------------------------------
# P4b — /api/cc-image fallback to permanent cache when source is gone
# ----------------------------------------------------------------------


class TestApiCcImagePermanentCacheFallback:
    """When the original on-disk file is gone, /api/cc-image must look it
    up in the permanent cache and serve those bytes."""

    def test_api_cc_image_falls_back_to_permanent_cache_when_source_gone(
        self, cc_env
    ):
        from fastapi.testclient import TestClient

        from backend import cc_image_cache
        from backend.main import app

        sess = "sess-fallback"
        conv_uuid = "conv-fallback"
        original = _write_cc_image(
            cc_env["claude_dir"], sess, "14.png", TINY_PNG_BYTES
        )
        conv = _conv_with_marker(conv_uuid, original)

        # Cache the bytes into the permanent cache.
        written = cc_image_cache.cache_all_markers(conv)
        assert len(written) == 1
        cached = written[0]
        assert cached.exists()

        # Simulate Claude Code rotation: the original is gone.
        original.unlink()
        assert not original.exists()

        client = TestClient(app)
        resp = client.get("/api/cc-image", params={"path": str(original)})
        assert resp.status_code == 200, resp.text
        assert resp.content == TINY_PNG_BYTES
        assert resp.headers["content-type"].startswith("image/png")

    def test_api_cc_image_404_when_neither_source_nor_cache(self, cc_env):
        from fastapi.testclient import TestClient

        from backend.main import app

        # The "image-cache" parent must exist so that the path-validation
        # against the cache root can be done; but the file itself is
        # absent and was NEVER cached.
        (cc_env["claude_dir"] / "image-cache" / "ghost-sess").mkdir(
            parents=True
        )
        ghost = (
            cc_env["claude_dir"] / "image-cache" / "ghost-sess" / "42.png"
        )

        client = TestClient(app)
        resp = client.get("/api/cc-image", params={"path": str(ghost)})
        assert resp.status_code == 404

    def test_api_cc_image_lazy_populates_cache_on_first_hit(self, cc_env):
        """Option B (2026-05-06): when the live file exists but no cache
        copy does, /api/cc-image copies it during the request. Means the
        cache fills as the user views images, not just at fetch time —
        principle of least surprise (no manual re-fetch required).
        """
        from fastapi.testclient import TestClient

        from backend.cc_image_cache import cache_dir
        from backend.main import app

        sess = "sess-lazy"
        conv_uuid = "sess-lazy"  # CC: conv_uuid == sess (parent dir name)
        original = _write_cc_image(
            cc_env["claude_dir"], sess, "7.png", TINY_PNG_BYTES
        )

        # Pre-condition: cache is empty for this sess/N.
        cache_root = cache_dir()
        assert not list(cache_root.glob(f"*/{sess}--7.*.png"))

        client = TestClient(app)
        resp = client.get("/api/cc-image", params={"path": str(original)})
        assert resp.status_code == 200, resp.text
        assert resp.content == TINY_PNG_BYTES

        # Post-condition: cache now has exactly one copy under
        # <conv_uuid>/<sess>--7.<sha8>.png, and it matches the original.
        copies = list(cache_root.glob(f"{conv_uuid}/{sess}--7.*.png"))
        assert len(copies) == 1, copies
        assert copies[0].read_bytes() == TINY_PNG_BYTES

    def test_api_cc_image_serves_original_when_present(self, cc_env):
        """If the original is on disk, serve it directly (don't go via cache).

        Use a distinct byte signature for the cached copy vs. the
        original so we can prove which file was returned.
        """
        from fastapi.testclient import TestClient

        from backend import cc_image_cache
        from backend.main import app

        sess = "sess-prefer-original"
        conv_uuid = "conv-prefer-original"
        original = _write_cc_image(
            cc_env["claude_dir"], sess, "5.png", TINY_PNG_BYTES
        )
        conv = _conv_with_marker(conv_uuid, original)

        # Populate the permanent cache.
        written = cc_image_cache.cache_all_markers(conv)
        assert len(written) == 1
        cached = written[0]

        # Now overwrite the cached bytes so they differ from the
        # original. This simulates "if the route ever fell back when it
        # shouldn't, the served bytes would be the cached ones".
        sentinel = b"NOT-THE-ORIGINAL-BYTES"
        cached.write_bytes(sentinel)
        assert original.read_bytes() == TINY_PNG_BYTES

        client = TestClient(app)
        resp = client.get("/api/cc-image", params={"path": str(original)})
        assert resp.status_code == 200, resp.text
        assert resp.content == TINY_PNG_BYTES, "must serve original, not cache"
