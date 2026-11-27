"""Ticket references (issue #93): which sessions worked on a ticket.

Three ticket systems. A ticket key is:

* GitHub: ``owner/repo#N`` (owner and repo in lower case), or ``#N`` when the
  repo is not known;
* Linear and Jira: ``ENG-123`` (the key prefix in upper case).

The references come from data that Explorer already has; nothing calls an
API:

* the title, and the text blocks of human and assistant messages (never tool
  calls, tool output, or thinking: tool output is full of "(#106)");
* the git branch (``fix/84-search-tools``, ``rpeck/eng-123-fix-search``);
* Claude Code ``pr-link`` records (exact session-to-PR links).

A Linear or Jira key in text or a branch counts only when its prefix is in
``config.json`` "ticket_keys"; a Linear or Jira URL always counts. A bare
``#N`` keeps repo None in the index: the query resolves it
(:func:`resolve_repos`), so a later PR fixes older sessions without a
reindex. A bare #N in message text counts only when the repo resolves.
"""

from __future__ import annotations

import json
import re
import zlib
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

#: Bump when the rows change: the index then reads every file again
#: (``SearchIndex._mark_stale_projections``, key "tickets"). 2: Linear, Jira,
#: Graphite, and the system, prefix, and url columns.
TICKET_REFS_VERSION = 2

GITHUB, LINEAR, JIRA = "github", "linear", "jira"

_OWNER = r"([A-Za-z0-9][A-Za-z0-9-]*)"
_NAME = r"([A-Za-z0-9._-]+)"
_TAIL = r"(?:/[^\s)\]>\"'`]*)?"
_URL_RE = re.compile(
    r"https?://(?:www\.)?github\.com/" + _OWNER + "/" + _NAME + r"/(?:issues|pull)/(\d+)(?![\w])"
)
_GRAPHITE_RE = re.compile(r"https?://app\.graphite\.dev/github/pr/" + _OWNER + "/" + _NAME + r"/(\d+)(?!\d)" + _TAIL)
_LINEAR_URL_RE = re.compile(r"https?://linear\.app/[A-Za-z0-9_-]+/issue/([A-Za-z][A-Za-z0-9_]*)-(\d+)(?!\d)" + _TAIL)
_JIRA_URL_RE = re.compile(r"https?://[A-Za-z0-9-]+\.atlassian\.net/browse/([A-Za-z][A-Za-z0-9_]*)-(\d+)(?![\w])")
_FULL_RE = re.compile(r"(?<![\w./-])" + _OWNER + "/" + _NAME + r"#(\d+)(?!\w)")
_BARE_RE = re.compile(r"(?<![\w#/&])#(\d+)(?!\w)")
_BRANCH_SEGMENT_RE = re.compile(r"^(\d+)(?:-|$)")
_GH_KEY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)?#(\d+)$")
# Keys are case-sensitive in text and as input ("eng-123" is no key); a branch matches any case.
_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)-(\d+)$")
_REMOTE_RE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?|ssh://git@|git@)github\.com[:/]" + _OWNER + r"/([A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_SNIPPET_BEFORE = 60
_SNIPPET_AFTER = 100

#: An index row: (system, repo, prefix, number, message_uuid, kind, snippet, url).
RefRow = tuple[str, "str | None", "str | None", int, "str | None", str, str, "str | None"]
#: A PR row: (number, repository, url, timestamp).
PrRow = tuple[int, str, str, str]
#: config.json "ticket_keys": {"linear": [...], "jira": [...]}.
TicketKeys = Mapping[str, Sequence[str]]


def ticket_key(scope: str | None, number: int) -> str:
    """The key of a ticket. ``scope``: "owner/repo", None (unknown repo), or a Linear or Jira prefix."""
    if not scope:
        return f"#{number}"
    return f"{scope.lower()}#{number}" if "/" in scope else f"{scope.upper()}-{number}"


def key_system(prefix: str, ticket_keys: TicketKeys | None) -> str | None:
    """"linear" or "jira" when the prefix is configured (Linear wins), else None."""
    for system in (LINEAR, JIRA):
        if prefix.upper() in ((ticket_keys or {}).get(system) or ()):
            return system
    return None


def keys_version(ticket_keys: TicketKeys | None) -> int:
    """The index version of the ticket rows: a changed "ticket_keys" re-indexes too."""
    blob = json.dumps({s: sorted(v) for s, v in (ticket_keys or {}).items()}, sort_keys=True)
    return TICKET_REFS_VERSION * 2**32 + zlib.crc32(blob.encode())


def _repo(owner: str, name: str) -> str:
    return f"{owner}/{name}".lower()


def _key_text_re(ticket_keys: TicketKeys | None) -> re.Pattern[str] | None:
    prefixes = sorted({k for v in (ticket_keys or {}).values() for k in v}, key=len, reverse=True)
    if not prefixes:
        return None
    return re.compile(r"(?<![\w/-])(" + "|".join(map(re.escape, prefixes)) + r")-(\d+)(?!\w)")


#: A match: (system, scope, number, start, end, url).
_Match = tuple[str, "str | None", int, int, int, "str | None"]


def _matches(text: str, ticket_keys: TicketKeys | None = None) -> list[_Match]:
    """Each reference in a text, in text order. ``scope`` as in :func:`ticket_key`."""
    out: list[_Match] = []
    taken: list[tuple[int, int]] = []

    def take(m: re.Match[str], system: str, scope: str | None, n: str, url: bool) -> None:
        if all(m.end() <= a or m.start() >= b for a, b in taken):
            out.append((system, scope, int(n), m.start(), m.end(), m.group(0) if url else None))
            taken.append(m.span())

    for m in _URL_RE.finditer(text):
        take(m, GITHUB, _repo(m.group(1), m.group(2)), m.group(3), True)
    for m in _GRAPHITE_RE.finditer(text):
        take(m, GITHUB, _repo(m.group(1), m.group(2)), m.group(3), True)
    for m in _LINEAR_URL_RE.finditer(text):
        take(m, LINEAR, m.group(1).upper(), m.group(2), True)
    for m in _JIRA_URL_RE.finditer(text):
        take(m, JIRA, m.group(1).upper(), m.group(2), True)
    for m in _FULL_RE.finditer(text):
        take(m, GITHUB, _repo(m.group(1), m.group(2)), m.group(3), False)
    key_re = _key_text_re(ticket_keys)
    if key_re is not None:
        for m in key_re.finditer(text):
            take(m, key_system(m.group(1), ticket_keys) or LINEAR, m.group(1), m.group(2), False)
    for m in _BARE_RE.finditer(text):
        take(m, GITHUB, None, m.group(1), False)
    return sorted((r for r in out if r[2] >= 1), key=lambda r: r[3])


def extract_text_refs(
    text: str, default_repo: str | None, ticket_keys: TicketKeys | None = None
) -> list[tuple[str | None, int]]:
    """The (scope, number) refs in a text, unique, in text order. A bare #N gets ``default_repo``.

    ``scope`` as in :func:`ticket_key`: "owner/repo", None, or a Linear or Jira prefix.
    """
    seen: dict[tuple[str | None, int], None] = {}
    default = default_repo.lower() if default_repo else None
    for system, scope, n, _s, _e, _u in _matches(text or "", ticket_keys):
        seen.setdefault((scope or default if system == GITHUB else scope, n), None)
    return list(seen)


def branch_number(branch: str | None) -> int | None:
    """The issue number of a branch: a path segment that starts with digits, then "-" or the end."""
    for segment in (branch or "").split("/"):
        m = _BRANCH_SEGMENT_RE.match(segment)
        if m and int(m.group(1)) >= 1:
            return int(m.group(1))
    return None


def branch_keys(branch: str | None, ticket_keys: TicketKeys | None) -> list[tuple[str, str, int]]:
    """(system, PREFIX, number) of each configured key in a branch, in any case ("eng-123-fix")."""
    out: list[tuple[str, str, int]] = []
    for system in (LINEAR, JIRA):
        for prefix in (ticket_keys or {}).get(system) or ():
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(prefix.lower()) + r"-(\d+)(?!\d)")
            for m in rx.finditer((branch or "").lower()):
                if int(m.group(1)) >= 1 and (prefix.upper(), int(m.group(1))) not in {(p, n) for _s, p, n in out}:
                    out.append((system, prefix.upper(), int(m.group(1))))
    return out


def parse_ticket_key(s: str | None) -> tuple[str | None, int] | None:
    """(scope, number) of "owner/repo#N", "#N", "ENG-123", or a GitHub, Graphite, Linear, or Jira URL.

    ``scope`` as in :func:`ticket_key`. None when invalid.
    """
    s = (s or "").strip()
    scope: str | None
    for rx, gh in ((_URL_RE, True), (_GRAPHITE_RE, True), (_LINEAR_URL_RE, False), (_JIRA_URL_RE, False)):
        m = rx.fullmatch(s)
        if m:
            scope, n = (_repo(m.group(1), m.group(2)), int(m.group(3))) if gh else (m.group(1).upper(), int(m.group(2)))
            break
    else:
        m = _GH_KEY_RE.match(s)
        if m:
            scope, n = (m.group(1).lower() if m.group(1) else None), int(m.group(2))
        else:
            m = _KEY_RE.match(s)
            if not m:
                return None
            scope, n = m.group(1).upper(), int(m.group(2))
    return (scope, n) if n >= 1 else None


def repo_from_remote(url: Any) -> str | None:
    """``owner/repo`` of a github.com remote URL, else None."""
    m = _REMOTE_RE.match(url.strip()) if isinstance(url, str) else None
    return _repo(m.group(1), m.group(2)) if m else None


def _snippet(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - _SNIPPET_BEFORE):end + _SNIPPET_AFTER].split())


def _message_text(m: dict) -> str:
    """The text blocks of a message; the ``text`` field only when it has no content list."""
    content = m.get("content")
    if isinstance(content, list) and content:
        return "\n".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    text = m.get("text")
    return text if isinstance(text, str) else ""


def _pr_links(conv: dict) -> list[dict]:
    return [
        p for p in conv.get("pr_links") or []
        if isinstance(p, dict) and isinstance(p.get("prNumber"), int) and p["prNumber"] >= 1
        and isinstance(p.get("prRepository"), str) and p["prRepository"]
    ]


def pr_rows(conv: dict) -> list[PrRow]:
    return [(p["prNumber"], p["prRepository"], str(p.get("prUrl") or ""), str(p.get("timestamp") or ""))
            for p in _pr_links(conv)]


def session_repo(conv: dict) -> str | None:
    """The session's own repo: a github.com Codex remote, else the one pr-link repository."""
    repo = repo_from_remote(conv.get("git_repository_url"))
    if repo:
        return repo
    repos = {p["prRepository"].lower() for p in _pr_links(conv)}
    return repos.pop() if len(repos) == 1 else None


def index_rows(conv: dict, ticket_keys: TicketKeys | None = None) -> tuple[list[RefRow], list[PrRow], str | None]:
    """The ``ticket_refs`` rows, the PR rows, and the session repo of one conversation.

    One row per (message, ticket, kind). Bare GitHub refs keep repo None; the
    query resolves them. An agent transcript of a session gives none: the
    ticket page lists sessions.
    """
    if conv.get("agent_id"):
        return [], [], None
    refs: dict[tuple[str, str | None, int, str | None, str], tuple[str, str | None]] = {}

    def add(system: str, scope: str | None, n: int, msg: str | None, kind: str, snippet: str, url: str | None) -> None:
        refs.setdefault((system, scope, n, msg, kind), (snippet, url))

    def add_text(text: str, msg_uuid: str | None, kind: str) -> None:
        for system, scope, n, s, e, url in _matches(text, ticket_keys):
            add(system, scope, n, msg_uuid, kind, _snippet(text, s, e), url)

    add_text(str(conv.get("name") or ""), None, "title")
    for m in conv.get("chat_messages") or []:
        if m.get("sender") in ("human", "assistant"):
            add_text(_message_text(m), m.get("uuid") or None, "message")
    branch = conv.get("git_branch")
    n = branch_number(branch)
    if n is not None:
        add(GITHUB, None, n, None, "branch", str(branch), None)
    for system, prefix, num in branch_keys(branch, ticket_keys):
        add(system, prefix, num, None, "branch", str(branch), None)
    for p in _pr_links(conv):
        add(GITHUB, p["prRepository"].lower(), p["prNumber"], None, "pr_link", str(p.get("prUrl") or ""), None)
    rows: list[RefRow] = []
    for (system, scope, num, msg, kind), (snippet, url) in refs.items():
        repo, prefix = (scope, None) if system == GITHUB else (None, scope)
        rows.append((system, repo, prefix, num, msg, kind, snippet, url))
    return rows, pr_rows(conv), session_repo(conv)


def resolve_repos(sessions: dict[str, tuple[str, str | None]]) -> dict[str, str | None]:
    """The repo of each conversation for its bare refs.

    ``sessions``: conv_uuid -> (project_path, own repo). The own repo wins;
    else the one repo that other sessions with the same project_path have;
    else None.
    """
    by_path: dict[str, set[str]] = {}
    for path, repo in sessions.values():
        if path and repo:
            by_path.setdefault(path, set()).add(repo)
    out: dict[str, str | None] = {}
    for conv, (path, repo) in sessions.items():
        repos = by_path.get(path) or set()
        out[conv] = repo or (next(iter(repos)) if len(repos) == 1 else None)
    return out


def row_key(system: str, repo: str | None, prefix: str | None, number: int, kind: str,
            resolved: str | None) -> str | None:
    """The ticket key of an index row, or None when it does not count.

    A bare #N in message text counts only when the session repo resolves;
    in a title or a branch it is the unknown-repo ticket.
    """
    if system != GITHUB:
        return ticket_key(prefix, number) if prefix else None
    repo = repo or resolved
    if repo is None and kind == "message":
        return None
    return ticket_key(repo, number)


def ticket_url(system: str | None, scope: str | None, number: int, seen_url: str | None) -> str | None:
    """GitHub: the issue URL; Linear: the seen URL, else linear.app/issue/KEY-N; Jira: the seen URL."""
    if system == GITHUB:
        return f"https://github.com/{scope}/issues/{number}" if scope else None
    if seen_url:
        return seen_url
    return f"https://linear.app/issue/{ticket_key(scope, number)}" if system == LINEAR and scope else None


# --- the API shape --------------------------------------------------------------


class TicketRefEntry(BaseModel):
    kind: str
    message_uuid: str | None = None
    snippet: str = ""


class TicketSession(BaseModel):
    conversation_uuid: str
    name: str
    source: str
    project_path: str | None = None
    entrypoint: str | None = None
    machine_id: str | None = None
    machine_label: str | None = None
    state: str | None = None
    created_at: str
    updated_at: str
    kinds: list[str] = Field(default_factory=list)
    refs: list[TicketRefEntry] = Field(default_factory=list)


class TicketPr(BaseModel):
    number: int
    repository: str
    url: str
    conversation_uuid: str
    timestamp: str


class TicketDetail(BaseModel):
    key: str
    repo: str | None = None
    number: int
    #: "github", "linear", "jira", or None (a key whose prefix is not known).
    system: str | None = None
    url: str | None = None
    sessions: list[TicketSession] = Field(default_factory=list)
    prs: list[TicketPr] = Field(default_factory=list)
