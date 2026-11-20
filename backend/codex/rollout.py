"""Read one Codex rollout (``rollout-<timestamp>-<id>.jsonl[.zst]``).

A rollout is one JSON ``RolloutLine`` per line: ``{"timestamp", "type",
"payload"}``. The reader opens the file read-only and keeps no handle after
it returns (plan §8, Windows renames).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from .zstd import open_decompressed


@dataclass
class RolloutRecord:
    line_no: int  # 1-based line in the decoded file
    timestamp: str | None
    type: str
    payload: dict[str, Any]
    ordinal: int | None = None  # paginated history only; history_base cutoffs count it


@dataclass
class RolloutRead:
    records: list[RolloutRecord] = field(default_factory=list)
    damaged_lines: int = 0


def read_rollout(path: Path) -> RolloutRead:
    """Parse a plain or ``.zst`` rollout. Damaged lines are counted, not returned."""
    result = RolloutRead()
    if path.suffix == ".zst":
        with open_decompressed(path) as stream:
            _parse(stream, result)
    else:
        with open(path, "rb") as stream:
            _parse(stream, result)
    return result


def _parse(stream: Any, result: RolloutRead) -> None:
    for line_no, line in enumerate(stream, start=1):
        if not line.endswith(b"\n"):
            break  # Codex can still be writing the last line (plan §4.2).
        if not line.strip():
            continue
        try:
            obj = orjson.loads(line)
        except orjson.JSONDecodeError:
            result.damaged_lines += 1
            continue
        if (
            not isinstance(obj, dict)
            or not isinstance(obj.get("type"), str)
            or not isinstance(obj.get("payload", {}), dict)
        ):
            result.damaged_lines += 1
            continue
        stamp = obj.get("timestamp")
        ordinal = obj.get("ordinal")
        result.records.append(
            RolloutRecord(
                line_no=line_no,
                timestamp=stamp if isinstance(stamp, str) else None,
                type=obj["type"],
                payload=obj.get("payload") or {},
                ordinal=ordinal if isinstance(ordinal, int) and not isinstance(ordinal, bool) else None,
            )
        )
