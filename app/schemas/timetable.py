"""Timetable Pydantic schemas"""

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class TimetableCreate(BaseModel):
    """Schema for creating a new timetable"""

    student_id: uuid.UUID
    course_section_ids: list[uuid.UUID] = Field(..., min_length=1)


class TimetableItemResponse(BaseModel):
    """Schema for timetable item response"""

    id: uuid.UUID
    course_section_id: uuid.UUID
    course_section: "CourseSectionWithCourse"  # Includes course details

    class Config:
        from_attributes = True


class CourseSectionWithCourse(BaseModel):
    """Course section with embedded course info for timetable display"""

    id: uuid.UUID
    course_id: uuid.UUID
    class_nbr: int
    section: str
    component: str
    day: str | None = None
    class_pattern: str | None = None
    mtg_start: time | None = None
    mtg_end: time | None = None
    exam_date: date | None = None
    exam_start: time | None = None
    exam_end: time | None = None
    instructor: str | None = None
    room: str | None = None
    cap_enrl: int | None = None
    tot_enrl: int = 0
    course: "CourseBasicInfo | None" = None

    class Config:
        from_attributes = True


class CourseBasicInfo(BaseModel):
    """Basic course info for embedding in timetable items"""

    id: uuid.UUID
    course_id: str
    subject: str
    catalog: str
    title: str
    max_units: float | None = None

    class Config:
        from_attributes = True


class TimetableResponse(BaseModel):
    """Schema for timetable response"""

    id: uuid.UUID
    student_id: uuid.UUID
    source: str = Field(
        ..., description="Source: 'portal_generated' or 'from_registration'"
    )
    status: str = Field(..., description="Status: 'draft', 'committed', or 'edited'")
    created_at: datetime
    created_by_id: uuid.UUID
    total_units: float | None
    updated_at: datetime | None = None
    updated_by_id: uuid.UUID | None = None
    items: list[TimetableItemResponse]

    class Config:
        from_attributes = True


class SeatPreferences(BaseModel):
    """Preferences for section seat availability"""

    prefer_lab_seats: bool = Field(
        default=True,
        description="Prefer LAB sections with available seats (highest priority)",
    )
    prefer_tut_seats: bool = Field(
        default=True,
        description="Prefer TUT sections with available seats (second priority)",
    )
    prefer_lec_seats: bool = Field(
        default=False,
        description="Prefer LEC sections with available seats (third priority)",
    )


class FixedSectionSimple(BaseModel):
    """Fixed section constraint for simple generation"""

    course_id: uuid.UUID
    component: str  # e.g., "LEC", "TUT", "LAB"
    class_nbr: int  # The specific class number to use


class TimetableGenerationRequest(BaseModel):
    """Schema for timetable generation request"""

    course_ids: list[uuid.UUID] = Field(..., min_length=1)
    algorithm: str = Field(
        default="backtrack_optimized",
        description="Algorithm to use: greedy, backtrack, backtrack_optimized, genetic, random, random_restart, simulated_annealing, hybrid",
    )
    generate_multiple: bool = Field(
        default=False, description="Generate multiple alternative timetables"
    )
    num_alternatives: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of alternative timetables to generate (if generate_multiple is True)",
    )
    # Constraints
    max_units: float = Field(
        default=25.0, ge=1, le=50, description="Maximum total units allowed"
    )
    # Section pinning
    fixed_sections: list[FixedSectionSimple] = Field(
        default_factory=list, description="Sections to pin (force selection)"
    )
    # Seat preferences
    seat_preferences: SeatPreferences | None = Field(
        default=None, description="Preferences for section seat availability"
    )


class GeneratedMeeting(BaseModel):
    """Schema for a generated meeting in the timetable preview"""

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
    # Computed field for UI display
    available_seats: int = 0


class ClashingCourseInfo(BaseModel):
    """Information about a course involved in a clash"""

    course_id: uuid.UUID | None = None
    subject: str
    catalog: str
    title: str
    component: str | None = None
    section: str | None = None
    class_nbr: int | None = None


class TimeClashDetail(BaseModel):
    """Detailed information about a time clash"""

    day: str
    course1: ClashingCourseInfo
    course1_time: str  # e.g., "09:00 - 10:30"
    course2: ClashingCourseInfo
    course2_time: str  # e.g., "10:00 - 11:00"
    overlap_time: str  # e.g., "10:00 - 10:30"


class ExamClashDetail(BaseModel):
    """Detailed information about an exam clash"""

    exam_date: date
    course1: ClashingCourseInfo
    course1_exam_time: str  # e.g., "09:00 - 12:00"
    course2: ClashingCourseInfo
    course2_exam_time: str  # e.g., "09:00 - 12:00"
    overlap_time: str | None = None  # e.g., "09:00 - 12:00"


class TimetableConflictDetail(BaseModel):
    """Schema for detailed conflict information"""

    type: str
    message: str
    courses: list[str] = []
    details: dict[str, Any] = {}
    # Enhanced clash information
    time_clashes: list[TimeClashDetail] = []
    exam_clashes: list[ExamClashDetail] = []


class TimetableGenerationResponse(BaseModel):
    """Schema for timetable generation response (preview, not committed)"""

    success: bool
    partial: bool = False  # True if some courses couldn't be scheduled
    meetings: list[GeneratedMeeting] = []  # Generated meetings to preview
    conflicts: list[TimetableConflictDetail] = []
    total_units: float = 0.0
    course_count: int = 0
    message: str
    algorithm_name: str | None = None
    execution_time_ms: float | None = None
    # Validation info
    validation_errors: list[str] = []  # Unit limit, missing components, etc.
    exam_conflicts: list[dict[str, Any]] = []  # Detailed exam conflict info
    # Section IDs for commit
    section_ids: list[uuid.UUID] = []
    # Alternative timetables (if generate_multiple was True)
    alternatives: list["TimetableGenerationResponse"] = []


class TimetableCommitRequest(BaseModel):
    """Schema for committing a generated timetable"""

    section_ids: list[uuid.UUID] = Field(
        ..., min_length=1, description="List of section IDs to commit"
    )


class TimetableCommitResponse(BaseModel):
    """Schema for timetable commit response"""

    success: bool
    timetable: TimetableResponse | None = None
    message: str


# ==================== Timetable Editing Schemas ====================


class CompatibleSectionInfo(BaseModel):
    """Schema for a compatible section that can be swapped into a timetable"""

    class_nbr: int  # Unique identifier for the section
    section_ids: list[uuid.UUID]  # All meeting time IDs for this class
    course_id: uuid.UUID
    subject: str
    catalog: str
    title: str
    component: str
    section: str
    meeting_times: str  # Summary like "M 10:00, W 10:00, TH 15:00"
    exam_date: date | None
    exam_start: time | None
    exam_end: time | None
    instructor: str | None
    room: str | None
    cap_enrl: int
    tot_enrl: int
    available_seats: int
    max_units: float


class FindCompatibleSectionsRequest(BaseModel):
    """Request to find compatible sections for a course"""

    course_id: uuid.UUID
    exclude_section_ids: list[
        uuid.UUID
    ] = []  # Current sections to exclude from results


class FindCompatibleSectionsResponse(BaseModel):
    """Response with compatible sections grouped by component"""

    success: bool
    course_id: uuid.UUID
    subject: str
    catalog: str
    title: str
    components: dict[str, list[CompatibleSectionInfo]]  # component -> list of sections
    message: str


class RemoveCourseRequest(BaseModel):
    """Request to remove a course from timetable"""

    course_id: uuid.UUID


class AddCourseRequest(BaseModel):
    """Request to add a course to existing timetable"""

    course_id: uuid.UUID
    section_ids: list[uuid.UUID] | None = None  # If None, auto-select best sections


class SwapSectionRequest(BaseModel):
    """Request to swap a section in the timetable"""

    model_config = ConfigDict(populate_by_name=True)

    old_class_nbr: int = Field(
        ...,
        validation_alias=AliasChoices("old_class_nbr", "old_section_id"),
    )
    new_class_nbr: int = Field(
        ...,
        validation_alias=AliasChoices("new_class_nbr", "new_section_id"),
    )


class TimetableEditResponse(BaseModel):
    """Response for timetable edit operations"""

    success: bool
    message: str
    updated_timetable: TimetableResponse | None = None
    conflicts: list[TimetableConflictDetail] = []


# ==================== Revert to Registration Schemas ====================


class RevertToRegistrationResponse(BaseModel):
    """Response for revert to registration operation"""

    success: bool
    message: str
    restored_section_count: int = 0
    timetable: TimetableResponse | None = None


# ==================== Multi-Swap Schemas ====================


class MultiSwapRequest(BaseModel):
    """Request to swap multiple sections at once (all components of a course)"""

    swaps: list[SwapSectionRequest] = Field(
        ..., min_length=1, description="List of section swaps to perform"
    )


class MultiSwapResponse(BaseModel):
    """Response for multi-swap operation"""

    success: bool
    message: str
    completed_swaps: int = 0
    failed_swaps: list[str] = []
    updated_timetable: TimetableResponse | None = None


# ==================== Enhanced Compatible Sections Schemas ====================


class CompatibleSectionInfoWithConflict(CompatibleSectionInfo):
    """Compatible section info with conflict status"""

    is_compatible: bool = True
    conflict_reason: str | None = None
    conflict_with_course: str | None = None


class FindCompatibleSectionsWithConflictsResponse(BaseModel):
    """Response with all sections showing compatibility status"""

    success: bool
    course_id: uuid.UUID
    subject: str
    catalog: str
    title: str
    components: dict[
        str, list[CompatibleSectionInfoWithConflict]
    ]  # component -> list of sections
    current_sections: dict[str, int]  # component -> current class_nbr
    message: str


# ==================== Capacity-based timetable buckets ====================


class TimetableBucketGenerationRequest(BaseModel):
    """Request to enumerate all clash-free timetables (buckets) for a fixed course set."""

    course_ids: list[uuid.UUID] = Field(..., min_length=1)
    max_units: float = Field(
        default=25.0,
        ge=1,
        le=50,
        description="Maximum total units per bucket (same rule as single-timetable generation)",
    )
    max_buckets: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Stop after collecting this many unique buckets (safety cap)",
    )
    max_search_nodes: int = Field(
        default=2_000_000,
        ge=1000,
        le=50_000_000,
        description="Stop search after this many DFS nodes (prevents runaway enumeration)",
    )
    fixed_sections: list[FixedSectionSimple] = Field(
        default_factory=list,
        description="Optional pinned components (same semantics as timetable generation)",
    )
    branch_codes: list[str] | None = Field(
        default=None,
        description="If set, only sections allowed for these branch codes are considered",
    )


class TimetableBucketItem(BaseModel):
    """One valid clash-free timetable for the requested course set."""

    capacity: int = Field(
        ...,
        ge=0,
        description="How many students can share this timetable: min available seats across chosen sections",
    )
    total_units: float = 0.0
    meetings: list[GeneratedMeeting] = []
    section_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="All course_section row IDs in this bucket (for commit or assignment)",
    )


class TimetableBucketGenerationResponse(BaseModel):
    """Result of capacity-based bucket enumeration."""

    success: bool
    buckets: list[TimetableBucketItem] = []
    conflicts: list[TimetableConflictDetail] = []
    message: str = ""
    total_units_reference: float = Field(
        default=0.0,
        description="Sum of max_units for the requested distinct courses (for sanity checks)",
    )
    buckets_returned: int = 0
    buckets_omitted_zero_capacity: int = Field(
        default=0,
        description="Buckets found but excluded from the response because capacity was 0",
    )
    enumeration_truncated: bool = Field(
        default=False,
        description="True if stopped early due to max_buckets or max_search_nodes",
    )
    search_nodes_explored: int = 0


class TimetableBucketSaveItem(BaseModel):
    """One bucket to persist as a buffer timetable."""

    capacity: int = Field(..., ge=0)
    section_ids: list[uuid.UUID] = Field(..., min_length=1)


class TimetableBucketSaveRequest(BaseModel):
    """Save enumerated buckets as assignable buffer timetables."""

    plan: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description=(
            "Buffer plan name. For “Assign timetable” on a student, this must match the "
            "buffer list filter: use the student’s branch code (e.g. A7) as plan, or a plan "
            "string that contains that code (see buffer timetable list API)."
        ),
    )
    buckets: list[TimetableBucketSaveItem] = Field(..., min_length=1)
    enrollment_deducted_on_upload: bool = Field(
        default=True,
        description="If true, reserve seats immediately by increasing tot_enrl (same as CSV upload). "
        "Recommended so manual allotment and buffer assignment cannot double-book the same seats. "
        "If false, tot_enrl increases only when each student is assigned from the buffer.",
    )
    max_buckets_to_save: int | None = Field(
        default=None,
        ge=1,
        le=5000,
        description="If set, only save this many buckets, highest capacity first (after dropping capacity 0).",
    )
    max_total_capacity: int | None = Field(
        default=None,
        ge=1,
        description="Total seats to reserve across saved buffer timetables. Prefers one bucket "
        "with exactly this capacity; otherwise truncates a larger bucket; otherwise combines "
        "smaller buckets (truncating the last) to reach the target.",
    )


class TimetableBucketSaveResponse(BaseModel):
    """Outcome of saving buckets to buffer timetables."""

    success: bool
    created: int = 0
    skipped: int = 0
    message: str = ""
    warnings: list[str] = []


class TimetableTransferRequest(BaseModel):
    """Request to transfer a timetable to another student."""

    target_student_id: uuid.UUID


class TimetableTransferResponse(BaseModel):
    """Response after transferring a timetable."""

    message: str
    target_student_id: uuid.UUID
    source_campus_id: str
    target_campus_id: str
    export_timestamp: str
    delete_filename: str
    add_filename: str
    zip_filename: str
