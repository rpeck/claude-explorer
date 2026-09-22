"""The 'install Chromium' hint must match how the user installed the app.

Earned 2026-09-21. Both hint sites told every user to run
``uv sync && uv run playwright install chromium``. That is the
from-source workflow. A user who installed from PyPI (``uvx`` or
``uv tool install``) has no source checkout and no ``uv run``, so the
command fails and leaves the user stuck at the first step.

A source checkout keeps ``pyproject.toml`` at the repo root. A PyPI
install does not. That marker selects the command.
"""

from __future__ import annotations

from pathlib import Path

from fetcher.install_hints import playwright_install_hint


def test_source_checkout_gets_uv_run(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert playwright_install_hint(tmp_path) == "uv run playwright install chromium"


def test_pypi_install_gets_uvx(tmp_path: Path) -> None:
    """No repo marker means a wheel install, so `uv run` is unavailable."""
    hint = playwright_install_hint(tmp_path)
    assert hint == "uvx --from claude-explorer playwright install chromium"


def test_the_two_hints_differ(tmp_path: Path) -> None:
    """Bidirectional: the marker actually changes the answer."""
    pypi = playwright_install_hint(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    source = playwright_install_hint(tmp_path)
    assert pypi != source


def test_no_hint_mentions_uv_sync(tmp_path: Path) -> None:
    """`uv sync` installs dependencies; it never downloads a browser binary."""
    assert "uv sync" not in playwright_install_hint(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert "uv sync" not in playwright_install_hint(tmp_path)


def test_default_root_resolves_without_argument() -> None:
    """Called with no argument it must still return one of the two commands."""
    assert "playwright install chromium" in playwright_install_hint()


def test_hint_module_imports_without_playwright() -> None:
    """The hint must survive the failure it explains.

    ``fetcher.playwright_capture`` imports ``playwright`` at module level.
    If the hint lived there, the "playwright is not installed" branch could
    not import it, and the user would get an ImportError instead of the
    instruction. The hint therefore lives in a standard-library-only module.
    """
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['playwright'] = None; "
        "from fetcher.install_hints import playwright_install_hint; "
        "print(playwright_install_hint())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "playwright install chromium" in result.stdout


def test_re_export_from_playwright_capture_still_works() -> None:
    """Existing imports from the old location must keep working."""
    from fetcher.playwright_capture import playwright_install_hint as reexported

    assert reexported is playwright_install_hint
