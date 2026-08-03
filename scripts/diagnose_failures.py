#!/usr/bin/env python3
"""
Diagnose Timetable Generation Failures

Analyzes why specific packages are failing in bulk timetable generation.
Focuses on: A4, A7, A8, AA, AD, AJ packages.

Usage:
    uv run python scripts/diagnose_failures.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025
"""

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from datetime import time
from itertools import combinations, product
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.algorithms.base import LogicalSection, SectionData, TimetableAlgorithm
from scripts.bulk_timetable_db import (
    get_all_packages_for_year,
    get_course_id_map,
    get_sections_for_courses,
    list_sessions,
)

# Packages to diagnose
FAILING_PACKAGES = ["A4", "A7", "A8", "AA", "AD", "AJ"]


def time_to_str(t: time | None) -> str:
    """Convert time to string"""
    if t is None:
        return "TBA"
    return t.strftime("%H:%M")


def build_logical_sections(
    sections: list[SectionData],
) -> dict[int, LogicalSection]:
    """Build logical sections from section data"""
    logical_map: dict[int, LogicalSection] = {}

    for s in sections:
        if s.class_nbr not in logical_map:
            logical_map[s.class_nbr] = LogicalSection(
                course_id=s.course_id,
                class_nbr=s.class_nbr,
                component=s.component,
                section=s.section,
                subject=s.subject,
                catalog=s.catalog,
                title=s.title,
                max_units=s.max_units,
                exam_date=s.exam_date,
                exam_start=s.exam_start,
                exam_end=s.exam_end,
                instructor=s.instructor,
                room=s.room,
            )

        logical = logical_map[s.class_nbr]
        logical.meetings.append(s)

        # Build day mask
        if s.day and s.mtg_start and s.mtg_end:
            start_min = TimetableAlgorithm.time_obj_to_minutes(s.mtg_start)
            end_min = TimetableAlgorithm.time_obj_to_minutes(s.mtg_end)
            mask = TimetableAlgorithm.mask_for_interval(start_min, end_min)
            logical.day_masks[s.day] = logical.day_masks.get(s.day, 0) | mask

        # Calculate seat score
        cap = s.cap_enrl or 0
        tot = s.tot_enrl or 0
        logical.seat_score += max(cap - tot, 0)

    return logical_map


def check_sections_clash(sec_a: LogicalSection, sec_b: LogicalSection) -> bool:
    """Check if two logical sections clash (time or exam)"""
    # Time clash using masks
    for day, mask_a in sec_a.day_masks.items():
        mask_b = sec_b.day_masks.get(day, 0)
        if mask_a & mask_b:
            return True

    # Exam clash - only check between different courses
    if sec_a.course_id != sec_b.course_id:
        if sec_a.exam_date and sec_b.exam_date:
            if sec_a.exam_date == sec_b.exam_date:
                if (
                    sec_a.exam_start
                    and sec_a.exam_end
                    and sec_b.exam_start
                    and sec_b.exam_end
                ):
                    a_start = TimetableAlgorithm.time_obj_to_minutes(sec_a.exam_start)
                    a_end = TimetableAlgorithm.time_obj_to_minutes(sec_a.exam_end)
                    b_start = TimetableAlgorithm.time_obj_to_minutes(sec_b.exam_start)
                    b_end = TimetableAlgorithm.time_obj_to_minutes(sec_b.exam_end)
                    if max(a_start, b_start) < min(a_end, b_end):
                        return True

    return False


def get_valid_combos_for_course(
    course_sections: list[SectionData],
    check_capacity: bool = True,
) -> tuple[list[list[LogicalSection]], dict[str, int]]:
    """
    Get all valid section combinations for a course.
    Returns (valid_combos, sections_per_component)
    """
    # Build logical sections
    logical_map = build_logical_sections(course_sections)

    # Group by component
    by_component: dict[str, list[LogicalSection]] = defaultdict(list)
    for logical in logical_map.values():
        by_component[logical.component].append(logical)

    if not by_component:
        return [], {}

    sections_per_component = {c: len(secs) for c, secs in by_component.items()}

    # Filter LAB sections by capacity if requested
    if check_capacity:
        for comp, sections in by_component.items():
            if comp.upper() == "LAB":
                by_component[comp] = [s for s in sections if s.seat_score > 0]

    # Check if any component is empty
    for comp, sections in by_component.items():
        if not sections:
            return [], sections_per_component

    # Generate all combos and filter for internal clashes
    components = sorted(by_component.keys())
    component_sections = [by_component[c] for c in components]

    valid_combos = []
    for combo_tuple in product(*component_sections):
        combo = list(combo_tuple)
        has_clash = False
        for i in range(len(combo)):
            for j in range(i + 1, len(combo)):
                if check_sections_clash(combo[i], combo[j]):
                    has_clash = True
                    break
            if has_clash:
                break
        if not has_clash:
            valid_combos.append(combo)

    return valid_combos, sections_per_component


def check_course_pair_compatibility(
    course1_sections: list[SectionData],
    course2_sections: list[SectionData],
) -> tuple[int, int, float]:
    """
    Check how many combo pairs between two courses don't conflict.
    Returns (non_conflicting, total, conflict_rate)
    """
    combos1, _ = get_valid_combos_for_course(course1_sections, check_capacity=False)
    combos2, _ = get_valid_combos_for_course(course2_sections, check_capacity=False)

    total = len(combos1) * len(combos2)
    if total == 0:
        return 0, 0, 1.0

    non_conflicting = 0
    for c1 in combos1:
        for c2 in combos2:
            has_conflict = False
            for s1 in c1:
                for s2 in c2:
                    if check_sections_clash(s1, s2):
                        has_conflict = True
                        break
                if has_conflict:
                    break
            if not has_conflict:
                non_conflicting += 1

    conflict_rate = 1.0 - (non_conflicting / total) if total > 0 else 1.0
    return non_conflicting, total, conflict_rate


async def diagnose_package(
    package_key: str,
    course_codes: list[str],
    course_id_map: dict[str, uuid.UUID],
    all_sections: dict[uuid.UUID, list[SectionData]],
):
    """Diagnose a single package for issues"""
    print(f"\n{'=' * 80}")
    print(f"DIAGNOSING PACKAGE: {package_key}")
    print(f"{'=' * 80}")
    print(f"Courses in package: {len(course_codes)}")

    # Map codes to IDs
    course_ids = {
        code: course_id_map[code] for code in course_codes if code in course_id_map
    }

    missing_courses = [c for c in course_codes if c not in course_id_map]
    if missing_courses:
        print(f"\n⚠ MISSING COURSES (not in course table): {missing_courses}")

    # Analyze each course
    print(f"\n{'-' * 80}")
    print("COURSE-BY-COURSE ANALYSIS")
    print(f"{'-' * 80}")

    problem_courses = []
    course_combos = {}

    for code in sorted(course_codes):
        if code not in course_ids:
            continue

        cid = course_ids[code]
        sections = all_sections.get(cid, [])

        if not sections:
            print(f"\n✗ {code}: NO SECTIONS FOUND")
            problem_courses.append((code, "no_sections", "No sections available"))
            continue

        # Get valid combos
        valid_combos, sections_per_comp = get_valid_combos_for_course(
            sections, check_capacity=True
        )
        valid_combos_no_cap, _ = get_valid_combos_for_course(
            sections, check_capacity=False
        )

        course_combos[code] = valid_combos

        # Analyze by component
        by_component: dict[str, list[SectionData]] = defaultdict(list)
        for s in sections:
            by_component[s.component].append(s)

        # Check capacity issues
        capacity_issues = []
        for comp, comp_sections in by_component.items():
            # Group by class_nbr for capacity
            by_class = defaultdict(list)
            for s in comp_sections:
                by_class[s.class_nbr].append(s)

            total_cap = 0
            total_enrl = 0
            full_sections = 0

            for class_nbr, class_sections in by_class.items():
                cap = class_sections[0].cap_enrl or 0
                enrl = class_sections[0].tot_enrl or 0
                total_cap += cap
                total_enrl += enrl
                if cap <= enrl:
                    full_sections += 1

            available = total_cap - total_enrl
            if available <= 0 or (
                comp.upper() == "LAB" and full_sections == len(by_class)
            ):
                capacity_issues.append(
                    f"{comp}: {total_enrl}/{total_cap} (all full)"
                    if full_sections == len(by_class)
                    else f"{comp}: {total_enrl}/{total_cap}"
                )

        # Status output
        if not valid_combos:
            if valid_combos_no_cap:
                print(f"\n✗ {code}: 0 valid combos (CAPACITY ISSUE)")
                print(
                    f"   Would have {len(valid_combos_no_cap)} combos if capacity ignored"
                )
                for issue in capacity_issues:
                    print(f"   → {issue}")
                problem_courses.append((code, "capacity", "All LAB sections full"))
            else:
                print(f"\n✗ {code}: 0 valid combos (TIME CONFLICT)")
                print(f"   Sections by component: {sections_per_comp}")
                print("   Internal component conflicts prevent any valid combo")
                problem_courses.append(
                    (code, "internal_conflict", "Components conflict internally")
                )
        elif len(valid_combos) < 3:
            print(f"\n⚠ {code}: Only {len(valid_combos)} valid combo(s)")
            print(f"   Sections: {sections_per_comp}")
            if capacity_issues:
                for issue in capacity_issues:
                    print(f"   → {issue}")
        else:
            print(f"\n✓ {code}: {len(valid_combos)} valid combos")

    # Check course pair conflicts
    print(f"\n{'-' * 80}")
    print("COURSE PAIR CONFLICT ANALYSIS")
    print(f"{'-' * 80}")

    course_codes_with_sections = [
        c for c in course_codes if c in course_ids and course_ids[c] in all_sections
    ]

    high_conflict_pairs = []
    for code1, code2 in combinations(course_codes_with_sections, 2):
        cid1 = course_ids[code1]
        cid2 = course_ids[code2]

        sections1 = all_sections.get(cid1, [])
        sections2 = all_sections.get(cid2, [])

        if not sections1 or not sections2:
            continue

        non_conf, total, rate = check_course_pair_compatibility(sections1, sections2)

        if rate > 0.8:  # More than 80% of combos conflict
            high_conflict_pairs.append((code1, code2, non_conf, total, rate))

    if high_conflict_pairs:
        print("\n⚠ HIGH-CONFLICT PAIRS (>80% conflict rate):")
        high_conflict_pairs.sort(key=lambda x: x[4], reverse=True)
        for code1, code2, non_conf, total, rate in high_conflict_pairs[:10]:
            print(
                f"   {code1} ↔ {code2}: {rate * 100:.0f}% conflict ({non_conf}/{total} valid)"
            )
    else:
        print("\n✓ No high-conflict course pairs found")

    # Summary
    print(f"\n{'-' * 80}")
    print("DIAGNOSIS SUMMARY")
    print(f"{'-' * 80}")

    if not problem_courses:
        print("✓ All courses have valid combos")
        print("  Failures may be due to cascading time conflicts across 3+ courses")
    else:
        print(f"✗ {len(problem_courses)} problematic course(s):")
        for code, issue_type, detail in problem_courses:
            print(f"   • {code}: {detail}")

        # Recommendations
        print(f"\n{'-' * 80}")
        print("RECOMMENDATIONS")
        print(f"{'-' * 80}")

        capacity_issues = [p for p in problem_courses if p[1] == "capacity"]
        if capacity_issues:
            print("\n1. INCREASE LAB/TUT CAPACITY:")
            for code, _, _ in capacity_issues:
                cid = course_ids.get(code)
                if cid:
                    sections = all_sections.get(cid, [])
                    lab_sections = [s for s in sections if s.component.upper() == "LAB"]
                    if lab_sections:
                        by_class = defaultdict(list)
                        for s in lab_sections:
                            by_class[s.class_nbr].append(s)
                        for class_nbr, class_sections in by_class.items():
                            cap = class_sections[0].cap_enrl or 0
                            enrl = class_sections[0].tot_enrl or 0
                            if cap <= enrl:
                                print(
                                    f"   → {code} LAB class {class_nbr}: {enrl}/{cap} - add {max(10, enrl - cap + 5)} seats"
                                )


async def main():
    parser = argparse.ArgumentParser(
        description="Diagnose Timetable Generation Failures"
    )
    parser.add_argument(
        "--excel",
        type=str,
        help="Path to Excel file (for context, not required)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2025,
        help="Year for package filtering (default: 2025)",
    )
    parser.add_argument(
        "--packages",
        type=str,
        nargs="*",
        default=FAILING_PACKAGES,
        help=f"Packages to diagnose (default: {FAILING_PACKAGES})",
    )
    parser.add_argument(
        "--all-packages",
        action="store_true",
        help="Diagnose ALL packages, not just failing ones",
    )

    args = parser.parse_args()

    print("=" * 80)
    print("TIMETABLE GENERATION FAILURE DIAGNOSIS")
    print("=" * 80)

    # Get session
    sessions = await list_sessions()
    if not sessions:
        print("No sessions found!")
        return

    session_info = sessions[0]
    schema_name = session_info["schema_name"]

    print(f"\nSession: {session_info['name']}")
    print(f"Schema: {schema_name}")
    print(f"Year: {args.year}")

    # Get packages
    packages = await get_all_packages_for_year(args.year)

    if args.all_packages:
        packages_to_check = list(packages.keys())
    else:
        packages_to_check = args.packages

    print(f"\nPackages to diagnose: {packages_to_check}")

    # Get all course codes from selected packages
    all_course_codes: set[str] = set()
    for pkg_key in packages_to_check:
        if pkg_key in packages:
            all_course_codes.update(packages[pkg_key].course_codes)

    print(f"Total unique courses: {len(all_course_codes)}")

    # Get course IDs
    course_id_map = await get_course_id_map(list(all_course_codes))
    print(f"Mapped to DB: {len(course_id_map)}")

    # Get sections
    all_sections = await get_sections_for_courses(
        schema_name, list(course_id_map.values())
    )
    print(f"Courses with sections: {len(all_sections)}")

    # Diagnose each package
    for pkg_key in packages_to_check:
        if pkg_key not in packages:
            print(f"\n⚠ Package {pkg_key} not found in database")
            continue

        await diagnose_package(
            pkg_key,
            packages[pkg_key].course_codes,
            course_id_map,
            all_sections,
        )

    print("\n" + "=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
