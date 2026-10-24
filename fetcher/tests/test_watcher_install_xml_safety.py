"""XML escaping for launchd plist generation (Council A2-PLIST-XSS).

Pre-fix bug:
    ``cli/watcher.py:_build_launchd_plist`` (originally
    ``fetcher/cli.py:_build_launchd_plist``, then briefly
    ``fetcher/watcher_install.py``) interpolates ``Path.cwd()`` and
    ``Path.home() / "Library" / "Logs"`` into the plist as raw
    f-strings without ``_xml_escape``. The neighboring
    ``ProgramArguments`` array WAS escaped. If a user runs
    ``claude-explorer install-watcher`` from a directory whose path
    contains XML-reserved characters (``&``, ``<``, ``>``, ``"``), the
    generated plist is malformed XML and launchd refuses to load it —
    silently breaking the supervised watcher.

Bidirectional discipline:

  * RED ``test_plist_with_ampersand_in_cwd_round_trips_through_xml_parser``:
    pre-fix, parsing the plist with ``xml.etree.ElementTree`` raises
    ``ParseError`` because ``<string>/some/path & dir</string>`` is
    invalid XML. Post-fix, parsing succeeds and the WorkingDirectory
    element's text equals the original path verbatim.
  * GREEN pair
    ``test_plist_with_normal_path_unchanged``: a plain cwd path still
    round-trips correctly post-fix (regression net).
  * Boundary ``test_plist_with_xml_metacharacters_in_log_dir``: the
    StandardOutPath / StandardErrorPath strings (built from
    ``Path.home() / "Library" / "Logs"``) also escape correctly — covers
    the case where the user's home directory contains ``&`` (rare but
    technically allowed by macOS).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from cli.watcher import _LAUNCHD_LABEL, _build_launchd_plist


def _parse_plist(plist_xml: str) -> ET.Element:
    """Parse the plist body and return the root <plist> element.

    The Apple DOCTYPE declaration requires network resolution by default;
    we strip it so the test parses without a network round-trip.
    """
    # Drop DOCTYPE so we don't hit the external DTD fetch on parse.
    stripped = "\n".join(
        line for line in plist_xml.splitlines() if not line.startswith("<!DOCTYPE")
    )
    return ET.fromstring(stripped)


def _dict_pairs(root: ET.Element) -> list[tuple[str, ET.Element]]:
    """Flatten the <plist><dict>...</dict></plist> into (<key>, <value>) pairs."""
    d = root.find("dict")
    assert d is not None, "plist must have a <dict> root"
    pairs: list[tuple[str, ET.Element]] = []
    children = list(d)
    i = 0
    while i < len(children):
        key_el = children[i]
        assert key_el.tag == "key", f"expected <key> at position {i}, got <{key_el.tag}>"
        value_el = children[i + 1]
        pairs.append((key_el.text or "", value_el))
        i += 2
    return pairs


def _value_for_key(root: ET.Element, key: str) -> ET.Element:
    for k, v in _dict_pairs(root):
        if k == key:
            return v
    raise AssertionError(f"key {key!r} not found in plist")


def test_plist_with_ampersand_in_cwd_round_trips_through_xml_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pre-fix this raises xml.etree.ElementTree.ParseError on the bare '&'.

    Post-fix the parser accepts the plist and the WorkingDirectory text
    equals the original cwd verbatim (XML decoded).
    """
    # Construct a cwd path with an unescaped XML metacharacter. Using
    # tmp_path as parent keeps the test cross-platform (Windows mkdir
    # tolerates '&' in subdir names).
    bad_cwd = tmp_path / "has & ampersand"
    bad_cwd.mkdir()

    monkeypatch.chdir(bad_cwd)
    plist_xml = _build_launchd_plist(python_bin="/usr/bin/python3", scan_interval=600.0)

    # Pre-fix this would raise: ParseError: not well-formed (invalid token).
    root = _parse_plist(plist_xml)

    wd_el = _value_for_key(root, "WorkingDirectory")
    assert wd_el.tag == "string"
    # ElementTree decodes &amp; back to & — so the text should equal the
    # raw filesystem path even though it appeared as &amp; in the source XML.
    assert wd_el.text == str(bad_cwd), (
        f"WorkingDirectory should decode to the original path, "
        f"got {wd_el.text!r}, expected {str(bad_cwd)!r}"
    )


def test_plist_with_normal_path_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression net: ordinary cwd path produces valid plist with unchanged text."""
    monkeypatch.chdir(tmp_path)
    plist_xml = _build_launchd_plist(python_bin="/usr/bin/python3", scan_interval=600.0)
    root = _parse_plist(plist_xml)

    wd_el = _value_for_key(root, "WorkingDirectory")
    assert wd_el.text == str(tmp_path)

    # Smoke-check the well-known scalars are still populated.
    label_el = _value_for_key(root, "Label")
    assert label_el.text == _LAUNCHD_LABEL

    args_array = _value_for_key(root, "ProgramArguments")
    assert args_array.tag == "array"
    arg_strings = [c.text for c in args_array.findall("string")]
    assert arg_strings[0] == "/usr/bin/python3"
    assert arg_strings[1] == "-c"
    # The third arg is the inline watcher script body.
    assert "run_watcher" in (arg_strings[2] or "")


def test_plist_with_xml_metacharacters_in_log_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Boundary: Path.home() containing '&' must escape in StandardOut/ErrorPath.

    Rare in practice (most macOS usernames are alphanumeric) but technically
    allowed by HFS+/APFS, so any code path that interpolates a home-derived
    path into XML must escape.
    """
    bad_home = tmp_path / "home & user"
    bad_home.mkdir()
    # §5.12 attribute-patch idiom: ``_build_launchd_plist`` resolves
    # ``Path.home()`` through its own module's namespace. After the
    # A1-CLI-LAYER move, that namespace is ``cli.watcher`` (not
    # ``fetcher.cli``). Patching the wrong module would be a SILENT
    # no-op — the test would pass against the un-patched real home.
    monkeypatch.setattr("cli.watcher.Path.home", lambda: bad_home)
    monkeypatch.chdir(tmp_path)

    plist_xml = _build_launchd_plist(python_bin="/usr/bin/python3", scan_interval=600.0)
    root = _parse_plist(plist_xml)

    out_el = _value_for_key(root, "StandardOutPath")
    err_el = _value_for_key(root, "StandardErrorPath")

    expected_log_dir = bad_home / "Library" / "Logs"
    assert out_el.text == f"{expected_log_dir}/claude-explorer-cc-watcher.out"
    assert err_el.text == f"{expected_log_dir}/claude-explorer-cc-watcher.err"
