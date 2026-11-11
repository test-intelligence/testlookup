from collections import deque

import httpx
import pytest

import bootstrap_admin


INITIAL = (200, {"must_change_password": True, "access_token": "first"})
FINAL = (200, {"must_change_password": False, "access_token": "after"})
PROFILE = (200, {"username": "admin", "role": "ADMIN", "id": "fixture-user"})
RESET = (204, None)
DENIED = (401, {})
TIMEOUT = httpx.ReadTimeout("simulated lost response")


def script(monkeypatch, replies):
    pending = deque(replies)
    calls = []
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "old")
    monkeypatch.setattr(bootstrap_admin.time, "sleep", lambda seconds: None)

    def request(method, url, **kwargs):
        path = url.removeprefix(bootstrap_admin.BASE)
        if path == "auth/login":
            credential = kwargs["data"]["password"]
        elif path == "auth/first-time-reset":
            credential = kwargs["json"]["new_password"]
            assert credential == kwargs["json"]["confirm_password"]
        else:
            credential = kwargs["headers"]["Authorization"]
        calls.append((path, credential))
        reply = pending.popleft()
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(bootstrap_admin.httpx, "request", request)
    return calls, pending


@pytest.mark.parametrize(
    ("replies", "writes"),
    [
        ([TIMEOUT, INITIAL, PROFILE, RESET, FINAL, PROFILE], 1),
        ([INITIAL, PROFILE, TIMEOUT, FINAL, PROFILE], 1),
        (
            [
                INITIAL,
                PROFILE,
                TIMEOUT,
                DENIED,
                INITIAL,
                PROFILE,
                RESET,
                FINAL,
                PROFILE,
            ],
            2,
        ),
        ([INITIAL, PROFILE, TIMEOUT, DENIED, DENIED, FINAL, PROFILE], 1),
        (
            [
                INITIAL,
                PROFILE,
                TIMEOUT,
                DENIED,
                INITIAL,
                PROFILE,
                (403, {}),
                FINAL,
                PROFILE,
            ],
            2,
        ),
    ],
)
def test_first_sign_in_recovers_only_with_verified_credentials(
    monkeypatch, replies, writes
):
    calls, pending = script(monkeypatch, replies)
    bootstrap_admin.main()
    assert not pending
    passwords = [value for path, value in calls if path == "auth/first-time-reset"]
    assert len(passwords) == writes
    assert len(set(passwords)) == 1 and passwords[0] != "old"
    assert calls[:1] == [("auth/login", "old")]
    assert calls[-2:] == [("auth/login", passwords[0]), ("auth/me", "Bearer after")]


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        ([TIMEOUT] * 3, httpx.ReadTimeout),
        ([DENIED], httpx.HTTPStatusError),
        ([FINAL], AssertionError),
        ([INITIAL, (200, {"username": "admin", "role": "VIEWER"})], AssertionError),
        ([INITIAL, PROFILE, (403, {})], httpx.HTTPStatusError),
        ([INITIAL, PROFILE, (200, {})], AssertionError),
        ([INITIAL, PROFILE, TIMEOUT, INITIAL], AssertionError),
        (
            [INITIAL, PROFILE, RESET, FINAL, (200, {**PROFILE[1], "id": "another"})],
            AssertionError,
        ),
    ],
)
def test_first_sign_in_fails_closed(monkeypatch, replies, expected):
    _, pending = script(monkeypatch, replies)
    with pytest.raises(expected):
        bootstrap_admin.main()
    assert not pending
