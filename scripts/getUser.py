import asyncio, sys

sys.path.insert(0, '/app')
from sqlalchemy import select
from app.core.security import get_password_hash
from app.db.postgres import AsyncSessionLocal
from app.models.postgres import User


async def main():
    pw = 'Admin@2026!'
    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == 'admin'))).scalar_one_or_none()
        if not u:
            print('No admin user found')
            return
        u.hashed_password = get_password_hash(pw)
        u.must_change_password = False
        await db.commit()
        print(f'username: {u.username}')
        print(f'email:    {u.email}')
        print(f'password: {pw}')


asyncio.run(main())