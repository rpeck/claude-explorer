"""Fetch remote Cowork sessions ("tasks") from claude.ai.

Claude Desktop can run a Cowork task in Anthropic's cloud instead of on the
user's machine. The sidebar of claude.ai and Claude Desktop lists these under
"Chats and tasks". They are NOT chat conversations:

* ``/api/organizations/<org>/chat_conversations`` never lists them. An
  account that only uses tasks gets an empty list there, so the chat fetch
  "succeeds" with 0 conversations.
* Nothing is written to disk locally, so ``backend.cowork_reader`` (which
  reads ``local-agent-mode-sessions/``) never sees them either.

They live behind the Claude Code sessions API, which takes the same
``sessionKey`` cookie plus three extra headers::

    GET https://claude.ai/v1/code/sessions?tags=cowork-remote&limit=100
        &include_trigger_sessions=true[&cursor=<next_cursor>]
        -> {"data": [session, ...], "next_cursor": "<opaque>"?}

    GET https://claude.ai/v1/code/sessions/<id>/events?limit=100[&cursor=<seq>]
        -> {"data": [event, ...], "next_cursor": "<seq>"?}
        Events come NEWEST FIRST. ``cursor=<seq>`` returns events with a
        lower ``sequence_num``. The last page has no ``next_cursor``.

Each event's ``payload`` is a Claude Agent SDK stream-json message
(``user`` / ``assistant`` / ``system`` / ``result`` / ...), the same shape a
local Cowork session writes to ``audit.jsonl``. So we store every remote
session in the local Cowork on-disk shape and the existing reader, search
index and UI render it unchanged::

    <data_dir>/cowork-remote/<org_uuid>/remote_<session_id>/audit.jsonl
    <data_dir>/cowork-remote/<org_uuid>/remote_<session_id>.json   (sidecar)

Each ``audit.jsonl`` line is the event payload plus ``_audit_timestamp``
(the event's ``created_at``) and ``_sequence_num``. Incremental fetches read
the highest ``_sequence_num`` back from the file, not from the sidecar, so a
crash between the two writes can never duplicate events.

Endpoints and headers were recorded from the claude.ai web app with
Playwright on 2026-10-14.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fetcher.atomic import atomic_replace
from fetcher.paths import REMOTE_COWORK_DIRNAME, REMOTE_SESSION_PREFIX


log = logging.getLogger(__name__)


CODE_SESSIONS_URL = "https://claude.ai/v1/code/sessions"

#: Headers the claude.ai web app sends to the sessions API. Without the
#: beta header the API rejects the request.
CCR_BETA = "ccr-byoc-2025-07-29"
ANTHROPIC_VERSION = "2023-06-01"

#: Tag the API puts on every remote Cowork session.
REMOTE_COWORK_TAG = "cowork-remote"

PAGE_LIMIT = 100
#: The most pages one listing follows. A server bug that kept handing back
#: new cursors would otherwise keep Refresh busy forever.
MAX_PAGES = 10_000
#: Session and org ids become folder names, so only these characters may
#: reach a path. Real ids look like ``cse_01AbC...`` and UUIDs.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def is_safe_id(value: object) -> bool:
    """True if ``value`` can be a folder name with no path tricks."""
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None

#: Transport chatter that carries no conversation content. ``control_request``
#: also repeats the full MCP server config on every turn.
_DROPPED_PAYLOAD_TYPES = frozenset(
    {"control_request", "control_response", "tool_progress", "keep_alive"}
)


def remote_cowork_root(data_dir: Path) -> Path:
    """Where remote Cowork sessions are stored for a conversations data dir."""
    return data_dir / REMOTE_COWORK_DIRNAME


@dataclass
class RemoteFetchResult:
    """Outcome of fetching one org's remote sessions."""

    listed: int = 0
    fetched: int = 0
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)


class RemoteCoworkFetcher:
    """Lists remote Cowork sessions for an org and archives their events.

    ``http_get`` is ``ClaudeFetcher._get``: it carries the session cookies
    and the transient-error retry layer. All network access goes through
    :meth:`_get_json`, which is the boundary tests stub.
    """

    def __init__(
        self,
        http_get: Callable[..., Any] | None,
        output_dir: Path,
        incremental: bool = True,
        delay: float = 0.3,
    ) -> None:
        self._http_get = http_get
        self.root = remote_cowork_root(output_dir)
        self.incremental = incremental
        self.delay = delay

    # ------------------------------------------------------------------ HTTP

    def _get_json(self, url: str, org_uuid: str) -> dict:
        """GET ``url`` against the sessions API and return the JSON body.

        Raises whatever ``ClaudeFetcher._get`` raises for transport errors,
        and ``curl_cffi``'s HTTPError (carrying the status) for 4xx.
        """
        if self._http_get is None:
            raise RuntimeError("RemoteCoworkFetcher has no HTTP client")
        headers = {
            "anthropic-beta": CCR_BETA,
            "anthropic-version": ANTHROPIC_VERSION,
            "x-organization-uuid": org_uuid,
            "content-type": "application/json",
        }
        resp = self._http_get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def list_sessions(self, org_uuid: str) -> list[dict]:
        """Every remote Cowork session in the org, following pagination."""
        sessions: list[dict] = []
        seen: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            url = (
                f"{CODE_SESSIONS_URL}?tags={REMOTE_COWORK_TAG}&limit={PAGE_LIMIT}"
                "&include_trigger_sessions=true"
            )
            if cursor:
                url += f"&cursor={cursor}"
            body = self._get_json(url, org_uuid)
            page = body.get("data") or []
            added = 0
            for s in page:
                sid = s.get("id")
                if sid and sid not in seen:
                    seen.add(sid)
                    sessions.append(s)
                    added += 1
            cursor = body.get("next_cursor")
            if not page or not cursor:
                return sessions
            if not _still_advancing(cursor, seen_cursors, added, "session list"):
                return sessions
            time.sleep(self.delay)
        log.warning("remote cowork: session list stopped at %d pages", MAX_PAGES)
        return sessions

    def fetch_events_after(
        self, org_uuid: str, session_id: str, after_seq: int | None
    ) -> list[dict]:
        """Events with ``sequence_num > after_seq``, oldest first.

        ``after_seq=None`` fetches the whole session. Pages run newest first,
        so we stop as soon as a page reaches ``after_seq``.
        """
        collected: list[dict] = []
        seen_seqs: set[int] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            url = f"{CODE_SESSIONS_URL}/{session_id}/events?limit={PAGE_LIMIT}"
            if cursor:
                url += f"&cursor={cursor}"
            body = self._get_json(url, org_uuid)
            page = body.get("data") or []
            reached_known = False
            added = 0
            for ev in page:
                seq = _seq(ev)
                if seq is None:
                    continue
                if after_seq is not None and seq <= after_seq:
                    reached_known = True
                    continue
                if seq in seen_seqs:
                    continue
                seen_seqs.add(seq)
                collected.append(ev)
                added += 1
            cursor = body.get("next_cursor")
            if not page or not cursor or reached_known:
                break
            if not _still_advancing(cursor, seen_cursors, added, f"events of {session_id}"):
                break
            time.sleep(self.delay)
        else:
            log.warning("remote cowork: events of %s stopped at %d pages", session_id, MAX_PAGES)
        collected.sort(key=lambda e: _seq(e) or 0)
        return collected

    # --------------------------------------------------------------- storage

    def _session_paths(self, org_uuid: str, session_id: str) -> tuple[Path, Path]:
        """The session's folder and sidecar. Both ids come from outside (the
        API, the credentials file), so refuse anything that is not a plain
        name: ``x/../../escaped`` once wrote outside the archive."""
        if not is_safe_id(org_uuid) or not is_safe_id(session_id):
            raise ValueError(f"unsafe id for a path: org={org_uuid!r} session={session_id!r}")
        name = f"{REMOTE_SESSION_PREFIX}{session_id}"
        org_dir = self.root / org_uuid
        return org_dir / name, org_dir / f"{name}.json"

    def save_session(
        self, org_uuid: str, session: dict, new_events: list[dict], replace: bool
    ) -> None:
        """Write the session's ``audit.jsonl`` and sidecar.

        ``replace=False`` appends ``new_events`` to the existing file;
        ``replace=True`` rewrites it from ``new_events`` alone.
        """
        session_id = session["id"]
        session_dir, sidecar_path = self._session_paths(org_uuid, session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        audit_path = session_dir / "audit.jsonl"

        lines: list[str] = []
        seen_uuids: set[str] = set()
        if not replace and audit_path.exists():
            for raw in audit_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                lines.append(raw)
                try:
                    u = json.loads(raw).get("uuid")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if u:
                    seen_uuids.add(u)

        for ev in new_events:
            line = _event_to_audit_line(ev)
            if line is None:
                continue
            u = line.get("uuid")
            if u and u in seen_uuids:
                continue
            if u:
                seen_uuids.add(u)
            lines.append(json.dumps(line, ensure_ascii=False))

        _atomic_write_text(audit_path, "\n".join(lines) + ("\n" if lines else ""))
        _atomic_write_text(
            sidecar_path,
            json.dumps(_build_sidecar(org_uuid, session), indent=2, ensure_ascii=False),
        )

    def _stored_state(self, org_uuid: str, session_id: str) -> tuple[int | None, str | None]:
        """(highest stored sequence_num, sidecar's API ``updated_at``)."""
        session_dir, sidecar_path = self._session_paths(org_uuid, session_id)
        audit_path = session_dir / "audit.jsonl"
        max_seq: int | None = None
        if audit_path.exists():
            for raw in audit_path.read_text(encoding="utf-8").splitlines():
                try:
                    seq = json.loads(raw).get("_sequence_num")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if isinstance(seq, int) and (max_seq is None or seq > max_seq):
                    max_seq = seq
        updated_at: str | None = None
        if sidecar_path.exists():
            try:
                updated_at = json.loads(sidecar_path.read_text(encoding="utf-8")).get(
                    "apiUpdatedAt"
                )
            except (OSError, json.JSONDecodeError, AttributeError):
                updated_at = None
        return max_seq, updated_at

    # ------------------------------------------------------------ orchestration

    def plan_org(self, org_uuid: str) -> tuple[list[tuple[dict, int | None]], int]:
        """List the org's sessions and decide which need fetching.

        Returns ``(work, unchanged)``. Each work item is ``(session,
        after_seq)``: fetch events newer than ``after_seq`` (``None`` means the
        whole session). In incremental mode a session whose sidecar already
        records the API's ``updated_at`` is unchanged and skipped.
        """
        work: list[tuple[dict, int | None]] = []
        unchanged = 0
        for session in self.list_sessions(org_uuid):
            sid = session.get("id")
            if not sid:
                continue
            if not is_safe_id(sid):
                log.warning("remote cowork: skipping a session with an unsafe id: %r", sid)
                continue
            if not self.incremental:
                work.append((session, None))
                continue
            max_seq, stored_updated = self._stored_state(org_uuid, sid)
            if (
                max_seq is not None
                and stored_updated is not None
                and stored_updated == session.get("updated_at")
            ):
                unchanged += 1
                continue
            work.append((session, max_seq))
        return work, unchanged

    def sync_session(self, org_uuid: str, session: dict, after_seq: int | None) -> int:
        """Fetch and store one session's new events. Returns how many."""
        events = self.fetch_events_after(org_uuid, session["id"], after_seq)
        self.save_session(org_uuid, session, events, replace=after_seq is None)
        return len(events)

    def sync_org(
        self,
        org_uuid: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> RemoteFetchResult:
        """Archive every new or changed remote session in the org.

        A failure on one session is recorded and the rest continue; a failure
        listing sessions propagates to the caller.
        """
        work, unchanged = self.plan_org(org_uuid)
        result = RemoteFetchResult(listed=len(work) + unchanged, unchanged=unchanged)
        for i, (session, after_seq) in enumerate(work):
            title = session.get("title") or "Untitled"
            if on_progress:
                on_progress(title)
            try:
                self.sync_session(org_uuid, session, after_seq)
            except Exception as exc:  # noqa: BLE001  one bad session must not stop the rest
                log.warning(
                    "remote cowork: failed to fetch %s (%s): %s", session["id"], title, exc
                )
                result.failed.append(session["id"])
                continue
            result.fetched += 1
            if i < len(work) - 1:
                time.sleep(self.delay)
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _still_advancing(cursor: str, seen_cursors: set[str], added: int, what: str) -> bool:
    """False when pagination stops making progress: a cursor that repeats, or
    a page that added nothing new. Records ``cursor`` as seen."""
    if cursor in seen_cursors or added == 0:
        log.warning("remote cowork: %s stopped: the next page brings nothing new", what)
        return False
    seen_cursors.add(cursor)
    return True


def _seq(event: dict) -> int | None:
    try:
        return int(event.get("sequence_num"))
    except (TypeError, ValueError):
        return None


def _event_to_audit_line(event: dict) -> dict | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("type") in _DROPPED_PAYLOAD_TYPES:
        return None
    line = dict(payload)
    line["_audit_timestamp"] = event.get("created_at")
    line["_sequence_num"] = _seq(event)
    return line


def _epoch_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _build_sidecar(org_uuid: str, session: dict) -> dict:
    """Sidecar in the local Cowork shape, plus the fields only remote has.

    The reader uses ``sessionId`` (as the conversation uuid), ``title``,
    ``model``, ``cwd``, ``createdAt``/``lastActivityAt`` (epoch ms) and
    ``isArchived``.
    """
    config = session.get("config") or {}
    folders = (
        ((session.get("client_metadata") or {}).get("remote_cowork") or {}).get(
            "userSelectedFolders"
        )
        or []
    )
    cwd = folders[0].get("path", "") if folders and isinstance(folders[0], dict) else ""
    last_activity = session.get("last_event_at") or session.get("updated_at")
    return {
        "sessionId": session["id"],
        "title": session.get("title") or "",
        "model": config.get("model") or "",
        "cwd": cwd,
        "createdAt": _epoch_ms(session.get("created_at")),
        "lastActivityAt": _epoch_ms(last_activity),
        "isArchived": session.get("status") == "archived",
        "remote": True,
        "organizationId": org_uuid,
        "origin": config.get("origin"),
        "environmentKind": session.get("environment_kind"),
        "tags": session.get("tags") or [],
        "apiUpdatedAt": session.get("updated_at"),
    }


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        atomic_replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = [
    "CODE_SESSIONS_URL",
    "REMOTE_COWORK_DIRNAME",
    "REMOTE_COWORK_TAG",
    "REMOTE_SESSION_PREFIX",
    "RemoteCoworkFetcher",
    "RemoteFetchResult",
    "remote_cowork_root",
]
