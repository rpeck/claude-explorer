from __future__ import annotations

from pathlib import Path

import pytest

import fetcher.run_fetch as rf


class _FakeFetcher:
    last_kwargs = None
    def __init__(self, **kwargs):
        _FakeFetcher.last_kwargs = kwargs
    def run(self, limit=None):
        _FakeFetcher.ran_with = limit


def test_v1_creds_resolve_single_org(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rf, "ClaudeFetcher", _FakeFetcher)
    monkeypatch.setattr(rf, "load_credentials",
                        lambda p: {"session_key": "sk", "org_id": "org-1"})
    rf.run_incremental_fetch(
        output_dir=tmp_path, files_dir=tmp_path, credentials=tmp_path / "c.json",
        session_key=None, org_id=None, incremental=True, download_files=False,
        delay=0.0, limit=5, verbose=False,
    )
    kw = _FakeFetcher.last_kwargs
    assert kw["session_key"] == "sk"
    assert kw["primary_org_id"] == "org-1"
    assert _FakeFetcher.ran_with == 5


def test_missing_session_key_raises_clickexception(monkeypatch, tmp_path: Path) -> None:
    import click
    monkeypatch.setattr(rf, "ClaudeFetcher", _FakeFetcher)
    monkeypatch.setattr(rf, "load_credentials", lambda p: {"org_id": "org-1"})
    with pytest.raises(click.ClickException):
        rf.run_incremental_fetch(
            output_dir=tmp_path, files_dir=tmp_path, credentials=tmp_path / "c.json",
            session_key=None, org_id=None, incremental=True, download_files=False,
            delay=0.0, limit=None, verbose=False,
        )
