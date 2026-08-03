import asyncio

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, sessionmaker

from app.database import engine
from app.models.course import Course
from app.models.prerequisite import Prerequisite


async def cleanup_duplicates():
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        print("Finding duplicates...")

        # 1. Find duplicates based on Subject + Catalog
        stmt = (
            select(Course.subject, Course.catalog, func.count(Course.id))
            .group_by(Course.subject, Course.catalog)
            .having(func.count(Course.id) > 1)
        )

        result = await session.execute(stmt)
        duplicates = result.all()

        print(f"Found {len(duplicates)} duplicate groups.")

        deleted_count = 0
        updated_refs_count = 0

        for subj, cat, count in duplicates:
            print(f"Processing {subj} {cat} ({count} copies)...")

            # Get all copies
            stmt_courses = (
                select(Course)
                .options(selectinload(Course.prerequisites))
                .where(Course.subject == subj, Course.catalog == cat)
                .order_by(Course.created_at)
            )
            res = await session.execute(stmt_courses)
            courses = res.scalars().all()

            # Strategy: Keep the one with the most prerequisites. If equal, keep the oldest.
            courses.sort(key=lambda c: len(c.prerequisites), reverse=True)

            to_keep = courses[0]
            to_delete_courses = courses[1:]

            print(f"  Keeping ID: {to_keep.id} (Prereqs: {len(to_keep.prerequisites)})")

            for c in to_delete_courses:
                print(
                    f"  Merging & Deleting ID: {c.id} (Prereqs: {len(c.prerequisites)})"
                )

                # 1. Handle usage as a dependency (prereq_course_id)
                # Find rows where c.id is the prerequisite
                stmt_dep = select(Prerequisite).where(
                    Prerequisite.prereq_course_id == c.id
                )
                res_dep = await session.execute(stmt_dep)
                deps = res_dep.scalars().all()

                for dep in deps:
                    # Check if the target course already has 'to_keep' as a prerequisite
                    stmt_exists = select(Prerequisite).where(
                        Prerequisite.course_id == dep.course_id,
                        Prerequisite.prereq_course_id == to_keep.id,
                    )
                    res_exists = await session.execute(stmt_exists)
                    existing = res_exists.scalars().first()

                    if existing:
                        # Redundant, just delete the one pointing to the duplicate
                        await session.delete(dep)
                    else:
                        # Update it to point to the kept course
                        dep.prereq_course_id = to_keep.id
                        session.add(dep)
                        updated_refs_count += 1

                # 2. Delete the course (this should cascade delete its OWN prerequisites list items via relationship if configured, or we delete manually)
                # Just to be safe, delete prerequisites owned by this course first
                stmt_own_prereqs = delete(Prerequisite).where(
                    Prerequisite.course_id == c.id
                )
                await session.execute(stmt_own_prereqs)

                # Now delete the course
                await session.delete(c)
                deleted_count += 1

        await session.commit()
        print(
            f"\nCleanup complete. Deleted {deleted_count} courses. Updated {updated_refs_count} dependency references."
        )


if __name__ == "__main__":
    asyncio.run(cleanup_duplicates())
