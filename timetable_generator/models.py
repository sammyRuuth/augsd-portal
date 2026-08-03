"""
Data models for the timetable generator.

Defines core data structures used throughout the system:
- Meeting: A single class meeting (day, time, room)
- Section: A course section with all its meetings
- Timetable: A complete timetable for a student group
- TimetableAssignment: Result of assigning students to timetables
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ComponentType(str, Enum):
    """Types of course components."""

    LEC = "LEC"  # Lecture
    TUT = "TUT"  # Tutorial
    LAB = "LAB"  # Laboratory
    PRO = "PRO"  # Project
    PRA = "PRA"  # Practical

    @property
    def is_strict(self) -> bool:
        """Whether this component has strict capacity limits."""
        return self in (ComponentType.LAB, ComponentType.PRO, ComponentType.PRA)

    @property
    def is_soft_strict(self) -> bool:
        """Whether this component can overfill as last resort."""
        return self == ComponentType.TUT

    @property
    def is_overfillable(self) -> bool:
        """Whether this component can always overfill."""
        return self == ComponentType.LEC

    @property
    def priority(self) -> int:
        """Scheduling priority (lower = schedule first)."""
        priorities = {
            ComponentType.LAB: 1,
            ComponentType.PRO: 1,
            ComponentType.PRA: 1,
            ComponentType.TUT: 2,
            ComponentType.LEC: 3,
        }
        return priorities.get(self, 4)


@dataclass(frozen=True, slots=True)
class Meeting:
    """A single class meeting time."""

    day: str  # Monday, Tuesday, etc.
    start: str  # HH:MM format
    end: str  # HH:MM format
    room: Optional[str] = None

    def __str__(self) -> str:
        room_str = f" @ {self.room}" if self.room else ""
        return f"{self.day[:3]} {self.start}-{self.end}{room_str}"

    @property
    def start_minutes(self) -> int:
        """Start time in minutes since midnight."""
        h, m = map(int, self.start.split(":"))
        return h * 60 + m

    @property
    def end_minutes(self) -> int:
        """End time in minutes since midnight."""
        h, m = map(int, self.end.split(":"))
        return h * 60 + m

    @property
    def duration_minutes(self) -> int:
        """Duration in minutes."""
        return self.end_minutes - self.start_minutes

    def overlaps_with(self, other: "Meeting") -> bool:
        """Check if this meeting overlaps with another on the same day."""
        if self.day != other.day:
            return False
        return not (
            self.end_minutes <= other.start_minutes
            or other.end_minutes <= self.start_minutes
        )


@dataclass
class Section:
    """A course section with all its meeting times."""

    course_code: str  # e.g., "MATH F101"
    course_id: str  # e.g., "002862"
    class_nbr: int  # Unique section identifier
    section: str  # e.g., "L1", "T2", "P3"
    component: ComponentType  # LEC, TUT, LAB, etc.
    title: str  # Course title
    capacity: int  # Maximum enrollment
    enrolled: int  # Current enrollment
    meetings: tuple[Meeting, ...] = field(default_factory=tuple)
    instructor: Optional[str] = None
    exam_date: Optional[str] = None
    exam_start: Optional[str] = None
    exam_end: Optional[str] = None

    def __post_init__(self):
        # Ensure meetings is a tuple for immutability
        if isinstance(self.meetings, list):
            object.__setattr__(self, "meetings", tuple(self.meetings))

    @property
    def available_seats(self) -> int:
        """Number of seats currently available."""
        return max(0, self.capacity - self.enrolled)

    @property
    def subject(self) -> str:
        """Subject code (e.g., 'MATH' from 'MATH F101')."""
        return (
            self.course_code.split()[0] if " " in self.course_code else self.course_code
        )

    @property
    def catalog(self) -> str:
        """Catalog number (e.g., 'F101' from 'MATH F101')."""
        parts = self.course_code.split()
        return parts[1] if len(parts) > 1 else ""

    @property
    def display_name(self) -> str:
        """Short display name for the section."""
        return f"{self.course_code} {self.component.value}-{self.section}"

    @property
    def unique_key(self) -> tuple:
        """Unique identifier for this section."""
        return (self.course_code, self.component, self.section, self.class_nbr)

    def clashes_with(self, other: "Section") -> bool:
        """Check if this section has any time clash with another."""
        for m1 in self.meetings:
            for m2 in other.meetings:
                if m1.overlaps_with(m2):
                    return True
        # Check exam clash
        if self.exam_date and other.exam_date:
            if self.exam_date == other.exam_date:
                return True
        return False

    def __hash__(self) -> int:
        return hash(self.class_nbr)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Section):
            return False
        return self.class_nbr == other.class_nbr


@dataclass
class Timetable:
    """A generated timetable with selected sections."""

    plan: str  # Student plan/branch name
    timetable_id: int  # Sequential ID within plan
    sections: list[Section] = field(default_factory=list)
    batch_size: int = 0  # Number of students assigned
    capacity_ceiling: int = 0  # Maximum students this could serve
    is_variant: bool = False  # Placeholder for mixing options

    @property
    def courses(self) -> set[str]:
        """Set of course codes in this timetable."""
        return {s.course_code for s in self.sections}

    @property
    def class_numbers(self) -> set[int]:
        """Set of class numbers in this timetable."""
        return {s.class_nbr for s in self.sections}

    def has_internal_clash(self) -> bool:
        """Check if any sections within this timetable clash."""
        sections = list(self.sections)
        for i in range(len(sections)):
            for j in range(i + 1, len(sections)):
                if sections[i].clashes_with(sections[j]):
                    return True
        return False

    def get_sections_by_course(self) -> dict[str, list[Section]]:
        """Group sections by course code."""
        result: dict[str, list[Section]] = {}
        for s in self.sections:
            if s.course_code not in result:
                result[s.course_code] = []
            result[s.course_code].append(s)
        return result

    def __str__(self) -> str:
        variant_str = " (variant)" if self.is_variant else ""
        return f"Timetable {self.timetable_id} for {self.plan}: {self.batch_size} students{variant_str}"


@dataclass
class TimetableAssignment:
    """Result of the timetable generation process."""

    plan: str
    student_count: int
    timetables: list[Timetable] = field(default_factory=list)
    students_assigned: int = 0
    unassigned_students: int = 0

    @property
    def assignment_rate(self) -> float:
        """Percentage of students successfully assigned."""
        if self.student_count == 0:
            return 100.0
        return (self.students_assigned / self.student_count) * 100

    @property
    def is_complete(self) -> bool:
        """Whether all students were assigned."""
        return self.unassigned_students == 0

    def __str__(self) -> str:
        status = (
            "Complete" if self.is_complete else f"{self.unassigned_students} unassigned"
        )
        return f"{self.plan}: {self.students_assigned}/{self.student_count} ({status})"


@dataclass
class GenerationResult:
    """Complete result of the generation process."""

    assignments: dict[str, TimetableAssignment] = field(default_factory=dict)
    capacity_usage: dict[int, int] = field(
        default_factory=dict
    )  # class_nbr -> students assigned
    strategy_used: str = ""
    fitness_score: float = 0.0

    @property
    def total_students_needed(self) -> int:
        """Total students across all plans."""
        return sum(a.student_count for a in self.assignments.values())

    @property
    def total_students_assigned(self) -> int:
        """Total students successfully assigned."""
        return sum(a.students_assigned for a in self.assignments.values())

    @property
    def overall_assignment_rate(self) -> float:
        """Overall assignment rate across all plans."""
        if self.total_students_needed == 0:
            return 100.0
        return (self.total_students_assigned / self.total_students_needed) * 100

    @property
    def all_timetables(self) -> list[Timetable]:
        """Flat list of all generated timetables."""
        result = []
        for assignment in self.assignments.values():
            result.extend(assignment.timetables)
        return result
