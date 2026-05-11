"""Create the TestLookup admin user after a rebuild/restart.

Designed for kubectl stdin piping:

    kubectl -n testlookup exec -i deployment/testlookup-backend -- \
        python < ./scripts/createAdmin.py

Idempotent: if a user with the target username/email already exists, prints
its details and exits without modifying it. Use ./scripts/getUser.py instead
when you want to reset the existing admin's password.

Override defaults via env vars on the pod (or pass with `kubectl exec --env`):
    ADMIN_EMAIL       default: admin@testlookup.local
    ADMIN_USERNAME    default: admin
    ADMIN_PASSWORD    default: Admin@2026!
    ADMIN_FULL_NAME   default: TestLookup Admin
"""
import asyncio
import os
import sys

sys.path.insert(0, '/app')

from sqlalchemy import or_, select

from app.core.security import get_password_hash
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User, UserRole


async def main() -> None:
    email = os.environ.get('ADMIN_EMAIL', 'admin@testlookup.local')
    username = os.environ.get('ADMIN_USERNAME', 'admin')
    full_name = os.environ.get('ADMIN_FULL_NAME', 'TestLookup Admin')
    password = os.environ.get('ADMIN_PASSWORD', 'Admin@2026!')

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(User).where(
                    or_(User.username == username, User.email == email)
                )
            )
        ).scalars().first()

        if existing is not None:
            print('admin user already exists - no changes made')
            print(f'  username: {existing.username}')
            print(f'  email:    {existing.email}')
            print(f'  role:     {existing.role}')
            print(f'  active:   {existing.is_active}')
            print('use ./scripts/getUser.py to reset the password')
            return

        admin = User(
            email=email,
            username=username,
            full_name=full_name,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN.value,
            is_active=True,
            must_change_password=False,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        print('admin user created')
        print(f'  username: {admin.username}')
        print(f'  email:    {admin.email}')
        print(f'  role:     {admin.role}')
        print(f'  active:   {admin.is_active}')
        print(f'  password: {password}')


asyncio.run(main())
