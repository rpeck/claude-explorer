#!/usr/bin/env python3
"""Pre-push gate: article image / link formats must render on GitHub.

The Medium series is read on GitHub AND Medium (co-equal surfaces). GitHub's
blob-Markdown renderer is the strict one, so this checker enforces the formats
that survive it (and Obsidian, where the author drafts). Run from the repo root:

    python3 scripts/check-article-formats.py                 # scan every articles/*.md
    python3 scripts/check-article-formats.py a.md b.md       # check only these files

With no arguments it scans every ``articles/*.md`` (the published surface). Given
explicit paths it checks exactly those (and a path that doesn't exist is a hard
error, exit 2 — never a silent pass). It FAILS (exit 1) on:

  1. Obsidian image embeds ``![[...]]``  -> GitHub shows literal text.
  2. Obsidian wikilinks    ``[[...]]``   -> GitHub shows literal text.
  3. A space or ``%20`` in a LOCAL image/link path or ``<img src>`` -> GitHub's
     blob renderer mangles ``%20`` in relative paths and shows a broken image
     (the raw file resolves at the %20 URL, which is the trap). Use dash-named
     files and plain relative paths instead.
  4. A referenced LOCAL image that is not git-tracked -> 404 on GitHub.

Rationale + the empirical findings live in
``PLANS/articles/medium-articles.md`` (image + TOC standards).

Files in KNOWN_PENDING are skipped with a loud warning (a WIP part whose images
are not in the repo yet). Empty that set before publishing those parts.
"""
from __future__ import annotations
import argparse
import glob
import os
import re
import subprocess
import sys

# Parts not yet ready to render on GitHub; fix and remove before publishing.
# (Part 3's restructure on 2026-06-02 dropped its image embeds entirely, so it
# no longer needs to be pending; the set is empty again.)
KNOWN_PENDING: set[str] = set()

INLINE_CODE = re.compile(r"`[^`]*`")
FENCE = re.compile(r"^\s*```")


def tracked_under_articles() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "articles"], capture_output=True, text=True
    ).stdout
    return set(out.splitlines())


def is_remote(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))


def check_file(path: str, tracked: set[str]) -> list[tuple[int, str, str]]:
    viol: list[tuple[int, str, str]] = []
    in_fence = False
    for lineno, raw in enumerate(open(path, encoding="utf-8").read().split("\n"), 1):
        if FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = INLINE_CODE.sub("", raw)  # drop `inline code` so doc examples don't trip it

        for m in re.finditer(r"!\[\[[^\]]*\]\]", line):
            viol.append((lineno, "Obsidian image embed ![[...]] (literal text on GitHub)", m.group(0)))
        for m in re.finditer(r"(?<!!)\[\[[^\]]*\]\]", line):
            viol.append((lineno, "Obsidian wikilink [[...]] (literal text on GitHub)", m.group(0)))

        # markdown image/link destinations
        for m in re.finditer(r"!?\[[^\]]*\]\(([^)]+)\)", line):
            tgt = m.group(1).strip()
            if is_remote(tgt):
                continue
            if " " in tgt or "%20" in tgt:
                viol.append((lineno, "space/%20 in local markdown path (GitHub mangles it)", tgt))
        # HTML <img src="...">
        for m in re.finditer(r'<img[^>]*\bsrc="([^"]+)"', line):
            tgt = m.group(1).strip()
            if is_remote(tgt):
                continue
            if " " in tgt or "%20" in tgt:
                viol.append((lineno, "space/%20 in <img src> (GitHub mangles it)", tgt))

        # referenced local IMAGES must be git-tracked (else 404 on GitHub)
        for m in re.finditer(r'!\[[^\]]*\]\(([^)]+)\)|<img[^>]*\bsrc="([^"]+)"', line):
            tgt = (m.group(1) or m.group(2)).strip()
            if is_remote(tgt):
                continue
            rel = os.path.normpath(os.path.join(os.path.dirname(path), tgt.replace("%20", " ")))
            # `git ls-files` always reports forward slashes. On Windows
            # normpath yields backslashes, so every image looked untracked.
            rel = rel.replace(os.sep, "/")
            if rel not in tracked:
                viol.append((lineno, "referenced image not git-tracked (404 on GitHub)", tgt))
    return viol


def resolve_targets(paths: list[str]) -> tuple[list[str], list[str]]:
    """Map CLI args to files to check.

    No paths -> every ``articles/*.md`` (the full pre-push scan). Explicit paths
    -> exactly those, with any that don't exist returned separately so the caller
    can hard-error instead of silently passing (the 2026-06-03 false-green bug).
    """
    if not paths:
        return sorted(glob.glob("articles/*.md")), []
    files: list[str] = []
    missing: list[str] = []
    for p in paths:
        (files if os.path.isfile(p) else missing).append(p)
    return files, missing


# Windows consoles default to a legacy code page (cp1252), and the status
# glyphs below are outside it, so printing them raises UnicodeEncodeError and
# the check dies before it reports anything. Force UTF-8 on the streams.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Specific article files to check. Default: every articles/*.md.",
    )
    args = parser.parse_args(argv)

    explicit = bool(args.paths)
    files, missing = resolve_targets(args.paths)
    if missing:
        for p in missing:
            print(f"error: no such file: {p}", file=sys.stderr)
        return 2
    if not files:
        print("error: no article files to check", file=sys.stderr)
        return 2

    tracked = tracked_under_articles()
    failed = False
    skipped: list[str] = []
    for path in sorted(files):
        # KNOWN_PENDING only auto-skips during a full (no-args) scan; a file named
        # explicitly on the command line is always checked.
        if not explicit and path in KNOWN_PENDING:
            skipped.append(path)
            continue
        viol = check_file(path, tracked)
        if viol:
            failed = True
            print(f"\n✗ {path}")
            for lineno, why, what in viol:
                print(f"    line {lineno}: {why}\n        {what}")
    for path in skipped:
        print(f"⚠️  SKIP {path} (KNOWN_PENDING — fix image format before publishing this part)")
    if failed:
        print(
            "\nArticle format check FAILED. Use standard Markdown for full-window shots and "
            "centered <img width> for crops, dash-named files, plain relative paths "
            "(no spaces / no %20). See PLANS/articles/medium-articles.md."
        )
        return 1
    print("✓ article image/link formats OK (GitHub-renderable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
