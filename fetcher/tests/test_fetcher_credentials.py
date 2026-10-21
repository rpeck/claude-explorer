"""``fetcher_credentials``: one place that turns credentials into the
``ClaudeFetcher`` arguments (issue #1 follow-up, 2026-10-21).

Issue #1 was a crash: ``claude-explorer fetch`` still passed the v1
``org_id=`` keyword after ``ClaudeFetcher`` moved to ``orgs`` plus
``primary_org_id``. Four other callers had their own copies of the same
conversion, and those copies still disagreed:

- The three web routes refused a v2 file without the legacy ``org_id``
  mirror, although the v2 schema calls that field a mirror to remove.
- ``claude-explorer fetch --org-id X`` ignored ``X`` with a v2 file.

Every caller now uses this function, so the conversion exists once.
"""

from __future__ import annotations

import pytest

from fetcher.credentials import (
    CredentialsCorruptError,
    FetcherCredentials,
    fetcher_credentials,
)

ORG_A = {"uuid": "org-A", "name": "Personal", "capabilities": ["chat"], "seen_in_response": True}
ORG_B = {"uuid": "org-B", "name": "Work", "capabilities": ["chat"], "seen_in_response": True}


def _v2(**overrides) -> dict:
    creds = {
        "schema_version": 2,
        "session_key": "sk-test",
        "cf_bm": "bm",
        "cf_clearance": "clear",
        "captured_at": "2026-10-21T00:00:00Z",
        "orgs": [ORG_A, ORG_B],
        "primary_org_id": "org-A",
    }
    creds.update(overrides)
    return creds


def test_v2_without_the_org_id_mirror() -> None:
    args = fetcher_credentials(_v2())
    assert args == FetcherCredentials(
        session_key="sk-test",
        orgs=[ORG_A, ORG_B],
        primary_org_id="org-A",
        cf_bm="bm",
        cf_clearance="clear",
    )


def test_v2_with_the_org_id_mirror_uses_primary_org_id() -> None:
    args = fetcher_credentials(_v2(org_id="org-A", primary_org_id="org-B"))
    assert args.primary_org_id == "org-B"


def test_v2_without_primary_org_id_resolves_one_like_the_capture() -> None:
    creds = _v2()
    del creds["primary_org_id"]
    assert fetcher_credentials(creds).primary_org_id == "org-A"


def test_v1_becomes_one_org() -> None:
    args = fetcher_credentials({"session_key": "sk-v1", "org_id": "org-1"})
    assert args.session_key == "sk-v1"
    assert [o["uuid"] for o in args.orgs] == ["org-1"]
    assert args.primary_org_id == "org-1"
    assert args.cf_bm is None and args.cf_clearance is None


def test_org_id_override_selects_a_known_org() -> None:
    args = fetcher_credentials(_v2(), org_id="org-B")
    assert args.primary_org_id == "org-B"
    assert args.orgs == [ORG_A, ORG_B]


def test_org_id_override_for_an_unknown_org_fetches_only_that_org() -> None:
    args = fetcher_credentials(_v2(), org_id="org-Z")
    assert [o["uuid"] for o in args.orgs] == ["org-Z"]
    assert args.primary_org_id == "org-Z"


def test_overrides_alone_need_no_file() -> None:
    args = fetcher_credentials({}, session_key="sk-cli", org_id="org-cli")
    assert args.session_key == "sk-cli"
    assert [o["uuid"] for o in args.orgs] == ["org-cli"]
    assert args.primary_org_id == "org-cli"


def test_session_key_override_wins() -> None:
    assert fetcher_credentials(_v2(), session_key="sk-cli").session_key == "sk-cli"


def test_kwargs_match_the_claude_fetcher_signature(tmp_path) -> None:
    from fetcher.bulk_fetch import ClaudeFetcher

    fetcher = ClaudeFetcher(
        **fetcher_credentials(_v2()).fetcher_kwargs(),
        output_dir=tmp_path / "out",
        files_dir=tmp_path / "files",
    )
    assert fetcher.primary_org_id == "org-A"
    assert fetcher.session_key == "sk-test"


@pytest.mark.parametrize(
    ("creds", "fragment"),
    [
        ({"org_id": "org-1"}, "session_key"),
        (_v2(session_key=""), "session_key"),
        ({"session_key": "sk"}, "org"),
        (_v2(orgs=[]), "org"),
        (_v2(primary_org_id="org-Z"), "org-Z"),
        (_v2(orgs=[{"name": "no uuid"}]), "uuid"),
    ],
)
def test_unusable_credentials_raise_a_clear_error(creds: dict, fragment: str) -> None:
    with pytest.raises(CredentialsCorruptError, match=fragment):
        fetcher_credentials(creds)
