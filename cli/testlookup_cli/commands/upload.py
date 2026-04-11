"""Upload test result files to TestLookup for ingestion and AI analysis."""
import asyncio
from pathlib import Path
from typing import Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.config import get_profile

upload_app = typer.Typer(name="upload", help="Upload test result files")


@upload_app.command("file")
def upload_file(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Path to test result file"),
    project: str = typer.Option(..., "--project", "-p", help="Project ID (UUID)"),
    build: str = typer.Option(..., "--build", "-b", help="Build number / identifier"),
    branch: Optional[str] = typer.Option(None, "--branch", help="Git branch"),
    commit: Optional[str] = typer.Option(None, "--commit", help="Git commit SHA"),
    release: Optional[str] = typer.Option(None, "--release", help="Release name"),
    format: str = typer.Option("auto", "--format", "-f", help="File format: auto|junit|testng|allure"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload a single test result file (JUnit XML, TestNG XML, or Allure JSON).

    The file is parsed and ingested asynchronously on the server.
    AI analysis is triggered automatically after ingestion completes.

    Examples:
        testlookup upload file results.xml -p <project-id> -b build-42
        testlookup upload file allure.json -p <project-id> -b v2.5.0 --format allure
    """
    try:
        data = asyncio.run(_upload_file(
            path=path, project=project, build=build,
            branch=branch, commit=commit, release=release,
            format=format, profile_name=profile_name,
        ))
        output.render(data, output_format)
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
    format: str = typer.Option("auto", "--format", "-f"),
    profile_name: Optional[str] = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Upload all test result files (.xml, .json) in a directory.

    Each file is uploaded as a separate ingestion job.

    Examples:
        testlookup upload dir ./target/surefire-reports -p <project-id> -b build-42
        testlookup upload dir ./allure-results -p <project-id> -b v2.5.0 --format allure
    """
    files = sorted(
        list(directory.glob("*.xml")) + list(directory.glob("*.json"))
    )
    if not files:
        output.print_error(f"No .xml or .json files found in {directory}")
        raise typer.Exit(1)

    results = []
    errors = 0
    for f in files:
        try:
            data = asyncio.run(_upload_file(
                path=f, project=project, build=build,
                branch=branch, commit=commit, release=release,
                format=format, profile_name=profile_name,
            ))
            results.append(data)
        except Exception as e:
            output.print_error(f"Failed: {f.name} — {e}")
            errors += 1

    if results:
        output.render(results, output_format)
    output.print_success(f"Uploaded {len(results)}/{len(files)} files ({errors} errors)")


async def _upload_file(
    path: Path,
    project: str,
    build: str,
    branch: Optional[str] = None,
    commit: Optional[str] = None,
    release: Optional[str] = None,
    format: str = "auto",
    profile_name: Optional[str] = None,
) -> dict:
    """Upload a single file via multipart POST to /api/v1/ingest/file."""
    import httpx

    profile = get_profile(profile_name)
    base_url = profile.get("url", "http://localhost:8000").rstrip("/")
    headers = client._build_headers(profile)

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
