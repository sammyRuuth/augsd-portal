#!/usr/bin/env python3
"""
Commit Bulk Timetables to Database

Reads generated timetables from CSV files and commits them to a selected session.
Maps students from an Excel file to their appropriate timetables based on branch/program.

Usage:
    uv run python scripts/commit_bulk_timetables.py

Or with arguments:
    uv run python scripts/commit_bulk_timetables.py --timetables exports/bulk_timetables --excel data/students.xlsx --session-id <uuid>
"""

import argparse
import asyncio
import csv
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text, update

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.session import Session
from app.models.student import Student
from app.models.timetable import Timetable, TimetableItem
from timetable_generator.config import Config, load_config

# ==================== Data Classes ====================


@dataclass
class StudentInfo:
    """Student information from Excel"""

    campus_id: str
    name: str
    branch: str
    program: str
    is_pcb: bool = False


@dataclass
class TimetableInfo:
    """Timetable information from CSV"""

    plan: str
    timetable_id: int
    batch_size: int
    capacity_ceiling: int
    is_variant: bool
    class_nbrs: list[int]


# ==================== Student Parsing ====================


# Valid branches and programs
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
    "C1",
    "C2",
    "C3",
    "C4",
    "C5",
    "C6",
    "C7",
    "C8",
    "D2",
    "H1",
    "H7",
    "PS",
    "RM",
}

VALID_PROGRAMS = {
    "PS",
    "RM",
    "UB",
    "CS",
    "IS",
    "RP",
    "MM",
}


def parse_campus_id(campus_id: str) -> tuple[str, str] | None:
    """Parse campus ID to extract branch and program."""
    if not campus_id or not isinstance(campus_id, str):
        return None

    campus_id = campus_id.strip().upper()

    # Format: YYYYAABBCPPPNNNNN (e.g., 2023A5PSH0001P)
    if len(campus_id) < 12:
        return None

    try:
        year_part = campus_id[:4]
        branch_part = campus_id[4:6]
        program_part = campus_id[6:8]

        if not year_part.isdigit():
            return None

        branch = branch_part
        program = program_part if program_part in VALID_PROGRAMS else ""

        return branch, program
    except Exception:
        return None


def parse_student_excel(file_path: str) -> list[StudentInfo]:
    """Parse Excel file to extract students with branch/program info."""
    students = []

    try:
        xl = pd.ExcelFile(file_path)
        sheet_names = xl.sheet_names

        for sheet_name in sheet_names:
            is_pcb = "PCB" in sheet_name.upper()

            # First, detect the header row by looking for "Campus ID" column
            df_raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
            header_row = 0

            for idx in range(min(10, len(df_raw))):
                row_values = df_raw.iloc[idx].astype(str).str.lower().tolist()
                # Look for "campus" and "id" in the same cell
                if any("campus" in val and "id" in val for val in row_values):
                    header_row = idx
                    break
                # Also check for just "campus id" as separate words
                if any("campus id" in val for val in row_values):
                    header_row = idx
                    break

            # Re-read with correct header row
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
            df.columns = df.columns.astype(str).str.strip()

            # Find campus ID column
            campus_col = None
            name_col = None

            for col in df.columns:
                col_lower = str(col).lower()
                if "campus" in col_lower and "id" in col_lower:
                    campus_col = col
                elif col_lower in ["name", "student name", "full name"]:
                    name_col = col

            if campus_col is None:
                # Try first column
                campus_col = df.columns[0] if len(df.columns) > 0 else None

            if campus_col is None:
                continue

            for _, row in df.iterrows():
                campus_id = str(row.get(campus_col, "")).strip()
                if not campus_id or campus_id == "nan":
                    continue

                parsed = parse_campus_id(campus_id)
                if parsed is None:
                    continue

                branch, program = parsed
                name = str(row.get(name_col, "")).strip() if name_col else ""

                students.append(
                    StudentInfo(
                        campus_id=campus_id,
                        name=name,
                        branch=branch,
                        program=program,
                        is_pcb=is_pcb,
                    )
                )

    except Exception as e:
        print(f"Error parsing Excel: {e}")

    return students


# ==================== Timetable CSV Parsing ====================


def parse_timetables_csv(timetables_dir: str) -> dict[str, list[TimetableInfo]]:
    """
    Parse timetables from the classnbrs CSV file.

    Returns dict mapping plan -> list of TimetableInfo
    """
    timetables: dict[str, list[TimetableInfo]] = defaultdict(list)

    classnbrs_file = Path(timetables_dir) / "timetables_classnbrs.csv"
    summary_file = Path(timetables_dir) / "timetables_summary.csv"

    if not classnbrs_file.exists():
        print(f"Error: {classnbrs_file} not found")
        return {}

    # First, get batch sizes from summary
    batch_info: dict[tuple[str, int], dict] = {}
    if summary_file.exists():
        with open(summary_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                plan = row.get("Plan", "")
                tt_id = int(row.get("Timetable ID", 0))
                key = (plan, tt_id)
                if key not in batch_info:
                    batch_info[key] = {
                        "batch_size": int(row.get("Batch Size", 0)),
                        "capacity_ceiling": int(row.get("Capacity Ceiling", 0)),
                        "is_variant": row.get("Variant", "no").lower() == "yes",
                    }

    # Parse class numbers
    tt_class_nbrs: dict[tuple[str, int], list[int]] = defaultdict(list)

    with open(classnbrs_file, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            plan = row.get("Plan", "")
            tt_id = int(row.get("Timetable ID", 0))
            class_nbr = int(row.get("Class Nbr", 0))

            key = (plan, tt_id)
            if class_nbr not in tt_class_nbrs[key]:
                tt_class_nbrs[key].append(class_nbr)

    # Build TimetableInfo objects
    for (plan, tt_id), class_nbrs in tt_class_nbrs.items():
        info = batch_info.get((plan, tt_id), {})
        timetables[plan].append(
            TimetableInfo(
                plan=plan,
                timetable_id=tt_id,
                batch_size=info.get("batch_size", 0),
                capacity_ceiling=info.get("capacity_ceiling", 0),
                is_variant=info.get("is_variant", False),
                class_nbrs=class_nbrs,
            )
        )

    # Sort by timetable_id
    for plan in timetables:
        timetables[plan].sort(key=lambda t: t.timetable_id)

    return timetables


# ==================== Database Functions ====================


async def list_sessions() -> list[dict]:
    """List all available sessions"""
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


async def get_system_user_id() -> uuid.UUID:
    """Get or create system user ID for automated operations"""
    # Use a fixed UUID for system operations
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


async def get_or_create_student(
    schema_name: str,
    campus_id: str,
    name: str = "",
) -> uuid.UUID | None:
    """Get existing student or create new one"""
    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Check if student exists
        result = await session.execute(
            select(Student).where(Student.campus_id == campus_id)
        )
        student = result.scalar_one_or_none()

        if student:
            return student.id

        # Create new student
        try:
            new_student = Student(
                campus_id=campus_id,
                name=name or campus_id,
                email=f"{campus_id.lower()}@hyderabad.bits-pilani.ac.in",
            )
            session.add(new_student)
            await session.commit()
            return new_student.id
        except Exception as e:
            await session.rollback()
            print(f"Error creating student {campus_id}: {e}")
            return None


async def get_section_ids_for_class_nbrs(
    schema_name: str,
    class_nbrs: list[int],
) -> dict[int, list[uuid.UUID]]:
    """Get section IDs for given class numbers"""
    section_ids: dict[int, list[uuid.UUID]] = defaultdict(list)

    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        result = await session.execute(
            select(CourseSection).where(CourseSection.class_nbr.in_(class_nbrs))
        )
        sections = result.scalars().all()

        for s in sections:
            section_ids[s.class_nbr].append(s.id)

    return section_ids


async def apply_capacity_overrides(
    schema_name: str,
    config: Config,
) -> dict[str, int]:
    """
    Apply capacity overrides from config to database sections.

    Returns dict of course_code -> sections_updated count.
    """
    updates = {}

    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Get all course sections with course info
        result = await session.execute(
            select(CourseSection, Course)
            .join(Course, CourseSection.course_id == Course.id)
        )
        rows = result.all()

        for section, course in rows:
            # Build course code from course data
            course_code = f"{course.subject} {course.catalog}"
            component = section.component.upper() if section.component else ""

            # Check if there's a capacity override for this course/component
            override = config.get_section_capacity_override(course_code, component)

            if override is not None and section.cap_enrl != override:
                old_cap = section.cap_enrl
                section.cap_enrl = override

                if course_code not in updates:
                    updates[course_code] = 0
                updates[course_code] += 1

                print(
                    f"  Updated {course_code} {section.section} ({component}): "
                    f"{old_cap} -> {override}"
                )

        await session.commit()

    return updates


async def commit_student_timetable(
    schema_name: str,
    student_id: uuid.UUID,
    section_ids: list[uuid.UUID],
    class_nbrs: list[int],
    created_by_id: uuid.UUID,
) -> uuid.UUID | None:
    """Commit a timetable for a student"""
    if not section_ids:
        return None

    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text(f'SET search_path TO "{schema_name}", public'))

            # Check if student already has a timetable
            existing = await session.execute(
                select(Timetable).where(Timetable.student_id == student_id)
            )
            if existing.scalar_one_or_none():
                # Student already has timetable, skip
                return None

            # Create timetable
            timetable = Timetable(
                student_id=student_id,
                source="portal_generated",
                status="committed",
                total_units=0,  # Will be calculated later if needed
                created_by_id=created_by_id,
            )
            session.add(timetable)
            await session.flush()

            # Add timetable items (unique section IDs)
            seen_ids = set()
            for sec_id in section_ids:
                if sec_id not in seen_ids:
                    seen_ids.add(sec_id)
                    item = TimetableItem(
                        timetable_id=timetable.id,
                        course_section_id=sec_id,
                    )
                    session.add(item)

            # Increment tot_enrl for affected sections (by unique class_nbr)
            unique_class_nbrs = list(set(class_nbrs))
            if unique_class_nbrs:
                # Get one section ID per class_nbr for update
                section_ids_to_update = []
                for class_nbr in unique_class_nbrs:
                    result = await session.execute(
                        select(CourseSection.id)
                        .where(CourseSection.class_nbr == class_nbr)
                        .limit(1)
                    )
                    sec_id = result.scalar_one_or_none()
                    if sec_id:
                        section_ids_to_update.append(sec_id)

                if section_ids_to_update:
                    await session.execute(
                        update(CourseSection)
                        .where(CourseSection.class_nbr.in_(unique_class_nbrs))
                        .values(tot_enrl=CourseSection.tot_enrl + 1)
                    )

            await session.commit()
            return timetable.id

        except Exception as e:
            await session.rollback()
            print(f"Error committing timetable: {e}")
            return None


# ==================== Student-to-Plan Matching ====================


def match_student_to_plan(
    student: StudentInfo,
    available_plans: list[str],
) -> str | None:
    """
    Match a student to an appropriate plan based on branch/program.

    Priority order:
    1. PCB variant (for PCB students)
    2. Program-specific plan (ALL_CS, ALL_RM, etc.) or COMBINED plan containing it
    3. Branch-based plan or COMBINED plan containing it

    Plans can be:
    - Simple branch: "A1", "A5"
    - Combined branches: "COMBINED:A3,A4,A5,A7,A8,AA,AD,AJ+ALL_MM"
    - Program-specific: "ALL_RM", "ALL_CS"
    - PCB variants: "A5_PCB"
    """
    branch = student.branch
    program = student.program
    is_pcb = student.is_pcb

    # 1. PCB variant (highest priority for PCB students)
    if is_pcb:
        pcb_plan = f"{branch}_PCB"
        if pcb_plan in available_plans:
            return pcb_plan

    # 2. Program-specific plans (ALL_CS, ALL_RM, etc.)
    # Check both standalone and COMBINED plans containing the program
    if program:
        program_plan = f"ALL_{program}"

        # Direct match
        if program_plan in available_plans:
            return program_plan

        # Check COMBINED plans that include this program
        for plan in available_plans:
            if plan.startswith("COMBINED:"):
                parts = plan[9:].split("+")
                for part in parts:
                    if program_plan == part.strip():
                        return plan

    # 3. Branch-based plans
    # Direct branch match
    if branch in available_plans:
        return branch

    # Check multi-branch plans (e.g., "A1,A2,AB,B1,...")
    for plan in available_plans:
        if not plan.startswith("COMBINED:") and "," in plan:
            branches_in_plan = [b.strip() for b in plan.split(",")]
            if branch in branches_in_plan:
                return plan

    # Check COMBINED plans that include this branch
    for plan in available_plans:
        if plan.startswith("COMBINED:"):
            parts = plan[9:].split("+")
            for part in parts:
                branches_in_part = [b.strip() for b in part.split(",")]
                if branch in branches_in_part:
                    return plan

    return None


# ==================== Interactive Session Selection ====================


async def interactive_session_select() -> dict | None:
    """Interactive session selection"""
    sessions = await list_sessions()

    if not sessions:
        print("No sessions found!")
        return None

    print("\n" + "=" * 60)
    print("AVAILABLE SESSIONS")
    print("=" * 60)

    for i, s in enumerate(sessions, 1):
        print(f"  [{i}] {s['name']} ({s['term_code']}) - {s['career']}")
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
                print(f"Invalid choice. Enter 1-{len(sessions)}")
        except ValueError:
            print("Please enter a valid number")


# ==================== Main Commit Logic ====================


async def run_commit(
    session_info: dict,
    timetables_dir: str,
    excel_path: str,
    dry_run: bool = False,
):
    """Run the commit process"""
    schema_name = session_info["schema_name"]

    print("\n" + "=" * 60)
    print("COMMIT BULK TIMETABLES")
    print("=" * 60)
    print(f"Session: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print(f"Timetables: {timetables_dir}")
    print(f"Students: {excel_path}")
    print(f"Dry Run: {dry_run}")

    # Parse timetables
    print("\n" + "-" * 60)
    print("PARSING TIMETABLES")
    print("-" * 60)

    timetables = parse_timetables_csv(timetables_dir)

    if not timetables:
        print("No timetables found!")
        return

    total_tts = sum(len(tts) for tts in timetables.values())
    print(f"Found {total_tts} timetables across {len(timetables)} plans")

    for plan, tts in sorted(timetables.items()):
        non_variant = [t for t in tts if not t.is_variant]
        variants = [t for t in tts if t.is_variant]
        total_capacity = sum(t.batch_size for t in non_variant)
        print(
            f"  {plan}: {len(non_variant)} timetables ({total_capacity} students), {len(variants)} variants"
        )

    # Parse students
    print("\n" + "-" * 60)
    print("PARSING STUDENTS")
    print("-" * 60)

    students = parse_student_excel(excel_path)

    if not students:
        print("No students found!")
        return

    print(f"Found {len(students)} students")

    # Group students by branch
    by_branch: dict[str, list[StudentInfo]] = defaultdict(list)
    for s in students:
        by_branch[s.branch].append(s)

    print("\nBy branch:")
    for branch in sorted(by_branch.keys()):
        print(f"  {branch}: {len(by_branch[branch])}")

    # Match students to plans
    print("\n" + "-" * 60)
    print("MATCHING STUDENTS TO PLANS")
    print("-" * 60)

    available_plans = list(timetables.keys())

    matched: dict[str, list[StudentInfo]] = defaultdict(list)
    unmatched: list[StudentInfo] = []

    for student in students:
        plan = match_student_to_plan(student, available_plans)
        if plan:
            matched[plan].append(student)
        else:
            unmatched.append(student)

    print(f"Matched: {len(students) - len(unmatched)}")
    print(f"Unmatched: {len(unmatched)}")

    if unmatched:
        print("\nUnmatched students (first 10):")
        for s in unmatched[:10]:
            print(f"  {s.campus_id} - {s.branch}/{s.program}")

    print("\nMatched by plan:")
    for plan in sorted(matched.keys()):
        plan_tts = timetables.get(plan, [])
        non_variant = [t for t in plan_tts if not t.is_variant]
        capacity = sum(t.batch_size for t in non_variant)
        students_count = len(matched[plan])
        status = "OK" if students_count <= capacity else "OVERFLOW"
        print(f"  {plan}: {students_count} students / {capacity} capacity [{status}]")

    if dry_run:
        print("\n" + "-" * 60)
        print("DRY RUN - No changes made")
        print("-" * 60)
        return

    # Confirm before committing
    print("\n" + "-" * 60)
    confirm = input("Proceed with commit? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    # Get system user ID
    system_user_id = await get_system_user_id()

    # Commit timetables
    print("\n" + "-" * 60)
    print("COMMITTING TIMETABLES")
    print("-" * 60)

    committed = 0
    skipped = 0
    errors = 0

    for plan, plan_students in matched.items():
        plan_tts = timetables.get(plan, [])
        non_variant_tts = [t for t in plan_tts if not t.is_variant]

        if not non_variant_tts:
            print(f"  {plan}: No non-variant timetables, skipping")
            skipped += len(plan_students)
            continue

        # Get section IDs for all class numbers in this plan
        all_class_nbrs = set()
        for tt in non_variant_tts:
            all_class_nbrs.update(tt.class_nbrs)

        section_ids_map = await get_section_ids_for_class_nbrs(
            schema_name, list(all_class_nbrs)
        )

        # Assign students to timetables round-robin style
        student_idx = 0
        tt_assignments: dict[int, list[StudentInfo]] = defaultdict(list)
        overflow_students: list[StudentInfo] = []

        for tt in non_variant_tts:
            for _ in range(tt.batch_size):
                if student_idx >= len(plan_students):
                    break
                tt_assignments[tt.timetable_id].append(plan_students[student_idx])
                student_idx += 1

        # Remaining students beyond batch_size are overflow
        remaining = plan_students[student_idx:]
        overflow_students.extend(remaining)

        # Report overflow students
        if overflow_students:
            print(f"  {plan}: WARNING - {len(overflow_students)} students have no timetable slot!")
            for s in overflow_students[:5]:
                print(f"    - {s.campus_id} ({s.branch})")
            if len(overflow_students) > 5:
                print(f"    ... and {len(overflow_students) - 5} more")
            skipped += len(overflow_students)

        # Commit each student
        plan_committed = 0
        plan_errors = 0

        for tt in non_variant_tts:
            tt_students = tt_assignments.get(tt.timetable_id, [])

            # Get section IDs for this timetable
            tt_section_ids = []
            for class_nbr in tt.class_nbrs:
                tt_section_ids.extend(section_ids_map.get(class_nbr, []))

            for student in tt_students:
                # Get or create student
                student_id = await get_or_create_student(
                    schema_name, student.campus_id, student.name
                )

                if student_id is None:
                    plan_errors += 1
                    continue

                # Commit timetable
                timetable_id = await commit_student_timetable(
                    schema_name,
                    student_id,
                    tt_section_ids,
                    tt.class_nbrs,
                    system_user_id,
                )

                if timetable_id:
                    plan_committed += 1
                else:
                    skipped += 1  # Student might already have timetable

        committed += plan_committed
        errors += plan_errors
        print(f"  {plan}: {plan_committed} committed, {plan_errors} errors")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total committed: {committed}")
    print(f"Skipped (existing/unmatched): {skipped}")
    print(f"Errors: {errors}")


async def main():
    parser = argparse.ArgumentParser(description="Commit bulk timetables to database")
    parser.add_argument(
        "--timetables",
        default="exports/bulk_timetables",
        help="Directory containing generated timetables",
    )
    parser.add_argument(
        "--excel",
        required=True,
        help="Excel file with student list",
    )
    parser.add_argument(
        "--session-id",
        help="Session ID (interactive selection if not provided)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without committing",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML config file for capacity overrides",
    )
    parser.add_argument(
        "--apply-capacity-overrides",
        action="store_true",
        help="Apply capacity overrides from config file to database sections",
    )

    args = parser.parse_args()

    # Load config if provided
    config = None
    if args.config:
        if not args.config.exists():
            print(f"Config file not found: {args.config}")
            return
        config = load_config(args.config)
        print(f"Loaded config from {args.config}")

    # Select session
    if args.session_id:
        from scripts.bulk_timetable_db import get_session_by_id

        session_info = await get_session_by_id(args.session_id)
        if not session_info:
            print(f"Session {args.session_id} not found!")
            return
    else:
        session_info = await interactive_session_select()
        if not session_info:
            return

    # Apply capacity overrides if requested
    if args.apply_capacity_overrides:
        if config is None:
            print("Error: --apply-capacity-overrides requires --config")
            return

        print("\n" + "-" * 60)
        print("APPLYING CAPACITY OVERRIDES")
        print("-" * 60)

        if args.dry_run:
            print("DRY RUN - showing what would be updated:")
            # Just show what would be updated without committing
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(f'SET search_path TO "{session_info["schema_name"]}", public')
                )
                result = await session.execute(
                    select(CourseSection, Course)
                    .join(Course, CourseSection.course_id == Course.id)
                )
                rows = result.all()

                for section, course in rows:
                    course_code = f"{course.subject} {course.catalog}"
                    component = section.component.upper() if section.component else ""
                    override = config.get_section_capacity_override(course_code, component)
                    if override is not None and section.cap_enrl != override:
                        print(
                            f"  Would update {course_code} {section.section} ({component}): "
                            f"{section.cap_enrl} -> {override}"
                        )
        else:
            updates = await apply_capacity_overrides(
                session_info["schema_name"], config
            )
            total_updated = sum(updates.values())
            print(f"\nUpdated {total_updated} sections across {len(updates)} courses")

    # Run commit
    await run_commit(
        session_info,
        args.timetables,
        args.excel,
        args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(main())
