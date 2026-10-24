"""JSON responses serialized with orjson.

FastAPI deprecated its ``ORJSONResponse`` (FastAPI 0.13x): it wants a return
type, so that Pydantic serializes the result. The conversation routes return
a cached ``model_dump`` dict on purpose, to skip Pydantic (W3, see
``backend/store.py``), so they keep orjson in this class instead.

``fastapi`` has no upper bound in pyproject.toml, and ``uv tool install``
takes the newest release, so an install would break on the day FastAPI
removes its class (2026-10-23).
"""

from __future__ import annotations

from typing import Any

import orjson
from fastapi.responses import JSONResponse


class OrjsonResponse(JSONResponse):
    """A JSONResponse that serializes with orjson.

    The same options as FastAPI's ``ORJSONResponse``, so the bytes are the
    same.
    """

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY)
