import asyncio
import os
import sys

# Ensure we can import app
sys.path.insert(0, os.path.dirname(__file__))

from app.db.postgres import AsyncSessionLocal
from sqlalchemy import select
from app.models.postgres import User
from app.core.security import verify_password

async def main():
    try:
        async with AsyncSessionLocal() as db:
            print("Connected to DB.")
            result = await db.execute(select(User).where(User.username == "admin"))
            user = result.scalar_one_or_none()
            if not user:
                print("User 'admin' not found.")
                return
            print("Found user:", user.email)
            print("Hashed password:", user.hashed_password)
            is_valid = verify_password("Admin@2026!", user.hashed_password)
            print("Password valid:", is_valid)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
