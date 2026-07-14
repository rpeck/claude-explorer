"""Shared terminal-color helpers for the CLI's status output.

Used by the `doctor` report (:mod:`backend.doctor`) and the `install`
summary (:mod:`cli.main`) so both colorize consistently. CLI-only —
must stay OUT of the MCPB import closure (the canary in
``mcp_server/tests/test_mcpb_closure.py`` enforces this).

Color policy follows the CLI conventions (clig.dev / no-color.org):
color is additive (the text marker always stays, so it's colorblind-
and pipe-safe), enabled only on an interactive TTY, disabled when
``NO_COLOR`` is set or ``--no-color`` is passed, and forced when
``FORCE_COLOR`` is set. Precedence: explicit flag > ``NO_COLOR`` >
``FORCE_COLOR`` > TTY detection.
"""

from __future__ import annotations

import os
import sys

import click


# Status kind -> foreground color. Keys match the three doctor states
# plus the install summary's ok/fail.
_FG = {"ok": "green", "warn": "yellow", "fail": "red"}


def should_use_color(no_color: bool = False) -> bool:
    """Decide whether to emit ANSI color for this invocation."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def style_status(text: str, kind: str, color: bool) -> str:
    """Bold-color a status marker (``kind`` in {ok, warn, fail}).

    Returns ``text`` unchanged when ``color`` is False.
    """
    if not color:
        return text
    return click.style(text, fg=_FG[kind], bold=True)


def style_dim(text: str, color: bool) -> str:
    """Dim secondary text (e.g. a fix hint). Plain when ``color`` is False."""
    return click.style(text, dim=True) if color else text
