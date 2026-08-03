#!/usr/bin/env python
"""
Test: Complete Timetable Generation Flow

Tests the complete flow from session creation to timetable generation.
"""

import asyncio
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

from rich import print as rprint
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.parsers import parse_courses_timetable_excel, parse_students_excel
from app.core.security import hash_password
from app.database import AsyncSessionLocal as AsyncSessionFactory
from app.database import Base, engine
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.session import Session
from app.models.student import Student

# from app.models.base import Base
from app.models.user import User
from app.services.timetable_service import (
    SectionWithCourseInfo,
    TimetableGeneratorV2,
    generate_timetable,
)

console = Console()
SAMPLE_DIR = Path(__file__).parent.parent / "sample_files"


async def reset_database():
    """Reset database schema"""
    console.print("[cyan]Resetting database...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    console.print("[green]  ✓ Database reset")


async def create_admin_user(db: AsyncSession) -> User:
    """Create admin user"""
    console.print("[cyan]Creating admin user...")
    admin = User(
        email="admin@augsd.bits-pilani.ac.in",
        password_hash=hash_password("testpass123"),
        role="admin",
        is_active=True,
    )
    db.add(admin)
    await db.flush()
    await db.refresh(admin)
    console.print(f"[green]  ✓ Admin user created: {admin.id}")
    return admin


async def create_session(db: AsyncSession, admin: User) -> Session:
    """Create a test session"""
    console.print("[cyan]Creating session...")
    session = Session(
        name="Test Session 2025",
        term_code="2025-1",
        career="UG",
        is_enabled=True,
        schema_name="test_session_2025",
        created_by_id=admin.id,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    console.print(f"[green]  ✓ Session created: {session.id}")
    return session


async def upload_courses(db: AsyncSession, session: Session) -> list[Course]:
    """Parse and upload courses from sample file"""
    console.print("[cyan]Parsing and uploading courses...")

    file_path = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"
    if not file_path.exists():
        console.print(f"[red]  ✗ File not found: {file_path}")
        return []

    courses_data, sections_data, parse_result = parse_courses_timetable_excel(
        file_path,
        include_parse_result=True,
    )
    console.print(
        f"  Parsed {len(courses_data)} courses and {len(sections_data)} section rows"
    )
    console.print(
        f"  Parse result: {parse_result.duplicates_removed} duplicates removed"
    )

    # Create courses
    courses_map = {}
    courses_list = []

    for c in courses_data:
        if c.course_id in courses_map:
            continue

        course = Course(
            subject=c.subject,
            catalog=c.catalog,
            title=c.title,
            course_id=c.course_id,
            max_units=c.max_units,
        )
        db.add(course)
        courses_map[c.course_id] = course
        courses_list.append(course)

    await db.flush()

    # Refresh to get IDs
    for course in courses_list:
        await db.refresh(course)

    console.print(f"[green]  ✓ Created {len(courses_list)} courses")

    # Create sections
    sections_created = 0
    seen_sections = set()

    for s in sections_data:
        course = courses_map.get(s["course_id"])
        if not course:
            continue

        # Create unique key for deduplication
        key = (course.id, s.get("class_nbr"), s.get("day"), str(s.get("mtg_start")))
        if key in seen_sections:
            continue
        seen_sections.add(key)

        section = CourseSection(
            course_id=course.id,
            class_nbr=s.get("class_nbr") or 0,
            section=s.get("section", "L1"),
            component=s.get("component", "LEC"),
            class_pattern=s.get("class_pattern"),
            day=s.get("day"),
            mtg_start=s.get("mtg_start"),
            mtg_end=s.get("mtg_end"),
            exam_date=s.get("exam_date"),
            exam_start=s.get("exam_start"),
            exam_end=s.get("exam_end"),
            instructor=s.get("instructor"),
            room=s.get("room"),
            cap_enrl=s.get("cap_enrl") or 0,
            tot_enrl=s.get("tot_enrl") or 0,
        )
        db.add(section)
        sections_created += 1

    await db.flush()
    console.print(f"[green]  ✓ Created {sections_created} sections")

    return courses_list


async def upload_students(db: AsyncSession, session: Session) -> list[Student]:
    """Parse and upload students from sample file"""
    console.print("[cyan]Parsing and uploading students...")

    file_path = SAMPLE_DIR / "BITS_ACTIVE_STUDENTS_ONROLL_431328192.xlsx"
    if not file_path.exists():
        console.print(f"[red]  ✗ File not found: {file_path}")
        return []

    parse_result = parse_students_excel(file_path)
    students_data = parse_result.data
    console.print(
        f"  Parsed {len(students_data)} students, {parse_result.duplicates_removed} duplicates removed"
    )

    # Create students (limit to 100 for testing)
    students_list = []
    seen_ids = set()

    for s in students_data[:100]:
        if s.student_id in seen_ids:
            continue
        seen_ids.add(s.student_id)

        student = Student(
            student_id=s.student_id,
            campus_id=s.campus_id,
            name=s.name,
            email=s.email,
            sex=s.sex,
            birthdate=s.birthdate,
            admission_category=s.admission_category,
        )
        db.add(student)
        students_list.append(student)

    await db.flush()
    for student in students_list:
        await db.refresh(student)

    console.print(f"[green]  ✓ Created {len(students_list)} students")
    return students_list


async def test_timetable_generation_direct():
    """Test timetable generation with direct service call"""
    console.rule("[bold blue]Test: Direct Timetable Generator")

    # Create sample data
    course1_id = uuid.uuid4()
    course2_id = uuid.uuid4()
    course3_id = uuid.uuid4()

    # Course 1: Has LEC on MWF and TUT on T
    sections_course1 = [
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course1_id,
            class_nbr=1001,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="M",
            mtg_start=datetime.strptime("09:00", "%H:%M").time(),
            mtg_end=datetime.strptime("09:50", "%H:%M").time(),
            exam_date=date(2025, 5, 1),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Smith",
            room="F101",
            cap_enrl=60,
            tot_enrl=30,
            subject="CS",
            catalog="F111",
            title="Computer Programming",
            max_units=4.0,
        ),
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course1_id,
            class_nbr=1001,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="W",
            mtg_start=datetime.strptime("09:00", "%H:%M").time(),
            mtg_end=datetime.strptime("09:50", "%H:%M").time(),
            exam_date=date(2025, 5, 1),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Smith",
            room="F101",
            cap_enrl=60,
            tot_enrl=30,
            subject="CS",
            catalog="F111",
            title="Computer Programming",
            max_units=4.0,
        ),
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course1_id,
            class_nbr=1001,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="F",
            mtg_start=datetime.strptime("09:00", "%H:%M").time(),
            mtg_end=datetime.strptime("09:50", "%H:%M").time(),
            exam_date=date(2025, 5, 1),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Smith",
            room="F101",
            cap_enrl=60,
            tot_enrl=30,
            subject="CS",
            catalog="F111",
            title="Computer Programming",
            max_units=4.0,
        ),
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course1_id,
            class_nbr=1002,
            section="T1",
            component="TUT",
            class_pattern="T",
            day="T",
            mtg_start=datetime.strptime("14:00", "%H:%M").time(),
            mtg_end=datetime.strptime("14:50", "%H:%M").time(),
            exam_date=None,
            exam_start=None,
            exam_end=None,
            instructor="TA 1",
            room="F102",
            cap_enrl=30,
            tot_enrl=15,
            subject="CS",
            catalog="F111",
            title="Computer Programming",
            max_units=4.0,
        ),
    ]

    # Course 2: Has LEC on TTh
    sections_course2 = [
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course2_id,
            class_nbr=2001,
            section="L1",
            component="LEC",
            class_pattern="TTh",
            day="T",
            mtg_start=datetime.strptime("10:00", "%H:%M").time(),
            mtg_end=datetime.strptime("11:15", "%H:%M").time(),
            exam_date=date(2025, 5, 2),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Jones",
            room="F201",
            cap_enrl=50,
            tot_enrl=25,
            subject="MATH",
            catalog="F113",
            title="Calculus I",
            max_units=4.0,
        ),
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course2_id,
            class_nbr=2001,
            section="L1",
            component="LEC",
            class_pattern="TTh",
            day="Th",
            mtg_start=datetime.strptime("10:00", "%H:%M").time(),
            mtg_end=datetime.strptime("11:15", "%H:%M").time(),
            exam_date=date(2025, 5, 2),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Jones",
            room="F201",
            cap_enrl=50,
            tot_enrl=25,
            subject="MATH",
            catalog="F113",
            title="Calculus I",
            max_units=4.0,
        ),
    ]

    # Course 3: Conflicts with Course 1 - same time on MWF
    sections_course3 = [
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=course3_id,
            class_nbr=3001,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="M",
            mtg_start=datetime.strptime("09:00", "%H:%M").time(),
            mtg_end=datetime.strptime("09:50", "%H:%M").time(),
            exam_date=date(2025, 5, 3),
            exam_start=datetime.strptime("09:00", "%H:%M").time(),
            exam_end=datetime.strptime("12:00", "%H:%M").time(),
            instructor="Dr. Brown",
            room="F301",
            cap_enrl=40,
            tot_enrl=20,
            subject="PHY",
            catalog="F110",
            title="Physics I",
            max_units=4.0,
        ),
    ]

    sections_by_course = {
        course1_id: sections_course1,
        course2_id: sections_course2,
    }

    # Test 1: Non-conflicting courses
    console.print("[cyan]Test 1: Two non-conflicting courses (CS + MATH)")
    generator = TimetableGeneratorV2(max_units=25.0)
    result = generator.generate(sections_by_course)

    console.print(f"  Success: {result.success}")
    console.print(f"  Partial: {result.partial}")
    console.print(f"  Total units: {result.total_units}")
    console.print(f"  Meetings: {len(result.meetings)}")
    console.print(f"  Conflicts: {len(result.conflicts)}")

    if result.meetings:
        console.print("  Meeting details:")
        for m in result.meetings[:5]:
            console.print(
                f"    - {m.subject} {m.catalog} {m.component} {m.section} {m.day} {m.mtg_start}-{m.mtg_end}"
            )

    if result.success:
        rprint(
            "[green]  ✓ Test 1 passed: Generated timetable for non-conflicting courses"
        )
    else:
        rprint("[red]  ✗ Test 1 failed")

    # Test 2: Add conflicting course
    console.print(
        "\n[cyan]Test 2: Three courses with one conflict (CS conflicts with PHY)"
    )
    sections_by_course_with_conflict = {
        course1_id: sections_course1,
        course2_id: sections_course2,
        course3_id: sections_course3,
    }

    generator2 = TimetableGeneratorV2(max_units=25.0)
    result2 = generator2.generate(sections_by_course_with_conflict)

    console.print(f"  Success: {result2.success}")
    console.print(f"  Partial: {result2.partial}")
    console.print(f"  Total units: {result2.total_units}")
    console.print(f"  Meetings: {len(result2.meetings)}")
    console.print(f"  Conflicts: {len(result2.conflicts)}")

    if result2.conflicts:
        console.print("  Conflict details:")
        for c in result2.conflicts:
            console.print(f"    - {c.type}: {c.message}")

    if result2.partial:
        rprint(
            "[green]  ✓ Test 2 passed: Partial schedule generated with conflict detected"
        )
    else:
        rprint("[yellow]  ~ Test 2: {result2}")

    return True


async def test_with_database():
    """Test complete flow with database"""
    console.rule("[bold blue]Test: Complete Flow with Database")

    await reset_database()

    async with AsyncSessionFactory() as db:
        try:
            # Setup
            admin = await create_admin_user(db)
            session = await create_session(db, admin)
            courses = await upload_courses(db, session)
            students = await upload_students(db, session)

            await db.commit()

            if not courses:
                console.print("[red]No courses uploaded, skipping timetable test")
                return False

            if not students:
                console.print("[red]No students uploaded, skipping timetable test")
                return False

            # Test timetable generation
            console.print("\n[cyan]Testing timetable generation...")

            # Pick a student and some courses
            test_student = students[0]
            test_course_ids = [c.id for c in courses[:5]]  # First 5 courses

            console.print(f"  Student: {test_student.name} ({test_student.campus_id})")
            console.print(f"  Courses: {len(test_course_ids)}")

            result = await generate_timetable(db, test_student.id, test_course_ids)

            console.print("\n  Generation result:")
            console.print(f"    Success: {result.success}")
            console.print(f"    Partial: {result.partial}")
            console.print(f"    Message: {result.message}")
            console.print(f"    Total units: {result.total_units}")
            console.print(f"    Meetings: {len(result.meetings)}")
            console.print(f"    Conflicts: {len(result.conflicts)}")

            if result.meetings:
                console.print("\n  First 10 meetings:")
                for m in result.meetings[:10]:
                    console.print(
                        f"    - {m.subject} {m.catalog} {m.component} {m.section} {m.day} {m.mtg_start}-{m.mtg_end}"
                    )

            if result.conflicts:
                console.print("\n  Conflicts:")
                for c in result.conflicts:
                    console.print(f"    - {c.type}: {c.message}")

            rprint("\n[green]  ✓ Complete flow test finished")
            return True

        except Exception as e:
            console.print(f"[red]Error: {e}")
            import traceback

            traceback.print_exc()
            return False


async def main():
    """Run all tests"""
    console.rule("[bold magenta]TIMETABLE GENERATION FLOW TESTS")

    # Test 1: Direct generator test (no DB)
    await test_timetable_generation_direct()

    # Test 2: Complete flow with database
    await test_with_database()

    console.rule("[bold magenta]TESTS COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
