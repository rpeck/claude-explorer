"""The corpus and the golden-file check for the source characterization tests.

The goldens record what the list, the viewer, search, Markdown export, and
the 5 MCP tools return today for all three sources. They guard the source
adapter refactor (PLANS/2026.11.18-codex-source.md §4.1, N0 and N1): a
change in any output fails with a diff.

The corpus is a copy of the committed e2e fixtures (Desktop, Claude Code,
local Cowork) plus ``data/cowork-remote/`` (one remote Cowork task, written
once with ``fetcher.remote_sessions.RemoteCoworkFetcher.save_session``).

Normalization, so the goldens are the same on every machine:

* The temp root of the corpus becomes ``<TMP>``, with ``/`` separators.
* Every copied file gets the mtime ``FIXED_MTIME`` before the first read.
* Values of keys that hold an mtime or a cache time (``MTIME_KEYS``)
  become ``<MTIME>``; ``indexed_at`` becomes ``<INDEXED_AT>``.

``UPDATE_GOLDENS=1`` rewrites the goldens instead of comparing them.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
GOLDENS_DIR = Path(__file__).resolve().parent
REMOTE_FIXTURE_DIR = GOLDENS_DIR / "data" / "cowork-remote"

#: 2026-01-01T00:00:00Z. Claude Code reads fall back to the file mtime.
FIXED_MTIME = 1767225600

MTIME_KEYS = frozenset({"mtime", "file_mtime", "cached_at"})

#: One token per conversation, so each search golden pins one source.
SEARCH_TOKENS = (
    "NEEDLE_HANDSHAKE",
    "NEEDLE_BRANCH",
    "NEEDLE_TOOL",
    "NEEDLE_CC",
    "COWORK_FIXTURE_HELLO_XYZ",
    "REMOTE_FIXTURE_TOKEN",
)

SOURCES = ("CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK")

#: Every conversation in the corpus: (golden file stem, conversation uuid).
CONVERSATIONS = (
    ("desktop-long", "0f415a45-9c62-8671-d4ad-53b84acb7e1a"),
    ("desktop-branch", "c8da94ab-641d-bf28-d4a6-cba9bc72468e"),
    ("desktop-tools", "a3c33811-ab27-c195-6938-54121b8673e4"),
    ("claude-code", "435e56ed-50ba-21c0-3495-0e8749b20543"),
    # Issue #90: real Code Mode recordings (an MCP-to-CLI pipe; a failed,
    # a refused, and a subagent-starting run).
    ("claude-code-mode-pipe", "b007d327-80c4-46ea-b737-53d84b30c624"),
    ("claude-code-mode-cases", "2339aab4-744c-4556-95b8-5ee5552c1b85"),
    # Issue #96: a synthetic session with a current-layout and a legacy agent.
    ("claude-subagents", "5a6e0000-0000-4000-8000-000000000096"),
    ("cowork-local", "aaaa1111-2222-3333-4444-555566667777"),
    ("cowork-remote", "session_01FIXTUREREMOTE000000000001"),
)
CONVERSATION_IDS = [stem for stem, _ in CONVERSATIONS]


@dataclass
class Corpus:
    root: Path
    data_dir: Path
    claude_dir: Path
    index: Any


def build_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """Copy the fixtures under ``tmp_path``, point the settings at them, and
    build the search index over them as the process singleton."""
    from backend import config
    from backend import search_index as si
    from backend.cache import clear_cache
    from backend.store import ConversationStore

    root = tmp_path / "corpus"
    data_dir = root / "data"
    claude_dir = root / "claude"
    app_dir = root / "desktop-app"
    # The goldens characterize the three sources from before Codex (N9 fixtures).
    shutil.copytree(FIXTURES_DIR / "desktop", data_dir, ignore=shutil.ignore_patterns("codex", "cowork-remote"))
    shutil.copytree(FIXTURES_DIR / "claude", claude_dir)
    shutil.copytree(FIXTURES_DIR / "cowork", app_dir / "local-agent-mode-sessions")
    shutil.copytree(REMOTE_FIXTURE_DIR, data_dir / "cowork-remote")
    for path in root.rglob("*"):
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(app_dir))
    # The summary cache shares this file; keep it per test, so no row of
    # another test can answer for these paths.
    monkeypatch.setattr(si, "default_index_path", lambda: root / "search-index.sqlite")
    config.get_settings.cache_clear()
    clear_cache()
    si.reset_search_index_for_tests()

    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    return Corpus(root=root, data_dir=data_dir, claude_dir=claude_dir, index=idx)


def _root_forms(root: Path) -> list[str]:
    forms = {str(root), os.path.realpath(root), root.as_posix()}
    # Longest first, so a prefix form never cuts a longer one.
    return sorted(forms, key=len, reverse=True)


def normalize(value: Any, root: Path) -> Any:
    """Replace machine-specific values with fixed tokens (module docstring)."""
    forms = _root_forms(root)

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out = {}
            for k, item in v.items():
                if k in MTIME_KEYS and item is not None:
                    out[k] = "<MTIME>"
                elif k == "indexed_at" and item is not None:
                    out[k] = "<INDEXED_AT>"
                else:
                    out[k] = walk(item)
            return out
        if isinstance(v, list):
            return [walk(item) for item in v]
        if isinstance(v, str):
            return _normalize_text(v, forms)
        return v

    return walk(value)


def _normalize_text(text: str, forms: list[str]) -> str:
    for form in forms:
        if form not in text:
            continue
        head, *rest = text.split(form)
        text = head + "".join("<TMP>" + _posix_tail(part) for part in rest)
    return text


def _posix_tail(part: str) -> str:
    """On Windows, the rest of a path after the root uses ``\\``."""
    if os.sep != "\\":
        return part
    end = len(part)
    for stop in ('"', "'", " ", "\n", ")", "]", ">"):
        i = part.find(stop)
        if i != -1:
            end = min(end, i)
    return part[:end].replace("\\", "/") + part[end:]


def check_golden(name: str, actual: Any) -> None:
    """Compare ``actual`` with ``goldens/<name>``; a JSON value is stored as
    sorted, indented JSON and a ``str`` as is. ``UPDATE_GOLDENS=1`` writes it."""
    path = GOLDENS_DIR / name
    if isinstance(actual, str):
        text = actual
    else:
        text = json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return
    if not path.exists():
        pytest.fail(f"golden {name} is missing. Run with UPDATE_GOLDENS=1 to record it.")
    expected = path.read_bytes().decode("utf-8")
    if text != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=f"golden/{name}",
                tofile="actual",
            )
        )
        pytest.fail(
            f"output differs from golden {name}. If the change is intended, "
            f"rerun with UPDATE_GOLDENS=1 and review the diff.\n{diff}",
            pytrace=False,
        )
