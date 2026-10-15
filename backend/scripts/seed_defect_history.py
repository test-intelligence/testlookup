#!/usr/bin/env python3
"""Seed one project's defect HISTORY for the Defects page's trend and escape rate (W06 DEF-04 / DEF-03).

The dev seed creates defects at one moment, so the Defects page's sparklines
were flat and its escape rate unmeasurable. This adds, to ONE project:

* 12 defects created over the last 30 days: 8 RESOLVED one to six days later,
  2 INVALID (both with a ``resolved_at``: every close has a time), 2 still OPEN;
  severities spread over CRITICAL / HIGH / MEDIUM / LOW;
* when the project has a SHIPPED release (``released_at`` set): 7 defects
  created after the most recent one shipped and 2 created before it shipped,
  all naming it in ``affects_releases`` -- 7 escapes of 9 attributed, 77.8 %,
  whenever all nine fall inside the window being read.

Every row carries ``promotion_source = "seed_defect_history"``; a re-run deletes
ONLY those rows first (idempotent, never touches another row) and draws from its
own RNG (seeded by the project slug), so no other seed shifts. Creation and
resolution times are explicit (the "seed rows need their own time" lesson).

Usage (from ``backend/``)::

    python scripts/seed_defect_history.py --project-slug payment-service

``seed_dev_data.main`` also calls :func:`apply_defect_history` for each project
after the visualization seed. Never run ``seed_data.py`` on the homelab; this
script is safe there (at most 21 rows, marker-scoped).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

MARKER = "seed_defect_history"
HISTORY_COUNT = 12
RESOLVED_COUNT = 8
INVALID_COUNT = 2
ESCAPED_COUNT = 7
PRE_SHIP_COUNT = 2
WINDOW_DAYS = 30

_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_CATEGORIES = ("PRODUCT_BUG", "PRODUCT_BUG", "INFRASTRUCTURE", "FLAKY", "TEST_DATA")
_COMPONENTS = ("checkout", "payments", "inventory", "notifications", "search")
_TITLES = (
    "Order total rounds half-cents the wrong way",
    "Refund posts twice when the gateway retries",
    "Stock count goes negative under concurrent checkout",
    "Password reset link expires immediately",
    "Search returns archived products",
    "Invoice PDF misses the VAT line",
    "Cart badge shows a stale item count",
    "Webhook retries flood the notifications queue",
    "Currency switcher resets on page reload",
    "Shipping estimate ignores public holidays",
    "Coupon applies to excluded categories",
    "Saved address loses the apartment number",
)
_ESCAPE_TITLES = (
    "Checkout times out for carts over 50 items",
    "Payment confirmation email sent twice",
    "Mobile checkout button hidden behind the cookie banner",
    "Tax calculated on the pre-discount price",
    "Gift card balance not refreshed after use",
    "Order history pagination skips a page",
    "Address autocomplete picks the wrong country",
    "Release notes link points at the previous version",
    "Feature flag default flipped for new projects",
)


@dataclass(frozen=True)
class ShippedRelease:
    """The part of a release the plan reads."""

    id: uuid.UUID
    name: str
    released_at: Optional[datetime]


def _rng(project_slug: str) -> random.Random:
    digest = hashlib.sha256(f"{MARKER}:{project_slug}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _latest_shipped(releases: Sequence[Any], now: datetime) -> Optional[ShippedRelease]:
    shipped = [
        ShippedRelease(id=r.id, name=r.name, released_at=_as_utc(r.released_at))
        for r in releases
        if getattr(r, "released_at", None) is not None and _as_utc(r.released_at) <= now
    ]
    return max(shipped, key=lambda r: r.released_at) if shipped else None


def build_defect_history_plan(project_slug: str, now: datetime, releases: Sequence[Any]) -> list[dict]:
    """The rows to insert (pure, deterministic for a slug and ``now``).

    Each dict holds ``Defect`` column values (no ``project_id``/``id``). The
    release-attributed rows are planned only when a release has shipped; they
    name the most recently shipped one in ``affects_releases``.
    """
    now = _as_utc(now)
    rng = _rng(project_slug)
    rows: list[dict] = []
    # History: created 2..29 days ago, so a 1..6 day resolution never lands in the future.
    offsets = sorted((rng.uniform(2.0, WINDOW_DAYS - 1.0) for _ in range(HISTORY_COUNT)), reverse=True)
    statuses = ["RESOLVED"] * RESOLVED_COUNT + ["INVALID"] * INVALID_COUNT
    statuses += ["OPEN"] * (HISTORY_COUNT - len(statuses))
    rng.shuffle(statuses)
    for i, (offset, status) in enumerate(zip(offsets, statuses)):
        created = now - timedelta(days=offset)
        resolved = None
        if status != "OPEN":
            delay = rng.uniform(1.0, min(6.0, offset - 0.5))
            resolved = created + timedelta(days=delay)
        rows.append(_row(rng, title=_TITLES[i % len(_TITLES)], severity=_SEVERITIES[i % len(_SEVERITIES)],
                         status=status, created=created, resolved=resolved))

    release = _latest_shipped(releases, now)
    if release is None or release.released_at is None:
        return rows
    shipped = release.released_at
    affects = [str(release.id).lower()]
    # After it shipped: spread between the later of (shipped, the window's start) and now.
    start = max(shipped, now - timedelta(days=WINDOW_DAYS - 2))
    for i in range(ESCAPED_COUNT):
        created = start + (now - start) * (i + 1) / (ESCAPED_COUNT + 1)
        rows.append(_row(rng, title=_ESCAPE_TITLES[i], severity=("MEDIUM", "LOW", "HIGH")[i % 3],
                         status="OPEN", created=created, resolved=None, affects=affects))
    # Before it shipped: half a day and a day before, resolved six hours later.
    for i in range(PRE_SHIP_COUNT):
        created = shipped - timedelta(hours=12 * (i + 1))
        rows.append(_row(rng, title=_ESCAPE_TITLES[ESCAPED_COUNT + i], severity="MEDIUM", status="RESOLVED",
                         created=created, resolved=created + timedelta(hours=6), affects=affects))
    return rows


def _row(rng: random.Random, *, title: str, severity: str, status: str, created: datetime,
         resolved: Optional[datetime], affects: Optional[list[str]] = None) -> dict:
    return {
        "title": title,
        "description": f"Seeded defect history for the Defects page trend ({MARKER}).",
        "severity": severity,
        "component": rng.choice(_COMPONENTS),
        "failure_category": rng.choice(_CATEGORIES),
        "ai_confidence_score": rng.randint(55, 95),
        "resolution_status": status,
        "created_at": created,
        "resolved_at": resolved,
        "affects_releases": affects,
        "promotion_source": MARKER,
        "approval_status": "approved",
    }


def expected_escape_rate(rows: Sequence[dict], now: datetime, release_shipped: Optional[datetime],
                         days: int = WINDOW_DAYS) -> Optional[tuple[int, int]]:
    """``(escaped, attributed)`` among the plan's rows created in the last ``days`` (the tile's numbers when
    these are the project's only attributed defects); ``None`` when nothing is attributed."""
    if release_shipped is None:
        return None
    since = _as_utc(now) - timedelta(days=days)
    attributed = [r for r in rows if r["affects_releases"] and r["created_at"] >= since]
    if not attributed:
        return None
    return sum(1 for r in attributed if r["created_at"] > release_shipped), len(attributed)


async def apply_defect_history(db: AsyncSession, project: Any, *, now: Optional[datetime] = None) -> dict:
    """Replace the project's seeded history (marker rows only) with a fresh plan. The caller commits."""
    from app.models.postgres import Defect, Release

    now = _as_utc(now or datetime.now(timezone.utc))
    deleted = (await db.execute(
        delete(Defect).where(Defect.project_id == project.id, Defect.promotion_source == MARKER)
    )).rowcount
    releases = (await db.execute(
        select(Release.id, Release.name, Release.released_at).where(Release.project_id == project.id)
    )).all()
    rows = build_defect_history_plan(project.slug, now, releases)
    for values in rows:
        db.add(Defect(id=uuid.uuid4(), project_id=project.id, **values))
    await db.flush()
    release = _latest_shipped(releases, now)
    return {
        "deleted": int(deleted or 0),
        "history": min(len(rows), HISTORY_COUNT),
        "attributed": max(0, len(rows) - HISTORY_COUNT),
        "release": release.name if release else None,
        "expected_30d": expected_escape_rate(rows, now, release.released_at if release else None),
    }


def describe(summary: dict, slug: str) -> str:
    lines = [f"{slug}: {summary['history']} history defects (replaced {summary['deleted']} seeded rows)"]
    if summary["release"] is None:
        lines.append("  no shipped release: escape-rate rows skipped")
    else:
        lines.append(f"  {summary['attributed']} defects affecting {summary['release']} "
                     f"({ESCAPED_COUNT} after it shipped, {PRE_SHIP_COUNT} before)")
        expected = summary["expected_30d"]
        if expected:
            escaped, attributed = expected
            lines.append(f"  30 d escape rate if these are the only attributed defects: "
                         f"{escaped} of {attributed} = {round(escaped / attributed * 100, 1)}%")
    return "\n".join(lines)


async def main(project_slug: str) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.postgres import Project

    engine = create_async_engine(str(settings.DATABASE_URL), echo=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            project = (await db.execute(select(Project).where(Project.slug == project_slug))).scalar_one_or_none()
            if project is None:
                print(f"no project with slug {project_slug!r}")
                return 2
            summary = await apply_defect_history(db, project)
            await db.commit()
        print(describe(summary, project_slug))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed one project's defect history (marker-scoped, idempotent)")
    parser.add_argument("--project-slug", required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.project_slug)))
