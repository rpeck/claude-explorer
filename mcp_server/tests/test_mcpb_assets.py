"""Asset tests for the MCPB bundle: icon and README.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 4 — icon + bundle assets"
and §6 (Icon).

The Claude Desktop Extensions panel shows the icon and the README to
the user before they install. Both need to exist and be the right
shape for the catalog UI to render correctly.

* Icon: 256×256 PNG. Smaller risks blurry rendering in the catalog tile,
  larger wastes bytes and slows the install bundle.
* README: plain Markdown the user sees in the Extensions detail pane.
"""

from __future__ import annotations

import pathlib
import struct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
ICON_PATH = ASSETS_DIR / "mcpb-icon.png"
README_PATH = ASSETS_DIR / "mcpb-README.md"


def _png_dimensions(path: pathlib.Path) -> tuple[int, int]:
    """Read a PNG's width/height from the IHDR chunk without Pillow.

    Avoids a runtime dep on Pillow for the test suite. PNG layout:
    8-byte signature, then chunks of [4-byte length, 4-byte type,
    data, 4-byte CRC]. IHDR is always the first chunk and starts at
    byte 16 with width (4B) + height (4B) as big-endian uint32s.
    """

    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", (
        f"{path} is not a PNG (bad signature {data[:8]!r})"
    )
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_mcpb_icon_exists() -> None:
    """The icon file is committed to ``assets/mcpb-icon.png``."""

    assert ICON_PATH.exists(), (
        f"MCPB icon missing at {ICON_PATH}. Add a 256×256 PNG — see "
        f"plan §6 (Icon) for sourcing options."
    )


def test_mcpb_icon_is_512x512_png() -> None:
    """Icon is a real PNG and exactly 512×512.

    The ``mcpb validate`` CLI emits an explicit recommendation for
    512×512 ("Recommended size is 512×512 pixels for best display in
    Claude Desktop"). Smaller works but loses sharpness on Retina
    Extensions tiles.
    """

    width, height = _png_dimensions(ICON_PATH)
    assert (width, height) == (512, 512), (
        f"MCPB icon is {width}×{height}; must be exactly 512×512 "
        f"(per mcpb validate recommendation)"
    )


def test_mcpb_readme_exists() -> None:
    """The Extensions-panel README ships alongside the icon."""

    assert README_PATH.exists(), (
        f"MCPB README missing at {README_PATH}. The Extensions panel "
        f"shows this to the user before install."
    )


def test_mcpb_readme_mentions_key_facts() -> None:
    """The README mentions the two non-obvious facts the user needs
    BEFORE installing — read-only contract and CLI-for-capture."""

    text = README_PATH.read_text(encoding="utf-8").lower()
    assert "read-only" in text or "read only" in text, (
        "MCPB README should set the read-only expectation explicitly so "
        "users don't expect the extension to fetch conversations"
    )
    assert "claude-explorer" in text and ("cli" in text or "command" in text), (
        "MCPB README should tell users the CLI is required for capture / "
        "fetch — otherwise they install this and see no data"
    )


def test_build_script_includes_icon_in_bundle(tmp_path: pathlib.Path) -> None:
    """When the icon exists, the build script copies it into the bundle.

    Closes the loop between commit 3 (build script with the soft-skip
    fallback) and commit 4 (asset now exists). After this commit lands,
    the bundle's ``icon.png`` must be a verbatim copy of
    ``assets/mcpb-icon.png``.
    """

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "build_mcpb", REPO_ROOT / "scripts" / "build-mcpb.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_mcpb"] = module
    spec.loader.exec_module(module)

    bundle = module.build_bundle(output_dir=tmp_path / "bundle", project_root=REPO_ROOT)
    bundled_icon = bundle / "icon.png"
    assert bundled_icon.exists(), (
        "Build script did not copy assets/mcpb-icon.png into the bundle"
    )
    assert bundled_icon.read_bytes() == ICON_PATH.read_bytes()

    bundled_readme = bundle / "README.md"
    assert bundled_readme.exists(), (
        "Build script did not copy assets/mcpb-README.md into the bundle"
    )
    assert bundled_readme.read_bytes() == README_PATH.read_bytes()
