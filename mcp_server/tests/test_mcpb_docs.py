"""README / AGENTS.md docs coverage for the MCPB bundle.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 7":

After the bundle ships, the README needs a discoverable install path
for the drag-drop user, and AGENTS.md needs the build/closure-canary
notes for future agents working on the MCP code path. If a future
refactor accidentally drops either, this test catches it.

Test approach: structural keyword checks, not exact-phrase matches —
the docs can evolve without breaking this test as long as the four
load-bearing facts stay in.
"""

from __future__ import annotations

import pathlib


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
# AGENTS.md is the canonical agent doc (renamed from CLAUDE.md 2026-09-23).
# Read it directly: CLAUDE.md is only a maintainer's local, gitignored
# symlink, so it exists on one machine and not in CI.
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def test_readme_documents_mcpb_install_path() -> None:
    """README has an Install-in-Claude-Desktop section pointing at the
    .mcpb artifact AND warns the user that the CLI is still required
    for capture.

    The read-only + CLI-for-capture facts are the two things a user
    needs to know BEFORE installing — otherwise they install, see no
    sessions, and assume the extension is broken.
    """

    text = README.read_text(encoding="utf-8").lower()

    assert ".mcpb" in text, (
        "README must mention the .mcpb artifact — without it, a "
        "drag-drop user has no way to discover the install path"
    )
    assert "claude desktop" in text and "extension" in text, (
        "README must mention Claude Desktop Extensions explicitly"
    )
    assert "read-only" in text or "read only" in text, (
        "README must set the read-only expectation for the extension "
        "(matches the contract in assets/mcpb-README.md)"
    )
    assert "capture" in text and "cli" in text, (
        "README must warn that the CLI is still required for capture / "
        "fetch — the extension does not fetch"
    )


def test_agents_md_documents_mcpb_build_pipeline() -> None:
    """AGENTS.md mentions the build script AND the closure-canary
    invariant, so a future agent working on the MCP code path
    discovers the discipline.

    Closure canary is the load-bearing rule: pulling FastAPI /
    weasyprint into the MCP path balloons the bundle and breaks
    Claude Desktop's sandbox-allowed dep list.
    """

    text = AGENTS_MD.read_text(encoding="utf-8").lower()

    assert "build-mcpb.py" in text, (
        "AGENTS.md must mention scripts/build-mcpb.py so future agents "
        "know where the bundle pipeline lives"
    )
    assert "closure" in text, (
        "AGENTS.md must mention the closure canary — it's the rule that "
        "keeps the MCP path lean"
    )
    assert "fastapi" in text and ("weasyprint" in text or "watchdog" in text), (
        "AGENTS.md should name the specific deps the canary protects "
        "against so a future PR knows what to avoid"
    )
