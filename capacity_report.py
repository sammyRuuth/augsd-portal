"""
Capacity Report Script

Shows final capacity usage for each course, broken down by components and sections.
Displays enrollment, capacity, and fill percentage.

Usage:
    python capacity_report.py
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class SectionCapacity:
    """Track capacity for a section"""

    course_code: str
    component: str
    section: str
    class_nbr: int
    capacity: int
    enrolled: int = 0

    @property
    def fill_percentage(self) -> float:
        if self.capacity == 0:
            return 100.0 if self.enrolled > 0 else 0.0
        return (self.enrolled / self.capacity) * 100

    @property
    def remaining(self) -> int:
        return self.capacity - self.enrolled


def parse_timetable_excel(file_path: str) -> dict[int, SectionCapacity]:
    """Parse Excel file and return section capacities indexed by class_nbr."""
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

    sections: dict[int, SectionCapacity] = {}

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

            if class_nbr not in sections:
                sections[class_nbr] = SectionCapacity(
                    course_code=course_code,
                    component=component,
                    section=section_name,
                    class_nbr=class_nbr,
                    capacity=cap_enrl,
                    enrolled=0,
                )
            else:
                # Update capacity if higher
                if cap_enrl > sections[class_nbr].capacity:
                    sections[class_nbr].capacity = cap_enrl

        except Exception:
            continue

    return sections


def parse_packages(file_path: str) -> set[str]:
    """Get all courses used in packages."""
    with open(file_path) as f:
        data = json.load(f)

    courses = set()
    for year, plans in data.items():
        for plan_key, plan_courses in plans.items():
            courses.update(plan_courses)

    return courses


def load_enrollment(timetables_path: str, sections: dict[int, SectionCapacity]):
    """Load enrollment from generated timetables."""
    with open(timetables_path) as f:
        reader = csv.DictReader(f)

        # Track which timetables we've processed to avoid double-counting
        processed = set()

        for row in reader:
            plan = row["Plan"]
            tt_id = int(row["Timetable ID"])
            batch_size = int(row["Batch Size"])
            class_nbr = int(row["Class Nbr"])

            # Each timetable has multiple rows (one per meeting), only count once per section per timetable
            key = (plan, tt_id, class_nbr)
            if key in processed:
                continue
            processed.add(key)

            if class_nbr in sections:
                sections[class_nbr].enrolled += batch_size


def generate_report(sections: dict[int, SectionCapacity], used_courses: set[str]):
    """Generate capacity report."""

    # Group sections by course and component
    by_course: dict[str, dict[str, list[SectionCapacity]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for sec in sections.values():
        # Check if this course is used (handle -1 suffix)
        course_used = False
        for used in used_courses:
            if (
                sec.course_code == used
                or sec.course_code == f"{used}-1"
                or sec.course_code.startswith(used)
            ):
                course_used = True
                break

        if course_used or sec.enrolled > 0:
            by_course[sec.course_code][sec.component].append(sec)

    # Sort and print report
    print("=" * 100)
    print("CAPACITY REPORT - Final Section Utilization")
    print("=" * 100)

    total_capacity = 0
    total_enrolled = 0

    for course_code in sorted(by_course.keys()):
        components = by_course[course_code]

        # Calculate course totals
        course_capacity = sum(
            s.capacity for comp_secs in components.values() for s in comp_secs
        )
        course_enrolled = sum(
            s.enrolled for comp_secs in components.values() for s in comp_secs
        )
        course_pct = (
            (course_enrolled / course_capacity * 100) if course_capacity > 0 else 0
        )

        # Skip courses with no enrollment
        if course_enrolled == 0:
            continue

        total_capacity += course_capacity
        total_enrolled += course_enrolled

        print(f"\n{'─' * 100}")
        print(f"📚 {course_code}")
        print(
            f"   Total: {course_enrolled}/{course_capacity} enrolled ({course_pct:.1f}% filled)"
        )
        print(f"{'─' * 100}")

        for component in sorted(components.keys()):
            comp_sections = components[component]
            comp_capacity = sum(s.capacity for s in comp_sections)
            comp_enrolled = sum(s.enrolled for s in comp_sections)
            comp_pct = (comp_enrolled / comp_capacity * 100) if comp_capacity > 0 else 0

            # Determine component status
            if comp_pct > 100:
                status = "🔴 OVERFILLED"
            elif comp_pct >= 90:
                status = "🟡 NEAR FULL"
            elif comp_pct >= 50:
                status = "🟢 MODERATE"
            else:
                status = "⚪ LOW"

            print(
                f"\n   [{component}] {comp_enrolled}/{comp_capacity} ({comp_pct:.1f}%) {status}"
            )
            print(
                f"   {'Section':<10} {'Class#':<10} {'Enrolled':<12} {'Capacity':<12} {'Remaining':<12} {'Fill %':<10}"
            )
            print(
                f"   {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 10}"
            )

            # Sort sections by fill percentage (descending)
            for sec in sorted(
                comp_sections, key=lambda s: s.fill_percentage, reverse=True
            ):
                if sec.enrolled > 0 or sec.capacity > 0:
                    # Status indicator
                    if sec.fill_percentage > 100:
                        ind = "🔴"
                    elif sec.fill_percentage >= 90:
                        ind = "🟡"
                    elif sec.fill_percentage >= 50:
                        ind = "🟢"
                    else:
                        ind = "⚪"

                    remaining = sec.remaining
                    remaining_str = (
                        str(remaining) if remaining >= 0 else f"{remaining} (OVER)"
                    )

                    print(
                        f"   {sec.section:<10} {sec.class_nbr:<10} {sec.enrolled:<12} {sec.capacity:<12} {remaining_str:<12} {sec.fill_percentage:>6.1f}% {ind}"
                    )

    # Summary
    overall_pct = (total_enrolled / total_capacity * 100) if total_capacity > 0 else 0

    print(f"\n{'=' * 100}")
    print("SUMMARY")
    print(f"{'=' * 100}")
    print(f"Total Capacity (used courses): {total_capacity}")
    print(f"Total Enrolled: {total_enrolled}")
    print(f"Overall Fill Rate: {overall_pct:.1f}%")
    print(f"{'=' * 100}")

    # Export to CSV
    export_path = Path("exports/bulk_timetables/capacity_report.csv")
    export_path.parent.mkdir(parents=True, exist_ok=True)

    with open(export_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Course Code",
                "Component",
                "Section",
                "Class Nbr",
                "Capacity",
                "Enrolled",
                "Remaining",
                "Fill %",
            ]
        )

        for course_code in sorted(by_course.keys()):
            for component in sorted(by_course[course_code].keys()):
                for sec in sorted(
                    by_course[course_code][component], key=lambda s: s.section
                ):
                    if sec.enrolled > 0 or sec.capacity > 0:
                        writer.writerow(
                            [
                                sec.course_code,
                                sec.component,
                                sec.section,
                                sec.class_nbr,
                                sec.capacity,
                                sec.enrolled,
                                sec.remaining,
                                f"{sec.fill_percentage:.1f}%",
                            ]
                        )

    print(f"\nExported to: {export_path}")


def main():
    excel_path = "data/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx"
    packages_path = "data/packages.json"
    timetables_path = "exports/bulk_timetables/timetables_incremental.csv"

    # Check files exist
    for path in [excel_path, packages_path, timetables_path]:
        if not Path(path).exists():
            print(f"ERROR: File not found: {path}")
            return

    print("Loading data...")

    # Parse Excel for section capacities
    sections = parse_timetable_excel(excel_path)
    print(f"  Loaded {len(sections)} sections from Excel")

    # Get used courses from packages
    used_courses = parse_packages(packages_path)
    print(f"  Found {len(used_courses)} courses in packages")

    # Load enrollment from generated timetables
    load_enrollment(timetables_path, sections)
    enrolled_sections = sum(1 for s in sections.values() if s.enrolled > 0)
    print(f"  {enrolled_sections} sections have enrollment")

    # Generate report
    generate_report(sections, used_courses)


if __name__ == "__main__":
    main()
