#!/usr/bin/env python3
"""Write frontend/src/lib/sources.generated.json from the source registry.

    uv run python scripts/generate_sources_json.py          # write the file
    uv run python scripts/generate_sources_json.py --check  # fail if it is stale

The web UI imports the file at build time, so the descriptors of the
backend and the UI cannot drift apart. ``scripts/check.py`` runs ``--check``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "src" / "lib" / "sources.generated.json"


def render() -> str:
    sys.path.insert(0, str(ROOT))
    from backend.sources.registry import get_descriptors

    return json.dumps([asdict(d) for d in get_descriptors()], indent=2) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print(f"{OUT.relative_to(ROOT)} is stale. Run: uv run python scripts/generate_sources_json.py")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
