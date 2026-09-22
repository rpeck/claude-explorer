"""Tests for /api/preferences endpoint (P3a).

The preferences blob lives at <data_dir parent>/preferences.json — i.e.
``~/.claude-explorer/preferences.json`` in production. Versioned envelope:

    {"version": 1, "data": {"theme": "dark", ...}}

PATCH is the primary write path: it deep-merges (top-level overwrite) into the
existing data so unrelated keys are preserved. PUT replaces the whole blob.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_prefs(tmp_path, monkeypatch):
    """TestClient where preferences persist under tmp_path."""
    # CLAUDE_EXPLORER_DATA_DIR points at the conversations dir; the
    # preferences file lives in its parent (mirroring ~/.claude-explorer/).
    data_dir = tmp_path / "conversations"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))

    # Drop the cached settings so the new env var is honored.
    from backend import config as cfg
    cfg.get_settings.cache_clear()

    # Reload routers + app so the new env var/data_dir is picked up.
    from backend import main as backend_main
    import backend.routers.preferences as prefs_router
    importlib.reload(prefs_router)
    importlib.reload(backend_main)

    prefs_file = tmp_path / "preferences.json"
    return TestClient(backend_main.app), prefs_file


def test_get_returns_defaults_when_file_missing(client_with_prefs):
    client, prefs_file = client_with_prefs
    assert not prefs_file.exists()
    r = client.get("/api/preferences")
    assert r.status_code == 200
    body = r.json()
    assert body == {"version": 1, "data": {}}


def test_patch_creates_file_with_keys(client_with_prefs):
    client, prefs_file = client_with_prefs
    r = client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    assert r.status_code == 200
    assert r.json() == {"version": 1, "data": {"theme": "dark"}}
    assert prefs_file.exists()
    on_disk = json.loads(prefs_file.read_text())
    assert on_disk == {"version": 1, "data": {"theme": "dark"}}


def test_patch_deep_merge_preserves_other_keys(client_with_prefs):
    client, _ = client_with_prefs
    r1 = client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    assert r1.status_code == 200
    r2 = client.patch("/api/preferences", json={"data": {"keyboardMode": "vim"}})
    assert r2.status_code == 200
    r3 = client.get("/api/preferences")
    assert r3.status_code == 200
    data = r3.json()["data"]
    assert data == {"theme": "dark", "keyboardMode": "vim"}


def test_patch_overwrites_same_key(client_with_prefs):
    client, _ = client_with_prefs
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    client.patch("/api/preferences", json={"data": {"theme": "light"}})
    r = client.get("/api/preferences")
    assert r.json()["data"]["theme"] == "light"


def test_round_trip_versioned_envelope(client_with_prefs):
    client, prefs_file = client_with_prefs
    client.patch("/api/preferences", json={"data": {"foo": "bar"}})
    on_disk = json.loads(prefs_file.read_text())
    assert "version" in on_disk and on_disk["version"] == 1
    assert "data" in on_disk and isinstance(on_disk["data"], dict)
    assert on_disk["data"] == {"foo": "bar"}


def test_file_mode_0600(client_with_prefs):
    client, prefs_file = client_with_prefs
    client.patch("/api/preferences", json={"data": {"theme": "dark"}})
    # Windows ignores mode bits and uses an NTFS ACL instead.
    from backend.tests.test_credentials_perms import assert_owner_only

    assert_owner_only(prefs_file)


def test_concurrent_patches_dont_corrupt(client_with_prefs):
    client, _ = client_with_prefs

    keys = [f"k{i}" for i in range(5)]
    errors: list[Exception] = []

    def patch_one(key: str) -> None:
        try:
            r = client.patch("/api/preferences", json={"data": {key: f"v-{key}"}})
            assert r.status_code == 200, r.text
        except Exception as e:  # pragma: no cover - reported via list
            errors.append(e)

    threads = [threading.Thread(target=patch_one, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = client.get("/api/preferences").json()["data"]
    for k in keys:
        assert final.get(k) == f"v-{k}", f"Lost key {k} in {final}"


def test_unknown_key_tolerated(client_with_prefs):
    client, _ = client_with_prefs
    r = client.patch(
        "/api/preferences",
        json={"data": {"__unknown_future_key": {"nested": True}}},
    )
    assert r.status_code == 200
    g = client.get("/api/preferences")
    assert g.status_code == 200
    assert g.json()["data"]["__unknown_future_key"] == {"nested": True}


def test_put_replaces_whole_blob(client_with_prefs):
    client, _ = client_with_prefs
    client.patch("/api/preferences", json={"data": {"keyboardMode": "vim"}})
    r = client.put("/api/preferences", json={"data": {"theme": "light"}})
    assert r.status_code == 200
    final = client.get("/api/preferences").json()["data"]
    assert final == {"theme": "light"}
    assert "keyboardMode" not in final


# ---------------------------------------------------------------------------
# P2.4 — Atomic write recovery (preferences). When os.replace fails mid-write,
# the original file MUST be byte-identical and no .tmp must leak.
# ---------------------------------------------------------------------------


def test__patch_preferences__os_replace_fails__original_byte_identical_no_tmp_leak(
    client_with_prefs, monkeypatch
):
    """PREF-ATOMIC-RECOVERY (P2.4). os.replace OSError → original preserved, no tmp leak.

    Per CLAUDE-TESTING.md section 5.8: simulate kernel-level rename failure at the
    Python boundary (monkeypatch os.replace), not by holding a lock or pulling
    the disk. We don't claim the test models a real kernel reorder; we claim
    the route handler doesn't corrupt user data when its own atomic-write
    helper raises.
    """
    client, prefs_file = client_with_prefs

    # Seed a known-good blob so the recovery target is non-trivial.
    r0 = client.put("/api/preferences", json={"data": {"theme": "dark", "lang": "en"}})
    assert r0.status_code == 200
    original_bytes = prefs_file.read_bytes()

    # Boom: any subsequent write raises before the atomic swap commits.
    import backend.routers.preferences as prefs_mod

    def _boom(_src, _dst):
        raise OSError("simulated kernel-level rename failure")

    monkeypatch.setattr(prefs_mod.os, "replace", _boom)

    # Starlette's TestClient defaults to ``raise_server_exceptions=True``, so
    # an OSError inside the route handler propagates out of ``client.patch``
    # rather than surfacing as a 500. The CONTRACT we care about is the
    # filesystem invariant: original blob preserved, no .tmp leak. FastAPI
    # converts to 500 in production; in tests we just assert it raises and
    # then verify the on-disk state — verified-then-asserted per
    # CLAUDE-TESTING.md section 5.8.
    with pytest.raises(OSError, match="simulated kernel-level rename failure"):
        client.patch("/api/preferences", json={"data": {"theme": "light"}})

    # Original blob is untouched (byte-identical, NOT just JSON-equivalent).
    assert prefs_file.read_bytes() == original_bytes, (
        "original preferences.json must be byte-identical after a failed atomic write"
    )

    # No .tmp leak in the parent directory.
    leaked = list(prefs_file.parent.glob("preferences.json.tmp*"))
    assert leaked == [], f"leaked tmp files after failed atomic write: {leaked}"


# ---------------------------------------------------------------------------
# P4.6 — Deep-merge stress: per-key independence, explicit-null isolation,
# no-op on empty {data: {}}.
# ---------------------------------------------------------------------------


def test__patch_preferences__multi_key__per_key_independent(client_with_prefs):
    """PREF-PATCH-INDEP (P4.6). PATCH with {A, B, C} updates each independently."""
    client, _ = client_with_prefs
    client.put("/api/preferences", json={"data": {
        "theme": "dark", "lang": "en", "keyboardMode": "vim", "untouched": 1,
    }})
    r = client.patch("/api/preferences", json={"data": {
        "theme": "light", "lang": "fr", "keyboardMode": "emacs",
    }})
    assert r.status_code == 200
    data = client.get("/api/preferences").json()["data"]
    assert data["theme"] == "light"
    assert data["lang"] == "fr"
    assert data["keyboardMode"] == "emacs"
    assert data["untouched"] == 1, "key absent from the PATCH must survive"


def test__patch_preferences__null_on_one_key__siblings_unaffected(client_with_prefs):
    """PREF-PATCH-NULL-ISOL (P4.6). Explicit null on key A leaves B/C/D unchanged."""
    client, _ = client_with_prefs
    client.put("/api/preferences", json={"data": {
        "theme": "dark", "lang": "en", "keyboardMode": "vim",
    }})
    r = client.patch("/api/preferences", json={"data": {"theme": None}})
    assert r.status_code == 200
    data = client.get("/api/preferences").json()["data"]
    # Top-level overwrite per key (preferences.py:107-110) — null is a value,
    # not a delete sentinel for unrelated keys.
    assert data["theme"] is None
    assert data["lang"] == "en", "null on theme must NOT clear lang"
    assert data["keyboardMode"] == "vim", "null on theme must NOT clear keyboardMode"


def test__patch_preferences__empty_data__no_op_preserves_all(client_with_prefs):
    """PREF-PATCH-EMPTY-NOOP (P4.6). PATCH {data: {}} is a no-op; nothing changes."""
    client, _ = client_with_prefs
    client.put("/api/preferences", json={"data": {
        "theme": "dark", "lang": "en", "keyboardMode": "vim",
    }})
    before = client.get("/api/preferences").json()["data"]
    r = client.patch("/api/preferences", json={"data": {}})
    assert r.status_code == 200
    after = client.get("/api/preferences").json()["data"]
    assert before == after, f"empty PATCH must not change state; before={before!r} after={after!r}"


# ---------------------------------------------------------------------------
# C6 (a) — PATCH /api/preferences with extra top-level fields → 422.
#
# Pydantic v2's default is ``extra='ignore'``, which would silently swallow a
# misnamed top-level field (e.g. a frontend typo writing ``themee`` at the
# root instead of inside ``data``). That's a silent data-loss footgun: the
# write looks successful but the value never persists. ``extra='forbid'`` on
# ``PreferencesWrite`` turns the typo into a 422 so the caller learns at the
# wire boundary.
#
# Scope: forbid applies at the *top level* only. The ``data`` field is
# ``dict[str, Any]`` so unknown KEYS INSIDE ``data`` keep working — see
# ``test_unknown_key_tolerated`` above. That's deliberate: the envelope
# shape is the public contract; the data blob is intentionally
# forward-compatible for future preference keys.
# ---------------------------------------------------------------------------


def test_patch_preferences_unknown_field_returns_422(client_with_prefs):
    """PATCH with an unknown TOP-LEVEL field must return 422.

    A request body whose only key is an unknown field is the canonical
    "client sending garbage" case. Pydantic with ``extra='forbid'`` rejects
    the body before the handler runs, so FastAPI surfaces 422 with the
    field name in the detail.
    """
    client, prefs_file = client_with_prefs
    r = client.patch(
        "/api/preferences",
        json={"unknown_field_xyz": "anything"},
    )
    assert r.status_code == 422, (
        f"expected 422 for unknown top-level field; got {r.status_code}: {r.text}"
    )
    # The 422 detail should mention the rejected field name so the
    # frontend can show a useful error. Pydantic v2 emits one error per
    # extra field with type ``extra_forbidden``.
    body = r.json()
    detail = body.get("detail", [])
    assert any(
        "unknown_field_xyz" in str(e) or "extra_forbidden" in str(e).lower()
        for e in (detail if isinstance(detail, list) else [detail])
    ), f"422 detail should reference unknown_field_xyz: {body!r}"


def test_patch_preferences_typo_field_returns_422(client_with_prefs):
    """PATCH with a valid ``data`` envelope AND a typo'd sibling key returns 422.

    The interesting failure mode is the SIBLING-typo case: a request that
    LOOKS half-right (real ``data`` plus a typo'd top-level key). Without
    ``extra='forbid'`` the valid ``data`` slice would silently apply and
    the typo would silently vanish. With forbid, the whole request is
    rejected and NOTHING is written — atomic, no half-applied state.

    Verifies both the 422 AND the non-application of the valid slice by
    GET'ing afterwards: ``theme`` MUST NOT have been set, because the
    PATCH was rejected as a whole.
    """
    client, prefs_file = client_with_prefs
    r = client.patch(
        "/api/preferences",
        json={"data": {"theme": "dark"}, "themee": "oops"},
    )
    assert r.status_code == 422, (
        f"expected 422 for typo'd top-level field alongside valid data; "
        f"got {r.status_code}: {r.text}"
    )
    # Atomic-reject contract: NONE of the body's keys must have been
    # applied. A GET should still see the empty/default blob.
    g = client.get("/api/preferences")
    assert g.status_code == 200
    data = g.json().get("data", {})
    assert "theme" not in data, (
        f"theme must not have been applied from a rejected PATCH; got data={data!r}"
    )


def test__write_uses_orjson__unicode_preserved_and_trailing_newline(client_with_prefs):
    """PREF-ORJSON (perf-polish A2). On-disk preferences match orjson output.

    Same byte-level invariants as the bookmarks orjson test:

      1. Non-ASCII characters in preference values (e.g. user-chosen labels,
         display names, or future i18n strings) are written as native
         UTF-8 bytes — not ``\\uXXXX`` escape sequences as stdlib's
         ``json.dumps(..., indent=2)`` default would produce.

      2. The file ends with a single trailing newline (orjson's
         ``OPT_APPEND_NEWLINE``) so the file plays nicely with cat /
         diff / git.

    Fails with stdlib json; passes with orjson + OPT_INDENT_2 |
    OPT_APPEND_NEWLINE.
    """
    client, prefs_file = client_with_prefs
    r = client.patch(
        "/api/preferences",
        json={"data": {"displayName": "Café 📖", "theme": "dark"}},
    )
    assert r.status_code == 200

    raw_bytes = prefs_file.read_bytes()
    # Native UTF-8 bytes for the book emoji (U+1F4D6 = F0 9F 93 96).
    assert b"\xf0\x9f\x93\x96" in raw_bytes, (
        "orjson must emit emoji as native UTF-8, not stdlib's \\uXXXX escapes"
    )
    # No ASCII-escaped \\u sequences in the on-disk file.
    assert b"\\u" not in raw_bytes, (
        "found ASCII-escaped unicode in preferences.json — stdlib json writer "
        "still active?"
    )
    # Trailing newline.
    assert raw_bytes.endswith(b"\n"), (
        "preferences file must end with a single trailing newline "
        "(orjson OPT_APPEND_NEWLINE)"
    )
