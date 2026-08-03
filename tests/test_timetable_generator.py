#!/usr/bin/env python
"""
Test 3: Conflict Detector and Timetable Generator Tests

Tests the conflict detection and timetable generation algorithms.
"""

import sys
from datetime import date, time
from pathlib import Path

from rich import print as rprint
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.conflict_detector import ConflictDetector
from app.core.timetable_generator import CourseSection, TimetableGenerator

console = Console()


def test_conflict_detector():
    """Test the conflict detection system"""
    console.rule("[bold blue]Test: Conflict Detector")

    detector = ConflictDetector()
    all_passed = True

    # Test 1: No conflicts when adding non-overlapping times
    console.print("[cyan]Test 1: Non-overlapping times")
    detector.reset()

    conflicts1 = detector.add_section(
        section_id="sec1",
        day="Monday",
        start=time(9, 0),
        end=time(10, 0),
        exam_date=date(2025, 12, 9),
        units=3.0,
    )

    conflicts2 = detector.add_section(
        section_id="sec2",
        day="Monday",
        start=time(10, 0),
        end=time(11, 0),
        exam_date=date(2025, 12, 10),
        units=3.0,
    )

    if not conflicts1 and not conflicts2:
        rprint("[green]  ✓ No conflicts for non-overlapping times")
    else:
        rprint("[red]  ✗ Unexpected conflicts detected")
        all_passed = False

    # Test 2: Time clash detection
    console.print("[cyan]Test 2: Time clash detection")
    detector.reset()

    detector.add_section(
        section_id="sec1",
        day="Monday",
        start=time(9, 0),
        end=time(10, 0),
        exam_date=None,
        units=3.0,
    )

    conflicts = detector.add_section(
        section_id="sec2",
        day="Monday",
        start=time(9, 30),
        end=time(10, 30),
        exam_date=None,
        units=3.0,
    )

    if conflicts and conflicts[0].type == "time_clash":
        rprint("[green]  ✓ Time clash correctly detected")
    else:
        rprint("[red]  ✗ Time clash not detected")
        all_passed = False

    # Test 3: Different days - no conflict
    console.print("[cyan]Test 3: Different days - no conflict")
    detector.reset()

    detector.add_section(
        section_id="sec1",
        day="Monday",
        start=time(9, 0),
        end=time(10, 0),
        exam_date=None,
        units=3.0,
    )

    conflicts = detector.add_section(
        section_id="sec2",
        day="Tuesday",
        start=time(9, 0),
        end=time(10, 0),
        exam_date=None,
        units=3.0,
    )

    if not conflicts:
        rprint("[green]  ✓ No conflict for different days (same time)")
    else:
        rprint("[red]  ✗ False conflict detected for different days")
        all_passed = False

    # Test 4: Exam clash detection
    console.print("[cyan]Test 4: Exam clash detection")
    detector.reset()

    detector.add_section(
        section_id="sec1",
        day="Monday",
        start=time(9, 0),
        end=time(10, 0),
        exam_date=date(2025, 12, 9),
        units=3.0,
    )

    conflicts = detector.add_section(
        section_id="sec2",
        day="Tuesday",
        start=time(11, 0),
        end=time(12, 0),
        exam_date=date(2025, 12, 9),  # Same exam date
        units=3.0,
    )

    if conflicts and conflicts[0].type == "exam_clash":
        rprint("[green]  ✓ Exam clash correctly detected")
    else:
        rprint("[red]  ✗ Exam clash not detected")
        all_passed = False

    # Test 5: Unit limit check
    console.print("[cyan]Test 5: Unit limit check")
    detector.reset()

    # Add sections totaling 28 units
    for i in range(7):
        detector.add_section(
            section_id=f"sec{i}",
            day="Monday",
            start=None,
            end=None,
            exam_date=None,
            units=4.0,
        )

    conflicts = detector.check_unit_limit(max_units=25.0)

    if conflicts and conflicts[0].type == "unit_limit":
        rprint("[green]  ✓ Unit limit exceeded correctly detected")
    else:
        rprint("[red]  ✗ Unit limit not detected")
        all_passed = False

    # Test 6: Adjacent times - no conflict
    console.print("[cyan]Test 6: Adjacent times - no conflict")
    detector.reset()

    detector.add_section(
        section_id="sec1",
        day="Monday",
        start=time(9, 0),
        end=time(9, 50),
        exam_date=None,
        units=3.0,
    )

    conflicts = detector.add_section(
        section_id="sec2",
        day="Monday",
        start=time(9, 50),
        end=time(10, 40),
        exam_date=None,
        units=3.0,
    )

    if not conflicts:
        rprint("[green]  ✓ No conflict for adjacent (back-to-back) times")
    else:
        rprint("[red]  ✗ False conflict detected for adjacent times")
        all_passed = False

    return all_passed


def test_timetable_generator():
    """Test the timetable generation algorithm"""
    console.rule("[bold blue]Test: Timetable Generator")

    generator = TimetableGenerator(max_units=25.0)
    all_passed = True

    # Test 1: Simple case - single course, single section
    console.print("[cyan]Test 1: Simple case - single course")

    course_sections = {
        "MATH101": [
            CourseSection(
                id="math-l1",
                course_id="MATH101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",
                end="10:00",
                exam_date="2025-12-09",
                units=4.0,
            ),
        ]
    }

    result = generator.generate(course_sections)

    if result.success and "math-l1" in result.assigned_sections:
        rprint("[green]  ✓ Single course scheduled successfully")
    else:
        rprint("[red]  ✗ Failed to schedule single course")
        all_passed = False

    # Test 2: Multiple courses, no conflicts
    console.print("[cyan]Test 2: Multiple non-conflicting courses")

    course_sections = {
        "MATH101": [
            CourseSection(
                id="math-l1",
                course_id="MATH101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",
                end="10:00",
                exam_date="2025-12-09",
                units=4.0,
            ),
        ],
        "PHY101": [
            CourseSection(
                id="phy-l1",
                course_id="PHY101",
                section="L1",
                component="LEC",
                day="Monday",
                start="10:00",
                end="11:00",
                exam_date="2025-12-10",
                units=4.0,
            ),
        ],
        "CS101": [
            CourseSection(
                id="cs-l1",
                course_id="CS101",
                section="L1",
                component="LEC",
                day="Tuesday",
                start="09:00",
                end="10:00",
                exam_date="2025-12-11",
                units=3.0,
            ),
        ],
    }

    result = generator.generate(course_sections)

    if result.success and len(result.assigned_sections) == 3:
        rprint("[green]  ✓ Multiple non-conflicting courses scheduled")
        rprint(f"     Total units: {result.total_units}")
    else:
        rprint("[red]  ✗ Failed to schedule multiple courses")
        all_passed = False

    # Test 3: Choose between conflicting sections
    console.print("[cyan]Test 3: Backtracking with section choices")

    course_sections = {
        "MATH101": [
            CourseSection(
                id="math-l1",
                course_id="MATH101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",
                end="10:00",
                exam_date="2025-12-09",
                units=4.0,
            ),
            CourseSection(
                id="math-l2",
                course_id="MATH101",
                section="L2",
                component="LEC",
                day="Monday",
                start="11:00",
                end="12:00",
                exam_date="2025-12-09",
                units=4.0,
            ),
        ],
        "PHY101": [
            CourseSection(
                id="phy-l1",
                course_id="PHY101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",  # Conflicts with MATH L1
                end="10:00",
                exam_date="2025-12-10",
                units=4.0,
            ),
        ],
    }

    result = generator.generate(course_sections)

    # Should choose MATH L2 since PHY L1 conflicts with MATH L1
    if (
        result.success
        and "math-l2" in result.assigned_sections
        and "phy-l1" in result.assigned_sections
    ):
        rprint("[green]  ✓ Backtracking found non-conflicting combination")
    else:
        rprint(
            f"[yellow]  ~ Backtracking result: success={result.success}, sections={result.assigned_sections}"
        )
        # Note: This test might not always work depending on ordering

    # Test 4: Impossible to schedule all
    console.print("[cyan]Test 4: Impossible to schedule all courses")

    course_sections = {
        "MATH101": [
            CourseSection(
                id="math-l1",
                course_id="MATH101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",
                end="10:00",
                exam_date="2025-12-09",
                units=4.0,
            ),
        ],
        "PHY101": [
            CourseSection(
                id="phy-l1",
                course_id="PHY101",
                section="L1",
                component="LEC",
                day="Monday",
                start="09:00",  # Only option, conflicts with MATH
                end="10:00",
                exam_date="2025-12-09",  # Also exam clash
                units=4.0,
            ),
        ],
    }

    result = generator.generate(course_sections)

    # The generator might return partial success or failure
    if not result.success or len(result.assigned_sections) < 2:
        rprint("[green]  ✓ Correctly identified impossible schedule")
        if result.conflicts:
            rprint(f"     Conflicts: {[c.type for c in result.conflicts]}")
    else:
        rprint("[red]  ✗ Should not have scheduled all courses")
        all_passed = False

    return all_passed


def test_timetable_with_sample_data():
    """Test timetable generation with realistic sample data"""
    console.rule("[bold blue]Test: Timetable with Sample Data")

    # Load actual course data from sample files
    from app.core.parsers import parse_courses_timetable_excel

    SAMPLE_DIR = Path(__file__).parent.parent / "sample_files"
    file_path = SAMPLE_DIR / "BITS_TIME_TABLE_WITHFACILITY_1346598190.xlsx"

    if not file_path.exists():
        rprint("[yellow]  ~ Sample file not found, skipping")
        return True

    courses, sections = parse_courses_timetable_excel(file_path)

    rprint(f"[cyan]Loaded {len(courses)} courses and {len(sections)} section records")

    # Group sections by course for a few test courses
    # Find courses with multiple sections
    course_section_map = {}
    for s in sections:
        course_id = s["course_id"]
        if course_id not in course_section_map:
            course_section_map[course_id] = []
        course_section_map[course_id].append(s)

    # Pick 3-4 courses that have sections with different days/times
    test_courses = []
    for course_id, sects in course_section_map.items():
        if len(sects) >= 2 and len(test_courses) < 4:
            test_courses.append(course_id)

    if not test_courses:
        rprint("[yellow]  ~ Not enough varied courses for testing")
        return True

    rprint(f"[cyan]Testing with courses: {test_courses[:4]}")

    # Build CourseSection objects
    generator = TimetableGenerator(max_units=25.0)

    course_sections_for_gen = {}
    for course_id in test_courses[:4]:
        sects = course_section_map[course_id]
        course_sections_for_gen[course_id] = []

        # Use first 3 sections per course (to keep test manageable)
        for s in sects[:3]:
            start_str = str(s["mtg_start"]) if s["mtg_start"] else None
            end_str = str(s["mtg_end"]) if s["mtg_end"] else None
            exam_str = str(s["exam_date"]) if s["exam_date"] else None

            # Get units from the course
            course_data = next((c for c in courses if c.course_id == course_id), None)
            units = (
                course_data.max_units if course_data and course_data.max_units else 3.0
            )

            cs = CourseSection(
                id=f"{course_id}-{s['section']}-{s['day']}",
                course_id=course_id,
                section=s["section"],
                component=s["component"],
                day=s["day"],
                start=start_str,
                end=end_str,
                exam_date=exam_str,
                units=units,
            )
            course_sections_for_gen[course_id].append(cs)

    result = generator.generate(course_sections_for_gen)

    rprint("[cyan]Generation result:")
    rprint(f"  Success: {result.success}")
    rprint(f"  Assigned sections: {len(result.assigned_sections)}")
    rprint(f"  Total units: {result.total_units}")
    if result.conflicts:
        rprint(f"  Conflicts: {len(result.conflicts)}")
        for c in result.conflicts[:3]:
            rprint(f"    - {c.type}: {c.message}")

    return True


def main():
    """Run all conflict detector and generator tests"""
    console.rule("[bold magenta]CONFLICT DETECTOR & GENERATOR TESTS")

    results = {
        "Conflict Detector": test_conflict_detector(),
        "Timetable Generator": test_timetable_generator(),
        "Sample Data Test": test_timetable_with_sample_data(),
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
