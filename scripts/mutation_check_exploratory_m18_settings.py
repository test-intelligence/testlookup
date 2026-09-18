"""Prove M18 settings and delivery regressions kill unsafe behavior."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str
    safe: str
    unsafe: str
    test: str


MUTATIONS = (
    Mutation(
        "jira-probe-config-authority",
        "backend/app/services/integration_probe_service.py",
        'domain = config.get("jira_domain") if config is not None else settings.JIRA_DOMAIN',
        'domain = settings.JIRA_DOMAIN',
        "backend/tests/regression/test_integration_probe_on_demand_authority.py::test_jira_probe_uses_resolved_endpoint_and_credentials",
    ),
    Mutation(
        "digest-failure-watermark",
        "backend/app/worker/tasks.py",
        'if status == "sent":',
        'if status in {"sent", "failed"}:',
        "backend/tests/regression/test_digest_failure_retry.py",
    ),
    Mutation(
        "digest-claim-success-history",
        "backend/app/worker/tasks.py",
        ".values(\n                        next_delivery_at=scheduled_next,\n                    )",
        ".values(\n                        last_delivered_at=now,\n                        next_delivery_at=scheduled_next,\n                        delivery_count=DigestSubscription.delivery_count + 1,\n                    )",
        "backend/tests/regression/test_digest_failure_retry.py::test_atomic_claim_does_not_advance_success_history",
    ),
    Mutation(
        "digest-first-window-anchor",
        "backend/app/worker/tasks.py",
        "return last_delivered_at or created_at or (now - delta)",
        "return last_delivered_at or (now - delta)",
        "backend/tests/regression/test_digest_failure_retry.py::test_first_delivery_retry_keeps_original_creation_window",
    ),
    Mutation(
        "digest-disabled-smtp",
        "backend/app/worker/tasks.py",
        'return None if smtp_cfg.get("enabled") else "SMTP is disabled"',
        'return None',
        "backend/tests/regression/test_digest_failure_retry.py::test_disabled_smtp_is_a_failed_delivery_not_a_successful_skip",
    ),
    Mutation(
        "digest-failure-claim-cas",
        "backend/app/worker/tasks.py",
        'if status == "failed":\n                            final_update = final_update.where(',
        'if False and status == "failed":\n                            final_update = final_update.where(',
        "backend/tests/regression/test_digest_failure_retry.py::test_failure_retry_update_is_bound_to_the_claimed_schedule_slot",
    ),
    Mutation(
        "feature-scope-clear",
        "backend/app/services/feature_flags.py",
        'if "enabled_projects" in updates:\n        projects = updates["enabled_projects"] or []',
        'if "enabled_projects" in updates and updates["enabled_projects"] is not None:\n        projects = updates["enabled_projects"] or []',
        "backend/tests/services/test_feature_flags_service.py::test_update_flag_explicit_null_clears_scope_allowlists",
    ),
    Mutation(
        "feature-role-clear",
        "backend/app/services/feature_flags.py",
        'if "enabled_roles" in updates:\n        roles = updates["enabled_roles"] or []',
        'if "enabled_roles" in updates and updates["enabled_roles"] is not None:\n        roles = updates["enabled_roles"] or []',
        "backend/tests/services/test_feature_flags_service.py::test_update_flag_explicit_null_clears_scope_allowlists",
    ),
    Mutation(
        "integration-secret-clear",
        "backend/app/routers/app_settings.py",
        'if raw_value == "":\n            await expire_secret(db, _INTEGRATIONS_KEY, key_name)',
        'if False and raw_value == "":\n            await expire_secret(db, _INTEGRATIONS_KEY, key_name)',
        "backend/tests/regression/test_integrations_secret_authority.py::test_integrations_empty_secret_expires_stored_value",
    ),
    Mutation(
        "integration-secret-flush",
        "backend/app/routers/app_settings.py",
        "await db.flush()\n    secret_backed = any(",
        "secret_backed = any(",
        "backend/tests/regression/test_integrations_secret_authority.py::test_integrations_empty_secret_expires_stored_value",
    ),
    Mutation(
        "smtp-runtime-secret-authority",
        "backend/app/services/notification/email_service.py",
        'cfg["password"] = (\n'
        '                    secret_value or cfg.get("password") or settings.SMTP_PASSWORD\n'
        "                )",
        'cfg["password"] = cfg.get("password") or settings.SMTP_PASSWORD',
        "backend/tests/regression/test_smtp_secret_tristate.py::test_runtime_smtp_resolver_reads_encrypted_secret_ref",
    ),
    Mutation(
        "smtp-secret-failure-fail-closed",
        "backend/app/services/notification/email_service.py",
        "except RuntimeError:\n        # Secret decryption/key failures are security configuration errors.\n        # Do not turn them into a stale environment-credential fallback.\n        raise",
        "except RuntimeError:\n        pass",
        "backend/tests/regression/test_smtp_secret_tristate.py::test_runtime_smtp_resolver_fails_closed_on_secret_key_error",
    ),
)


def run_test(test: str, suffix: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:testlookup",
            "--basetemp",
            f".pytest-tmp-exploratory-m18-mutation-{suffix}",
            test,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main() -> int:
    originals: dict[Path, bytes] = {}
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        originals.setdefault(path, path.read_bytes())
        source = originals[path].decode("utf-8")
        if source.count(mutation.safe) != 1:
            raise AssertionError(f"{mutation.name} mutation must apply exactly once")
        baseline = run_test(mutation.test, f"baseline-{mutation.name}")
        if baseline.returncode != 0:
            raise AssertionError(
                f"{mutation.name} baseline failed\n{baseline.stdout}{baseline.stderr}"
            )
        try:
            path.write_text(
                source.replace(mutation.safe, mutation.unsafe, 1),
                encoding="utf-8",
                newline="",
            )
            mutated = run_test(mutation.test, mutation.name)
            if mutated.returncode != 1:
                raise AssertionError(
                    f"{mutation.name} was not killed with pytest exit 1\n"
                    f"{mutated.stdout}{mutated.stderr}"
                )
        finally:
            path.write_bytes(originals[path])

    print(f"M18 settings mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
