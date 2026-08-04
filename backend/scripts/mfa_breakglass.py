#!/usr/bin/env python3
"""
Breakglass: clear MFA and account lockout for one user.

This is the recovery path for a lost authenticator with no recovery codes
left. Nothing in the product can do this — by design. A user who could reset
their own second factor from inside the app would not have a second factor,
and an admin endpoint that resets *another* user's factor is an account
takeover primitive sitting behind whatever the weakest admin session is.

``IdentityEventType.ADMIN_FALLBACK_LOGIN`` does NOT cover this: that is the
SSO fallback (``routers/auth.py``, gated on ``SSO_ADMIN_FALLBACK_ENABLED``),
which lets an admin use password login while SSO is enforced. It says nothing
about a second factor.

Following the ``SSO_ADMIN_FALLBACK_ENABLED`` precedent, this is:

  * **environment-gated** — refuses to run unless ``MFA_BREAKGLASS_ENABLED``
    is true in the backend environment. There is no in-app toggle, so turning
    it on takes deployment-level access (a pod env var, a compose override),
    not a session;
  * **loud** — every run writes an ``MFA_BREAKGLASS_RESET`` identity event
    naming the operator and the reason. The event is written in the same
    transaction as the reset, so there is no version of this that succeeds
    quietly;
  * **narrow** — it clears MFA and lockout for exactly one user. It does not
    change passwords, roles, or sessions.

Usage (inside the backend container):

    MFA_BREAKGLASS_ENABLED=true python /app/scripts/mfa_breakglass.py \\
        --user alice@example.com --operator "on-call: sam" \\
        --reason "lost phone, recovery codes exhausted, verified by video"

    # See what it would do, without writing:
    python /app/scripts/mfa_breakglass.py --user alice --dry-run

Or from kubectl:

    kubectl -n testlookup exec -it deployment/testlookup-backend -- \\
        env MFA_BREAKGLASS_ENABLED=true python /app/scripts/mfa_breakglass.py \\
        --user alice --operator "sam" --reason "..."

After the reset the user signs in with their password alone. If workspace
policy requires MFA for their role, the next login immediately hands them an
enrollment challenge — so the account does not sit unprotected.
"""
import argparse
import asyncio
import os
import sys

# Ensure the app package is importable when running from /app/scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear MFA enrollment and account lockout for one user.",
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Username or email address of the account to reset.",
    )
    parser.add_argument(
        "--operator",
        default=os.environ.get("MFA_BREAKGLASS_OPERATOR", ""),
        help="Who is performing the reset. Recorded in the audit event.",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="Why. Recorded in the audit event. Required unless --dry-run.",
    )
    parser.add_argument(
        "--keep-lockout",
        action="store_true",
        help="Clear MFA only; leave any active lockout in place.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change and exit without writing.",
    )
    return parser.parse_args(argv)


async def main(argv=None, session_factory=None) -> int:
    """Run the breakglass reset. Returns a process exit code.

    ``session_factory`` exists so tests can drive this against a throwaway
    database without monkeypatching ``app.db.postgres.AsyncSessionLocal``,
    which is served by a PEP 562 ``__getattr__`` and does not survive
    monkeypatching cleanly (it leaves a real attribute behind and pollutes
    later tests in the same process).
    """
    args = _parse_args(argv)

    from sqlalchemy import select

    from app.core.config import settings
    from app.models.postgres import IdentityEvent, IdentityEventType, User
    from app.services import mfa_service

    if session_factory is None:
        from app.db.postgres import AsyncSessionLocal

        session_factory = AsyncSessionLocal

    if not args.dry_run and not settings.MFA_BREAKGLASS_ENABLED:
        print(
            "\n  REFUSED: MFA_BREAKGLASS_ENABLED is not set in this environment.\n"
            "  This is the deliberate gate on the MFA recovery path. Set\n"
            "  MFA_BREAKGLASS_ENABLED=true in the backend environment and re-run,\n"
            "  or use --dry-run to inspect the account without changing it.\n"
        )
        return 2

    if not args.dry_run and not args.reason.strip():
        print("\n  REFUSED: --reason is required (it is written to the audit trail).\n")
        return 2

    needle = args.user.strip()
    async with session_factory() as db:
        result = await db.execute(
            select(User).where((User.username == needle) | (User.email == needle))
        )
        user = result.scalar_one_or_none()
        if user is None:
            print(f"\n  No user found matching {needle!r}.\n")
            return 1

        lock = mfa_service.locked_until(user)
        remaining = await mfa_service.count_unused_recovery_codes(db, user)
        seed = await mfa_service.load_totp_secret(db, user)

        print(f"\n  {'=' * 58}")
        print("  MFA breakglass")
        print(f"  {'=' * 58}")
        print(f"    user            : {user.username} <{user.email}>")
        print(f"    role            : {user.role}")
        print(f"    mfa_enabled     : {user.mfa_enabled}")
        print(f"    seed state      : {seed.state.value}")
        print(f"    recovery codes  : {remaining} unused")
        print(f"    failed attempts : {user.failed_login_attempts}")
        print(f"    locked until    : {lock.isoformat() if lock else '(not locked)'}")

        if args.dry_run:
            actions = []
            if user.mfa_enabled:
                actions.append("disable MFA, destroy the TOTP seed, drop recovery codes")
            if lock is not None and not args.keep_lockout:
                actions.append("clear the lockout")
            print(f"  {'-' * 58}")
            print("    DRY RUN — would: " + ("; ".join(actions) or "nothing to do"))
            print(f"  {'=' * 58}\n")
            return 0

        if not user.mfa_enabled and lock is None:
            print(f"  {'-' * 58}")
            print("    Nothing to do — MFA is off and the account is not locked.")
            print(f"  {'=' * 58}\n")
            return 0

        detail = {
            "operator": args.operator or "(unspecified)",
            "reason": args.reason.strip(),
            "mfa_was_enabled": bool(user.mfa_enabled),
            "seed_state": seed.state.value,
            "recovery_codes_remaining": remaining,
            "was_locked": lock is not None,
            "lockout_cleared": lock is not None and not args.keep_lockout,
        }

        if user.mfa_enabled:
            await mfa_service.disable_mfa(db, user)
        if lock is not None and not args.keep_lockout:
            user.locked_until = None
        user.failed_login_attempts = 0

        # Written on the same transaction as the reset — there is no path where
        # the factor is cleared without the trail recording it.
        db.add(
            IdentityEvent(
                event_type=IdentityEventType.MFA_BREAKGLASS_RESET,
                user_id=user.id,
                actor_name=(args.operator or "breakglass-script")[:200],
                detail=detail,
            )
        )
        await db.commit()

        print(f"  {'-' * 58}")
        print("    MFA cleared. The user can now sign in with their password.")
        if not args.keep_lockout:
            print("    Lockout cleared.")
        print("    Audit event MFA_BREAKGLASS_RESET written.")
        print(
            "    If workspace policy requires MFA for this role, their next\n"
            "    login will immediately require re-enrollment."
        )
        print(f"  {'=' * 58}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
