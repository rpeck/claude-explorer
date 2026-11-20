"""Follow the history links of a Codex thread (slice 2, plan §4.4 N10).

A paginated rollout can start with a prefix of another rollout. Its
``session_meta.history_base`` (``HistoryPosition`` in
``codex-rs/protocol/src/protocol.rs`` at ``f2d3086``) names that rollout
and the first ordinal that it does not inherit. A fork and a revert both
write one, and the base can have its own base. Codex walks the links in
``thread-store/src/local/rollout_lineage/walk.rs``; this module does the
same on the archive copies, with a link limit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .rollout import RolloutRecord

MAX_LINKS = 32

#: Returns the records of a rollout (by the rollout id in its file stem), or None.
LoadBase = Callable[[str], Sequence[RolloutRecord] | None]


@dataclass
class Lineage:
    records: list[RolloutRecord]  # inherited records, oldest first, then the thread's own
    reasons: list[str] = field(default_factory=list)  # why a prefix is missing


def resolve_lineage(
    meta: dict[str, Any], own: Sequence[RolloutRecord], load_base: LoadBase | None
) -> Lineage:
    """Put the inherited prefix in front of the thread's own records.

    When a link fails, the records found so far still show; the reason
    marks the session incomplete.
    """
    own = list(own)
    start = meta.get("subagent_history_start_ordinal")
    if _is_int(start):
        # Earlier records of a subagent are the parent context that it inherited.
        own = [r for r in own if r.ordinal is None or r.ordinal >= start]

    segments: list[list[RolloutRecord]] = []
    reasons: list[str] = []
    seen: set[str] = set()
    try:
        position = _position(meta)
        while position is not None:
            if len(segments) == MAX_LINKS:
                reasons.append("history_base too deep")
                break
            rollout_id, end = position
            if rollout_id in seen:
                reasons.append("history_base cycle")
                break
            seen.add(rollout_id)
            base = _load(load_base, rollout_id)
            if base is None:
                reasons.append("history_base missing")
                break
            base_meta = next((r.payload for r in base if r.type == "session_meta"), {})
            position = _position(base_meta)
            # The session_meta of a base with its own base has the ordinal of that cutoff.
            first = position[1] + 1 if position else 1
            segments.append([r for r in base if r.ordinal is not None and first <= r.ordinal < end])
    except ValueError:
        reasons.append("history_base missing")

    records = [r for segment in reversed(segments) for r in segment]
    return Lineage(records + own, reasons)


def parent_uuid(meta: dict[str, Any], thread_id: str) -> str | None:
    """The fork parent, else the subagent parent. A revert of the thread itself has none."""
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    for value in (
        meta.get("forked_from_id"),
        meta.get("parent_thread_id"),
        spawn.get("parent_thread_id") if isinstance(spawn, dict) else None,
    ):
        if isinstance(value, str) and value and value != thread_id:
            return value
    return None


def _position(meta: dict[str, Any]) -> tuple[str, int] | None:
    base = meta.get("history_base")
    if base is None:
        return None
    if (
        isinstance(base, dict)
        and isinstance(base.get("thread_id"), str)
        and base["thread_id"]
        and _is_int(base.get("end_ordinal_exclusive"))
    ):
        return base["thread_id"], base["end_ordinal_exclusive"]
    raise ValueError("damaged history_base")


def _load(load_base: LoadBase | None, rollout_id: str) -> list[RolloutRecord] | None:
    if load_base is None:
        return None
    try:
        base = load_base(rollout_id)
    except OSError:
        return None
    return None if base is None else list(base)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
