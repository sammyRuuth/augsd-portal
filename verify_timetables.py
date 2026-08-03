"""
Timetable Verification Script

Verifies generated timetables against the source Excel file:
1. No time conflicts within each timetable
2. All courses from package are present
3. All components (LEC, TUT, LAB) for each course are present
4. Section capacity not exceeded (cumulative tracking)

Usage:
    python verify_timetables.py
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class SectionInfo:
    """Section information from Excel"""

    course_code: str
    class_nbr: int
    component: str
    section: str
    cap_enrl: int
    meetings: list[dict] = field(default_factory=list)


@dataclass
class TimetableEntry:
    """Single entry from generated timetable"""

    plan: str
    timetable_id: int
    batch_size: int
    course_code: str
    component: str
    section: str
    class_nbr: int
    day: str
    start: str
    end: str


@dataclass
class VerificationResult:
    """Result of verification"""

    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_timetable_excel(file_path: str) -> dict[int, SectionInfo]:
    """
    Parse Excel file and return section info indexed by class_nbr.
    Initialize all sections with 0 enrollment (full capacity available).
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

    df = pd.read_excel(file_path, header=header_row)
    df.columns = df.columns.str.strip()

    sections: dict[int, SectionInfo] = {}

    # Day pattern expansion
    def expand_days(pattern: str) -> list[str]:
        if not pattern:
            return []
        pattern = pattern.upper().strip()
        days = []
        i = 0
        day_map = {
            "M": "Monday",
            "T": "Tuesday",
            "W": "Wednesday",
            "F": "Friday",
            "S": "Saturday",
        }
        while i < len(pattern):
            if i + 1 < len(pattern) and pattern[i : i + 2] == "TH":
                days.append("Thursday")
                i += 2
            elif i + 1 < len(pattern) and pattern[i : i + 2] == "SU":
                days.append("Sunday")
                i += 2
            elif pattern[i] in day_map:
                days.append(day_map[pattern[i]])
                i += 1
            else:
                i += 1
        return days

    for _, row in df.iterrows():
        try:
            subject = str(row.get("Subject", "")).strip()
            catalog = str(row.get("Catalog", "")).strip()
            if not subject or not catalog:
                continue

            course_code = f"{subject} {catalog}"

            class_nbr_raw = row.get("Class Nbr", 0)
            if pd.isna(class_nbr_raw):
                continue
            class_nbr = int(float(class_nbr_raw))
            if not class_nbr:
                continue

            component = str(row.get("Component", "")).strip()
            section_name = str(row.get("Section", "")).strip()

            cap_enrl = 0
            if pd.notna(row.get("Cap Enrl", row.get("CAP ENRL"))):
                try:
                    cap_enrl = int(float(row.get("Cap Enrl", row.get("CAP ENRL"))))
                except:
                    pass

            # Parse meeting times
            class_pattern = (
                str(row.get("Class Pattern", "")).strip()
                if pd.notna(row.get("Class Pattern"))
                else ""
            )

            mtg_start_str = None
            if pd.notna(row.get("Mtg Start", row.get("MTG START"))):
                try:
                    val = row.get("Mtg Start", row.get("MTG START"))
                    if hasattr(val, "hour"):
                        mtg_start_str = val.strftime("%H:%M")
                    else:
                        mtg_start_str = pd.to_datetime(val).strftime("%H:%M")
                except:
                    pass

            mtg_end_str = None
            if pd.notna(row.get("End Time", row.get("END TIME"))):
                try:
                    val = row.get("End Time", row.get("END TIME"))
                    if hasattr(val, "hour"):
                        mtg_end_str = val.strftime("%H:%M")
                    else:
                        mtg_end_str = pd.to_datetime(val).strftime("%H:%M")
                except:
                    pass

            days = expand_days(class_pattern)

            # Create or update section
            if class_nbr not in sections:
                sections[class_nbr] = SectionInfo(
                    course_code=course_code,
                    class_nbr=class_nbr,
                    component=component,
                    section=section_name,
                    cap_enrl=cap_enrl,
                    meetings=[],
                )

            # Add meeting times
            for day in days:
                if day and mtg_start_str and mtg_end_str:
                    meeting = {"day": day, "start": mtg_start_str, "end": mtg_end_str}
                    if meeting not in sections[class_nbr].meetings:
                        sections[class_nbr].meetings.append(meeting)

            # Update capacity (take max)
            if cap_enrl > sections[class_nbr].cap_enrl:
                sections[class_nbr].cap_enrl = cap_enrl

        except Exception:
            continue

    return sections


def parse_packages(file_path: str) -> dict[str, list[str]]:
    """Parse packages.json"""
    with open(file_path) as f:
        data = json.load(f)

    packages = {}
    for year, plans in data.items():
        for plan_key, courses in plans.items():
            plan_name = plan_key.replace(", ", ",").replace(" ,", ",")
            packages[plan_name] = courses

    return packages


def parse_generated_timetables(
    file_path: str,
) -> dict[str, dict[int, list[TimetableEntry]]]:
    """
    Parse generated timetables CSV.
    Returns: {plan: {timetable_id: [entries]}}
    """
    timetables: dict[str, dict[int, list[TimetableEntry]]] = defaultdict(
        lambda: defaultdict(list)
    )

    with open(file_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry = TimetableEntry(
                plan=row["Plan"],
                timetable_id=int(row["Timetable ID"]),
                batch_size=int(row["Batch Size"]),
                course_code=row["Course Code"],
                component=row["Component"],
                section=row["Section"],
                class_nbr=int(row["Class Nbr"]),
                day=row.get("Day", ""),
                start=row.get("Start", ""),
                end=row.get("End", ""),
            )
            timetables[entry.plan][entry.timetable_id].append(entry)

    return dict(timetables)


def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM to minutes since midnight"""
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return -1


def times_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    """Check if two time ranges overlap"""
    s1, e1 = time_to_minutes(start1), time_to_minutes(end1)
    s2, e2 = time_to_minutes(start2), time_to_minutes(end2)

    if s1 < 0 or e1 < 0 or s2 < 0 or e2 < 0:
        return False

    return not (e1 <= s2 or e2 <= s1)


def find_course_in_package(course_code: str, package_courses: list[str]) -> str | None:
    """Find matching course in package (handles BITS F101 -> BITS F101-1 mapping)"""
    for pkg_course in package_courses:
        if course_code == pkg_course:
            return pkg_course
        if course_code == f"{pkg_course}-1":
            return pkg_course
        if course_code.startswith(pkg_course):
            return pkg_course
    return None


def get_required_components(
    course_code: str, sections: dict[int, SectionInfo]
) -> set[str]:
    """Get all components available for a course in the Excel"""
    components = set()
    for section in sections.values():
        if section.course_code == course_code:
            components.add(section.component)
    return components


def verify_timetable(
    plan: str,
    timetable_id: int,
    entries: list[TimetableEntry],
    package_courses: list[str],
    sections: dict[int, SectionInfo],
) -> VerificationResult:
    """Verify a single timetable"""
    result = VerificationResult()

    # 0. Check all class numbers exist in Excel
    for entry in entries:
        if entry.class_nbr not in sections:
            result.passed = False
            result.errors.append(
                f"Class {entry.class_nbr} ({entry.course_code}) not found in Excel"
            )

    # Group entries by course
    courses_in_tt: dict[str, list[TimetableEntry]] = defaultdict(list)
    for entry in entries:
        courses_in_tt[entry.course_code].append(entry)

    # 1. Check all courses from package are present
    resolved_courses = {}  # package_course -> actual_course_code
    for pkg_course in package_courses:
        found = False
        for tt_course in courses_in_tt.keys():
            if find_course_in_package(tt_course, [pkg_course]):
                resolved_courses[pkg_course] = tt_course
                found = True
                break

        if not found:
            result.passed = False
            result.errors.append(f"Missing course: {pkg_course}")

    # 2. Check all components for each course are present
    for pkg_course, tt_course in resolved_courses.items():
        required_components = get_required_components(tt_course, sections)
        present_components = set(e.component for e in courses_in_tt[tt_course])

        missing = required_components - present_components
        if missing:
            result.passed = False
            result.errors.append(f"Missing components for {tt_course}: {missing}")

    # 3. Check for time conflicts
    # Collect all meetings with their times (deduplicate by class_nbr + day + time)
    # Format: (course, day, start, end, section, class_nbr)
    all_meetings: list[tuple[str, str, str, str, str, int]] = []
    seen_meetings: set[tuple[int, str, str, str]] = (
        set()
    )  # (class_nbr, day, start, end)

    for course, course_entries in courses_in_tt.items():
        for entry in course_entries:
            if (
                entry.day
                and entry.start
                and entry.end
                and entry.day != "Online/Self-Study"
            ):
                # Deduplicate: same section, same day, same time should only appear once
                meeting_key = (entry.class_nbr, entry.day, entry.start, entry.end)
                if meeting_key not in seen_meetings:
                    seen_meetings.add(meeting_key)
                    all_meetings.append(
                        (
                            course,
                            entry.day,
                            entry.start,
                            entry.end,
                            entry.section,
                            entry.class_nbr,
                        )
                    )

    # Check pairwise conflicts (skip comparing same section with itself)
    for i in range(len(all_meetings)):
        for j in range(i + 1, len(all_meetings)):
            c1, d1, s1, e1, sec1, nbr1 = all_meetings[i]
            c2, d2, s2, e2, sec2, nbr2 = all_meetings[j]

            # Skip if same section (same class_nbr) - a section can't conflict with itself
            if nbr1 == nbr2:
                continue

            if d1 == d2 and times_overlap(s1, e1, s2, e2):
                result.passed = False
                result.errors.append(
                    f"Time conflict: {c1} ({sec1}) and {c2} ({sec2}) on {d1} ({s1}-{e1} vs {s2}-{e2})"
                )

    return result


def verify_capacity(
    timetables: dict[str, dict[int, list[TimetableEntry]]],
    sections: dict[int, SectionInfo],
) -> VerificationResult:
    """Verify cumulative capacity across all timetables"""
    result = VerificationResult()

    # Track enrollment per section
    enrollment: dict[int, int] = defaultdict(int)

    # Courses that can exceed capacity
    unlimited_courses = {"BITS F101-1", "BITS K101-1"}

    # Components that can always be overfilled (lectures)
    overfillable_components = {"LEC"}

    # Components that can be overfilled as last resort (tutorials)
    soft_strict_components = {"TUT"}

    # Components with hard strict capacity (labs, practicals)
    hard_strict_components = {"LAB", "PRO", "PRA"}

    # Process all timetables in order
    for plan in sorted(timetables.keys()):
        for tt_id in sorted(timetables[plan].keys()):
            entries = timetables[plan][tt_id]

            # Get batch size (same for all entries in a timetable)
            batch_size = entries[0].batch_size if entries else 0

            # Get unique class numbers in this timetable
            class_nbrs = set(e.class_nbr for e in entries)

            # Add enrollment
            for nbr in class_nbrs:
                enrollment[nbr] += batch_size

    # Check against capacity
    for class_nbr, enrolled in enrollment.items():
        if class_nbr not in sections:
            result.warnings.append(f"Class {class_nbr} not found in Excel")
            continue

        section = sections[class_nbr]
        capacity = section.cap_enrl
        component = section.component

        if section.course_code in unlimited_courses:
            # These can exceed capacity (no warning needed)
            if enrolled > capacity:
                result.warnings.append(
                    f"[OK - Unlimited] {section.course_code} ({class_nbr}): {enrolled}/{capacity} enrolled"
                )
        elif component in overfillable_components:
            # LEC sections can be overfilled (warning only, not error)
            if enrolled > capacity:
                result.warnings.append(
                    f"[OK - LEC Overfill] {section.course_code} {section.section} ({class_nbr}): "
                    f"{enrolled}/{capacity} enrolled (over by {enrolled - capacity})"
                )
        elif component in soft_strict_components:
            # TUT sections can be overfilled as last resort (warning only)
            if enrolled > capacity:
                result.warnings.append(
                    f"[OK - TUT Overfill] {section.course_code} {section.section} ({class_nbr}): "
                    f"{enrolled}/{capacity} enrolled (over by {enrolled - capacity})"
                )
        elif component in hard_strict_components:
            # LAB/PRO sections have hard strict capacity
            if enrolled > capacity:
                result.passed = False
                result.errors.append(
                    f"CAPACITY EXCEEDED ({component}): {section.course_code} {section.section} ({class_nbr}): "
                    f"{enrolled}/{capacity} enrolled (over by {enrolled - capacity})"
                )
        else:
            # Unknown component - treat as strict
            if enrolled > capacity:
                result.passed = False
                result.errors.append(
                    f"CAPACITY EXCEEDED: {section.course_code} {section.section} ({class_nbr}): "
                    f"{enrolled}/{capacity} enrolled (over by {enrolled - capacity})"
                )

    return result


def main():
    print("=" * 70)
    print("TIMETABLE VERIFICATION")
    print("=" * 70)

    # File paths
    excel_path = "data/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx"
    packages_path = "data/packages.json"
    timetables_path = "exports/bulk_timetables/timetables_incremental.csv"

    # Check files exist
    for path in [excel_path, packages_path, timetables_path]:
        if not Path(path).exists():
            print(f"ERROR: File not found: {path}")
            return

    # Parse files
    print(f"\n[1] Parsing Excel timetable: {excel_path}")
    sections = parse_timetable_excel(excel_path)
    print(f"    Found {len(sections)} sections")

    print(f"\n[2] Parsing packages: {packages_path}")
    packages = parse_packages(packages_path)
    print(f"    Found {len(packages)} plans")

    print(f"\n[3] Parsing generated timetables: {timetables_path}")
    timetables = parse_generated_timetables(timetables_path)
    total_tt = sum(len(tt_dict) for tt_dict in timetables.values())
    print(f"    Found {total_tt} timetables across {len(timetables)} plans")

    # Verification results
    all_passed = True
    total_errors = 0
    total_warnings = 0

    # Verify each timetable
    print("\n" + "=" * 70)
    print("VERIFYING INDIVIDUAL TIMETABLES")
    print("=" * 70)

    for plan in sorted(timetables.keys()):
        # Find matching package (handle plan name variations)
        package_courses = None
        plan_parts = set(plan.split(","))

        for pkg_plan, courses in packages.items():
            pkg_parts = set(pkg_plan.split(","))
            if (
                plan_parts == pkg_parts
                or plan_parts.issubset(pkg_parts)
                or pkg_parts.issubset(plan_parts)
            ):
                package_courses = courses
                break

        if not package_courses:
            print(f"\n[{plan}] WARNING: No matching package found")
            total_warnings += 1
            continue

        print(f"\n[{plan}]")
        print(f"  Required courses: {package_courses}")

        plan_errors = 0
        for tt_id in sorted(timetables[plan].keys()):
            entries = timetables[plan][tt_id]
            result = verify_timetable(plan, tt_id, entries, package_courses, sections)

            if not result.passed:
                all_passed = False
                plan_errors += len(result.errors)
                print(f"  TT{tt_id}: FAILED")
                for err in result.errors:
                    print(f"    - {err}")

            for warn in result.warnings:
                print(f"  TT{tt_id} WARNING: {warn}")
                total_warnings += 1

        if plan_errors == 0:
            print(f"  All {len(timetables[plan])} timetables passed ✓")
        else:
            total_errors += plan_errors

    # Verify capacity
    print("\n" + "=" * 70)
    print("VERIFYING CUMULATIVE CAPACITY")
    print("=" * 70)

    capacity_result = verify_capacity(timetables, sections)

    if capacity_result.errors:
        all_passed = False
        total_errors += len(capacity_result.errors)
        print("\nCAPACITY ERRORS:")
        for err in capacity_result.errors:
            print(f"  - {err}")

    if capacity_result.warnings:
        total_warnings += len(capacity_result.warnings)
        print("\nCapacity Warnings (OK - unlimited courses):")
        for warn in capacity_result.warnings[:5]:  # Show first 5
            print(f"  - {warn}")
        if len(capacity_result.warnings) > 5:
            print(f"  ... and {len(capacity_result.warnings) - 5} more")

    if capacity_result.passed and not capacity_result.errors:
        print("\nAll capacity checks passed ✓")

    # Final summary
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)

    # Calculate totals
    total_students = 0
    for plan, tt_dict in timetables.items():
        for tt_id, entries in tt_dict.items():
            if entries:
                total_students += entries[0].batch_size

    print(f"\nTotal timetables verified: {total_tt}")
    print(f"Total students assigned: {total_students}")
    print(f"Total errors: {total_errors}")
    print(f"Total warnings: {total_warnings}")

    if all_passed:
        print("\n✓ ALL VERIFICATIONS PASSED")
    else:
        print("\n✗ VERIFICATION FAILED - See errors above")

    return all_passed


if __name__ == "__main__":
    import sys

    success = main()
    sys.exit(0 if success else 1)
