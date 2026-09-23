"""Migration legacy seed tests for /api/preferences (P2 Tier 2).

Per ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` P2.8 and P2.9, these tests
lock in the contract that the backend is a **passthrough** for the
preferences blob, NOT a migrator. The frontend orchestrates the v1->v2
filter migration (see ``frontend/src/contexts/FilterContext.tsx``).

Allowlist for spec-driven authoring (per TESTING.md section 1):

* ``PLANS/2026.05.07-frontend-api-contract.md`` (clause IDs cited below).
* ``PLANS/2026.05.08 BACKEND TEST PLAN.md`` (the parent plan).
* ``TESTING.md`` sections 5.4 (negative-space) and 5.5 (legacy
  seeds).
* ``backend/tests/conftest.py`` for the ``isolated_data_dir`` and
  ``legacy_v1_prefs`` fixtures.
* ``backend/routers/preferences.py`` lines 50-62 (GET passthrough) and
  108-110 (PATCH per-key overwrite) -- these are the TARGETS.

Bidirectional verification canary (TESTING.md section 2): the
PATCH negative-space test
(:func:`test__patch_preferences__null_legacy_keys__clears_keys_preserves_siblings`)
is the canary. To verify it falsifies, replace the merge loop in
``preferences.py:108-110`` with ``merged = payload.data`` (clobber); the
``g["theme"] == "dark"`` assertion will fail because siblings get
clobbered. Revert after verifying.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test__get_preferences__legacy_v1_blob__bytes_equivalent_passthrough(
    legacy_v1_prefs: Path, client: TestClient
) -> None:
    """PREF-200-PASSTHROUGH: legacy v1 keys returned unchanged on GET.

    Frontend contract clause ``PREF-200-PASSTHROUGH``
    (``PLANS/2026.05.07-frontend-api-contract.md:765``):

        Existing v1-shape filters legacy-keyed (``savedFilters``,
        ``activeFilterIds``) returned bytes-equivalent -- backend is a
        passthrough, NOT a migrator.

    Targets ``backend/routers/preferences.py:50-62`` (``_read_blob``).
    The reader must NOT defensively normalize nested values; only the
    outer ``data`` / ``version`` envelope is validated.
    """

    seed = json.loads(legacy_v1_prefs.read_text())

    resp = client.get("/api/preferences")
    assert resp.status_code == 200
    assert resp.json() == seed, (
        "GET must return the seeded legacy v1 blob unchanged. "
        "Any divergence implies the backend silently normalized legacy "
        "fields (polarity, pinned, activeFilterIds) -- which would break "
        "the frontend's v1->v2 migration contract."
    )


def test__patch_preferences__null_legacy_keys__clears_keys_preserves_siblings(
    legacy_v1_prefs: Path, client: TestClient
) -> None:
    """PREF-PATCH-NULL + PREF-PATCH-NEG-NO-CLOBBER (negative-space).

    Frontend contract clauses
    (``PLANS/2026.05.07-frontend-api-contract.md:805, :815``):

    * ``PREF-PATCH-NULL`` -- explicit ``null`` clears the matching key.
    * ``PREF-PATCH-NEG-NO-CLOBBER`` -- PATCH on key A does NOT alter
      keys B/C/D (negative-space).

    Targets ``backend/routers/preferences.py:108-110`` (the per-key
    overwrite loop ``merged[k] = v`` for each k in payload.data).

    Implementation note: the per-key overwrite SETS the value to
    ``None`` (it does NOT delete the key from the dict). JSON encodes
    ``None`` as ``null`` and decodes ``null`` back to ``None``, so a
    subsequent GET observes the key as present-with-value-``None``.
    The frontend treats null-valued keys as semantically absent
    (``PREF-PATCH-NULL``); we assert the backend-observable state.

    Bidirectional verification (TESTING.md section 2): replace
    the merge loop with ``merged = payload.data``; the sibling
    assertions below fail.
    """

    resp = client.patch(
        "/api/preferences",
        json={"data": {"savedFilters": None, "activeFilterIds": None}},
    )
    assert resp.status_code == 200

    # 1. API round-trip: legacy keys cleared (value is None).
    g = client.get("/api/preferences").json()["data"]
    assert g.get("savedFilters") is None, (
        "PATCH {savedFilters: null} must leave the key value as None "
        "(per-key overwrite). Got: " + repr(g.get("savedFilters"))
    )
    assert g.get("activeFilterIds") is None, (
        "PATCH {activeFilterIds: null} must leave the key value as None. "
        "Got: " + repr(g.get("activeFilterIds"))
    )

    # 2. NEGATIVE-SPACE assertion -- siblings seeded by legacy_v1_prefs
    # (theme, keyboardMode) MUST survive a PATCH targeting other keys.
    # This is the load-bearing assertion against the
    # PREF-PATCH-NEG-NO-CLOBBER contract.
    assert g["theme"] == "dark", (
        "Sibling key 'theme' was clobbered by an unrelated PATCH. "
        "Backend deep-merge contract violated."
    )
    assert g["keyboardMode"] == "vim", (
        "Sibling key 'keyboardMode' was clobbered by an unrelated PATCH. "
        "Backend deep-merge contract violated."
    )

    # 3. Disk-state assertion -- prove the null was *physically written*
    # to disk and not silently dropped by the JSON serializer between
    # the merge and os.replace.
    on_disk = json.loads(legacy_v1_prefs.read_text())["data"]
    assert "savedFilters" in on_disk and on_disk["savedFilters"] is None, (
        "Expected savedFilters: null on disk; got "
        f"{on_disk.get('savedFilters', '<absent>')!r}"
    )
    assert "activeFilterIds" in on_disk and on_disk["activeFilterIds"] is None
    # Siblings on disk too.
    assert on_disk["theme"] == "dark"
    assert on_disk["keyboardMode"] == "vim"


def test__get_preferences__v1_atom_polarity__returned_unchanged(
    legacy_v1_prefs: Path, client: TestClient
) -> None:
    """PREF-200-PASSTHROUGH (P2.9): v1 atom polarity passthrough.

    Frontend contract clause ``PREF-200-PASSTHROUGH``
    (``PLANS/2026.05.07-frontend-api-contract.md:765``).

    Confirms the backend does NOT defensively normalize legacy atom
    shape: a v1 atom with ``polarity: "exclude"`` and NO ``behavior``
    key must come back from GET unchanged. The v1->v2 promotion (which
    rewrites ``polarity`` to ``behavior``) is the FRONTEND's job, per
    ``frontend/src/contexts/FilterContext.tsx:391-401``.

    Targets ``backend/routers/preferences.py:50-62`` (``_read_blob``).
    """

    g = client.get("/api/preferences").json()["data"]
    atom = g["savedFilters"][0]

    # v1 marker present (passthrough confirmed).
    assert atom["polarity"] == "exclude", (
        "Legacy atom 'polarity' field was modified or stripped. "
        "Backend must passthrough nested filter shape verbatim."
    )

    # NEGATIVE SPACE: backend MUST NOT have helpfully filled in the v2
    # 'behavior' field. That promotion is the frontend's responsibility.
    assert "behavior" not in atom, (
        "Backend silently added 'behavior' to a legacy atom -- this "
        "would break the frontend-orchestrated v1->v2 migration "
        "(FilterContext.tsx:391-401)."
    )

    # Other v1 markers still present.
    assert atom["pinned"] is True
    # And the v2 sentinel is still ABSENT (unmigrated state).
    assert "_migratedV2" not in g
