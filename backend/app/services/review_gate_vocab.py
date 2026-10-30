"""The review-gate vocabulary (W23c AGT-10), in ONE place.

The instance-wide enforcement date of the AI-report review gate lives in one
``app_settings`` row (key ``review_gate``); no row = unset = audit only (the
owner's D17 upgrade default). ``review_gate_setting`` reads and writes it and
feeds the synchronous ``report_distribution_policy.gate_enforced()``; the
Settings › AI › Review gate page reads ``GET /api/v1/settings/review-gate``.

Accepting a run's AI report re-posts the run's existing PR / MR marker comment
through two ``run_downstream_outbox`` kinds (``REPOST_KINDS``); each terminal
outcome is ONE ``integration.comment_reposted`` activity row carrying a
``REPOST_SENTENCES`` sentence.

The frontend mirrors these in ``frontend/src/types/reviewGate.ts``;
``tests/test_review_gate_inline_vocab_parity.py`` pins the mirror and the
backend sources each tuple is derived from.

Absence is not health: an unreadable audit log reads "Not measured", never 0.

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

APP_SETTINGS_KEY = "review_gate"                 # app_settings.key; value {"enforced_from": ISO-8601 UTC | None}
CACHE_KEY = "config:review_gate:v1"              # Redis mirror of the row (the ai_config_resolver pattern), deleted on PUT
CACHE_TTL_SECONDS = 60                           # Redis mirror lifetime
SNAPSHOT_TTL_SECONDS = 30                        # process-local snapshot read by the SYNC gate_enforced()
LOAD_TIMEOUT_SECONDS = 1.0                       # one refresh (Redis get + fallback DB read) — slower = keep the last value
GATE_SOURCES = ("env", "date", "unset", "scheduled")   # why the gate is (not) enforced right now
#   env       — REVIEW_GATE_ENFORCED=true: enforced whatever the date (the hard override)
#   date      — enforced_from <= now: enforced since that instant
#   scheduled — enforced_from > now: audit only until that instant
#   unset     — no date: audit only (D17's upgrade default)
WOULD_REFUSE_ACTION = "ai_report.distribution_would_refuse"   # == report_distribution_policy._AUDIT_ACTIONS[WOULD_REFUSE] (parity test)
WOULD_REFUSE_WINDOW_DAYS = 30
GATED_CHANNELS = (                               # every channel literal a decision is recorded under TODAY (13 literals on 6df8186c);
    # the card lists these, nothing invented
    ("report_pdf", "PDF export of a run report"),                              # routers/reports.py
    ("share_link_html", "Shared report link (web page)"),                     # routers/shared_reports.py
    ("share_link_pdf", "Shared report link (PDF)"),                           # routers/shared_reports.py
    ("intelligence_export", "Run intelligence export (JSON)"),                # routers/run_intelligence.py
    ("ai_history_export", "AI history export (JSON)"),                        # routers/ai_history.py EXPORT_CHANNEL
    ("analysis_report", "Window analysis report download"),                   # services/analysis_report_service.py
    ("digest_attachment", "Analysis report attached to digests"),             # services/analysis_report_service.py
    ("email_run_report", "Run report e-mail"),                                # services/notification/manager.py
    ("ai_summary_notification", "AI summary e-mail and event-driven digests"),  # worker/tasks.py
    ("ai_summary_notification_relay", "AI summary in notification relays"),  # services/notification/manager.py
    ("github_pr_comment", "AI failure-kind labels in GitHub PR comments"),     # services/github_pr_comment_service.py
    ("gitlab_mr_note", "AI failure-kind labels in GitLab MR notes"),           # services/gitlab_integration_service.py
    ("release.decided.webhook", "release.decided webhooks"),                  # services/webhook_service.py
)   # plus two value rewrites that record no channel of their own while enforced: the release-readiness answer CI reads
    # (GO / NO_GO → PENDING_REVIEW) and the release verdict quoted in reports (gate_release_verdict); the page lists
    # them under "Also changes". The parity test asserts every channel literal in app/ is in this tuple.
GATE_SENTENCES = {
    "unset": "Audit only — nothing is withheld. {runs} runs' AI reports would have been withheld in the last 30 days ({decisions} distribution attempts).",
    "scheduled": "Audit only until {date} — then unreviewed AI reports are withheld. {runs} runs' AI reports would have been withheld in the last 30 days ({decisions} distribution attempts).",
    "date": "Enforced since {date}: unreviewed AI reports are withheld on the channels below.",
    "env": "Enforced by the server setting REVIEW_GATE_ENFORCED — the date below does not apply while it is on.",
    "not_measured": "Not measured: the audit log could not be read.",
    "past_date_warning": "Setting a date in the past enforces the gate at once: CI reads PENDING_REVIEW for every unreviewed AI decision, and PDF and shared links of unreviewed reports are refused.",
}
REVIEW_GATE_ERRORS = {   # 422 / 403 detail {"code","message"}
    "naive_datetime": "enforced_from must carry a timezone offset, for example 2026-11-02T09:00:00+01:00.",
    "interactive_only": "Only an administrator signed in interactively can change the review gate; API keys can read it.",
}
REPOST_KINDS = ("pr_comment_repost", "mr_note_repost")          # run_downstream_outbox operations (§2.1 R-1)
REPOST_OUTCOMES = ("updated", "skipped", "timed_out", "failed")
REPOST_SKIP_REASONS = (
    # the git-host reasons, == github_target.GITHUB_UNAVAILABLE_REASONS ∪ gitlab_integration_service.GITLAB_UNAVAILABLE_REASONS (parity test)
    "offline_mode", "github_checks_off", "gitlab_off", "no_integration", "integration_disabled",
    "repo_mismatch", "no_project_path", "no_token",
    # the re-post's own
    "no_pr_for_run", "mode_off", "no_prior_comment", "superseded_by_newer_run", "review_no_longer_accepted",
    "blocked_unsafe_target", "run_not_found",
)
REPOST_SENTENCES = {   # the activity row's {sentence}; {host} = "PR comment" | "MR note"
    "updated": "{host} updated with the reviewed AI labels.",
    "timed_out": "{host} re-post timed out; it will be retried.",          # log line only (no activity row per retry)
    "timed_out_final": "{host} re-post timed out three times; the comment was not changed.",   # the terminal activity row
    "failed": "{host} re-post failed: the git host refused it ({status}).",
    "skipped:offline_mode": "{host} re-post skipped: offline mode (AI_OFFLINE_MODE=true).",
    "skipped:github_checks_off": "{host} re-post skipped: GitHub posting is switched off for this project.",
    "skipped:gitlab_off": "{host} re-post skipped: GitLab posting is switched off for this project.",
    "skipped:no_integration": "{host} re-post skipped: no {provider} integration for this project.",
    "skipped:integration_disabled": "{host} re-post skipped: the {provider} integration is disabled.",
    "skipped:repo_mismatch": "{host} re-post skipped: the run's repository is not the integration's.",
    "skipped:no_project_path": "{host} re-post skipped: the GitLab integration names no project.",
    "skipped:no_token": "{host} re-post skipped: no token is stored for the {provider} integration.",
    "skipped:no_pr_for_run": "{host} re-post skipped: this run is not linked to a pull or merge request.",
    "skipped:mode_off": "{host} re-post skipped: comments are switched off in the integration.",
    "skipped:no_prior_comment": "{host} re-post skipped: there is no TestLookup comment on the request to update.",
    "skipped:superseded_by_newer_run": "{host} re-post skipped: a newer run of the same request owns the comment now.",
    "skipped:review_no_longer_accepted": "{host} re-post skipped: the run's AI report is no longer accepted.",
    "skipped:blocked_unsafe_target": "{host} re-post skipped: the integration's address was refused as unsafe.",
    "skipped:run_not_found": "{host} re-post skipped: the run no longer exists.",
}
REVIEWED_LINE = "_Reviewed by a person · {reviewed_at}_"       # {reviewed_at} = "%Y-%m-%d %H:%M UTC"; NEVER a name (architecture §8.2)
