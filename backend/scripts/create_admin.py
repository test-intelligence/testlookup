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
changed or shown). The first output line is ``admin user created``,
``admin user already exists - no changes made`` or ``admin user not created
- ...``; the deploy scripts read it.

There is no default password. Environment variables:
    ADMIN_USERNAME    default: admin
    ADMIN_EMAIL       default: admin@example.com
    ADMIN_FULL_NAME   default: TestLookup Admin
    ADMIN_PASSWORD    the password to set. Give it through the environment or,
                      from a deploy script, on the pod's stdin; never as a
                      command-line argument, which other users of the host can
                      read. When unset or empty AND the output is a terminal,
                      a random one is generated, printed ONCE, and the account
                      must change it at first sign-in. When unset or empty and
                      the output is NOT a terminal (a service log, a pipe), no
                      admin is created: a generated password would be stored
                      wherever that output is kept (security review
                      2026-11-20, TL-08).
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


def output_is_a_terminal() -> bool:
    """Whether what this script prints goes to a person's terminal, not a log or a pipe."""
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


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
        if generated_here and not output_is_a_terminal():
            # A password generated here can only reach the operator by being
            # printed. When the output is a service log or a deploy script's
            # capture, printing it would store an admin password there, and
            # creating the account without showing it would leave an admin
            # nobody can sign in as. So nothing is created.
            print("admin user not created - no password was given and the output is not a terminal")
            print("  either set the password (ADMIN_PASSWORD; INITIAL_ADMIN_PASSWORD in the")
            print("  release stack's .env) and run this again, or run it in a terminal, where")
            print("  a generated password is shown once:")
            print("    docker compose exec backend python /app/scripts/create_admin.py")
            return
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
