"""A2 (issue #92): the CLI commands for bundles, labels, and the shared folder.

- ``claude-explorer archive export [--to PATH] [--passphrase] [--full]``
  prints "Exported N file(s), M bytes to PATH". ``--passphrase`` asks for it
  twice, hidden.
- ``claude-explorer archive import PATH [--passphrase]`` prints the counts; a
  refused bundle exits 1 with the reason.
- ``claude-explorer archive preview`` prints one line per file and a total.
- ``claude-explorer machine label TEXT``.
- ``claude-explorer archive remove-shared [--yes]`` asks before it removes
  only this machine's folder from the shared folder.
- Export and import refuse a corrupt config (exit 1).

Synthetic data only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from backend.tests._machines_env import (
    World,
    bundle_manifest,
    make_world,
    tree,
)

pytestmark = pytest.mark.xfail(strict=True, reason="A2 not implemented yet")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    with make_world(tmp_path, monkeypatch) as w:
        yield w


S1 = "a2f1f1f1-0000-4000-8000-000000000001"
WORD = "cliword"
PASS = "synthetic cli pass"


def _run(m: Any, args: list[str], input: str | None = None) -> Any:
    from click.testing import CliRunner

    from cli.main import main

    m.activate()
    return CliRunner().invoke(main, args, input=input, catch_exceptions=False)


def _b_ready(world: World) -> None:
    world.b.cc_session(S1, WORD)
    world.b.sync()
    world.b.export_all()


def test_archive_export_writes_the_bundle_and_says_so(world: World) -> None:
    _b_ready(world)
    dest = world.tmp / "b.cebundle"

    result = _run(world.b, ["archive", "export", "--to", str(dest)])

    assert result.exit_code == 0, result.output
    _, manifest = bundle_manifest(dest)
    n = len(manifest["files"])
    size = sum(f["bytes"] for f in manifest["files"])
    assert f"Exported {n} file(s), {size} bytes to {dest}" in result.output


def test_archive_export_with_passphrase_asks_twice_and_encrypts(world: World) -> None:
    _b_ready(world)
    dest = world.tmp / "b.cebundle"

    result = _run(
        world.b,
        ["archive", "export", "--to", str(dest), "--passphrase"],
        input=f"{PASS}\n{PASS}\n",
    )

    assert result.exit_code == 0, result.output
    assert dest.read_bytes().startswith(b"CEXB1")
    assert PASS not in result.output


def test_archive_export_in_mode_off_exits_1(world: World) -> None:
    world.b.cc_session(S1, WORD)
    world.b.sync()
    dest = world.tmp / "b.cebundle"

    result = _run(world.b, ["archive", "export", "--to", str(dest)])

    assert result.exit_code == 1
    assert not dest.exists()


def test_archive_import_imports_and_prints_the_counts(world: World) -> None:
    _b_ready(world)
    dest = world.tmp / "b.cebundle"
    _run(world.b, ["archive", "export", "--to", str(dest)])

    result = _run(world.a, ["archive", "import", str(dest)])

    assert result.exit_code == 0, result.output
    assert "copied" in result.output
    assert S1 in world.a.rows("CLAUDE_CODE")


def test_archive_import_of_an_encrypted_bundle_asks_for_the_passphrase(
    world: World,
) -> None:
    _b_ready(world)
    dest = world.tmp / "b.cebundle"
    _run(
        world.b,
        ["archive", "export", "--to", str(dest), "--passphrase"],
        input=f"{PASS}\n{PASS}\n",
    )

    result = _run(
        world.a, ["archive", "import", str(dest), "--passphrase"], input=f"{PASS}\n"
    )

    assert result.exit_code == 0, result.output
    assert S1 in world.a.rows("CLAUDE_CODE")


def test_archive_import_of_a_bad_bundle_exits_1_with_the_reason(world: World) -> None:
    junk = world.tmp / "junk.cebundle"
    junk.write_bytes(b"not a bundle")

    result = _run(world.a, ["archive", "import", str(junk)])

    assert result.exit_code == 1
    assert "not_a_bundle" in result.output or "not a bundle" in result.output.lower()


@pytest.mark.parametrize("command", ["export", "import"])
def test_archive_export_and_import_refuse_a_corrupt_config(
    world: World, command: str
) -> None:
    _b_ready(world)
    dest = world.tmp / "b.cebundle"
    _run(world.b, ["archive", "export", "--to", str(dest)])
    m = world.b if command == "export" else world.a
    m.write_corrupt_config()
    args = (
        ["archive", "export", "--to", str(world.tmp / "again.cebundle")]
        if command == "export"
        else ["archive", "import", str(dest)]
    )

    result = _run(m, args)

    assert result.exit_code == 1
    assert not (world.tmp / "again.cebundle").exists()
    assert not (world.a.data_dir / "machines").exists()


def test_archive_preview_lists_the_files_and_a_total(world: World) -> None:
    _b_ready(world)

    result = _run(world.b, ["archive", "preview"])

    assert result.exit_code == 0, result.output
    assert f"{S1}.jsonl" in result.output
    world.b.activate()
    from backend import machines

    preview = machines.preview_export()
    assert str(preview.total_bytes) in result.output


def test_machine_label_sets_the_label(world: World) -> None:
    world.a.ensure_id()

    result = _run(world.a, ["machine", "label", "Home Mac"])

    assert result.exit_code == 0, result.output
    ident = json.loads(
        (world.a.explorer_home / "machine.json").read_text(encoding="utf-8")
    )
    assert ident["label"] == "Home Mac"


def _pushed(world: World) -> Path:
    from backend import machines

    _b_ready(world)
    world.b.write_config(shared_archive_dir=str(world.shared))
    machines.sync_shared_folder(world.b.settings(), "cli", 10)
    folder = world.shared / "machines" / world.b.machine_id
    assert folder.is_dir()
    return folder


def test_archive_remove_shared_asks_first_and_no_keeps_the_folder(world: World) -> None:
    folder = _pushed(world)

    result = _run(world.b, ["archive", "remove-shared"], input="n\n")

    assert "Remove this machine's folder" in result.output
    assert folder.is_dir()


def test_archive_remove_shared_yes_removes_only_the_own_folder(world: World) -> None:
    folder = _pushed(world)
    other = world.shared / "machines" / "0e0e0e0e-0000-4000-8000-000000000000"
    other.mkdir(parents=True)
    (other / "machine.json").write_text("{}", encoding="utf-8")
    local = tree(world.b.data_dir)

    result = _run(world.b, ["archive", "remove-shared"], input="y\n")

    assert result.exit_code == 0, result.output
    assert not folder.exists()
    assert (other / "machine.json").is_file()
    assert tree(world.b.data_dir) == local


def test_archive_remove_shared_with_yes_flag_does_not_ask(world: World) -> None:
    folder = _pushed(world)

    result = _run(world.b, ["archive", "remove-shared", "--yes"])

    assert result.exit_code == 0, result.output
    assert not folder.exists()
