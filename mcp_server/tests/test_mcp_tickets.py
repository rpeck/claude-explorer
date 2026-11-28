"""T1 (issue #93): the MCP ``list_sessions(ticket=...)`` filter.

The contract (orchestrator, 2026-11-27):

* ``ticket`` takes "owner/repo#N", "#N" (the unknown-repo ticket only), or a
  GitHub issue or pull URL. The match is exact; owner/repo ignore case.
* It works with and without ``query``, and with the other filters.
* "KEY-N" (Linear, Jira) when the key is in config.json ``ticket_keys`` or was
  seen in a Linear or Jira URL; Linear and Jira URLs too.
* An invalid value raises a ``ToolError`` with "ticket must be owner/repo#N, #N,
  a configured KEY-N, or a ticket URL".
* Still five tools (pinned by ``test_server_metadata.py``).

The sessions are ``backend/tests/test_tickets.use_ticket_corpus``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests.test_tickets import (
    CODEX,
    DESK,
    JIRA_URL,
    L1,
    L2,
    S1,
    S2,
    S3,
    S5,
    S11,
    use_ticket_corpus,
)

ALPHA_84 = {S1, S2, S5, DESK}


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from mcp_server import server

    with use_ticket_corpus(tmp_path, monkeypatch):
        server.reset_archive_sync_for_tests()
        try:
            yield
        finally:
            server.reset_archive_sync_for_tests()


def _list(**kw: Any) -> dict[str, Any]:
    from mcp_server import server

    fn = server.list_sessions
    return getattr(fn, "fn", fn)(limit=100, **kw)


def _uuids(**kw: Any) -> set[str]:
    return {s["uuid"] for s in _list(**kw)["sessions"]}


def test_ticket_lists_every_session_of_the_ticket(corpus: None) -> None:
    assert _uuids(ticket="fixture-org/alpha#84") == ALPHA_84


def test_total_counts_the_filtered_sessions(corpus: None) -> None:
    assert _list(ticket="fixture-org/alpha#84")["total"] == len(ALPHA_84)


def test_ticket_ignores_the_case_of_owner_and_repo(corpus: None) -> None:
    assert _uuids(ticket="FIXTURE-ORG/Alpha#84") == ALPHA_84


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/fixture-org/alpha/issues/84",
        "https://github.com/fixture-org/alpha/pull/84",
    ],
)
def test_ticket_takes_a_github_url(corpus: None, url: str) -> None:
    assert _uuids(ticket=url) == ALPHA_84


def test_a_bare_number_matches_only_the_unknown_repo_ticket(corpus: None) -> None:
    assert _uuids(ticket="#84") == {S11}


def test_the_same_number_in_another_repo_is_another_ticket(corpus: None) -> None:
    assert _uuids(ticket="fixture-org/beta#84") == {S1, S3, CODEX}


def test_ticket_combines_with_a_query(corpus: None) -> None:
    assert _uuids(ticket="fixture-org/alpha#84", query="laptop") == {S5}


def test_ticket_combines_with_source(corpus: None) -> None:
    assert _uuids(ticket="fixture-org/beta#84", source="CODEX") == {CODEX}


def test_an_unknown_ticket_lists_nothing(corpus: None) -> None:
    out = _list(ticket="fixture-org/alpha#9999")
    assert (out["sessions"], out["total"]) == ([], 0)


@pytest.mark.parametrize(
    "bad", ["alpha-84", "fixture-org/alpha#0", "84", "#x", "XYZ-5", "UTF-8", "ENG-0"]
)
def test_an_invalid_ticket_is_an_error(corpus: None, bad: str) -> None:
    from fastmcp.exceptions import ToolError

    with pytest.raises(
        ToolError, match="ticket must be owner/repo#N, #N, a configured KEY-N, or a ticket URL"
    ):
        _list(ticket=bad)


def test_a_configured_linear_key_lists_its_sessions(corpus: None) -> None:
    assert _uuids(ticket="ENG-123") == {L1}


def test_a_configured_jira_key_lists_its_sessions(corpus: None) -> None:
    assert _uuids(ticket="PROJ-7") == {L1}


def test_a_key_seen_only_in_a_url_is_accepted(corpus: None) -> None:
    assert _uuids(ticket="OPS-9") == {L2}


@pytest.mark.parametrize(
    "url",
    ["https://linear.app/fixture-ws/issue/ENG-123/some-slug", "https://linear.app/fixture-ws/issue/ENG-123"],
)
def test_a_linear_url_is_accepted(corpus: None, url: str) -> None:
    assert _uuids(ticket=url) == {L1}


def test_a_jira_url_is_accepted(corpus: None) -> None:
    assert _uuids(ticket=JIRA_URL) == {L2}
