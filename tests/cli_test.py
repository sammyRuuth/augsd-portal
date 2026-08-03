#!/usr/bin/env python
"""
Interactive CLI Test Tool

Provides an interactive way to test the portal components.

Usage:
    uv run python tests/cli_test.py
"""

import json
import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()

SAMPLE_DIR = Path(__file__).parent.parent / "sample_files"


def test_student_lookup():
    """Interactive student lookup test"""
    from app.core.branch_extractor import extract_branch_info
    from app.core.parsers import parse_students_excel

    console.rule("[bold blue]Student Lookup Test")

    students_file = SAMPLE_DIR / "BITS_ACTIVE_STUDENTS_ONROLL_431328192.xlsx"
    if not students_file.exists():
        rprint("[red]Students file not found")
        return

    students = parse_students_excel(students_file)
    students_by_id = {s.campus_id: s for s in students}

    rprint(f"[green]Loaded {len(students)} students")

    while True:
        campus_id = Prompt.ask(
            "\nEnter Campus ID to lookup (or 'q' to quit)", default=""
        )

        if campus_id.lower() == "q" or not campus_id:
            break

        student = students_by_id.get(campus_id.upper())
        if student:
            info = extract_branch_info(campus_id.upper())

            console.print("\n[bold green]Student Found:[/]")
            console.print(f"  Name: {student.name}")
            console.print(f"  Campus ID: {student.campus_id}")
            console.print(f"  Student ID: {student.student_id}")
            console.print(f"  Email: {student.email}")

            if info:
                console.print("\n[bold cyan]Branch Info:[/]")
                console.print(f"  Year: {info.get('year')}")
                console.print(f"  Branches: {info.get('branches')}")
        else:
            rprint(f"[yellow]Student not found: {campus_id}")

            # Suggest similar IDs
            suggestions = [
                s.campus_id for s in students if campus_id.upper() in s.campus_id
            ][:5]
            if suggestions:
                rprint(f"[cyan]Similar IDs: {suggestions}")


def test_course_search():
    """Interactive course search test"""
    from app.core.parsers import parse_courses_timetable_excel

    console.rule("[bold blue]Course Search Test")

    courses_file = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"
    if not courses_file.exists():
        rprint("[red]Courses file not found")
        return

    courses, sections = parse_courses_timetable_excel(courses_file)

    # Build course lookup
    courses_list = list(courses)

    rprint(
        f"[green]Loaded {len(courses_list)} courses, {len(sections)} section records"
    )

    while True:
        query = Prompt.ask(
            "\nSearch courses by subject/catalog (or 'q' to quit)", default=""
        )

        if query.lower() == "q" or not query:
            break

        # Search
        results = []
        query_upper = query.upper()
        for c in courses_list:
            if (
                query_upper in c.subject.upper()
                or query_upper in c.catalog.upper()
                or query_upper in c.title.upper()
            ):
                results.append(c)

        if results:
            table = Table(title=f"Found {len(results)} courses")
            table.add_column("Course ID")
            table.add_column("Subject")
            table.add_column("Catalog")
            table.add_column("Title")
            table.add_column("Units")

            for c in results[:15]:
                table.add_row(
                    c.course_id,
                    c.subject,
                    c.catalog,
                    c.title[:40],
                    str(c.max_units) if c.max_units else "",
                )

            console.print(table)

            if len(results) > 15:
                rprint(f"[yellow]...and {len(results) - 15} more")
        else:
            rprint(f"[yellow]No courses found matching: {query}")


def test_section_details():
    """Show sections for a specific course"""
    from app.core.parsers import parse_courses_timetable_excel

    console.rule("[bold blue]Section Details Test")

    courses_file = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"
    if not courses_file.exists():
        rprint("[red]Courses file not found")
        return

    courses, sections = parse_courses_timetable_excel(courses_file)

    # Group sections by course
    sections_by_course = {}
    for s in sections:
        course_id = s["course_id"]
        if course_id not in sections_by_course:
            sections_by_course[course_id] = []
        sections_by_course[course_id].append(s)

    while True:
        course_id = Prompt.ask(
            "\nEnter Course ID to see sections (or 'q' to quit)", default=""
        )

        if course_id.lower() == "q" or not course_id:
            break

        # Normalize course ID
        normalized = course_id.zfill(6)

        course_sections = sections_by_course.get(normalized, [])
        if course_sections:
            table = Table(
                title=f"Sections for {normalized} ({len(course_sections)} records)"
            )
            table.add_column("Section")
            table.add_column("Component")
            table.add_column("Class Nbr")
            table.add_column("Day")
            table.add_column("Time")
            table.add_column("Room")
            table.add_column("Capacity")

            for s in course_sections[:20]:
                start = str(s["mtg_start"])[:5] if s.get("mtg_start") else ""
                end = str(s["mtg_end"])[:5] if s.get("mtg_end") else ""
                time_str = f"{start}-{end}" if start else ""
                cap = str(s.get("cap_enrl", "")) if s.get("cap_enrl") else ""

                table.add_row(
                    s["section"],
                    s["component"],
                    str(s["class_nbr"]),
                    s.get("day", ""),
                    time_str,
                    s.get("room", "") or "",
                    cap,
                )

            console.print(table)
        else:
            rprint(f"[yellow]No sections found for: {course_id}")


def test_timetable_generation():
    """Interactive timetable generation test"""
    from app.core.parsers import parse_courses_timetable_excel
    from app.core.timetable_generator import CourseSection, TimetableGenerator

    console.rule("[bold blue]Timetable Generation Test")

    courses_file = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"
    if not courses_file.exists():
        rprint("[red]Courses file not found")
        return

    courses, sections = parse_courses_timetable_excel(courses_file)

    # Group sections by course
    sections_by_course = {}
    for s in sections:
        course_id = s["course_id"]
        if course_id not in sections_by_course:
            sections_by_course[course_id] = []
        sections_by_course[course_id].append(s)

    courses_dict = {c.course_id: c for c in courses}

    rprint("[cyan]Enter course IDs to generate a timetable (comma-separated)")
    rprint("[cyan]Example: 001022, 001004, 002863")

    course_ids_input = Prompt.ask("\nCourse IDs", default="")

    if not course_ids_input:
        return

    course_ids = [c.strip().zfill(6) for c in course_ids_input.split(",")]

    # Build CourseSection objects
    course_sections_for_gen = {}

    for course_id in course_ids:
        if course_id not in sections_by_course:
            rprint(f"[yellow]Course not found: {course_id}")
            continue

        sects = sections_by_course[course_id]
        course_data = courses_dict.get(course_id, {})
        units = (
            course_data.max_units
            if hasattr(course_data, "max_units") and course_data.max_units
            else 3.0
        )

        course_sections_for_gen[course_id] = []

        # Deduplicate and limit sections
        seen = set()
        for s in sects[:10]:
            section_key = (s["section"], s["component"])
            if section_key in seen:
                continue
            seen.add(section_key)

            start_str = str(s["mtg_start"]) if s.get("mtg_start") else None
            end_str = str(s["mtg_end"]) if s.get("mtg_end") else None
            exam_str = str(s["exam_date"]) if s.get("exam_date") else None

            cs = CourseSection(
                id=f"{course_id}-{s['section']}-{s['component']}-{s.get('day', '')}",
                course_id=course_id,
                section=s["section"],
                component=s["component"],
                day=s.get("day", ""),
                start=start_str,
                end=end_str,
                exam_date=exam_str,
                units=units,
            )
            course_sections_for_gen[course_id].append(cs)

    if not course_sections_for_gen:
        rprint("[red]No valid courses found")
        return

    rprint(
        f"\n[cyan]Generating timetable for {len(course_sections_for_gen)} courses..."
    )

    generator = TimetableGenerator(max_units=30.0)
    result = generator.generate(course_sections_for_gen)

    console.print("\n[bold green]Generation Result:[/]")
    console.print(f"  Success: {result.success}")
    console.print(f"  Assigned sections: {len(result.assigned_sections)}")
    console.print(f"  Total units: {result.total_units}")

    if result.assigned_sections:
        console.print("\n[bold cyan]Assigned Sections:[/]")
        for sec_id in result.assigned_sections:
            console.print(f"  - {sec_id}")

    if result.conflicts:
        console.print(f"\n[bold yellow]Conflicts ({len(result.conflicts)}):[/]")
        for c in result.conflicts:
            console.print(f"  - {c.type}: {c.message}")


def test_default_packages():
    """Show default packages"""
    console.rule("[bold blue]Default Packages Test")

    packages_file = SAMPLE_DIR / "defualt_packages.json"
    if not packages_file.exists():
        rprint("[red]Default packages file not found")
        return

    with open(packages_file) as f:
        packages = json.load(f)

    for year, year_packages in packages.items():
        console.print(f"\n[bold green]Year {year}:[/]")

        for branch_group, courses in year_packages.items():
            console.print(f"\n  [cyan]Branches: {branch_group}[/]")
            for course in courses:
                console.print(f"    - {course}")


def main():
    """Main CLI menu"""
    console.rule("[bold magenta]AUGSD Portal - Interactive Test Tool")

    while True:
        console.print("\n[bold cyan]Available Tests:[/]")
        console.print("  1. Student Lookup")
        console.print("  2. Course Search")
        console.print("  3. Section Details")
        console.print("  4. Timetable Generation")
        console.print("  5. Default Packages")
        console.print("  q. Quit")

        choice = Prompt.ask(
            "\nSelect test", choices=["1", "2", "3", "4", "5", "q"], default="q"
        )

        if choice == "q":
            break
        elif choice == "1":
            test_student_lookup()
        elif choice == "2":
            test_course_search()
        elif choice == "3":
            test_section_details()
        elif choice == "4":
            test_timetable_generation()
        elif choice == "5":
            test_default_packages()

    console.print("\n[green]Goodbye!")


if __name__ == "__main__":
    main()
