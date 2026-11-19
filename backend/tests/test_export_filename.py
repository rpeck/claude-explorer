"""L4: export downloads for titles with non-Latin-1 characters.

An HTTP header must be Latin-1. Before L4 the Markdown, Markdown-bundle, and
PDF routes put the raw title into ``Content-Disposition``, so a title with an
em dash, an emoji, or CJK text made the export fail with HTTP 500.

Each route must send an ASCII ``filename`` fallback plus an RFC 5987
``filename*`` with the UTF-8 name (RFC 6266 §4.3).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

#: (route suffix, file extension). The PDF render is stubbed, see ``seed``.
ROUTES = [
    ("markdown", ".md"),
    ("markdown-bundle", ".zip"),
    ("pdf", ".pdf"),
]

#: Title -> the UTF-8 file stem that the download must carry. The stem is
#: today's name rule (``sanitize_filename``: whitespace becomes ``_``).
NON_ASCII_TITLES = {
    "em-dash": ("Plan — review", "Plan_—_review"),
    "emoji": ("Ship it 🚀", "Ship_it_🚀"),
    "cjk": ("会议记录 notes", "会议记录_notes"),
    # Latin-1 but not ASCII: the fallback must still be ASCII.
    "latin1": ("Café notes", "Café_notes"),
}

UUID = "4c4c4c4c-0000-4000-8000-000000000004"

# RFC 5987 attr-char, plus pct-encoded triplets.
_ATTR_VALUE = re.compile(r"(?:[A-Za-z0-9!#$&+\-.^_`|~]|%[0-9A-Fa-f]{2})+")


def _write_conversation(data_dir: Path, title: str) -> None:
    conv = {
        "uuid": UUID,
        "name": title,
        "summary": "",
        "source": "CLAUDE_AI",
        "model": "claude-sonnet-4-6",
        "created_at": "2026-11-19T12:00:00Z",
        "updated_at": "2026-11-19T12:00:00Z",
        "is_starred": False,
        "current_leaf_message_uuid": "m1",
        "chat_messages": [
            {
                "uuid": "m1",
                "sender": "human",
                "text": "hi",
                "content": [{"type": "text", "text": "hi"}],
                "created_at": "2026-11-19T12:00:00Z",
                "updated_at": "2026-11-19T12:00:00Z",
                "parent_message_uuid": None,
            }
        ],
    }
    (data_dir / f"{UUID}.json").write_text(json.dumps(conv, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def seed(isolated_data_dir, monkeypatch):
    """Return a function that stores one conversation with a given title."""
    # The header, not the renderer, is under test: no WeasyPrint needed.
    monkeypatch.setattr(
        "backend.routers.export.create_pdf",
        lambda conv, include_tools, include_compact=False: b"%PDF-1.4 stub",
    )
    return lambda title: _write_conversation(isolated_data_dir, title)


def _export(client, route: str):
    return client.get(f"/api/conversations/{UUID}/export/{route}")


def _raw_disposition(response) -> bytes:
    values = [v for k, v in response.headers.raw if k.lower() == b"content-disposition"]
    assert len(values) == 1, f"expected one Content-Disposition header, got {values!r}"
    return values[0]


def _params(response) -> tuple[str, dict[str, str]]:
    """Split the header into its disposition type and its parameters.

    A quoted ``filename`` keeps its quotes off. Parameter names are lowercase.
    """
    value = _raw_disposition(response).decode("ascii")
    disposition, _, rest = value.partition(";")
    params: dict[str, str] = {}
    for m in re.finditer(r'\s*([A-Za-z*]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)\s*(?:;|$)', rest):
        name, val = m.group(1).lower(), m.group(2).strip()
        assert name not in params, f"parameter {name} sent twice: {value!r}"
        params[name] = val[1:-1] if val.startswith('"') else val
    return disposition.strip().lower(), params


def _decode_ext_value(ext_value: str) -> str:
    """Decode an RFC 5987 ext-value. It must use UTF-8 and attr-chars only."""
    charset, sep1, rest = ext_value.partition("'")
    _lang, sep2, encoded = rest.partition("'")
    assert sep1 and sep2, f"filename* is not charset'lang'value: {ext_value!r}"
    assert charset.lower() == "utf-8", f"filename* charset is {charset!r}, not UTF-8"
    assert _ATTR_VALUE.fullmatch(encoded), f"filename* value has a non-attr-char: {encoded!r}"
    return unquote(encoded, encoding="utf-8", errors="strict")


_ROUTE_IDS = [r for r, _ in ROUTES]
_TITLE_IDS = list(NON_ASCII_TITLES)


@pytest.mark.parametrize("title_id", _TITLE_IDS)
@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_export_of_non_ascii_title_returns_200(seed, client, route, ext, title_id):
    seed(NON_ASCII_TITLES[title_id][0])
    r = _export(client, route)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("title_id", _TITLE_IDS)
@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_content_disposition_header_is_pure_ascii(seed, client, route, ext, title_id):
    seed(NON_ASCII_TITLES[title_id][0])
    raw = _raw_disposition(_export(client, route))
    assert all(0x20 <= b < 0x7F for b in raw), f"non-ASCII byte in header: {raw!r}"


@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_disposition_type_stays_attachment(seed, client, route, ext):
    seed(NON_ASCII_TITLES["em-dash"][0])
    disposition, _ = _params(_export(client, route))
    assert disposition == "attachment"


@pytest.mark.parametrize("title_id", _TITLE_IDS)
@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_ascii_fallback_filename_is_safe_and_keeps_extension(
    seed, client, route, ext, title_id
):
    seed(NON_ASCII_TITLES[title_id][0])
    _, params = _params(_export(client, route))
    assert "filename" in params, f"no ASCII filename fallback: {params!r}"
    fallback = params["filename"]
    assert fallback.isascii(), fallback
    assert not re.search(r'["\\/\x00-\x1f\x7f]', fallback), fallback
    assert fallback.endswith(ext), (fallback, ext)
    assert len(fallback) > len(ext), f"fallback has no stem: {fallback!r}"


@pytest.mark.parametrize("title_id", _TITLE_IDS)
@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_filename_star_carries_the_utf8_name(seed, client, route, ext, title_id):
    title, stem = NON_ASCII_TITLES[title_id]
    seed(title)
    _, params = _params(_export(client, route))
    assert "filename*" in params, f"no RFC 5987 filename*: {params!r}"
    assert _decode_ext_value(params["filename*"]) == stem + ext


@pytest.mark.parametrize(("route", "ext"), ROUTES, ids=_ROUTE_IDS)
def test_ascii_title_keeps_todays_filename(seed, client, route, ext):
    """No regression for the common case. The plan: each route sends both."""
    seed("Plain title")
    _, params = _params(_export(client, route))
    assert params.get("filename") == "Plain_title" + ext
    assert "filename*" in params, f"no RFC 5987 filename*: {params!r}"
    assert _decode_ext_value(params["filename*"]) == "Plain_title" + ext


def test_markdown_body_keeps_the_utf8_title(seed, client):
    """The fix changes the header only: the document keeps the real title."""
    seed("Plan — review")
    r = _export(client, "markdown")
    assert "Plan — review" in r.content.decode("utf-8")
