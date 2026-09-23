"""Tests for bookmark CRUD (Build-4)."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_bookmarks(tmp_path, monkeypatch):
    """Spin up a TestClient where bookmarks persist to a tmp file."""
    bookmarks_file = tmp_path / "bookmarks.json"
    monkeypatch.setenv("CLAUDE_EXPLORER_BOOKMARKS_FILE", str(bookmarks_file))

    # Reload modules so the env var is picked up.
    import importlib
    from backend import main as backend_main
    import backend.routers.bookmarks as bm_router
    importlib.reload(bm_router)
    importlib.reload(backend_main)

    return TestClient(backend_main.app), bookmarks_file


def test_list_empty(client_with_bookmarks):
    client, _ = client_with_bookmarks
    r = client.get("/api/bookmarks")
    assert r.status_code == 200
    assert r.json() == {"bookmarks": []}


def test_create_and_list(client_with_bookmarks):
    client, path = client_with_bookmarks
    payload = {
        "conversation_id": "conv-1",
        "message_uuid": "msg-1",
        "source": "claude_code",
        "snippet": "First bookmarked message",
        "note": "important",
    }
    r = client.post("/api/bookmarks", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["conversation_id"] == "conv-1"
    assert body["message_uuid"] == "msg-1"
    assert body["note"] == "important"
    assert body["snippet"] == "First bookmarked message"
    assert "id" in body and body["id"]
    assert "created_at" in body

    r2 = client.get("/api/bookmarks")
    assert r2.status_code == 200
    items = r2.json()["bookmarks"]
    assert len(items) == 1
    assert items[0]["id"] == body["id"]

    on_disk = json.loads(path.read_text())
    assert len(on_disk["bookmarks"]) == 1


def test_update_note(client_with_bookmarks):
    client, _ = client_with_bookmarks
    r = client.post("/api/bookmarks", json={
        "conversation_id": "c", "message_uuid": "m", "source": "claude_code",
        "snippet": "s", "note": "old",
    })
    assert r.status_code == 201
    bid = r.json()["id"]

    r2 = client.patch(f"/api/bookmarks/{bid}", json={"note": "new note"})
    assert r2.status_code == 200
    assert r2.json()["note"] == "new note"


def test_delete(client_with_bookmarks):
    client, _ = client_with_bookmarks
    r = client.post("/api/bookmarks", json={
        "conversation_id": "c", "message_uuid": "m", "source": "claude_code",
        "snippet": "s",
    })
    bid = r.json()["id"]
    r2 = client.delete(f"/api/bookmarks/{bid}")
    assert r2.status_code == 204
    assert client.get("/api/bookmarks").json()["bookmarks"] == []


def test_delete_unknown_returns_404(client_with_bookmarks):
    client, _ = client_with_bookmarks
    r = client.delete("/api/bookmarks/no-such-id")
    assert r.status_code == 404


def test__patch_bookmark__unknown_id__returns_404_with_detail(client_with_bookmarks):
    """BKM-PATCH-404 (P4.3). Unknown id → 404 with non-empty `detail` string.

    Frontend's ApiError(status, text) shape (api.ts:176-184) uses `detail` to
    surface the failure to the user; pin the exact code AND that the body
    carries a non-empty detail message (not a bare HTTP error page).
    """
    client, _ = client_with_bookmarks
    r = client.patch("/api/bookmarks/no-such-id", json={"note": "irrelevant"})
    assert r.status_code == 404
    body = r.json()
    assert body.get("detail"), f"PATCH 404 must carry non-empty detail; got {body!r}"
    assert "not found" in body["detail"].lower(), (
        f"detail should reference the not-found nature; got {body['detail']!r}"
    )


def test__delete_bookmark__unknown_id__returns_404_with_detail(client_with_bookmarks):
    """BKM-DEL-404 (P4.3). Unknown id → 404 with non-empty `detail` string.

    Strengthens the existing 404 assertion with a body-contract check: the
    detail message is the same shape the frontend dispatches on.
    """
    client, _ = client_with_bookmarks
    r = client.delete("/api/bookmarks/no-such-id")
    assert r.status_code == 404
    body = r.json()
    assert body.get("detail"), f"DELETE 404 must carry non-empty detail; got {body!r}"
    assert "not found" in body["detail"].lower()


def test__post_bookmark__duplicate_conv_msg__creates_second_row_no_409(client_with_bookmarks):
    """BKM-DUPLICATE-PINNED (P4.3). Duplicate (conv, msg) creates a SECOND row.

    The frontend API contract (PLANS/2026.05.07-frontend-api-contract.md:878-880)
    flags this as ambiguous: probably 409 vs 200-with-existing. The actual backend
    contract is "duplicates allowed; new id every POST" — pin it. If a future
    change adds a 409 / dedup contract, this test must be flipped intentionally,
    not silently.
    """
    client, _ = client_with_bookmarks
    payload = {
        "conversation_id": "conv-X", "message_uuid": "msg-X",
        "source": "claude_code", "snippet": "first", "note": "n1",
    }
    r1 = client.post("/api/bookmarks", json=payload)
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    r2 = client.post("/api/bookmarks", json={**payload, "snippet": "second", "note": "n2"})
    assert r2.status_code == 201, (
        "current contract: duplicates allowed (no 409). If this is now 409, "
        "update the contract clause BKM-DUPLICATE-PINNED."
    )
    id2 = r2.json()["id"]
    assert id1 != id2, "each duplicate POST must mint a fresh id"

    listing = client.get("/api/bookmarks").json()["bookmarks"]
    assert len(listing) == 2
    ids = {b["id"] for b in listing}
    assert ids == {id1, id2}


def test__post_bookmark__os_replace_fails__no_tmp_leak(client_with_bookmarks, monkeypatch):
    """BKM-ATOMIC-RECOVERY (P2.4). Failed atomic-write swap leaves no .tmp leak.

    Per TESTING.md section 5.8: monkeypatch the rename at the Python
    boundary, assert (a) the inner exception propagates and (b) no tmp file
    survives in the bookmarks dir. Bookmarks have no pre-existing file
    invariant to compare against (this can be the FIRST write), so byte-
    identity isn't tested here — the leak check is the recovery contract.
    """
    import pathlib
    client, path = client_with_bookmarks

    def _boom(self, _target):
        raise OSError("simulated kernel-level rename failure")

    monkeypatch.setattr(pathlib.Path, "replace", _boom)

    with pytest.raises(OSError, match="simulated kernel-level rename failure"):
        client.post("/api/bookmarks", json={
            "conversation_id": "c", "message_uuid": "m", "source": "claude_code",
            "snippet": "s",
        })

    leaked = list(path.parent.glob("bookmarks.json.tmp*"))
    assert leaked == [], f"leaked tmp files after failed atomic write: {leaked}"


# ---------------------------------------------------------------------------
# Hunt #6 — `extra='forbid'` on BookmarkCreate / BookmarkUpdate.
#
# Pydantic v2's default `extra='ignore'` silently drops unknown fields on a
# POST / PATCH body. For a mutation endpoint that's a silent-data-loss bug:
# a frontend typo (`{"notee": "x"}`) on PATCH returns 200 OK with the field
# unchanged. The user sees "saved" and thinks the note was updated.
#
# `extra='forbid'` turns the typo into a 422 at the wire boundary, mirroring
# `PreferencesWrite` (see `test_patch_preferences_unknown_field_returns_422`).
# Bookmarks are the second user-input write surface on the backend; the
# rationale is identical.
# ---------------------------------------------------------------------------


def test__create_bookmark__unknown_field__returns_422(client_with_bookmarks):
    """POST with a typo'd field must return 422 (silent-drop guard).

    The pre-`forbid` default silently dropped `notee` and stored the
    bookmark with `note=""` — a successful 201 that doesn't match the
    user's intent.
    """
    client, _ = client_with_bookmarks
    r = client.post(
        "/api/bookmarks",
        json={
            "conversation_id": "c",
            "message_uuid": "m",
            "source": "claude_code",
            "snippet": "s",
            "notee": "typo lives here",
        },
    )
    assert r.status_code == 422, (
        f"unknown field on POST must 422; got {r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", [])
    assert any(
        "notee" in str(e) or "extra_forbidden" in str(e).lower()
        for e in (detail if isinstance(detail, list) else [detail])
    ), f"422 detail should reference the offending field: {r.json()!r}"


def test__update_bookmark__unknown_field__returns_422(client_with_bookmarks):
    """PATCH with a typo'd field must return 422.

    Update is the worst-case silent-drop: a `{"notee": "new"}` PATCH
    used to return 200 OK with the bookmark unchanged.
    """
    client, _ = client_with_bookmarks
    # Seed a real bookmark so the route path resolves to the validator.
    r = client.post(
        "/api/bookmarks",
        json={"conversation_id": "c", "message_uuid": "m", "source": "claude_code"},
    )
    assert r.status_code == 201
    bid = r.json()["id"]

    r2 = client.patch(f"/api/bookmarks/{bid}", json={"notee": "typo"})
    assert r2.status_code == 422, (
        f"unknown field on PATCH must 422; got {r2.status_code}: {r2.text}"
    )
    detail = r2.json().get("detail", [])
    assert any(
        "notee" in str(e) or "extra_forbidden" in str(e).lower()
        for e in (detail if isinstance(detail, list) else [detail])
    ), f"422 detail should reference the offending field: {r2.json()!r}"


def test__update_bookmark__valid_partial_still_accepted(client_with_bookmarks):
    """Sanity pin: a real partial-update body still 200s post-forbid.

    Without this, a future regression could tighten the schema to require
    BOTH `note` and `snippet` and this file's other tests would still pass.
    """
    client, _ = client_with_bookmarks
    r = client.post(
        "/api/bookmarks",
        json={"conversation_id": "c", "message_uuid": "m", "source": "claude_code"},
    )
    bid = r.json()["id"]
    r2 = client.patch(f"/api/bookmarks/{bid}", json={"note": "valid"})
    assert r2.status_code == 200, f"valid partial PATCH must still 200: {r2.text}"
    assert r2.json()["note"] == "valid"


def test__write_uses_orjson__unicode_preserved_and_trailing_newline(
    client_with_bookmarks,
):
    """BKM-ORJSON (perf-polish A1). On-disk file matches orjson output.

    Two byte-level invariants pin the migration from stdlib json to orjson:

      1. Non-ASCII characters (emoji, accented prose) are written as native
         UTF-8 bytes, NOT as ``\\uXXXX`` escape sequences. stdlib
         ``json.dumps(..., indent=2)`` defaults to ``ensure_ascii=True`` and
         would emit ``\\ud83d\\udcd6`` for a book emoji; orjson emits the
         four-byte UTF-8 sequence directly.

      2. The file ends with a single trailing newline (``OPT_APPEND_NEWLINE``)
         so the artifact stays POSIX-friendly (cat / diff / git ergonomics).

    Together these two checks fail for any stdlib-json writer and pass only
    for the orjson writer with ``OPT_INDENT_2 | OPT_APPEND_NEWLINE``.
    """
    client, path = client_with_bookmarks
    r = client.post(
        "/api/bookmarks",
        json={
            "conversation_id": "c-unicode",
            "message_uuid": "m-unicode",
            "source": "claude_code",
            "snippet": "café",
            "note": "Notes with emoji 📖 and accent é",
        },
    )
    assert r.status_code == 201

    raw_bytes = path.read_bytes()
    # Native UTF-8 bytes for the emoji (book = U+1F4D6 = F0 9F 93 96).
    assert b"\xf0\x9f\x93\x96" in raw_bytes, (
        "orjson must emit emoji as native UTF-8 bytes, not stdlib's "
        "\\uXXXX escape sequences"
    )
    # No ASCII-escaped \\u sequences (stdlib default would emit \\ud83d\\udcd6).
    assert b"\\u" not in raw_bytes, (
        "found ASCII-escaped unicode in on-disk file — stdlib json writer "
        "still active?"
    )
    # Trailing newline (OPT_APPEND_NEWLINE).
    assert raw_bytes.endswith(b"\n"), (
        "bookmarks file must end with a single trailing newline "
        "(orjson OPT_APPEND_NEWLINE)"
    )
