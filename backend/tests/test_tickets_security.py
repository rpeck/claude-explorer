"""T1 security round 2 (issue #93): the reference scan, the external links, PR links.

The contract: _orchestration/T1-SECURITY.md (orchestrator, 2026-11-27).

* T1 (the parts outside the web page): every example in UX.md puts {title} and
  {cwd} outside quotes.
* T2: ``_matches`` is linear; ``MAX_REFS_PER_MESSAGE = 200`` and
  ``MAX_TICKETS_PER_SESSION = 2000`` in ``backend/tickets.py``; extra references
  are dropped in text order. 20,000 references in one message scan in < 1 s.
* T3: "Open in Jira/Linear" uses config.json ``ticket_hosts`` when set; else
  the host (Jira) or workspace (Linear) that the most references name, a tie
  to the earliest. The system comes from the configured prefixes first.
* T4: ``tickets.pr_rows`` keeps only https://github.com/ PR URLs.

Every value here is synthetic.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from backend.tests import test_tickets as tt

UX_MD = Path(__file__).resolve().parents[2] / "UX.md"

J1 = "b9300000-0000-4000-8000-000000000001"  # earliest: one evil PROJ-7 URL, the first PROJ-8 URL
J2 = "b9300000-0000-4000-8000-000000000002"  # two acme PROJ-7 URLs, the second PROJ-8 URL
L1 = "b9300000-0000-4000-8000-000000000003"  # one evil OPS-9 URL
L2 = "b9300000-0000-4000-8000-000000000004"  # two acme OPS-9 URLs
K1 = "b9300000-0000-4000-8000-000000000005"  # PROJ-5 (no URL); PROJ-12 only in a Linear URL
P1 = "b9300000-0000-4000-8000-000000000006"  # pr-link records with bad URLs
tt.TITLES.update({
    J1: "Jira, the first session",
    J2: "Jira, the second session",
    L1: "Linear, the first session",
    L2: "Linear, the second session",
    K1: "Keys without a URL",
    P1: "PR links with bad URLs",
})
KEYS = {"jira": ["PROJ"]}
HOSTS = {"jira": "cfg.atlassian.net", "linear": "cfgws"}
GOOD_PR = "https://github.com/fixture-org/alpha/pull/7"
BAD_PRS = (
    "javascript:alert(1)",
    "https://evil.example/fixture-org/alpha/pull/6",
    "https://github.com.evil.example/fixture-org/alpha/pull/8",
    "http://github.com/fixture-org/alpha/pull/9",
    "https://github.com/fixture-org/alpha/issues/10",
    "https://github.com/fixture-org/alpha/pull/11x",
)


def build_sessions() -> list[tt.Session]:
    j1 = tt.Session(J1, "/home/fixture/sec", offset=1)
    j1.prompt(
        "Look at https://first.atlassian.net/browse/PROJ-8 and https://evil.atlassian.net/browse/PROJ-7"
    )
    j2 = tt.Session(J2, "/home/fixture/sec", offset=2)
    j2.prompt("https://acme.atlassian.net/browse/PROJ-7 first")
    j2.text("https://acme.atlassian.net/browse/PROJ-7 again")
    j2.text("https://second.atlassian.net/browse/PROJ-8")
    l1 = tt.Session(L1, "/home/fixture/sec", offset=3)
    l1.prompt("https://linear.app/evil/issue/OPS-9")
    l2 = tt.Session(L2, "/home/fixture/sec", offset=4)
    l2.prompt("https://linear.app/acme/issue/OPS-9/some-slug")
    l2.text("https://linear.app/acme/issue/OPS-9")
    k1 = tt.Session(K1, "/home/fixture/sec", offset=5)
    k1.prompt("Work on PROJ-5 and https://linear.app/acme/issue/PROJ-12")
    p1 = tt.Session(P1, "/home/fixture/prs", offset=6)
    p1.prompt("Fix fixture-org/alpha#84.")
    for n, url in enumerate(BAD_PRS, start=20):
        p1.pr_link(n, "fixture-org/alpha", url)
    p1.pr_link(7, "fixture-org/alpha", GOOD_PR)
    return [j1, j2, l1, l2, k1, p1]


@contextmanager
def use_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]
) -> Iterator[None]:
    from backend import search_index as si
    from backend.store import ConversationStore
    from backend.tests._platform_home import patch_home

    root = tmp_path / "sec"
    home = patch_home(monkeypatch, root / "home")
    cfg = home / ".claude-explorer" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps(config), encoding="utf-8")
    projects = root / "claude" / "projects"
    for s in build_sessions():
        s.write(projects)
    data_dir = root / "explorer" / "conversations"
    data_dir.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_EXPLORER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("CLAUDE_DIR", str(root / "claude"))
    monkeypatch.setenv("CLAUDE_DESKTOP_APP_DIR", str(root / "desktop-app-missing"))
    monkeypatch.setattr(si, "default_index_path", lambda: root / "explorer" / "search-index.sqlite")
    tt._reset_caches()
    idx = si.SearchIndex(root / "index.sqlite")
    si.build_full_index(ConversationStore(), index=idx)
    assert idx.is_ready()
    monkeypatch.setattr(si, "_search_index", idx)
    try:
        yield
    finally:
        idx.close()
        tt._reset_caches()


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with use_corpus(tmp_path, monkeypatch, {"ticket_keys": KEYS}):
        yield


@pytest.fixture
def corpus_hosts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with use_corpus(tmp_path, monkeypatch, {"ticket_keys": KEYS, "ticket_hosts": HOSTS}):
        yield


def key_ticket(key: str) -> dict[str, Any]:
    r = tt._client().get(f"/api/tickets/key/{key}")
    assert r.status_code == 200, r.text
    return r.json()


def _conv(*texts: str) -> dict[str, Any]:
    return {
        "name": "",
        "chat_messages": [
            {"uuid": f"m{i}", "sender": "human", "content": [{"type": "text", "text": t}]}
            for i, t in enumerate(texts, start=1)
        ],
    }


def _message_keys(rows: list[tuple[Any, ...]]) -> list[tuple[str | None, int, str | None]]:
    """(repo, number, message_uuid) of the message rows, in row order."""
    return [(r[1], r[3], r[4]) for r in rows if r[5] == "message"]


# ---------------------------------------------------------------------------
# T1: the examples in UX.md
# ---------------------------------------------------------------------------


def _placeholder_in_quotes(s: str) -> bool:
    """True when {title} or {cwd} is between POSIX quotes in a template."""
    quote: str | None = None
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and quote != "'":
            i += 2
            continue
        if c in "'\"":
            quote = c if quote is None else (None if quote == c else quote)
        elif quote is not None and (s.startswith("{title}", i) or s.startswith("{cwd}", i)):
            return True
        i += 1
    return False


def test_the_quote_detector_works() -> None:
    assert _placeholder_in_quotes('claude --bg "Fix {repo}#{issue}: {title}"')
    assert _placeholder_in_quotes("emacsclient -e '(start \"{cwd}\")'")
    assert not _placeholder_in_quotes('claude --bg "Fix {repo}#{issue}: "{title}')
    assert not _placeholder_in_quotes("cd {cwd} && claude {title}")


def test_every_ux_example_puts_title_and_cwd_outside_quotes() -> None:
    text = UX_MD.read_text(encoding="utf-8")
    # Inline code spans, and every line of a fenced block.
    spans = re.findall(r"\x60([^\x60\n]+)\x60", text)
    spans += [ln for block in re.findall(r"\x60{3}[^\n]*\n(.*?)\x60{3}", text, re.DOTALL) for ln in block.splitlines()]
    bad = [s for s in spans if ("{title}" in s or "{cwd}" in s) and _placeholder_in_quotes(s)]
    assert bad == []


# ---------------------------------------------------------------------------
# T2: the scan is linear and bounded
# ---------------------------------------------------------------------------


def test_the_caps_have_their_values() -> None:
    from backend import tickets

    assert (tickets.MAX_REFS_PER_MESSAGE, tickets.MAX_TICKETS_PER_SESSION) == (200, 2000)


def test_twenty_thousand_refs_in_one_message_scan_in_under_a_second() -> None:
    from backend import tickets

    text = " ".join(f"a/b#{i}" for i in range(1, 20_001))
    assert len(text) > 180_000
    start = time.perf_counter()
    tickets._matches(text)
    assert time.perf_counter() - start < 1.0


def test_twenty_thousand_bare_and_url_refs_scan_in_under_a_second() -> None:
    from backend import tickets

    text = " ".join(
        f"#{i} https://github.com/a/b/issues/{i}" if i % 2 else f"x/y#{i}" for i in range(1, 20_001)
    )
    start = time.perf_counter()
    tickets._matches(text, {"linear": ["ENG"]})
    assert time.perf_counter() - start < 1.0


def test_indexing_a_huge_message_is_fast() -> None:
    from backend import tickets

    conv = _conv(" ".join(f"a/b#{i}" for i in range(1, 20_001)))
    start = time.perf_counter()
    tickets.index_rows(conv)
    assert time.perf_counter() - start < 1.0


def test_a_url_wins_over_the_full_and_bare_forms_inside_it() -> None:
    from backend import tickets

    found = tickets._matches(
        "https://github.com/o/r/issues/5 o/r#6 #7 https://linear.app/ws/issue/ENG-8",
        {"linear": ["ENG"]},
    )
    assert [(m[0], m[1], m[2]) for m in found] == [
        ("github", "o/r", 5),
        ("github", "o/r", 6),
        ("github", None, 7),
        ("linear", "ENG", 8),
    ]


def test_refs_per_message_are_capped_in_text_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend import tickets

    monkeypatch.setattr(tickets, "MAX_REFS_PER_MESSAGE", 3)
    rows, _prs, _repo = tickets.index_rows(_conv("o/r#1 o/r#2 o/r#3 o/r#4 o/r#5"))
    assert _message_keys(rows) == [("o/r", 1, "m1"), ("o/r", 2, "m1"), ("o/r", 3, "m1")]


def test_the_message_cap_counts_each_message_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend import tickets

    monkeypatch.setattr(tickets, "MAX_REFS_PER_MESSAGE", 2)
    rows, _prs, _repo = tickets.index_rows(_conv("o/r#1 o/r#2 o/r#3", "o/r#4 o/r#5 o/r#6"))
    assert _message_keys(rows) == [
        ("o/r", 1, "m1"),
        ("o/r", 2, "m1"),
        ("o/r", 4, "m2"),
        ("o/r", 5, "m2"),
    ]


def test_tickets_per_session_are_capped_in_text_order(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend import tickets

    monkeypatch.setattr(tickets, "MAX_TICKETS_PER_SESSION", 4)
    rows, _prs, _repo = tickets.index_rows(
        _conv("o/r#1 o/r#2", "o/r#3 o/r#4", "o/r#1 o/r#5")
    )
    # A key that is already kept still records its later message; a fifth key does not.
    assert _message_keys(rows) == [
        ("o/r", 1, "m1"),
        ("o/r", 2, "m1"),
        ("o/r", 3, "m2"),
        ("o/r", 4, "m2"),
        ("o/r", 1, "m3"),
    ]


def test_the_default_caps_keep_two_hundred_refs_of_a_message() -> None:
    from backend import tickets

    rows, _prs, _repo = tickets.index_rows(_conv(" ".join(f"a/b#{i}" for i in range(1, 501))))
    assert [n for _r, n, _m in _message_keys(rows)] == list(range(1, 201))


# ---------------------------------------------------------------------------
# T3: the external link
# ---------------------------------------------------------------------------


def test_jira_takes_the_host_that_most_references_name(corpus: None) -> None:
    body = key_ticket("PROJ-7")
    assert (body["system"], body["url"]) == ("jira", "https://acme.atlassian.net/browse/PROJ-7")


def test_a_jira_tie_goes_to_the_earliest_reference(corpus: None) -> None:
    assert key_ticket("PROJ-8")["url"] == "https://first.atlassian.net/browse/PROJ-8"


def test_linear_takes_the_workspace_that_most_references_name(corpus: None) -> None:
    body = key_ticket("OPS-9")
    assert body["system"] == "linear"
    assert body["url"].startswith("https://linear.app/acme/issue/OPS-9")


def test_a_configured_prefix_names_the_system_before_the_url(corpus: None) -> None:
    assert key_ticket("PROJ-12")["system"] == "jira"


def test_jira_without_a_seen_url_or_a_host_has_no_link(corpus: None) -> None:
    assert key_ticket("PROJ-5")["url"] is None


def test_a_configured_jira_host_wins_over_the_urls(corpus_hosts: None) -> None:
    assert key_ticket("PROJ-7")["url"] == "https://cfg.atlassian.net/browse/PROJ-7"
    assert key_ticket("PROJ-8")["url"] == "https://cfg.atlassian.net/browse/PROJ-8"


def test_a_configured_jira_host_gives_a_link_without_a_seen_url(corpus_hosts: None) -> None:
    assert key_ticket("PROJ-5")["url"] == "https://cfg.atlassian.net/browse/PROJ-5"


def test_a_configured_linear_workspace_wins_over_the_urls(corpus_hosts: None) -> None:
    assert key_ticket("OPS-9")["url"].startswith("https://linear.app/cfgws/issue/OPS-9")


# ---------------------------------------------------------------------------
# T4: PR links
# ---------------------------------------------------------------------------


def test_pr_rows_keep_only_github_pull_request_urls() -> None:
    from backend import tickets

    conv = {
        "pr_links": [
            {"prNumber": n, "prRepository": "fixture-org/alpha", "prUrl": url, "timestamp": "t"}
            for n, url in enumerate((*BAD_PRS, GOOD_PR), start=1)
        ]
    }
    assert [r[2] for r in tickets.pr_rows(conv)] == [GOOD_PR]


def test_the_ticket_page_lists_only_github_pr_links(corpus: None) -> None:
    body = tt.ticket("fixture-org", "alpha", 84)
    assert [p["url"] for p in body["prs"]] == [GOOD_PR]


def test_pr_keys_hold_only_valid_github_pr_links(corpus: None) -> None:
    from backend.store import ConversationStore

    (p1,) = [c for c in ConversationStore().list_conversations() if c.uuid == P1]
    assert p1.pr_keys == ["fixture-org/alpha#7"]
    assert p1.named_keys == ["fixture-org/alpha#84"]
