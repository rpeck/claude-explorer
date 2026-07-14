from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import mcp_config_install as mci


def _read(p: Path) -> dict:
    return json.loads(p.read_text())


def test_merge_creates_file_and_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "sub" / "x.json"  # parent does not exist yet
    changed = mci._merge_entry(cfg, mci.SERVER_NAME, mci.mcp_block())
    assert changed is True
    data = _read(cfg)
    assert data["mcpServers"][mci.SERVER_NAME] == mci.mcp_block()


def test_merge_is_idempotent(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    mci._merge_entry(cfg, mci.SERVER_NAME, mci.mcp_block())
    changed = mci._merge_entry(cfg, mci.SERVER_NAME, mci.mcp_block())
    assert changed is False


def test_merge_preserves_other_keys_and_servers(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text(json.dumps({
        "theme": "dark",
        "mcpServers": {"other": {"command": "uvx", "args": ["other"]}},
    }))
    mci._merge_entry(cfg, mci.SERVER_NAME, mci.mcp_block())
    data = _read(cfg)
    assert data["theme"] == "dark"
    assert "other" in data["mcpServers"]
    assert mci.SERVER_NAME in data["mcpServers"]


def test_remove_entry(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text(json.dumps({"mcpServers": {
        mci.SERVER_NAME: mci.mcp_block(),
        "other": {"command": "uvx", "args": ["other"]},
    }}))
    changed = mci._remove_entry(cfg, mci.SERVER_NAME)
    assert changed is True
    data = _read(cfg)
    assert mci.SERVER_NAME not in data["mcpServers"]
    assert "other" in data["mcpServers"]


def test_remove_absent_is_noop(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text(json.dumps({"mcpServers": {}}))
    assert mci._remove_entry(cfg, mci.SERVER_NAME) is False


def test_load_corrupt_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text("{ not json ")
    with pytest.raises(ValueError):
        mci._load_config(cfg)


def test_load_non_dict_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        mci._load_config(cfg)


def test_atomic_write_no_partial_on_failure(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "x.json"
    cfg.write_text(json.dumps({"keep": True}))

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mci.os, "replace", boom)
    with pytest.raises(OSError):
        mci._atomic_write_json(cfg, {"new": True})
    # original file untouched, no leftover temp in the dir
    assert _read(cfg) == {"keep": True}
    assert list(tmp_path.glob("*.tmp*")) == []
