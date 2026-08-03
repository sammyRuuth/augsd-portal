"""
Find available time slots for rescheduling a section.

Analyzes the timetable to find time slots where there are no conflicts
with courses from the packages JSON.

Usage:
    uv run python -m timetable_generator.tools.find_available_slots \
        --packages data/2025-2/defualt_packages_2025-2.json \
        --timetable data/2025-2/6-1-26/BITS_TIME_TABLE_ADJUSTED.xlsx \
        --section "MATH F113" --component "LEC" --section-name "L3" \
        --exclude-days Saturday,Sunday
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import load_config
from ..models import Meeting, Section
from ..parsers.excel import parse_excel_timetable
from ..parsers.packages import find_course_match


@dataclass
class TimeSlot:
    """Represents a time slot."""
    day: str
    start: str  # HH:MM
    end: str    # HH:MM

    @property
    def start_minutes(self) -> int:
        h, m = map(int, self.start.split(":"))
        return h * 60 + m

    @property
    def end_minutes(self) -> int:
        h, m = map(int, self.end.split(":"))
        return h * 60 + m

    def overlaps_with(self, other: "TimeSlot") -> bool:
        """Check if two time slots overlap on the same day."""
        if self.day != other.day:
            return False
        return not (
            self.end_minutes <= other.start_minutes
            or other.end_minutes <= self.start_minutes
        )

    def __str__(self) -> str:
        return f"{self.day} {self.start}-{self.end}"

    def __hash__(self) -> int:
        return hash((self.day, self.start, self.end))

    def __eq__(self, other) -> bool:
        if not isinstance(other, TimeSlot):
            return False
        return self.day == other.day and self.start == other.start and self.end == other.end


@dataclass
class SlotAnalysis:
    """Analysis of an available time slot."""
    slot: TimeSlot
    conflicts_with: list[str]  # List of conflicting courses if moved here
    is_free: bool
    notes: str = ""


def get_package_courses(packages_path: Path, available_courses: set[str]) -> set[str]:
    """Extract all unique course codes from packages JSON."""
    with open(packages_path) as f:
        packages_data = json.load(f)

    courses = set()

    # Handle nested structure (year -> plans -> courses)
    for year_data in packages_data.values():
        if isinstance(year_data, dict):
            for course_list in year_data.values():
                if isinstance(course_list, list):
                    for course in course_list:
                        course = course.strip()
                        if course:
                            match = find_course_match(course, available_courses)
                            if match:
                                courses.add(match)

    return courses


def get_all_time_slots_used(
    sections_by_course: dict[str, list[Section]],
    package_courses: set[str],
    target_course: Optional[str] = None,
) -> dict[str, list[tuple[TimeSlot, str]]]:
    """
    Get all time slots used by package courses.

    Returns:
        Dictionary of day -> list of (TimeSlot, course_code) tuples
    """
    slots_by_day: dict[str, list[tuple[TimeSlot, str]]] = defaultdict(list)

    for course_code in package_courses:
        if course_code not in sections_by_course:
            continue

        # Skip the target course (we're finding slots FOR it)
        if target_course and course_code == target_course:
            continue

        for section in sections_by_course[course_code]:
            for meeting in section.meetings:
                slot = TimeSlot(
                    day=meeting.day,
                    start=meeting.start,
                    end=meeting.end,
                )
                slots_by_day[meeting.day].append((slot, course_code))

    return slots_by_day


def generate_candidate_slots(
    days: list[str],
    start_hour: int = 8,
    end_hour: int = 18,
    slot_duration: int = 50,  # minutes
) -> list[TimeSlot]:
    """Generate all possible time slots for the given days."""
    candidates = []

    for day in days:
        current_minutes = start_hour * 60
        end_minutes = end_hour * 60

        while current_minutes + slot_duration <= end_minutes:
            start_h = current_minutes // 60
            start_m = current_minutes % 60
            end_time = current_minutes + slot_duration
            end_h = end_time // 60
            end_m = end_time % 60

            slot = TimeSlot(
                day=day,
                start=f"{start_h:02d}:{start_m:02d}",
                end=f"{end_h:02d}:{end_m:02d}",
            )
            candidates.append(slot)

            # Move to next slot (hourly intervals)
            current_minutes += 60

    return candidates


def find_available_slots(
    sections_by_course: dict[str, list[Section]],
    package_courses: set[str],
    target_course: str,
    target_component: str,
    target_section: str,
    excluded_days: list[str],
    start_hour: int = 8,
    end_hour: int = 18,
) -> tuple[Section, list[SlotAnalysis]]:
    """
    Find available time slots for rescheduling a section.

    Returns:
        Tuple of (target section, list of slot analyses)
    """
    # Find the target section
    target_sec = None
    if target_course in sections_by_course:
        for sec in sections_by_course[target_course]:
            if sec.component.value == target_component and sec.section == target_section:
                target_sec = sec
                break

    if not target_sec:
        raise ValueError(f"Section not found: {target_course} {target_component} {target_section}")

    # Get all package course slots (excluding target)
    slots_by_day = get_all_time_slots_used(
        sections_by_course,
        package_courses,
        target_course=target_course
    )

    # Determine days to check
    all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days_to_check = [d for d in all_days if d not in excluded_days]

    # Generate candidate slots
    candidates = generate_candidate_slots(
        days_to_check,
        start_hour=start_hour,
        end_hour=end_hour,
    )

    # Analyze each candidate
    analyses = []

    for candidate in candidates:
        conflicts = []

        # Check against all package course slots on this day
        for used_slot, course_code in slots_by_day.get(candidate.day, []):
            if candidate.overlaps_with(used_slot):
                conflicts.append(f"{course_code} ({used_slot.start}-{used_slot.end})")

        analysis = SlotAnalysis(
            slot=candidate,
            conflicts_with=conflicts,
            is_free=len(conflicts) == 0,
        )
        analyses.append(analysis)

    return target_sec, analyses


def find_multi_day_combinations(
    analyses: list[SlotAnalysis],
    required_days: list[str],
) -> list[tuple[str, list[SlotAnalysis]]]:
    """
    Find time slots that are free across multiple required days.

    Returns:
        List of (time_str, list of slot analyses for each day) tuples
    """
    # Group by time
    by_time: dict[str, dict[str, SlotAnalysis]] = defaultdict(dict)
    for a in analyses:
        time_str = f"{a.slot.start}-{a.slot.end}"
        by_time[time_str][a.slot.day] = a

    # Find times that have slots for all required days
    valid_combos = []
    for time_str, day_slots in by_time.items():
        if all(day in day_slots for day in required_days):
            slots = [day_slots[day] for day in required_days]
            # Check if all are free
            all_free = all(s.is_free for s in slots)
            # Count total conflicts
            total_conflicts = sum(len(s.conflicts_with) for s in slots)
            valid_combos.append((time_str, slots, all_free, total_conflicts))

    # Sort by: free first, then by total conflicts
    valid_combos.sort(key=lambda x: (not x[2], x[3]))

    return [(c[0], c[1]) for c in valid_combos]


def display_results(
    console: Console,
    target_section: Section,
    analyses: list[SlotAnalysis],
    show_all: bool = False,
) -> None:
    """Display the analysis results."""
    # Current timing
    console.print()
    console.print(Panel.fit(
        f"[bold blue]Finding Available Slots for[/bold blue]\n"
        f"[cyan]{target_section.display_name}[/cyan]",
        border_style="blue",
    ))

    console.print()
    console.print("[bold]Current Schedule:[/bold]")
    current_days = []
    current_time = None
    for meeting in target_section.meetings:
        console.print(f"  • {meeting}")
        current_days.append(meeting.day)
        if current_time is None:
            current_time = f"{meeting.start}-{meeting.end}"
    console.print()

    # Multi-day section analysis
    num_meeting_days = len(current_days)
    if num_meeting_days > 1:
        console.print(f"[dim]This section meets on {num_meeting_days} days: {', '.join(current_days)}[/dim]")
        console.print()

        # Find combinations for the same days
        console.print(f"[bold cyan]Looking for slots available on all {num_meeting_days} days ({', '.join(current_days)}):[/bold cyan]")
        console.print()

        combos = find_multi_day_combinations(analyses, current_days)

        if combos:
            # Show free combinations first
            free_combos = [(t, s) for t, s in combos if all(a.is_free for a in s)]
            partial_combos = [(t, s) for t, s in combos if not all(a.is_free for a in s)]

            if free_combos:
                console.print(f"[bold green]✓ Found {len(free_combos)} completely free time slot(s) for {'/'.join(d[:3] for d in current_days)}:[/bold green]")
                console.print()

                table = Table(title=f"Available Times for {'/'.join(d[:3] for d in current_days)}")
                table.add_column("Time", style="green")
                table.add_column("Status", style="green")

                for time_str, slots in free_combos:
                    table.add_row(time_str, "✓ FREE on all days")

                console.print(table)
                console.print()

            # Show alternatives with minimal conflicts
            if show_all and partial_combos:
                console.print(f"[bold yellow]Alternative times (with some conflicts):[/bold yellow]")
                console.print()

                table = Table(title="Alternatives with Conflicts")
                table.add_column("Time", style="yellow")
                for day in current_days:
                    table.add_column(day[:3], style="dim")

                for time_str, slots in partial_combos[:10]:
                    row = [time_str]
                    for slot in slots:
                        if slot.is_free:
                            row.append("[green]FREE[/green]")
                        else:
                            conflicts = ", ".join(c.split()[0] for c in slot.conflicts_with[:2])
                            if len(slot.conflicts_with) > 2:
                                conflicts += "..."
                            row.append(f"[red]{conflicts}[/red]")
                    table.add_row(*row)

                console.print(table)
                console.print()
        else:
            console.print("[yellow]No time slots found that work for all days[/yellow]")
            console.print()

    # Separate free and conflicting slots
    free_slots = [a for a in analyses if a.is_free]
    conflicting_slots = [a for a in analyses if not a.is_free]

    # Display individual free slots
    console.print("[bold]Individual Day Availability:[/bold]")
    console.print()

    if free_slots:
        console.print(f"[green]✓ Found {len(free_slots)} available individual time slots:[/green]")
        console.print()

        # Group by day
        by_day: dict[str, list[SlotAnalysis]] = defaultdict(list)
        for a in free_slots:
            by_day[a.slot.day].append(a)

        table = Table(title="Available Slots (No Package Course Conflicts)")
        table.add_column("Day", style="cyan")
        table.add_column("Available Times", style="green")

        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        for day in day_order:
            if day in by_day:
                times = ", ".join(f"{a.slot.start}-{a.slot.end}" for a in by_day[day])
                table.add_row(day, times)

        console.print(table)
    else:
        console.print("[bold red]✗ No completely free slots found[/bold red]")

    console.print()

    # Show slots with minimal conflicts
    if show_all or not free_slots:
        # Sort by number of conflicts
        conflicting_slots.sort(key=lambda a: len(a.conflicts_with))

        # Show slots with fewest conflicts
        low_conflict = [a for a in conflicting_slots if len(a.conflicts_with) <= 2]

        if low_conflict:
            console.print(f"[bold yellow]Slots with few conflicts ({len(low_conflict)} slots with ≤2 conflicts):[/bold yellow]")
            console.print()

            table = Table(title="Low-Conflict Alternatives")
            table.add_column("Day", style="cyan")
            table.add_column("Time", style="yellow")
            table.add_column("Conflicts With", style="red")

            for a in low_conflict[:15]:  # Show top 15
                conflicts_str = "\n".join(a.conflicts_with) if a.conflicts_with else "-"
                table.add_row(a.slot.day, f"{a.slot.start}-{a.slot.end}", conflicts_str)

            console.print(table)


def display_package_course_schedule(
    console: Console,
    sections_by_course: dict[str, list[Section]],
    package_courses: set[str],
    target_course: str,
) -> None:
    """Display the full schedule of package courses for reference."""
    console.print()
    console.rule("[bold blue]Package Courses Schedule (Reference)")

    all_slots: list[tuple[str, str, str, str]] = []  # (day, start, end, course_section)

    for course_code in sorted(package_courses):
        if course_code not in sections_by_course:
            continue
        if course_code == target_course:
            continue

        for section in sections_by_course[course_code]:
            for meeting in section.meetings:
                all_slots.append((
                    meeting.day,
                    meeting.start,
                    meeting.end,
                    f"{course_code} {section.component.value}-{section.section}",
                ))

    # Group by day
    by_day: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for day, start, end, course_section in all_slots:
        by_day[day].append((start, end, course_section))

    # Sort by start time
    for day in by_day:
        by_day[day].sort()

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for day in day_order:
        if day not in by_day:
            continue

        console.print(f"\n[bold cyan]{day}[/bold cyan]")
        table = Table(show_header=True, box=None)
        table.add_column("Time", style="dim")
        table.add_column("Course Section")

        for start, end, course_section in by_day[day]:
            table.add_row(f"{start}-{end}", course_section)

        console.print(table)


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        description="Find available time slots for rescheduling a section.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--packages",
        type=Path,
        required=True,
        help="Path to packages JSON file",
    )
    parser.add_argument(
        "--timetable",
        type=Path,
        required=True,
        help="Path to timetable Excel file",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--course",
        type=str,
        required=True,
        help="Course code (e.g., 'MATH F113')",
    )
    parser.add_argument(
        "--component",
        type=str,
        default="LEC",
        help="Component type (default: LEC)",
    )
    parser.add_argument(
        "--section-name",
        type=str,
        required=True,
        help="Section name (e.g., 'L3')",
    )
    parser.add_argument(
        "--exclude-days",
        type=str,
        default="Saturday,Sunday",
        help="Comma-separated list of days to exclude (default: Saturday,Sunday)",
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=8,
        help="Start hour for candidate slots (default: 8)",
    )
    parser.add_argument(
        "--end-hour",
        type=int,
        default=18,
        help="End hour for candidate slots (default: 18)",
    )
    parser.add_argument(
        "--show-schedule",
        action="store_true",
        help="Show full package courses schedule",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all slots including those with conflicts",
    )

    return parser


def main(args: list[str] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    opts = parser.parse_args(args)

    console = Console()

    # Validate files
    if not opts.packages.exists():
        console.print(f"[red]Error: Packages file not found: {opts.packages}[/red]")
        return 1
    if not opts.timetable.exists():
        console.print(f"[red]Error: Timetable file not found: {opts.timetable}[/red]")
        return 1

    # Load config
    config = load_config(opts.config)

    # Parse excluded days
    excluded_days = [d.strip() for d in opts.exclude_days.split(",")] if opts.exclude_days else []

    console.print("[cyan]Loading timetable data...[/cyan]")

    # Parse timetable
    sections_by_course, _ = parse_excel_timetable(opts.timetable, config)

    # Get package courses
    available_courses = set(sections_by_course.keys())
    package_courses = get_package_courses(opts.packages, available_courses)

    console.print(f"[dim]Found {len(package_courses)} package courses[/dim]")
    console.print(f"[dim]Excluding days: {', '.join(excluded_days) if excluded_days else 'None'}[/dim]")

    try:
        # Find available slots
        target_section, analyses = find_available_slots(
            sections_by_course=sections_by_course,
            package_courses=package_courses,
            target_course=opts.course,
            target_component=opts.component,
            target_section=opts.section_name,
            excluded_days=excluded_days,
            start_hour=opts.start_hour,
            end_hour=opts.end_hour,
        )

        # Display results
        display_results(console, target_section, analyses, show_all=opts.show_all)

        # Optionally show full schedule
        if opts.show_schedule:
            display_package_course_schedule(
                console,
                sections_by_course,
                package_courses,
                opts.course,
            )

        return 0

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
