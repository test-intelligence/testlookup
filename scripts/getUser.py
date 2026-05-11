"""Read the TestLookup admin user, or create one if none exists.

Designed for kubectl stdin piping:

    kubectl -n testlookup exec -i deployment/testlookup-backend -- \
        python < ./scripts/getUser.py

Behavior:
  - If an admin user (username='admin' OR role=ADMIN) exists, prints its
    details and resets the password to a known value so the operator can
    log back in after a rebuild.
  - If no admin exists, creates one with the same known credentials.

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
                must_change_password=False,
            )
            db.add(admin)
            await db.commit()
            await db.refresh(admin)
            action = 'created'
        else:
            admin.hashed_password = get_password_hash(password)
            admin.role = UserRole.ADMIN.value
            admin.is_active = True
            admin.must_change_password = False
            await db.commit()
            await db.refresh(admin)
            action = 'reset'

        print(f'admin user {action}')
        print(f'  username: {admin.username}')
        print(f'  email:    {admin.email}')
        print(f'  role:     {admin.role}')
        print(f'  active:   {admin.is_active}')
        print(f'  password: {password}')


asyncio.run(main())
