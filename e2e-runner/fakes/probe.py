"""Check the release fixture from inside its container using only Python's stdlib."""

from http.client import HTTPConnection, HTTPException
import json
import sys


def check(port=8080):
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/api/version")
        response = connection.getresponse()
        if response.status != 200:
            print(
                f"Fixture /api/version returned HTTP {response.status}", file=sys.stderr
            )
            return 1
        body = response.read(1025)
        if len(body) <= 1024 and json.loads(body) == {"version": "0.13.0"}:
            return 0
        print("Fixture /api/version returned an unexpected payload", file=sys.stderr)
    except (ValueError, OSError, HTTPException) as exc:
        print(f"Fixture /api/version failed ({type(exc).__name__})", file=sys.stderr)
    finally:
        connection.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(check())
