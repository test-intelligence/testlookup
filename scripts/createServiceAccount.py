"""Provision (or converge) a machine account from its Kubernetes secret.

Designed for kubectl stdin piping, same as scripts/createAdmin.py:

    kubectl -n testlookup exec -i deployment/testlookup-backend -- \
        env SERVICE_USERNAME=ci_agent SERVICE_PASSWORD=... SERVICE_ROLE=QA_LEAD \
        python < ./scripts/createServiceAccount.py

This helper remains available for machine integrations that intentionally own
a service identity. Network MCP does not use it: every remote MCP request is
authenticated with the presenting caller's TestLookup bearer token.

Unlike createAdmin.py this CONVERGES an existing account. For a machine account
the secret is the source of truth: if the stored password no longer matches it
the account is unusable, and the deploy should repair that rather than report
success. A human admin's password is deliberately never overwritten that way —
use scripts/getUser.py for those.

It also enrols the account in every active project, because non-admin users
only see projects they belong to; a service account with the right role and no
memberships authenticates and then sees nothing. Projects created later are
handled by the backend at creation time (see
app/services/service_account_service.py) — this script covers the ones that
already exist when it runs.

Env:
    SERVICE_USERNAME   required
    SERVICE_PASSWORD   required — no default, see below
    SERVICE_ROLE       default QA_LEAD
    SERVICE_EMAIL      default <username>@testlookup.local
    SERVICE_FULL_NAME  default "<username> (service account)"
"""
import asyncio
import os
import sys

sys.path.insert(0, '/app')

from sqlalchemy import or_, select

from app.core.security import get_password_hash, verify_password
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import Project, ProjectMember, User, UserRole


async def _enroll_in_all_projects(db, account, role) -> int:
    """Give the account membership of every active project. Idempotent."""
    project_ids = (
        await db.execute(select(Project.id).where(Project.is_active.is_(True)))
    ).scalars().all()
    existing_ids = set(
        (
            await db.execute(
                select(ProjectMember.project_id).where(
                    ProjectMember.user_id == account.id
                )
            )
        ).scalars().all()
    )
    added = 0
    for pid in project_ids:
        if pid in existing_ids:
            continue
        db.add(ProjectMember(user_id=account.id, project_id=pid, role=role.value))
        added += 1
    return added


async def _invalidate_membership_cache(user_id) -> None:
    """The accessible-project set is cached in Redis for five minutes. Without
    this, an account enrolled just now still reports "no projects" until the
    TTL lapses — which looks exactly like enrolment having failed."""
    try:
        from app.core.deps import invalidate_membership_cache
        await invalidate_membership_cache(user_id)
    except Exception as exc:  # pragma: no cover - best effort
        print(f'  warning: could not invalidate membership cache: {exc}')


async def main() -> int:
    username = os.environ.get('SERVICE_USERNAME', '').strip()
    password = os.environ.get('SERVICE_PASSWORD', '')
    role_name = os.environ.get('SERVICE_ROLE', 'QA_LEAD').strip().upper()
    email = os.environ.get('SERVICE_EMAIL', '').strip() or f'{username}@testlookup.local'
    full_name = (
        os.environ.get('SERVICE_FULL_NAME', '').strip()
        or f'{username} (service account)'
    )

    if not username:
        print('SERVICE_USERNAME is required', file=sys.stderr)
        return 2
    if not password:
        # Defaulting one would create a guessable account that, at QA_LEAD,
        # can quarantine tests and trigger analysis.
        print('SERVICE_PASSWORD is required — refusing to create a service '
              'account with a default password', file=sys.stderr)
        return 2
    try:
        role = UserRole(role_name)
    except ValueError:
        valid = ', '.join(r.value for r in UserRole)
        print(f'SERVICE_ROLE={role_name!r} is not a valid UserRole ({valid})',
              file=sys.stderr)
        return 2

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(User).where(or_(User.username == username, User.email == email))
            )
        ).scalars().first()

        if existing is None:
            account = User(
                email=email,
                username=username,
                full_name=full_name,
                hashed_password=get_password_hash(password),
                role=role.value,
                is_active=True,
                is_service_account=True,
            )
            db.add(account)
            await db.flush()
            enrolled = await _enroll_in_all_projects(db, account, role)
            await db.commit()
            await _invalidate_membership_cache(account.id)
            print(f'created service account {username!r} with role {role.value}')
            print(f'  enrolled in {enrolled} project(s)')
            return 0

        changes = []
        if not verify_password(password, existing.hashed_password):
            existing.hashed_password = get_password_hash(password)
            changes.append('password (now matches the secret)')
        if str(existing.role) != role.value:
            changes.append(f'role {existing.role} -> {role.value}')
            existing.role = role.value
        if not existing.is_active:
            existing.is_active = True
            changes.append('reactivated')
        if not existing.is_service_account:
            existing.is_service_account = True
            changes.append('flagged as a service account')

        enrolled = await _enroll_in_all_projects(db, existing, role)
        if enrolled:
            changes.append(f'enrolled in {enrolled} new project(s)')

        if changes:
            await db.commit()
            await _invalidate_membership_cache(existing.id)
            print(f'converged service account {username!r}: ' + '; '.join(changes))
        else:
            print(f'service account {username!r} already correct '
                  f'(role {existing.role}, active) — no changes')
        return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
