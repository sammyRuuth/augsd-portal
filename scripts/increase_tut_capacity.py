#!/usr/bin/env python3
"""
TUT Section Capacity Increase Calculator & Updater

Calculates the minimum uniform seat increase needed per section
to meet student demand, and optionally applies the changes.

Usage:
    # Calculate only (dry run)
    uv run python scripts/increase_tut_capacity.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025

    # Apply changes to database
    uv run python scripts/increase_tut_capacity.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025 --apply
"""

import argparse
import asyncio
import math
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text, update

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal
from app.models.course_section import CourseSection
from scripts.bulk_timetable_db import (
    get_all_packages_for_year,
    get_course_id_map,
    get_package_key_for_student,
    get_sections_for_courses,
    list_sessions,
    parse_student_excel,
)


@dataclass
class TUTCapacityPlan:
    """Plan for increasing TUT capacity for a course"""

    course_code: str
    course_id: uuid.UUID
    demand: int
    current_total_capacity: int
    current_total_enrolled: int
    current_available: int
    shortfall: int
    num_sections: int
    sections: list[
        dict
    ]  # List of {class_nbr, current_cap, current_enrolled, section_ids}
    # Calculated values
    seats_to_add_per_section: int = 0
    new_capacity_per_section: list[int] = None  # New capacity for each section
    total_seats_added: int = 0
    new_total_available: int = 0


async def select_session_interactive(sessions: list[dict]) -> dict:
    """Prompt user to select a session interactively."""
    print("\nAvailable sessions:")
    print("-" * 60)
    for i, s in enumerate(sessions, 1):
        print(f"  {i}. {s['name']} (schema: {s['schema_name']})")
    print("-" * 60)

    while True:
        try:
            choice = input(f"\nSelect session (1-{len(sessions)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
            print(f"Please enter a number between 1 and {len(sessions)}")
        except ValueError:
            print("Please enter a valid number")


async def calculate_capacity_increase(
    excel_path: str,
    year: int,
    session_id: str | None = None,
    interactive: bool = True,
) -> tuple[list[TUTCapacityPlan], str]:
    """
    Calculate the minimum uniform seat increase needed for each TUT section.

    Returns: (list of capacity plans, schema_name)
    """
    # Get session
    sessions = await list_sessions()
    if not sessions:
        raise ValueError("No sessions found!")

    if session_id:
        session_info = next((s for s in sessions if s["id"] == session_id), None)
        if not session_info:
            raise ValueError(f"Session not found: {session_id}")
    elif interactive:
        session_info = await select_session_interactive(sessions)
    else:
        session_info = sessions[0]

    schema_name = session_info["schema_name"]

    print(f"Session: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print()

    # Parse students
    students = parse_student_excel(excel_path)
    print(f"Total students: {len(students)}")

    # Get packages
    db_packages = await get_all_packages_for_year(year)
    available_packages = set(db_packages.keys())

    # Group students by package
    students_by_package: dict[str, list] = defaultdict(list)
    for student in students:
        package_key = get_package_key_for_student(student, available_packages)
        if package_key:
            students_by_package[package_key].append(student)

    # Get all course codes
    all_course_codes: set[str] = set()
    for pkg_key in students_by_package.keys():
        pkg_info = db_packages.get(pkg_key)
        if pkg_info:
            all_course_codes.update(pkg_info.course_codes)

    # Get course IDs
    course_id_map = await get_course_id_map(list(all_course_codes))

    # Get sections
    all_sections = await get_sections_for_courses(
        schema_name, list(course_id_map.values())
    )

    # Calculate demand per course
    course_demand: dict[str, int] = defaultdict(int)
    for pkg_key, pkg_students in students_by_package.items():
        pkg_info = db_packages.get(pkg_key)
        if pkg_info:
            for code in pkg_info.course_codes:
                course_demand[code] += len(pkg_students)

    # Analyze TUT sections and find shortfalls
    capacity_plans: list[TUTCapacityPlan] = []

    for course_code, course_id in course_id_map.items():
        sections = all_sections.get(course_id, [])
        if not sections:
            continue

        # Filter to TUT sections only
        tut_sections = [s for s in sections if s.component.upper() == "TUT"]
        if not tut_sections:
            continue

        demand = course_demand.get(course_code, 0)
        if demand == 0:
            continue

        # Group by class_nbr (unique sections)
        by_class_nbr: dict[int, list] = defaultdict(list)
        for s in tut_sections:
            by_class_nbr[s.class_nbr].append(s)

        # Calculate totals
        section_info = []
        total_capacity = 0
        total_enrolled = 0

        for class_nbr, sec_list in by_class_nbr.items():
            first = sec_list[0]
            cap = first.cap_enrl or 0
            enrolled = first.tot_enrl or 0
            total_capacity += cap
            total_enrolled += enrolled

            section_info.append(
                {
                    "class_nbr": class_nbr,
                    "current_cap": cap,
                    "current_enrolled": enrolled,
                    "current_available": max(0, cap - enrolled),
                    "section_ids": [s.id for s in sec_list],
                }
            )

        total_available = max(0, total_capacity - total_enrolled)
        shortfall = demand - total_available

        if shortfall > 0:
            # Sort sections by class_nbr for consistent ordering
            section_info.sort(key=lambda x: x["class_nbr"])

            plan = TUTCapacityPlan(
                course_code=course_code,
                course_id=course_id,
                demand=demand,
                current_total_capacity=total_capacity,
                current_total_enrolled=total_enrolled,
                current_available=total_available,
                shortfall=shortfall,
                num_sections=len(by_class_nbr),
                sections=section_info,
            )

            # Calculate uniform increase needed
            # We need to add `shortfall` seats total, distributed across `num_sections`
            # Each section gets ceil(shortfall / num_sections) to ensure we meet demand
            seats_per_section = math.ceil(shortfall / plan.num_sections)
            plan.seats_to_add_per_section = seats_per_section
            plan.total_seats_added = seats_per_section * plan.num_sections
            plan.new_total_available = total_available + plan.total_seats_added

            # Calculate new capacity for each section
            plan.new_capacity_per_section = [
                sec["current_cap"] + seats_per_section for sec in section_info
            ]

            capacity_plans.append(plan)

    return capacity_plans, schema_name


async def apply_capacity_increase(
    schema_name: str,
    plans: list[TUTCapacityPlan],
) -> None:
    """Apply the capacity increases to the database."""
    async with AsyncSessionLocal() as session:
        await session.execute(text(f'SET search_path TO "{schema_name}", public'))

        for plan in plans:
            for sec_info in plan.sections:
                new_cap = sec_info["current_cap"] + plan.seats_to_add_per_section

                # Update all section records with this class_nbr
                for section_id in sec_info["section_ids"]:
                    await session.execute(
                        update(CourseSection)
                        .where(CourseSection.id == section_id)
                        .values(cap_enrl=new_cap)
                    )

                print(
                    f"  Updated {plan.course_code} Class {sec_info['class_nbr']}: "
                    f"{sec_info['current_cap']} -> {new_cap} (+{plan.seats_to_add_per_section})"
                )

        await session.commit()
        print("\nChanges committed to database.")


def print_capacity_report(plans: list[TUTCapacityPlan]) -> None:
    """Print detailed capacity increase report."""
    print("=" * 100)
    print("                    TUT CAPACITY INCREASE CALCULATION")
    print("=" * 100)
    print()

    if not plans:
        print("No TUT sections need capacity increases!")
        return

    # Summary table
    print("SUMMARY: COURSES REQUIRING TUT CAPACITY INCREASE")
    print("-" * 100)
    print(
        f"{'Course':<15} {'Demand':>8} {'Current':>10} {'Shortfall':>10} "
        f"{'Sections':>10} {'Add/Sec':>10} {'Total Add':>10} {'New Avail':>10}"
    )
    print("-" * 100)

    total_shortfall = 0
    total_seats_added = 0

    for plan in sorted(plans, key=lambda x: -x.shortfall):
        print(
            f"{plan.course_code:<15} {plan.demand:>8} {plan.current_available:>10} "
            f"{plan.shortfall:>10} {plan.num_sections:>10} "
            f"+{plan.seats_to_add_per_section:>9} +{plan.total_seats_added:>9} "
            f"{plan.new_total_available:>10}"
        )
        total_shortfall += plan.shortfall
        total_seats_added += plan.total_seats_added

    print("-" * 100)
    print(
        f"{'TOTAL':<15} {'':<8} {'':<10} {total_shortfall:>10} "
        f"{'':<10} {'':<10} +{total_seats_added:>9}"
    )
    print()

    # Detailed per-course breakdown
    print("=" * 100)
    print("DETAILED SECTION-BY-SECTION CHANGES")
    print("=" * 100)

    for plan in sorted(plans, key=lambda x: -x.shortfall):
        print()
        print(f"{'─' * 80}")
        print(f"{plan.course_code} TUT")
        print(f"{'─' * 80}")
        print(f"  Demand:           {plan.demand} students")
        print(f"  Current enrolled: {plan.current_total_enrolled}")
        print(f"  Current capacity: {plan.current_total_capacity}")
        print(f"  Current available: {plan.current_available}")
        print(f"  Shortfall:        {plan.shortfall} seats")
        print()
        print(
            f"  Solution: Add +{plan.seats_to_add_per_section} seats to each of {plan.num_sections} sections"
        )
        print(f"  Total seats added: +{plan.total_seats_added}")
        print(
            f"  New available:     {plan.new_total_available} (surplus: +{plan.new_total_available - plan.demand})"
        )
        print()
        print("  Section changes:")
        print(
            f"  {'Class':<10} {'Current':>10} {'Enrolled':>10} {'New Cap':>10} {'Change':>10}"
        )
        print(f"  {'-' * 50}")

        for i, sec in enumerate(plan.sections):
            new_cap = plan.new_capacity_per_section[i]
            print(
                f"  {sec['class_nbr']:<10} {sec['current_cap']:>10} "
                f"{sec['current_enrolled']:>10} {new_cap:>10} "
                f"+{plan.seats_to_add_per_section:>9}"
            )

    print()
    print("=" * 100)
    print("FINAL SUMMARY")
    print("=" * 100)
    print()
    print(f"Courses affected:     {len(plans)}")
    print(f"Total shortfall:      {total_shortfall} seats")
    print(f"Total seats to add:   {total_seats_added} seats")
    print()

    # Per-course summary
    print("Per-course seat additions needed:")
    for plan in sorted(plans, key=lambda x: x.course_code):
        print(
            f"  • {plan.course_code}: +{plan.seats_to_add_per_section} per section "
            f"× {plan.num_sections} sections = +{plan.total_seats_added} total"
        )
    print()


async def main():
    parser = argparse.ArgumentParser(
        description="Calculate and optionally apply TUT capacity increases"
    )
    parser.add_argument(
        "--excel",
        type=str,
        required=True,
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
        help="Session ID (if not provided, uses first session)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes to database (default: dry run)",
    )

    args = parser.parse_args()

    if not Path(args.excel).exists():
        print(f"Excel file not found: {args.excel}")
        return

    # Calculate capacity increases
    plans, schema_name = await calculate_capacity_increase(
        excel_path=args.excel,
        year=args.year,
        session_id=args.session,
    )

    # Print report
    print_capacity_report(plans)

    if not plans:
        return

    if args.apply:
        print("=" * 100)
        print("APPLYING CHANGES TO DATABASE")
        print("=" * 100)
        print()

        confirm = input("Are you sure you want to apply these changes? (yes/no): ")
        if confirm.lower() == "yes":
            await apply_capacity_increase(schema_name, plans)
            print("\nCapacity increases applied successfully!")
        else:
            print("\nChanges NOT applied.")
    else:
        print("=" * 100)
        print("DRY RUN - No changes made to database")
        print("=" * 100)
        print()
        print("To apply these changes, run with --apply flag:")
        print(
            f"  uv run python scripts/increase_tut_capacity.py --excel {args.excel} --year {args.year} --apply"
        )
        print()


if __name__ == "__main__":
    asyncio.run(main())
