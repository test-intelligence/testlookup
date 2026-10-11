"""Read the TestLookup admin user, or create one if none exists.

Designed for kubectl stdin piping:

    kubectl -n testlookup exec -i deployment/testlookup-backend -- \
        env ADMIN_PASSWORD='<new password>' python < ./scripts/getUser.py

Behavior:
  - If an admin user (username='admin' OR role=ADMIN) exists, prints its
    details and RESETS its password (and re-activates it) so the operator can
    sign in again.
  - If no admin exists, creates one.

There is no default password. Environment variables:
    ADMIN_USERNAME    default: admin
    ADMIN_EMAIL       default: admin@example.com
    ADMIN_FULL_NAME   default: TestLookup Admin
    ADMIN_PASSWORD    the password to set. When unset or empty, a random one is
                      generated, printed ONCE, and must be changed at the next
                      sign-in.
"""
import asyncio
import os
import secrets
import sys

sys.path.insert(0, '/app')

from sqlalchemy import or_, select

from app.core.security import get_password_hash
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User, UserRole


async def main() -> None:
    email = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
    username = os.environ.get('ADMIN_USERNAME', 'admin')
    full_name = os.environ.get('ADMIN_FULL_NAME', 'TestLookup Admin')
    password = os.environ.get('ADMIN_PASSWORD', '')
    generated = not password
    if generated:
        password = secrets.token_urlsafe(18)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                or_(
                    User.username == username,
                    User.email == email,
                    User.role == UserRole.ADMIN.value,
                )
            )
        )
        admin = result.scalars().first()

        if admin is None:
            admin = User(
                email=email,
                username=username,
                full_name=full_name,
                hashed_password=get_password_hash(password),
                role=UserRole.ADMIN.value,
                is_active=True,
                must_change_password=generated,
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
            action = 'created'
        else:
            admin.hashed_password = get_password_hash(password)
            admin.role = UserRole.ADMIN.value
            admin.is_active = True
            admin.must_change_password = generated
            await db.commit()
            await db.refresh(admin)
            action = 'reset'

        print(f'admin user {action}')
        print(f'  username: {admin.username}')
        print(f'  email:    {admin.email}')
        print(f'  role:     {admin.role}')
        print(f'  active:   {admin.is_active}')
        if generated:
            print(f'  password: {password}')
            print('  ** generated and shown only once: save it now; it must be changed at the next sign-in **')
        else:
            print('  password: (the ADMIN_PASSWORD you supplied)')


asyncio.run(main())
