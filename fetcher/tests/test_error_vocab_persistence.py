"""A1 hunt — fetcher-side counterpart of error_vocab_persistence tests.

Mirrors the backend rollup tests but exercises the in-process
`run_all_orgs()` path used by `claude-explorer fetch` CLI. The
`backend/routers/fetch.py` SSE path is exercised in the backend test
of the same name.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from fetcher.bulk_fetch import ClaudeFetcher, FetchAuthError

PERSONAL = "ae24ae66-4622-48e7-b4b3-1ab2c49f933d"
COWORK = "0c0c170b-1234-5678-90ab-cdef00000000"


def _make_fetcher(tmp_path: Path) -> ClaudeFetcher:
    orgs = [
        {"uuid": PERSONAL, "name": "Personal", "capabilities": ["chat"], "seen_in_response": True},
        {"uuid": COWORK, "name": "Cowork", "capabilities": ["chat"], "seen_in_response": True},
    ]
    return ClaudeFetcher(
        session_key="sk-test",
        orgs=orgs,
        primary_org_id=PERSONAL,
        output_dir=tmp_path / "conversations",
        files_dir=tmp_path / "files",
        download_files=False,
        delay=0.0,
    )


def _conv(uuid: str) -> dict:
    return {
        "uuid": uuid,
        "name": "X",
        "model": "claude-sonnet-4-6",
        "created_at": "2024-03-01T12:00:00Z",
        "updated_at": "2024-03-01T13:00:00Z",
    }


def test_run_all_orgs_persists_error_kind_for_403(tmp_path: Path) -> None:
    """Secondary 403: persisted record carries error_kind=ORG_FORBIDDEN + http_status=403."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("403 Forbidden")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    cowork = by_id[COWORK]
    assert cowork["status"] == "skipped"
    assert cowork["error_kind"] == "ORG_FORBIDDEN"
    assert cowork["http_status"] == 403
    # Bidirectional: the legacy ad-hoc string MUST NOT be persisted as the kind.
    assert cowork["error_kind"] != "HTTP_403"


def test_run_all_orgs_persists_error_kind_for_401(tmp_path: Path) -> None:
    """Secondary 401: persisted record carries error_kind=AUTH_EXPIRED + http_status=401."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("401 Unauthorized")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    cowork = by_id[COWORK]
    assert cowork["error_kind"] == "AUTH_EXPIRED"
    assert cowork["http_status"] == 401


def test_run_all_orgs_persists_error_kind_for_404(tmp_path: Path) -> None:
    """Secondary 404: persisted record carries error_kind=ORG_NOT_FOUND + http_status=404."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("404 Not Found")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    cowork = by_id[COWORK]
    assert cowork["error_kind"] == "ORG_NOT_FOUND"
    assert cowork["http_status"] == 404


def test_run_all_orgs_persists_terminal_kind_for_unknown_exception(tmp_path: Path) -> None:
    """A non-domain exception persists error_kind=TERMINAL + http_status=None."""
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise RuntimeError("something else went wrong")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    by_id = {o["org_id"]: o for o in idx["orgs"]}
    cowork = by_id[COWORK]
    assert cowork["status"] == "failed"
    assert cowork["error_kind"] == "TERMINAL"
    assert cowork["http_status"] is None
    # Bidirectional: NO `type(e).__name__` style code leaks into error_kind.
    assert cowork["error_kind"] != "RuntimeError"


def test_run_all_orgs_no_legacy_error_code_in_new_writes(tmp_path: Path) -> None:
    """Post-fix writes must not include the legacy `error_code` HTTP_*** string.

    The field may be absent entirely OR explicitly None — either is fine.
    What must NOT happen: persisting `"HTTP_403"` / `"HTTP_401"` / `"HTTP_404"`
    / `"TRANSIENT"` as `error_code`.
    """
    fetcher = _make_fetcher(tmp_path)

    def fake_list(self):
        if self.current_org["uuid"] == COWORK:
            raise FetchAuthError("403 Forbidden")
        return [_conv("11111111-1111-2222-3333-444444444444")]

    def fake_fetch(self, uuid):
        return _conv(uuid)

    with patch.object(ClaudeFetcher, "fetch_conversation_list", fake_list), \
         patch.object(ClaudeFetcher, "fetch_conversation", fake_fetch):
        fetcher.run_all_orgs()

    idx = json.loads((tmp_path / "conversations" / "_index.json").read_text())
    raw = json.dumps(idx)
    # No HTTP_*** strings persisted ANYWHERE in the on-disk record.
    assert "HTTP_401" not in raw
    assert "HTTP_403" not in raw
    assert "HTTP_404" not in raw
