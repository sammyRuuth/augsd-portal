#!/usr/bin/env python3
"""
Comprehensive Timetable Analysis Tool

Provides detailed analysis of:
- Capacity vs demand by course and component
- Section-level utilization and bottlenecks
- LAB/TUT section analysis with seat availability
- Package/student flexibility analysis
- Time conflict analysis between courses
- Actionable recommendations

Usage:
    uv run python scripts/timetable_analysis.py --excel data/2025-2/6-1-26/25-dtc.xlsx --year 2025
"""

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import time
from itertools import combinations, product
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.algorithms.base import LogicalSection, SectionData, TimetableAlgorithm
from scripts.bulk_timetable_db import (
    StudentInfo,
    get_all_packages_for_year,
    get_course_id_map,
    get_package_key_for_student,
    get_sections_for_courses,
    list_sessions,
    parse_student_excel,
)

# ==================== Data Classes ====================


@dataclass
class SectionAnalysis:
    """Analysis data for a single section (by class_nbr)"""

    class_nbr: int
    section_code: str
    component: str
    course_code: str
    course_id: uuid.UUID
    capacity: int
    enrolled: int
    available: int
    utilization_pct: float
    is_full: bool
    is_over_capacity: bool
    # Time slots
    days: list[str] = field(default_factory=list)
    time_slots: list[str] = field(default_factory=list)
    instructor: str = ""
    room: str = ""


@dataclass
class CourseComponentAnalysis:
    """Analysis of a course component (e.g., BIO F101 TUT)"""

    course_code: str
    course_id: uuid.UUID
    component: str
    total_sections: int
    total_capacity: int
    total_enrolled: int
    total_available: int
    demand: int  # How many students need this
    shortfall: int  # Negative if insufficient
    utilization_pct: float
    sections: list[SectionAnalysis] = field(default_factory=list)
    has_insufficient_capacity: bool = False


@dataclass
class PackageAnalysis:
    """Analysis of a package/student group"""

    package_key: str
    student_count: int
    course_codes: list[str]
    # Flexibility metrics
    avg_valid_combos: float = 0.0
    min_valid_combos: int = 0
    max_valid_combos: int = 0
    most_constrained_course: str = ""
    most_constrained_count: int = 0
    # Problem courses
    problem_courses: list[str] = field(default_factory=list)


@dataclass
class TimeSlotAnalysis:
    """Analysis of time slot conflicts"""

    course_pair: tuple[str, str]
    total_combos: int
    non_conflicting_combos: int
    conflict_rate: float  # 0.0 to 1.0


# ==================== Analysis Functions ====================


def time_to_str(t: time | None) -> str:
    """Convert time to string"""
    if t is None:
        return ""
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


def analyze_section(
    class_nbr: int,
    sections: list[SectionData],
    course_code: str,
) -> SectionAnalysis:
    """Analyze a single section (unique by class_nbr)"""
    first = sections[0]

    # Get unique capacity (all meetings share same cap/tot)
    capacity = first.cap_enrl or 0
    enrolled = first.tot_enrl or 0
    available = max(0, capacity - enrolled)
    utilization = (enrolled / capacity * 100) if capacity > 0 else 0.0

    # Collect time info
    days = []
    time_slots = []
    for s in sections:
        if s.day:
            days.append(s.day)
            if s.mtg_start and s.mtg_end:
                time_slots.append(
                    f"{s.day} {time_to_str(s.mtg_start)}-{time_to_str(s.mtg_end)}"
                )

    return SectionAnalysis(
        class_nbr=class_nbr,
        section_code=first.section,
        component=first.component,
        course_code=course_code,
        course_id=first.course_id,
        capacity=capacity,
        enrolled=enrolled,
        available=available,
        utilization_pct=utilization,
        is_full=available == 0,
        is_over_capacity=enrolled > capacity,
        days=list(set(days)),
        time_slots=time_slots,
        instructor=first.instructor or "",
        room=first.room or "",
    )


def analyze_course_component(
    course_code: str,
    course_id: uuid.UUID,
    component: str,
    sections: list[SectionData],
    demand: int,
) -> CourseComponentAnalysis:
    """Analyze a course component (all sections of a type)"""
    # Group by class_nbr
    by_class_nbr: dict[int, list[SectionData]] = defaultdict(list)
    for s in sections:
        by_class_nbr[s.class_nbr].append(s)

    # Analyze each section
    section_analyses = []
    total_capacity = 0
    total_enrolled = 0

    for class_nbr, sec_list in by_class_nbr.items():
        analysis = analyze_section(class_nbr, sec_list, course_code)
        section_analyses.append(analysis)
        total_capacity += analysis.capacity
        total_enrolled += analysis.enrolled

    total_available = max(0, total_capacity - total_enrolled)
    shortfall = total_available - demand
    utilization = (total_enrolled / total_capacity * 100) if total_capacity > 0 else 0.0

    # Sort sections by utilization (most full first)
    section_analyses.sort(key=lambda x: x.utilization_pct, reverse=True)

    return CourseComponentAnalysis(
        course_code=course_code,
        course_id=course_id,
        component=component,
        total_sections=len(by_class_nbr),
        total_capacity=total_capacity,
        total_enrolled=total_enrolled,
        total_available=total_available,
        demand=demand,
        shortfall=shortfall,
        utilization_pct=utilization,
        sections=section_analyses,
        has_insufficient_capacity=shortfall < 0,
    )


def count_valid_combos(
    course_sections: list[SectionData],
) -> tuple[int, dict[str, int]]:
    """
    Count valid combos for a course (sections that don't clash internally).
    Returns (total_combos, combos_by_component)
    """
    # Group by component and class_nbr
    logical_by_component: dict[str, list[LogicalSection]] = defaultdict(list)
    logical_map = build_logical_sections(course_sections)

    for logical in logical_map.values():
        logical_by_component[logical.component].append(logical)

    if not logical_by_component:
        return 0, {}

    # Get component lists in order
    components = sorted(logical_by_component.keys())
    component_sections = [logical_by_component[c] for c in components]

    # Count valid combos
    valid_count = 0
    combos_per_component = {c: len(logical_by_component[c]) for c in components}

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
            valid_count += 1

    return valid_count, combos_per_component


def count_non_conflicting_combos_between_courses(
    course1_sections: list[SectionData],
    course2_sections: list[SectionData],
) -> tuple[int, int]:
    """
    Count how many combo pairs between two courses don't conflict.
    Returns (non_conflicting_count, total_count)
    """
    # Build logical sections for both
    logical1 = build_logical_sections(course1_sections)
    logical2 = build_logical_sections(course2_sections)

    # Group by component
    def group_by_component(
        logical_map: dict[int, LogicalSection],
    ) -> dict[str, list[LogicalSection]]:
        by_comp: dict[str, list[LogicalSection]] = defaultdict(list)
        for ls in logical_map.values():
            by_comp[ls.component].append(ls)
        return by_comp

    by_comp1 = group_by_component(logical1)
    by_comp2 = group_by_component(logical2)

    if not by_comp1 or not by_comp2:
        return 0, 0

    # Generate all valid combos for each course
    def get_valid_combos(
        by_comp: dict[str, list[LogicalSection]],
    ) -> list[list[LogicalSection]]:
        components = sorted(by_comp.keys())
        component_sections = [by_comp[c] for c in components]

        valid = []
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
                valid.append(combo)
        return valid

    combos1 = get_valid_combos(by_comp1)
    combos2 = get_valid_combos(by_comp2)

    total = len(combos1) * len(combos2)
    if total == 0:
        return 0, 0

    # Count non-conflicting pairs
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

    return non_conflicting, total


# ==================== Main Analysis ====================


async def run_analysis(
    excel_path: str,
    year: int,
    session_id: str | None = None,
    output_file: str | None = None,
):
    """Run comprehensive timetable analysis"""

    output_lines: list[str] = []

    def log(msg: str = ""):
        print(msg)
        output_lines.append(msg)

    # Get session
    sessions = await list_sessions()
    if not sessions:
        log("No sessions found!")
        return

    if session_id:
        session_info = next((s for s in sessions if s["id"] == session_id), None)
        if not session_info:
            log(f"Session not found: {session_id}")
            return
    else:
        session_info = sessions[0]

    schema_name = session_info["schema_name"]

    log("=" * 100)
    log("                    COMPREHENSIVE TIMETABLE ANALYSIS REPORT")
    log("=" * 100)
    log()
    log(f"Session: {session_info['name']}")
    log(f"Schema: {schema_name}")
    log(f"Excel: {excel_path}")
    log(f"Year: {year}")
    log()

    # Parse students
    log("-" * 100)
    log("PARSING STUDENT DATA")
    log("-" * 100)

    students = parse_student_excel(excel_path)
    log(f"Total students: {len(students)}")

    # Get packages
    db_packages = await get_all_packages_for_year(year)
    available_packages = set(db_packages.keys())

    # Group students by package
    students_by_package: dict[str, list[StudentInfo]] = defaultdict(list)
    unmatched_students: list[StudentInfo] = []

    for student in students:
        package_key = get_package_key_for_student(student, available_packages)
        if package_key:
            students_by_package[package_key].append(student)
        else:
            unmatched_students.append(student)

    log(f"Matched to packages: {len(students) - len(unmatched_students)}")
    log(f"Unmatched students: {len(unmatched_students)}")
    log()

    log("Students by package:")
    for pkg_key in sorted(students_by_package.keys()):
        log(f"  {pkg_key}: {len(students_by_package[pkg_key])} students")
    log()

    # Get all course codes
    all_course_codes: set[str] = set()
    for pkg_key in students_by_package.keys():
        pkg_info = db_packages.get(pkg_key)
        if pkg_info:
            all_course_codes.update(pkg_info.course_codes)

    # Get course IDs
    course_id_map = await get_course_id_map(list(all_course_codes))
    {cid: code for code, cid in course_id_map.items()}

    log(f"Total unique courses: {len(course_id_map)}")
    log()

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

    # ==================== SECTION 1: CAPACITY ANALYSIS ====================

    log("=" * 100)
    log("SECTION 1: CAPACITY ANALYSIS BY COURSE AND COMPONENT")
    log("=" * 100)
    log()

    # Analyze each course
    course_analyses: dict[str, dict[str, CourseComponentAnalysis]] = {}
    problem_components: list[CourseComponentAnalysis] = []

    for course_code, course_id in course_id_map.items():
        sections = all_sections.get(course_id, [])
        if not sections:
            continue

        demand = course_demand.get(course_code, 0)

        # Group by component
        by_component: dict[str, list[SectionData]] = defaultdict(list)
        for s in sections:
            by_component[s.component].append(s)

        course_analyses[course_code] = {}

        for component in sorted(by_component.keys()):
            analysis = analyze_course_component(
                course_code, course_id, component, by_component[component], demand
            )
            course_analyses[course_code][component] = analysis

            if analysis.has_insufficient_capacity:
                problem_components.append(analysis)

    # Print summary table
    log("CAPACITY SUMMARY TABLE")
    log("-" * 100)
    log(
        f"{'Course':<15} {'Comp':<5} {'Sections':>8} {'Capacity':>10} {'Enrolled':>10} {'Available':>10} {'Demand':>8} {'Status':>12}"
    )
    log("-" * 100)

    for course_code in sorted(course_analyses.keys()):
        for component in sorted(course_analyses[course_code].keys()):
            analysis = course_analyses[course_code][component]

            if analysis.shortfall < 0:
                status = f"SHORT {abs(analysis.shortfall)}"
            elif analysis.total_available == 0:
                status = "FULL"
            else:
                status = "OK"

            log(
                f"{course_code:<15} {component:<5} {analysis.total_sections:>8} {analysis.total_capacity:>10} {analysis.total_enrolled:>10} {analysis.total_available:>10} {analysis.demand:>8} {status:>12}"
            )

    log()

    # ==================== SECTION 2: PROBLEM COURSES ====================

    log("=" * 100)
    log("SECTION 2: PROBLEM COURSES (INSUFFICIENT CAPACITY)")
    log("=" * 100)
    log()

    if not problem_components:
        log("✓ No courses with insufficient capacity detected!")
    else:
        log(
            f"Found {len(problem_components)} course-components with insufficient capacity:"
        )
        log()

        # Sort by severity (largest shortfall first)
        problem_components.sort(key=lambda x: x.shortfall)

        for analysis in problem_components:
            log(f"{'─' * 80}")
            log(f"PROBLEM: {analysis.course_code} {analysis.component}")
            log(f"{'─' * 80}")
            log(f"  Demand:    {analysis.demand} students")
            log(f"  Available: {analysis.total_available} seats")
            log(f"  Shortfall: {abs(analysis.shortfall)} seats missing")
            log(f"  Sections:  {analysis.total_sections}")
            log()
            log("  Individual sections:")

            for sec in analysis.sections:
                status = (
                    "OVER-CAP"
                    if sec.is_over_capacity
                    else ("FULL" if sec.is_full else f"{sec.available} avail")
                )
                time_str = ", ".join(sec.time_slots) if sec.time_slots else "TBA"
                log(
                    f"    • Class {sec.class_nbr} ({sec.section_code}): {sec.enrolled}/{sec.capacity} [{status}]"
                )
                log(f"      Time: {time_str}")
                if sec.instructor:
                    log(f"      Instructor: {sec.instructor}")
            log()

    log()

    # ==================== SECTION 3: LAB SECTION ANALYSIS ====================

    log("=" * 100)
    log("SECTION 3: LAB SECTION DETAILED ANALYSIS")
    log("=" * 100)
    log()

    lab_components = [
        ca
        for course in course_analyses.values()
        for ca in course.values()
        if ca.component.upper() == "LAB"
    ]

    if not lab_components:
        log("No LAB sections found in the course data.")
    else:
        log(f"Found {len(lab_components)} courses with LAB components")
        log()

        # Overall LAB stats
        total_lab_capacity = sum(c.total_capacity for c in lab_components)
        total_lab_enrolled = sum(c.total_enrolled for c in lab_components)
        total_lab_available = sum(c.total_available for c in lab_components)
        total_lab_demand = sum(c.demand for c in lab_components)

        log("OVERALL LAB STATISTICS")
        log("-" * 50)
        log(f"  Total LAB sections: {sum(c.total_sections for c in lab_components)}")
        log(f"  Total capacity:     {total_lab_capacity}")
        log(f"  Total enrolled:     {total_lab_enrolled}")
        log(f"  Total available:    {total_lab_available}")
        log(f"  Total demand:       {total_lab_demand}")
        log()

        # Sections at/over capacity
        full_labs = [s for c in lab_components for s in c.sections if s.is_full]
        over_cap_labs = [
            s for c in lab_components for s in c.sections if s.is_over_capacity
        ]

        log(f"LAB sections at full capacity: {len(full_labs)}")
        log(f"LAB sections OVER capacity: {len(over_cap_labs)}")
        log()

        if over_cap_labs:
            log("OVER-CAPACITY LAB SECTIONS (enrolled > capacity):")
            log("-" * 80)
            for sec in over_cap_labs:
                log(
                    f"  • {sec.course_code} Class {sec.class_nbr}: {sec.enrolled}/{sec.capacity} (over by {sec.enrolled - sec.capacity})"
                )
            log()

        # Detailed per-course breakdown
        log("LAB SECTIONS BY COURSE")
        log("-" * 100)

        for ca in sorted(lab_components, key=lambda x: x.shortfall):
            status = "INSUFFICIENT" if ca.has_insufficient_capacity else "OK"
            log(f"\n{ca.course_code} LAB [{status}]")
            log(
                f"  Sections: {ca.total_sections}, Capacity: {ca.total_capacity}, Available: {ca.total_available}, Demand: {ca.demand}"
            )

            for sec in ca.sections:
                avail_str = f"{sec.available} avail" if sec.available > 0 else "FULL"
                if sec.is_over_capacity:
                    avail_str = f"OVER +{sec.enrolled - sec.capacity}"
                time_str = ", ".join(sec.time_slots) if sec.time_slots else "TBA"
                log(
                    f"    Class {sec.class_nbr:>6}: {sec.enrolled:>3}/{sec.capacity:<3} [{avail_str:>10}] - {time_str}"
                )

    log()

    # ==================== SECTION 4: TUT SECTION ANALYSIS ====================

    log("=" * 100)
    log("SECTION 4: TUT (TUTORIAL) SECTION DETAILED ANALYSIS")
    log("=" * 100)
    log()

    tut_components = [
        ca
        for course in course_analyses.values()
        for ca in course.values()
        if ca.component.upper() == "TUT"
    ]

    if not tut_components:
        log("No TUT sections found in the course data.")
    else:
        log(f"Found {len(tut_components)} courses with TUT components")
        log()

        # Overall TUT stats
        total_tut_capacity = sum(c.total_capacity for c in tut_components)
        total_tut_enrolled = sum(c.total_enrolled for c in tut_components)
        total_tut_available = sum(c.total_available for c in tut_components)
        total_tut_demand = sum(c.demand for c in tut_components)

        log("OVERALL TUT STATISTICS")
        log("-" * 50)
        log(f"  Total TUT sections: {sum(c.total_sections for c in tut_components)}")
        log(f"  Total capacity:     {total_tut_capacity}")
        log(f"  Total enrolled:     {total_tut_enrolled}")
        log(f"  Total available:    {total_tut_available}")
        log(f"  Total demand:       {total_tut_demand}")
        log()

        # Problem TUT sections
        problem_tut = [c for c in tut_components if c.has_insufficient_capacity]

        if problem_tut:
            log(f"TUT sections with INSUFFICIENT CAPACITY: {len(problem_tut)}")
            log("-" * 80)
            for ca in sorted(problem_tut, key=lambda x: x.shortfall):
                log(
                    f"  • {ca.course_code}: Need {ca.demand}, Have {ca.total_available} (short {abs(ca.shortfall)})"
                )
            log()

        # Detailed breakdown
        log("TUT SECTIONS BY COURSE")
        log("-" * 100)

        for ca in sorted(tut_components, key=lambda x: x.shortfall):
            status = "INSUFFICIENT" if ca.has_insufficient_capacity else "OK"
            log(f"\n{ca.course_code} TUT [{status}]")
            log(
                f"  Sections: {ca.total_sections}, Capacity: {ca.total_capacity}, Available: {ca.total_available}, Demand: {ca.demand}"
            )

            for sec in ca.sections:
                avail_str = f"{sec.available} avail" if sec.available > 0 else "FULL"
                if sec.is_over_capacity:
                    avail_str = f"OVER +{sec.enrolled - sec.capacity}"
                time_str = ", ".join(sec.time_slots) if sec.time_slots else "TBA"
                log(
                    f"    Class {sec.class_nbr:>6}: {sec.enrolled:>3}/{sec.capacity:<3} [{avail_str:>10}] - {time_str}"
                )

    log()

    # ==================== SECTION 5: SECTION UTILIZATION ====================

    log("=" * 100)
    log("SECTION 5: SECTION UTILIZATION ANALYSIS")
    log("=" * 100)
    log()

    # Collect all sections
    all_section_analyses: list[SectionAnalysis] = []
    for course in course_analyses.values():
        for component in course.values():
            all_section_analyses.extend(component.sections)

    total_sections = len(all_section_analyses)
    full_sections = [s for s in all_section_analyses if s.is_full]
    over_cap_sections = [s for s in all_section_analyses if s.is_over_capacity]
    under_50_pct = [s for s in all_section_analyses if s.utilization_pct < 50]

    log("UTILIZATION SUMMARY")
    log("-" * 50)
    log(f"  Total sections (unique class_nbr): {total_sections}")
    log(
        f"  Sections at 100% capacity: {len(full_sections)} ({len(full_sections) / total_sections * 100:.1f}%)"
    )
    log(
        f"  Sections OVER capacity: {len(over_cap_sections)} ({len(over_cap_sections) / total_sections * 100:.1f}%)"
    )
    log(
        f"  Sections < 50% utilized: {len(under_50_pct)} ({len(under_50_pct) / total_sections * 100:.1f}%)"
    )
    log()

    # Histogram of utilization
    log("UTILIZATION DISTRIBUTION")
    log("-" * 50)
    ranges = [(0, 25), (25, 50), (50, 75), (75, 100), (100, 150)]
    for low, high in ranges:
        count = len(
            [s for s in all_section_analyses if low <= s.utilization_pct < high]
        )
        bar = "█" * (count // 2)
        if low == 100:
            log(f"  {low:>3}%+   : {count:>4} {bar}")
        else:
            log(f"  {low:>3}-{high:>3}%: {count:>4} {bar}")
    log()

    # Top over-utilized sections
    if over_cap_sections:
        log("SECTIONS OVER CAPACITY (top 20)")
        log("-" * 80)
        for sec in sorted(
            over_cap_sections, key=lambda x: x.enrolled - x.capacity, reverse=True
        )[:20]:
            log(
                f"  {sec.course_code} {sec.component} Class {sec.class_nbr}: {sec.enrolled}/{sec.capacity} (over by {sec.enrolled - sec.capacity})"
            )
    log()

    # ==================== SECTION 6: TIME CONFLICT ANALYSIS ====================

    log("=" * 100)
    log("SECTION 6: COURSE CONFLICT ANALYSIS")
    log("=" * 100)
    log()

    log("Analyzing time conflicts between course pairs...")
    log("(This shows which course pairs have limited non-conflicting options)")
    log()

    # Only analyze courses that students actually need together
    # Group courses by package
    courses_per_package: dict[str, set[str]] = {}
    for pkg_key, students in students_by_package.items():
        if students:
            pkg_info = db_packages.get(pkg_key)
            if pkg_info:
                courses_per_package[pkg_key] = set(pkg_info.course_codes)

    # Find course pairs that appear together in packages
    course_pair_analysis: list[TimeSlotAnalysis] = []
    analyzed_pairs: set[tuple[str, str]] = set()

    for pkg_key, course_codes in courses_per_package.items():
        valid_codes = [c for c in course_codes if c in course_id_map]

        for code1, code2 in combinations(valid_codes, 2):
            pair = tuple(sorted([code1, code2]))
            if pair in analyzed_pairs:
                continue
            analyzed_pairs.add(pair)

            cid1 = course_id_map[code1]
            cid2 = course_id_map[code2]

            sections1 = all_sections.get(cid1, [])
            sections2 = all_sections.get(cid2, [])

            if not sections1 or not sections2:
                continue

            non_conf, total = count_non_conflicting_combos_between_courses(
                sections1, sections2
            )

            if total > 0:
                conflict_rate = 1.0 - (non_conf / total)
                course_pair_analysis.append(
                    TimeSlotAnalysis(
                        course_pair=pair,
                        total_combos=total,
                        non_conflicting_combos=non_conf,
                        conflict_rate=conflict_rate,
                    )
                )

    # Sort by conflict rate (highest first)
    course_pair_analysis.sort(key=lambda x: x.conflict_rate, reverse=True)

    # Show high-conflict pairs
    high_conflict = [p for p in course_pair_analysis if p.conflict_rate > 0.5]

    if high_conflict:
        log(f"HIGH-CONFLICT COURSE PAIRS (>50% conflict rate): {len(high_conflict)}")
        log("-" * 80)
        log(
            f"{'Course 1':<15} {'Course 2':<15} {'Total':>8} {'Valid':>8} {'Conflict':>10}"
        )
        log("-" * 80)

        for analysis in high_conflict[:30]:
            log(
                f"{analysis.course_pair[0]:<15} {analysis.course_pair[1]:<15} "
                f"{analysis.total_combos:>8} {analysis.non_conflicting_combos:>8} "
                f"{analysis.conflict_rate * 100:>9.1f}%"
            )
    else:
        log("✓ No high-conflict course pairs detected (all <50% conflict rate)")

    log()

    # ==================== SECTION 7: PACKAGE ANALYSIS ====================

    log("=" * 100)
    log("SECTION 7: PACKAGE/STUDENT GROUP ANALYSIS")
    log("=" * 100)
    log()

    package_analyses: list[PackageAnalysis] = []

    for pkg_key in sorted(students_by_package.keys()):
        pkg_info = db_packages.get(pkg_key)
        if not pkg_info:
            continue

        students = students_by_package[pkg_key]
        course_codes = pkg_info.course_codes

        # Find problem courses for this package
        problem_courses = []
        for code in course_codes:
            if code in course_analyses:
                for comp, analysis in course_analyses[code].items():
                    if analysis.has_insufficient_capacity:
                        problem_courses.append(f"{code} {comp}")

        # Calculate flexibility (valid combos per course)
        combo_counts = []
        most_constrained_course = ""
        min_combos = float("inf")

        for code in course_codes:
            if code not in course_id_map:
                continue
            cid = course_id_map[code]
            sections = all_sections.get(cid, [])
            if sections:
                valid, by_comp = count_valid_combos(sections)
                combo_counts.append(valid)
                if valid < min_combos:
                    min_combos = valid
                    most_constrained_course = code

        avg_combos = sum(combo_counts) / len(combo_counts) if combo_counts else 0

        pa = PackageAnalysis(
            package_key=pkg_key,
            student_count=len(students),
            course_codes=course_codes,
            avg_valid_combos=avg_combos,
            min_valid_combos=int(min_combos) if min_combos != float("inf") else 0,
            max_valid_combos=max(combo_counts) if combo_counts else 0,
            most_constrained_course=most_constrained_course,
            most_constrained_count=int(min_combos) if min_combos != float("inf") else 0,
            problem_courses=problem_courses,
        )
        package_analyses.append(pa)

    log("PACKAGE SUMMARY")
    log("-" * 100)
    log(
        f"{'Package':<20} {'Students':>8} {'Courses':>8} {'Problems':>8} {'Min Combos':>12} {'Most Constrained':<20}"
    )
    log("-" * 100)

    for pa in package_analyses:
        log(
            f"{pa.package_key:<20} {pa.student_count:>8} {len(pa.course_codes):>8} "
            f"{len(pa.problem_courses):>8} {pa.min_valid_combos:>12} {pa.most_constrained_course:<20}"
        )

    log()

    # Detail problem packages
    problem_packages = [pa for pa in package_analyses if pa.problem_courses]

    if problem_packages:
        log("PACKAGES WITH CAPACITY PROBLEMS")
        log("-" * 80)

        for pa in problem_packages:
            log(f"\n{pa.package_key} ({pa.student_count} students)")
            log("  Problem courses:")
            for pc in pa.problem_courses:
                log(f"    • {pc}")

    log()

    # ==================== SECTION 8: RECOMMENDATIONS ====================

    log("=" * 100)
    log("SECTION 8: RECOMMENDATIONS")
    log("=" * 100)
    log()

    recommendations: list[tuple[str, str]] = []

    # Capacity issues
    for analysis in problem_components:
        rec = f"Add {abs(analysis.shortfall)} seats to {analysis.course_code} {analysis.component}"
        detail = f"Current: {analysis.total_available} available, Need: {analysis.demand} students"
        recommendations.append((rec, detail))

    # Over-capacity sections (data issue)
    if over_cap_sections:
        for sec in over_cap_sections:
            rec = f"Fix over-enrollment in {sec.course_code} {sec.component} Class {sec.class_nbr}"
            detail = f"Enrolled {sec.enrolled} > Capacity {sec.capacity} (over by {sec.enrolled - sec.capacity})"
            recommendations.append((rec, detail))

    # High conflict pairs
    critical_conflicts = [p for p in course_pair_analysis if p.conflict_rate > 0.8]
    for analysis in critical_conflicts[:5]:
        rec = f"Review scheduling between {analysis.course_pair[0]} and {analysis.course_pair[1]}"
        detail = f"{analysis.conflict_rate * 100:.0f}% of combo pairs conflict"
        recommendations.append((rec, detail))

    if recommendations:
        log(f"Found {len(recommendations)} recommendations:")
        log()
        for i, (rec, detail) in enumerate(recommendations, 1):
            log(f"  {i}. {rec}")
            log(f"     → {detail}")
            log()
    else:
        log("✓ No critical issues found - capacity appears sufficient")

    log()

    # ==================== SECTION 9: SUMMARY ====================

    log("=" * 100)
    log("SUMMARY")
    log("=" * 100)
    log()

    log(f"Students to allocate:       {len(students)}")
    log(f"Matched to packages:        {len(students) - len(unmatched_students)}")
    log(f"Unique courses needed:      {len(course_id_map)}")
    log(f"Total sections available:   {total_sections}")
    log()
    log(f"Sections at full capacity:  {len(full_sections)}")
    log(f"Sections OVER capacity:     {len(over_cap_sections)}")
    log(f"Course-components short:    {len(problem_components)}")
    log()

    if problem_components:
        log("CRITICAL SHORTFALLS:")
        for analysis in problem_components[:5]:
            log(
                f"  • {analysis.course_code} {analysis.component}: {abs(analysis.shortfall)} seats short"
            )

    log()
    log("=" * 100)

    # Save to file if requested
    if output_file:
        with open(output_file, "w") as f:
            f.write("\n".join(output_lines))
        print(f"\nReport saved to: {output_file}")


async def main():
    parser = argparse.ArgumentParser(description="Comprehensive Timetable Analysis")
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
        "--output",
        type=str,
        help="Output file path for the report",
    )

    args = parser.parse_args()

    if not Path(args.excel).exists():
        print(f"Excel file not found: {args.excel}")
        return

    await run_analysis(
        excel_path=args.excel,
        year=args.year,
        session_id=args.session,
        output_file=args.output,
    )


if __name__ == "__main__":
    asyncio.run(main())
