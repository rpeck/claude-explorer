"""Cowork: local sessions of Claude Desktop, and remote tasks that the fetcher saves."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import SourceDescriptor, SyncContext, SyncReport, WatchRoot

if TYPE_CHECKING:
    from backend.store import ConversationStore

DESCRIPTOR = SourceDescriptor(
    id="CLAUDE_COWORK",
    label="Claude Cowork",
    short_label="Cowork",
    chronological=True,
    has_projects=False,
    has_archive_state=True,
    # Remote tasks carry the workspace of their fetch.
    has_organizations=True,
    refetchable=False,
    messages_change_in_place=False,
)


class CoworkAdapter:
    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def descriptor(self) -> SourceDescriptor:
        return DESCRIPTOR

    def watch_roots(self) -> list[WatchRoot]:
        # The local root only: the remote archive changes through the fetch.
        # The sidecar (``local_<id>.json``, a sibling of the session folder)
        # holds the title and the archive state.
        return [WatchRoot(self._store.cowork_root, ("audit.jsonl", "local_*.json"), create=True)]

    def discover(self) -> list[Path]:
        return self._store._discover_cowork()

    def list_rows(self, *, full_content: bool, include_phantom: bool) -> list[dict[str, Any]]:
        return self._store._cowork_rows(full_content)

    def find(self, uuid: str) -> tuple[dict[str, Any], Path] | None:
        return self._store._find_cowork(uuid)

    def load_for_index(self, path: Path) -> dict[str, Any] | None:
        return self._store._load_cowork_for_index(path)

    def archive_sync(self, ctx: SyncContext) -> SyncReport | None:
        """Copy the local sessions into ``machines/<id>/cowork``; remote tasks are
        already in the archive (``cowork-remote``)."""
        from backend.archive_sync import sync_machine_tree
        from backend.cowork_reader import iter_cowork_session_dirs

        root = self._store.cowork_root

        def streams() -> list[Path]:
            audits = (d / "audit.jsonl" for d in iter_cowork_session_dirs(root))
            return [a for a in audits if a.is_file()]

        def side_files(files: list[Path]) -> list[Path]:
            sidecars = (a.parent.with_suffix(".json") for a in files)
            return [s for s in sidecars if s.is_file()]

        return sync_machine_tree(
            ctx, data_dir=self._store.data_dir, part="cowork", label="Cowork",
            live_root=root, mirror=None, streams=streams, side_files=side_files,
        )

    def branch_flag(self, data: dict[str, Any], chat_messages: list[dict[str, Any]]) -> bool:
        # A Cowork session is one chronological stream: its messages have no
        # parent links, so a parent->children count reads every row as branched.
        return False
