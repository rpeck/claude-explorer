"""Pytest fixtures for the MCP server tests.

Pytest's conftest discovery only walks up from each test file, so the
fixtures defined under ``backend/tests/conftest.py`` are not visible
here. The minimal ones the MCP tests need (``isolated_data_dir``,
search-index isolation, data-dir migration suppression) are mirrored
into this file. When the backend versions evolve, this file should
follow.

Fixtures defined here:

* :func:`isolated_data_dir` — env-var-driven, ``lru_cache``-aware
  data-dir + claude-dir isolation. Mirrors the backend version.
* :func:`isolate_search_index_singleton` (autouse) — repoints
  ``backend.search_index.default_index_path`` to a per-session tmp file
  so MCP search tests never scribble against ``~/.claude-explorer/``.
* :func:`disable_data_dir_migration_in_tests` (autouse) — env-var that
  prevents accidental ``~/.claude-exporter/`` -> ``~/.claude-explorer/``
  renames during MCP test runs.
* :func:`reset_mcp_singletons` (autouse) — clears the module-level
  ``_settings`` and ``_store`` singletons in ``mcp_server.server`` so
  a prior test's settings (pointing at a now-deleted tmp dir) does not
  leak into the next test.
* :class:`McpFixture` — small helper that writes minimal Claude Desktop
  JSON conversations (and, on demand, Claude Code JSONL sessions) into
  the isolated data and claude dirs.
* :func:`mcp_data` — yields a configured :class:`McpFixture` ready to
  call. Depends on ``isolated_data_dir``.
"""

from __future__ import annotations

import json
import re
import uuid as _uuid_mod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Matches the production filter in ``backend/store.py:28``; conversation
# files must be ``<36-char-UUID>.json`` or they're silently skipped.
_UUID_RE = re.compile(r"^[0-9a-f-]{36}$", re.IGNORECASE)
_TEST_NS = _uuid_mod.UUID("11111111-2222-3333-4444-555555555555")


# ---------------------------------------------------------------------------
# Backend-fixture mirrors (kept in lockstep with backend/tests/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Per-test, env-var-driven, ``lru_cache``-aware data-dir isolation.

    Creates ``<tmp_path>/data`` (a SUBDIRECTORY of ``tmp_path``, NOT
    ``tmp_path`` itself) and points ``CLAUDE_EXPLORER_DATA_DIR`` at it.
    The SQLite outline cache opened by ``mcp_server`` lives at
    ``data_dir.parent / "cache.db"`` (i.e. ``<tmp_path>/cache.db``),
    so ``data_dir`` MUST be a subdirectory of a writable root.

    Also pins ``CLAUDE_DIR`` to ``<tmp_path>/claude`` so the MCP
    server's Claude-Code reader walks the synthetic fixture
    directory and not the developer's real ``~/.claude``.

    The ``backend.config.get_settings`` lru_cache is cleared on
    teardown to ensure no cached ``Settings`` leaks across tests.

    Yields the data-dir path.
    """

    from backend import config

    data_dir = tmp_path / "data"
    claude_dir = tmp_path / "claude"
    desktop_app_dir = tmp_path / "desktop_app"
    data_dir.mkdir()
    claude_dir.mkdir()
    # Create the Cowork sessions root empty so the reader finds nothing.
    (desktop_app_dir / "local-agent-mode-sessions").mkdir(parents=True)

    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(claude_dir))
    # Isolate the Cowork reader too — it walks
    # ``CLAUDE_DESKTOP_APP_DIR/local-agent-mode-sessions``. Without this,
    # any "all" or CLAUDE_COWORK query leaks the developer's real Cowork
    # sessions into the result (and makes those tests non-deterministic).
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(desktop_app_dir))

    config.get_settings.cache_clear()
    try:
        yield data_dir
    finally:
        config.get_settings.cache_clear()


@pytest.fixture(autouse=True)
def disable_data_dir_migration_in_tests(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Prevent the MCP test runs from ever moving the developer's real
    ``~/.claude-exporter/`` to ``~/.claude-explorer/``. The backend
    migration only runs from the FastAPI lifespan handler, so we're
    defense-in-depth here, but the env var is cheap and the failure
    mode (a real data move during a test run) is bad.
    """

    monkeypatch.setenv("CLAUDE_EXPLORER_SKIP_DATA_DIR_MIGRATION", "1")
    yield


@pytest.fixture(autouse=True)
def isolate_search_index_singleton(tmp_path_factory, monkeypatch) -> Iterator[None]:
    """Repoint ``backend.search_index.default_index_path`` to a
    per-session tmp file so ``list_sessions(query=...)`` (which routes
    through ``backend.search.search_conversations`` -> FTS5) never
    writes to the developer's real
    ``~/.claude-explorer/search-index.sqlite``.

    Same strategy as ``backend/tests/conftest.py``.
    """

    from backend import search_index as si

    safe_path = tmp_path_factory.mktemp("mcp_search_index_root") / "search-index.sqlite"
    monkeypatch.setattr(si, "default_index_path", lambda: safe_path)

    si.reset_search_index_for_tests()
    try:
        yield
    finally:
        si.reset_search_index_for_tests()


# ---------------------------------------------------------------------------
# Module-singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_mcp_singletons() -> Iterator[None]:
    """Clear ``mcp_server.server`` module singletons between tests.

    ``mcp_server.server`` caches the settings and store at module level
    (``_settings`` and ``_store``) on first tool call. Without this
    reset, the first test in a suite captures the dev's real
    ``~/.claude-explorer`` settings (the cache is populated before our
    ``isolated_data_dir`` fixture can clear it), and every subsequent
    test under ``isolated_data_dir`` silently reads from the dev's
    machine — exactly the data-leak failure mode the isolation fixture
    is supposed to prevent.

    Reset before and after so a crashing test still leaves a clean
    slate for the next one.
    """

    from mcp_server import server as mcp_mod

    mcp_mod._settings = None
    mcp_mod._store = None
    try:
        yield
    finally:
        mcp_mod._settings = None
        mcp_mod._store = None


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


@dataclass
class McpFixture:
    """In-process helper for writing MCP-test fixture conversations.

    The ``data_dir`` matches the resolved ``Settings.data_dir`` (i.e.
    ``<isolated>/data`` under ``CLAUDE_EXPLORER_DATA_DIR``) and the
    ``claude_dir`` matches the ``CLAUDE_DIR`` env var pinned by
    ``isolated_data_dir``. The helper writes files under those paths.

    Short-name ↔ UUID mapping: tests pass friendly names like ``"u-1"``
    to :meth:`add_desktop_session` and :meth:`add_cc_session`; the
    fixture translates them via a deterministic ``uuid5`` so the
    filename matches the ``^[0-9a-f-]{36}\\.json$`` filter that
    ``backend/store.py`` applies to conversation files. Tests look up
    the resolved UUID via :meth:`uuid_for` when asserting on result
    fields.
    """

    data_dir: Path
    claude_dir: Path
    _uuid_map: dict[str, str] = field(default_factory=dict)
    _msg_counter: dict[str, int] = field(default_factory=dict)

    def _resolve_uuid(self, name_or_uuid: str) -> str:
        """Map a short test name to a stable UUID; pass UUIDs through."""
        if _UUID_RE.match(name_or_uuid):
            return name_or_uuid
        if name_or_uuid in self._uuid_map:
            return self._uuid_map[name_or_uuid]
        u = str(_uuid_mod.uuid5(_TEST_NS, name_or_uuid))
        self._uuid_map[name_or_uuid] = u
        return u

    def uuid_for(self, name_or_uuid: str) -> str:
        """Return the resolved UUID for a previously-used short name.

        Stable across calls within a single test. Idempotent on real
        UUIDs (returns them unchanged).
        """
        return self._resolve_uuid(name_or_uuid)

    # -----------------------------------------------------------------
    # Claude Desktop (.json under data_dir)
    # -----------------------------------------------------------------

    def add_desktop_session(
        self,
        uuid_or_name: str,
        name: str = "Test Conversation",
        messages: list[dict[str, Any]] | None = None,
        model: str = "claude-sonnet-4-6",
        created_at: str = "2026-04-01T10:00:00Z",
        updated_at: str = "2026-04-01T11:00:00Z",
        is_starred: bool = False,
    ) -> str:
        """Write a minimal Claude Desktop conversation JSON.

        ``uuid_or_name`` may be a real 36-char UUID or a short friendly
        name (e.g. ``"u-1"``); short names are translated to a stable
        ``uuid5`` so the file's basename matches the production
        ``_UUID_FILENAME_RE`` filter.

        ``messages`` is the raw ``chat_messages`` list (i.e. the disk
        shape, not the API shape). If omitted, a simple two-message
        exchange is used. The trailing message's UUID becomes
        ``current_leaf_message_uuid``.

        Returns the resolved UUID (matching what the loaded
        ``ConversationSummary.uuid`` field will be).
        """

        if messages is None:
            messages = self._default_desktop_messages()

        leaf = messages[-1]["uuid"] if messages else ""
        real_uuid = self._resolve_uuid(uuid_or_name)

        blob = {
            "uuid": real_uuid,
            "name": name,
            "summary": "",
            "model": model,
            "created_at": created_at,
            "updated_at": updated_at,
            "is_starred": is_starred,
            "current_leaf_message_uuid": leaf,
            "chat_messages": messages,
        }

        path = self.data_dir / f"{real_uuid}.json"
        path.write_text(json.dumps(blob, indent=2))
        return real_uuid

    @staticmethod
    def _default_desktop_messages() -> list[dict[str, Any]]:
        return [
            {
                "uuid": "msg-h-1",
                "sender": "human",
                "text": "Hello, Claude. Tell me about FTS5 in SQLite.",
                "content": [
                    {"type": "text", "text": "Hello, Claude. Tell me about FTS5 in SQLite."}
                ],
                "created_at": "2026-04-01T10:00:00Z",
                "updated_at": "2026-04-01T10:00:00Z",
                "parent_message_uuid": None,
            },
            {
                "uuid": "msg-a-1",
                "sender": "assistant",
                "text": "FTS5 is SQLite's full-text-search virtual-table module.",
                "content": [
                    {"type": "text", "text": "FTS5 is SQLite's full-text-search virtual-table module."}
                ],
                "created_at": "2026-04-01T10:00:30Z",
                "updated_at": "2026-04-01T10:00:30Z",
                "parent_message_uuid": "msg-h-1",
            },
        ]

    @staticmethod
    def make_tool_message(
        uuid: str,
        sender: str,
        text_before: str,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: str,
        parent_uuid: str | None = None,
        created_at: str = "2026-04-01T10:05:00Z",
    ) -> dict[str, Any]:
        """Build a message containing a tool_use + tool_result pair.

        Used by ``test_get_messages`` to exercise the verbosity flags
        on ``get_messages`` and the placeholder filtering on
        ``get_session_outline``.
        """

        return {
            "uuid": uuid,
            "sender": sender,
            "text": text_before,
            "content": [
                {"type": "text", "text": text_before},
                {"type": "tool_use", "name": tool_name, "input": tool_input},
                {
                    "type": "tool_result",
                    "content": [{"type": "text", "text": tool_result}],
                },
            ],
            "created_at": created_at,
            "updated_at": created_at,
            "parent_message_uuid": parent_uuid,
        }

    # -----------------------------------------------------------------
    # Claude Code (.jsonl under claude_dir/projects/<encoded-cwd>/)
    # -----------------------------------------------------------------

    def add_cc_session(
        self,
        session_uuid_or_name: str,
        cwd: str = "/fixture/project",
        user_text: str = "Hi from CC fixture.",
        assistant_text: str = "Hello back from CC fixture.",
        timestamp: str = "2026-04-01T12:00:00Z",
    ) -> str:
        """Write a minimal Claude Code JSONL session under the pinned
        ``claude_dir``. Two messages: one user, one assistant. Uses
        the same shape as ``backend/tests/fixtures/claude/projects/...``.

        ``session_uuid_or_name`` may be a real UUID or a short name;
        short names are translated through :meth:`_resolve_uuid` for
        consistency with :meth:`add_desktop_session`.

        Returns the resolved session UUID.
        """

        session_uuid = self._resolve_uuid(session_uuid_or_name)

        encoded = cwd.replace("/", "-")
        proj_dir = self.claude_dir / "projects" / encoded
        proj_dir.mkdir(parents=True, exist_ok=True)

        # Per-line UUIDs derived from the session UUID + an ordinal so
        # multiple CC sessions in one test don't collide.
        user_uuid = str(_uuid_mod.uuid5(_TEST_NS, f"{session_uuid}-user"))
        assistant_uuid = str(_uuid_mod.uuid5(_TEST_NS, f"{session_uuid}-asst"))

        lines: list[dict[str, Any]] = [
            {
                "cwd": cwd,
                "entrypoint": "cli",
                "gitBranch": "main",
                "isSidechain": False,
                "message": {"content": user_text, "role": "user"},
                "parentUuid": None,
                "sessionId": session_uuid,
                "timestamp": timestamp,
                "type": "user",
                "userType": "external",
                "uuid": user_uuid,
                "version": "2.0.0",
            },
            {
                "cwd": cwd,
                "entrypoint": "cli",
                "gitBranch": "main",
                "isSidechain": False,
                "message": {
                    "content": [{"text": assistant_text, "type": "text"}],
                    "id": "asst-msg-id",
                    "model": "claude-sonnet-4-6",
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "type": "message",
                    "usage": {
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "input_tokens": 10,
                        "output_tokens": 10,
                    },
                },
                "parentUuid": user_uuid,
                "sessionId": session_uuid,
                "timestamp": timestamp,
                "type": "assistant",
                "userType": "external",
                "uuid": assistant_uuid,
                "version": "2.0.0",
            },
        ]

        path = proj_dir / f"{session_uuid}.jsonl"
        with path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return session_uuid


@pytest.fixture
def mcp_data(isolated_data_dir: Path) -> McpFixture:
    """Return a :class:`McpFixture` rooted at the isolated data + claude dirs.

    Depends on the ``isolated_data_dir`` fixture from
    ``backend/tests/conftest.py``, which sets up
    ``CLAUDE_EXPLORER_DATA_DIR`` and ``CLAUDE_DIR`` env vars and clears
    the ``backend.config.get_settings`` lru_cache before and after.
    The MCP server reads those env vars when its singleton settings
    are first loaded — and our ``reset_mcp_singletons`` autouse
    fixture ensures that happens after the env vars are set, not
    before.
    """

    claude_dir = isolated_data_dir.parent / "claude"
    return McpFixture(data_dir=isolated_data_dir, claude_dir=claude_dir)
