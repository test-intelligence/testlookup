"""Complete first sign-in inside the newly provisioned disposable backend."""

import os
import secrets
import time

import httpx


BASE = "http://127.0.0.1:8000/api/v1/"
TRANSIENT_READ = (httpx.ReadTimeout, httpx.ReadError)


def request(method, path, *, attempts=3, **kwargs):
    for attempt in range(1, attempts + 1):
        try:
            # httpx.request opens a fresh connection for each attempt.
            return httpx.request(
                method,
                BASE + path,
                timeout=httpx.Timeout(45, connect=5),
                trust_env=False,
                **kwargs,
            )
        except TRANSIENT_READ:
            if attempt == attempts:
                raise
            print(
                f"Transient read on {path}; retrying ({attempt}/{attempts}).",
                flush=True,
            )
            time.sleep(attempt)


def login(password):
    return request(
        "POST", "auth/login", data={"username": "admin", "password": password}
    )


def session_identity(login_response, *, must_change):
    login_response.raise_for_status()
    session = login_response.json()
    assert session["must_change_password"] is must_change
    headers = {"Authorization": "Bearer " + session["access_token"]}
    profile = request("GET", "auth/me", headers=headers)
    profile.raise_for_status()
    identity = profile.json()
    assert (identity["username"], identity["role"]) == ("admin", "ADMIN")
    return headers, identity["id"]


def main():
    initial = os.environ["INITIAL_ADMIN_PASSWORD"]
    headers, admin_id = session_identity(login(initial), must_change=True)
    password = secrets.token_urlsafe(36) + "aA1!"
    payload = {"new_password": password, "confirm_password": password}
    for attempt in (1, 2):
        try:
            reset = request(
                "POST",
                "auth/first-time-reset",
                attempts=1,
                headers=headers,
                json=payload,
            )
        except TRANSIENT_READ:
            # A lost response may have followed a committed reset. Try the new
            # credential before considering a second write with the same password.
            reset = None
        if reset is not None:
            if attempt == 1 or reset.status_code not in (401, 403):
                reset.raise_for_status()
                assert reset.status_code == 204
        final = login(password)
        if reset is None and final.status_code == 401 and attempt == 1:
            prior = login(initial)
            if prior.status_code == 401:
                # The original write could have committed between the two logins.
                final = login(password)
            else:
                headers, prior_id = session_identity(prior, must_change=True)
                assert prior_id == admin_id
                continue
        _, final_id = session_identity(final, must_change=False)
        assert final_id == admin_id
        print(
            "First sign-in, forced reset and subsequent sign-in passed on disposable admin."
        )
        return


if __name__ == "__main__":
    main()
