import asyncio
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.parsers import parse_prerequisites_excel
from app.database import engine
from app.models.course import Course
from app.models.prerequisite import Prerequisite


async def seed_prerequisites():
    file_path = Path("sample_files/Pre-requisite_18-01-2025.xlsx")
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return

    print(f"Parsing {file_path}...")
    prereq_data = parse_prerequisites_excel(file_path)
    print(f"Parsed {len(prereq_data)} prerequisite records.")

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        processed_count = 0
        skipped_count = 0

        # Cache courses to avoid repetitive lookups
        # Map "SUBJECT CATALOG" -> Course ID
        print("Caching courses...")
        result = await session.execute(select(Course))
        courses = result.scalars().all()
        course_map = {f"{c.subject} {c.catalog}".strip(): c.id for c in courses}
        print(f"Cached {len(course_map)} courses.")

        print("Inserting prerequisites...")
        for prereq in prereq_data:
            subject = prereq.get("subject", "").strip()
            catalog = prereq.get("catalog", "").strip()
            course_key = f"{subject} {catalog}"

            if course_key not in course_map:
                # print(f"Skipping unknown course: {course_key}")
                skipped_count += 1
                continue

            course_id = course_map[course_key]

            # Parse prereq value
            prereq_str = prereq.get("prereq_value", "").strip()
            if not prereq_str:
                continue

            parts = prereq_str.split()
            if len(parts) < 2:
                skipped_count += 1
                continue

            p_subject = parts[0]
            p_catalog = parts[1]
            p_key = f"{p_subject} {p_catalog}"

            if p_key not in course_map:
                # print(f"Skipping unknown prereq course: {p_key}")
                skipped_count += 1
                continue

            p_course_id = course_map[p_key]

            # Check duplicate rule
            existing = await session.execute(
                select(Prerequisite).where(
                    Prerequisite.course_id == course_id,
                    Prerequisite.prereq_course_id == p_course_id,
                    Prerequisite.prereq_order == prereq.get("prereq_order", 1),
                )
            )
            if existing.scalars().first():
                continue

            new_prereq = Prerequisite(
                course_id=course_id,
                prereq_course_id=p_course_id,
                prereq_type=prereq.get("prereq_type", "AND"),
                prereq_order=prereq.get("prereq_order", 1),
                is_corequisite=prereq.get("is_corequisite", False),
            )
            session.add(new_prereq)
            processed_count += 1

            if processed_count % 100 == 0:
                print(f"Processed {processed_count}...")

        await session.commit()
        print(
            f"Done! Added {processed_count} rules. Skipped {skipped_count} entries (mostly duplicates or missing courses)."
        )


if __name__ == "__main__":
    asyncio.run(seed_prerequisites())
