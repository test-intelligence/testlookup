"""Test case commands — list, get."""
import asyncio
import typer

from testlookup_cli import client, output

tests_app = typer.Typer(name="tests", help="Inspect test cases")

# Step statuses that count as a failure (mirror backend verdict vocab).
_FAILED_STEP_STATUSES = {"FAILED", "BROKEN"}


@tests_app.command("list")
def list_tests(
    run_id: str = typer.Argument(..., help="Run ID"),
    status: str = typer.Option(None, "--status", "-s"),
    suite: str = typer.Option(None, "--suite"),
    page: int = typer.Option(1, "--page"),
    size: int = typer.Option(50, "--size"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List test cases for a run."""
    params: dict = {"page": page, "size": size}
    if status:
        params["status"] = status
    if suite:
        params["suite"] = suite

    try:
        data = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}/test-cases", params=params, profile_name=profile_name))
        items = data.get("items", []) if isinstance(data, dict) else data
        output.render(
            items, output_format,
            columns=["id", "test_name", "suite_name", "status", "duration_ms", "failure_category"],
            title="Test Cases",
        )
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)


def _flatten_steps(nodes: list) -> list[dict]:
    """Flatten the nested step tree into a pre-order (ordinal) list."""
    flat: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        flat.append(node)
        flat.extend(_flatten_steps(node.get("steps") or []))
    return flat


@tests_app.command("get")
def get_test(
    run_id: str = typer.Argument(..., help="Run ID"),
    test_id: str = typer.Argument(..., help="Test case ID"),
    show_steps: bool = typer.Option(False, "--show-steps", help="Fetch and summarise the granular step tree"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Get a single test case. Use --show-steps to summarise the granular step tree."""
    try:
        data = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}/tests/{test_id}", profile_name=profile_name))
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(1)

    if not show_steps:
        output.render(data, output_format)
        return

    structured = output_format in ("json", "yaml")

    # Best-effort: older runs have no captured steps; degrade gracefully.
    summary = None
    try:
        tree = asyncio.run(client.request("GET", f"/api/v1/runs/{run_id}/tests/{test_id}/steps", profile_name=profile_name))
        steps = _flatten_steps(tree.get("steps", []) if isinstance(tree, dict) else [])
        if steps:
            failing = next((s for s in steps if str(s.get("status", "")).upper() in _FAILED_STEP_STATUSES), None)
            summary = {
                "step_count": len(steps),
                "first_step": steps[0].get("name", "—"),
                "last_step": steps[-1].get("name", "—"),
                "failing_step": failing.get("name") if failing else "—",
                "failing_assertion": (failing.get("assertion_message") if failing else None) or "—",
            }
    except Exception as e:
        # Warnings go to stderr, so structured stdout stays parseable.
        output.print_warning(f"Could not fetch steps: {e}")

    if structured:
        # Emit a SINGLE parseable document with the steps folded in.
        if isinstance(data, dict):
            data = {**data, "steps_summary": summary}
        output.render(data, output_format)
        return

    output.render(data, output_format)
    if summary is not None:
        output.render(summary, "table")
    else:
        output.print_warning("No granular steps captured for this test.")
