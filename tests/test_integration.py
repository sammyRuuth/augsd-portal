#!/usr/bin/env python
"""
Test 4: Full Integration Test

Tests the complete workflow:
1. Parse sample files
2. Simulate database operations
3. Generate timetables for students
4. Test export functionality
"""

import json
import sys
from pathlib import Path

import pytest
from rich import print as rprint
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.branch_extractor import extract_branch_info
from app.core.parsers import (
    parse_courses_timetable_excel,
    parse_students_excel,
)
from app.core.timetable_generator import CourseSection, TimetableGenerator

console = Console()

SAMPLE_DIR = Path(__file__).parent.parent / "sample_files"


class MockDatabase:
    """In-memory mock database for testing without PostgreSQL"""

    def __init__(self):
        self.students = {}  # campus_id -> student data
        self.courses = {}  # course_id -> course data
        self.sections = {}  # (course_id, section, component) -> section data
        self.timetables = {}  # campus_id -> list of sections
        self.registrations = []  # registration records

    def add_students(self, students_data):
        """Add parsed students to mock database"""
        for s in students_data:
            self.students[s.campus_id] = {
                "student_id": s.student_id,
                "campus_id": s.campus_id,
                "name": s.name,
                "email": s.email,
            }
        return len(students_data)

    def add_courses_and_sections(self, courses_data, sections_data):
        """Add parsed courses and sections"""
        for c in courses_data:
            self.courses[c.course_id] = {
                "course_id": c.course_id,
                "subject": c.subject,
                "catalog": c.catalog,
                "title": c.title,
                "max_units": c.max_units,
            }

        for s in sections_data:
            key = (s["course_id"], s["section"], s["component"], s["day"])
            self.sections[key] = s

        return len(courses_data), len(sections_data)

    def get_sections_for_course(self, course_id):
        """Get all sections for a course ID"""
        result = []
        for key, section in self.sections.items():
            if key[0] == course_id:
                result.append(section)
        return result

    def get_student_by_campus_id(self, campus_id):
        """Get student by campus ID"""
        return self.students.get(campus_id)

    def find_course_by_subject_catalog(self, subject, catalog):
        """Find course by subject and catalog"""
        for course_id, course in self.courses.items():
            if (
                course["subject"] == subject
                and course["catalog"].strip() == catalog.strip()
            ):
                return course
        return None

    def commit_timetable(self, campus_id, section_ids):
        """Save committed timetable"""
        self.timetables[campus_id] = section_ids

    def get_timetable(self, campus_id):
        """Get committed timetable for student"""
        return self.timetables.get(campus_id, [])


def load_sample_data(db: MockDatabase):
    """Load all sample data into mock database"""
    console.rule("[bold blue]Loading Sample Data")

    # Load students
    students_file = SAMPLE_DIR / "BITS_ACTIVE_STUDENTS_ONROLL_431328192.xlsx"
    if students_file.exists():
        students = parse_students_excel(students_file)
        count = db.add_students(students)
        rprint(f"[green]✓ Loaded {count} students")
    else:
        rprint("[yellow]  ~ Students file not found")

    # Load courses and sections
    courses_file = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"
    if courses_file.exists():
        courses, sections = parse_courses_timetable_excel(courses_file)
        c_count, s_count = db.add_courses_and_sections(courses, sections)
        rprint(f"[green]✓ Loaded {c_count} courses and {s_count} section records")
    else:
        rprint("[yellow]  ~ Courses file not found")

    # Load default packages
    packages_file = SAMPLE_DIR / "defualt_packages.json"
    if packages_file.exists():
        with open(packages_file) as f:
            db.default_packages = json.load(f)
        rprint("[green]✓ Loaded default packages")
    else:
        db.default_packages = {}
        rprint("[yellow]  ~ Default packages file not found")

    return True


@pytest.fixture(scope="module")
def db() -> MockDatabase:
    """Provide a populated mock database for integration-style tests."""
    mock_db = MockDatabase()
    load_sample_data(mock_db)
    return mock_db


def test_student_workflow(db: MockDatabase):
    """Test the student timetable generation workflow"""
    console.rule("[bold blue]Test: Student Timetable Workflow")

    # Pick a 2025 student to test default packages
    test_student = None
    for campus_id, student in db.students.items():
        info = extract_branch_info(campus_id)
        if info.get("year") == 2024:  # Use 2024 since we have students
            test_student = student
            test_student["branch_info"] = info
            break

    if not test_student:
        # Fall back to any student
        test_student = next(iter(db.students.values()), None)
        if test_student:
            test_student["branch_info"] = extract_branch_info(test_student["campus_id"])

    if not test_student:
        rprint("[red]✗ No students found in database")
        return False

    rprint(
        f"[cyan]Testing with student: {test_student['name']} ({test_student['campus_id']})"
    )

    # Step 1: Extract branch info
    info = test_student["branch_info"]
    rprint(
        f"[cyan]  Branch info: year={info.get('year')}, branches={info.get('branches')}"
    )

    # Step 2: Get default courses for this student (if applicable)
    year = info.get("year")
    branches = info.get("branches", [])

    default_courses = []
    if db.default_packages and str(year) in db.default_packages:
        year_packages = db.default_packages[str(year)]
        for branch_group, courses in year_packages.items():
            group_branches = [b.strip() for b in branch_group.split(",")]
            if any(b in group_branches for b in branches):
                default_courses = courses
                break

    if default_courses:
        rprint(
            f"[cyan]  Default courses for {year}/{branches}: {default_courses[:3]}..."
        )
    else:
        rprint(f"[cyan]  No default package for {year}/{branches}")

    # Step 3: Select some courses for timetable generation
    # If no default courses, pick some available courses
    selected_courses = []

    if default_courses:
        # Try to find these courses in the database
        for course_name in default_courses[:5]:
            parts = course_name.split()
            if len(parts) >= 2:
                subject, catalog = parts[0], parts[1]
                course = db.find_course_by_subject_catalog(subject, catalog)
                if course:
                    selected_courses.append(course["course_id"])

    if not selected_courses:
        # Fall back to first 4 courses with sections
        seen_courses = set()
        for key in db.sections.keys():
            course_id = key[0]
            if course_id not in seen_courses:
                seen_courses.add(course_id)
                selected_courses.append(course_id)
                if len(selected_courses) >= 4:
                    break

    rprint(f"[cyan]  Selected courses for generation: {selected_courses}")

    # Step 4: Build CourseSection objects for timetable generation
    course_sections = {}
    for course_id in selected_courses:
        sections = db.get_sections_for_course(course_id)
        if not sections:
            continue

        course_data = db.courses.get(course_id, {})
        units = course_data.get("max_units", 3.0) or 3.0

        course_sections[course_id] = []
        # Deduplicate sections (same section number, different days)
        seen = set()
        for s in sections[:10]:  # Limit to 10 sections per course
            section_key = (s["section"], s["component"])
            if section_key in seen:
                continue
            seen.add(section_key)

            start_str = str(s["mtg_start"]) if s.get("mtg_start") else None
            end_str = str(s["mtg_end"]) if s.get("mtg_end") else None
            exam_str = str(s["exam_date"]) if s.get("exam_date") else None

            cs = CourseSection(
                id=f"{course_id}-{s['section']}-{s['component']}-{s['day']}",
                course_id=course_id,
                section=s["section"],
                component=s["component"],
                day=s.get("day", ""),
                start=start_str,
                end=end_str,
                exam_date=exam_str,
                units=units,
            )
            course_sections[course_id].append(cs)

    if not course_sections:
        rprint("[red]✗ No sections found for selected courses")
        return False

    rprint(
        f"[cyan]  Prepared {len(course_sections)} courses with sections for generation"
    )

    # Step 5: Generate timetable
    generator = TimetableGenerator(max_units=25.0)
    result = generator.generate(course_sections)

    rprint("[cyan]  Generation result:")
    rprint(f"    Success: {result.success}")
    rprint(f"    Assigned: {len(result.assigned_sections)} sections")
    rprint(f"    Total units: {result.total_units}")

    if result.conflicts:
        rprint(f"    Conflicts: {len(result.conflicts)}")
        for c in result.conflicts[:3]:
            rprint(f"      - {c.type}: {c.message}")

    # Step 6: Commit timetable (simulate)
    if result.assigned_sections:
        db.commit_timetable(test_student["campus_id"], result.assigned_sections)
        rprint(f"[green]✓ Timetable committed for {test_student['campus_id']}")

    # Step 7: Verify committed timetable
    committed = db.get_timetable(test_student["campus_id"])
    if committed:
        rprint(f"[green]✓ Verified committed timetable: {len(committed)} sections")

    return True


def test_export_workflow(db: MockDatabase):
    """Test the export workflow"""
    console.rule("[bold blue]Test: Export Workflow")

    # Check how many committed timetables we have
    committed_count = len(db.timetables)
    rprint(f"[cyan]Committed timetables: {committed_count}")

    if committed_count == 0:
        rprint("[yellow]  ~ No committed timetables to export")
        return True

    # Simulate export format (Campus ID, Career, Term, Class Nbr)
    export_data = []

    for campus_id, section_ids in db.timetables.items():
        for section_id in section_ids:
            # Parse section_id to get class_nbr (format: course_id-section-component-day)
            parts = section_id.split("-")
            if len(parts) >= 2:
                course_id = parts[0]
                section = parts[1]

                # Find the section's class_nbr
                for key, section_data in db.sections.items():
                    if key[0] == course_id and key[1] == section:
                        export_data.append(
                            {
                                "campus_id": campus_id,
                                "career": "FD",  # Default
                                "term": "1163",  # Default
                                "class_nbr": section_data.get("class_nbr", 0),
                            }
                        )
                        break

    if export_data:
        rprint(f"[green]✓ Generated export with {len(export_data)} records")

        # Show sample
        table = Table(title="Sample Export (first 5)")
        table.add_column("Campus ID")
        table.add_column("Career")
        table.add_column("Term")
        table.add_column("Class Nbr")

        for row in export_data[:5]:
            table.add_row(
                row["campus_id"],
                row["career"],
                row["term"],
                str(row["class_nbr"]),
            )

        console.print(table)
    else:
        rprint("[yellow]  ~ No export data generated")

    return True


def test_statistics(db: MockDatabase):
    """Test course statistics generation"""
    console.rule("[bold blue]Test: Course Statistics")

    # Calculate enrollment statistics per course
    course_stats = {}

    for key, section in db.sections.items():
        course_id = key[0]
        if course_id not in course_stats:
            course_data = db.courses.get(course_id, {})
            course_stats[course_id] = {
                "course_id": course_id,
                "subject": course_data.get("subject", ""),
                "catalog": course_data.get("catalog", ""),
                "title": course_data.get("title", ""),
                "sections": 0,
                "total_capacity": 0,
                "total_enrollment": 0,
                "components": set(),
            }

        course_stats[course_id]["sections"] += 1
        course_stats[course_id]["total_capacity"] += section.get("cap_enrl", 0) or 0
        course_stats[course_id]["total_enrollment"] += section.get("tot_enrl", 0) or 0
        course_stats[course_id]["components"].add(section.get("component", ""))

    # Show top courses by sections
    top_courses = sorted(
        course_stats.values(), key=lambda x: x["sections"], reverse=True
    )[:10]

    table = Table(title="Top 10 Courses by Sections")
    table.add_column("Course ID")
    table.add_column("Subject")
    table.add_column("Catalog")
    table.add_column("Sections", justify="right")
    table.add_column("Capacity", justify="right")
    table.add_column("Components")

    for c in top_courses:
        table.add_row(
            c["course_id"],
            c["subject"],
            c["catalog"],
            str(c["sections"]),
            str(c["total_capacity"]),
            ", ".join(sorted(c["components"])),
        )

    console.print(table)

    # Calculate overfilled (not applicable with sample data since tot_enrl is usually 0)
    overfilled = [
        c
        for c in course_stats.values()
        if c["total_enrollment"] > c["total_capacity"] > 0
    ]

    rprint(f"[cyan]Overfilled courses: {len(overfilled)}")

    return True


def main():
    """Run full integration test"""
    console.rule("[bold magenta]FULL INTEGRATION TEST")

    # Create mock database
    db = MockDatabase()

    results = {}

    # Load data
    results["Load Data"] = load_sample_data(db)

    # Test student workflow
    results["Student Workflow"] = test_student_workflow(db)

    # Test export
    results["Export Workflow"] = test_export_workflow(db)

    # Test statistics
    results["Statistics"] = test_statistics(db)

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
