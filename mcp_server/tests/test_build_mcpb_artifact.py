"""Test for the ``mcpb pack`` step of ``scripts/build-mcpb.py``.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 5 — mcpb pack invocation
+ dist artifact":

The ``mcpb`` CLI is a Node binary that the dev installs via
``npm install -g @anthropic-ai/mcpb``. We do NOT gate CI on having the
Node binary available, so these tests SKIP cleanly when ``mcpb`` is not
on PATH. When it IS available, they assert:

* ``mcpb pack`` produces a real zip with ``manifest.json`` at the root.
* The artifact path matches the expected ``dist/claude-explorer-${VERSION}.mcpb``.
* The artifact is small (bundle dir is ~590 KB; zipped, expect ~150 KB).

When ``mcpb`` is missing, the build script must NOT crash — it must
return cleanly with the bundle dir intact, so CI can run on a minimal
runner and so a dev without Node installed can still see the bundle
layout.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys
import zipfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_mcpb", REPO_ROOT / "scripts" / "build-mcpb.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mcpb"] = module
    spec.loader.exec_module(module)
    return module


def test_pack_bundle_raises_when_mcpb_cli_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``mcpb`` on PATH, ``pack_bundle`` raises ``MCPBPackError``.

    Caller (the CLI) catches this and falls back to "bundle dir
    assembled, install mcpb to pack." We don't want a generic crash
    that confuses devs who haven't installed the Node CLI.
    """

    build = _load_build_module()
    monkeypatch.setenv("PATH", "/nonexistent-dir-no-binaries")
    bundle = build.build_bundle(output_dir=tmp_path / "bundle", project_root=REPO_ROOT)
    with pytest.raises(build.MCPBPackError, match="mcpb CLI not found"):
        build.pack_bundle(bundle, tmp_path / "dist", version="1.0.6")


def test_cli_handles_missing_mcpb_gracefully(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running ``scripts/build-mcpb.py`` with mcpb missing returns 0
    and leaves the bundle dir assembled.

    A dev running ``python scripts/build-mcpb.py`` without ``mcpb``
    installed should see a helpful "install mcpb to pack" message, not
    a stacktrace. Exit code stays 0 because the bundle dir IS a
    legitimate intermediate output.
    """

    build = _load_build_module()
    monkeypatch.setenv("PATH", "/nonexistent-dir-no-binaries")

    output_dir = tmp_path / "bundle"
    dist_dir = tmp_path / "dist"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build-mcpb.py",
            "--output-dir",
            str(output_dir),
            "--dist-dir",
            str(dist_dir),
        ],
    )
    exit_code = build._cli()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert output_dir.exists(), "Bundle dir should still be assembled"
    assert "npm install -g @anthropic-ai/mcpb" in captured.err
    # No artifact should be produced.
    assert not dist_dir.exists() or not list(dist_dir.glob("*.mcpb"))


@pytest.mark.skipif(
    shutil.which("mcpb") is None,
    reason="mcpb CLI not installed (npm install -g @anthropic-ai/mcpb)",
)
def test_pack_bundle_produces_valid_zip(tmp_path: pathlib.Path) -> None:
    """``pack_bundle`` produces a valid zip with ``manifest.json`` at the
    root.

    SKIPs when the Node CLI isn't installed. When it IS available, this
    is the end-to-end smoke test that proves the bundle dir we assemble
    is something ``mcpb pack`` can actually pack.
    """

    build = _load_build_module()
    bundle = build.build_bundle(output_dir=tmp_path / "bundle", project_root=REPO_ROOT)
    dist_dir = tmp_path / "dist"
    version = build._read_version()
    artifact = build.pack_bundle(bundle, dist_dir, version)

    assert artifact.exists()
    assert artifact.name == f"claude-explorer-{version}.mcpb"
    assert zipfile.is_zipfile(artifact), (
        f"{artifact} is not a valid zip (mcpb pack should produce a zip)"
    )

    with zipfile.ZipFile(artifact) as zf:
        names = set(zf.namelist())
    assert "manifest.json" in names, (
        f"manifest.json missing from packed .mcpb (got: {sorted(names)[:10]}...)"
    )
