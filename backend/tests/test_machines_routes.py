"""A2 (issue #92): the HTTP routes of the Settings "Machines" section.

Every POST and PUT follows ``POST /api/shutdown``: the header
``X-Claude-Explorer-Action: machines`` (403 without it), the Origin check
against the Host guard's allowlist (403), and the corrupt-config gate (503).
Import also refuses while the data-folder cloud gate is closed (409).
The shared-folder consent is ``POST /api/write-consent/shared-archive``
with ``X-Claude-Explorer-Action: allow-shared-archive-writes`` and the body
``{"shared_archive_dir", "provider"}``. Synthetic data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.tests._machines_env import (
    World,
    make_world,
    tree,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


H = {"X-Claude-Explorer-Action": "machines"}
EVIL = {**H, "Origin": "https://evil.example.com"}
S1 = "a2e1e1e1-0000-4000-8000-000000000001"
S_WORK = "a2e1e1e1-0000-4000-8000-000000000002"
CONSENT_URL = "/api/write-consent/shared-archive"
CONSENT_H = {"X-Claude-Explorer-Action": "allow-shared-archive-writes"}


def _client(m: Any) -> TestClient:
    from backend.main import app

    m.activate()
    return TestClient(app)


def _b_ready(world: World) -> None:
    b = world.b
    b.cc_session(S1, "routeword", cwd=b.cwd("Source", "personal", "app"))
    b.cc_session(S_WORK, "routeworkword", cwd=b.cwd("Source", "work", "api"))
    b.sync()


def _bundle_bytes(world: World, **body: Any) -> bytes:
    _b_ready(world)
    world.b.export_all()
    r = _client(world.b).post(
        "/api/machines/export",
        json={"passphrase": None, "full": False, **body},
        headers=H,
    )
    assert r.status_code == 200, r.text
    return r.content


def _import(
    world: World,
    data: bytes,
    passphrase: str | None = None,
    headers: dict[str, str] = H,
) -> Any:
    form = {"passphrase": passphrase} if passphrase is not None else {}
    return _client(world.a).post(
        "/api/machines/import",
        files={"file": ("b.cebundle", data, "application/octet-stream")},
        data=form,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Machines and labels
# ---------------------------------------------------------------------------


def test_get_machines_lists_this_machine(world: World) -> None:
    world.a.cc_session(S1, "aword")
    world.a.sync()

    r = _client(world.a).get("/api/machines")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["this_machine_id"] == world.a.machine_id
    [me] = body["machines"]
    assert me["machine_id"] == world.a.machine_id
    assert me["is_this_machine"] is True
    assert me["session_count"] == 1
    assert set(me) >= {"machine_id", "label", "is_this_machine", "session_count"}


def test_put_self_label_sets_the_label(world: World) -> None:
    world.a.ensure_id()

    r = _client(world.a).put(
        "/api/machines/self/label", json={"label": "Home Mac"}, headers=H
    )

    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Home Mac"
    ident = json.loads(
        (world.a.explorer_home / "machine.json").read_text(encoding="utf-8")
    )
    assert ident["label"] == "Home Mac"


def test_a_bad_label_is_422(world: World) -> None:
    world.a.ensure_id()

    r = _client(world.a).put(
        "/api/machines/self/label", json={"label": "x" * 65}, headers=H
    )

    assert r.status_code == 422


def test_put_label_of_another_machine_writes_only_the_local_names_file(
    world: World,
) -> None:
    data = _bundle_bytes(world)
    assert _import(world, data).status_code == 200
    b_marker = world.a.foreign(world.b.machine_id) / "machine.json"
    before = b_marker.read_bytes()

    r = _client(world.a).put(
        f"/api/machines/{world.b.machine_id}/label", json={"label": "Office"}, headers=H
    )

    assert r.status_code == 200, r.text
    names = json.loads(
        (world.a.explorer_home / "machine-labels.json").read_text(encoding="utf-8")
    )
    assert names == {world.b.machine_id: "Office"}
    assert b_marker.read_bytes() == before
    labels = {
        m["machine_id"]: m["label"]
        for m in _client(world.a).get("/api/machines").json()["machines"]
    }
    assert labels[world.b.machine_id] == "Office"


def test_the_conversation_list_carries_the_machine_fields(world: World) -> None:
    data = _bundle_bytes(world)
    assert _import(world, data).status_code == 200

    r = _client(world.a).get("/api/conversations", params={"source": "CLAUDE_CODE"})

    assert r.status_code == 200, r.text
    items = r.json()
    items = items["conversations"] if isinstance(items, dict) else items
    [row] = [i for i in items if i["uuid"] == S1]
    assert row["machine_id"] == world.b.machine_id
    assert row["machine_label"] == f"Machine {world.b.machine_id[:8]}"
    assert row["machine_labels"] == [row["machine_label"]]


# ---------------------------------------------------------------------------
# Rules and preview
# ---------------------------------------------------------------------------


def test_get_export_rules_defaults_to_off(world: World) -> None:
    r = _client(world.b).get("/api/machines/export-rules")

    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "off"


def test_put_export_rules_saves_them(world: World) -> None:
    rules = {
        "version": 1,
        "mode": "export",
        "allow": [{"source": "claude-code", "project": "~/Source/**"}],
        "deny": ["~/Source/work/**"],
        "since": "2026-01-01",
    }

    r = _client(world.b).put("/api/machines/export-rules", json=rules, headers=H)

    assert r.status_code == 200, r.text
    saved = json.loads(
        (world.b.explorer_home / "export-rules.json").read_text(encoding="utf-8")
    )
    assert saved["mode"] == "export"
    assert saved["deny"] == ["~/Source/work/**"]


def test_put_export_rules_with_a_bad_mode_is_422(world: World) -> None:
    r = _client(world.b).put(
        "/api/machines/export-rules",
        json={"version": 1, "mode": "everything"},
        headers=H,
    )

    assert r.status_code == 422
    assert not (world.b.explorer_home / "export-rules.json").exists()


def test_get_export_preview_lists_the_allowed_files(world: World) -> None:
    _b_ready(world)
    world.b.export_all(deny=["~/Source/work/**"])

    r = _client(world.b).get("/api/machines/export-preview")

    assert r.status_code == 200, r.text
    body = r.json()
    paths = [f["path"] for f in body["files"]]
    assert any(p.endswith(f"/{S1}.jsonl") for p in paths)
    assert not any(p.endswith(f"/{S_WORK}.jsonl") for p in paths)
    assert body["file_count"] == len(body["files"])
    assert body["total_bytes"] == sum(f["bytes"] for f in body["files"])
    assert set(body["files"][0]) >= {"path", "bytes", "source", "project"}


# ---------------------------------------------------------------------------
# Export and import
# ---------------------------------------------------------------------------


def test_post_export_returns_the_bundle_as_an_attachment(world: World) -> None:
    import io
    import zipfile

    _b_ready(world)
    world.b.export_all()

    r = _client(world.b).post(
        "/api/machines/export", json={"passphrase": None, "full": False}, headers=H
    )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/octet-stream")
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".cebundle" in disposition
    assert zipfile.is_zipfile(io.BytesIO(r.content))


def test_post_export_with_a_passphrase_returns_an_encrypted_bundle(
    world: World,
) -> None:
    data = _bundle_bytes(world, passphrase="synthetic route pass")

    assert data.startswith(b"CEXB1")


def test_post_export_in_mode_off_is_409(world: World) -> None:
    _b_ready(world)

    r = _client(world.b).post(
        "/api/machines/export", json={"passphrase": None, "full": False}, headers=H
    )

    assert r.status_code == 409
    assert r.json()["detail"]


def test_post_import_imports_the_bundle(world: World) -> None:
    data = _bundle_bytes(world)

    r = _import(world, data)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["machine_id"] == world.b.machine_id
    assert body["counts"]["copied"] >= 2
    assert S1 in world.a.rows("CLAUDE_CODE")


def test_post_import_of_an_encrypted_bundle_takes_the_passphrase(world: World) -> None:
    data = _bundle_bytes(world, passphrase="synthetic route pass")

    r = _import(world, data, passphrase="synthetic route pass")

    assert r.status_code == 200, r.text
    assert S1 in world.a.rows("CLAUDE_CODE")


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda data: b"junk bytes", "not_a_bundle"),
        (lambda data: data, "bad_passphrase"),
    ],
)
def test_post_import_refusals_are_400_with_the_reason(
    world: World, make: Any, reason: str
) -> None:
    data = make(_bundle_bytes(world, passphrase="synthetic route pass"))

    r = _import(world, data, passphrase="synthetic wrong pass")

    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == reason


def test_post_import_is_503_while_the_config_is_corrupt(world: World) -> None:
    data = _bundle_bytes(world)
    world.a.write_corrupt_config()

    r = _import(world, data)

    assert r.status_code == 503
    assert not (world.a.data_dir / "machines").exists()


def test_post_import_is_409_while_the_data_folder_cloud_gate_is_closed(
    world: World,
) -> None:
    from backend.tests._machines_env import Machine

    data = _bundle_bytes(world)
    # A fourth machine whose data folder is in Dropbox, with no consent.
    d = Machine(
        "d", world.tmp / "machine-d", world.a.mp, world.tmp / "machine-d" / "codex"
    )
    (d.home / "Dropbox").mkdir()
    d.data_dir = d.home / "Dropbox" / "explorer" / "conversations"
    d.data_dir.mkdir(parents=True)
    world.a = d

    r = _import(world, data)

    assert r.status_code == 409
    assert not (d.data_dir / "machines").exists()


# ---------------------------------------------------------------------------
# The shared folder
# ---------------------------------------------------------------------------


def test_put_shared_folder_saves_it_in_the_config(world: World) -> None:
    r = _client(world.b).put(
        "/api/machines/shared-folder", json={"path": str(world.shared)}, headers=H
    )

    assert r.status_code == 200, r.text
    assert r.json()["shared_archive_dir"] == str(world.shared)
    saved = json.loads(
        (world.b.explorer_home / "config.json").read_text(encoding="utf-8")
    )
    assert saved["shared_archive_dir"] == str(world.shared)
    assert world.b.settings().shared_archive_dir == Path(world.shared)


def test_put_shared_folder_null_clears_it(world: World) -> None:
    world.b.write_config(shared_archive_dir=str(world.shared), other_key="kept")

    r = _client(world.b).put(
        "/api/machines/shared-folder", json={"path": None}, headers=H
    )

    assert r.status_code == 200, r.text
    saved = json.loads(
        (world.b.explorer_home / "config.json").read_text(encoding="utf-8")
    )
    assert "shared_archive_dir" not in saved or saved["shared_archive_dir"] is None
    assert saved["other_key"] == "kept"


def test_post_shared_folder_remove_removes_only_the_own_folder(world: World) -> None:
    from backend import machines

    b = world.b
    _b_ready(world)
    b.write_config(shared_archive_dir=str(world.shared))
    b.export_all()
    b.sync()
    machines.sync_shared_folder(b.settings(), "refresh", 10)
    other = world.shared / "machines" / "0e0e0e0e-0000-4000-8000-000000000000"
    other.mkdir(parents=True)
    (other / "machine.json").write_text(
        '{"machine_id": "0e0e0e0e-0000-4000-8000-000000000000"}', encoding="utf-8"
    )
    assert (world.shared / "machines" / b.machine_id).is_dir()

    wrong = _client(b).post(
        "/api/machines/shared-folder/remove", json={"confirm": "yes"}, headers=H
    )
    assert wrong.status_code in (400, 422)
    assert (world.shared / "machines" / b.machine_id).is_dir()

    r = _client(b).post(
        "/api/machines/shared-folder/remove", json={"confirm": b.machine_id}, headers=H
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"removed": True}
    assert not (world.shared / "machines" / b.machine_id).exists()
    assert (other / "machine.json").is_file()


def _shared_in_dropbox(world: World) -> Path:
    shared = world.b.home / "Dropbox" / "ce-shared"
    shared.mkdir(parents=True)
    world.b.write_config(shared_archive_dir=str(shared))
    return shared


def test_shared_archive_consent_writes_the_resolved_folder(world: World) -> None:
    import os

    shared = _shared_in_dropbox(world)

    r = _client(world.b).post(
        CONSENT_URL,
        json={"shared_archive_dir": os.path.realpath(shared), "provider": "Dropbox"},
        headers=CONSENT_H,
    )

    assert r.status_code == 200, r.text
    saved = json.loads(
        (world.b.explorer_home / "config.json").read_text(encoding="utf-8")
    )
    assert saved["allow_writes_in_shared_archive"] == os.path.realpath(shared)
    assert saved["shared_archive_dir"] == str(shared)


def test_shared_archive_consent_refuses_a_stale_folder_or_provider(
    world: World,
) -> None:
    import os

    shared = _shared_in_dropbox(world)

    r = _client(world.b).post(
        CONSENT_URL,
        json={"shared_archive_dir": os.path.realpath(shared), "provider": "OneDrive"},
        headers=CONSENT_H,
    )

    assert r.status_code == 409
    saved = json.loads(
        (world.b.explorer_home / "config.json").read_text(encoding="utf-8")
    )
    assert "allow_writes_in_shared_archive" not in saved


def test_shared_archive_consent_needs_its_header(world: World) -> None:
    import os

    shared = _shared_in_dropbox(world)

    r = _client(world.b).post(
        CONSENT_URL,
        json={"shared_archive_dir": os.path.realpath(shared), "provider": "Dropbox"},
        headers=H,
    )

    assert r.status_code == 403


# ---------------------------------------------------------------------------
# The header, Origin, and corrupt-config rules on every writer
# ---------------------------------------------------------------------------

WRITERS = [
    ("put", "/api/machines/self/label", {"json": {"label": "Home Mac"}}),
    (
        "put",
        "/api/machines/0e0e0e0e-0000-4000-8000-000000000000/label",
        {"json": {"label": "Office"}},
    ),
    ("put", "/api/machines/export-rules", {"json": {"version": 1, "mode": "export"}}),
    ("post", "/api/machines/export", {"json": {"passphrase": None, "full": False}}),
    (
        "post",
        "/api/machines/import",
        {"files": {"file": ("b.cebundle", b"junk", "application/octet-stream")}},
    ),
    ("put", "/api/machines/shared-folder", {"json": {"path": "/tmp/a2-nowhere"}}),
    ("post", "/api/machines/shared-folder/remove", {"json": {"confirm": "x"}}),
]


@pytest.mark.parametrize(
    ("method", "url", "kw"), WRITERS, ids=[f"{m} {u}" for m, u, _ in WRITERS]
)
def test_a_writer_route_needs_the_action_header(
    world: World, method: str, url: str, kw: dict[str, Any]
) -> None:
    world.b.ensure_id()
    before = tree(world.b.explorer_home)

    r = getattr(_client(world.b), method)(url, **kw)

    assert r.status_code == 403, r.text
    assert tree(world.b.explorer_home) == before


@pytest.mark.parametrize(
    ("method", "url", "kw"), WRITERS, ids=[f"{m} {u}" for m, u, _ in WRITERS]
)
def test_a_writer_route_refuses_a_foreign_origin(
    world: World, method: str, url: str, kw: dict[str, Any]
) -> None:
    world.b.ensure_id()
    before = tree(world.b.explorer_home)

    r = getattr(_client(world.b), method)(url, headers=EVIL, **kw)

    assert r.status_code == 403, r.text
    assert tree(world.b.explorer_home) == before


@pytest.mark.parametrize(
    ("method", "url", "kw"), WRITERS, ids=[f"{m} {u}" for m, u, _ in WRITERS]
)
def test_a_writer_route_is_503_while_the_config_is_corrupt(
    world: World, method: str, url: str, kw: dict[str, Any]
) -> None:
    world.b.ensure_id()
    world.b.write_corrupt_config()
    before = tree(world.b.explorer_home)

    r = getattr(_client(world.b), method)(url, headers=H, **kw)

    assert r.status_code == 503, r.text
    assert tree(world.b.explorer_home) == before
