"""Review commands — list, accept, reject (architecture E8.4, section 8.3).

Accepting or rejecting an AI report records that a person vouches for it, or
not. The server refuses both to API keys. The CLI refuses them up front on an
API-key profile as well, with a message saying why, instead of sending a
request that can only come back 403.
"""
import asyncio
from typing import Optional

import typer

from testlookup_cli import client, output
from testlookup_cli.config import get_profile
from testlookup_cli.errors import exit_code_for

reviews_app = typer.Typer(name="reviews", help="Human review of AI-generated reports")

REASON_CODES = (
    "wrong_category", "unsupported_claim", "missing_evidence",
    "contradiction", "stale_data", "other",
)
STATES = ("pending_review", "accepted", "rejected", "superseded", "all")
_COLUMNS = ["id", "kind", "subject_type", "subject_id", "workflow_type", "state", "created_at"]

API_KEY_REFUSAL = (
    "Accepting or rejecting a review needs a signed-in user: an API key cannot "
    "vouch for an AI report. Run `testlookup auth login` and try again."
)


def _require_user_login(profile_name: Optional[str]) -> None:
    profile = get_profile(profile_name)
    if profile.get("auth_type") == "api_key" and profile.get("api_key"):
        output.print_error(API_KEY_REFUSAL)
        raise typer.Exit(1)


@reviews_app.command("list")
def list_reviews(
    project_id: str = typer.Argument(..., help="Project ID"),
    state: str = typer.Option("pending_review", "--state", help="pending_review|accepted|rejected|superseded|all"),
    limit: int = typer.Option(50, "--limit", min=1, max=200),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """List a project's AI-report reviews (the open queue by default)."""
    if state not in STATES:
        output.print_error(f"Unknown state '{state}'. Use one of: {', '.join(STATES)}.")
        raise typer.Exit(1)
    params: dict = {"limit": limit}
    if state != "all":
        params["state"] = state
    try:
        rows = asyncio.run(client.request(
            "GET", f"/api/v1/projects/{project_id}/reviews", params=params, profile_name=profile_name,
        ))
        output.render(rows, output_format, columns=_COLUMNS, title=f"Reviews ({state})")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@reviews_app.command("accept")
def accept(
    review_id: str = typer.Argument(..., help="Review ID"),
    notes: str = typer.Option(None, "--notes", help="Why the report holds up"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Accept an AI report. Its pipeline run becomes passed."""
    _require_user_login(profile_name)
    try:
        data = asyncio.run(client.request(
            "POST", f"/api/v1/reviews/{review_id}/accept",
            json_body={"notes": notes} if notes else None, profile_name=profile_name,
        ))
        if output_format == "json":
            output.print_json(data)
        else:
            output.print_success(f"Review {review_id} accepted.")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))


@reviews_app.command("reject")
def reject(
    review_id: str = typer.Argument(..., help="Review ID"),
    reason: str = typer.Option(..., "--reason", help="|".join(REASON_CODES)),
    notes: str = typer.Option(None, "--notes"),
    profile_name: str = typer.Option(None, "--profile"),
    output_format: str = typer.Option("table", "--output", "-o"),
):
    """Reject an AI report with a reason code. Its pipeline run becomes failed."""
    if reason not in REASON_CODES:
        output.print_error(f"Unknown reason '{reason}'. Use one of: {', '.join(REASON_CODES)}.")
        raise typer.Exit(1)
    _require_user_login(profile_name)
    body: dict = {"reason_code": reason}
    if notes:
        body["notes"] = notes
    try:
        data = asyncio.run(client.request(
            "POST", f"/api/v1/reviews/{review_id}/reject", json_body=body, profile_name=profile_name,
        ))
        if output_format == "json":
            output.print_json(data)
        else:
            output.print_success(f"Review {review_id} rejected ({reason}).")
    except Exception as e:
        output.print_error(str(e))
        raise typer.Exit(exit_code_for(e))
