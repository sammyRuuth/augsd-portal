#!/usr/bin/env python3
"""
Bulk Timetable Generator (Database-backed)

Generates timetables for students from:
- Sessions from the database
- Packages from default_packages table
- Sections from course_sections in session schema
- Student list from Excel file with branch/program markers

Usage:
    uv run python scripts/bulk_timetable_db.py

Or with arguments:
    uv run python scripts/bulk_timetable_db.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025
"""

import argparse
import asyncio
import re
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text, update

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    LogicalSection,
    SectionData,
)
from app.core.algorithms.registry import AlgorithmRegistry
from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.default_package import DefaultPackage
from app.models.session import Session
from app.models.student import Student
from app.models.timetable import Timetable, TimetableItem

# ==================== Data Classes ====================


@dataclass
class StudentInfo:
    """Student information from Excel"""

    campus_id: str
    name: str
    branch: str  # e.g., A5, A1, B2
    program: str  # e.g., PS, RM, UB, CS, IS, RP
    is_pcb: bool = False  # True if student is from PCB background sheet


@dataclass
class PackageInfo:
    """Package information from database"""

    branch: str
    year: int
    course_codes: list[str]


@dataclass
class GeneratedTimetableResult:
    """Result of timetable generation for a student"""

    student_id: uuid.UUID
    campus_id: str
    success: bool
    sections: list[LogicalSection]
    conflicts: list[dict]
    algorithm: str
    execution_time_ms: float
    package_used: str


# ==================== Campus ID Parsing ====================


# Valid branches and programs based on BITS system
VALID_BRANCHES = {
    "A1",
    "A2",
    "A3",
    "A4",
    "A5",
    "A7",
    "A8",
    "AA",
    "AB",
    "AD",
    "AJ",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B7",
    "D2",
}

PROGRAM_SPECIFIC_PACKAGES = {
    "RM",
    "UB",
    "CS",
    "IS",
    "RP",
}  # Programs that have ALL_X packages


def parse_campus_id(campus_id: str) -> tuple[str, str] | None:
    """
    Parse campus ID to extract branch and program.

    Format: YYYYBBPPnnnnC or YYYYBBBPPnnnnC
    Examples:
        - 2025A5PS1518P -> branch=A5, program=PS
        - 2025A1PS0954P -> branch=A1, program=PS
        - 2025B2A30309P -> branch=B2 (dual degree with A3), program=None
        - 2025A7RM1234P -> branch=A7, program=RM

    Returns: (branch, program) or None if invalid
    """
    campus_id = campus_id.strip().upper()

    # Pattern: 4-digit year + branch (2-3 chars) + program (2 chars) + number + campus
    # Single degree: 2025A5PS1518P
    pattern = r"^(\d{4})([A-Z]\d|[A-Z]{2})([A-Z]{2})(\d+)([A-Z])$"
    match = re.match(pattern, campus_id)

    if match:
        year, branch, program, num, campus = match.groups()
        if branch in VALID_BRANCHES:
            return branch, program

    # Try dual degree pattern: 2025B2A30309P
    dual_pattern = r"^(\d{4})([A-Z]\d)([A-Z]\d)(\d+)([A-Z])$"
    match = re.match(dual_pattern, campus_id)

    if match:
        year, branch1, branch2, num, campus = match.groups()
        if branch1 in VALID_BRANCHES:
            return branch1, ""  # Dual degree, no specific program

    return None


def get_package_key_for_student(
    student: StudentInfo, available_packages: set[str]
) -> str | None:
    """
    Determine the best package key for a student based on their branch, program, and PCB status.

    Priority:
    1. Program-specific package (ALL_RM, ALL_UB, ALL_CS, ALL_IS, ALL_RP) if program matches
    2. Branch_PCB package if student is PCB
    3. Branch package (A1, A2, etc.)
    """
    # Check program-specific packages first (RM, UB, CS, IS, RP)
    if student.program in PROGRAM_SPECIFIC_PACKAGES:
        program_key = f"ALL_{student.program}"
        if program_key in available_packages:
            return program_key

    # Check PCB-specific package
    if student.is_pcb:
        pcb_key = f"{student.branch}_PCB"
        if pcb_key in available_packages:
            return pcb_key

    # Fall back to branch package
    if student.branch in available_packages:
        return student.branch

    # Special handling for AJ with PCM Intake
    if student.branch == "AJ" and "AJ (PCM Intake)" in available_packages:
        return "AJ (PCM Intake)"

    return None


# ==================== Database Functions ====================


async def list_sessions() -> list[dict]:
    """List all available sessions from database"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Session)
            .where(Session.is_enabled)
            .order_by(Session.created_at.desc())
        )
        sessions = result.scalars().all()
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "term_code": s.term_code,
                "career": s.career,
                "schema_name": s.schema_name,
            }
            for s in sessions
        ]


async def get_session_by_id(session_id: str) -> dict | None:
    """Get session details by ID"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Session).where(Session.id == uuid.UUID(session_id))
        )
        s = result.scalar_one_or_none()
        if s:
            return {
                "id": str(s.id),
                "name": s.name,
                "term_code": s.term_code,
                "career": s.career,
                "schema_name": s.schema_name,
            }
        return None


async def get_all_packages_for_year(year: int) -> dict[str, PackageInfo]:
    """Get all packages for a year, keyed by branch"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DefaultPackage).where(DefaultPackage.year == year)
        )
        packages = result.scalars().all()

        return {
            p.branch: PackageInfo(
                branch=p.branch,
                year=p.year,
                course_codes=p.course_codes,
            )
            for p in packages
        }


async def get_course_id_map(course_codes: list[str]) -> dict[str, uuid.UUID]:
    """Get mapping of course codes to course IDs from global courses table"""
    async with AsyncSessionLocal() as session:
        course_map = {}

        for code in course_codes:
            parts = code.strip().split()
            if len(parts) >= 2:
                subject = parts[0]
                catalog = " ".join(parts[1:])

                result = await session.execute(
                    select(Course).where(
                        Course.subject == subject, Course.catalog == catalog
                    )
                )
                course = result.scalar_one_or_none()
                if course:
                    course_map[code] = course.id

        return course_map


async def get_sections_for_courses(
    schema_name: str, course_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SectionData]]:
    """Get all sections for given courses from session schema"""
    sections_by_course: dict[uuid.UUID, list[SectionData]] = defaultdict(list)

    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Get course info from global table
        course_info = {}
        for course_id in course_ids:
            result = await session.execute(select(Course).where(Course.id == course_id))
            course = result.scalar_one_or_none()
            if course:
                course_info[course_id] = {
                    "subject": course.subject,
                    "catalog": course.catalog,
                    "title": course.title,
                    "max_units": float(course.max_units) if course.max_units else 3.0,
                }

        # Query sections from session schema
        result = await session.execute(
            select(CourseSection).where(CourseSection.course_id.in_(course_ids))
        )
        sections = result.scalars().all()

        for sec in sections:
            if sec.course_id not in course_info:
                continue

            info = course_info[sec.course_id]
            section_data = SectionData(
                id=sec.id,
                course_id=sec.course_id,
                class_nbr=sec.class_nbr,
                section=sec.section,
                component=sec.component,
                day=sec.day,
                mtg_start=sec.mtg_start,
                mtg_end=sec.mtg_end,
                exam_date=sec.exam_date,
                exam_start=sec.exam_start,
                exam_end=sec.exam_end,
                instructor=sec.instructor,
                room=sec.room,
                cap_enrl=sec.cap_enrl or 0,
                tot_enrl=sec.tot_enrl or 0,
                subject=info["subject"],
                catalog=info["catalog"],
                title=info["title"],
                max_units=info["max_units"],
            )
            sections_by_course[sec.course_id].append(section_data)

    return dict(sections_by_course)


async def get_or_create_student(
    schema_name: str, campus_id: str, name: str
) -> uuid.UUID:
    """Get existing student or create new one in session schema"""
    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Check if student exists
        result = await session.execute(
            select(Student).where(Student.campus_id == campus_id)
        )
        student = result.scalar_one_or_none()

        if student:
            return student.id

        # Generate a unique student_id from campus_id
        numeric_part = "".join(c for c in campus_id if c.isdigit())
        student_id = int(numeric_part) if numeric_part else hash(campus_id) % (10**12)

        # Create new student
        new_student = Student(
            student_id=student_id,
            campus_id=campus_id,
            name=name,
        )
        session.add(new_student)
        await session.commit()
        await session.refresh(new_student)
        return new_student.id


async def get_system_user_id() -> uuid.UUID:
    """Get or create a system user for bulk operations"""
    from app.models.user import User

    async with AsyncSessionLocal() as session:
        # Look for existing system user
        result = await session.execute(
            select(User).where(User.email == "system@bulk-generator.local")
        )
        user = result.scalar_one_or_none()

        if user:
            return user.id

        # Try to find any admin user to use
        result = await session.execute(
            select(User).where(User.role == "admin").limit(1)
        )
        admin = result.scalar_one_or_none()

        if admin:
            return admin.id

        # Return a fallback - get any user
        result = await session.execute(select(User).limit(1))
        any_user = result.scalar_one_or_none()

        if any_user:
            return any_user.id

        raise ValueError("No users found in database. Please create a user first.")


def update_in_memory_enrollment(
    all_sections_by_course: dict[uuid.UUID, list[SectionData]],
    committed_sections: list[LogicalSection],
) -> None:
    """
    Update in-memory section enrollment counts after a timetable is committed.

    This ensures subsequent timetable generations see accurate enrollment counts
    and prevents over-enrollment of sections.
    """
    # Get all section IDs that were committed (unique by class_nbr)
    committed_section_ids: set[uuid.UUID] = set()
    for logical_sec in committed_sections:
        for meeting in logical_sec.meetings:
            committed_section_ids.add(meeting.id)

    # Update tot_enrl in memory for all matching sections
    for course_id, sections in all_sections_by_course.items():
        for section in sections:
            if section.id in committed_section_ids:
                section.tot_enrl += 1


async def commit_timetable(
    schema_name: str,
    student_id: uuid.UUID,
    sections: list[LogicalSection],
    created_by_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """
    Commit a generated timetable to the database.
    Creates timetable and timetable_items records,
    and increments tot_enrl for each section.
    """
    if not sections:
        return None

    if created_by_id is None:
        created_by_id = await get_system_user_id()

    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text(f'SET search_path TO "{schema_name}", public'))

            # Calculate total units
            total_units = sum(
                sec.max_units for sec in sections if sec.component.upper() == "LEC"
            )

            # Create timetable
            timetable = Timetable(
                student_id=student_id,
                source="portal_generated",
                status="committed",
                total_units=total_units,
                created_by_id=created_by_id,
            )
            session.add(timetable)
            await session.flush()

            # Get unique class_nbrs (sections can have multiple meetings)
            seen_class_nbrs = set()
            section_ids_to_update = []

            for sec in sections:
                # Add timetable items for each meeting
                for meeting in sec.meetings:
                    item = TimetableItem(
                        timetable_id=timetable.id,
                        course_section_id=meeting.id,
                    )
                    session.add(item)

                # Track unique class_nbrs for enrollment update
                if sec.class_nbr not in seen_class_nbrs:
                    seen_class_nbrs.add(sec.class_nbr)
                    for meeting in sec.meetings:
                        section_ids_to_update.append(meeting.id)

            # Increment tot_enrl for all affected sections
            if section_ids_to_update:
                await session.execute(
                    update(CourseSection)
                    .where(CourseSection.id.in_(section_ids_to_update))
                    .values(tot_enrl=CourseSection.tot_enrl + 1)
                )

            await session.commit()
            return timetable.id

        except Exception as e:
            await session.rollback()
            print(f"Error committing timetable: {e}")
            return None


# ==================== Excel Parsing ====================


def parse_student_excel(file_path: str) -> list[StudentInfo]:
    """
    Parse Excel file to extract ALL students with their branch/program info.

    Handles:
    - Multiple sheets
    - PCB-marked sheets (e.g., A5-PCB, AJ_PCB)
    - Regular sheets with mixed branches

    Returns: List of StudentInfo with branch, program, and PCB status
    """
    xl = pd.ExcelFile(file_path)
    all_students: list[StudentInfo] = []
    pcb_campus_ids: set[str] = set()

    print(f"\nFound sheets: {xl.sheet_names}")

    # First pass: identify PCB students from PCB-marked sheets
    for sheet_name in xl.sheet_names:
        sheet_upper = sheet_name.upper()
        is_pcb_sheet = any(marker in sheet_upper for marker in ["_PCB", "-PCB", " PCB"])

        if is_pcb_sheet:
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)

            # Find header row
            header_row = 0
            for idx in range(min(5, len(df))):
                row_values = df.iloc[idx].astype(str).str.upper().tolist()
                if any("ID" in val for val in row_values):
                    header_row = idx
                    break

            df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
            df.columns = df.columns.str.strip()

            # Find ID column
            id_col = None
            for col in df.columns:
                if "ID" in str(col).upper():
                    id_col = col
                    break

            if id_col:
                for _, row in df.iterrows():
                    campus_id = str(row.get(id_col, "")).strip().upper()
                    if campus_id and campus_id != "NAN" and len(campus_id) > 5:
                        pcb_campus_ids.add(campus_id)

            print(
                f"  Sheet '{sheet_name}' (PCB): {len(pcb_campus_ids)} PCB students identified"
            )

    # Second pass: process all sheets and extract students
    seen_campus_ids: set[str] = set()

    for sheet_name in xl.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)

        # Find header row
        header_row = 0
        for idx in range(min(5, len(df))):
            row_values = df.iloc[idx].astype(str).str.upper().tolist()
            if any("ID" in val for val in row_values):
                header_row = idx
                break

        df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
        df.columns = df.columns.str.strip()

        # Find ID and Name columns
        id_col = None
        name_col = None
        for col in df.columns:
            col_upper = str(col).upper()
            if "ID" in col_upper and id_col is None:
                id_col = col
            elif "NAME" in col_upper and name_col is None:
                name_col = col

        if not id_col or not name_col:
            print(f"  Sheet '{sheet_name}': Could not find ID/Name columns, skipping")
            continue

        sheet_count = 0
        for _, row in df.iterrows():
            campus_id = str(row.get(id_col, "")).strip().upper()
            name = str(row.get(name_col, "")).strip()

            if not campus_id or campus_id == "NAN" or len(campus_id) < 5:
                continue

            # Skip duplicates
            if campus_id in seen_campus_ids:
                continue
            seen_campus_ids.add(campus_id)

            # Parse campus ID
            parsed = parse_campus_id(campus_id)
            if not parsed:
                print(f"    Warning: Could not parse campus_id '{campus_id}'")
                continue

            branch, program = parsed
            is_pcb = campus_id in pcb_campus_ids

            all_students.append(
                StudentInfo(
                    campus_id=campus_id,
                    name=name,
                    branch=branch,
                    program=program,
                    is_pcb=is_pcb,
                )
            )
            sheet_count += 1

        print(f"  Sheet '{sheet_name}': {sheet_count} students extracted")

    return all_students


# ==================== Timetable Generation ====================


def generate_timetable(
    sections_by_course: dict[uuid.UUID, list[SectionData]],
    algorithm_name: str = "backtrack_optimized",
) -> AlgorithmResult:
    """
    Generate a timetable using the specified algorithm.

    For non-parallel algorithms, runs 3 times and returns the best result.
    If no best result found (all same score), retries up to 3 more batches (max 9 runs).
    For parallel algorithms, runs once.
    """
    constraints = GenerationConstraints()

    # Parallel algorithms that should only run once
    parallel_algorithms = {""}

    # Determine if parallel
    is_parallel = algorithm_name in parallel_algorithms

    if is_parallel:
        # Parallel algorithms run once
        try:
            algorithm = AlgorithmRegistry.get(algorithm_name, constraints)
            return algorithm.generate(sections_by_course)
        except Exception as e:
            print(f"  Error with algorithm {algorithm_name}: {e}")
            return AlgorithmResult(
                success=False,
                partial=False,
                message=str(e),
            )

    # Non-parallel algorithms: run up to 3 batches of 3 attempts (max 9 total)
    max_batches = 3
    runs_per_batch = 3

    try:
        all_results = []

        for batch in range(max_batches):
            batch_results = []

            # Run 3 attempts in this batch
            for run in range(runs_per_batch):
                algorithm = AlgorithmRegistry.get(algorithm_name, constraints)
                result = algorithm.generate(sections_by_course)

                # Score the result
                # Priority: success > partial > failure
                # For successful results, prefer more courses
                score = 0
                if result.success:
                    score = 1000 + result.course_count
                elif result.partial:
                    score = 500 + result.course_count
                else:
                    score = 0

                batch_results.append((result, score))
                all_results.append((result, score))

            # Check if we have a clear best in this batch
            scores = [score for _, score in batch_results]
            max_score = max(scores)

            # If we have a unique best score (not all the same), return it
            if scores.count(max_score) == 1:
                best_result = [r for r, s in batch_results if s == max_score][0]
                return best_result

            # If all scores are the same but we have a successful result, return it
            if max_score >= 1000:  # Success threshold
                return batch_results[0][0]

        # After all batches, return the best overall result
        if all_results:
            max_score = max(score for _, score in all_results)
            best_result = [r for r, s in all_results if s == max_score][0]
            return best_result

        return AlgorithmResult(
            success=False,
            partial=False,
            message="All runs failed",
        )
    except Exception as e:
        print(f"  Error with algorithm {algorithm_name}: {e}")
        return AlgorithmResult(
            success=False,
            partial=False,
            message=str(e),
        )


# ==================== Main Functions ====================


async def interactive_session_select() -> dict | None:
    """Interactive session selection"""
    sessions = await list_sessions()

    if not sessions:
        print("No sessions found in database!")
        return None

    print("\n" + "=" * 60)
    print("AVAILABLE SESSIONS")
    print("=" * 60)

    for i, s in enumerate(sessions, 1):
        print(f"  [{i}] {s['name']} ({s['term_code']}) - {s['career']}")
        print(f"      ID: {s['id']}")
        print(f"      Schema: {s['schema_name']}")
        print()

    while True:
        try:
            choice = input("Select session number (or 'q' to quit): ").strip()
            if choice.lower() == "q":
                return None

            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
            else:
                print(f"Invalid choice. Please enter 1-{len(sessions)}")
        except ValueError:
            print("Please enter a valid number")


async def run_bulk_generation(
    session_info: dict,
    excel_path: str,
    year: int,
    algorithm: str = "backtrack_optimized",
    dry_run: bool = False,
):
    """Run bulk timetable generation"""
    schema_name = session_info["schema_name"]

    print("\n" + "=" * 60)
    print("BULK TIMETABLE GENERATION")
    print("=" * 60)
    print(f"Session: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print(f"Excel: {excel_path}")
    print(f"Year: {year}")
    print(f"Algorithm: {algorithm}")
    print(f"Dry Run: {dry_run}")

    # Parse Excel for ALL students
    print("\n" + "-" * 60)
    print("PARSING STUDENT LIST")
    print("-" * 60)

    students = parse_student_excel(excel_path)

    if not students:
        print("No students found in Excel file!")
        return

    # Group students by branch/program for summary
    by_branch: dict[str, list[StudentInfo]] = defaultdict(list)
    by_program: dict[str, list[StudentInfo]] = defaultdict(list)
    pcb_students = [s for s in students if s.is_pcb]

    for s in students:
        by_branch[s.branch].append(s)
        if s.program:
            by_program[s.program].append(s)

    print(f"\nTotal students: {len(students)}")
    print(f"PCB students: {len(pcb_students)}")
    print("\nBy branch:")
    for branch in sorted(by_branch.keys()):
        print(f"  {branch}: {len(by_branch[branch])}")
    print("\nBy program:")
    for program in sorted(by_program.keys()):
        print(f"  {program}: {len(by_program[program])}")

    # Get ALL packages for this year
    print("\n" + "-" * 60)
    print("FETCHING PACKAGES FROM DATABASE")
    print("-" * 60)

    packages = await get_all_packages_for_year(year)

    if not packages:
        print(f"No packages found for year {year}")
        return

    available_packages = set(packages.keys())
    print(f"Found {len(packages)} packages:")
    for branch in sorted(packages.keys()):
        pkg = packages[branch]
        print(f"  {branch}: {len(pkg.course_codes)} courses")

    # Get all unique course codes across all packages
    all_course_codes: set[str] = set()
    for pkg in packages.values():
        all_course_codes.update(pkg.course_codes)

    # Get course ID mapping
    print("\n" + "-" * 60)
    print("MAPPING COURSES")
    print("-" * 60)

    course_id_map = await get_course_id_map(list(all_course_codes))
    print(f"Mapped {len(course_id_map)}/{len(all_course_codes)} courses to IDs")

    missing_courses = all_course_codes - set(course_id_map.keys())
    if missing_courses:
        print(f"  Missing courses: {missing_courses}")

    # Get sections for all courses
    print("\n" + "-" * 60)
    print("FETCHING SECTIONS FROM SESSION")
    print("-" * 60)

    all_course_ids = list(course_id_map.values())
    all_sections_by_course = await get_sections_for_courses(schema_name, all_course_ids)

    print(f"Found sections for {len(all_sections_by_course)} courses")
    total_sections = sum(len(secs) for secs in all_sections_by_course.values())
    print(f"Total section records: {total_sections}")

    # Build reverse map: course_id -> course_code
    {cid: code for code, cid in course_id_map.items()}

    # Generate timetables for each student
    print("\n" + "-" * 60)
    print("GENERATING TIMETABLES")
    print("-" * 60)
    print(f"Algorithm: {algorithm}")
    is_parallel = algorithm in {"parallel_best", "parallel_race"}
    if is_parallel:
        print("Run strategy: Single run (parallel algorithm)")
    else:
        print("Run strategy: Best of 3 runs per student (up to 9 if needed)")
    print()

    results_by_package: dict[str, list[GeneratedTimetableResult]] = defaultdict(list)
    package_stats: dict[str, dict] = defaultdict(
        lambda: {
            "total": 0,
            "success": 0,
            "partial": 0,
            "fail": 0,
            "no_sections": 0,
            "total_time_ms": 0,
            "failed_students": [],
        }
    )

    success_count = 0
    fail_count = 0
    partial_count = 0
    no_package_count = 0
    no_sections_count = 0
    students_no_package = []

    for i, student in enumerate(students):
        # Find appropriate package for this student
        package_key = get_package_key_for_student(student, available_packages)

        if not package_key:
            no_package_count += 1
            students_no_package.append(
                f"{student.campus_id} ({student.branch}/{student.program})"
            )
            continue

        pkg = packages[package_key]
        package_stats[package_key]["total"] += 1

        # Get course IDs for this package
        package_course_ids = [
            course_id_map[code] for code in pkg.course_codes if code in course_id_map
        ]

        # Filter sections to only this package's courses
        package_sections = {
            cid: all_sections_by_course[cid]
            for cid in package_course_ids
            if cid in all_sections_by_course
        }

        if not package_sections:
            no_sections_count += 1
            package_stats[package_key]["no_sections"] += 1
            package_stats[package_key]["failed_students"].append(
                f"{student.campus_id} (no sections)"
            )
            continue

        # Get or create student in DB
        student_id = await get_or_create_student(
            schema_name, student.campus_id, student.name
        )

        # Generate timetable
        result = generate_timetable(
            package_sections,
            algorithm_name=algorithm,
        )

        gen_result = GeneratedTimetableResult(
            student_id=student_id,
            campus_id=student.campus_id,
            success=result.success,
            sections=result.selected_sections,
            conflicts=result.conflicts,
            algorithm=algorithm,
            execution_time_ms=result.execution_time_ms,
            package_used=package_key,
        )
        results_by_package[package_key].append(gen_result)
        package_stats[package_key]["total_time_ms"] += result.execution_time_ms

        if result.success:
            success_count += 1
            package_stats[package_key]["success"] += 1

            # Update in-memory enrollment counts so subsequent students
            # see accurate capacity data (both dry run and real run)
            update_in_memory_enrollment(
                all_sections_by_course,
                result.selected_sections,
            )

            # Commit timetable if not dry run
            if not dry_run:
                timetable_id = await commit_timetable(
                    schema_name,
                    student_id,
                    result.selected_sections,
                )
                if timetable_id:
                    pass
                else:
                    pass
        elif result.partial:
            partial_count += 1
            package_stats[package_key]["partial"] += 1
            package_stats[package_key]["failed_students"].append(
                f"{student.campus_id} (partial: {result.course_count}/{len(package_course_ids)})"
            )
            f"⚠ PARTIAL {result.course_count}/{len(package_course_ids)}"
        else:
            fail_count += 1
            package_stats[package_key]["fail"] += 1
            package_stats[package_key]["failed_students"].append(
                f"{student.campus_id} (failed)"
            )

        # Progress update - show more detail
        if (i + 1) % 20 == 0 or (i + 1) == len(students):
            total_processed = success_count + partial_count + fail_count
            success_pct = (
                (success_count / total_processed * 100) if total_processed > 0 else 0
            )
            print(
                f"  [{i + 1:4d}/{len(students)}] ✓ {success_count:3d} | "
                f"⚠ {partial_count:3d} | ✗ {fail_count:3d} | "
                f"Success: {success_pct:5.1f}%"
            )

    # Enhanced Summary
    print("\n" + "=" * 80)
    print(" " * 25 + "GENERATION SUMMARY")
    print("=" * 80)

    total_processed = success_count + partial_count + fail_count

    print(f"\n{'OVERALL STATISTICS':^80}")
    print("-" * 80)
    print(f"  Total students in Excel:     {len(students):5d}")
    print(f"  No package found:            {no_package_count:5d}")
    print(f"  No sections available:       {no_sections_count:5d}")
    print(f"  Processed:                   {total_processed:5d}")
    print()
    print(
        f"  ✓ Successful:                {success_count:5d}  ({success_count / total_processed * 100:5.1f}%)"
        if total_processed > 0
        else "  ✓ Successful:                    0"
    )
    print(
        f"  ⚠ Partial:                   {partial_count:5d}  ({partial_count / total_processed * 100:5.1f}%)"
        if total_processed > 0
        else "  ⚠ Partial:                       0"
    )
    print(
        f"  ✗ Failed:                    {fail_count:5d}  ({fail_count / total_processed * 100:5.1f}%)"
        if total_processed > 0
        else "  ✗ Failed:                        0"
    )

    # Package breakdown
    print(f"\n{'PACKAGE BREAKDOWN':^80}")
    print("-" * 80)
    print(
        f"{'Package':<20} {'Total':>7} {'Success':>7} {'Partial':>7} {'Failed':>7} {'Avg Time':>10}"
    )
    print("-" * 80)

    for package_key in sorted(package_stats.keys()):
        stats = package_stats[package_key]
        total = stats["total"]
        success = stats["success"]
        partial = stats["partial"]
        failed = stats["fail"] + stats["no_sections"]
        avg_time = stats["total_time_ms"] / total if total > 0 else 0

        success_rate = (success / total * 100) if total > 0 else 0

        # Color code by success rate
        if success_rate >= 95:
            status_icon = "✓"
        elif success_rate >= 80:
            status_icon = "⚠"
        else:
            status_icon = "✗"

        print(
            f"{status_icon} {package_key:<18} {total:7d} {success:7d} {partial:7d} {failed:7d} {avg_time:9.1f}ms"
        )

    # Failed students detail
    print(f"\n{'FAILED/PARTIAL STUDENTS DETAIL':^80}")
    print("-" * 80)

    if students_no_package:
        print(f"\n  No Package Found ({len(students_no_package)} students):")
        for student in students_no_package[:10]:
            print(f"    • {student}")
        if len(students_no_package) > 10:
            print(f"    ... and {len(students_no_package) - 10} more")

    for package_key in sorted(package_stats.keys()):
        stats = package_stats[package_key]
        if stats["failed_students"]:
            print(
                f"\n  {package_key} ({len(stats['failed_students'])} failed/partial):"
            )
            for student in stats["failed_students"][:5]:
                print(f"    • {student}")
            if len(stats["failed_students"]) > 5:
                print(f"    ... and {len(stats['failed_students']) - 5} more")

    # Algorithm run statistics
    print(f"\n{'ALGORITHM RUN STATISTICS':^80}")
    print("-" * 80)
    print(f"  Algorithm: {algorithm}")
    if not is_parallel:
        print("  Run strategy: Best of 3 runs per student (max 9 with retries)")
        total_runs = sum(len(results) for results in results_by_package.values())
        # Note: Each student can have 3-9 runs, we're showing approximate total
        avg_runs_estimate = total_runs * 3  # Minimum estimate
        print(f"  Estimated minimum algorithm runs: ~{avg_runs_estimate}")
    else:
        print("  Run strategy: Single run (parallel algorithm)")

    total_time = sum(stats["total_time_ms"] for stats in package_stats.values())
    avg_time_per_student = total_time / total_processed if total_processed > 0 else 0
    print(f"  Total generation time: {total_time / 1000:.2f}s")
    print(f"  Average time per student: {avg_time_per_student:.1f}ms")

    print("\n" + "=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Bulk Timetable Generator (DB-backed)")
    parser.add_argument(
        "--excel",
        type=str,
        help="Path to Excel file with student list",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Year for package filtering (default: 2025)",
    )
    parser.add_argument(
        "--session",
        type=str,
        help="Session ID (if not provided, interactive selection)",
    )
    parser.add_argument(
        "--algorithm",
        type=str,
        default="backtrack_optimized",
        choices=[
            "greedy",
            "backtrack",
            "backtrack_optimized",
            "genetic",
            "random",
            "random_restart",
            "simulated_annealing",
            "hybrid",
            "parallel_best",
            "parallel_race",
        ],
        help="Algorithm to use (default: backtrack_optimized)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate without committing to database",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List available sessions and exit",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("BULK TIMETABLE GENERATOR (Database-backed)")
    print("=" * 60)

    # List sessions only
    if args.list_sessions:
        sessions = await list_sessions()
        print("\nAvailable Sessions:")
        for s in sessions:
            print(f"  ID: {s['id']}")
            print(f"  Name: {s['name']}")
            print(f"  Term: {s['term_code']}")
            print(f"  Schema: {s['schema_name']}")
            print()
        return

    # Get session
    if args.session:
        session_info = await get_session_by_id(args.session)
        if not session_info:
            print(f"Session not found: {args.session}")
            return
    else:
        session_info = await interactive_session_select()
        if not session_info:
            print("No session selected. Exiting.")
            return

    # Check Excel file
    if not args.excel:
        args.excel = input("Enter path to Excel file: ").strip()

    if not Path(args.excel).exists():
        print(f"Excel file not found: {args.excel}")
        return

    # Run generation
    await run_bulk_generation(
        session_info=session_info,
        excel_path=args.excel,
        year=args.year,
        algorithm=args.algorithm,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
