"""Tests for ``fetcher.bulk_fetch.load_credentials`` — the legacy
credentials reader used by the CLI's `claude-explorer fetch` entry point.

Pre-fix bug (Hunt #2 — unsafe structured-parse exceptions):
    A user who hand-edits ``~/.claude-explorer/credentials.json`` and breaks
    the JSON (truncated, missing quote, control character) hits an
    unhandled ``json.JSONDecodeError`` when running ``claude-explorer
    fetch``. The CLI surfaces a raw Python stack trace instead of an
    actionable ClickException with recovery copy.

Bidirectional discipline:

  * RED ``test_load_credentials_corrupt_json_raises_clean_clickexception``:
    pre-fix this raises ``JSONDecodeError`` (stack trace to user); post-fix
    it raises ``click.ClickException`` with a recovery message.
  * GREEN pair ``test_load_credentials_valid_json_returns_dict``: a normal
    JSON file still returns its dict unchanged.
  * GREEN pair ``test_load_credentials_missing_file_raises_clean_clickexception``:
    the original missing-file gate still fires (not regressed by the new
    guard).
  * Boundary ``test_load_credentials_empty_file_raises_clean_clickexception``:
    empty file is JSON-invalid; must surface as ClickException not as a
    raw parse exception.
  * Boundary ``test_load_credentials_list_root_returns_unchanged_or_raises_clean``:
    a JSON file whose root is a list (e.g. ``[1,2,3]``) is technically
    parseable. The legacy code returned it unchanged (and downstream
    ``.get()`` failed later); we accept either:
      (a) post-fix returns it unchanged for backward compatibility, OR
      (b) post-fix raises ClickException naming the type mismatch.
    We pin (a) because the legacy contract is "return what the file
    contains" and rewriting that semantics is a contract-shape change
    outside the scope of THIS fix. The canonical reader in
    `fetcher/credentials.py` is the place for strict schema validation.
"""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from fetcher.bulk_fetch import load_credentials


def test_load_credentials_corrupt_json_raises_clean_clickexception(
    tmp_path: Path,
) -> None:
    """The bug case: corrupt JSON must surface as a ClickException with
    a recovery instruction the user can act on — NOT as a raw
    ``json.JSONDecodeError`` propagating up to the CLI top-level.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text('{"truncated json')  # missing closing brace+quote

    with pytest.raises(click.ClickException) as excinfo:
        load_credentials(creds)

    msg = str(excinfo.value.message)
    # Recovery copy: user must know what to do next.
    assert "capture" in msg.lower() or "recapture" in msg.lower() or "re-run" in msg.lower(), (
        f"ClickException must include recovery instruction; got {msg!r}"
    )
    # The path SHOULD be named so the user knows which file to fix.
    assert str(creds) in msg or "credentials" in msg.lower(), (
        f"ClickException must reference the credentials file; got {msg!r}"
    )


def test_load_credentials_valid_json_returns_dict(tmp_path: Path) -> None:
    """Bidirectional GREEN: a well-formed JSON file is returned unchanged.

    Without this companion to the corrupt-JSON test, a trivially-broken
    impl that ALWAYS raised ClickException would pass the RED case.
    """
    creds = tmp_path / "credentials.json"
    payload = {
        "session_key": "sk-test",
        "org_id": "org-uuid",
        "cf_bm": None,
        "cf_clearance": None,
    }
    import json
    creds.write_text(json.dumps(payload))

    loaded = load_credentials(creds)
    assert loaded == payload, (
        f"valid JSON must round-trip unchanged; got {loaded!r}"
    )


def test_load_credentials_missing_file_raises_clean_clickexception(
    tmp_path: Path,
) -> None:
    """The pre-existing missing-file gate must continue to fire.

    Without this, a refactor that adds the corrupt-JSON guard might
    accidentally swallow the FileNotFoundError path and let the
    nonexistent-file case crash later in ``open()``.
    """
    creds = tmp_path / "nonexistent.json"

    with pytest.raises(click.ClickException) as excinfo:
        load_credentials(creds)

    msg = str(excinfo.value.message)
    assert "not found" in msg.lower() or "missing" in msg.lower(), (
        f"missing-file ClickException must reference absence; got {msg!r}"
    )


def test_load_credentials_empty_file_raises_clean_clickexception(
    tmp_path: Path,
) -> None:
    """Boundary case: an empty file is JSON-invalid (json.load on empty
    string raises JSONDecodeError). Same recovery semantics as truncated.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text("")  # empty

    with pytest.raises(click.ClickException) as excinfo:
        load_credentials(creds)

    msg = str(excinfo.value.message)
    assert "capture" in msg.lower() or "recapture" in msg.lower() or "re-run" in msg.lower(), (
        f"empty-file ClickException must include recovery instruction; "
        f"got {msg!r}"
    )


def test_load_credentials_list_root_returns_unchanged_or_raises_clean(
    tmp_path: Path,
) -> None:
    """Boundary case: legacy contract was "return whatever JSON parses
    into". A list root is parseable; the legacy code returned it.

    Post-fix the function is free to either preserve that contract OR
    raise ClickException naming the type mismatch — both are safe for
    the user (no raw stack trace). This test pins "no raw exception"
    without over-pinning which of the two safe options.

    If a future schema-validation refactor wants to reject list-root
    explicitly, update this test to enforce ClickException instead of
    accepting either.
    """
    creds = tmp_path / "credentials.json"
    creds.write_text("[1, 2, 3]")

    # Acceptable: either returns the list (legacy contract) OR raises
    # ClickException. Must NOT raise a raw ValueError/TypeError/AttrError.
    try:
        result = load_credentials(creds)
    except click.ClickException:
        # OK — strict mode declined the list root.
        return
    except Exception as e:  # pragma: no cover — failure mode  # noqa: BLE001 — any other exception fails the test with its type
        pytest.fail(
            f"load_credentials must raise ClickException for non-dict "
            f"root, NOT a raw {type(e).__name__}: {e!r}"
        )
    # If we got here, legacy contract held — non-dict root returned as-is.
    assert result == [1, 2, 3], (
        f"legacy contract: list root returned unchanged; got {result!r}"
    )
