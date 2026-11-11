"""Complete first sign-in inside the newly provisioned disposable backend."""

import os
import secrets

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000/api/v1", timeout=15) as client:
        login = client.post(
            "auth/login",
            data={
                "username": "admin",
                "password": os.environ["INITIAL_ADMIN_PASSWORD"],
            },
        )
        login.raise_for_status()
        session = login.json()
        assert session["must_change_password"] is True
        headers = {"Authorization": "Bearer " + session["access_token"]}
        profile = client.get("auth/me", headers=headers)
        profile.raise_for_status()
        assert (
            profile.json()["username"] == "admin" and profile.json()["role"] == "ADMIN"
        )
        password = secrets.token_urlsafe(36) + "aA1!"
        reset = client.post(
            "auth/first-time-reset",
            headers=headers,
            json={"new_password": password, "confirm_password": password},
        )
        reset.raise_for_status()
        assert reset.status_code == 204
        final = client.post(
            "auth/login", data={"username": "admin", "password": password}
        )
        final.raise_for_status()
        assert final.json()["must_change_password"] is False
        print(
            "First sign-in, forced reset and subsequent sign-in passed on disposable admin."
        )


if __name__ == "__main__":
    main()
