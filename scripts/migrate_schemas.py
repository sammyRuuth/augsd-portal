#!/usr/bin/env python3
"""
Manually run schema migrations for all sessions.

Usage:
    uv run python scripts/migrate_schemas.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main():
    from sqlalchemy import select, text

    from app.database import AsyncSessionLocal, engine
    from app.models.session import Session

    print("Fetching all sessions...")

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Session.id, Session.name, Session.schema_name))
        sessions = result.fetchall()

    print(f"Found {len(sessions)} sessions")

    for session_id, name, schema_name in sessions:
        print(f"\nMigrating schema: {schema_name} (session: {name})")

        async with engine.begin() as conn:
            # Check if timetables table exists
            check_table = await conn.execute(
                text("""
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = :schema AND table_name = 'timetables'
                """),
                {"schema": schema_name},
            )
            if not check_table.fetchone():
                print(f"  - timetables table doesn't exist, skipping")
                continue

            # Check if column exists
            check_col = await conn.execute(
                text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = :schema
                    AND table_name = 'timetables'
                    AND column_name = 'buffer_timetable_id'
                """),
                {"schema": schema_name},
            )

            if check_col.fetchone():
                print(f"  - buffer_timetable_id already exists")
            else:
                print(f"  - Adding buffer_timetable_id column...")
                await conn.execute(
                    text(
                        f'ALTER TABLE "{schema_name}".timetables '
                        f"ADD COLUMN buffer_timetable_id UUID"
                    )
                )
                print(f"  ✓ Added buffer_timetable_id")

    print("\nMigration complete!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
