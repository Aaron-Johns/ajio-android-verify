"""qa/cohort_client.py: guest-token caching/refresh, cohort fetch, and user-groups resolution -
all against a fake session, no live network (see analysis/FINDINGS.md 6.10 for the real endpoints)."""
import json
import time

import pytest

from qa import cohort_client


class FakeResp:
    def __init__(self, status_code, body):
        self.status_code, self._body, self.headers, self.text = status_code, body, {}, json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    """post()/get() each pop the next preset response off their own queue, in call order."""

    def __init__(self, posts=(), gets=()):
        self.posts, self.gets = list(posts), list(gets)
        self.post_calls, self.get_calls = [], []

    def post(self, url, headers=None, data=None, timeout=None):
        self.post_calls.append((url, headers, data))
        return self.posts.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        self.get_calls.append((url, params, headers))
        return self.gets.pop(0)


GUEST_OK = {"access_token": "guest-jwt-abc", "token_type": "bearer", "expires_in": 1_000_000, "scope": "extended"}
COHORT_OK = {
    "status": {"statusCode": 0},
    "userSegmentIdSet": ["13", "19", "10"],
    "userCohortValue": {
        "rilfnl_v1": {
            "genesys": {"cohorts": "nontransacted"},
            "plp": {"cohorts": "nontransacted|p_null,false,unisex,noasp"},
        },
    },
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cohort_client, "CACHE_PATH", tmp_path / "guest_token.json")
    # Tests default to the guest flow regardless of whether the real .env has a personal
    # AJIO_ACCOUNT_TOKEN set; tests that exercise that path set it back explicitly.
    monkeypatch.delenv("AJIO_ACCOUNT_TOKEN", raising=False)


def test_fetch_guest_token_caches_to_disk_and_reuses_it():
    session = FakeSession(posts=[FakeResp(200, GUEST_OK)])
    token = cohort_client.fetch_guest_token(session=session)
    assert token == "guest-jwt-abc"
    assert json.loads(cohort_client.CACHE_PATH.read_text())["access_token"] == "guest-jwt-abc"

    # Second call: no more posts queued, so this only passes if the cache was actually used.
    again = cohort_client.fetch_guest_token(session=FakeSession())
    assert again == "guest-jwt-abc"


def test_fetch_guest_token_refetches_once_the_cached_token_is_near_expiry():
    cohort_client.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cohort_client.CACHE_PATH.write_text(json.dumps({"access_token": "stale", "expires_at": time.time() + 10}))
    session = FakeSession(posts=[FakeResp(200, {**GUEST_OK, "access_token": "fresh-jwt"})])
    assert cohort_client.fetch_guest_token(session=session) == "fresh-jwt"


def test_fetch_guest_token_force_refresh_bypasses_a_valid_cache():
    session1 = FakeSession(posts=[FakeResp(200, GUEST_OK)])
    cohort_client.fetch_guest_token(session=session1)
    session2 = FakeSession(posts=[FakeResp(200, {**GUEST_OK, "access_token": "forced-fresh"})])
    assert cohort_client.fetch_guest_token(session=session2, force_refresh=True) == "forced-fresh"


def test_fetch_guest_token_raises_cohort_error_on_bad_status():
    session = FakeSession(posts=[FakeResp(500, {})])
    with pytest.raises(cohort_client.CohortError):
        cohort_client.fetch_guest_token(session=session)


def test_fetch_guest_token_raises_cohort_error_when_no_access_token_in_body():
    session = FakeSession(posts=[FakeResp(200, {"token_type": "bearer"})])
    with pytest.raises(cohort_client.CohortError):
        cohort_client.fetch_guest_token(session=session)


def test_fetch_cohort_bootstraps_its_own_guest_token_when_none_given():
    session = FakeSession(posts=[FakeResp(200, GUEST_OK)], gets=[FakeResp(200, COHORT_OK)])
    data = cohort_client.fetch_cohort("dev-1", session=session)
    assert data == COHORT_OK
    assert session.get_calls[0][2]["authorization"] == "Bearer guest-jwt-abc"
    assert session.get_calls[0][2]["device-id"] == "dev-1"


def test_fetch_cohort_retries_once_with_a_fresh_token_on_401():
    session = FakeSession(
        posts=[FakeResp(200, GUEST_OK), FakeResp(200, {**GUEST_OK, "access_token": "refreshed"})],
        gets=[FakeResp(401, {"error": "expired"}), FakeResp(200, COHORT_OK)],
    )
    data = cohort_client.fetch_cohort("dev-1", session=session)
    assert data == COHORT_OK
    assert session.get_calls[1][2]["authorization"] == "Bearer refreshed"


def test_fetch_cohort_raises_on_a_second_401_rather_than_retrying_forever():
    session = FakeSession(
        posts=[FakeResp(200, GUEST_OK), FakeResp(200, {**GUEST_OK, "access_token": "still-bad"})],
        gets=[FakeResp(401, {}), FakeResp(401, {})],
    )
    with pytest.raises(cohort_client.CohortError):
        cohort_client.fetch_cohort("dev-1", session=session)


def test_resolve_user_groups_extracts_and_reformats_the_cohort_string():
    session = FakeSession(posts=[FakeResp(200, GUEST_OK)], gets=[FakeResp(200, COHORT_OK)])
    assert cohort_client.resolve_user_groups("dev-1", session=session) == "l1:nontransacted|l2:p_null,false,unisex,noasp"


def test_resolve_user_groups_raises_for_an_l1_only_context_like_genesys():
    session = FakeSession(posts=[FakeResp(200, GUEST_OK)], gets=[FakeResp(200, COHORT_OK)])
    with pytest.raises(cohort_client.CohortError):
        cohort_client.resolve_user_groups("dev-1", context="genesys", session=session)


def test_resolve_user_groups_raises_for_an_unknown_vertical_or_context():
    session = FakeSession(posts=[FakeResp(200, GUEST_OK)], gets=[FakeResp(200, COHORT_OK)])
    with pytest.raises(cohort_client.CohortError):
        cohort_client.resolve_user_groups("dev-1", vertical="no_such_vertical", session=session)


# ---- AJIO_ACCOUNT_TOKEN: a real logged-in account's session instead of the anonymous guest flow ----

def test_account_token_is_used_instead_of_bootstrapping_a_guest_token(monkeypatch):
    monkeypatch.setenv("AJIO_ACCOUNT_TOKEN", "real-account-jwt")
    session = FakeSession(gets=[FakeResp(200, COHORT_OK)])  # no posts queued - a guest bootstrap would fail
    data = cohort_client.fetch_cohort("dev-1", session=session)
    assert data == COHORT_OK
    assert session.get_calls[0][2]["authorization"] == "Bearer real-account-jwt"


def test_an_explicit_guest_token_argument_still_wins_over_the_account_token(monkeypatch):
    monkeypatch.setenv("AJIO_ACCOUNT_TOKEN", "real-account-jwt")
    session = FakeSession(gets=[FakeResp(200, COHORT_OK)])
    cohort_client.fetch_cohort("dev-1", guest_token="explicit-token", session=session)
    assert session.get_calls[0][2]["authorization"] == "Bearer explicit-token"


def test_a_rejected_account_token_raises_instead_of_silently_falling_back_to_guest(monkeypatch):
    monkeypatch.setenv("AJIO_ACCOUNT_TOKEN", "expired-account-jwt")
    session = FakeSession(gets=[FakeResp(401, {"error": "expired"})])  # no posts queued either
    with pytest.raises(cohort_client.CohortError, match="AJIO_ACCOUNT_TOKEN"):
        cohort_client.fetch_cohort("dev-1", session=session)
    assert len(session.get_calls) == 1  # no retry attempted
