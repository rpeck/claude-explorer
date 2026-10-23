"""Text files are read and written as UTF-8, not with the OS default (2026-10-23).

On Windows the default encoding is not UTF-8. ``release.yml`` holds 20
non-ASCII bytes, and the release test read it with ``read_text()``; the
MCPB build script read ``mcp_server/__init__.py`` the same way; the fetcher
wrote conversation and index JSON and read the credentials file with a bare
``open()``. Each case runs in a child interpreter with
``-X warn_default_encoding -W error::EncodingWarning`` (PEP 597): a
default-encoding access then raises.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _run_strict(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning", "-c", textwrap.dedent(code)],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": "", "HOME": str(tmp_path), "PYTHONPATH": str(REPO)},
        timeout=120,
    )


CASES = {
    "the release test reads release.yml": """
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location("t", "mcp_server/tests/test_mcpb_ci_release.py")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        mod.release_workflow.__wrapped__()
    """,
    "the MCPB build reads the version": """
        import runpy
        ns = runpy.run_path("scripts/build-mcpb.py")
        assert ns["_read_version"]()
    """,
    "the fetcher saves a conversation and the index": """
        import pathlib, tempfile
        from fetcher.bulk_fetch import ClaudeFetcher
        out = pathlib.Path(tempfile.mkdtemp())
        f = ClaudeFetcher(session_key="sk", orgs=[{"uuid": "org-A", "name": "A"}], primary_org_id="org-A",
                          output_dir=out, files_dir=out / "files", download_files=False)
        f.save_conversation({"uuid": "c-1", "name": "caf\\u00e9 \\U0001F600", "chat_messages": []})
        f.save_index([{"uuid": "c-1", "name": "caf\\u00e9"}])
    """,
    "the credentials are read": """
        import json, pathlib, tempfile
        from fetcher.credentials import load_credentials
        p = pathlib.Path(tempfile.mkdtemp()) / "credentials.json"
        p.write_text(json.dumps({"session_key": "sk", "org_id": "org-A"}), encoding="utf-8")
        load_credentials(p)
    """,
}


@pytest.mark.parametrize("name", list(CASES))
def test_file_io_does_not_use_the_default_encoding(name: str, tmp_path: Path) -> None:
    result = _run_strict(CASES[name], tmp_path)
    assert result.returncode == 0, f"{name}:\n{result.stderr[-2000:]}"
