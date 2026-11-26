"""The data flow inside a Code Mode run (issue #90), for Claude Code and Codex.

The usual reason to use Code Mode: one tool's output goes into another tool,
and only a small result goes back to the model. Neither transcript records
the flow, but each records every input and output, so the flow shows as
text: an earlier call's output inside a later call's input, or inside what
the run returned.

* ``inputs_from`` on a call: the indexes of the earlier calls whose output
  is in this call's input.
* ``returned_from`` on the run: the indexes of the calls whose output is in
  what the run returned.

Only outputs of ``MIN_CHARS`` or more count, so a short output such as
"16" or "src" is never taken for a flow.
"""

from __future__ import annotations

from typing import Any

MIN_CHARS = 8


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def add_data_flow(run: dict[str, Any]) -> None:
    """Set ``inputs_from`` on each inner call and ``returned_from`` on the run."""
    calls = run.get("inner_calls") or []
    outputs = [(c.get("result") or "").strip() for c in calls]
    for i, call in enumerate(calls):
        text = "\n".join(_strings(call.get("input")))
        call["inputs_from"] = [j for j in range(i) if len(outputs[j]) >= MIN_CHARS and outputs[j] in text]
    returned = run.get("result") or ""
    run["returned_from"] = [j for j, out in enumerate(outputs) if len(out) >= MIN_CHARS and out in returned]
