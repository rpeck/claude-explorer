"""The source ids. Every other module imports them from here.

The members of ``SourceId`` must equal the registry keys
(``backend.sources.registry``); a test checks it.
"""

from typing import Literal, TypeAlias

SourceId: TypeAlias = Literal["CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK", "CODEX"]
SourceFilter: TypeAlias = Literal["all", "CLAUDE_AI", "CLAUDE_CODE", "CLAUDE_COWORK", "CODEX"]
