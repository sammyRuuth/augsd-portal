"""Schemas for advanced timetable generation"""

import uuid
from datetime import date, time
from typing import Any

from pydantic import BaseModel, Field


class FixedSectionConstraint(BaseModel):
    """Constraint to fix a specific section for a course component"""

    course_id: uuid.UUID
    component: str  # e.g., "LEC", "TUT", "LAB"
    class_nbr: int  # The specific class number to use


class BlockedTimeSlot(BaseModel):
    """A time slot that should be blocked"""

    day: str  # e.g., "Monday", "Tuesday"
    start_time: time
    end_time: time


class AdvancedGenerationRequest(BaseModel):
    """Request for advanced timetable generation with constraints"""

    course_ids: list[uuid.UUID] = Field(..., min_length=1)
    algorithm: str = Field(
        default="backtrack_optimized",
        description="Algorithm to use: greedy, backtrack, backtrack_optimized, genetic",
    )
    max_units: float = Field(default=25.0, ge=0, le=50)
    fixed_sections: list[FixedSectionConstraint] = Field(default_factory=list)
    blocked_slots: list[BlockedTimeSlot] = Field(default_factory=list)
    # If true, run in background and return task ID
    async_mode: bool = Field(default=False)


class AlgorithmInfo(BaseModel):
    """Information about an available algorithm"""

    id: str
    name: str
    description: str


class ListAlgorithmsResponse(BaseModel):
    """Response listing available algorithms"""

    algorithms: list[AlgorithmInfo]


class GeneratedMeetingAdvanced(BaseModel):
    """A meeting in the generated timetable"""

    section_id: uuid.UUID
    course_id: uuid.UUID
    class_nbr: int
    subject: str
    catalog: str
    title: str
    component: str
    section: str
    day: str | None
    mtg_start: time | None
    mtg_end: time | None
    exam_date: date | None
    exam_start: time | None
    exam_end: time | None
    instructor: str | None
    room: str | None
    cap_enrl: int
    tot_enrl: int
    max_units: float


class AdvancedGenerationResult(BaseModel):
    """Result from advanced generation"""

    success: bool
    partial: bool = False
    meetings: list[GeneratedMeetingAdvanced] = Field(default_factory=list)
    section_ids: list[uuid.UUID] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    total_units: float = 0.0
    course_count: int = 0
    # Algorithm stats
    algorithm_name: str = ""
    execution_time_ms: float = 0.0
    iterations: int = 0
    message: str = ""


class AdvancedGenerationResponse(BaseModel):
    """Response for synchronous advanced generation"""

    success: bool
    result: AdvancedGenerationResult | None = None
    message: str = ""


class AsyncGenerationResponse(BaseModel):
    """Response for async generation (returns task ID)"""

    task_id: str
    status: str
    message: str


class TaskStatusResponse(BaseModel):
    """Response for task status query"""

    id: str
    session_id: str
    student_id: str
    algorithm: str
    status: str
    progress: float
    message: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    result: AdvancedGenerationResult | None = None
    error: str | None = None


class CourseComponentInfo(BaseModel):
    """Information about a course component (LEC, TUT, LAB)"""

    component: str
    sections: list["SectionOptionInfo"]


class SectionOptionInfo(BaseModel):
    """Information about a section option"""

    class_nbr: int
    section: str
    instructor: str | None
    room: str | None
    meeting_times: str  # Summary like "MW 10:00-10:50"
    exam_date: date | None
    exam_start: time | None
    exam_end: time | None
    cap_enrl: int
    tot_enrl: int
    available_seats: int
    seat_score: int


class CourseWithSectionsInfo(BaseModel):
    """Course information with all available sections"""

    course_id: uuid.UUID
    subject: str
    catalog: str
    title: str
    max_units: float
    components: list[CourseComponentInfo]


class CourseSectionsResponse(BaseModel):
    """Response with course sections for selection UI"""

    success: bool
    courses: list[CourseWithSectionsInfo]
    message: str = ""


# Update forward references
CourseComponentInfo.model_rebuild()
