#!/usr/bin/env python3
"""
Create the initial admin user for a fresh TestLookup deployment.

Usage (inside the backend container):
    python /app/scripts/create_admin.py

Or from kubectl:
    kubectl -n testlookup exec -it deployment/testlookup-backend -- \
        python /app/scripts/create_admin.py

Environment variables (optional overrides):
    ADMIN_EMAIL       default: admin@example.com
    ADMIN_USERNAME    default: admin
    ADMIN_PASSWORD    default: none — a random password is generated, printed
                      once, and must be changed at first sign-in
    ADMIN_FULL_NAME   default: TestLookup Admin
    ADMIN_PASSWORD_GENERATED  "true" when the caller generated ADMIN_PASSWORD:
                      the account must change it at first sign-in

The script is idempotent — if the admin user already exists, it prints the
existing user info and exits without changes.
"""
import asyncio
import os
import secrets
import sys

# Ensure the app package is importable when running from /app/scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def main() -> None:
    from sqlalchemy import select

    from app.core.security import get_password_hash
    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import User, UserRole

    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    username = os.environ.get("ADMIN_USERNAME", "admin")
    full_name = os.environ.get("ADMIN_FULL_NAME", "TestLookup Admin")
    password = os.environ.get("ADMIN_PASSWORD", "")

    async with AsyncSessionLocal() as db:
        # Check if user already exists
        result = await db.execute(
            select(User).where(
                (User.email == email) | (User.username == username)
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"\n  Admin user already exists:")
            print(f"    username : {existing.username}")
            print(f"    email    : {existing.email}")
            print(f"    role     : {existing.role}")
            print(f"    active   : {existing.is_active}")
            print(f"\n  No changes made. To reset password, use the API or update the DB directly.\n")
            return

        # Generate password if not provided
        if not password:
            password = secrets.token_urlsafe(16)
            generated = True
        else:
            generated = False
        # The installers generate the password themselves and show it; the
        # account must still change it at first sign-in.
        must_change = generated or os.environ.get("ADMIN_PASSWORD_GENERATED", "").lower() == "true"

        user = User(
            email=email,
            username=username,
            full_name=full_name,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN,
            is_active=True,
            must_change_password=must_change,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        print(f"\n  {'='*50}")
        print(f"  Admin user created successfully!")
        print(f"  {'='*50}")
        print(f"    username : {username}")
        print(f"    email    : {email}")
        if generated:
            print(f"    password : {password}")
        else:
            print("    password : (the ADMIN_PASSWORD supplied)")
        print(f"    role     : ADMIN")
        print(f"  {'='*50}")
        if generated:
            print(f"  ** Password was auto-generated. Save it now — it cannot be retrieved later;")
            print(f"     you will be asked to change it at first sign-in. **")
        print(f"  {'='*50}\n")


if __name__ == "__main__":
    asyncio.run(main())
