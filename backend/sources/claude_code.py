"""Claude Code sessions: the JSONL files under ``~/.claude/projects``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SourceDescriptor, SyncContext, SyncReport, WatchRoot

if TYPE_CHECKING:
    from backend.store import ConversationStore

DESCRIPTOR = SourceDescriptor(
    id="CLAUDE_CODE",
    label="Claude Code",
    short_label="Code",
    chronological=True,
    has_projects=True,
    has_archive_state=False,
    has_organizations=False,
    refetchable=False,
    messages_change_in_place=False,
)


class ClaudeCodeAdapter:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def descriptor(self) -> SourceDescriptor:
        return DESCRIPTOR

    def watch_roots(self) -> list[WatchRoot]:
        return [WatchRoot(self._store.claude_dir / "projects", ("*.jsonl",), create=True)]

    def discover(self) -> list[Path]:
        # Issue #96: each agent transcript is its own indexed source.
        from backend.cc_subagents import iter_agent_files

        return [*self._store._discover_claude_code(), *iter_agent_files(self._store.claude_dir)]

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        return self._store._get_claude_code_conversations(
            full_content=full_content, include_phantom=include_phantom
        )

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        return self._store._find_claude_code(uuid)

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        from backend.cc_subagents import (
            is_agent_file,
            owner_of,
            read_agent_conversation,
        )

        if is_agent_file(path):
            owner = owner_of(path)
            return read_agent_conversation(*owner, cached=False) if owner else None
        return self._store._load_claude_code_for_index(path)

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        return None

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        # Post-compact duplicate UUIDs poison the parent->children count,
        # and Claude Code has no edit-branch UI.
        return False
