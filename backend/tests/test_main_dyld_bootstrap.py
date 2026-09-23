"""Pin: the dev server bootstraps DYLD for WeasyPrint at import time.

Regression: before 2026-05-24 the DYLD bootstrap lived ONLY in
``backend/tests/conftest.py``. The dev server (``uvicorn backend.main:app``)
imports ``backend.main`` directly, which transitively imports the
``export`` router, which transitively imports WeasyPrint at module-load
time. macOS SIP strips ``DYLD_*`` env vars from ``uv run`` subprocess
invocations, so the standard ``DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run
uvicorn ...`` recipe documented in ``AGENTS.md`` silently no-ops — the
shell prefix never reaches the python interpreter. Result: the PDF
export route returned ``500`` on a fresh-start dev server, with a
``OSError: cannot load library 'libgobject-2.0-0'`` in the traceback.

This file pins the user-observable contract (TESTING.md §5.13):
a FRESH process invoking ``python -c 'import backend.main; import weasyprint'``
must succeed on macOS. We spawn an actual subprocess so the conftest.py
bootstrap (which fires only inside this pytest process) cannot mask a
regression in the backend.main bootstrap path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.mark.skipif(sys.platform != "darwin", reason="DYLD bootstrap is macOS-only")
def test_fresh_subprocess_import_backend_main_then_weasyprint_succeeds() -> None:
    """Spawn a fresh python process. Import ``backend.main`` (which must
    bootstrap DYLD as a side-effect of import) and then import
    ``weasyprint``. Both must succeed.

    Why a subprocess: this pytest process inherited the conftest.py
    bootstrap, so a plain in-process ``import weasyprint`` would pass
    regardless of whether backend.main does the right thing. The fresh
    subprocess has no pytest, no conftest, and no inherited DYLD env —
    same shape as the dev server's startup.

    The subprocess env explicitly STRIPS ``DYLD_FALLBACK_LIBRARY_PATH``
    so the bootstrap inside backend.main is the only way for WeasyPrint
    to find its native libs. Without the bootstrap, weasyprint's
    import raises ``OSError`` and the subprocess exits non-zero.
    """
    script = textwrap.dedent(
        """
        import sys
        try:
            import backend.main  # noqa: F401 — side effect is the test
            import weasyprint  # noqa: F401
        except Exception as e:
            print(f"FAIL: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)
        print("OK")
        """
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    # Make sure the subprocess can find the backend package.
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        "Fresh-process import chain failed. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}. "
        "Likely the DYLD bootstrap in backend/main.py is missing, "
        "commented out, or runs AFTER the export-router import."
    )
    assert "OK" in result.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="DYLD bootstrap is macOS-only")
def test_fresh_subprocess_without_bootstrap_fails_as_expected() -> None:
    """Bidirectional pair for the test above. Confirms the subprocess
    harness actually exercises the SIP-strip scenario.

    Imports weasyprint DIRECTLY (no backend.main) in a stripped-DYLD
    subprocess. On a macOS dev box without Homebrew libs on the default
    loader path, this MUST fail. If it doesn't, the harness above is
    proving nothing (e.g. the user has glib installed somewhere the
    default loader finds, in which case the bootstrap is a no-op and
    the regression can't recur).
    """
    script = textwrap.dedent(
        """
        import sys
        try:
            import weasyprint  # noqa: F401
        except OSError as e:
            print(f"EXPECTED_OSERROR: {e}", file=sys.stderr)
            sys.exit(2)
        except Exception as e:
            print(f"UNEXPECTED: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)
        print("WEASYPRINT_IMPORTED_WITHOUT_BOOTSTRAP")
        """
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
    env.setdefault("PYTHONPATH", str(REPO_ROOT))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    if result.returncode == 0:
        # weasyprint imported without help — the user's machine has
        # libs reachable on the default loader path. Skip rather than
        # fail because the bootstrap is genuinely not needed here.
        pytest.skip(
            "WeasyPrint imports without DYLD bootstrap on this machine; "
            "the SIP-strip regression cannot recur here, so the "
            "positive-pair test above is the only meaningful guard."
        )
    assert result.returncode == 2, (
        f"Expected OSError when importing weasyprint without backend.main "
        f"bootstrap, got returncode={result.returncode}. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "EXPECTED_OSERROR" in result.stderr
