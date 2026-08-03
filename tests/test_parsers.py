#!/usr/bin/env python
"""
Test 1: Parser Tests

Tests all Excel parsers with sample files from sample_files/
"""

# Add parent to path
import sys
from pathlib import Path

from rich import print as rprint
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.parsers import (
    parse_courses_timetable_excel,
    parse_prerequisites_excel,
    parse_registration_excel,
    parse_students_excel,
)

console = Console()

SAMPLE_DIR = Path(__file__).parent.parent / "sample_files"


def test_students_parser():
    """Test student Excel parser"""
    console.rule("[bold blue]Test: Students Parser")

    file_path = SAMPLE_DIR / "BITS_ACTIVE_STUDENTS_ONROLL_431328192.xlsx"

    if not file_path.exists():
        rprint(f"[red]File not found: {file_path}")
        return False

    try:
        students = parse_students_excel(file_path)

        rprint(f"[green]✓ Parsed {len(students)} students")

        # Show sample data
        if students:
            table = Table(title="Sample Students (first 5)")
            table.add_column("Student ID", style="cyan")
            table.add_column("Campus ID", style="green")
            table.add_column("Name")
            table.add_column("Email")

            for s in students[:5]:
                table.add_row(
                    str(s.student_id),
                    s.campus_id,
                    s.name[:30] if s.name else "",
                    s.email[:30] if s.email else "",
                )

            console.print(table)

            # Validate campus ID format
            valid_campus_ids = 0
            for s in students:
                if s.campus_id and len(s.campus_id) >= 10:
                    valid_campus_ids += 1

            rprint(f"[green]✓ {valid_campus_ids}/{len(students)} have valid campus IDs")

        return True

    except Exception as e:
        rprint(f"[red]✗ Parser failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_courses_parser():
    """Test courses/timetable Excel parser"""
    console.rule("[bold blue]Test: Courses/Timetable Parser")

    file_path = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"

    if not file_path.exists():
        rprint(f"[red]File not found: {file_path}")
        return False

    try:
        courses, sections = parse_courses_timetable_excel(file_path)

        rprint(f"[green]✓ Parsed {len(courses)} unique courses")
        rprint(f"[green]✓ Parsed {len(sections)} section records")

        # Show sample courses
        if courses:
            table = Table(title="Sample Courses (first 5)")
            table.add_column("Course ID", style="cyan")
            table.add_column("Subject", style="green")
            table.add_column("Catalog")
            table.add_column("Title")
            table.add_column("Units")

            for c in list(courses)[:5]:
                table.add_row(
                    c.course_id,
                    c.subject,
                    c.catalog,
                    c.title[:30] if c.title else "",
                    str(c.max_units) if c.max_units else "",
                )

            console.print(table)

        # Show sample sections
        if sections:
            table = Table(title="Sample Sections (first 5)")
            table.add_column("Course ID")
            table.add_column("Class Nbr", style="cyan")
            table.add_column("Section")
            table.add_column("Component")
            table.add_column("Day")
            table.add_column("Time")

            for s in sections[:5]:
                start = str(s["mtg_start"]) if s.get("mtg_start") else ""
                end = str(s["mtg_end"]) if s.get("mtg_end") else ""
                time_str = f"{start}-{end}" if start else ""
                table.add_row(
                    s["course_id"],
                    str(s["class_nbr"]),
                    s["section"],
                    s["component"],
                    s.get("day", ""),
                    time_str,
                )

            console.print(table)

            # Analyze components
            components = {}
            for s in sections:
                comp = s.get("component", "UNKNOWN")
                components[comp] = components.get(comp, 0) + 1

            rprint(f"[cyan]Component breakdown: {components}")

        return True

    except Exception as e:
        rprint(f"[red]✗ Parser failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_prerequisites_parser():
    """Test prerequisites Excel parser"""
    console.rule("[bold blue]Test: Prerequisites Parser")

    file_path = SAMPLE_DIR / "Pre-requisite_18-01-2025.xlsx"

    if not file_path.exists():
        rprint(f"[red]File not found: {file_path}")
        return False

    try:
        prerequisites = parse_prerequisites_excel(file_path)

        rprint(f"[green]✓ Parsed {len(prerequisites)} prerequisite records")

        # Show sample prerequisites
        if prerequisites:
            table = Table(title="Sample Prerequisites (first 10)")
            table.add_column("Course ID")
            table.add_column("Subject")
            table.add_column("Catalog")
            table.add_column("Prereq Value")
            table.add_column("Order")
            table.add_column("Type")
            table.add_column("Is Coreq")

            for p in prerequisites[:10]:
                table.add_row(
                    p["course_id"],
                    p["subject"],
                    p["catalog"],
                    str(p["prereq_value"])[:30] if p.get("prereq_value") else "",
                    str(p["prereq_order"]),
                    p["prereq_type"],
                    "Yes" if p.get("is_corequisite") else "No",
                )

            console.print(table)

        return True

    except Exception as e:
        rprint(f"[red]✗ Parser failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_registration_parser():
    """Test registration data Excel parser"""
    console.rule("[bold blue]Test: Registration Data Parser")

    file_path = SAMPLE_DIR / "BITS_REG_DATA_AUGSDP-291799.xlsx"

    if not file_path.exists():
        rprint(f"[red]File not found: {file_path}")
        return False

    try:
        registrations = parse_registration_excel(file_path)

        rprint(f"[green]✓ Parsed {len(registrations)} registration records")

        # Show sample registrations
        if registrations:
            table = Table(title="Sample Registrations (first 5)")
            table.add_column("Campus ID", style="cyan")
            table.add_column("Course ID")
            table.add_column("Subject")
            table.add_column("Section")
            table.add_column("Component")
            table.add_column("Class Nbr")

            for r in registrations[:5]:
                table.add_row(
                    r.campus_id,
                    r.course_id,
                    r.subject,
                    r.section,
                    r.component,
                    str(r.class_nbr),
                )

            console.print(table)

            # Analyze unique students
            unique_students = set(r.campus_id for r in registrations)
            unique_courses = set(r.course_id for r in registrations)
            rprint(f"[cyan]Unique students: {len(unique_students)}")
            rprint(f"[cyan]Unique courses: {len(unique_courses)}")

        return True

    except Exception as e:
        rprint(f"[red]✗ Parser failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all parser tests"""
    console.rule("[bold magenta]PARSER TESTS")

    results = {
        "Students Parser": test_students_parser(),
        "Courses Parser": test_courses_parser(),
        "Prerequisites Parser": test_prerequisites_parser(),
        "Registration Parser": test_registration_parser(),
    }

    console.rule("[bold magenta]RESULTS")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "[green]✓ PASS" if result else "[red]✗ FAIL"
        rprint(f"  {status}[/] {name}")

    rprint(f"\n[bold]Total: {passed}/{total} passed")

    return passed == total


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
