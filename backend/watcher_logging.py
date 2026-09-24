"""Shared log helper for the missing-watcher banner / terminal warning.

Called from two contexts:

* :mod:`backend.main`'s lifespan startup — emits a structured log
  record so supervised-job tails (launchd / systemd / Task Scheduler)
  surface the missing-watcher state.
* :func:`cli.main.serve` — emits a one-line terminal warning visible
  to a user running ``claude-explorer serve`` in their own shell.

The shared helper guarantees the two callers stay byte-identical in
their message text. A user grepping their terminal output should see
the same advice as a user grepping the lifespan log.

Per ``PLANS/2026.05.26-watcher-install-detection.md`` design principle
"at most once per session": the *helper* fires every call (no internal
dedupe) — the *caller* is responsible for calling exactly once at
process start. This separation lets tests pin the message contract
without fighting an internal flag.
"""

from __future__ import annotations

import logging

from .watcher_status import is_watcher_installed


log = logging.getLogger(__name__)


_DOCS_URL = "PLANS/2026.05.26-watcher-install-detection.md"


def log_watcher_status() -> None:
    """Emit one log record describing the watcher install state.

    * Watcher installed → INFO (single line: "CC image-cache watcher
      installed and supervised — image-cache rotations safe.").
    * Watcher missing → WARNING (four lines: situation, install
      command, consequence, docs ref).

    Idempotency is the caller's responsibility.
    """
    if is_watcher_installed():
        log.info(
            "CC image-cache watcher installed and supervised — "
            "image-cache rotations safe."
        )
        return
    # Multi-line WARNING so launchd / journalctl tails surface the
    # full advice in one place. Each line is its own log record (one
    # call per .warning) so structured-log parsers don't fight the
    # newlines.
    log.warning("CC image-cache watcher not installed.")
    from fetcher.install_hints import watcher_install_hint

    log.warning(
        "  Run %r to prevent permanent image-cache data loss",
        watcher_install_hint(),
    )
    log.warning("  during backend downtime.")
    log.warning("  See %s for details.", _DOCS_URL)
