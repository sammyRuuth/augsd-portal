"""
Bulk Timetable Generator

Generates unique timetables for all students based on:
- packages.json: Maps branch plans to required courses
- count.csv: Student counts per plan
- Excel timetable file: Section details with capacities

Usage:
    python bulk_timetable_generator.py

Or with custom files:
    python bulk_timetable_generator.py --packages data/packages.json --count data/count.csv --timetable data/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx
"""

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ==================== Data Classes ====================


@dataclass
class Section:
    """Represents a course section with all its meeting times"""

    course_code: str  # e.g., "MATH F101"
    course_id: str  # e.g., "002862"
    subject: str  # e.g., "MATH"
    catalog: str  # e.g., "F101"
    title: str
    class_nbr: int
    section: str  # e.g., "L1", "T2", "P3"
    component: str  # LEC, TUT, LAB, PRO
    cap_enrl: int
    tot_enrl: int
    meetings: list[dict] = field(default_factory=list)
    exam_date: str | None = None
    exam_start: str | None = None
    exam_end: str | None = None
    instructor: str | None = None
    room: str | None = None

    @property
    def available_seats(self) -> int:
        return max(0, self.cap_enrl - self.tot_enrl)

    @property
    def logical_key(self) -> tuple:
        """Unique key for this logical section"""
        return (self.course_code, self.component, self.section, self.class_nbr)


@dataclass
class GeneratedTimetable:
    """A generated timetable with selected sections"""

    plan: str
    timetable_id: int
    sections: list[Section]
    total_units: float = 0.0
    batch_size: int = 0  # Number of students this timetable serves
    capacity_ceiling: int = 0  # Max students this timetable could accommodate
    is_variant: bool = False  # Variant generated for mixing options

    def get_class_nbrs(self) -> list[int]:
        """Get unique class numbers"""
        return list(set(s.class_nbr for s in self.sections))


# ==================== Parsing Functions ====================

DAY_PATTERNS = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "TH": "Thursday",
    "F": "Friday",
    "S": "Saturday",
    "SU": "Sunday",
}


def expand_class_pattern(pattern: str) -> list[str]:
    """Expand class patterns like 'MWF' or 'TTH' into list of days."""
    if not pattern:
        return []

    pattern = pattern.upper().strip()
    days = []
    i = 0

    while i < len(pattern):
        if i + 1 < len(pattern):
            two_char = pattern[i : i + 2]
            if two_char == "TH":
                days.append("Thursday")
                i += 2
                continue
            elif two_char == "SU":
                days.append("Sunday")
                i += 2
                continue

        char = pattern[i]
        if char in DAY_PATTERNS:
            days.append(DAY_PATTERNS[char])
        i += 1

    return days


def normalize_course_id(course_id: str) -> str:
    """Normalize course ID to 6-digit zero-padded format."""
    if not course_id:
        return ""
    numeric = "".join(c for c in str(course_id) if c.isdigit())
    return numeric.zfill(6) if numeric else ""


def parse_packages(file_path: str) -> dict[str, list[str]]:
    """
    Parse packages.json to get courses for each plan.
    Returns: {plan_name: [course_codes]}
    """
    with open(file_path, "r") as f:
        data = json.load(f)

    packages = {}
    for year, plans in data.items():
        for plan_key, courses in plans.items():
            # Normalize plan names (remove spaces around commas)
            plan_name = plan_key.replace(", ", ",").replace(" ,", ",")
            packages[plan_name] = courses

    return packages


def parse_count(file_path: str) -> dict[str, int]:
    """
    Parse count.csv to get student counts per plan.
    Returns: {plan_name: count}
    """
    counts = {}
    with open(file_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            plan = row["Plan"].strip().replace(", ", ",").replace(" ,", ",")
            count = int(row["Count"].strip())
            counts[plan] = count

    return counts


def canonicalize_course_list(courses: list[str]) -> tuple[str, ...]:
    """
    Create a canonical, order-insensitive representation of a course list.
    Whitespace is trimmed and duplicates are removed.
    """
    cleaned = {c.strip() for c in courses if c and str(c).strip()}
    return tuple(sorted(cleaned))


def group_plans_by_packages(
    packages: dict[str, list[str]], counts: dict[str, int]
) -> tuple[dict[str, list[str]], dict[str, int], dict[str, list[str]]]:
    """
    Combine plans that share the same package (course set).

    Returns:
        grouped_packages: {combined_plan_name: course_list}
        grouped_counts: {combined_plan_name: total_count}
        membership: {combined_plan_name: [original_plan_names]}
    """
    groups: dict[tuple[str, ...], dict] = {}

    for plan, courses in packages.items():
        key = canonicalize_course_list(courses)
        if key not in groups:
            groups[key] = {"courses": courses, "plans": []}
        groups[key]["plans"].append(plan)

    grouped_packages: dict[str, list[str]] = {}
    grouped_counts: dict[str, int] = {}
    membership: dict[str, list[str]] = {}

    for key, data in groups.items():
        plans = sorted(data["plans"])
        # Keep the original course ordering from the first plan in the group
        course_list = data["courses"]
        if len(plans) == 1:
            group_name = plans[0]
        else:
            group_name = f"COMBINED:{'+'.join(plans)}"

        grouped_packages[group_name] = course_list
        grouped_counts[group_name] = sum(counts.get(p, 0) for p in plans)
        membership[group_name] = plans

    return grouped_packages, grouped_counts, membership


def parse_timetable_excel(
    file_path: str,
) -> tuple[dict[str, list[Section]], dict[str, str]]:
    """
    Parse timetable Excel file.

    Returns:
        - sections_by_course: {course_code: [Section]}
        - course_id_map: {course_code: course_id}
    """
    df = pd.read_excel(file_path, header=None)

    # Detect header row
    expected_cols = ["Course ID", "Subject", "Catalog", "Class Nbr", "Section"]
    header_row = 0
    for idx in range(min(5, len(df))):
        row_values = df.iloc[idx].astype(str).str.strip().str.upper().tolist()
        matches = sum(
            1 for exp in expected_cols if any(exp.upper() in val for val in row_values)
        )
        if matches >= 3:
            header_row = idx
            break

    # Re-read with correct header
    df = pd.read_excel(file_path, header=header_row)
    df.columns = df.columns.str.strip()

    # Group meetings by logical section key
    logical_sections: dict[tuple, dict] = {}
    course_id_map: dict[str, str] = {}

    for idx, row in df.iterrows():
        try:
            raw_course_id = str(row.get("Course ID", "")).strip()
            course_id = normalize_course_id(raw_course_id)
            subject = str(row.get("Subject", "")).strip()
            catalog = str(row.get("Catalog", "")).strip()

            if not course_id or not subject or not catalog:
                continue

            course_code = f"{subject} {catalog}"
            course_id_map[course_code] = course_id

            class_nbr_raw = row.get("Class Nbr", 0)
            if pd.isna(class_nbr_raw):
                continue
            class_nbr = int(float(class_nbr_raw))
            if not class_nbr:
                continue

            section_name = str(row.get("Section", "")).strip()
            component = str(row.get("Component", "")).strip()
            title = str(row.get("Title", row.get("Course Title", ""))).strip()

            # Parse class pattern and times
            class_pattern = (
                str(row.get("Class Pattern", "")).strip()
                if pd.notna(row.get("Class Pattern"))
                else ""
            )

            mtg_start = None
            mtg_start_str = None
            if pd.notna(row.get("Mtg Start", row.get("MTG START"))):
                try:
                    val = row.get("Mtg Start", row.get("MTG START"))
                    if hasattr(val, "hour"):
                        mtg_start = val
                    else:
                        mtg_start = pd.to_datetime(val).time()
                    mtg_start_str = mtg_start.strftime("%H:%M") if mtg_start else None
                except Exception:
                    pass

            mtg_end = None
            mtg_end_str = None
            end_time_val = row.get("End Time", row.get("END TIME"))
            if pd.notna(end_time_val):
                try:
                    if hasattr(end_time_val, "hour"):
                        mtg_end = end_time_val
                    else:
                        mtg_end = pd.to_datetime(end_time_val).time()
                    mtg_end_str = mtg_end.strftime("%H:%M") if mtg_end else None
                except Exception:
                    pass

            # Expand days
            days = expand_class_pattern(class_pattern)
            if not days or not (mtg_start and mtg_end):
                days = [""]

            # Parse capacity
            cap_enrl = 0
            if pd.notna(row.get("Cap Enrl", row.get("CAP ENRL"))):
                try:
                    cap_enrl = int(float(row.get("Cap Enrl", row.get("CAP ENRL"))))
                except Exception:
                    pass

            tot_enrl = 0
            if pd.notna(row.get("Tot Enrl", row.get("TOT ENRL"))):
                try:
                    tot_enrl = int(float(row.get("Tot Enrl", row.get("TOT ENRL"))))
                except Exception:
                    pass

            # Instructor and room
            instructor = (
                str(row.get("Name", "")).strip() if pd.notna(row.get("Name")) else None
            )
            room = (
                str(row.get("Room", "")).strip() if pd.notna(row.get("Room")) else None
            )

            # Exam info
            exam_date = None
            if pd.notna(row.get("Exam Date")):
                try:
                    val = row.get("Exam Date")
                    if hasattr(val, "date"):
                        exam_date = (
                            val.date().isoformat() if hasattr(val, "date") else str(val)
                        )
                    else:
                        exam_date = pd.to_datetime(val).date().isoformat()
                except Exception:
                    pass

            # Create/update logical section
            logical_key = (course_code, component, section_name, class_nbr)

            for day in days:
                if logical_key not in logical_sections:
                    logical_sections[logical_key] = {
                        "course_code": course_code,
                        "course_id": course_id,
                        "subject": subject,
                        "catalog": catalog,
                        "title": title,
                        "class_nbr": class_nbr,
                        "section": section_name,
                        "component": component,
                        "cap_enrl": cap_enrl,
                        "tot_enrl": tot_enrl,
                        "meetings": [],
                        "exam_date": exam_date,
                        "instructor": instructor,
                        "room": room,
                    }

                # Add meeting time
                if day and mtg_start_str and mtg_end_str:
                    meeting = {
                        "day": day,
                        "start": mtg_start_str,
                        "end": mtg_end_str,
                    }
                    # Avoid duplicates
                    if meeting not in logical_sections[logical_key]["meetings"]:
                        logical_sections[logical_key]["meetings"].append(meeting)

                # Update capacity (take max)
                if cap_enrl > logical_sections[logical_key]["cap_enrl"]:
                    logical_sections[logical_key]["cap_enrl"] = cap_enrl

        except Exception as e:
            print(f"Error parsing row {idx}: {e}")
            continue

    # Convert to Section objects grouped by course
    sections_by_course: dict[str, list[Section]] = defaultdict(list)

    for key, data in logical_sections.items():
        # Apply section capacity override if defined (replaces Excel value)
        effective_cap = data["cap_enrl"]
        capacity_override = get_section_capacity_override(
            data["course_code"], data["component"]
        )
        if capacity_override is not None:
            effective_cap = capacity_override  # Override Excel value completely

        section = Section(
            course_code=data["course_code"],
            course_id=data["course_id"],
            subject=data["subject"],
            catalog=data["catalog"],
            title=data["title"],
            class_nbr=data["class_nbr"],
            section=data["section"],
            component=data["component"],
            cap_enrl=effective_cap,
            tot_enrl=data["tot_enrl"],
            meetings=data["meetings"],
            exam_date=data["exam_date"],
            instructor=data["instructor"],
            room=data["room"],
        )
        sections_by_course[data["course_code"]].append(section)

    return dict(sections_by_course), course_id_map


# ==================== Conflict Detection ====================


def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight."""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return -1


def create_time_mask(start: str, end: str) -> int:
    """Create a 5-minute resolution bitmask for a time interval."""
    if not start or not end:
        return 0
    s_min = time_to_minutes(start)
    e_min = time_to_minutes(end)
    if s_min < 0 or e_min <= s_min:
        return 0
    s_idx = s_min // 5
    e_idx = e_min // 5
    if e_idx <= s_idx:
        return 0
    width = e_idx - s_idx
    return ((1 << width) - 1) << s_idx


# Gap/compactness helpers to prefer back-to-back classes with minimal idle time
LUNCH_START_MIN = 12 * 60
LUNCH_END_MIN = 14 * 60
EVENING_PRIORITY_MIN = 16 * 60
GAP_IGNORE_MINUTES = 10  # Small gaps are fine


def _gap_after(prev_end: int, next_start: int) -> float:
    """Compute penalty for gap between two intervals."""
    if next_start <= prev_end:
        return 0.0
    gap = next_start - prev_end

    # Remove lunch overlap from penalty window
    lunch_overlap = max(
        0, min(next_start, LUNCH_END_MIN) - max(prev_end, LUNCH_START_MIN)
    )
    gap = max(0, gap - lunch_overlap)

    if gap <= GAP_IGNORE_MINUTES:
        return 0.0

    evening_multiplier = (
        2.0
        if next_start >= EVENING_PRIORITY_MIN or prev_end >= EVENING_PRIORITY_MIN
        else 1.0
    )
    return gap * evening_multiplier


def compactness_penalty(sections: list[Section]) -> float:
    """Penalty for gaps; lower is better. Ignores lunch gap, penalizes evening gaps more."""
    by_day: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for sec in sections:
        for m in sec.meetings:
            start = time_to_minutes(m.get("start", ""))
            end = time_to_minutes(m.get("end", ""))
            day = m.get("day")
            if start < 0 or end <= start or not day:
                continue
            by_day[day].append((start, end))

    penalty = 0.0
    for intervals in by_day.values():
        intervals.sort()
        prev_end = None
        for start, end in intervals:
            if prev_end is not None:
                penalty += _gap_after(prev_end, start)
            prev_end = max(prev_end, end) if prev_end is not None else end

    return penalty


def sections_clash(section1: Section, section2: Section) -> bool:
    """Check if two sections have a time clash."""
    # Build day masks for each section
    masks1: dict[str, int] = defaultdict(int)
    masks2: dict[str, int] = defaultdict(int)

    for m in section1.meetings:
        mask = create_time_mask(m["start"], m["end"])
        if mask:
            masks1[m["day"]] |= mask

    for m in section2.meetings:
        mask = create_time_mask(m["start"], m["end"])
        if mask:
            masks2[m["day"]] |= mask

    # Check for overlap on any day
    for day, mask1 in masks1.items():
        if mask1 and (mask1 & masks2.get(day, 0)):
            return True

    # Check exam clash
    if section1.exam_date and section2.exam_date:
        if section1.exam_date == section2.exam_date:
            # Simple overlap check (could be more precise)
            return True

    return False


def combo_clashes_with_current(combo: list[Section], current: list[Section]) -> bool:
    """Check if a combo of sections clashes with current selections."""
    for new_sec in combo:
        for curr_sec in current:
            if sections_clash(new_sec, curr_sec):
                return True
    return False


def internal_clash(combo: list[Section]) -> bool:
    """Check if sections within a combo clash with each other."""
    for i in range(len(combo)):
        for j in range(i + 1, len(combo)):
            if sections_clash(combo[i], combo[j]):
                return True
    return False


# ==================== Timetable Generation ====================


def get_component_priority(component: str) -> int:
    """Priority for component ordering."""
    component = component.upper().strip()
    if component == "LAB":
        return 1
    elif component == "TUT":
        return 2
    elif component == "LEC":
        return 3
    return 4


def generate_course_combos(
    sections: list[Section],
    remaining_capacity: dict[int, int] | None = None,
) -> list[list[Section]]:
    """
    Generate all valid section combinations for a course.
    Each combo has one section per component (LEC, TUT, LAB, etc.)

    Args:
        sections: List of available sections
        remaining_capacity: Optional dict of {class_nbr: remaining_seats} for prioritization
    """
    # Group by component
    by_component: dict[str, list[Section]] = defaultdict(list)
    for s in sections:
        by_component[s.component].append(s)

    if not by_component:
        return []

    # Sort components by priority
    components = sorted(by_component.keys(), key=get_component_priority)

    # Sort sections within each component by remaining capacity (descending)
    # SELF-ADAPTIVE: Heavily penalize overfilled/near-full sections to balance load

    for comp in components:
        if remaining_capacity:

            def balance_score(s, component=comp):
                cap = remaining_capacity.get(s.class_nbr, s.available_seats)
                original_cap = s.cap_enrl
                max_overfill = get_max_overfill(s.course_code, component)

                if ALLOW_NEGATIVE_CAPACITY:
                    # LAB/PRO/TUT sections have physical limits - spread load evenly
                    if (
                        component in HARD_STRICT_COMPONENTS
                        or component in SOFT_STRICT_COMPONENTS
                    ):
                        # Check if section has exceeded max overfill
                        if cap < -max_overfill:
                            # Exceeded max overfill - make unavailable
                            return -999999
                        elif cap < 0:
                            # Already overfilled but within limit - heavy penalty
                            return cap * 1000
                        elif cap < original_cap * 0.2:
                            # Nearly full - moderate penalty
                            return cap - original_cap
                        else:
                            return cap
                    else:
                        # LEC can concentrate - prefer highest remaining
                        return cap
                else:
                    # Normal mode - spread load and avoid overfilled sections
                    if cap < 0:
                        # Already overfilled - big penalty
                        return cap * 10  # Very negative score
                    elif cap < original_cap * 0.1:
                        # Less than 10% remaining - penalty
                        return cap - original_cap
                    else:
                        # Normal - prefer higher remaining capacity
                        return cap

            by_component[comp].sort(key=balance_score, reverse=True)
        else:
            by_component[comp].sort(key=lambda s: s.available_seats, reverse=True)

    # Generate combinations using DFS
    combos: list[list[Section]] = []

    def dfs(idx: int, acc: list[Section]):
        if idx == len(components):
            combos.append(acc[:])
            return

        comp = components[idx]
        for section in by_component[comp]:
            # Check internal clash with already selected sections
            clash = False
            for chosen in acc:
                if sections_clash(chosen, section):
                    clash = True
                    break
            if clash:
                continue

            acc.append(section)
            dfs(idx + 1, acc)
            acc.pop()

    dfs(0, [])

    # Sort combos by balance score - heavily prefer combos using less-filled sections
    if remaining_capacity:

        def combo_balance_score(combo):
            total_score = 0
            for s in combo:
                cap = remaining_capacity.get(s.class_nbr, s.available_seats)
                original_cap = s.cap_enrl
                max_overfill = get_max_overfill(s.course_code, s.component)

                if ALLOW_NEGATIVE_CAPACITY:
                    # LAB/PRO/TUT - heavily penalize overfilled to spread load
                    if (
                        s.component in HARD_STRICT_COMPONENTS
                        or s.component in SOFT_STRICT_COMPONENTS
                    ):
                        if cap < -max_overfill:
                            # Exceeded limit - make combo very undesirable
                            total_score += -999999
                        elif cap < 0:
                            total_score += cap * 1000  # Very heavy penalty
                        elif cap < original_cap * 0.2:
                            total_score += cap - original_cap
                        else:
                            total_score += cap
                    else:
                        # LEC - simple preference for capacity
                        total_score += cap
                else:
                    if cap < 0:
                        # Overfilled section - heavy penalty
                        total_score += cap * 10
                    elif original_cap > 0 and cap < original_cap * 0.2:
                        # Less than 20% remaining - penalty
                        total_score += cap - original_cap * 0.5
                    else:
                        # Prefer sections with more remaining capacity
                        total_score += cap

            return total_score

        combos.sort(key=combo_balance_score, reverse=True)
    else:
        combos.sort(key=lambda c: sum(s.available_seats for s in c), reverse=True)

    return combos


# Courses that can go negative on capacity (limited sections)
UNLIMITED_CAPACITY_COURSES = {"BITS F101-1", "BITS K101-1"}

# Components that can ALWAYS be overfilled (lectures can accommodate more students)
OVERFILLABLE_COMPONENTS = {"LEC"}

# Components that can be overfilled ONLY after regular capacity is exhausted
SOFT_STRICT_COMPONENTS = {"TUT"}  # Tutorials - can overfill as last resort

# Components that can NEVER be overfilled (hands-on sessions have strict limits)
HARD_STRICT_COMPONENTS = {"LAB", "PRO", "PRA"}  # Labs/Practicals - safety limits

# Mode for allowing negative capacity (to find exact capacity needs)
ALLOW_NEGATIVE_CAPACITY = (
    True  # When True, algorithm will assign to least-negative section
)

# Default max overfill limits per component (can be overridden via CLI)
MAX_OVERFILL_LIMITS = {"LAB": 0, "PRO": 3, "PRA": 3, "TUT": 0}

# Course-specific overrides for max overfill (stricter limits for specific courses)
# Set to 0 to completely disallow overfill
COURSE_OVERFILL_OVERRIDES = {
    "CHEM F101": {"LAB": 0},
    "PHY F101": {"LAB": 0},  # No overfill allowed for PHY labs
}

# Section-specific capacity OVERRIDES (replaces Excel values)
# These values REPLACE the Excel capacity for the specified course/component
# Format: "COURSE_PREFIX": {"COMPONENT": capacity}
SECTION_CAPACITY_OVERRIDES = {
    "BIO F101": {"TUT": 58, "LAB": 50},
    "PHY F101": {"TUT": 58, "LAB": 50},
    "CHEM F101": {"TUT": 58, "LAB": 60},
    "MATH F102": {"TUT": 58},
    "MATH F113": {"TUT": 58},
}


def get_section_capacity_override(course_code: str, component: str) -> int | None:
    """
    Get the capacity override for a section.
    Returns the override value if defined, which REPLACES the Excel capacity.
    Returns None if no override is defined (use Excel capacity as-is).
    """
    for course_prefix, overrides in SECTION_CAPACITY_OVERRIDES.items():
        if course_prefix in course_code:
            if component in overrides:
                return overrides[component]
    return None


# Track warnings to avoid repetition (reset per run)
_WARNED_COURSES: set[str] = set()


def get_max_overfill(course_code: str, component: str) -> int:
    """Get the max overfill for a course/component, considering course-specific overrides."""
    # Check for course-specific override
    for course_prefix, overrides in COURSE_OVERFILL_OVERRIDES.items():
        if course_prefix in course_code:
            if component in overrides:
                return overrides[component]
    # Fall back to global limit
    return MAX_OVERFILL_LIMITS.get(component, float("inf"))


def generate_timetable_for_courses(
    course_codes: list[str],
    sections_by_course: dict[str, list[Section]],
    remaining_capacity: dict[int, int],
    require_all_courses: bool = True,
    allow_tut_overfill: bool = False,
    avoid_class_nbrs: set[int] | None = None,
    compactness_weight: float = 0.0,
) -> list[Section] | None:
    """
    Generate a single timetable using GREEDY algorithm (fast).

    Args:
        course_codes: List of required course codes
        sections_by_course: Available sections by course
        remaining_capacity: {class_nbr: remaining_seats} - only use sections with capacity > 0
        require_all_courses: If True, timetable must include ALL courses
        allow_tut_overfill: If True, allow TUT sections to overfill (used in second pass)
        avoid_class_nbrs: Optional set of class numbers to avoid (for variant generation)
        compactness_weight: Optional weight to prefer back-to-back classes (0 disables)

    Returns:
        List of selected sections, or None if no valid timetable found
    """
    # Build combos for each course, filtering by remaining capacity
    course_combos: list[tuple[str, list[list[Section]]]] = []

    for code in course_codes:
        sections = sections_by_course.get(code, [])
        if not sections:
            print(f"    Warning: No sections found for {code}")
            if require_all_courses:
                return None
            continue

        # Filter sections based on component type:
        # - LEC: can always overfill
        # - TUT: can overfill only if allow_tut_overfill=True (second pass)
        # - LAB/PRO: strict capacity, never overfill (unless ALLOW_NEGATIVE_CAPACITY)
        # - UNLIMITED_CAPACITY_COURSES: always include all sections
        # Max overfill limits per component type

        if code in UNLIMITED_CAPACITY_COURSES:
            available = sections
        elif ALLOW_NEGATIVE_CAPACITY:
            # Allow sections up to their max overfill limit
            available = []
            for s in sections:
                if avoid_class_nbrs and s.class_nbr in avoid_class_nbrs:
                    continue
                # Check if section has exceeded max overfill (course-aware)
                cap = remaining_capacity.get(s.class_nbr, s.available_seats)
                max_overfill = get_max_overfill(s.course_code, s.component)
                if cap < -max_overfill:
                    # Section has exceeded its max overfill - skip it
                    continue
                available.append(s)
        else:
            available = []
            for s in sections:
                if avoid_class_nbrs and s.class_nbr in avoid_class_nbrs:
                    # Skip already-used sections when trying to diversify variants
                    continue

                cap = remaining_capacity.get(s.class_nbr, 0)
                if s.component in OVERFILLABLE_COMPONENTS:
                    # LEC can always overfill - include all
                    available.append(s)
                elif s.component in SOFT_STRICT_COMPONENTS and allow_tut_overfill:
                    # TUT can overfill in second pass - include all
                    available.append(s)
                elif cap > 0:
                    # All other components need positive capacity
                    available.append(s)

        if not available:
            warn_key = f"no_capacity:{code}"
            if warn_key not in _WARNED_COURSES:
                _WARNED_COURSES.add(warn_key)
                print(f"    ⚠ No sections with capacity for {code}")
            if require_all_courses:
                return None
            continue

        combos = generate_course_combos(available, remaining_capacity)
        if not combos:
            warn_key = f"no_combos:{code}"
            if warn_key not in _WARNED_COURSES:
                _WARNED_COURSES.add(warn_key)
                print(f"    ⚠ No valid combos for {code}")
            if require_all_courses:
                return None
            continue

        course_combos.append((code, combos))

    if not course_combos:
        return None

    # Must have all courses
    if require_all_courses and len(course_combos) != len(course_codes):
        return None

    # Sort courses by constraint (fewer combos first) for greedy selection
    course_combos.sort(key=lambda x: len(x[1]))

    import random

    # Use iterative greedy with multiple random attempts
    max_attempts = 100

    for attempt in range(max_attempts):
        selected: list[Section] = []
        success = True

        # Shuffle course order (except first attempt)
        attempt_combos = course_combos[:]
        if attempt > 0:
            random.shuffle(attempt_combos)

        for code, combos in attempt_combos:
            # Shuffle combos for variety (except first attempt)
            if attempt > 0 and len(combos) > 1:
                combos = random.sample(combos, len(combos))

            found = False
            for combo in combos:
                if internal_clash(combo):
                    continue
                if combo_clashes_with_current(combo, selected):
                    continue
                # Valid combo found - add it
                selected.extend(combo)
                found = True
                break

            if not found:
                success = False
                break

        if success:
            return selected

    # All attempts failed
    if require_all_courses:
        return None
    return selected if selected else None


def find_course_match(
    course_code: str, sections_by_course: dict[str, list[Section]]
) -> str | None:
    """
    Find a matching course code, handling variations like 'BITS F101' -> 'BITS F101-1'.
    """
    # Direct match
    if course_code in sections_by_course:
        return course_code

    # Try with '-1' suffix (common variation)
    if f"{course_code}-1" in sections_by_course:
        return f"{course_code}-1"

    # Try partial match (course code is a prefix)
    for available_code in sections_by_course.keys():
        if available_code.startswith(course_code):
            return available_code

    return None


def write_timetable_to_csv(
    timetable: GeneratedTimetable,
    output_file: Path,
    write_header: bool = False,
):
    """Append a single timetable to CSV file incrementally."""
    with open(output_file, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(
                [
                    "Plan",
                    "Timetable ID",
                    "Batch Size",
                    "Capacity Ceiling",
                    "Variant",
                    "Course Code",
                    "Component",
                    "Section",
                    "Class Nbr",
                    "Day",
                    "Start",
                    "End",
                    "Room",
                    "Instructor",
                ]
            )

        for section in timetable.sections:
            if section.meetings:
                for meeting in section.meetings:
                    writer.writerow(
                        [
                            timetable.plan,
                            timetable.timetable_id,
                            timetable.batch_size,
                            timetable.capacity_ceiling,
                            "yes" if timetable.is_variant else "no",
                            section.course_code,
                            section.component,
                            section.section,
                            section.class_nbr,
                            meeting.get("day", ""),
                            meeting.get("start", ""),
                            meeting.get("end", ""),
                            section.room or "",
                            section.instructor or "",
                        ]
                    )
            else:
                # Section with no meeting times (online/self-study)
                writer.writerow(
                    [
                        timetable.plan,
                        timetable.timetable_id,
                        timetable.batch_size,
                        timetable.capacity_ceiling,
                        "yes" if timetable.is_variant else "no",
                        section.course_code,
                        section.component,
                        section.section,
                        section.class_nbr,
                        "Online/Self-Study",
                        "",
                        "",
                        section.room or "",
                        section.instructor or "",
                    ]
                )


def calculate_fitness(
    timetables: dict[str, list[GeneratedTimetable]],
    counts: dict[str, int],
    capacity_usage: dict[int, int],
    sections_by_course: dict[str, list[Section]],
) -> tuple[float, dict]:
    """
    Calculate fitness score for a generation result.
    Higher is better.

    Returns:
        (fitness_score, metrics_dict)
    """
    # Total students assigned
    total_assigned = sum(
        sum(tt.batch_size for tt in plan_tts) for plan_tts in timetables.values()
    )
    total_needed = sum(counts.values())
    assignment_ratio = total_assigned / total_needed if total_needed > 0 else 0

    # Calculate balance score (variance of fill ratios for LEC sections)
    lec_fill_ratios = []
    overfill_penalty = 0
    overfilled_section_count = 0

    for course_secs in sections_by_course.values():
        for s in course_secs:
            if s.component == "LEC":
                used = capacity_usage.get(s.class_nbr, 0)
                if s.cap_enrl > 0:
                    fill_ratio = used / s.cap_enrl
                    lec_fill_ratios.append(fill_ratio)
                    if fill_ratio > 1.0:
                        overfill_penalty += (fill_ratio - 1.0) ** 2
                        overfilled_section_count += 1

    # Calculate variance of fill ratios (lower is better = more balanced)
    if lec_fill_ratios:
        mean_fill = sum(lec_fill_ratios) / len(lec_fill_ratios)
        variance = sum((r - mean_fill) ** 2 for r in lec_fill_ratios) / len(
            lec_fill_ratios
        )
        balance_score = 1.0 / (
            1.0 + variance
        )  # Convert to 0-1 score (higher is better)
    else:
        balance_score = 1.0

    # Combined fitness:
    # 1. First priority: 100% assignment (must achieve this)
    # 2. Second priority: minimize number of overfilled sections
    # 3. Third priority: minimize amount of overfill
    # 4. Fourth priority: balance

    # Use tiered scoring: assignment is a gate (huge bonus for 100%)
    if assignment_ratio >= 1.0:
        assignment_bonus = 1000  # Huge bonus for 100% assignment
    else:
        assignment_bonus = assignment_ratio * 100  # 0-100 for partial

    # Penalty per overfilled section (want to minimize count)
    overfill_count_penalty = overfilled_section_count * 200  # Heavy penalty per section

    # Penalty for amount of overfill
    overfill_amount_penalty = overfill_penalty * 50

    fitness = (
        assignment_bonus
        + balance_score * 10  # 0-10 points for balance
        - overfill_count_penalty  # Heavy penalty for number of overfilled sections
        - overfill_amount_penalty  # Penalty for amount of overfill
    )

    metrics = {
        "total_assigned": total_assigned,
        "total_needed": total_needed,
        "assignment_ratio": assignment_ratio,
        "balance_score": balance_score,
        "overfill_penalty": overfill_penalty,
        "overfilled_sections": overfilled_section_count,
        "fitness": fitness,
    }

    return fitness, metrics


def choose_batch_size(
    base_size: int,
    default_capacity: int,
    students_remaining: int,
    min_batch_size: int | None = None,
    enable_randomness: bool = True,
) -> int:
    """
    Choose a batch size with optional randomness to create more, smaller batches.
    """
    if base_size <= 0 or students_remaining <= 0:
        return 0

    upper = min(base_size, default_capacity, students_remaining)

    # Set a lower bound to split batches more often, but keep it sane
    lower = (
        min_batch_size if min_batch_size is not None else max(1, default_capacity // 2)
    )
    lower = max(1, min(lower, upper))

    if not enable_randomness or upper <= lower:
        return upper

    import random

    return random.randint(lower, upper)


def generate_bulk_timetables_single_run(
    packages: dict[str, list[str]],
    counts: dict[str, int],
    sections_by_course: dict[str, list[Section]],
    plan_order: list[str],
    default_capacity: int = 40,
    verbose: bool = True,
    min_batch_size: int | None = None,
    batch_randomness: bool = True,
    min_timetables_per_plan: int = 1,
    variant_retry_attempts: int = 3,
    compactness_weight: float = 0.0,
) -> tuple[dict[str, list[GeneratedTimetable]], dict[int, int]]:
    """
    Single run of timetable generation with a specific plan order.
    Returns (timetables, capacity_usage).

    Args:
        min_batch_size: Lower bound when splitting cohorts; smaller -> more timetables.
        batch_randomness: When True, batches are jittered to avoid single large groups.
        min_timetables_per_plan: Target minimum number of variants per plan for mixing.
        variant_retry_attempts: Attempts to diversify section combos when duplicates occur.
        compactness_weight: Optional weight to prefer back-to-back classes; 0 disables.

    Notes:
        - If a plan has fewer students than min_timetables_per_plan, zero-sized
          variant timetables are produced to give at least that many options, with
          capacity_ceiling indicating how many students they could host.
    """
    all_timetables: dict[str, list[GeneratedTimetable]] = {}

    # Global capacity tracking
    global_remaining_capacity: dict[int, int] = {}
    for course_sections in sections_by_course.values():
        for section in course_sections:
            if section.class_nbr not in global_remaining_capacity:
                # Use available seats (cap - enrolled), not total capacity
                available = section.available_seats
                # Add 8 extra seats for tutorial sections
                if section.component == "TUT":
                    available += 8
                global_remaining_capacity[section.class_nbr] = available

    for plan in plan_order:
        if plan not in packages:
            continue

        courses = packages[plan]

        # Map course codes to available courses
        resolved_courses = []
        for code in courses:
            matched = find_course_match(code, sections_by_course)
            if matched:
                resolved_courses.append(matched)

        if not resolved_courses:
            continue

        # Find student count
        student_count = counts.get(plan, 0)
        if not student_count:
            plan_parts = set(plan.split(","))
            for count_plan, count in counts.items():
                count_parts = set(count_plan.split(","))
                if plan_parts.issubset(count_parts) or count_parts.issubset(plan_parts):
                    student_count = count
                    break
                if len(plan_parts & count_parts) >= 0.8 * max(
                    len(plan_parts), len(count_parts)
                ):
                    student_count = count
                    break

        if not student_count:
            continue

        plan_timetables: list[GeneratedTimetable] = []
        seen_class_sets: set[frozenset[int]] = set()
        students_remaining = student_count
        timetable_id = 1
        allow_tut_overfill = False

        while students_remaining > 0 or len(plan_timetables) < min_timetables_per_plan:
            sections = generate_timetable_for_courses(
                resolved_courses,
                sections_by_course,
                global_remaining_capacity,
                require_all_courses=True,
                allow_tut_overfill=allow_tut_overfill,
                compactness_weight=compactness_weight,
            )

            if sections is None or len(sections) == 0:
                if not allow_tut_overfill:
                    allow_tut_overfill = True
                    sections = generate_timetable_for_courses(
                        resolved_courses,
                        sections_by_course,
                        global_remaining_capacity,
                        require_all_courses=True,
                        allow_tut_overfill=True,
                        compactness_weight=compactness_weight,
                    )

                if sections is None or len(sections) == 0:
                    break

            # Calculate batch size
            assert sections is not None  # for type checkers
            sections_list: list[Section] = list(sections)
            class_set = frozenset(s.class_nbr for s in sections_list)

            # Try to diversify combos if we still need variants and hit a duplicate
            if (
                class_set in seen_class_sets
                and len(plan_timetables) < min_timetables_per_plan
            ):
                diversified = False
                for _ in range(variant_retry_attempts):
                    alt_sections = generate_timetable_for_courses(
                        resolved_courses,
                        sections_by_course,
                        global_remaining_capacity,
                        require_all_courses=True,
                        allow_tut_overfill=allow_tut_overfill,
                        avoid_class_nbrs=set(class_set),
                        compactness_weight=compactness_weight,
                    )
                    if alt_sections:
                        alt_set = frozenset(s.class_nbr for s in alt_sections)
                        if alt_set not in seen_class_sets:
                            sections_list = list(alt_sections)
                            class_set = alt_set
                            diversified = True
                            break

                if not diversified:
                    # Accept duplicate to avoid infinite loops when no alternatives
                    pass

            seen_class_sets.add(class_set)
            used_class_nbrs = set(s.class_nbr for s in sections_list)
            strict_nbrs = set(
                s.class_nbr
                for s in sections_list
                if s.component in HARD_STRICT_COMPONENTS
                and s.course_code not in UNLIMITED_CAPACITY_COURSES
            )

            if not allow_tut_overfill:
                strict_nbrs.update(
                    s.class_nbr
                    for s in sections_list
                    if s.component in SOFT_STRICT_COMPONENTS
                    and s.course_code not in UNLIMITED_CAPACITY_COURSES
                )

            if ALLOW_NEGATIVE_CAPACITY:
                # Calculate max batch size respecting overfill limits (course-aware)
                max_batch_by_limits = float("inf")
                for s in sections_list:
                    max_overfill = get_max_overfill(s.course_code, s.component)
                    if max_overfill < float("inf"):
                        cap = global_remaining_capacity.get(
                            s.class_nbr, s.available_seats
                        )
                        # How many more can we add before hitting limit?
                        headroom = cap + max_overfill
                        if headroom < max_batch_by_limits:
                            max_batch_by_limits = headroom

                if max_batch_by_limits == float("inf"):
                    max_batch_by_limits = students_remaining
                elif max_batch_by_limits <= 0:
                    # All limited sections are at capacity - need different combo
                    break

                if strict_nbrs:
                    min_cap = min(global_remaining_capacity[nbr] for nbr in strict_nbrs)
                    base_batch = min(
                        max(min_cap, students_remaining), max_batch_by_limits
                    )
                else:
                    base_batch = min(default_capacity, max_batch_by_limits)
            elif strict_nbrs:
                base_batch = min(global_remaining_capacity[nbr] for nbr in strict_nbrs)
            else:
                if allow_tut_overfill:
                    # When overfilling tutorials, keep batches intentionally small
                    base_batch = max(10, default_capacity // 2)
                else:
                    base_batch = default_capacity

            # Keep enough students to hit the target number of timetables
            target_remaining = max(1, min_timetables_per_plan - len(plan_timetables))
            if students_remaining > 0:
                max_for_target = max(
                    1, math.ceil(students_remaining / target_remaining)
                )
            else:
                # Variants after assignment: use base batch as theoretical ceiling
                max_for_target = base_batch if base_batch > 0 else default_capacity

            effective_default_capacity = min(default_capacity, max_for_target)

            variant_only = students_remaining <= 0
            if not variant_only:
                if ALLOW_NEGATIVE_CAPACITY:
                    # Assign students respecting overfill limits
                    batch_size = min(students_remaining, max_for_target, base_batch)
                else:
                    batch_size = choose_batch_size(
                        base_batch,
                        default_capacity=effective_default_capacity,
                        students_remaining=students_remaining,
                        min_batch_size=min_batch_size,
                        enable_randomness=batch_randomness,
                    )
            else:
                # Placeholder variant: keep capacity info but do not consume seats
                batch_size = 0

            if batch_size <= 0 and not variant_only and not ALLOW_NEGATIVE_CAPACITY:
                break

            capacity_ceiling = max(
                0,
                min(
                    base_batch if base_batch > 0 else students_remaining,
                    effective_default_capacity,
                    students_remaining if students_remaining > 0 else max_for_target,
                ),
            )

            if variant_only and capacity_ceiling <= 0:
                # No capacity left for additional variants
                break

            timetable = GeneratedTimetable(
                plan=plan,
                timetable_id=timetable_id,
                sections=sections_list,
                batch_size=batch_size if students_remaining > 0 else 0,
                capacity_ceiling=capacity_ceiling,
                is_variant=variant_only,
            )
            plan_timetables.append(timetable)

            if batch_size > 0:
                for nbr in used_class_nbrs:
                    global_remaining_capacity[nbr] -= batch_size

                students_remaining -= batch_size
            timetable_id += 1

        all_timetables[plan] = plan_timetables

    # Calculate capacity usage (inverse of remaining)
    capacity_usage = {}
    for course_sections in sections_by_course.values():
        for s in course_sections:
            original = s.cap_enrl
            remaining = global_remaining_capacity.get(s.class_nbr, original)
            capacity_usage[s.class_nbr] = original - remaining

    return all_timetables, capacity_usage


def generate_bulk_timetables(
    packages: dict[str, list[str]],
    counts: dict[str, int],
    sections_by_course: dict[str, list[Section]],
    default_capacity: int = 40,
    output_dir: str = "exports/bulk_timetables",
    num_strategies: int = 25,
    min_batch_size: int | None = None,
    batch_randomness: bool = True,
    min_timetables_per_plan: int = 1,
    variant_retry_attempts: int = 3,
    compactness_weight: float = 0.0,
) -> dict[str, list[GeneratedTimetable]]:
    """
    Generate bulk timetables using MULTI-STRATEGY OPTIMIZATION.

    Tries multiple different strategies and picks the best result based on:
    - Total students assigned (higher is better)
    - Balance of section usage (lower variance is better)
    - Overfill penalty (less overfill is better)

    Additional randomness:
    - batch_randomness/min_batch_size: split cohorts into more timetables to promote mixing.

    Args:
        packages: {plan: [course_codes]}
        counts: {plan: student_count}
        sections_by_course: {course_code: [sections]}
        default_capacity: Default section capacity
        output_dir: Directory to write output files
        num_strategies: Number of different strategies to try
        min_timetables_per_plan: Target minimum variants per plan for mixing
        variant_retry_attempts: Attempts to find different section combos per plan
        compactness_weight: Optional weight to favor back-to-back classes; 0 disables

    Returns:
        {plan: [GeneratedTimetable]}
    """
    import random

    # Reset warning tracker for this run
    global _WARNED_COURSES
    _WARNED_COURSES = set()

    print("\n" + "=" * 70)
    print("🔄 MULTI-STRATEGY OPTIMIZATION")
    print("=" * 70)
    print("Testing strategies to find optimal allocation...\n")

    plans = list(packages.keys())
    best_result = None
    best_fitness = float("-inf")
    best_metrics = None
    all_results = []

    strategies = []

    # Precompute simple capacity stats to drive smarter plan orders
    plan_stats_cache: dict[str, tuple[int, int]] = {}

    def plan_capacity_stats(plan: str) -> tuple[int, int]:
        """Return (total_lec_capacity, bottleneck_lec_capacity) for the plan."""
        if plan in plan_stats_cache:
            return plan_stats_cache[plan]

        courses = packages.get(plan, [])
        lec_caps: list[int] = []
        for code in courses:
            matched = find_course_match(code, sections_by_course)
            if not matched:
                continue
            max_lec_cap = max(
                (
                    s.cap_enrl
                    for s in sections_by_course.get(matched, [])
                    if s.component == "LEC"
                ),
                default=0,
            )
            if max_lec_cap > 0:
                lec_caps.append(max_lec_cap)

        total_cap = sum(lec_caps)
        bottleneck_cap = min(lec_caps) if lec_caps else 0
        plan_stats_cache[plan] = (total_cap, bottleneck_cap)
        return plan_stats_cache[plan]

    def capacity_ratio(plan: str) -> float:
        total_cap, _ = plan_capacity_stats(plan)
        need = counts.get(plan, 0)
        if need <= 0:
            return float("inf")
        return total_cap / need

    def bottleneck(plan: str) -> int:
        _, bottleneck_cap = plan_capacity_stats(plan)
        return bottleneck_cap

    # Strategy 1: Original order
    strategies.append(("Original order", plans[:]))

    # Strategy 2: Smallest plans first (guarantee smaller plans get allocation)
    sorted_by_size_asc = sorted(plans, key=lambda p: counts.get(p, 0))
    strategies.append(("Smallest first", sorted_by_size_asc))

    # Strategy 3: Largest plans first
    sorted_by_size_desc = sorted(plans, key=lambda p: counts.get(p, 0), reverse=True)
    strategies.append(("Largest first", sorted_by_size_desc))

    # Strategy 4: Most constrained (most courses) first
    sorted_by_courses = sorted(
        plans, key=lambda p: len(packages.get(p, [])), reverse=True
    )
    strategies.append(("Most courses first", sorted_by_courses))

    # Strategy 5: Least constrained first
    sorted_by_courses_asc = sorted(plans, key=lambda p: len(packages.get(p, [])))
    strategies.append(("Least courses first", sorted_by_courses_asc))

    # Strategy 6: Interleaved (alternate small and large)
    small_first = sorted(plans, key=lambda p: counts.get(p, 0))
    large_first = sorted(plans, key=lambda p: counts.get(p, 0), reverse=True)
    interleaved = []
    for i in range(max(len(small_first), len(large_first))):
        if i < len(small_first) and small_first[i] not in interleaved:
            interleaved.append(small_first[i])
        if i < len(large_first) and large_first[i] not in interleaved:
            interleaved.append(large_first[i])
    strategies.append(("Interleaved", interleaved))

    # Strategy 7: Reverse original
    strategies.append(("Reverse order", plans[::-1]))

    # Strategy 8: Tight capacity ratio first (prioritize scarce plans)
    strategies.append(("Tight capacity first", sorted(plans, key=capacity_ratio)))

    # Strategy 9: Loose capacity ratio first (plans with headroom first)
    strategies.append(
        ("Loose capacity first", sorted(plans, key=capacity_ratio, reverse=True))
    )

    # Strategy 10: Bottleneck first (smallest LEC cap first)
    strategies.append(("Bottleneck first", sorted(plans, key=bottleneck)))

    # Strategy 11: Bottleneck last (largest LEC cap first)
    strategies.append(("Bottleneck last", sorted(plans, key=bottleneck, reverse=True)))

    # Remaining strategies: Random permutations with different seeds
    random.seed(42)  # Fixed seed for reproducibility
    base_strategy_count = len(strategies)
    random_strategy_count = max(0, num_strategies - base_strategy_count)
    for i in range(random_strategy_count):
        shuffled = plans[:]
        random.shuffle(shuffled)
        strategies.append((f"Random #{i + 1}", shuffled))

    # Reset random seed
    random.seed()

    print(
        f"Prepared {len(strategies)} strategies ({base_strategy_count} base + {random_strategy_count} random)"
    )
    print("-" * 70)

    # Test each strategy (show compact progress)
    strategy_results = []
    for i, (strategy_name, plan_order) in enumerate(strategies):
        # Show progress dots for random strategies
        if "Random" in strategy_name:
            if i == base_strategy_count:  # First random
                print(
                    f"  Testing {random_strategy_count} random strategies",
                    end="",
                    flush=True,
                )
            print(".", end="", flush=True)
        else:
            print(f"  {strategy_name:<22}", end="", flush=True)

        timetables, capacity_usage = generate_bulk_timetables_single_run(
            packages,
            counts,
            sections_by_course,
            plan_order,
            default_capacity,
            verbose=False,
            min_batch_size=min_batch_size,
            batch_randomness=batch_randomness,
            min_timetables_per_plan=min_timetables_per_plan,
            variant_retry_attempts=variant_retry_attempts,
            compactness_weight=compactness_weight,
        )

        fitness, metrics = calculate_fitness(
            timetables, counts, capacity_usage, sections_by_course
        )
        all_results.append(
            (strategy_name, fitness, metrics, timetables, capacity_usage)
        )

        if math.isnan(fitness):
            if "Random" not in strategy_name:
                print(" -> NaN (skipped)")
            continue

        assigned = metrics["total_assigned"]
        needed = metrics["total_needed"]
        pct = assigned / needed * 100 if needed > 0 else 0

        strategy_results.append((strategy_name, assigned, needed, pct, fitness))

        # Only show details for non-random strategies
        if "Random" not in strategy_name:
            print(f" {assigned:>3}/{needed:<3} ({pct:>5.1f}%)")

        if best_metrics is None or fitness > best_fitness:
            best_fitness = fitness
            best_result = timetables
            best_metrics = metrics
            best_strategy = strategy_name

    # End random strategies line
    if random_strategy_count > 0:
        print()  # newline after dots

    if best_metrics is None:
        raise ValueError(
            "No valid strategy produced a fitness score. "
            "Check input counts/capacities for NaN or invalid values."
        )

    assert best_metrics is not None
    assert best_result is not None

    # Report results
    print("\n" + "-" * 70)
    print(f"✅ BEST STRATEGY: {best_strategy}")
    print(
        f"   Assigned: {best_metrics['total_assigned']}/{best_metrics['total_needed']} ({best_metrics['assignment_ratio'] * 100:.1f}%)"
    )
    print("-" * 70)

    # Setup output
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    incremental_file = output_path / "timetables_incremental.csv"

    # Write best result to CSV
    with open(incremental_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Plan",
                "Timetable ID",
                "Batch Size",
                "Course Code",
                "Component",
                "Section",
                "Class Nbr",
                "Day",
                "Start",
                "End",
                "Room",
                "Instructor",
            ]
        )

    for plan, plan_timetables in best_result.items():
        for timetable in plan_timetables:
            write_timetable_to_csv(timetable, incremental_file, write_header=False)

    return best_result


# ==================== Output ====================


def export_timetables_to_csv(
    timetables: dict[str, list[GeneratedTimetable]],
    output_dir: str = "exports",
):
    """Export timetables to CSV files."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Summary file
    summary_file = output_path / "timetables_summary.csv"
    with open(summary_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Plan",
                "Timetable ID",
                "Batch Size",
                "Capacity Ceiling",
                "Variant",
                "Course Code",
                "Component",
                "Section",
                "Class Nbr",
                "Day",
                "Start",
                "End",
                "Room",
                "Instructor",
                "Capacity",
                "Enrolled",
            ]
        )

        for plan, plan_timetables in timetables.items():
            for tt in plan_timetables:
                for section in tt.sections:
                    for meeting in section.meetings:
                        writer.writerow(
                            [
                                plan,
                                tt.timetable_id,
                                tt.batch_size,
                                tt.capacity_ceiling,
                                "yes" if tt.is_variant else "no",
                                section.course_code,
                                section.component,
                                section.section,
                                section.class_nbr,
                                meeting.get("day", ""),
                                meeting.get("start", ""),
                                meeting.get("end", ""),
                                section.room or "",
                                section.instructor or "",
                                section.cap_enrl,
                                section.tot_enrl,
                            ]
                        )

    print(f"\nExported summary to: {summary_file}")

    # Class numbers file (for registration)
    classnbr_file = output_path / "timetables_classnbrs.csv"
    with open(classnbr_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Plan", "Timetable ID", "Class Nbr", "Course Code", "Section"])

        for plan, plan_timetables in timetables.items():
            for tt in plan_timetables:
                seen = set()
                for section in tt.sections:
                    if section.class_nbr not in seen:
                        seen.add(section.class_nbr)
                        writer.writerow(
                            [
                                plan,
                                tt.timetable_id,
                                section.class_nbr,
                                section.course_code,
                                section.section,
                            ]
                        )

    print(f"Exported class numbers to: {classnbr_file}")

    # Per-plan files
    for plan, plan_timetables in timetables.items():
        safe_plan = (
            plan.replace(",", "_").replace(" ", "").replace(":", "_").replace("+", "_")
        )
        plan_file = output_path / f"timetable_{safe_plan}.csv"
        with open(plan_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "Timetable ID",
                    "Batch Size",
                    "Capacity Ceiling",
                    "Variant",
                    "Class Nbr",
                    "Course Code",
                    "Component",
                    "Section",
                    "Day",
                    "Start",
                    "End",
                ]
            )

            for tt in plan_timetables:
                for section in tt.sections:
                    for meeting in section.meetings:
                        writer.writerow(
                            [
                                tt.timetable_id,
                                tt.batch_size,
                                tt.capacity_ceiling,
                                "yes" if tt.is_variant else "no",
                                section.class_nbr,
                                section.course_code,
                                section.component,
                                section.section,
                                meeting.get("day", ""),
                                meeting.get("start", ""),
                                meeting.get("end", ""),
                            ]
                        )

        print(f"Exported {plan} to: {plan_file}")


def print_summary(
    timetables: dict[str, list[GeneratedTimetable]],
    membership: dict[str, list[str]] | None = None,
    counts: dict[str, int] | None = None,
):
    """Print a compact summary of generated timetables."""
    print("\n" + "=" * 70)
    print("📋 GENERATION SUMMARY")
    print("=" * 70)

    total_timetables = 0
    total_students = 0
    total_needed = sum(counts.values()) if counts else 0

    summary_rows = []

    for plan, plan_timetables in timetables.items():
        plan_students = sum(tt.batch_size for tt in plan_timetables)
        needed = counts.get(plan, plan_students) if counts else plan_students

        status = "✅" if plan_students >= needed else "⚠️"
        summary_rows.append((status, plan, plan_students, needed, len(plan_timetables)))

        total_timetables += len(plan_timetables)
        total_students += plan_students

    # Print table header
    print(f"\n  {'Plan':<45} {'Assigned':>10} {'Timetables':>12}")
    print("  " + "-" * 67)

    for status, plan, students, needed, tt_count in summary_rows:
        plan_display = plan[:42] + "..." if len(plan) > 45 else plan
        assigned_str = f"{students}/{needed}"
        print(f"  {status} {plan_display:<43} {assigned_str:>10} {tt_count:>10}")

    print("  " + "-" * 67)
    pct = (total_students / total_needed * 100) if total_needed > 0 else 100
    status = "✅" if total_students >= total_needed else "⚠️"
    print(
        f"  {status} {'TOTAL':<43} {total_students:>4}/{total_needed:<5} {total_timetables:>10}"
    )
    print(f"\n  Allocation Rate: {pct:.1f}%")


# ==================== PDF Generation ====================

# Time slots for the grid (8 AM to 6 PM in hourly slots)
TIME_SLOTS = [
    ("08:00", "09:00"),
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:00", "12:00"),
    ("12:00", "13:00"),
    ("13:00", "14:00"),
    ("14:00", "15:00"),
    ("15:00", "16:00"),
    ("16:00", "17:00"),
    ("17:00", "18:00"),
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# Color palette for courses
COURSE_COLORS = [
    colors.Color(0.85, 0.92, 0.98),  # Light blue
    colors.Color(0.85, 0.98, 0.85),  # Light green
    colors.Color(0.98, 0.92, 0.85),  # Light orange
    colors.Color(0.92, 0.85, 0.98),  # Light purple
    colors.Color(0.98, 0.85, 0.92),  # Light pink
    colors.Color(0.98, 0.98, 0.85),  # Light yellow
    colors.Color(0.85, 0.98, 0.98),  # Light cyan
    colors.Color(0.95, 0.90, 0.85),  # Light brown
]


def time_to_slot_index(time_str: str) -> int:
    """Convert time string to slot index."""
    try:
        hour = int(time_str.split(":")[0])
        if hour < 8:
            return -1
        if hour >= 18:
            return -1
        return hour - 8
    except:
        return -1


def create_timetable_grid(timetable: GeneratedTimetable) -> list[list[str]]:
    """
    Create a 2D grid representation of a timetable.
    Returns grid[day_index][slot_index] = cell content
    """
    # Initialize empty grid (6 days x 10 slots)
    grid = [["" for _ in range(len(TIME_SLOTS))] for _ in range(len(DAYS))]

    # Assign colors to courses
    course_codes = list(set(s.course_code for s in timetable.sections))
    {code: COURSE_COLORS[i % len(COURSE_COLORS)] for i, code in enumerate(course_codes)}

    # Fill grid with section info
    for section in timetable.sections:
        for meeting in section.meetings:
            day = meeting.get("day", "")
            start = meeting.get("start", "")
            end = meeting.get("end", "")

            if day not in DAYS:
                continue

            day_idx = DAYS.index(day)
            start_slot = time_to_slot_index(start)
            end_slot = time_to_slot_index(end)

            if start_slot < 0:
                continue

            # Create cell content
            content = f"{section.course_code}\n{section.component}-{section.section}"
            if section.room:
                content += f"\n{section.room}"

            # Fill slots
            for slot_idx in range(start_slot, max(start_slot + 1, end_slot)):
                if 0 <= slot_idx < len(TIME_SLOTS):
                    if grid[day_idx][slot_idx]:
                        grid[day_idx][slot_idx] += "\n---\n" + content
                    else:
                        grid[day_idx][slot_idx] = content

    return grid


# ==================== Verification ====================


def verify_timetables(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
    packages: dict[str, list[str]],
) -> dict:
    """
    Verify generated timetables for correctness.

    Checks:
    1. No time conflicts within each timetable
    2. No exam conflicts within each timetable
    3. All required courses are present (based on packages)
    4. All required components present for each course
    5. Capacity not exceeded for strict components (LAB/PRO)

    Returns dict with verification results and any issues found.
    """
    issues: list[str] = []
    warnings: list[str] = []
    stats = {
        "total_timetables": 0,
        "timetables_with_issues": 0,
        "time_conflicts": 0,
        "exam_conflicts": 0,
        "missing_courses": 0,
        "missing_components": 0,
        "capacity_violations": 0,
    }

    # Build capacity usage map
    capacity_usage: dict[int, int] = defaultdict(int)
    for plan_tts in timetables.values():
        for tt in plan_tts:
            seen_in_tt = set()
            for section in tt.sections:
                if section.class_nbr not in seen_in_tt:
                    seen_in_tt.add(section.class_nbr)
                    capacity_usage[section.class_nbr] += tt.batch_size

    # Build section lookup by class_nbr
    section_lookup: dict[int, Section] = {}
    for sections in sections_by_course.values():
        for s in sections:
            section_lookup[s.class_nbr] = s

    # Get required components per course
    def get_required_components(course_code: str) -> set[str]:
        """Get required components for a course based on available sections."""
        sections = sections_by_course.get(course_code, [])
        return {s.component for s in sections}

    for plan, plan_tts in timetables.items():
        required_courses = set(packages.get(plan, []))

        for tt in plan_tts:
            stats["total_timetables"] += 1
            tt_issues = []
            tt_id = f"{plan}/TT{tt.timetable_id}"

            # Check 1: Time conflicts within timetable
            sections_list = tt.sections
            for i in range(len(sections_list)):
                for j in range(i + 1, len(sections_list)):
                    if sections_clash(sections_list[i], sections_list[j]):
                        tt_issues.append(
                            f"Time conflict: {sections_list[i].course_code} {sections_list[i].section} "
                            f"vs {sections_list[j].course_code} {sections_list[j].section}"
                        )
                        stats["time_conflicts"] += 1

            # Check 2: Exam conflicts (same exam date)
            exam_dates: dict[str, list[str]] = defaultdict(list)
            for s in sections_list:
                if s.exam_date:
                    exam_dates[s.exam_date].append(f"{s.course_code} {s.section}")
            for exam_date, courses in exam_dates.items():
                if len(courses) > 1:
                    # This might be okay for different components of same course
                    unique_courses = set(c.split()[0] for c in courses)
                    if len(unique_courses) > 1:
                        tt_issues.append(
                            f"Exam conflict on {exam_date}: {', '.join(courses)}"
                        )
                        stats["exam_conflicts"] += 1

            # Check 3: All required courses present
            tt_courses = {s.course_code for s in sections_list}
            missing = required_courses - tt_courses
            if missing:
                tt_issues.append(f"Missing courses: {', '.join(sorted(missing))}")
                stats["missing_courses"] += len(missing)

            # Check 4: All required components present for each course
            tt_course_components: dict[str, set[str]] = defaultdict(set)
            for s in sections_list:
                tt_course_components[s.course_code].add(s.component)

            for course_code in tt_courses:
                required_comps = get_required_components(course_code)
                present_comps = tt_course_components[course_code]
                missing_comps = required_comps - present_comps
                if missing_comps:
                    tt_issues.append(
                        f"{course_code}: Missing components {', '.join(sorted(missing_comps))}"
                    )
                    stats["missing_components"] += len(missing_comps)

            if tt_issues:
                stats["timetables_with_issues"] += 1
                issues.append(f"\n{tt_id}:")
                issues.extend(f"  - {issue}" for issue in tt_issues)

    # Check 5: Capacity violations for strict components (LAB/PRO)
    for class_nbr, used in capacity_usage.items():
        section = section_lookup.get(class_nbr)
        if section and section.component in {"LAB", "PRO"}:
            total_enrolled = section.tot_enrl + used
            if total_enrolled > section.cap_enrl:
                overfill = total_enrolled - section.cap_enrl
                warnings.append(
                    f"Capacity exceeded: {section.course_code} {section.section} "
                    f"({section.component}): {total_enrolled}/{section.cap_enrl} (+{overfill})"
                )
                stats["capacity_violations"] += 1

    return {
        "valid": stats["timetables_with_issues"] == 0
        and stats["capacity_violations"] == 0,
        "stats": stats,
        "issues": issues,
        "warnings": warnings,
    }


def print_verification_report(verification: dict) -> None:
    """Print verification report to console."""
    stats = verification["stats"]

    if verification["valid"]:
        print(
            f"\n✅ Verification passed ({stats['total_timetables']} timetables, no conflicts)"
        )
    else:
        print("\n" + "=" * 70)
        print("⚠️  VERIFICATION ISSUES")
        print("=" * 70)

        issues_summary = []
        if stats["time_conflicts"] > 0:
            issues_summary.append(f"{stats['time_conflicts']} time conflicts")
        if stats["exam_conflicts"] > 0:
            issues_summary.append(f"{stats['exam_conflicts']} exam conflicts")
        if stats["missing_courses"] > 0:
            issues_summary.append(f"{stats['missing_courses']} missing courses")
        if stats["capacity_violations"] > 0:
            issues_summary.append(f"{stats['capacity_violations']} capacity violations")

        print(f"\n  Issues: {', '.join(issues_summary)}")

        if verification["issues"]:
            print("\n  Details:")
            for issue in verification["issues"][:10]:
                print(f"    {issue}")
            if len(verification["issues"]) > 10:
                print(f"    ... and {len(verification['issues']) - 10} more")

        print("=" * 70)


def report_capacity_deficits(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
) -> dict[int, dict]:
    """
    Report sections that have been assigned beyond their capacity (negative remaining).

    Returns a dict of {class_nbr: {section_info, deficit, needed_increase}}.
    """
    # Build capacity usage map
    capacity_usage: dict[int, int] = defaultdict(int)
    section_lookup: dict[int, Section] = {}

    for sections in sections_by_course.values():
        for section in sections:
            section_lookup[section.class_nbr] = section

    for plan_tts in timetables.values():
        for tt in plan_tts:
            if tt.batch_size > 0:
                for section in tt.sections:
                    capacity_usage[section.class_nbr] += tt.batch_size

    # Find sections with deficit (usage > capacity)
    deficits: dict[int, dict] = {}

    for class_nbr, used in capacity_usage.items():
        section = section_lookup.get(class_nbr)
        if section is None:
            continue

        available = section.cap_enrl - section.tot_enrl  # Original available
        if used > available:
            deficit = used - available
            deficits[class_nbr] = {
                "section": section,
                "course_code": section.course_code,
                "component": section.component,
                "section_id": section.section,
                "capacity": section.cap_enrl,
                "already_enrolled": section.tot_enrl,
                "available": available,
                "assigned": used,
                "deficit": deficit,
                "needed_capacity": section.cap_enrl
                + deficit,  # New total capacity needed
            }

    return deficits


def print_capacity_deficit_report(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
) -> None:
    """Print a report of sections needing capacity increases."""
    deficits = report_capacity_deficits(timetables, sections_by_course)

    if not deficits:
        return  # No deficits, nothing to report

    print("\n" + "=" * 70)
    print("📈 CAPACITY DEFICIT REPORT")
    print("=" * 70)

    # Group by course and component
    by_course: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for info in deficits.values():
        by_course[info["course_code"]][info["component"]].append(info)

    total_deficit = 0

    for course_code in sorted(by_course.keys()):
        for component in sorted(by_course[course_code].keys()):
            sections_list = by_course[course_code][component]
            component_deficit = sum(s["deficit"] for s in sections_list)
            total_deficit += component_deficit

            # Compact per-section info
            details = []
            for info in sorted(sections_list, key=lambda x: -x["deficit"])[:3]:
                section = info["section"]
                time_str = ""
                if section.meetings:
                    m = section.meetings[0]
                    time_str = f"{m.get('day', '')} {m.get('start', '')}"
                details.append(f"{info['section_id']}({time_str}):+{info['deficit']}")

            print(
                f"\n  {course_code} [{component}]: +{component_deficit} total across {len(sections_list)} section(s)"
            )
            print(f"     {', '.join(details)}")

    print("\n  " + "-" * 66)
    print(f"  Total: {len(deficits)} sections need +{total_deficit} seats")
    print("=" * 70)


def diagnose_allocation_failures(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
    counts: dict[str, int],
    packages: dict[str, list[str]],
) -> None:
    """
    Diagnose why some plans couldn't allocate all students.
    Identifies bottleneck courses and specific constraints.
    """
    # Find plans with incomplete allocation
    failed_plans = []
    for plan, needed in counts.items():
        if needed == 0:
            continue
        plan_tts = timetables.get(plan, [])
        assigned = sum(tt.batch_size for tt in plan_tts)
        if assigned < needed:
            failed_plans.append((plan, assigned, needed, needed - assigned))

    if not failed_plans:
        return  # All good, nothing to diagnose

    print("\n" + "=" * 70)
    print("🔍 ALLOCATION FAILURE DIAGNOSIS")
    print("=" * 70)

    # Build capacity usage map
    capacity_usage: dict[int, int] = defaultdict(int)
    for plan_tts in timetables.values():
        for tt in plan_tts:
            if tt.batch_size > 0:
                for section in tt.sections:
                    capacity_usage[section.class_nbr] += tt.batch_size

    for plan, assigned, needed, shortfall in failed_plans:
        print(f"\n  ❌ {plan}: {assigned}/{needed} ({shortfall} unassigned)")

        # Get courses for this plan
        plan_courses = packages.get(plan, [])
        if not plan_courses:
            for pkg_plan, courses in packages.items():
                if plan in pkg_plan or pkg_plan in plan:
                    plan_courses = courses
                    break

        if not plan_courses:
            print("     Could not determine courses for this plan")
            continue

        # Analyze each course's capacity situation
        bottlenecks = []

        for course_code in plan_courses:
            matched_code = find_course_match(course_code, sections_by_course)

            if not matched_code:
                bottlenecks.append(
                    (course_code, "NOT_FOUND", 0, 0, "Course not in timetable")
                )
                continue

            sections = sections_by_course[matched_code]

            by_component: dict[str, list[Section]] = defaultdict(list)
            for s in sections:
                by_component[s.component].append(s)

            for component, comp_sections in by_component.items():
                if component == "LEC":
                    continue

                total_capacity = sum(s.cap_enrl - s.tot_enrl for s in comp_sections)
                total_used = sum(
                    capacity_usage.get(s.class_nbr, 0) for s in comp_sections
                )
                max_overfill = get_max_overfill(matched_code, component)
                max_with_overfill = total_capacity + len(comp_sections) * max_overfill

                remaining = max_with_overfill - total_used

                if remaining < shortfall:
                    at_limit = []
                    has_capacity = []

                    for s in comp_sections:
                        used = capacity_usage.get(s.class_nbr, 0)
                        available = s.cap_enrl - s.tot_enrl
                        section_remaining = available + max_overfill - used

                        time_str = "?"
                        if s.meetings:
                            time_str = f"{s.meetings[0].get('day', '')} {s.meetings[0].get('start', '')}"

                        if section_remaining <= 0:
                            at_limit.append((s.section, time_str, used, available))
                        else:
                            has_capacity.append(
                                (s.section, time_str, section_remaining)
                            )

                    bottlenecks.append(
                        (
                            matched_code,
                            component,
                            remaining,
                            shortfall,
                            {
                                "total_capacity": total_capacity,
                                "total_used": total_used,
                                "max_overfill": max_overfill,
                                "at_limit": at_limit,
                                "has_capacity": has_capacity,
                            },
                        )
                    )

        if bottlenecks:
            print("\n     🚧 Bottleneck Analysis:")
            bottlenecks.sort(key=lambda x: x[2])

            for course, component, remaining, shortfall, details in bottlenecks[:3]:
                if component == "NOT_FOUND":
                    print(f"        ❌ {course}: Not found in timetable")
                    continue

                print(
                    f"        • {course} [{component}]: {details['total_used']}/{details['total_capacity']} used"
                )
                if details["has_capacity"]:
                    avail = ", ".join(
                        f"{s[0]}({s[1]})" for s in details["has_capacity"][:2]
                    )
                    print(f"          Available but schedule conflict: {avail}")
        else:
            # Schedule conflicts - show exact sections
            print("\n     ⏰ Schedule Conflict (all courses have capacity)")
            print("\n     Conflicting sections by time slot:")

            # Day abbreviation map for display
            day_display = {
                "M": "Mon",
                "T": "Tue",
                "W": "Wed",
                "TH": "Thu",
                "F": "Fri",
                "S": "Sat",
            }

            # Collect sections by time slot with full details
            time_slots: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
            for course_code in plan_courses:
                matched_code = find_course_match(course_code, sections_by_course)
                if not matched_code:
                    continue
                for s in sections_by_course[matched_code]:
                    if s.component in ("LAB", "TUT"):
                        available = (
                            s.cap_enrl - s.tot_enrl - capacity_usage.get(s.class_nbr, 0)
                        )
                        for m in s.meetings:
                            day = m.get("day", "?")
                            start = m.get("start", "?")
                            # Normalize time format
                            if start and ":" not in str(start):
                                try:
                                    h = int(float(start))
                                    start = f"{h:02d}:00"
                                except (ValueError, TypeError):
                                    pass
                            time_key = f"{day_display.get(day, day)} {start}"
                            # Store: (course, section, component, available)
                            time_slots[time_key].append(
                                (matched_code, s.section, s.component, available)
                            )

            # Find slots with multiple courses (potential conflicts)
            crowded = [
                (t, secs)
                for t, secs in time_slots.items()
                if len(set(s[0] for s in secs)) > 1  # More than one course
            ]
            if crowded:
                # Sort by number of different courses at that slot
                crowded.sort(key=lambda x: -len(set(s[0] for s in x[1])))

                for time_key, sections_at_time in crowded[
                    :5
                ]:  # Show top 5 conflict slots
                    # Group by course
                    by_course: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
                    for course, sec, comp, avail in sections_at_time:
                        by_course[course].append((sec, comp, avail))

                    courses_involved = sorted(by_course.keys())
                    print(f"\n        📍 {time_key} ({len(courses_involved)} courses)")

                    for course in courses_involved:
                        secs = by_course[course]
                        # Show sections with availability
                        sec_strs = []
                        for sec, comp, avail in sorted(secs, key=lambda x: x[0]):
                            avail_str = f"+{avail}" if avail > 0 else str(avail)
                            sec_strs.append(f"{sec}[{avail_str}]")
                        print(f"           {course}: {', '.join(sec_strs[:6])}")

    print("\n" + "=" * 70)


def generate_capacity_insights(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
    counts: dict[str, int],
    packages: dict[str, list[str]],
) -> None:
    """
    Generate detailed, actionable insights about capacity issues and recommendations.
    Only shows courses that are in the packages (relevant courses).
    """
    print("\n" + "=" * 70)
    print("📊 CAPACITY INSIGHTS & RECOMMENDATIONS")
    print("=" * 70)

    # Get all courses from packages (only show insights for these)
    package_courses: set[str] = set()
    for courses in packages.values():
        package_courses.update(courses)

    # Map package course codes to actual course codes in sections_by_course
    relevant_courses: set[str] = set()
    for pkg_course in package_courses:
        matched = find_course_match(pkg_course, sections_by_course)
        if matched:
            relevant_courses.add(matched)

    # Calculate total assigned vs needed
    total_assigned = sum(
        sum(tt.batch_size for tt in plan_tts) for plan_tts in timetables.values()
    )
    total_needed = sum(counts.values())
    unassigned = total_needed - total_assigned

    if unassigned <= 0:
        print("\n✅ All students successfully assigned!")
        print("=" * 70)
        return

    print(
        f"\n⚠️  {unassigned} students could not be assigned ({total_assigned}/{total_needed})"
    )

    # Build capacity usage map
    capacity_usage: dict[int, int] = defaultdict(int)

    for plan_tts in timetables.values():
        for tt in plan_tts:
            if tt.batch_size > 0:
                for section in tt.sections:
                    capacity_usage[section.class_nbr] += tt.batch_size

    # Analyze LAB sections - only for relevant courses
    lab_insights = []
    lab_analysis = []

    for course_code in sorted(relevant_courses):
        sections = sections_by_course.get(course_code, [])
        labs = [s for s in sections if s.component == "LAB"]
        if not labs:
            continue

        total_lab_capacity = sum(s.cap_enrl - s.tot_enrl for s in labs)
        total_lab_used = sum(capacity_usage.get(s.class_nbr, 0) for s in labs)

        # Only show if there's usage or capacity issue
        if total_lab_used == 0 and total_lab_capacity > 0:
            continue  # Skip unused labs

        by_time: dict[str, list[Section]] = defaultdict(list)
        for lab in labs:
            if lab.meetings:
                day = lab.meetings[0].get("day", "Unknown")
                start = lab.meetings[0].get("start", "Unknown")
                time_key = f"{day} {start}"
                by_time[time_key].append(lab)

        underutilized = []
        overutilized = []

        for time_key, time_labs in by_time.items():
            for lab in time_labs:
                used = capacity_usage.get(lab.class_nbr, 0)
                available = lab.cap_enrl - lab.tot_enrl
                remaining = available - used
                max_overfill = get_max_overfill(lab.course_code, "LAB")

                if (
                    remaining > lab.cap_enrl * 0.5 and used > 0
                ):  # More than 50% unused but some usage
                    underutilized.append((lab, time_key, remaining))
                elif remaining < -max_overfill:
                    overutilized.append((lab, time_key, -remaining))

        if overutilized or total_lab_used > total_lab_capacity:
            max_overfill = get_max_overfill(course_code, "LAB")

            entry = {
                "course": course_code,
                "capacity": total_lab_capacity,
                "used": total_lab_used,
                "max_overfill": max_overfill,
                "overutilized": overutilized,
                "underutilized": underutilized if underutilized else [],
            }

            existing_times = set(by_time.keys())
            suggested_times = []
            saturday_exists = any(
                "S " in t or t.startswith("S ") for t in existing_times
            )
            if not saturday_exists:
                suggested_times.append("Saturday 08:00")
            for day in ["M", "T", "W", "TH", "F"]:
                early_slot = f"{day} 08:00"
                if early_slot not in existing_times:
                    suggested_times.append(early_slot)
                    break
            entry["suggested_times"] = suggested_times

            lab_analysis.append(entry)
            lab_insights.append(
                {
                    "course": course_code,
                    "deficit": max(0, total_lab_used - total_lab_capacity),
                    "suggested_times": suggested_times,
                }
            )

    # Print LAB analysis if any
    if lab_analysis:
        print("\n" + "-" * 70)
        print("🔬 LAB SECTION ANALYSIS (Package Courses Only)")
        print("-" * 70)

        for entry in lab_analysis:
            print(f"\n  📚 {entry['course']}")
            print(
                f"     Capacity: {entry['capacity']} | Used: {entry['used']} | Max overfill: +{entry['max_overfill']}/section"
            )

            if entry["overutilized"]:
                print("     ❌ Overutilized:")
                for lab, time_key, excess in sorted(
                    entry["overutilized"], key=lambda x: -x[2]
                )[:3]:
                    print(f"        • {lab.section} ({time_key}): +{excess} over limit")

            if entry["underutilized"]:
                print("     💡 Underutilized (schedule conflicts?):")
                for lab, time_key, remaining in sorted(
                    entry["underutilized"], key=lambda x: -x[2]
                )[:2]:
                    print(f"        • {lab.section} ({time_key}): {remaining} unused")

            if entry["suggested_times"] and entry["used"] > entry["capacity"]:
                print(
                    f"     🆕 Suggested times: {', '.join(entry['suggested_times'][:2])}"
                )

    # Analyze TUT sections - only for relevant courses
    tut_analysis = []

    for course_code in sorted(relevant_courses):
        sections = sections_by_course.get(course_code, [])
        tuts = [s for s in sections if s.component == "TUT"]
        if not tuts:
            continue

        total_tut_capacity = sum(s.cap_enrl - s.tot_enrl for s in tuts)
        total_tut_used = sum(capacity_usage.get(s.class_nbr, 0) for s in tuts)

        if total_tut_used == 0:
            continue  # Skip unused TUTs

        max_overfill = get_max_overfill(course_code, "TUT")

        if total_tut_used > total_tut_capacity:
            deficit = total_tut_used - total_tut_capacity
            max_with_overfill = total_tut_capacity + len(tuts) * max_overfill

            tut_analysis.append(
                {
                    "course": course_code,
                    "capacity": total_tut_capacity,
                    "used": total_tut_used,
                    "deficit": deficit,
                    "max_with_overfill": max_with_overfill,
                    "num_sections": len(tuts),
                    "max_overfill": max_overfill,
                    "needs_new_sections": total_tut_used > max_with_overfill,
                }
            )

    # Print TUT analysis if any
    if tut_analysis:
        print("\n" + "-" * 70)
        print("📝 TUT SECTION ANALYSIS (Package Courses Only)")
        print("-" * 70)

        for entry in tut_analysis:
            status = "⚠️" if entry["needs_new_sections"] else "📊"
            print(f"\n  {status} {entry['course']}")
            print(
                f"     Capacity: {entry['capacity']} | Used: {entry['used']} | Deficit: +{entry['deficit']}"
            )
            print(
                f"     Max with +{entry['max_overfill']}/section: {entry['max_with_overfill']} ({entry['num_sections']} sections)"
            )

            if entry["needs_new_sections"]:
                sections_needed = (entry["used"] - entry["max_with_overfill"]) // 30 + 1
                print(f"     🆕 Need ~{sections_needed} new TUT section(s)")

    # Summary recommendations
    print("\n" + "-" * 70)
    print("📋 SUMMARY")
    print("-" * 70)

    recommendations = []

    for insight in lab_insights:
        if insight["deficit"] > 0:
            recommendations.append(
                f"Add {insight['course']} LAB at {insight['suggested_times'][0] if insight['suggested_times'] else 'new time'}"
            )

    for entry in tut_analysis:
        if entry["needs_new_sections"]:
            sections_needed = (entry["used"] - entry["max_with_overfill"]) // 30 + 1
            recommendations.append(
                f"Add {sections_needed} new {entry['course']} TUT section(s)"
            )

    if recommendations:
        print("\n  🔧 Priority Actions:")
        for i, rec in enumerate(recommendations[:5], 1):
            print(f"     {i}. {rec}")
    else:
        print("\n  ✅ No critical capacity issues (within overfill limits)")

    print("\n" + "=" * 70)


def print_detailed_section_report(
    timetables: dict[str, list[GeneratedTimetable]],
    sections_by_course: dict[str, list[Section]],
    packages: dict[str, list[str]],
) -> None:
    """
    Print detailed section-level report showing capacities before and after allocation.
    Shows exact section numbers, times, and capacity utilization.
    """
    print("\n" + "=" * 70)
    print("📊 DETAILED SECTION ALLOCATION REPORT")
    print("=" * 70)

    # Get all courses from packages
    package_courses: set[str] = set()
    for courses in packages.values():
        package_courses.update(courses)

    # Map package course codes to actual course codes
    relevant_courses: dict[str, str] = {}
    for pkg_course in package_courses:
        matched = find_course_match(pkg_course, sections_by_course)
        if matched:
            relevant_courses[pkg_course] = matched

    # Build capacity usage map from all timetables
    capacity_usage: dict[int, int] = defaultdict(int)
    for plan_tts in timetables.values():
        for tt in plan_tts:
            if tt.batch_size > 0:
                for section in tt.sections:
                    capacity_usage[section.class_nbr] += tt.batch_size

    # Day abbreviation map
    day_map = {
        "M": "Mon",
        "T": "Tue",
        "W": "Wed",
        "TH": "Thu",
        "F": "Fri",
        "S": "Sat",
    }

    def format_time(time_str: str) -> str:
        """Format time string to HH:MM format"""
        if not time_str:
            return "?"
        time_str = str(time_str).strip()
        if ":" in time_str:
            return time_str[:5]
        try:
            # Handle decimal time like 8.0 or 13.5
            hours = float(time_str)
            h = int(hours)
            m = int((hours - h) * 60)
            return f"{h:02d}:{m:02d}"
        except (ValueError, TypeError):
            return time_str[:5] if len(time_str) >= 5 else time_str

    def get_schedule(section: Section) -> str:
        """Get schedule string for a section"""
        if not section.meetings:
            return "No schedule"
        schedules = []
        for m in section.meetings:
            day = m.get("day", "?")
            day_str = day_map.get(day, day)
            start = format_time(m.get("start", ""))
            schedules.append(f"{day_str} {start}")
        return ", ".join(schedules)

    # Process each course
    for pkg_course in sorted(relevant_courses.keys()):
        course_code = relevant_courses[pkg_course]
        sections = sections_by_course.get(course_code, [])
        if not sections:
            continue

        # Group by component
        by_component: dict[str, list[Section]] = defaultdict(list)
        for s in sections:
            by_component[s.component].append(s)

        # Check if course has any allocation
        course_has_usage = any(capacity_usage.get(s.class_nbr, 0) > 0 for s in sections)
        if not course_has_usage:
            continue  # Skip courses with no allocation

        print(f"\n{'─' * 70}")
        print(f"📚 {course_code}")
        print(f"{'─' * 70}")

        for component in ["LEC", "TUT", "LAB", "PRO", "PRA"]:
            comp_sections = by_component.get(component, [])
            if not comp_sections:
                continue

            # Sort by section number
            comp_sections.sort(key=lambda s: s.section)

            # Calculate totals
            total_excel_cap = sum(s.cap_enrl for s in comp_sections)
            total_pre_enrolled = sum(s.tot_enrl for s in comp_sections)
            total_available_before = total_excel_cap - total_pre_enrolled
            total_allocated = sum(
                capacity_usage.get(s.class_nbr, 0) for s in comp_sections
            )
            total_available_after = total_available_before - total_allocated
            max_overfill = get_max_overfill(course_code, component)

            # Check for capacity override
            override = get_section_capacity_override(course_code, component)
            override_str = f" (Override: {override})" if override else ""

            print(f"\n  [{component}] {len(comp_sections)} sections{override_str}")
            print(
                f"  {'Section':<8} {'Schedule':<18} {'Excel':>6} {'Pre':>5} {'Avail':>6} {'Alloc':>6} {'After':>6} {'Status':<10}"
            )
            print(
                f"  {'-' * 8} {'-' * 18} {'-' * 6} {'-' * 5} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 10}"
            )

            for s in comp_sections:
                allocated = capacity_usage.get(s.class_nbr, 0)
                excel_cap = s.cap_enrl
                pre_enrolled = s.tot_enrl

                # Get effective capacity (after override)
                if override:
                    effective_cap = override
                else:
                    effective_cap = excel_cap

                avail_before = effective_cap - pre_enrolled
                avail_after = avail_before - allocated

                # Determine status
                if avail_after > 0:
                    status = "✅ OK"
                elif avail_after == 0:
                    status = "🟡 FULL"
                elif avail_after >= -max_overfill:
                    status = f"🟠 +{-avail_after}"
                else:
                    status = f"🔴 +{-avail_after}"

                schedule = get_schedule(s)
                if len(schedule) > 18:
                    schedule = schedule[:15] + "..."

                # Show effective capacity if different from excel
                cap_display = str(effective_cap)
                if override and override != excel_cap:
                    cap_display = f"{effective_cap}*"

                print(
                    f"  {s.section:<8} {schedule:<18} {cap_display:>6} {pre_enrolled:>5} {avail_before:>6} {allocated:>6} {avail_after:>6} {status:<10}"
                )

            # Print component summary
            effective_total = (
                sum(override if override else s.cap_enrl for s in comp_sections)
                - total_pre_enrolled
            )
            print(
                f"  {'-' * 8} {'-' * 18} {'-' * 6} {'-' * 5} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 10}"
            )

            summary_status = "✅" if total_available_after >= 0 else "⚠️"
            print(
                f"  {'TOTAL':<8} {'':<18} {total_excel_cap:>6} {total_pre_enrolled:>5} {effective_total:>6} {total_allocated:>6} {effective_total - total_allocated:>6} {summary_status}"
            )

    # Print legend
    print(f"\n{'─' * 70}")
    print("📝 Legend:")
    print("   Excel  = Capacity from Excel file")
    print("   Pre    = Already enrolled (tot_enrl)")
    print("   Avail  = Available seats before allocation")
    print("   Alloc  = Students allocated in this run")
    print("   After  = Remaining seats after allocation")
    print("   *      = Capacity modified by override")
    print("   Status: ✅ OK | 🟡 FULL | 🟠 Within overfill | 🔴 Over limit")
    print("=" * 70)


def export_timetables_to_pdf(
    timetables: dict[str, list[GeneratedTimetable]],
    output_path: str,
    sections_by_course: dict[str, list[Section]] | None = None,
    packages: dict[str, list[str]] | None = None,
):
    """Export all timetables to a single PDF with one timetable per page, plus capacity report."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4),
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "TimetableTitle",
        parent=styles["Heading1"],
        fontSize=16,
        alignment=TA_CENTER,
        spaceAfter=10,
    )

    subtitle_style = ParagraphStyle(
        "TimetableSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        alignment=TA_CENTER,
        spaceAfter=15,
        textColor=colors.gray,
    )

    cell_style = ParagraphStyle(
        "CellStyle",
        parent=styles["Normal"],
        fontSize=7,
        alignment=TA_CENTER,
        leading=9,
    )

    elements = []

    # Get course colors
    all_courses = set()
    for plan_tts in timetables.values():
        for tt in plan_tts:
            for s in tt.sections:
                all_courses.add(s.course_code)

    course_colors = {
        code: COURSE_COLORS[i % len(COURSE_COLORS)]
        for i, code in enumerate(sorted(all_courses))
    }

    for plan, plan_timetables in timetables.items():
        for tt in plan_timetables:
            # Title
            title = Paragraph(f"Timetable {tt.timetable_id} - {plan}", title_style)
            elements.append(title)

            # Course count subtitle (no class numbers)
            courses = sorted(set(s.course_code for s in tt.sections))
            subtitle_text = (
                f"{len(courses)} Courses | "
                f"{'Variant (0 assigned)' if tt.is_variant else f'Batch Size: {tt.batch_size} students'}"
                f" | Max capacity: {tt.capacity_ceiling}"
            )
            subtitle = Paragraph(subtitle_text, subtitle_style)
            elements.append(subtitle)

            # Create grid
            grid = create_timetable_grid(tt)

            # Build table data
            # Header row
            header = ["Time"] + DAYS
            table_data = [header]

            # Time slot rows
            for slot_idx, (start, end) in enumerate(TIME_SLOTS):
                row = [f"{start}\n-\n{end}"]
                for day_idx in range(len(DAYS)):
                    cell_content = grid[day_idx][slot_idx]
                    if cell_content:
                        # Wrap in Paragraph for proper text handling
                        cell = Paragraph(
                            cell_content.replace("\n", "<br/>"), cell_style
                        )
                    else:
                        cell = ""
                    row.append(cell)
                table_data.append(row)

            # Create table
            col_widths = [0.8 * inch] + [1.6 * inch] * 6
            row_heights = [0.35 * inch] + [0.55 * inch] * len(TIME_SLOTS)

            table = Table(table_data, colWidths=col_widths, rowHeights=row_heights)

            # Style the table
            style_commands = [
                # Header styling
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                # Time column styling
                ("BACKGROUND", (0, 1), (0, -1), colors.Color(0.9, 0.9, 0.92)),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 1), (0, -1), 8),
                ("ALIGN", (0, 1), (0, -1), "CENTER"),
                ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
                # General cell styling
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("VALIGN", (1, 1), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (1, 1), (-1, -1), 7),
                # Grid lines
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.7, 0.7, 0.7)),
                ("BOX", (0, 0), (-1, -1), 1.5, colors.Color(0.2, 0.4, 0.6)),
                # Inner cell padding
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]

            # Add colored backgrounds for filled cells
            for section in tt.sections:
                color = course_colors.get(section.course_code, COURSE_COLORS[0])
                for meeting in section.meetings:
                    day = meeting.get("day", "")
                    start = meeting.get("start", "")

                    if day not in DAYS:
                        continue

                    day_idx = DAYS.index(day) + 1  # +1 for time column
                    slot_idx = time_to_slot_index(start) + 1  # +1 for header row

                    if slot_idx > 0:
                        style_commands.append(
                            (
                                "BACKGROUND",
                                (day_idx, slot_idx),
                                (day_idx, slot_idx),
                                color,
                            )
                        )

            table.setStyle(TableStyle(style_commands))
            elements.append(table)

            # Page break after each timetable
            elements.append(PageBreak())

    # Add Capacity Report at the end
    if sections_by_course:
        # Calculate capacity usage from timetables (newly assigned students)
        newly_assigned: dict[int, int] = defaultdict(int)
        for plan_tts in timetables.values():
            for tt in plan_tts:
                seen_in_tt = set()
                for section in tt.sections:
                    if section.class_nbr not in seen_in_tt:
                        seen_in_tt.add(section.class_nbr)
                        newly_assigned[section.class_nbr] += tt.batch_size

        # Build a map of class_nbr -> tot_enrl (existing enrollment)
        existing_enrl: dict[int, int] = {}
        for sections in sections_by_course.values():
            for s in sections:
                existing_enrl[s.class_nbr] = s.tot_enrl

        # Total enrolled = existing + newly assigned
        capacity_usage: dict[int, int] = defaultdict(int)
        for class_nbr in set(newly_assigned.keys()) | set(existing_enrl.keys()):
            capacity_usage[class_nbr] = existing_enrl.get(
                class_nbr, 0
            ) + newly_assigned.get(class_nbr, 0)

        # Title for capacity report
        report_title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Heading1"],
            fontSize=20,
            alignment=TA_CENTER,
            spaceAfter=20,
        )
        elements.append(Paragraph("CAPACITY REPORT", report_title_style))
        elements.append(Paragraph("Section-wise Utilization Summary", subtitle_style))
        elements.append(Spacer(1, 0.3 * inch))

        # Get courses from packages (if provided), otherwise use courses from timetables
        if packages:
            courses_in_packages = set()
            for course_list in packages.values():
                courses_in_packages.update(course_list)
            courses_used = courses_in_packages
        else:
            # Fallback: use courses from timetables
            courses_used = set()
            for plan_tts in timetables.values():
                for tt in plan_tts:
                    for s in tt.sections:
                        courses_used.add(s.course_code)

        for course_code in sorted(courses_used):
            sections = sections_by_course.get(course_code, [])
            if not sections:
                continue

            # Group by component
            by_component: dict[str, list[Section]] = defaultdict(list)
            for s in sections:
                by_component[s.component].append(s)

            # Calculate course totals
            course_capacity = sum(s.cap_enrl for s in sections)
            course_enrolled = sum(capacity_usage.get(s.class_nbr, 0) for s in sections)
            course_pct = (
                (course_enrolled / course_capacity * 100) if course_capacity > 0 else 0
            )

            # Course header
            course_header_style = ParagraphStyle(
                "CourseHeader",
                parent=styles["Heading2"],
                fontSize=12,
                spaceBefore=15,
                spaceAfter=5,
                textColor=colors.Color(0.2, 0.3, 0.5),
            )
            elements.append(
                Paragraph(
                    f"{course_code} - {course_enrolled}/{course_capacity} enrolled ({course_pct:.1f}%)",
                    course_header_style,
                )
            )

            # Create table for each component
            for component in sorted(by_component.keys()):
                comp_sections = by_component[component]
                comp_capacity = sum(s.cap_enrl for s in comp_sections)
                comp_enrolled = sum(
                    capacity_usage.get(s.class_nbr, 0) for s in comp_sections
                )
                comp_pct = (
                    (comp_enrolled / comp_capacity * 100) if comp_capacity > 0 else 0
                )

                # Component label
                comp_label_style = ParagraphStyle(
                    "CompLabel",
                    parent=styles["Normal"],
                    fontSize=9,
                    spaceBefore=5,
                    textColor=colors.gray,
                )
                status = (
                    "OVERFILLED"
                    if comp_pct > 100
                    else "NEAR FULL"
                    if comp_pct >= 90
                    else "OK"
                )
                elements.append(
                    Paragraph(
                        f"[{component}] {comp_enrolled}/{comp_capacity} ({comp_pct:.1f}%) - {status}",
                        comp_label_style,
                    )
                )

                # Build section table
                table_data = [
                    ["Section", "Class#", "Capacity", "Enrolled", "Remaining", "Fill %"]
                ]

                for s in sorted(comp_sections, key=lambda x: x.section):
                    enrolled = capacity_usage.get(s.class_nbr, 0)
                    remaining = s.cap_enrl - enrolled
                    fill_pct = (enrolled / s.cap_enrl * 100) if s.cap_enrl > 0 else 0

                    remaining_str = str(remaining) if remaining >= 0 else f"{remaining}"
                    fill_str = f"{fill_pct:.1f}%"

                    table_data.append(
                        [
                            s.section,
                            str(s.class_nbr),
                            str(s.cap_enrl),
                            str(enrolled),
                            remaining_str,
                            fill_str,
                        ]
                    )

                # Create and style the table
                cap_table = Table(
                    table_data,
                    colWidths=[
                        0.8 * inch,
                        0.8 * inch,
                        0.8 * inch,
                        0.8 * inch,
                        0.8 * inch,
                        0.8 * inch,
                    ],
                )
                cap_table_style = TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.85, 0.85, 0.9)),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 8),
                        ("FONTSIZE", (0, 1), (-1, -1), 7),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.8, 0.8, 0.8)),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]
                )

                # Color rows based on fill percentage
                for row_idx in range(1, len(table_data)):
                    enrolled = capacity_usage.get(
                        comp_sections[row_idx - 1].class_nbr, 0
                    )
                    cap = comp_sections[row_idx - 1].cap_enrl
                    fill_pct = (enrolled / cap * 100) if cap > 0 else 0

                    if fill_pct > 100:
                        bg_color = colors.Color(1.0, 0.85, 0.85)  # Light red
                    elif fill_pct >= 90:
                        bg_color = colors.Color(1.0, 0.95, 0.85)  # Light yellow
                    elif fill_pct >= 50:
                        bg_color = colors.Color(0.85, 1.0, 0.85)  # Light green
                    else:
                        bg_color = colors.white

                    cap_table_style.add(
                        "BACKGROUND", (0, row_idx), (-1, row_idx), bg_color
                    )

                cap_table.setStyle(cap_table_style)
                elements.append(cap_table)
                elements.append(Spacer(1, 0.1 * inch))

        # Summary at the end
        elements.append(Spacer(1, 0.3 * inch))

        total_assigned = sum(
            sum(tt.batch_size for tt in plan_tts) for plan_tts in timetables.values()
        )
        total_timetables = sum(len(plan_tts) for plan_tts in timetables.values())

        summary_style = ParagraphStyle(
            "Summary",
            parent=styles["Normal"],
            fontSize=11,
            alignment=TA_CENTER,
            spaceBefore=10,
        )
        elements.append(
            Paragraph(
                f"<b>Total Timetables:</b> {total_timetables} | <b>Total Students Assigned:</b> {total_assigned}",
                summary_style,
            )
        )

    # Build PDF
    doc.build(elements)
    print(f"\nExported PDF to: {output_path}")


# ==================== Main ====================


def main():
    parser = argparse.ArgumentParser(description="Bulk Timetable Generator")
    parser.add_argument(
        "--packages", default="data/packages.json", help="Path to packages.json file"
    )
    parser.add_argument(
        "--count", default="data/count.csv", help="Path to count.csv file"
    )
    parser.add_argument(
        "--timetable",
        default="data/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx",
        help="Path to timetable Excel file",
    )
    parser.add_argument(
        "--output",
        default="exports/bulk_timetables",
        help="Output directory for generated timetables",
    )
    parser.add_argument(
        "--capacity", type=int, default=40, help="Default section capacity"
    )
    parser.add_argument(
        "--min-timetables-per-plan",
        type=int,
        default=10,
        help="Target minimum number of timetable variants per plan",
    )
    parser.add_argument(
        "--variant-retries",
        type=int,
        default=3,
        help="Attempts to diversify section combos when variants repeat",
    )
    parser.add_argument(
        "--compactness-weight",
        type=float,
        default=0.0,
        help="Weight to prefer back-to-back classes (0 disables)",
    )
    parser.add_argument(
        "--allow-negative",
        action="store_true",
        default=True,
        help="Allow negative capacity to find exact capacity needs (default: True)",
    )
    parser.add_argument(
        "--no-allow-negative",
        action="store_true",
        help="Disable negative capacity mode (strict capacity enforcement)",
    )
    parser.add_argument(
        "--max-lab-overfill",
        type=int,
        default=3,
        help="Maximum overfill per LAB section (default: 3)",
    )
    parser.add_argument(
        "--max-tut-overfill",
        type=int,
        default=8,
        help="Maximum overfill per TUT section (default: 8)",
    )

    args = parser.parse_args()

    # Handle the allow-negative flag
    global ALLOW_NEGATIVE_CAPACITY
    if args.no_allow_negative:
        ALLOW_NEGATIVE_CAPACITY = False
    else:
        ALLOW_NEGATIVE_CAPACITY = args.allow_negative

    # Set global overfill limits
    global MAX_OVERFILL_LIMITS
    MAX_OVERFILL_LIMITS = {
        "LAB": args.max_lab_overfill,
        "PRO": args.max_lab_overfill,
        "PRA": args.max_lab_overfill,
        "TUT": args.max_tut_overfill,
    }

    print("=" * 70)
    print("📚 BULK TIMETABLE GENERATOR")
    print("=" * 70)

    # Parse input files
    packages = parse_packages(args.packages)
    counts = parse_count(args.count)

    print("\n📦 Input Files:")
    print(f"   Packages: {args.packages} ({len(packages)} plans)")
    print(f"   Counts:   {args.count} ({sum(counts.values())} total students)")
    print(f"   Timetable: {args.timetable}")

    # Combine plans that have identical packages to mix cohorts
    grouped_packages, grouped_counts, membership = group_plans_by_packages(
        packages, counts
    )

    print("\n📊 Plans (after grouping):")
    for combined, plans in membership.items():
        count = grouped_counts.get(combined, 0)
        if count > 0:
            if len(plans) > 1:
                print(f"   • {combined}: {count} students (combined)")
            else:
                print(f"   • {combined}: {count} students")

    sections_by_course, course_id_map = parse_timetable_excel(args.timetable)
    print(f"\n📅 Timetable: {len(sections_by_course)} courses loaded")

    # Show settings
    print("\n⚙️  Settings:")
    print(f"   Max LAB overfill: +{MAX_OVERFILL_LIMITS.get('LAB', 3)}/section")
    print(f"   Max TUT overfill: +{MAX_OVERFILL_LIMITS.get('TUT', 8)}/section")
    if COURSE_OVERFILL_OVERRIDES:
        overrides_str = ", ".join(
            f"{c}:{v}"
            for c, comp in COURSE_OVERFILL_OVERRIDES.items()
            for v in [f"LAB={comp.get('LAB', '?')}"]
            if "LAB" in comp
        )
        print(f"   Course overrides: {overrides_str}")

    # Generate timetables
    print("\n" + "=" * 70)
    print("🔄 GENERATING TIMETABLES")
    print("=" * 70)

    # Encourage more, smaller batches for mixing
    min_batch_size = max(5, args.capacity // 3)

    timetables = generate_bulk_timetables(
        grouped_packages,
        grouped_counts,
        sections_by_course,
        args.capacity,
        args.output,
        num_strategies=25,
        min_batch_size=min_batch_size,
        batch_randomness=True,
        min_timetables_per_plan=args.min_timetables_per_plan,
        variant_retry_attempts=args.variant_retries,
        compactness_weight=args.compactness_weight,
    )

    # Print summary
    print_summary(timetables, membership, grouped_counts)

    # Verify timetables
    if timetables:
        verification = verify_timetables(
            timetables, sections_by_course, grouped_packages
        )
        print_verification_report(verification)

        # Report capacity deficits (when ALLOW_NEGATIVE_CAPACITY is True)
        if ALLOW_NEGATIVE_CAPACITY:
            print_capacity_deficit_report(timetables, sections_by_course)

            # Diagnose allocation failures
            diagnose_allocation_failures(
                timetables, sections_by_course, grouped_counts, grouped_packages
            )

            # Generate detailed capacity insights
            generate_capacity_insights(
                timetables, sections_by_course, grouped_counts, grouped_packages
            )

            # Print detailed section-level report
            print_detailed_section_report(
                timetables, sections_by_course, grouped_packages
            )

    # Export
    if timetables:
        export_timetables_to_csv(timetables, args.output)
        print(f"\n📁 Exported to: {args.output}/")

        # Export PDF with capacity report
        pdf_path = Path(args.output) / "all_timetables.pdf"
        export_timetables_to_pdf(
            timetables, str(pdf_path), sections_by_course, packages
        )


if __name__ == "__main__":
    main()
