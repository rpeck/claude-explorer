"""Issue #80: ``list_sessions`` with a search finds a word in every source.

Same corpus and words as ``backend/tests/test_search_all_sources.py`` (a
copy of the e2e fixture tree: Desktop, Claude Code, local and remote
Cowork, Codex). For each word, ``list_sessions(query=word)`` finds the one
session that holds it with no source, with ``"all"``, and with its own
source, on the FTS path and on the linear path, and finds nothing with
each other source.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_search_all_sources import (
    CASE_IDS,
    CASES,
    OTHER_SOURCE_CASES,
    OTHER_SOURCE_IDS,
    build_e2e_corpus,
    close_e2e_corpus,
    use_path,
)

PATHS = ("fts", "linear")


@pytest.fixture
def e2e_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    idx = build_e2e_corpus(tmp_path, monkeypatch)
    try:
        yield
    finally:
        close_e2e_corpus(idx)


def _list(**kwargs: Any) -> dict[str, Any]:
    from mcp_server import server

    tool = server.list_sessions
    return getattr(tool, "fn", tool)(limit=100, **kwargs)


def _uuids(out: dict[str, Any]) -> set[str]:
    return {s["uuid"] for s in out["sessions"]}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_list_sessions_finds_the_word_with_no_source(
    e2e_corpus: None, case: tuple, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source, word, conv_uuid, _ = case
    use_path("fts-snippet" if path == "fts" else "linear", monkeypatch)
    out = _list(query=word)
    assert _uuids(out) == {conv_uuid}, f"list_sessions(query={word!r}): {sorted(_uuids(out))}"
    assert out["sessions"][0]["source"] == source


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_list_sessions_finds_the_word_with_source_all(
    e2e_corpus: None, case: tuple, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, word, conv_uuid, _ = case
    use_path("fts-snippet" if path == "fts" else "linear", monkeypatch)
    out = _list(query=word, source="all")
    assert _uuids(out) == {conv_uuid}, f"list_sessions(query={word!r}, source='all'): {sorted(_uuids(out))}"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_list_sessions_finds_the_word_with_its_own_source(
    e2e_corpus: None, case: tuple, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, source, word, conv_uuid, _ = case
    use_path("fts-snippet" if path == "fts" else "linear", monkeypatch)
    out = _list(query=word, source=source)
    assert _uuids(out) == {conv_uuid}, f"list_sessions(query={word!r}, source={source!r}): {sorted(_uuids(out))}"
    assert out["sessions"][0]["match_count"] >= 1


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("case", "other"), OTHER_SOURCE_CASES, ids=OTHER_SOURCE_IDS)
def test_list_sessions_does_not_find_the_word_with_another_source(
    e2e_corpus: None, case: tuple, other: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    word = case[2]
    use_path("fts-snippet" if path == "fts" else "linear", monkeypatch)
    out = _list(query=word, source=other)
    assert _uuids(out) == set(), f"list_sessions(query={word!r}, source={other!r}): {sorted(_uuids(out))}"
