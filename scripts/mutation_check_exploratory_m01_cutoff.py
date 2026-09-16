"""Prove the M01 revocation-cutoff regressions require precise ordering."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
UV = "uv.exe" if os.name == "nt" else "uv"
TESTS = (
    UV,
    "run",
    "pytest",
    "-q",
    "-p",
    "no:testlookup",
    "--basetemp=.pytest-tmp-exploratory-m01-cutoff-mutation",
    "tests/core/test_access_token_precision.py",
    "tests/core/test_auth_session_tokens.py",
    "tests/core/test_token_revocation.py",
    "tests/core/test_deps_revocation_fail_closed.py::test_fractional_iat_reaches_cutoff_check",
    "tests/regression/test_durable_token_revocation.py",
    "tests/regression/test_auth_session_serialization.py",
)

SECURITY = BACKEND / "app" / "core" / "security.py"
DEPS = BACKEND / "app" / "core" / "deps.py"
REVOCATION = BACKEND / "app" / "core" / "token_revocation.py"
AUTH = BACKEND / "app" / "routers" / "auth.py"
MFA = BACKEND / "app" / "routers" / "mfa.py"
SSO = BACKEND / "app" / "routers" / "sso.py"
DEFAULT_QA_LEAD = BACKEND / "app" / "services" / "default_qa_lead_service.py"
SESSION_TOKENS = BACKEND / "app" / "services" / "auth_session_tokens.py"
MUTATIONS = (
    (
        SECURITY,
        '        "iat": now.timestamp(),\n        "exp": expire,\n        "type": "access",',
        '        "iat": int(now.timestamp()),\n        "exp": expire,\n        "type": "access",',
    ),
    (
        SECURITY,
        '        "iat": now.timestamp(),\n        "exp": expire,\n        "type": token_type,',
        '        "iat": int(now.timestamp()),\n        "exp": expire,\n        "type": token_type,',
    ),
    (DEPS, "        iat_value = float(iat)", "        iat_value = float(int(iat))"),
    (
        REVOCATION,
        "    cutoff = datetime.now(timezone.utc).timestamp()",
        "    cutoff = float(int(datetime.now(timezone.utc).timestamp()))",
    ),
    (
        REVOCATION,
        "token_iat <= cutoff_value.timestamp()",
        "token_iat <= int(cutoff_value.timestamp())",
    ),
    (REVOCATION, "        precise_cutoff = float(legacy)", "        precise_cutoff = int(legacy)"),
    (REVOCATION, "        cutoff = float(cutoff_str)", "        cutoff = int(cutoff_str)"),
    (
        REVOCATION,
        "            cutoff = result.scalar_one().timestamp()",
        "            _ = result.scalar_one().timestamp()",
    ),
    (
        REVOCATION,
        "            cutoff = rows[0][0].timestamp()",
        "            _ = rows[0][0].timestamp()",
    ),
    (
        REVOCATION,
        '    "VALUES (:jti, :uid, clock_timestamp()) "\n'
        '    "ON CONFLICT (jti) DO UPDATE SET valid_from=clock_timestamp() "',
        '    "VALUES (:jti, :uid, now()) "\n'
        '    "ON CONFLICT (jti) DO UPDATE SET valid_from=now() "',
    ),
    (
        AUTH,
        "        ).with_for_update()\n    )\n    user = result.scalar_one_or_none()",
        "        )\n    )\n    user = result.scalar_one_or_none()",
    ),
    (
        AUTH,
        "    result = await db.execute(select(User).where(User.id == uid).with_for_update())",
        "    result = await db.execute(select(User).where(User.id == uid))",
    ),
    (
        MFA,
        "        await db.execute(select(User).where(User.id == uid).with_for_update())",
        "        await db.execute(select(User).where(User.id == uid))",
    ),
    (
        MFA,
        "        if await is_token_before_cutoff(uid, iat_value):",
        "        if False and await is_token_before_cutoff(uid, iat_value):",
    ),
    (
        AUTH,
        "    await db.execute(select(User.id).where(User.id == user.id).with_for_update())\n"
        "    await db.refresh(user)\n\n    # dev-login",
        "    await db.execute(select(User.id).where(User.id == user.id))\n"
        "    await db.refresh(user)\n\n    # dev-login",
    ),
    (
        AUTH,
        "    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())\n"
        "    await db.refresh(current_user)\n    if not current_user.must_change_password:",
        "    await db.execute(select(User.id).where(User.id == current_user.id))\n"
        "    await db.refresh(current_user)\n    if not current_user.must_change_password:",
    ),
    (
        AUTH,
        "    await db.execute(select(User.id).where(User.id == current_user.id).with_for_update())\n"
        "    await db.refresh(current_user)\n    if not verify_password",
        "    await db.execute(select(User.id).where(User.id == current_user.id))\n"
        "    await db.refresh(current_user)\n    if not verify_password",
    ),
    (
        SSO,
        "    await db.execute(select(User.id).where(User.id == user.id).with_for_update())",
        "    await db.execute(select(User.id).where(User.id == user.id))",
    ),
    (
        DEFAULT_QA_LEAD,
        "    await db.execute(select(User.id).where(User.id == user.id).with_for_update())",
        "    await db.execute(select(User.id).where(User.id == user.id))",
    ),
    (
        SESSION_TOKENS,
        "    result = await db.execute(select(func.clock_timestamp()))",
        "    result = await db.execute(select(func.now()))",
    ),
    (
        SESSION_TOKENS,
        "    return create_access_token(subject, expires_delta, issued_at=issued_at)",
        "    return create_access_token(subject, expires_delta)",
    ),
    (
        SESSION_TOKENS,
        "    return create_mfa_token(subject, token_type, issued_at=issued_at)",
        "    return create_mfa_token(subject, token_type)",
    ),
)


def main() -> int:
    original_by_path = {path: path.read_bytes() for path, _, _ in MUTATIONS}
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(ROOT / ".uv-cache")

    for source_path, good, bad in MUTATIONS:
        original = original_by_path[source_path]
        text = original.decode("utf-8")
        count = text.count(good)
        if count != 1:
            raise AssertionError(
                f"mutation must apply exactly once: {good!r}; found {count}"
            )
        mutated = text.replace(good, bad, 1)
        if mutated == text:
            raise AssertionError(f"mutation did not change source: {good!r}")
        source_path.write_text(mutated, encoding="utf-8")
        try:
            run = subprocess.run(
                TESTS,
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        finally:
            source_path.write_bytes(original)
        if run.returncode == 0:
            raise AssertionError(f"mutation survived: {bad!r}")
        if hashlib.sha256(source_path.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise AssertionError(f"source restoration failed: {source_path}")

    print(f"M01 cutoff mutation check: {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
