"""The support statement (ETSI EN 303 645 5.3-13): published, the same everywhere,
derived from the newest release, and never already past."""
import sys
from datetime import date

import pytest

from support import paths

pytestmark = pytest.mark.static
sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import support_date as S  # noqa: E402


def test_readme_and_security_state_the_same_date_once_each():
    got = S.stated()
    assert all(len(v) == 1 for v in got.values()), got
    assert len({v[0] for v in got.values()}) == 1, got


def test_the_date_is_what_the_newest_release_commits_to():
    assert S.stated()["SECURITY.md"][0] == S.committed().isoformat(), "run python tools/support_date.py --write"


def test_the_date_is_not_in_the_past():
    assert S.committed() >= date.today(), \
        "the support date has passed: release, or announce the end of support in SECURITY.md first"
