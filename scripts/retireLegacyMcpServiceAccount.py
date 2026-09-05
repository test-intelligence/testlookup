"""Idempotently retire the deployment-managed legacy MCP service account.

Run this only for the username captured from the old ``MCP_USERNAME`` Secret
key. The script refuses to touch a human account, deactivates the legacy
machine account, removes its project memberships, and revokes refresh/access
token authority. Unrelated service accounts are never selected.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app.db.postgres import AsyncSessionLocal
from app.core.token_revocation import revoke_all_user_tokens
from app.services.legacy_mcp_retirement import stage_legacy_account_retirement


async def main() -> int:
    username = os.environ.get("LEGACY_MCP_USERNAME", "").strip()
    if not username:
        print("LEGACY_MCP_USERNAME is required", file=sys.stderr)
        return 2

    async with AsyncSessionLocal() as db:
        try:
            outcome, account_id = await stage_legacy_account_retirement(db, username)
            if account_id is not None:
                await db.commit()
                await revoke_all_user_tokens(account_id)
        except RuntimeError as exc:
            await db.rollback()
            print(str(exc), file=sys.stderr)
            return 3
    print(f"legacy MCP account {username!r}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
