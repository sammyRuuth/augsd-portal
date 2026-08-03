"""
Comprehensive diagnostics for timetable generation.

Analyzes generation results to identify:
- Section utilization issues
- Capacity bottlenecks
- Time slot conflicts
- Suggestions for improvements
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from ..config import Config
from ..models import GenerationResult, Section


@dataclass
class SectionIssue:
    """An issue identified with a section."""

    section: Section
    issue_type: str  # "overfilled", "underutilized", "conflict", "bottleneck"
    severity: str  # "critical", "warning", "info"
    description: str
    suggestion: Optional[str] = None


@dataclass
class CapacityDeficit:
    """Capacity deficit for a course/component."""

    course_code: str
    component: str
    current_capacity: int
    required_capacity: int
    deficit: int
    affected_students: int


@dataclass
class TimeSlotAnalysis:
    """Analysis of time slot usage."""

    day: str
    slot: str  # "08:00-09:00"
    sections_count: int
    total_students: int
    utilization: float  # 0-1


@dataclass
class Suggestion:
    """A suggestion for improving allocation."""

    priority: str  # "high", "medium", "low"
    category: str  # "capacity", "scheduling", "structure"
    title: str
    description: str
    impact: str  # Estimated impact


@dataclass
class AnalyticsSummary:
    """Summary of all analytics."""

    # Overall metrics
    total_students: int = 0
    assigned_students: int = 0
    unassigned_students: int = 0
    assignment_rate: float = 0.0

    # Section metrics
    total_sections: int = 0
    overfilled_sections: int = 0
    underutilized_sections: int = 0

    # Issues and suggestions
    issues: list[SectionIssue] = field(default_factory=list)
    deficits: list[CapacityDeficit] = field(default_factory=list)
    time_slots: list[TimeSlotAnalysis] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)

    # Plan-level metrics
    plan_summaries: dict[str, dict] = field(default_factory=dict)


class TimetableAnalyzer:
    """
    Analyzes timetable generation results.

    Provides comprehensive diagnostics including:
    - Section utilization analysis
    - Capacity bottleneck identification
    - Time slot conflict detection
    - Actionable suggestions for improvement
    """

    def __init__(
        self,
        config: Config,
        sections_by_course: dict[str, list[Section]],
        result: GenerationResult,
        counts: dict[str, int],
        packages: Optional[dict[str, list[str]]] = None,
    ):
        """
        Initialize analyzer.

        Args:
            config: Configuration object
            sections_by_course: All available sections
            result: Generation result to analyze
            counts: Original student counts per plan
            packages: Package courses (if provided, filter analytics to these courses only)
        """
        self.config = config
        self.result = result
        self.counts = counts
        self.packages = packages
        self.all_sections_by_course = sections_by_course

        # Filter sections to only package courses if packages provided
        if packages:
            package_courses = self._get_package_courses(packages, sections_by_course)
            self.sections_by_course = {
                k: v for k, v in sections_by_course.items() if k in package_courses
            }
        else:
            self.sections_by_course = sections_by_course

    def _get_package_courses(
        self, packages: dict[str, list[str]], sections_by_course: dict[str, list[Section]]
    ) -> set[str]:
        """Extract all unique course codes from packages."""
        from ..parsers.packages import find_course_match

        courses = set()
        available = set(sections_by_course.keys())

        for course_list in packages.values():
            for course in course_list:
                course = course.strip()
                if course:
                    # Try to find matching course code
                    match = find_course_match(course, available)
                    if match:
                        courses.add(match)
                    else:
                        courses.add(course)
        return courses

    def analyze(self) -> AnalyticsSummary:
        """
        Run comprehensive analysis.

        Returns:
            AnalyticsSummary with all findings
        """
        summary = AnalyticsSummary()

        # Overall metrics
        summary.total_students = sum(self.counts.values())
        summary.assigned_students = self.result.total_students_assigned
        summary.unassigned_students = summary.total_students - summary.assigned_students
        summary.assignment_rate = self.result.overall_assignment_rate

        # Analyze sections
        self._analyze_sections(summary)

        # Analyze capacity
        self._analyze_capacity(summary)

        # Analyze time slots
        self._analyze_time_slots(summary)

        # Analyze plans
        self._analyze_plans(summary)

        # Generate suggestions
        self._generate_suggestions(summary)

        return summary

    def _analyze_sections(self, summary: AnalyticsSummary) -> None:
        """Analyze section utilization."""
        for course_code, sections in self.sections_by_course.items():
            for section in sections:
                summary.total_sections += 1

                usage = self.result.capacity_usage.get(section.class_nbr, 0)
                fill_ratio = usage / section.capacity if section.capacity > 0 else 0

                # Check for overfilling
                if fill_ratio > 1.0:
                    summary.overfilled_sections += 1
                    overfill_amount = usage - section.capacity
                    severity = "critical" if overfill_amount > 10 else "warning"

                    issue = SectionIssue(
                        section=section,
                        issue_type="overfilled",
                        severity=severity,
                        description=f"Section is overfilled by {overfill_amount} students ({fill_ratio:.0%} capacity)",
                        suggestion=f"Consider adding another {section.component.value} section for {course_code}",
                    )
                    summary.issues.append(issue)

                # Check for underutilization
                elif fill_ratio < 0.3 and usage > 0:
                    summary.underutilized_sections += 1
                    issue = SectionIssue(
                        section=section,
                        issue_type="underutilized",
                        severity="info",
                        description=f"Section is only {fill_ratio:.0%} utilized ({usage}/{section.capacity})",
                        suggestion="Consider consolidating with other sections or adjusting schedule",
                    )
                    summary.issues.append(issue)

    def _analyze_capacity(self, summary: AnalyticsSummary) -> None:
        """Analyze capacity deficits."""
        # Group usage by course and component
        course_component_usage: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"usage": 0, "capacity": 0, "sections": []}
        )

        for course_code, sections in self.sections_by_course.items():
            for section in sections:
                key = (course_code, section.component.value)
                usage = self.result.capacity_usage.get(section.class_nbr, 0)
                course_component_usage[key]["usage"] += usage
                course_component_usage[key]["capacity"] += section.capacity
                course_component_usage[key]["sections"].append(section)

        # Find deficits
        for (course_code, component), data in course_component_usage.items():
            if data["usage"] > data["capacity"]:
                deficit = data["usage"] - data["capacity"]
                summary.deficits.append(
                    CapacityDeficit(
                        course_code=course_code,
                        component=component,
                        current_capacity=data["capacity"],
                        required_capacity=data["usage"],
                        deficit=deficit,
                        affected_students=deficit,
                    )
                )

    def _analyze_time_slots(self, summary: AnalyticsSummary) -> None:
        """Analyze time slot usage patterns."""
        slot_usage: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"sections": 0, "students": 0}
        )

        for course_code, sections in self.sections_by_course.items():
            for section in sections:
                usage = self.result.capacity_usage.get(section.class_nbr, 0)
                for meeting in section.meetings:
                    # Round to hour slots
                    hour = meeting.start.split(":")[0]
                    slot = f"{hour}:00-{int(hour) + 1:02d}:00"
                    key = (meeting.day, slot)
                    slot_usage[key]["sections"] += 1
                    slot_usage[key]["students"] += usage

        # Convert to analysis objects
        for (day, slot), data in sorted(slot_usage.items()):
            # Estimate utilization (assume ~50 students per section is "full")
            utilization = (
                min(1.0, data["students"] / (data["sections"] * 50))
                if data["sections"] > 0
                else 0
            )

            summary.time_slots.append(
                TimeSlotAnalysis(
                    day=day,
                    slot=slot,
                    sections_count=data["sections"],
                    total_students=data["students"],
                    utilization=utilization,
                )
            )

    def _analyze_plans(self, summary: AnalyticsSummary) -> None:
        """Analyze per-plan metrics."""
        for plan, assignment in self.result.assignments.items():
            original_count = self.counts.get(plan, assignment.student_count)

            summary.plan_summaries[plan] = {
                "students_needed": original_count,
                "students_assigned": assignment.students_assigned,
                "unassigned": assignment.unassigned_students,
                "timetables_generated": len(assignment.timetables),
                "assignment_rate": assignment.assignment_rate,
                "is_complete": assignment.is_complete,
            }

    def _generate_suggestions(self, summary: AnalyticsSummary) -> None:
        """Generate actionable suggestions."""
        # High priority: Capacity deficits
        for deficit in sorted(summary.deficits, key=lambda d: d.deficit, reverse=True)[
            :5
        ]:
            (deficit.deficit // 40) + 1  # Assume 40 per section

            summary.suggestions.append(
                Suggestion(
                    priority="high",
                    category="capacity",
                    title=f"Add {deficit.component} section(s) for {deficit.course_code}",
                    description=(
                        f"Current capacity: {deficit.current_capacity}, "
                        f"Required: {deficit.required_capacity}. "
                        f"Deficit of {deficit.deficit} seats."
                    ),
                    impact=f"Would accommodate {deficit.affected_students} more students",
                )
            )

        # Medium priority: Highly overfilled specific sections
        critical_issues = [i for i in summary.issues if i.severity == "critical"]
        for issue in critical_issues[:3]:
            summary.suggestions.append(
                Suggestion(
                    priority="medium",
                    category="capacity",
                    title=f"Address overfilling in {issue.section.display_name}",
                    description=issue.description,
                    impact="Reduce section overcrowding and improve student experience",
                )
            )

        # Find underutilized time slots for new sections
        low_util_slots = [
            s
            for s in summary.time_slots
            if s.utilization < 0.3 and s.sections_count < 5
        ]
        if low_util_slots and summary.deficits:
            best_slots = sorted(low_util_slots, key=lambda s: s.utilization)[:3]
            slot_str = ", ".join(f"{s.day} {s.slot}" for s in best_slots)

            summary.suggestions.append(
                Suggestion(
                    priority="medium",
                    category="scheduling",
                    title="Schedule new sections in underutilized time slots",
                    description=f"These time slots have low utilization: {slot_str}",
                    impact="Better distribute student load across the week",
                )
            )

        # Low priority: Consolidation opportunities
        underutilized = [i for i in summary.issues if i.issue_type == "underutilized"]
        if len(underutilized) > 3:
            summary.suggestions.append(
                Suggestion(
                    priority="low",
                    category="structure",
                    title="Consider consolidating underutilized sections",
                    description=f"{len(underutilized)} sections are below 30% utilization",
                    impact="Reduce scheduling complexity and improve resource efficiency",
                )
            )

        # Check for plans with 0% assignment
        failed_plans = [
            p for p, s in summary.plan_summaries.items() if s["assignment_rate"] == 0
        ]
        if failed_plans:
            summary.suggestions.append(
                Suggestion(
                    priority="high",
                    category="structure",
                    title="Investigate plans with no assignments",
                    description=f"These plans have 0% assignment: {', '.join(failed_plans[:5])}",
                    impact="Ensure all student groups can be accommodated",
                )
            )


def find_bottleneck_sections(
    sections_by_course: dict[str, list[Section]],
    result: GenerationResult,
    threshold: float = 0.9,
) -> list[Section]:
    """
    Find sections that are bottlenecks (near or at capacity).

    Args:
        sections_by_course: Available sections
        result: Generation result
        threshold: Fill ratio threshold for bottleneck

    Returns:
        List of bottleneck sections
    """
    bottlenecks = []

    for sections in sections_by_course.values():
        for section in sections:
            usage = result.capacity_usage.get(section.class_nbr, 0)
            if section.capacity > 0:
                fill_ratio = usage / section.capacity
                if fill_ratio >= threshold:
                    bottlenecks.append(section)

    return sorted(
        bottlenecks,
        key=lambda s: result.capacity_usage.get(s.class_nbr, 0),
        reverse=True,
    )


def find_available_time_slots(
    sections_by_course: dict[str, list[Section]],
    days: list[str],
    hours: range = range(8, 18),
) -> list[tuple[str, str]]:
    """
    Find time slots with no or few sections scheduled.

    Args:
        sections_by_course: Available sections
        days: List of days to check
        hours: Hour range to check

    Returns:
        List of (day, time_slot) tuples that are available
    """
    # Count sections per slot
    slot_counts: dict[tuple[str, str], int] = defaultdict(int)

    for sections in sections_by_course.values():
        for section in sections:
            for meeting in section.meetings:
                try:
                    hour = int(meeting.start.split(":")[0])
                    slot = f"{hour:02d}:00-{hour + 1:02d}:00"
                    slot_counts[(meeting.day, slot)] += 1
                except (ValueError, IndexError):
                    continue

    # Find slots with low usage
    available = []
    for day in days:
        for hour in hours:
            slot = f"{hour:02d}:00-{hour + 1:02d}:00"
            if slot_counts.get((day, slot), 0) < 3:  # Less than 3 sections
                available.append((day, slot))

    return available
