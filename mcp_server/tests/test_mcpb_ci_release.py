"""CI wiring test for the MCPB release flow.

Per ``PLANS/2026.06.04-mcpb-bundle.md`` §"Commit 6 — CI release wiring":

When a ``v*`` tag is pushed, ``.github/workflows/release.yml`` must:

1. Build the MCPB artifact alongside the wheel.
2. Attach ``dist/claude-explorer-*.mcpb`` to the GitHub Release.

If a future PR rips out the build-mcpb job or breaks the artifact upload,
this test fails BEFORE a release tag is pushed — instead of "the
GitHub Release ships without the .mcpb attachment and a user file an
issue."

The test parses the YAML and asserts structural invariants, NOT exact
string matches — so reformatting (re-ordering steps, renaming workers,
bumping action versions) doesn't break it.
"""

from __future__ import annotations

import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def release_workflow() -> dict:
    """Parse the release workflow into a dict.

    Imports PyYAML lazily so the rest of the suite doesn't require it
    when this single test isn't run.
    """

    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(RELEASE_YML.read_text(encoding="utf-8"))


def test_release_yaml_parses(release_workflow: dict) -> None:
    """The workflow file is structurally valid YAML.

    Sanity check — guards against a quoting/indent break in any other
    test that consumes the parsed dict.
    """

    assert "jobs" in release_workflow
    assert isinstance(release_workflow["jobs"], dict)


def test_release_includes_build_mcpb_job(release_workflow: dict) -> None:
    """A ``build-mcpb`` job exists.

    Naming is load-bearing only insofar as the test recognizes it; if a
    future refactor renames the job, update this test rather than
    removing it.
    """

    jobs = release_workflow["jobs"]
    assert "build-mcpb" in jobs, (
        "release.yml must define a build-mcpb job that produces the "
        ".mcpb artifact. See PLANS/2026.06.04-mcpb-bundle.md §8."
    )


def test_build_mcpb_job_depends_on_test_gate(release_workflow: dict) -> None:
    """``build-mcpb`` runs only after the test gate passes.

    Mirror of the existing ``build`` (wheel) job. A green test suite is
    a precondition for both PyPI publish and GitHub Release attach.
    """

    job = release_workflow["jobs"]["build-mcpb"]
    needs = job.get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    assert "test" in needs, (
        "build-mcpb must `needs: test` so a tag with failing tests "
        "doesn't ship a .mcpb"
    )


def test_build_mcpb_job_installs_mcpb_cli(release_workflow: dict) -> None:
    """The job runs ``npm install -g @anthropic-ai/mcpb`` (or equivalent).

    Without this step, ``scripts/build-mcpb.py`` falls back to the
    "assembled but not packed" path and the GitHub Release ships
    without the .mcpb artifact.
    """

    job = release_workflow["jobs"]["build-mcpb"]
    steps = job.get("steps", [])
    run_blocks = [s.get("run", "") for s in steps if isinstance(s, dict)]
    combined = "\n".join(run_blocks)
    assert "@anthropic-ai/mcpb" in combined, (
        "build-mcpb job must install the mcpb CLI (npm install -g "
        "@anthropic-ai/mcpb) before invoking scripts/build-mcpb.py"
    )


def test_build_mcpb_job_invokes_build_script(release_workflow: dict) -> None:
    """The job invokes ``scripts/build-mcpb.py``.

    Catches a regression where the job is added but the script isn't
    wired in — would produce an empty dist/ and silent release failure.
    """

    job = release_workflow["jobs"]["build-mcpb"]
    steps = job.get("steps", [])
    run_blocks = [s.get("run", "") for s in steps if isinstance(s, dict)]
    combined = "\n".join(run_blocks)
    assert "scripts/build-mcpb.py" in combined, (
        "build-mcpb job must invoke scripts/build-mcpb.py"
    )


def test_release_uploads_mcpb_to_github_release(release_workflow: dict) -> None:
    """A release step uploads ``dist/*.mcpb`` to the GitHub Release.

    Uses the `softprops/action-gh-release` pattern from the plan (§8),
    but accepts any action whose `files:` input includes the .mcpb
    glob, to leave room for a future swap to a different release
    action.
    """

    found = False
    for job in release_workflow["jobs"].values():
        for step in job.get("steps", []):
            if not isinstance(step, dict):
                continue
            uses = step.get("uses", "")
            if "action-gh-release" not in uses:
                continue
            files = step.get("with", {}).get("files", "")
            if ".mcpb" in str(files):
                found = True
                break
        if found:
            break
    assert found, (
        "release.yml must include a softprops/action-gh-release (or "
        "equivalent) step whose `files:` input matches dist/*.mcpb so "
        "the artifact attaches to the GitHub Release"
    )
