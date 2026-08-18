"""Upload test result files to TestLookup for ingestion and AI analysis."""
import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.ci_context import resolve_ci_context
from testlookup_cli.commit_range import resolve_commit_range
from testlookup_cli.config import get_profile
from testlookup_cli.errors import map_connection_error

upload_app = typer.Typer(name="upload", help="Upload test result files")


@upload_app.command("file")
def upload_file(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to test result file"),
    project: str = typer.Option(..., "--project", "-p", help="Project ID (UUID)"),
    build: str = typer.Option(..., "--build", "-b", help="Build number / identifier"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Git branch"),
    commit: Optional[str] = typer.Option(None, "--commit", help="Git commit SHA"),
    release: Optional[str] = typer.Option(None, "--release", help="Release name"),
    ci_provider: Optional[str] = typer.Option(None, "--ci-provider", help="CI provider (overrides auto-detection)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository as org/name (overrides auto-detection)"),
    pr_number: Optional[int] = typer.Option(None, "--pr-number", min=1, help="Pull/merge request number (overrides auto-detection)"),
    ci_actor: Optional[str] = typer.Option(None, "--ci-actor", help="CI actor / triggering user (overrides auto-detection)"),
    ci_run_url: Optional[str] = typer.Option(None, "--ci-run-url", help="CI run/job URL (overrides auto-detection)"),
    commit_range_base: Optional[str] = typer.Option(
        None, "--commit-range-base",
        help="Base ref/SHA for commit collection (overrides CI + local-git detection)",
    ),
    no_commit_range: bool = typer.Option(
        False, "--no-commit-range",
        help="Skip collecting the commit range from local git",
    ),
    format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="File format: auto|junit|testng|allure|cypress|playwright|pytest|robot|cucumber|nunit|trx|xunit",
    ),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload a single test result file.

    Accepts JUnit/TestNG XML, Allure JSON, Cypress (Mochawesome) JSON,
    Playwright JSON, pytest --json-report, Robot Framework output.xml,
    Cucumber JSON, NUnit3 XML, Visual Studio TRX, or xUnit.net v2 XML.
    ``--format auto`` (default) detects the format from the file content.

    CI context (provider, repo, PR number, actor, run URL) is auto-detected
    from standard CI env vars (GitHub Actions, GitLab CI, Jenkins, Azure
    DevOps, CircleCI); the --ci-provider/--repo/--pr-number/--ci-actor/
    --ci-run-url options override detection.

    The commit range (base..HEAD, with per-commit changed files) is collected
    from local git so failures can be attributed to commits with no VCS token
    and no network. --commit-range-base pins the base; --no-commit-range (or
    TESTLOOKUP_COMMIT_RANGE=0) skips collection entirely. Any git problem is
    non-fatal — the range is simply omitted.

    The file is parsed and ingested asynchronously on the server.
    AI analysis is triggered automatically after ingestion completes.

    Examples:
        testlookup upload file results.xml -p <project-id> -b build-42
        testlookup upload file allure.json -p <project-id> -b v2.5.0 --format allure
        testlookup upload file results.xml -p <project-id> -b build-42 --pr-number 421
        testlookup upload file results.xml -p <project-id> -b build-42 --commit-range-base origin/main
    """
    try:
        commit_range = resolve_commit_range(
            explicit_base=commit_range_base,
            enabled=False if no_commit_range else None,
        )
        data = asyncio.run(_upload_file(
            path=path, project=project, build=build,
            branch=branch, commit=commit, release=release,
            ci_provider=ci_provider, ci_repo=repo, pr_number=pr_number,
            ci_actor=ci_actor, ci_run_url=ci_run_url,
            commit_range=commit_range,
            format=format, profile_name=profile_name,
        ))
        output.render(data, output_format)
        # Keep structured stdout parseable (`upload ... --output json | jq -r
        # '.run_id'` feeds ci-verdict in CI recipes) — the run_id is already
        # in the rendered JSON/YAML document.
        if output_format not in ("json", "yaml"):
            run_id = data.get("run_id", "?")
            output.print_success(f"Ingestion queued — run_id={run_id}")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


@upload_app.command("dir")
def upload_dir(
    directory: Path = typer.Argument(..., exists=True, file_okay=False, help="Directory of result files"),
    project: str = typer.Option(..., "--project", "-p", help="Project ID (UUID)"),
    build: str = typer.Option(..., "--build", "-b", help="Build number / identifier"),
    branch: Optional[str] = typer.Option(None, "--branch"),
    commit: Optional[str] = typer.Option(None, "--commit"),
    release: Optional[str] = typer.Option(None, "--release"),
    ci_provider: Optional[str] = typer.Option(None, "--ci-provider", help="CI provider (overrides auto-detection)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="Repository as org/name (overrides auto-detection)"),
    pr_number: Optional[int] = typer.Option(None, "--pr-number", min=1, help="Pull/merge request number (overrides auto-detection)"),
    ci_actor: Optional[str] = typer.Option(None, "--ci-actor", help="CI actor / triggering user (overrides auto-detection)"),
    ci_run_url: Optional[str] = typer.Option(None, "--ci-run-url", help="CI run/job URL (overrides auto-detection)"),
    commit_range_base: Optional[str] = typer.Option(
        None, "--commit-range-base",
        help="Base ref/SHA for commit collection (overrides CI + local-git detection)",
    ),
    no_commit_range: bool = typer.Option(
        False, "--no-commit-range",
        help="Skip collecting the commit range from local git",
    ),
    format: str = typer.Option(
        "auto", "--format", "-f",
        help="File format: auto|junit|testng|allure|cypress|playwright|pytest|robot|cucumber|nunit|trx|xunit",
    ),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload all test result files (.xml, .trx, .json) in a directory.

    Each file is uploaded as a separate ingestion job. CI context is
    auto-detected from standard CI env vars (override with --ci-provider /
    --repo / --pr-number / --ci-actor / --ci-run-url).

    The commit range is collected from local git ONCE and stamped on every
    file in the directory (they are all one execution of one checkout). Pin it
    with --commit-range-base, or skip it with --no-commit-range.

    Examples:
        testlookup upload dir ./target/surefire-reports -p <project-id> -b build-42
        testlookup upload dir ./allure-results -p <project-id> -b v2.5.0 --format allure
    """
    files = sorted(
        list(directory.glob("*.xml")) + list(directory.glob("*.json")) + list(directory.glob("*.trx"))
    )
    if not files:
        output.print_error(f"No .xml or .json files found in {directory}")
        raise typer.Exit(1)

    # Collected once: every file here came from the same checkout, and N git
    # walks for one range would be pure waste.
    commit_range = resolve_commit_range(
        explicit_base=commit_range_base,
        enabled=False if no_commit_range else None,
    )

    results = []
    errors = 0
    for f in files:
        try:
            data = asyncio.run(_upload_file(
                path=f, project=project, build=build,
                branch=branch, commit=commit, release=release,
                ci_provider=ci_provider, ci_repo=repo, pr_number=pr_number,
                ci_actor=ci_actor, ci_run_url=ci_run_url,
                commit_range=commit_range,
                format=format, profile_name=profile_name,
            ))
            results.append(data)
        except Exception as e:
            output.print_error(f"Failed: {f.name} — {e}")
            errors += 1

    if results:
        output.render(results, output_format)

    summary = f"Uploaded {len(results)}/{len(files)} files ({errors} errors)"
    if errors:
        # Any failed upload fails the command. A CI ingest step reads the exit
        # code, and returning 0 here let reports silently never land while the
        # pipeline went green — the same false-success trap the empty-directory
        # guard above avoids. Mirrors `upload file`, which exits non-zero on its
        # single failure.
        output.print_error(summary)
        raise typer.Exit(1)
    output.print_success(summary)


def _build_form_data(
    project: str,
    build: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    release: Optional[str] = None,
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_actor: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    commit_range: Optional[list] = None,
    format: str = "auto",
) -> dict:
    """Assemble the multipart form fields for POST /api/v1/ingest/file.

    CI context (US-4.3b) is auto-detected from standard CI env vars; the
    explicit ci_* arguments (CLI flags) always win over detection.

    ``commit_range`` (US-8.1) is already-collected local-git output; the
    server takes it as a JSON array string on the multipart form.
    """
    form_data = {
        "project_id": project,
        "build_number": build,
        "format": format,
    }
    if branch:
        form_data["branch"] = branch
    if commit:
        form_data["commit_hash"] = commit
    if release:
        form_data["release_name"] = release

    ci_context = resolve_ci_context({
        "ci_provider": ci_provider,
        "ci_repo": ci_repo,
        "pr_number": pr_number,
        "ci_actor": ci_actor,
        "ci_run_url": ci_run_url,
    })
    for field, value in ci_context.items():
        form_data[field] = str(value)

    if commit_range:
        form_data["commit_range"] = json.dumps(commit_range)

    return form_data


async def _upload_file(
    path: Path,
    project: str,
    build: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    release: Optional[str] = None,
    ci_provider: Optional[str] = None,
    ci_repo: Optional[str] = None,
    pr_number: Optional[int] = None,
    ci_actor: Optional[str] = None,
    ci_run_url: Optional[str] = None,
    commit_range: Optional[list] = None,
    format: str = "auto",
    profile_name: Optional[str] = None,
) -> dict:
    """Upload a single file via multipart POST to /api/v1/ingest/file."""
    import httpx

    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    headers = client._build_headers(profile)

    form_data = _build_form_data(
        project=project, build=build, branch=branch, commit=commit,
        release=release, ci_provider=ci_provider, ci_repo=ci_repo,
        pr_number=pr_number, ci_actor=ci_actor, ci_run_url=ci_run_url,
        commit_range=commit_range, format=format,
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as http:
            with open(path, "rb") as f:
                resp = await http.post(
                    f"{base_url}/api/v1/ingest/file",
                    headers=headers,
                    files={"file": (path.name, f, "application/octet-stream")},
                    data=form_data,
                )

            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                raise Exception(f"HTTP {resp.status_code}: {detail}")

            return resp.json()
    except httpx.RequestError as exc:
        # Server unreachable / DNS failure / timeout on the primary ingest path
        # (the first thing a new self-hoster runs) — give an actionable hint
        # rather than a raw transport traceback.
        raise map_connection_error(exc, base_url) from exc
