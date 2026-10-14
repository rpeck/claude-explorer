"""Council code-review B6 (2026-05-21) — OpenAPI completeness.

Portfolio-piece V1 invariant: every route in the public API must
expose enough metadata for /docs to render a meaningful entry.
Currently many routes omit ``summary=`` (FastAPI silently falls
back to the docstring first-line, which is often too long or
mid-sentence to look polished).

This module pins:

  * Every route has a non-empty ``summary``.
  * Every route documents at least the 200 (or 201/204) response
    content-type that a reader would expect.

Two endpoint families are intentionally exempt and listed in the
``OPENAPI_EXEMPT_PATHS`` allow-list:

  * SSE streaming endpoints (``/fetch/start``, ``/fetch/refresh``)
    where the response body is ``text/event-stream`` and the
    OpenAPI schema can't fully express it. They are still required
    to have a ``summary``.

The test is designed to fail-build: a new route added without a
``summary=`` MUST trigger a failure in CI.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# Routes that legitimately have no JSON response_model (file
# downloads, SSE streams). They still need a `summary=`.
NON_JSON_RESPONSE_PATHS: set[str] = {
    # SSE streams — content-type is text/event-stream
    "/api/fetch/start",
    "/api/fetch/refresh",
    # File downloads — content-type is set on the Response object
    "/api/conversations/{uuid}/export/markdown",
    "/api/conversations/{uuid}/export/markdown-bundle",
    "/api/conversations/{uuid}/export/pdf",
    "/api/export/all/markdown",
    # Image proxy + image-cache + attachments — binary content
    "/api/{org_id}/files/{file_uuid}/thumbnail",
    "/api/{org_id}/files/{file_uuid}/preview",
    "/api/cc-image",
    "/api/attachments/{conv_uuid}/{file_uuid}/{variant}",
    # SPA-shell catchall + static — return HTML, not JSON
    "/",
    "/{full_path}",
}

# App-level routes that pre-date the router architecture and live in
# backend/main.py rather than under backend/routers/. They DO need
# explicit summary= but their JSON responses are simple {"status":
# "healthy"}-style dicts that don't warrant a Pydantic model.
INFRA_NO_RESPONSE_MODEL: set[str] = {
    "/api/info",
    "/health",
    "/api/health",
}


def _all_route_operations(openapi: dict) -> list[tuple[str, str, dict]]:
    """Yield (path, method, operation) for every route in /openapi.json.

    Excludes the OpenAPI infra routes themselves (``/openapi.json``,
    ``/docs``, ``/redoc``) which FastAPI auto-installs.
    """
    out: list[tuple[str, str, dict]] = []
    for path, methods in openapi.get("paths", {}).items():
        if path in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
            continue
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            out.append((path, method.upper(), op))
    return out


def _looks_auto_derived_from_handler_name(summary: str, operation_id: str) -> bool:
    """FastAPI's default summary is the handler function name, title-cased.

    e.g. ``def export_pdf()`` -> auto-summary ``"Export Pdf"`` and
    operation_id ``"export_pdf_api_conversations__uuid__export_pdf_get"``.

    Auto-derived summaries are the portfolio-piece tell — they read
    as "lowercase_function name with underscores stripped" rather
    than as English sentences.

    Heuristic: the summary is auto-derived if all of these hold:
      * It is short (<= 4 words / 35 chars) — explicit summaries
        for portfolio-piece quality are sentences.
      * Every word in the summary appears at the start of the
        operation_id (which contains the handler name verbatim).

    Falsifiable: a short explicit summary like "List bookmarks" with
    handler `list_bookmarks` SHOULD be flagged as auto-derived. The
    fix is to write a sentence-cased explicit summary.
    """
    if not summary:
        return True
    if len(summary) > 35:
        return False
    words = summary.lower().split()
    if len(words) > 4:
        return False
    op_id_norm = operation_id.lower().replace("_", " ").replace("-", " ")
    # All summary words appear in operation_id prefix → likely auto-derived.
    return all(w in op_id_norm for w in words)


def test_every_route_has_an_explicit_summary(client: TestClient) -> None:
    """Council B6: every route in the public API must declare an
    explicit ``summary=`` (not rely on FastAPI's auto-derived
    title-cased handler-name fallback).

    Pre-council inspection showed routes like
    ``GET /api/conversations/{uuid}/export/pdf`` rendering as
    ``"Export Pdf"`` in /docs — the auto-derived form. Portfolio-
    piece V1 bar: every route gets a sentence-cased explicit summary.

    Bidirectional: see ``test_no_route_has_an_empty_summary`` for
    the negative case (an explicit ``summary=""`` would technically
    pass the existence check but is still a regression).
    """
    openapi = client.get("/openapi.json").json()
    auto_derived: list[str] = []
    missing: list[str] = []
    for path, method, op in _all_route_operations(openapi):
        summary = op.get("summary", "")
        if not summary:
            missing.append(f"{method} {path}")
            continue
        if _looks_auto_derived_from_handler_name(summary, op.get("operationId", "")):
            auto_derived.append(f"{method} {path}: auto-derived as {summary!r}")
    issues = missing + auto_derived
    assert not issues, (
        "Council B6: the following routes lack an explicit `summary=` "
        "(either missing or auto-derived from the handler name):\n\n  - "
        + "\n  - ".join(issues)
        + "\n\nFix: add `summary=\"...\"` to the @router.<verb>(...) "
        "decorator. The summary is what /docs renders as the route's "
        "one-line label; the auto-derived 'Export Pdf' / 'Search Post' "
        "form is the portfolio-piece tell."
    )


def test_no_route_has_an_empty_summary(client: TestClient) -> None:
    """Bidirectional negative: a route with ``summary=\"\"`` would
    pass the existence check above but is a regression."""
    openapi = client.get("/openapi.json").json()
    bad: list[str] = []
    for path, method, op in _all_route_operations(openapi):
        summary = op.get("summary", "")
        if isinstance(summary, str) and summary == "":
            bad.append(f"{method} {path}")
    assert not bad, (
        "Council B6: routes with explicit empty summary= are forbidden:\n  "
        + "\n  ".join(bad)
    )


def test_every_json_route_documents_a_response_schema(client: TestClient) -> None:
    """Every JSON-returning route must declare a ``response_model=`` so
    /docs shows the response shape (not just ``{}``). Non-JSON routes
    (SSE, file downloads, image proxies) are exempt — see
    ``NON_JSON_RESPONSE_PATHS``.

    The pre-council force_refetch_conversation route was the canary
    failure: it returned a bare ``dict`` with no response_model, so
    OpenAPI documented it as ``200 {}``. This test would have caught
    that regression.
    """
    openapi = client.get("/openapi.json").json()
    schemas = openapi.get("components", {}).get("schemas", {})
    missing: list[str] = []
    for path, method, op in _all_route_operations(openapi):
        if path in NON_JSON_RESPONSE_PATHS or path in INFRA_NO_RESPONSE_MODEL:
            continue
        responses = op.get("responses", {})
        # FastAPI uses "200" for GET/PUT/PATCH and "201"/"202"/"204" for
        # POST/DELETE configured with `status_code=` (202: /api/shutdown).
        # Find the success response.
        success_code = None
        for code in ("200", "201", "202", "204"):
            if code in responses:
                success_code = code
                break
        if success_code is None:
            missing.append(f"{method} {path} (no 2xx response documented)")
            continue
        if success_code == "204":
            # 204 No Content has no body by spec; no schema required.
            continue
        content = responses[success_code].get("content", {})
        json_block = content.get("application/json", {})
        schema = json_block.get("schema")
        if not schema:
            missing.append(f"{method} {path} (no application/json schema)")
            continue
        # A `{}` schema (which is what bare-dict returns produce) is
        # NOT polished — must be a $ref or have actual properties.
        if "$ref" not in schema and not schema.get("properties") and not schema.get("type"):
            missing.append(
                f"{method} {path} (empty schema — likely returning bare dict/list "
                f"without response_model=)"
            )
    assert not missing, (
        "Council B6: the following JSON routes do not document a response "
        f"schema (response_model= missing):\n  - "
        + "\n  - ".join(missing)
        + "\n\nFix: add `response_model=<Model>` to the @router.<verb>(...) "
        "decorator. If the route legitimately returns non-JSON (SSE, file "
        "download), add the path to NON_JSON_RESPONSE_PATHS in this test."
    )


def test_force_refetch_response_schema_documented(client: TestClient) -> None:
    """Regression: ``POST /api/fetch/conversation/{uuid}`` previously
    returned a bare ``dict`` with no ``response_model=``. The council
    fix wired ``ForceRefetchResponse`` — this test pins the
    documented shape so a future "convenience" `-> dict` revert
    fails CI."""
    openapi = client.get("/openapi.json").json()
    schemas = openapi.get("components", {}).get("schemas", {})

    path = openapi["paths"].get("/api/fetch/conversation/{uuid}")
    assert path is not None, "POST /api/fetch/conversation/{uuid} missing"
    post_op = path.get("post")
    assert post_op is not None, "POST verb missing on force_refetch route"

    ok = post_op["responses"].get("200", {})
    schema = ok.get("content", {}).get("application/json", {}).get("schema", {})
    # Either inline schema with `uuid`/`status`/`name` or a $ref to
    # `ForceRefetchResponse`.
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        target = schemas.get(ref_name, {})
    else:
        target = schema
    props = target.get("properties", {})
    assert "uuid" in props, f"ForceRefetchResponse missing 'uuid'; got {list(props)}"
    assert "status" in props, f"ForceRefetchResponse missing 'status'; got {list(props)}"
    assert "name" in props, f"ForceRefetchResponse missing 'name'; got {list(props)}"
