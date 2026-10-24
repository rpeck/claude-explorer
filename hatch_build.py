"""Custom Hatch build hook that bundles the React frontend into the wheel.

Behavior:
- Runs ONLY for the `wheel` build target (not sdist, not editable).
- Builds the frontend via `npm ci && npm run build` if `frontend/dist` is
  missing or older than any curated source input.
- Injects `frontend/dist/` into the wheel at `backend/_static/` via
  ``build_data["force_include"]`` so files don't need to be tracked by git
  and the source tree is never polluted.
- Fails loudly with an actionable message if `npm` isn't on PATH.

End users installing from PyPI never see this hook — wheels are pre-built.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# Curated input set for the staleness check. If any of these is newer than
# `frontend/dist/index.html`, we rebuild. (`src/**` alone would miss config
# changes; `**/*` would race against `dist/` itself.)
_INPUT_GLOBS = (
    "src/**/*",
    "public/**/*",
    "index.html",
    "vite.config.ts",
    "package.json",
    "package-lock.json",
)


class FrontendBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        # Only run during a real wheel build. Hatchling's "wheel" builder is
        # used for both `wheel` AND `editable` targets — they differ in the
        # `version` argument ("standard" vs "editable"). We must NOT run npm
        # for editable installs because `uv run` / `uv sync` invokes
        # build_editable on every dependency-graph change, which would
        # silently re-run `npm ci` on every Python invocation.
        if self.target_name != "wheel" or version == "editable":
            return

        # Manual override for contributors who already built the frontend
        # and just want to (re)build the wheel without re-running npm.
        if os.environ.get("CLAUDE_EXPLORER_SKIP_FRONTEND_BUILD") == "1":
            self.app.display_info(
                "hatch_build: CLAUDE_EXPLORER_SKIP_FRONTEND_BUILD=1, "
                "using existing frontend/dist (no npm)"
            )
            dist_dir = Path(self.root) / "frontend" / "dist"
            if not (dist_dir / "index.html").exists():
                raise RuntimeError(
                    "hatch_build: CLAUDE_EXPLORER_SKIP_FRONTEND_BUILD=1 was set "
                    "but frontend/dist/index.html doesn't exist."
                )
            build_data.setdefault("force_include", {})[str(dist_dir)] = "backend/_static"
            return

        root = Path(self.root)
        frontend = root / "frontend"
        dist_dir = frontend / "dist"

        # If the user is building from an sdist that didn't include the
        # frontend sources, there's nothing we can do. Fail with a useful
        # message rather than a confusing FileNotFoundError deep inside npm.
        if not (frontend / "package.json").exists():
            raise RuntimeError(
                "hatch_build: frontend/package.json not found. "
                "If you're building from an sdist, make sure 'frontend/**' "
                "was included; if you're building from a git checkout, "
                "ensure the frontend/ directory is present."
            )

        if self._needs_rebuild(frontend, dist_dir):
            self._run_npm_build(frontend)
        else:
            self.app.display_info(
                "hatch_build: frontend/dist is up to date — skipping npm build"
            )

        if not (dist_dir / "index.html").exists():
            raise RuntimeError(
                f"hatch_build: expected {dist_dir / 'index.html'} after build "
                f"but it doesn't exist. Frontend build silently failed?"
            )

        # Inject dist/ → backend/_static/ at wheel-assembly time. This
        # bypasses git's tracked-files list entirely (frontend/dist is
        # .gitignored, and we don't want to commit build outputs).
        force_include = build_data.setdefault("force_include", {})
        force_include[str(dist_dir)] = "backend/_static"
        self.app.display_info(
            f"hatch_build: force_include {dist_dir} -> backend/_static"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _needs_rebuild(self, frontend: Path, dist_dir: Path) -> bool:
        """Return True if frontend/dist is missing or older than any input."""
        index_html = dist_dir / "index.html"
        if not index_html.exists():
            return True
        if os.environ.get("CLAUDE_EXPLORER_FORCE_FRONTEND_BUILD") == "1":
            return True

        dist_mtime = index_html.stat().st_mtime
        for pattern in _INPUT_GLOBS:
            for candidate in frontend.glob(pattern):
                if not candidate.is_file():
                    continue
                try:
                    if candidate.stat().st_mtime > dist_mtime:
                        return True
                except OSError:
                    continue
        return False

    def _run_npm_build(self, frontend: Path) -> None:
        """Run `npm ci && npm run build` in the frontend directory."""
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "hatch_build: `npm` not found on PATH. Node.js 20+ and npm "
                "are required to build the wheel. End users installing from "
                "PyPI don't need them — they get a pre-built wheel. "
                "To build locally: install Node 20+ (e.g. `brew install node@20` "
                "or via nvm) and re-run `uv build`."
            )

        self.app.display_info(
            f"hatch_build: running `npm ci` in {frontend}"
        )
        self._run([npm, "ci"], cwd=frontend)
        self.app.display_info(
            f"hatch_build: running `npm run build` in {frontend}"
        )
        self._run([npm, "run", "build"], cwd=frontend)

    @staticmethod
    def _run(cmd: list[str], cwd: Path) -> None:
        try:
            subprocess.run(cmd, cwd=cwd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"hatch_build: command {cmd!r} failed in {cwd} "
                f"with exit code {e.returncode}"
            ) from e
