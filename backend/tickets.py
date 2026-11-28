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

import functools
import json
import re
import zlib
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

#: Bump when the rows change: the index then reads every file again
#: (``SearchIndex._mark_stale_projections``, key "tickets"). 2: Linear, Jira,
#: Graphite, and the system, prefix, and url columns.
TICKET_REFS_VERSION = 3

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
_JIRA_HOST_RE = re.compile(r"https?://([A-Za-z0-9-]+\.atlassian\.net)/")
_LINEAR_WS_RE = re.compile(r"https?://linear\.app/([A-Za-z0-9_-]+)/issue/")
_PR_URL_RE = re.compile(r"https://github\.com/" + _OWNER + "/" + _NAME + r"/pull/(\d+)/?")

#: Caps against a hostile or huge message; extra references are dropped in
#: text order. Read at call time, so a test can lower them.
MAX_REFS_PER_MESSAGE = 200
MAX_TICKETS_PER_SESSION = 2000

#: An index row: (system, repo, prefix, number, message_uuid, kind, snippet, url,
#: created_at (UTC ISO, or None), pos (the start of the ref in its text)).
RefRow = tuple[str, "str | None", "str | None", int, "str | None", str, str, "str | None", "str | None", int]
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


@functools.lru_cache(maxsize=32)
def _key_text_re_for(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"(?<![\w/-])(" + "|".join(map(re.escape, prefixes)) + r")-(\d+)(?!\w)")


def _key_text_re(ticket_keys: TicketKeys | None) -> re.Pattern[str] | None:
    prefixes = sorted({k for v in (ticket_keys or {}).values() for k in v}, key=len, reverse=True)
    return _key_text_re_for(tuple(prefixes)) if prefixes else None


#: A match: (system, scope, number, start, end, url).
_Match = tuple[str, "str | None", int, int, int, "str | None"]


def _matches(text: str, ticket_keys: TicketKeys | None = None) -> list[_Match]:
    """Each reference in a text, in text order, without overlaps. ``scope`` as in :func:`ticket_key`.

    Linear in the number of candidates: collect all, sort by start, one sweep.
    Where two overlap, the higher priority wins (URL forms, then
    ``owner/repo#N``, then a configured key, then a bare #N).
    """
    cands: list[tuple[int, int, int, _Match]] = []  # (start, priority, -end, match)

    def add(prio: int, rx: re.Pattern[str], make: Callable[[re.Match[str]], tuple[str, str | None, str, bool]]) -> None:
        for m in rx.finditer(text):
            system, scope, n, url = make(m)
            if int(n) >= 1:
                cands.append((m.start(), prio, -m.end(),
                              (system, scope, int(n), m.start(), m.end(), m.group(0) if url else None)))

    add(0, _URL_RE, lambda m: (GITHUB, _repo(m.group(1), m.group(2)), m.group(3), True))
    add(1, _GRAPHITE_RE, lambda m: (GITHUB, _repo(m.group(1), m.group(2)), m.group(3), True))
    add(2, _LINEAR_URL_RE, lambda m: (LINEAR, m.group(1).upper(), m.group(2), True))
    add(3, _JIRA_URL_RE, lambda m: (JIRA, m.group(1).upper(), m.group(2), True))
    add(4, _FULL_RE, lambda m: (GITHUB, _repo(m.group(1), m.group(2)), m.group(3), False))
    key_re = _key_text_re(ticket_keys)
    if key_re is not None:
        add(5, key_re, lambda m: (key_system(m.group(1), ticket_keys) or LINEAR, m.group(1), m.group(2), False))
    add(6, _BARE_RE, lambda m: (GITHUB, None, m.group(1), False))
    cands.sort(key=lambda c: c[:3])
    out: list[tuple[int, _Match]] = []  # (priority, match)
    for _start, prio, _neg_end, match in cands:
        if out and match[3] < out[-1][1][4]:  # overlaps the last kept match
            if prio < out[-1][0]:
                out[-1] = (prio, match)
            continue
        out.append((prio, match))
    return [m for _p, m in out]


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
    """The PR links. Only a github.com PR URL is kept: the ticket page links to it."""
    return [(p["prNumber"], p["prRepository"], p["prUrl"], str(p.get("timestamp") or ""))
            for p in _pr_links(conv)
            if isinstance(p.get("prUrl"), str) and _PR_URL_RE.fullmatch(p["prUrl"])]


def session_repo(conv: dict) -> str | None:
    """The session's own repo: a github.com Codex remote, else the one pr-link repository."""
    repo = repo_from_remote(conv.get("git_repository_url"))
    if repo:
        return repo
    repos = {p["prRepository"].lower() for p in _pr_links(conv)}
    return repos.pop() if len(repos) == 1 else None


def _utc(value: Any) -> str | None:
    """A timestamp as UTC ISO 8601, so sources with "Z" and "+00:00" compare as strings."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value)  # Python 3.11+ reads a trailing "Z"
        except ValueError:
            return None
    else:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def ref_order(r: Mapping[str, Any]) -> tuple[bool, str, str, str, int]:
    """The order of references on every platform: message time (none last), then
    session uuid, message uuid, and position in the text."""
    ts = r.get("created_at")
    return (ts is None, ts or "", r.get("conv_uuid") or "", r.get("message_uuid") or "", r.get("pos") or 0)


def index_rows(conv: dict, ticket_keys: TicketKeys | None = None) -> tuple[list[RefRow], list[PrRow], str | None]:
    """The ``ticket_refs`` rows, the PR rows, and the session repo of one conversation.

    One row per (message, ticket, kind). Bare GitHub refs keep repo None; the
    query resolves them. An agent transcript of a session gives none: the
    ticket page lists sessions.
    """
    if conv.get("agent_id"):
        return [], [], None
    refs: dict[tuple[str, str | None, int, str | None, str], tuple[str, str | None, str | None, int]] = {}
    keys: set[str] = set()

    def add(system: str, scope: str | None, n: int, msg: str | None, kind: str, snippet: str, url: str | None,
            ts: str | None = None, pos: int = 0) -> None:
        key = ticket_key(scope, n)
        if key not in keys:
            if len(keys) >= MAX_TICKETS_PER_SESSION:
                return
            keys.add(key)
        refs.setdefault((system, scope, n, msg, kind), (snippet, url, ts, pos))

    def add_text(text: str, msg_uuid: str | None, kind: str, ts: str | None) -> None:
        for system, scope, n, s, e, url in _matches(text, ticket_keys)[:MAX_REFS_PER_MESSAGE]:
            add(system, scope, n, msg_uuid, kind, _snippet(text, s, e), url, ts, s)

    add_text(str(conv.get("name") or ""), None, "title", _utc(conv.get("created_at")))
    for m in conv.get("chat_messages") or []:
        if m.get("sender") in ("human", "assistant"):
            add_text(_message_text(m), m.get("uuid") or None, "message", _utc(m.get("created_at")))
    branch = conv.get("git_branch")
    n = branch_number(branch)
    if n is not None:
        add(GITHUB, None, n, None, "branch", str(branch), None)
    for system, prefix, num in branch_keys(branch, ticket_keys):
        add(system, prefix, num, None, "branch", str(branch), None)
    for p in _pr_links(conv):
        add(GITHUB, p["prRepository"].lower(), p["prNumber"], None, "pr_link", str(p.get("prUrl") or ""), None)
    rows: list[RefRow] = []
    for (system, scope, num, msg, kind), (snippet, url, ts, pos) in refs.items():
        repo, prefix = (scope, None) if system == GITHUB else (None, scope)
        rows.append((system, repo, prefix, num, msg, kind, snippet, url, ts, pos))
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


def _host_of(system: str, url: str | None) -> str | None:
    """The Jira host or the Linear workspace that a seen URL names."""
    m = (_JIRA_HOST_RE if system == JIRA else _LINEAR_WS_RE).match(url or "")
    return m.group(1).lower() if m else None


def external_link(
    prefix: str, number: int, refs: Sequence[Mapping[str, Any]],
    ticket_keys: TicketKeys | None, ticket_hosts: Mapping[str, str] | None,
) -> tuple[str | None, str | None]:
    """(system, url) of a Linear or Jira ticket, for "Open in Linear" or "Open in Jira".

    The first URL in a message is not trusted. The system comes from the
    configured prefix lists, else from the chosen URL. The host is the
    configured one (``ticket_hosts``), else the one that most references
    name (a tie goes to the earliest reference, :func:`ref_order`). The URL is built from the
    host, never copied from a message.
    """
    key = ticket_key(prefix, number)
    system = key_system(prefix, ticket_keys)
    # The index returns refs in drift-pass order, which differs by platform.
    refs = sorted(refs, key=ref_order)
    seen = [(r["system"], _host_of(r["system"], r.get("url"))) for r in refs if r.get("url")]
    seen = [s for s in seen if s[1] and (system is None or s[0] == system)]
    host: str | None = None
    if seen:
        counts: dict[tuple[str, str | None], int] = {}
        for s in seen:
            counts[s] = counts.get(s, 0) + 1
        best = max(counts.values())
        chosen = next(s for s in seen if counts[s] == best)
        system = system or chosen[0]
        host = chosen[1]
    host = (ticket_hosts or {}).get(system or "") or host
    if system == JIRA:
        return system, f"https://{host}/browse/{key}" if host else None
    if system == LINEAR:
        return system, f"https://linear.app/{host}/issue/{key}" if host else f"https://linear.app/issue/{key}"
    return system, None


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
