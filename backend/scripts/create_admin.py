#!/usr/bin/env python3
"""Create the first TestLookup admin on a fresh install.

The image ships this file as /app/scripts/create_admin.py:

    docker compose exec backend python /app/scripts/create_admin.py
    kubectl -n testlookup exec deployment/testlookup-backend -- \\
        sh -c 'python /app/scripts/create_admin.py'

The release stack's db-migrate service and the K3s, OpenShift and generic
Kubernetes deploy scripts run it after migrations. It was also kept as
scripts/createAdmin.py for piping over kubectl stdin; that copy is gone.

Idempotent: if a user with the target username or email already exists, it
prints that account and exits WITHOUT modifying it (its password is never
changed or shown). The first output line is ``admin user created`` or
``admin user already exists - no changes made``; the deploy scripts read it.

There is no default password. Environment variables:
    ADMIN_USERNAME    default: admin
    ADMIN_EMAIL       default: admin@example.com
    ADMIN_FULL_NAME   default: TestLookup Admin
    ADMIN_PASSWORD    the password to set. When unset or empty, a random one is
                      generated, printed ONCE, and the account must change it
                      at first sign-in.
    ADMIN_PASSWORD_GENERATED
                      "true" when the CALLER generated ADMIN_PASSWORD (the
                      deploy scripts do, and show it themselves): the account
                      must change it at first sign-in, and it is not printed.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

# /app/scripts/create_admin.py -> /app, where the `app` package lives.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def resolve_password(environ) -> tuple[str, bool, bool]:
    """(password, generated_here, must_change) for this invocation."""
    password = environ.get("ADMIN_PASSWORD", "")
    if not password:
        return secrets.token_urlsafe(18), True, True
    caller_generated = environ.get("ADMIN_PASSWORD_GENERATED", "").lower() == "true"
    return password, False, caller_generated


async def main() -> None:
    from sqlalchemy import or_, select

    from app.core.security import get_password_hash
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import User, UserRole

    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    username = os.environ.get("ADMIN_USERNAME", "admin")
    full_name = os.environ.get("ADMIN_FULL_NAME", "TestLookup Admin")

    async with AsyncSessionLocal() as db:
        # first(), not one_or_none(): the username and the email can each
        # match a different account, and neither may be touched.
        existing = (
            await db.execute(select(User).where(or_(User.username == username, User.email == email)))
        ).scalars().first()

        if existing is not None:
            print("admin user already exists - no changes made")
            print(f"  username: {existing.username}")
            print(f"  email:    {existing.email}")
            print(f"  role:     {existing.role}")
            print(f"  active:   {existing.is_active}")
            print("  reset a password from the admin UI or with scripts/getUser.py")
            return

        password, generated_here, must_change = resolve_password(os.environ)
        admin = User(
            email=email,
            username=username,
            full_name=full_name,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN.value,
            is_active=True,
            must_change_password=must_change,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        print("admin user created")
        print(f"  username: {admin.username}")
        print(f"  email:    {admin.email}")
        print(f"  role:     {admin.role}")
        print(f"  active:   {admin.is_active}")
        if generated_here:
            print(f"  password: {password}")
            print("  ** generated for this install and shown only once: save it now;")
            print("     you will be asked to change it at first sign-in **")


if __name__ == "__main__":
    asyncio.run(main())
