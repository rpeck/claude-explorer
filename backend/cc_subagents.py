"""Subagent transcripts of a Claude Code session: membership only (issue #96).

The minimal part of the #46 plan (``PLANS/2026.10.23-subagent-viewer-and-dag.md``
on ``feat/subagent-visualization``, §2.1, §4.1, Step 1.1). Two layouts:

* current: ``<project>/<session>/subagents/agent-<id>.jsonl``; the directory
  name is the session's file stem. An optional ``agent-<id>.meta.json``
  next to it holds the ``description``.
* legacy: ``<project>/agent-<id>.jsonl``; the first row that has a
  ``sessionId`` names the session.

Internal runs of Claude Code (``acompact-*``, ``aside_question-*``) are not
agents that the user launched, so discovery leaves them out. No call graph,
no workflows (``subagents/workflows/``), no ghosts: #46 adds those.

Containment (decision R12): every file must resolve to a real path under the
session's project directory, checked before the read. A symlink that points
outside is not listed.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import orjson

logger = logging.getLogger(__name__)

#: Every real agent id matches this (plan §2.1); the routes check it (R17).
AGENT_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_AGENT_ID_RE = re.compile(AGENT_ID_PATTERN)
#: Compaction runs and side questions (plan §2.1).
INTERNAL_RUN_PREFIXES = ("acompact-", "aside_question-")
_SESSION_STEM_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: Legacy ownership by file: ``{path: ((mtime_ns, size), sessionId)}``.
_legacy_owner_cache: dict[str, tuple[tuple[int, int], str | None]] = {}


@dataclass(frozen=True)
class AgentFile:
    """One agent transcript that belongs to a session."""

    agent_id: str
    #: The real path (symlinks resolved).
    path: Path
    layout: Literal["current", "legacy"]
    description: str | None = None


def is_valid_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_RE.match(agent_id))


def _agent_id_of(path: Path) -> str | None:
    """The id in ``agent-<id>.jsonl``, or None for an invalid or internal id."""
    if not (path.name.startswith("agent-") and path.suffix == ".jsonl"):
        return None
    agent_id = path.stem[len("agent-"):]
    if not is_valid_agent_id(agent_id) or agent_id.startswith(INTERNAL_RUN_PREFIXES):
        return None
    return agent_id


def _real_path_in(path: Path, project_dir: Path) -> Path | None:
    """The real path of a regular file under the real ``project_dir``, else None."""
    try:
        real = path.resolve()
        if real.is_relative_to(project_dir.resolve()) and real.is_file():
            return real
    except OSError:
        return None
    logger.debug("cc_subagents: %s is not a file inside its project; skipped", path)
    return None


def _read_description(transcript: Path) -> str | None:
    """``description`` of the sibling ``.meta.json``; a bad file gives None.

    First-object parsing (plan C3): trailing bytes after the object do not
    lose the metadata.
    """
    meta = transcript.with_suffix(".meta.json")
    try:
        text = meta.read_text(encoding="utf-8").lstrip()
        obj, _ = json.JSONDecoder().raw_decode(text)
    except (OSError, ValueError):
        return None
    desc = obj.get("description") if isinstance(obj, dict) else None
    return (desc.strip() or None) if isinstance(desc, str) else None


def _first_session_id(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            for line in f:
                try:
                    entry = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                sid = entry.get("sessionId") if isinstance(entry, dict) else None
                if isinstance(sid, str) and sid:
                    return sid
    except OSError:
        return None
    return None


def legacy_session_id(path: Path) -> str | None:
    """The ``sessionId`` of the first row that has one, cached by mtime and size."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _legacy_owner_cache.get(str(path))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    sid = _first_session_id(path)
    _legacy_owner_cache[str(path)] = (stamp, sid)
    return sid


def _glob(directory: Path, pattern: str) -> list[Path]:
    try:
        return sorted(directory.glob(pattern))
    except OSError:
        return []


def _current(project_dir: Path, session_uuid: str) -> Iterator[AgentFile]:
    for path in _glob(project_dir / session_uuid / "subagents", "agent-*.jsonl"):
        agent_id = _agent_id_of(path)
        real = _real_path_in(path, project_dir) if agent_id else None
        if agent_id and real:
            yield AgentFile(agent_id, real, "current", _read_description(path))


def _legacy(project_dir: Path) -> Iterator[tuple[str, Path]]:
    for path in _glob(project_dir, "agent-*.jsonl"):
        agent_id = _agent_id_of(path)
        real = _real_path_in(path, project_dir) if agent_id else None
        if agent_id and real:
            yield agent_id, real


def discover_agents(session_file: Path) -> list[AgentFile]:
    """The agent transcripts of the session file ``<project>/<uuid>.jsonl``.

    Sorted by ``agent_id``. An id in both layouts is listed once; the
    current layout wins.
    """
    # shortcut: lists the directories on every call (legacy heads are
    # cached by mtime); cache membership in summary_cache (plan R8') in #46.
    project_dir = session_file.parent
    session_uuid = session_file.stem
    out = {a.agent_id: a for a in _current(project_dir, session_uuid)}
    for agent_id, path in _legacy(project_dir):
        if agent_id not in out and legacy_session_id(path) == session_uuid:
            out[agent_id] = AgentFile(agent_id, path, "legacy")
    return sorted(out.values(), key=lambda a: a.agent_id)


def find_agent(session_file: Path, agent_id: str) -> AgentFile | None:
    """The agent of the session, found only through discovery (never a built path)."""
    if not is_valid_agent_id(agent_id):
        return None
    return next((a for a in discover_agents(session_file) if a.agent_id == agent_id), None)


def iter_agent_files(claude_dir: Path) -> Iterator[Path]:
    """Every candidate agent transcript under ``claude_dir/projects``, both layouts.

    Lists the files without reading them; :func:`owner_of` gives the owning
    session when the file is read. The paths are as listed, not resolved,
    so that the search index keys them like the watcher sees them.
    """
    projects = claude_dir / "projects"
    try:
        project_dirs = sorted(p for p in projects.iterdir() if p.is_dir())
    except OSError:
        return
    for project_dir in project_dirs:
        for path in [*_glob(project_dir, "agent-*.jsonl"),
                     *_glob(project_dir, "*/subagents/agent-*.jsonl")]:
            if _agent_id_of(path) and _real_path_in(path, project_dir):
                yield path


def owner_of(path: Path) -> tuple[Path, AgentFile] | None:
    """``(session file, agent)`` of one agent file, or None with no known session."""
    agent_id = _agent_id_of(path)
    if agent_id is None:
        return None
    if path.parent.name == "subagents":
        project_dir = path.parent.parent.parent
        real = _real_path_in(path, project_dir)
        session_file = project_dir / f"{path.parent.parent.name}.jsonl"
        layout: Literal["current", "legacy"] = "current"
        description = _read_description(path)
    else:
        project_dir = path.parent
        real = _real_path_in(path, project_dir)
        sid = legacy_session_id(path) if real else None
        # The id names a file: refuse anything but a plain stem.
        if not sid or not _SESSION_STEM_RE.match(sid):
            return None
        session_file = project_dir / f"{sid}.jsonl"
        layout, description = "legacy", None
    if real is None or not session_file.is_file():
        return None
    return session_file, AgentFile(agent_id, real, layout, description)


def is_agent_file(path: Path) -> bool:
    """The name of a user agent's transcript (not an internal run)."""
    return _agent_id_of(path) is not None


def agent_display_name(agent: AgentFile) -> str:
    return agent.description or f"Agent {agent.agent_id}"


def _session_cwd(session_file: Path) -> str:
    """The ``cwd`` of the session's first turn, as the list reader picks it."""
    first_other = None
    try:
        with open(session_file, "rb") as f:
            for line in f:
                try:
                    entry = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                kind = entry.get("type")
                if kind == "user":
                    return entry.get("cwd") or ""
                if first_other is None and (
                    kind == "assistant"
                    or (kind == "system" and entry.get("subtype") == "local_command")
                ):
                    first_other = entry
    except OSError:
        return ""
    return (first_other or {}).get("cwd") or ""


def as_agent_conversation(
    data: dict[str, Any], session_file: Path, agent: AgentFile
) -> dict[str, Any]:
    """A reader dict of an agent transcript, in the session's identity (plan §4.5).

    A shallow copy: ``data`` can be the shared FileCache entry.
    """
    return {
        **data,
        "uuid": session_file.stem,
        "agent_id": agent.agent_id,
        "agent_description": agent.description,
        "name": agent_display_name(agent),
        "project_path": _session_cwd(session_file) or data.get("project_path"),
    }


def read_agent_conversation(
    session_file: Path, agent: AgentFile, *, cached: bool = True
) -> dict[str, Any] | None:
    """The agent transcript, read like a session (Code Mode runs fold the same way)."""
    from .claude_code_reader import (
        _load_conversation_cached,
        read_claude_code_conversation,
    )

    read = _load_conversation_cached if cached else read_claude_code_conversation
    data = read(agent.path)
    return as_agent_conversation(data, session_file, agent) if data else None


def subagents_found(session_file: Path) -> list[dict[str, Any]]:
    """The ``subagents_found`` of a session detail (contract item 3)."""
    return [
        {"agent_id": a.agent_id, "agent_description": a.description}
        for a in discover_agents(session_file)
    ]
