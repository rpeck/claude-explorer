"""Issue #80: search finds a word in every session source, on both paths.

The user's demand (2026-11-20): "You MUST test search with ALL session
sources!" The corpus is a copy of the e2e fixture tree, which has one
session per source with a word that no other session holds:

* Claude Desktop (``CLAUDE_AI``): ``NEEDLE_HANDSHAKE``.
* Claude Code (``CLAUDE_CODE``): ``NEEDLE_CC``.
* local Cowork (``CLAUDE_COWORK``): ``NEEDLE_COWORKLOCAL``.
* remote Cowork (``CLAUDE_COWORK``): ``NEEDLE_COWORKREMOTE``.
* Codex (``CODEX``): ``semicolon`` (the archive in ``desktop/codex/``).

For each word, the FTS5 path (snippet and full) and the linear path find
that one session with ``source="all"`` and with its own source, and find
nothing with each other source. ``/api/search`` does the same.

Index freshness for Codex: a Codex session that the archive copy pass
writes after the index build is found after the watcher's drift pass.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.tests import codex_fake as fake

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

SOURCES = ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK", "CODEX")

#: (case id, source, word, conversation uuid, message uuid of the hit)
CASES = (
    ("desktop", "CLAUDE_AI", "NEEDLE_HANDSHAKE", "0f415a45-9c62-8671-d4ad-53b84acb7e1a",
     "f7c8d26f-69bb-2cb3-e8db-87e7b2c3f8cf"),
    ("claude-code", "CLAUDE_CODE", "NEEDLE_CC", "435e56ed-50ba-21c0-3495-0e8749b20543",
     "4b8feb03-4b8a-494d-49fd-05a0fc6afbcd"),
    ("cowork-local", "CLAUDE_COWORK", "NEEDLE_COWORKLOCAL", "cccc2222-3333-4444-5555-666677778888", "u-1"),
    ("cowork-remote", "CLAUDE_COWORK", "NEEDLE_COWORKREMOTE", "session_01E2EREMOTE00000000000001", "u-2"),
    ("codex", "CODEX", "semicolon", fake.THREAD_LEGACY, "2bc8c4a4-babc-5c5b-b49d-f794b61f81c3"),
)
CASE_IDS = [c[0] for c in CASES]

#: Every (case, other source) pair: the word must not be found there.
OTHER_SOURCE_CASES = [(c, other) for c in CASES for other in SOURCES if other != c[1]]
OTHER_SOURCE_IDS = [f"{c[0]}-under-{other}" for c, other in OTHER_SOURCE_CASES]

PATHS = ("fts-snippet", "fts-full", "linear")


def build_e2e_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Copy the e2e fixture tree under ``tmp_path``, point the settings at
    it, and build the index over it as the process singleton. Return the
    index. ``CODEX_HOME`` is missing, as in e2e. The MCP tests use it too."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache

    root = tmp_path / "corpus"
    data_dir = root / "data"
    claude_dir = root / "claude"
    app_dir = root / "desktop-app"
    shutil.copytree(FIXTURES_DIR / "desktop", data_dir)
    shutil.copytree(FIXTURES_DIR / "claude", claude_dir)
    shutil.copytree(FIXTURES_DIR / "cowork", app_dir)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(app_dir))
    monkeypatch.setenv("CODEX_HOME", str(root / "codex-home-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "search-index.sqlite")
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    si.reset_search_index_for_tests()

    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    return idx


def close_e2e_corpus(idx: Any) -> None:
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import invalidate_detail_dict_cache

    idx.close()
    si.reset_search_index_for_tests()
    invalidate_detail_dict_cache()
    clear_cache()
    config.get_settings.cache_clear()


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from backend.store import ConversationStore

    idx = build_e2e_corpus(tmp_path, monkeypatch)
    try:
        yield ConversationStore()
    finally:
        close_e2e_corpus(idx)


def use_path(path: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Force one search path and return its ``context_size``.

    The FTS cases make the linear scan raise, so a silent fallback fails the
    test. The linear case hides the index."""
    from backend import search
    from backend import search_index as si

    if path == "linear":
        monkeypatch.setattr(si, "get_search_index", lambda: None)
        return "snippet"

    def _no_linear(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the search fell back to the linear scan")

    monkeypatch.setattr(search, "_search_via_linear_scan", _no_linear)
    return "full" if path == "fts-full" else "snippet"


def _hits(store: Any, word: str, source: str, context_size: str) -> dict[str, set[str]]:
    from backend.search import search_conversations

    resp = search_conversations(store, word, source=source, context_size=context_size)
    return {r.conversation_uuid: {m.message_uuid for m in r.matching_messages} for r in resp.results}


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_word_is_found_with_source_all(corpus: Any, case: tuple, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, word, conv_uuid, msg_uuid = case
    hits = _hits(corpus, word, "all", use_path(path, monkeypatch))
    assert set(hits) == {conv_uuid}, f"{word!r} under all: {sorted(hits)}"
    assert msg_uuid in hits[conv_uuid], f"{word!r}: the hit message {msg_uuid} is missing: {hits[conv_uuid]}"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_word_is_found_with_its_own_source(corpus: Any, case: tuple, path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _, source, word, conv_uuid, msg_uuid = case
    hits = _hits(corpus, word, source, use_path(path, monkeypatch))
    assert set(hits) == {conv_uuid}, f"{word!r} under {source}: {sorted(hits)}"
    assert msg_uuid in hits[conv_uuid]


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(("case", "other"), OTHER_SOURCE_CASES, ids=OTHER_SOURCE_IDS)
def test_the_word_is_not_found_with_another_source(
    corpus: Any, case: tuple, other: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    word = case[2]
    hits = _hits(corpus, word, other, use_path(path, monkeypatch))
    assert hits == {}, f"{word!r} under {other}: {sorted(hits)}"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_the_api_finds_the_word_with_source_all_and_its_own_source_only(corpus: Any, case: tuple) -> None:
    from fastapi.testclient import TestClient

    from backend.main import app

    _, source, word, conv_uuid, _ = case
    client = TestClient(app)  # No ``with``: the lifespan (watcher, index build) stays off.
    for src in ("all", *SOURCES):
        r = client.get("/api/search", params={"q": word, "source": src})
        assert r.status_code == 200, r.text
        got = {res["conversation_uuid"] for res in r.json()["results"]}
        want = {conv_uuid} if src in ("all", source) else set()
        assert got == want, f"/api/search q={word!r} source={src}: {sorted(got)}"


# ---------------------------------------------------------------------------
# Index freshness for Codex
# ---------------------------------------------------------------------------

THREAD_FRESH = "019aa000-0000-7000-8000-0000000000f1"
FRESH_WORD = "quasarfresh"


@pytest.fixture
def codex_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A synthetic Codex home, copied into the archive, with the index built
    over the archive as the process singleton."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore, invalidate_detail_dict_cache
    from backend.tests._platform_home import patch_home
    from backend.write_gate import run_archive_syncs

    home = patch_home(monkeypatch, tmp_path / "home")
    home.mkdir()
    codex = fake.build_codex_home(home / ".codex")
    data_dir = tmp_path / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(tmp_path / "desktop-app"))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr(si, "default_index_path", lambda: tmp_path / "explorer" / "search-index.sqlite")
    config.get_settings.cache_clear()
    clear_cache()
    invalidate_detail_dict_cache()
    si.reset_search_index_for_tests()

    run_archive_syncs(ConversationStore(), "cli", 10)
    idx = si.SearchIndex(tmp_path / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield codex
    finally:
        idx.close()
        si.reset_search_index_for_tests()
        invalidate_detail_dict_cache()
        clear_cache()
        config.get_settings.cache_clear()


def _write_fresh_thread(codex: Path) -> None:
    t0 = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
    cwd = "/home/fixture/proj-fresh"
    fake.write_rollout(
        fake.rollout_path(codex, t0, THREAD_FRESH),
        [
            fake.session_meta(t0, THREAD_FRESH, cwd=cwd, history_mode="paginated"),
            fake.turn_context(t0 + timedelta(seconds=1), "turn-fresh", cwd=cwd, model="gpt-5.5-codex"),
            fake.item_completed(t0 + timedelta(seconds=2), THREAD_FRESH, "turn-fresh",
                                fake.user_message_item("item-fresh-user", f"Explain the {FRESH_WORD} flag.")),
            fake.item_completed(t0 + timedelta(seconds=3), THREAD_FRESH, "turn-fresh",
                                fake.agent_message_item("item-fresh-reply", "The flag turns on the new parser.")),
        ],
    )


def test_a_codex_session_copied_after_the_index_build_is_found_after_the_drift_pass(
    codex_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import cc_watcher
    from backend.store import ConversationStore

    _write_fresh_thread(codex_env)
    assert _hits(ConversationStore(), FRESH_WORD, "CODEX", "snippet") == {}, "precondition: not indexed yet"

    # The watcher's event-driven pass: the archive copy, then the drift pass.
    cc_watcher._event_drift_pass()

    use_path("fts-snippet", monkeypatch)
    for source in ("all", "CODEX"):
        hits = _hits(ConversationStore(), FRESH_WORD, source, "snippet")
        assert set(hits) == {THREAD_FRESH}, f"{FRESH_WORD!r} under {source} after the drift pass: {sorted(hits)}"
