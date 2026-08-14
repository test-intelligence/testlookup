"""Doctor command — one-shot, end-to-end diagnosis of a self-hoster's setup.

``testlookup health`` proxies ``GET /health/ready`` and reports the *server's*
view of its own dependencies. It says nothing about the highest-friction part of
first-run adoption: **is the CLI on this machine pointed at a server it can
actually reach and authenticate against?** A fresh self-hoster who mistyped a
URL, never ran ``auth login``, or whose token expired has three separate ways to
fail before a single command works, and today they discover them one failed
command at a time.

``testlookup doctor`` runs those checks in order and prints a single verdict:

1. **profile** — is a server URL resolved (from the saved profile or the
   ``TESTLOOKUP_URL`` env fallback)?
2. **server** — is that URL reachable, and which build is it running
   (``GET /health/version`` — unauthenticated and probe-free, so it answers even
   when the database is down)?
3. **auth** — are this profile's credentials accepted (an authenticated
   ``GET /api/v1/projects`` probe)?

The checks short-circuit: with no URL there is nothing to reach, and with an
unreachable server there is nothing to authenticate against, so downstream
checks are reported as skipped rather than piling on redundant failures. The
command exits non-zero only when a check hard-**fails**, so it is safe to drop
into a CI preflight; a missing credential is a ``warn`` (the server is up, you
just have not logged in yet) and does not fail the exit code.
"""
import asyncio

import typer

from testlookup_cli import client, config, output
from testlookup_cli.errors import CLIError, EXIT_AUTH

doctor_app = typer.Typer(
    name="doctor",
    help="Diagnose your setup — profile, server reachability, and auth.",
    invoke_without_command=True,
)

# Status vocabulary. Only ``fail`` drives a non-zero exit; ``warn``/``skip`` are
# informational so a not-yet-authenticated but reachable server still exits 0.
_STATUS_FAIL = "fail"
_STATUS_WARN = "warn"
_STATUS_SKIP = "skip"
_STATUS_OK = "ok"


async def _gather_checks(profile_name: str | None) -> list[dict]:
    """Run the setup checks in order and return a list of result dicts.

    Each result is ``{"name", "status", "detail"}``. Later checks are skipped
    once an earlier one makes them moot (no URL → cannot reach; unreachable →
    cannot authenticate).
    """
    results: list[dict] = []

    # 1. Profile / config resolved.
    profile = config.get_profile(profile_name)
    url = (profile.get("url") or "").strip()
    resolved_name = profile.get("name") or config.get_active_profile_name()
    if not url:
        results.append(
            {
                "name": "profile",
                "status": _STATUS_FAIL,
                "detail": (
                    "no server URL configured — run "
                    "'testlookup auth login --url <url>' or set TESTLOOKUP_URL."
                ),
            }
        )
        return results
    results.append(
        {
            "name": "profile",
            "status": _STATUS_OK,
            "detail": f"'{resolved_name}' -> {url}",
        }
    )

    # 2. Reachability + server build identity (unauthenticated, probe-free).
    reachable = False
    try:
        version = await client.request(
            "GET", "/health/version", profile_name=profile_name
        )
        reachable = True
        ver = (version or {}).get("version", "unknown")
        revision = ((version or {}).get("build") or {}).get("revision", "unknown")
        results.append(
            {
                "name": "server",
                "status": _STATUS_OK,
                "detail": f"reachable — v{ver} (build {revision})",
            }
        )
    except CLIError as exc:
        results.append({"name": "server", "status": _STATUS_FAIL, "detail": str(exc)})

    # 3. Authentication — only meaningful if the server answered.
    if not reachable:
        results.append(
            {
                "name": "auth",
                "status": _STATUS_SKIP,
                "detail": "skipped — server unreachable.",
            }
        )
        return results

    has_credential = bool(profile.get("api_key") or profile.get("access_token"))
    if not has_credential:
        results.append(
            {
                "name": "auth",
                "status": _STATUS_WARN,
                "detail": "no credentials in this profile — run 'testlookup auth login'.",
            }
        )
        return results

    auth_type = profile.get("auth_type", "jwt")
    try:
        await client.request(
            "GET", "/api/v1/projects", profile_name=profile_name
        )
        results.append(
            {
                "name": "auth",
                "status": _STATUS_OK,
                "detail": f"{auth_type} credentials accepted.",
            }
        )
    except CLIError as exc:
        # A 401 is a genuine auth failure; anything else (a transient 5xx, a
        # permission quirk) is surfaced as a warning rather than blocking the
        # exit code, since the credentials themselves were not rejected.
        status = _STATUS_FAIL if exc.exit_code == EXIT_AUTH else _STATUS_WARN
        results.append({"name": "auth", "status": status, "detail": str(exc)})

    return results


def _print_human(results: list[dict]) -> None:
    """Render the checklist for a human, one line per check."""
    for r in results:
        line = f"{r['name']}: {r['detail']}"
        status = r["status"]
        if status == _STATUS_OK:
            output.print_success(line)
        elif status == _STATUS_WARN:
            output.print_warning(line)
        elif status == _STATUS_SKIP:
            output.console.print(f"[dim]- {line}[/]")
        else:
            output.print_error(line)


@doctor_app.callback(invoke_without_command=True)
def doctor(
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Diagnose the local TestLookup setup end-to-end.

    Exits non-zero if any check hard-fails (no URL, unreachable server, or
    rejected credentials), so it doubles as a CI preflight.
    """
    results = asyncio.run(_gather_checks(profile_name))

    if output_format in ("json", "yaml"):
        output.render({"checks": results}, output_format)
    else:
        _print_human(results)

    if any(r["status"] == _STATUS_FAIL for r in results):
        raise typer.Exit(1)
