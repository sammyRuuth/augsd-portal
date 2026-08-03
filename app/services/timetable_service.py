"""Timetable service for timetable generation and management"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from typing import Any, Optional, Sequence

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.course import Course
from app.models.course_section import CourseSection
from app.models.registration_timetable import (
    RegistrationTimetable,
    RegistrationTimetableItem,
)
from app.models.buffer_timetable import BufferTimetable
from app.models.student import Student
from app.models.timetable import Timetable, TimetableItem
from app.schemas.timetable import (
    GeneratedMeeting,
    TimetableBucketGenerationResponse,
    TimetableBucketItem,
    TimetableConflictDetail,
    TimetableGenerationResponse,
)
from app.services import audit_trail_service


@dataclass
class SectionWithCourseInfo:
    """Section data combined with course metadata for timetable generation."""

    # From CourseSection
    id: uuid.UUID
    course_id: uuid.UUID
    class_nbr: int
    section: str
    component: str
    class_pattern: Optional[str]
    day: Optional[str]
    mtg_start: Optional[time]
    mtg_end: Optional[time]
    exam_date: Optional[date]
    exam_start: Optional[time]
    exam_end: Optional[time]
    instructor: Optional[str]
    room: Optional[str]
    cap_enrl: Optional[int]
    tot_enrl: int
    # From Course
    subject: str
    catalog: str
    title: str
    max_units: float


async def get_course_section_with_lock(
    db: AsyncSession, section_id: uuid.UUID
) -> CourseSection | None:
    """
    Get a course section with a row-level lock for safe enrollment updates.

    Uses SELECT FOR UPDATE to prevent race conditions when modifying tot_enrl.
    """
    stmt = select(CourseSection).where(CourseSection.id == section_id).with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_timetable_by_student(
    db: AsyncSession, student_id: uuid.UUID, include_all_statuses: bool = False
) -> Timetable | None:
    """
    Get timetable for a student.

    Args:
        db: Database session
        student_id: Student UUID
        include_all_statuses: If False, only returns committed/edited timetables. If True, includes all statuses.

    Returns:
        Timetable or None
    """
    query = select(Timetable).where(Timetable.student_id == student_id)

    if not include_all_statuses:
        # Only get committed or edited timetables (exclude drafts)
        # Cast status to String to avoid PostgreSQL enum casting issues across schemas
        query = query.where(
            or_(
                cast(Timetable.status, String) == "committed",
                cast(Timetable.status, String) == "edited",
            )
        )

    query = query.options(
        selectinload(Timetable.items).selectinload(TimetableItem.course_section)
    )

    result = await db.execute(query)
    return result.scalar_one_or_none()


class TimetableGeneratorV2:
    """
    Timetable generator using backtracking with logical sections and bitset clash detection.

    This follows the algorithm from the old ttgen system:
    - Groups sections by (course_id, component, section, class_nbr)
    - Uses 5-minute resolution bitmasks for time clash detection
    - Supports partial solutions (some courses may not be schedulable)
    - Returns preview data without auto-committing
    """

    def __init__(self, max_units: float = 25.0, max_iterations: int = 100000):
        self.max_units = max_units
        self.max_iterations = max_iterations
        self._iterations = 0
        self.conflicts: list[TimetableConflictDetail] = []
        self._state_stack: list[tuple] = []

    @staticmethod
    @lru_cache(maxsize=8192)
    def _to_minutes(time_str: str) -> int:
        """Convert HH:MM:SS to minutes since midnight. Returns -1 on error."""
        try:
            if not time_str or ":" not in time_str:
                return -1
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return -1

    @staticmethod
    @lru_cache(maxsize=8192)
    def _mask_for_interval(start: str, end: str) -> int:
        """Build a 5-min resolution bitmask between [start, end)"""
        if not start or not end:
            return 0
        try:
            s_min = TimetableGeneratorV2._to_minutes(start)
            e_min = TimetableGeneratorV2._to_minutes(end)
            if s_min < 0 or e_min <= s_min:
                return 0
            s_idx = s_min // 5
            e_idx = e_min // 5
            if e_idx <= s_idx:
                return 0
            width = e_idx - s_idx
            return ((1 << width) - 1) << s_idx
        except Exception:
            return 0

    @staticmethod
    def _time_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
        """Check if two time periods overlap"""
        if not all([start1, end1, start2, end2]):
            return False
        s1 = TimetableGeneratorV2._to_minutes(start1)
        e1 = TimetableGeneratorV2._to_minutes(end1)
        s2 = TimetableGeneratorV2._to_minutes(start2)
        e2 = TimetableGeneratorV2._to_minutes(end2)
        if -1 in (s1, e1, s2, e2):
            return False
        return max(s1, s2) < min(e1, e2)

    @staticmethod
    def _datetime_overlap(date1, start1, end1, date2, start2, end2) -> bool:
        """Check if two datetime periods overlap"""
        if not all([date1, start1, end1, date2, start2, end2]):
            return False
        if str(date1) != str(date2):
            return False
        return TimetableGeneratorV2._time_overlap(start1, end1, start2, end2)

    def _build_course_structures(
        self, sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """
        Build course structures with logical sections.

        A logical section is all meetings for (course_id, component, section, class_nbr).
        """
        course_structs: dict[uuid.UUID, dict[str, Any]] = {}

        for course_id, sections in sections_by_course.items():
            if not sections:
                self.conflicts.append(
                    TimetableConflictDetail(
                        type="no_sections",
                        message=f"No sections found for course {course_id}",
                        courses=[str(course_id)],
                    )
                )
                continue

            # Group meetings by (component, section, class_nbr) - logical section
            logical_sections: dict[tuple, dict[str, Any]] = {}

            for s in sections:
                key = (s.component, s.section, s.class_nbr)

                if key not in logical_sections:
                    # Get time strings for mask calculation
                    mtg_start_str = (
                        s.mtg_start.strftime("%H:%M:%S") if s.mtg_start else None
                    )
                    mtg_end_str = s.mtg_end.strftime("%H:%M:%S") if s.mtg_end else None
                    exam_start_str = (
                        s.exam_start.strftime("%H:%M:%S") if s.exam_start else None
                    )
                    exam_end_str = (
                        s.exam_end.strftime("%H:%M:%S") if s.exam_end else None
                    )
                    exam_date_str = s.exam_date.isoformat() if s.exam_date else None

                    logical_sections[key] = {
                        "course_id": course_id,
                        "subject": s.subject,
                        "catalog": s.catalog,
                        "title": s.title,
                        "component": s.component,
                        "section": s.section,
                        "class_nbr": s.class_nbr,
                        "max_units": float(s.max_units or 0),
                        "meetings": [],
                        "day_masks": defaultdict(int),
                        "exam": (exam_date_str, exam_start_str, exam_end_str),
                        "seat_score": 0,
                    }

                logical = logical_sections[key]
                logical["meetings"].append(s)

                # Accumulate mask and seat availability
                if s.day:
                    mtg_start_str = (
                        s.mtg_start.strftime("%H:%M:%S") if s.mtg_start else None
                    )
                    mtg_end_str = s.mtg_end.strftime("%H:%M:%S") if s.mtg_end else None
                    mask = self._mask_for_interval(mtg_start_str, mtg_end_str)
                    logical["day_masks"][s.day] |= mask

                cap = s.cap_enrl or 0
                tot = s.tot_enrl or 0
                logical["seat_score"] += max(cap - tot, 0)

            # Partition by component type
            components_map: dict[str, list[dict]] = defaultdict(list)
            for logical in logical_sections.values():
                components_map[logical["component"]].append(logical)

            # Sort options per component by seat availability
            for comp in components_map:
                components_map[comp].sort(
                    key=lambda s: (s["seat_score"], -len(s["meetings"])), reverse=True
                )

            # Estimate combo count
            combo_est = 1
            for comp_list in components_map.values():
                combo_est *= max(1, len(comp_list))

            # Store structure
            first = sections[0]
            course_structs[course_id] = {
                "meta": {
                    "course_id": course_id,
                    "subject": first.subject,
                    "catalog": first.catalog,
                    "title": first.title,
                    "max_units": float(first.max_units or 0),
                },
                "components": dict(components_map),
                "combos_estimate": combo_est,
            }

        return course_structs

    def _logical_sections_clash(self, A: dict, B: dict) -> bool:
        """Check if two logical sections clash on any day or in exam."""
        # Time clash using masks
        for day, mask in A["day_masks"].items():
            if mask and (mask & B["day_masks"].get(day, 0)):
                return True

        # Exam clash
        a_date, a_start, a_end = A.get("exam", (None, None, None))
        b_date, b_start, b_end = B.get("exam", (None, None, None))
        if self._datetime_overlap(a_date, a_start, a_end, b_date, b_start, b_end):
            return True

        return False

    def _get_component_priority(self, component: str) -> int:
        """Priority for component ordering (LAB first, then TUT, then LEC)"""
        component = component.upper().strip()
        if component == "LAB":
            return 1
        elif component == "TUT":
            return 2
        elif component == "LEC":
            return 3
        return 4

    def _enumerate_course_combos(
        self, components_map: dict[str, list[dict]]
    ) -> list[list[dict]]:
        """Enumerate valid combinations (one option per component) avoiding internal clashes."""
        comp_types = list(components_map.keys())
        if not comp_types:
            return []

        comp_types.sort(key=self._get_component_priority)
        combos: list[list[dict]] = []

        def dfs(idx: int, acc: list[dict]):
            if idx == len(comp_types):
                combos.append(acc[:])
                return

            comp = comp_types[idx]
            for option in components_map[comp]:
                ok = True
                for chosen in acc:
                    if self._logical_sections_clash(chosen, option):
                        ok = False
                        break
                if not ok:
                    continue
                acc.append(option)
                dfs(idx + 1, acc)
                acc.pop()

        dfs(0, [])

        # Sort combos by seat score desc
        combos.sort(
            key=lambda combo: sum(s.get("seat_score", 0) for s in combo), reverse=True
        )
        return combos

    def _valid_with_current(
        self, current: dict[str, Any], logical_sections: list[dict]
    ) -> bool:
        """Check if adding logical_sections is valid with current solution."""
        # Unit limit check
        units_to_add = (
            logical_sections[0].get("max_units", 0) if logical_sections else 0
        )
        if current["units"] + units_to_add > self.max_units:
            return False

        # Clash against current using masks
        for sec in logical_sections:
            for day, mask in sec["day_masks"].items():
                if mask & current["day_masks"].get(day, 0):
                    return False

            # Check against existing meetings for exam
            for existing in current.get("logical_sections", []):
                if self._logical_sections_clash(sec, existing):
                    return False

        # Internal clashes among the chosen logical sections
        for i in range(len(logical_sections)):
            for j in range(i + 1, len(logical_sections)):
                if self._logical_sections_clash(
                    logical_sections[i], logical_sections[j]
                ):
                    return False

        return True

    def _merge_solution(self, current: dict, logical_sections: list[dict]):
        """Merge chosen logical_sections into current solution."""
        # Push snapshot for backtracking
        snapshot = (
            dict(current["day_masks"]),
            current["units"],
            current["seat_score"],
            set(current["courses"]),
            len(current["logical_sections"]),
        )
        self._state_stack.append(snapshot)

        for sec in logical_sections:
            for day, mask in sec["day_masks"].items():
                if mask:
                    current["day_masks"][day] = current["day_masks"].get(day, 0) | mask
            current["logical_sections"].append(sec)

        # Update meta
        if logical_sections:
            meta = logical_sections[0]
            current["courses"].add(meta["course_id"])
            current["units"] += meta.get("max_units", 0)
            current["seat_score"] += sum(
                s.get("seat_score", 0) for s in logical_sections
            )

    def _unmerge_solution(self, current: dict, logical_sections: list[dict]):
        """Undo last merge using snapshot pop."""
        if not self._state_stack:
            return
        snap_day_masks, snap_units, snap_seat, snap_courses, snap_len = (
            self._state_stack.pop()
        )
        current["day_masks"] = snap_day_masks
        current["units"] = snap_units
        current["seat_score"] = snap_seat
        current["courses"] = snap_courses
        current["logical_sections"] = current["logical_sections"][:snap_len]

    def _search_courses(
        self,
        course_order: list[tuple[uuid.UUID, dict]],
        idx: int,
        current: dict,
        best: dict,
    ):
        """Backtracking search for best timetable."""
        # Check iteration limit to prevent infinite loops
        self._iterations += 1
        if self._iterations > self.max_iterations:
            return

        # Update best if current is better
        if len(current["courses"]) > len(best["courses"]) or (
            len(current["courses"]) == len(best["courses"])
            and current["seat_score"] > best["seat_score"]
        ):
            best["logical_sections"] = current["logical_sections"][:]
            best["day_masks"] = current["day_masks"].copy()
            best["courses"] = set(current["courses"])
            best["units"] = current["units"]
            best["seat_score"] = current["seat_score"]

        # Base case
        if idx >= len(course_order):
            return

        # Pruning
        remaining = len(course_order) - idx
        if len(current["courses"]) + remaining <= len(best["courses"]):
            return

        course_id, data = course_order[idx]
        combos = self._enumerate_course_combos(data["components"])

        # Try each combo
        for combo in combos:
            if not combo:
                continue
            if not self._valid_with_current(current, combo):
                continue

            self._merge_solution(current, combo)
            self._search_courses(course_order, idx + 1, current, best)
            self._unmerge_solution(current, combo)

            # Check iteration limit after each recursion
            if self._iterations > self.max_iterations:
                return

        # Option: skip this course
        self._search_courses(course_order, idx + 1, current, best)

    def generate(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]],
    ) -> TimetableGenerationResponse:
        """
        Generate timetable without committing.

        Returns a preview of the generated timetable that can be reviewed
        before committing.
        """
        self.conflicts = []
        self._state_stack = []
        self._iterations = 0

        if not sections_by_course:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=[
                    TimetableConflictDetail(
                        type="no_courses",
                        message="No courses selected",
                    )
                ],
                message="No courses selected for timetable generation",
            )

        # Build course structures
        course_structs = self._build_course_structures(sections_by_course)

        if not course_structs:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=self.conflicts,
                message="No valid course structures found",
            )

        # Order courses by constraint (fewer combos first)
        def key_fn(item):
            cid, data = item
            return (data.get("combos_estimate", 1), -data["meta"].get("max_units", 0))

        course_order = sorted(course_structs.items(), key=key_fn)

        # Initialize search
        current = {
            "logical_sections": [],
            "day_masks": defaultdict(int),
            "courses": set(),
            "units": 0.0,
            "seat_score": 0,
        }

        best = {
            "logical_sections": [],
            "day_masks": defaultdict(int),
            "courses": set(),
            "units": 0.0,
            "seat_score": 0,
        }

        # Run backtracking
        self._search_courses(course_order, 0, current, best)

        # Build result
        meetings: list[GeneratedMeeting] = []
        seen_section_ids = set()

        for logical in best["logical_sections"]:
            for meeting in logical["meetings"]:
                if meeting.id in seen_section_ids:
                    continue
                seen_section_ids.add(meeting.id)

                meetings.append(
                    GeneratedMeeting(
                        section_id=meeting.id,
                        course_id=meeting.course_id,
                        class_nbr=meeting.class_nbr,
                        subject=meeting.subject,
                        catalog=meeting.catalog,
                        title=meeting.title,
                        component=meeting.component,
                        section=meeting.section,
                        day=meeting.day,
                        mtg_start=meeting.mtg_start,
                        mtg_end=meeting.mtg_end,
                        exam_date=meeting.exam_date,
                        exam_start=meeting.exam_start,
                        exam_end=meeting.exam_end,
                        instructor=meeting.instructor,
                        room=meeting.room,
                        cap_enrl=meeting.cap_enrl or 0,
                        tot_enrl=meeting.tot_enrl or 0,
                        max_units=float(meeting.max_units or 0),
                    )
                )

        # Build conflicts for unscheduled courses
        all_courses = set(course_structs.keys())
        scheduled_courses = best["courses"]

        for cid in all_courses - scheduled_courses:
            course_data = course_structs.get(cid, {})
            meta = course_data.get("meta", {})
            analysis = self._analyze_scheduling_failure(cid, course_structs, best)

            # Import schema types for structured clash details
            from app.schemas.timetable import (
                ClashingCourseInfo,
                ExamClashDetail,
                TimeClashDetail,
            )

            # Build time clash details
            time_clash_details = []
            for tc in analysis.get("time_clashes", []):
                time_clash_details.append(
                    TimeClashDetail(
                        day=tc.get("day", "Unknown"),
                        course1=ClashingCourseInfo(
                            course_id=tc.get("course1", {}).get("course_id"),
                            subject=tc.get("course1", {}).get("subject", ""),
                            catalog=tc.get("course1", {}).get("catalog", ""),
                            title=tc.get("course1", {}).get("title", ""),
                            component=tc.get("course1", {}).get("component"),
                            section=tc.get("course1", {}).get("section"),
                            class_nbr=tc.get("course1", {}).get("class_nbr"),
                        ),
                        course1_time=tc.get("course1_time", "Unknown"),
                        course2=ClashingCourseInfo(
                            course_id=tc.get("course2", {}).get("course_id"),
                            subject=tc.get("course2", {}).get("subject", ""),
                            catalog=tc.get("course2", {}).get("catalog", ""),
                            title=tc.get("course2", {}).get("title", ""),
                            component=tc.get("course2", {}).get("component"),
                            section=tc.get("course2", {}).get("section"),
                            class_nbr=tc.get("course2", {}).get("class_nbr"),
                        ),
                        course2_time=tc.get("course2_time", "Unknown"),
                        overlap_time=tc.get("overlap_time", ""),
                    )
                )

            # Build exam clash details
            exam_clash_details = []
            for ec in analysis.get("exam_clashes", []):
                exam_date_str = ec.get("exam_date", "")
                try:
                    from datetime import datetime as dt

                    exam_date_parsed = dt.strptime(exam_date_str, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    exam_date_parsed = date.today()

                exam_clash_details.append(
                    ExamClashDetail(
                        exam_date=exam_date_parsed,
                        course1=ClashingCourseInfo(
                            course_id=ec.get("course1", {}).get("course_id"),
                            subject=ec.get("course1", {}).get("subject", ""),
                            catalog=ec.get("course1", {}).get("catalog", ""),
                            title=ec.get("course1", {}).get("title", ""),
                        ),
                        course1_exam_time=ec.get("course1_exam_time", "Unknown"),
                        course2=ClashingCourseInfo(
                            course_id=ec.get("course2", {}).get("course_id"),
                            subject=ec.get("course2", {}).get("subject", ""),
                            catalog=ec.get("course2", {}).get("catalog", ""),
                            title=ec.get("course2", {}).get("title", ""),
                        ),
                        course2_exam_time=ec.get("course2_exam_time", "Unknown"),
                        overlap_time=ec.get("overlap_time"),
                    )
                )

            self.conflicts.append(
                TimetableConflictDetail(
                    type="scheduling_conflict",
                    message=f"Could not schedule {meta.get('subject', '')} {meta.get('catalog', '')} ({meta.get('title', '')}) - {analysis['reason']}",
                    courses=[str(cid)],
                    details={
                        "course_info": {
                            "subject": meta.get("subject", ""),
                            "catalog": meta.get("catalog", ""),
                            "title": meta.get("title", ""),
                            "units": meta.get("max_units", 0),
                        },
                        "reason": analysis["reason"],
                        "unit_conflict": analysis.get("unit_conflict"),
                    },
                    time_clashes=time_clash_details,
                    exam_clashes=exam_clash_details,
                )
            )

        # Calculate course count
        unique_courses = set()
        for m in meetings:
            unique_courses.add((m.subject, m.catalog))

        success = len(meetings) > 0 and len(self.conflicts) == 0
        partial = len(meetings) > 0 and len(self.conflicts) > 0

        return TimetableGenerationResponse(
            success=success,
            partial=partial,
            meetings=meetings,
            conflicts=self.conflicts,
            total_units=best["units"],
            course_count=len(unique_courses),
            message="Timetable generated successfully"
            if success
            else (
                "Partial timetable generated - some courses could not be scheduled"
                if partial
                else "Could not generate timetable due to conflicts"
            ),
        )

    def _logical_min_available_seats(self, logical: dict) -> int:
        """Minimum (cap - tot) across meeting rows for one enrollable logical section."""
        best: int | None = None
        for meeting in logical.get("meetings", []):
            cap = meeting.cap_enrl or 0
            tot = meeting.tot_enrl or 0
            avail = max(0, cap - tot)
            if best is None or avail < best:
                best = avail
        return best if best is not None else 0

    def _solution_bucket_capacity(self, logical_sections: list[dict]) -> int:
        """Bucket capacity: min available seats across chosen logical sections."""
        if not logical_sections:
            return 0
        return min(self._logical_min_available_seats(ls) for ls in logical_sections)

    @staticmethod
    def _solution_dedupe_key(logical_sections: list[dict]) -> frozenset[uuid.UUID]:
        """Unique key for a complete timetable (all meeting row UUIDs)."""
        ids: set[uuid.UUID] = set()
        for logical in logical_sections:
            for meeting in logical.get("meetings", []):
                ids.add(meeting.id)
        return frozenset(ids)

    def _meetings_payload_from_logicals(
        self, logical_sections: list[dict]
    ) -> tuple[list[GeneratedMeeting], list[uuid.UUID]]:
        """Build GeneratedMeeting list and section_ids from a solution (same shape as generate)."""
        meetings: list[GeneratedMeeting] = []
        section_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for logical in logical_sections:
            for meeting in logical.get("meetings", []):
                if meeting.id in seen:
                    continue
                seen.add(meeting.id)
                section_ids.append(meeting.id)
                cap = meeting.cap_enrl or 0
                tot = meeting.tot_enrl or 0
                meetings.append(
                    GeneratedMeeting(
                        section_id=meeting.id,
                        course_id=meeting.course_id,
                        class_nbr=meeting.class_nbr,
                        subject=meeting.subject,
                        catalog=meeting.catalog,
                        title=meeting.title,
                        component=meeting.component,
                        section=meeting.section,
                        day=meeting.day,
                        mtg_start=meeting.mtg_start,
                        mtg_end=meeting.mtg_end,
                        exam_date=meeting.exam_date,
                        exam_start=meeting.exam_start,
                        exam_end=meeting.exam_end,
                        instructor=meeting.instructor,
                        room=meeting.room,
                        cap_enrl=cap,
                        tot_enrl=tot,
                        max_units=float(meeting.max_units or 0),
                        available_seats=max(0, cap - tot),
                    )
                )
        return meetings, section_ids

    def _filter_course_structs_for_fixed(
        self,
        course_structs: dict[uuid.UUID, dict[str, Any]],
        fixed: dict[uuid.UUID, dict[str, int]],
    ) -> tuple[dict[uuid.UUID, dict[str, Any]] | None, str | None]:
        """Keep only logical sections matching pinned class_nbr per component."""
        if not fixed:
            return course_structs, None
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for cid, data in course_structs.items():
            pinned = fixed.get(cid)
            if not pinned:
                out[cid] = data
                continue
            new_components: dict[str, list[dict]] = {}
            components_map = data.get("components", {})
            for comp, options in components_map.items():
                if comp not in pinned:
                    new_components[comp] = options
                    continue
                want = pinned[comp]
                filtered = [o for o in options if o.get("class_nbr") == want]
                if not filtered:
                    meta = data.get("meta", {})
                    return None, (
                        f"No section with class_nbr {want} for component {comp} in "
                        f"{meta.get('subject', '')} {meta.get('catalog', '')}"
                    )
                new_components[comp] = filtered
            new_data = {**data, "components": new_components}
            combo_est = 1
            for comp_list in new_components.values():
                combo_est *= max(1, len(comp_list))
            new_data["combos_estimate"] = combo_est
            out[cid] = new_data
        return out, None

    def _enumerate_complete_buckets_dfs(
        self,
        course_order: list[tuple[uuid.UUID, dict]],
        idx: int,
        current: dict[str, Any],
        buckets: list[dict[str, Any]],
        seen_keys: set[frozenset[uuid.UUID]],
        stats: dict[str, Any],
    ) -> None:
        """DFS: collect every complete assignment (all courses), no skipping courses."""
        stats["nodes"] = stats.get("nodes", 0) + 1
        if stats["nodes"] > stats["max_nodes"]:
            stats["truncated"] = True
            return
        if len(buckets) >= stats["max_buckets"]:
            stats["truncated"] = True
            return

        n = len(course_order)
        if idx >= n:
            if len(current["courses"]) == n:
                key = self._solution_dedupe_key(current["logical_sections"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    cap = self._solution_bucket_capacity(current["logical_sections"])
                    meetings, section_ids = self._meetings_payload_from_logicals(
                        current["logical_sections"]
                    )
                    buckets.append(
                        {
                            "capacity": cap,
                            "meetings": meetings,
                            "section_ids": section_ids,
                            "total_units": current["units"],
                        }
                    )
                    if len(buckets) >= stats["max_buckets"]:
                        stats["truncated"] = True
            return

        _course_id, data = course_order[idx]
        combos = self._enumerate_course_combos(data["components"])
        for combo in combos:
            if not combo:
                continue
            if not self._valid_with_current(current, combo):
                continue
            self._merge_solution(current, combo)
            self._enumerate_complete_buckets_dfs(
                course_order, idx + 1, current, buckets, seen_keys, stats
            )
            self._unmerge_solution(current, combo)
            if stats.get("truncated"):
                return

    def enumerate_buckets(
        self,
        sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]],
        *,
        max_buckets: int = 500,
        max_search_nodes: int = 2_000_000,
        fixed_sections: dict[uuid.UUID, dict[str, int]] | None = None,
    ) -> TimetableBucketGenerationResponse:
        """
        Enumerate every clash-free timetable that schedules all requested courses.

        Uses the same logical-section model, intra-course component combinations,
        and clash rules as :meth:`generate`, but returns all complete solutions
        (each with bucket capacity = min available seats across chosen sections).
        """
        self.conflicts = []
        self._state_stack = []
        self._iterations = 0

        if not sections_by_course:
            return TimetableBucketGenerationResponse(
                success=False,
                message="No courses selected for bucket enumeration",
                conflicts=[
                    TimetableConflictDetail(
                        type="no_courses",
                        message="No courses selected",
                    )
                ],
            )

        course_structs = self._build_course_structures(sections_by_course)
        if not course_structs:
            return TimetableBucketGenerationResponse(
                success=False,
                message="No valid course structures found",
                conflicts=self.conflicts,
            )

        fixed_map = fixed_sections or {}
        filtered, ferr = self._filter_course_structs_for_fixed(course_structs, fixed_map)
        if ferr:
            return TimetableBucketGenerationResponse(
                success=False,
                message=ferr,
                conflicts=[
                    TimetableConflictDetail(
                        type="fixed_section_invalid",
                        message=ferr,
                    )
                ],
            )
        assert filtered is not None

        def key_fn(item: tuple[uuid.UUID, dict]) -> tuple:
            cid, data = item
            return (data.get("combos_estimate", 1), -data["meta"].get("max_units", 0))

        course_order = sorted(filtered.items(), key=key_fn)

        for cid, data in course_order:
            combos = self._enumerate_course_combos(data["components"])
            if not combos:
                meta = data.get("meta", {})
                return TimetableBucketGenerationResponse(
                    success=False,
                    message=(
                        f"No valid section combination for "
                        f"{meta.get('subject', '')} {meta.get('catalog', '')}"
                    ),
                    conflicts=[
                        TimetableConflictDetail(
                            type="no_valid_combo",
                            message="No valid component combination (internal clashes or pins)",
                            courses=[str(cid)],
                        )
                    ],
                )

        buckets_raw: list[dict[str, Any]] = []
        seen_keys: set[frozenset[uuid.UUID]] = set()
        stats: dict[str, Any] = {
            "nodes": 0,
            "max_nodes": max_search_nodes,
            "max_buckets": max_buckets,
            "truncated": False,
        }
        current = {
            "logical_sections": [],
            "day_masks": defaultdict(int),
            "courses": set(),
            "units": 0.0,
            "seat_score": 0,
        }
        self._enumerate_complete_buckets_dfs(
            course_order, 0, current, buckets_raw, seen_keys, stats
        )

        total_units_ref = sum(
            float(data["meta"].get("max_units", 0)) for _, data in course_order
        )

        if not buckets_raw:
            return TimetableBucketGenerationResponse(
                success=False,
                message="No complete clash-free timetable exists for this course set "
                "(with current sections, pins, and max units)",
                conflicts=[
                    TimetableConflictDetail(
                        type="no_complete_timetable",
                        message="Enumeration found zero full timetables",
                    )
                ],
                total_units_reference=total_units_ref,
                search_nodes_explored=stats["nodes"],
                enumeration_truncated=bool(stats.get("truncated")),
            )

        buckets_raw.sort(
            key=lambda b: (-b["capacity"], -b["total_units"], str(b["section_ids"]))
        )

        positive_raw = [b for b in buckets_raw if int(b.get("capacity", 0) or 0) > 0]
        omitted_zero = len(buckets_raw) - len(positive_raw)

        if not positive_raw:
            return TimetableBucketGenerationResponse(
                success=False,
                message=(
                    "No buckets with positive capacity. Every clash-free timetable found "
                    "has at least one section with no available seats."
                ),
                conflicts=[
                    TimetableConflictDetail(
                        type="no_positive_capacity",
                        message=(
                            "All complete timetables have bucket capacity 0 "
                            f"({omitted_zero} timetable(s) evaluated). "
                            "Check enrollments (tot_enrl vs cap_enrl) or adjust courses and branch filters."
                        ),
                    )
                ],
                total_units_reference=total_units_ref,
                buckets_omitted_zero_capacity=omitted_zero,
                search_nodes_explored=stats["nodes"],
                enumeration_truncated=bool(stats.get("truncated")),
            )

        items = [
            TimetableBucketItem(
                capacity=b["capacity"],
                total_units=b["total_units"],
                meetings=b["meetings"],
                section_ids=b["section_ids"],
            )
            for b in positive_raw
        ]

        msg = "Bucket enumeration completed"
        if omitted_zero:
            msg += f" ({omitted_zero} zero-capacity timetable(s) omitted)"

        return TimetableBucketGenerationResponse(
            success=True,
            buckets=items,
            message=msg,
            total_units_reference=total_units_ref,
            buckets_returned=len(items),
            buckets_omitted_zero_capacity=omitted_zero,
            enumeration_truncated=bool(stats.get("truncated")),
            search_nodes_explored=stats["nodes"],
        )

    def _analyze_scheduling_failure(
        self,
        course_id: uuid.UUID,
        course_structs: dict,
        best_solution: dict,
    ) -> dict[str, Any]:
        """
        Analyze why a course couldn't be scheduled.
        Returns a dict with detailed clash information.
        """
        result: dict[str, Any] = {
            "reason": "",
            "time_clashes": [],
            "exam_clashes": [],
            "unit_conflict": None,
        }

        try:
            course_data = course_structs.get(course_id, {})
            meta = course_data.get("meta", {})
            components_map = course_data.get("components", {})

            # Check unit limit
            current_units = best_solution.get("units", 0)
            course_units = meta.get("max_units", 0)
            if current_units + course_units > self.max_units:
                result["reason"] = (
                    f"Adding this course would exceed the {self.max_units:.1f} unit limit (current: {current_units:.1f}, course: {course_units:.1f})"
                )
                result["unit_conflict"] = {
                    "current_units": current_units,
                    "course_units": course_units,
                    "max_units": self.max_units,
                }
                return result

            # Try to find conflicts
            combos = self._enumerate_course_combos(components_map)
            if not combos:
                result["reason"] = (
                    "No valid section combinations available (internal component conflicts)"
                )
                return result

            # Check best combo against current schedule
            best_combo = combos[0]
            time_clashes = []
            exam_clashes = []

            for new_section in best_combo:
                for existing in best_solution.get("logical_sections", []):
                    if self._logical_sections_clash(new_section, existing):
                        # Check if time or exam clash
                        has_time_clash = False
                        for day, mask in new_section["day_masks"].items():
                            if mask and (mask & existing["day_masks"].get(day, 0)):
                                has_time_clash = True
                                # Get actual times
                                new_times = self._get_meeting_times_for_day(
                                    new_section, day
                                )
                                existing_times = self._get_meeting_times_for_day(
                                    existing, day
                                )

                                overlap_str = ""
                                if new_times and existing_times:
                                    overlap_start = max(new_times[0], existing_times[0])
                                    overlap_end = min(new_times[1], existing_times[1])
                                    if overlap_start < overlap_end:
                                        overlap_str = f"{self._minutes_to_str(overlap_start)} - {self._minutes_to_str(overlap_end)}"

                                time_clashes.append(
                                    {
                                        "day": day,
                                        "course1": {
                                            "course_id": str(
                                                new_section.get("course_id", "")
                                            ),
                                            "subject": new_section.get("subject", ""),
                                            "catalog": new_section.get("catalog", ""),
                                            "title": new_section.get("title", ""),
                                            "component": new_section.get(
                                                "component", ""
                                            ),
                                            "section": new_section.get("section", ""),
                                            "class_nbr": new_section.get("class_nbr"),
                                        },
                                        "course1_time": f"{self._minutes_to_str(new_times[0])} - {self._minutes_to_str(new_times[1])}"
                                        if new_times
                                        else "Unknown",
                                        "course2": {
                                            "course_id": str(
                                                existing.get("course_id", "")
                                            ),
                                            "subject": existing.get("subject", ""),
                                            "catalog": existing.get("catalog", ""),
                                            "title": existing.get("title", ""),
                                            "component": existing.get("component", ""),
                                            "section": existing.get("section", ""),
                                            "class_nbr": existing.get("class_nbr"),
                                        },
                                        "course2_time": f"{self._minutes_to_str(existing_times[0])} - {self._minutes_to_str(existing_times[1])}"
                                        if existing_times
                                        else "Unknown",
                                        "overlap_time": overlap_str,
                                    }
                                )
                                break

                        if not has_time_clash:
                            # Exam clash
                            new_exam = new_section.get("exam", (None, None, None))
                            existing_exam = existing.get("exam", (None, None, None))
                            if (
                                new_exam[0]
                                and existing_exam[0]
                                and new_exam[0] == existing_exam[0]
                            ):
                                exam_clashes.append(
                                    {
                                        "exam_date": str(new_exam[0]),
                                        "course1": {
                                            "course_id": str(
                                                new_section.get("course_id", "")
                                            ),
                                            "subject": new_section.get("subject", ""),
                                            "catalog": new_section.get("catalog", ""),
                                            "title": new_section.get("title", ""),
                                        },
                                        "course1_exam_time": f"{new_exam[1]} - {new_exam[2]}"
                                        if new_exam[1] and new_exam[2]
                                        else "Unknown",
                                        "course2": {
                                            "course_id": str(
                                                existing.get("course_id", "")
                                            ),
                                            "subject": existing.get("subject", ""),
                                            "catalog": existing.get("catalog", ""),
                                            "title": existing.get("title", ""),
                                        },
                                        "course2_exam_time": f"{existing_exam[1]} - {existing_exam[2]}"
                                        if existing_exam[1] and existing_exam[2]
                                        else "Unknown",
                                        "overlap_time": None,
                                    }
                                )

            if time_clashes:
                result["time_clashes"] = time_clashes
                clash_summaries = []
                for clash in time_clashes:
                    clash_summaries.append(
                        f"{clash['course2']['subject']} {clash['course2']['catalog']} on {clash['day']} "
                        f"({clash['overlap_time']})"
                    )
                result["reason"] = f"Time conflict with: {'; '.join(clash_summaries)}"
                return result

            if exam_clashes:
                result["exam_clashes"] = exam_clashes
                clash_summaries = []
                for clash in exam_clashes:
                    clash_summaries.append(
                        f"{clash['course2']['subject']} {clash['course2']['catalog']} on {clash['exam_date']}"
                    )
                result["reason"] = f"Exam conflict with: {'; '.join(clash_summaries)}"
                return result

            result["reason"] = (
                "Schedule conflict with other courses (no compatible section combination found)"
            )
            return result

        except Exception as e:
            result["reason"] = f"Unable to determine reason: {str(e)}"
            return result

    def _get_meeting_times_for_day(
        self, logical_section: dict, day: str
    ) -> tuple[int, int] | None:
        """Get the start and end time (in minutes) for a logical section on a specific day."""
        for meeting in logical_section.get("meetings", []):
            if meeting.day == day and meeting.mtg_start and meeting.mtg_end:
                start = self._to_minutes(meeting.mtg_start.strftime("%H:%M:%S"))
                end = self._to_minutes(meeting.mtg_end.strftime("%H:%M:%S"))
                if start >= 0 and end > start:
                    return (start, end)
        return None

    def _minutes_to_str(self, minutes: int) -> str:
        """Convert minutes since midnight to HH:MM format."""
        if minutes < 0:
            return "??:??"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours:02d}:{mins:02d}"


async def generate_timetable(
    db: AsyncSession,
    student_id: uuid.UUID,
    course_ids: list[uuid.UUID],
) -> TimetableGenerationResponse:
    """
    Generate timetable for a student (preview only, not committed).

    Returns TimetableGenerationResponse with generated meetings for review.
    """
    # Check if student already has a committed timetable
    existing_timetable = await get_timetable_by_student(db, student_id)
    if existing_timetable:
        return TimetableGenerationResponse(
            success=False,
            partial=False,
            meetings=[],
            conflicts=[
                TimetableConflictDetail(
                    type="existing_timetable",
                    message="Student already has a committed timetable. Uncommit it first.",
                )
            ],
            message="Cannot generate timetable - student already has one committed",
        )

    # Fetch all courses metadata first
    courses_result = await db.execute(select(Course).where(Course.id.in_(course_ids)))
    courses_map: dict[uuid.UUID, Course] = {
        c.id: c for c in courses_result.scalars().all()
    }

    # Fetch all sections for the requested courses and combine with course metadata
    sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]] = {}

    for course_id in course_ids:
        course = courses_map.get(course_id)
        if not course:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=[
                    TimetableConflictDetail(
                        type="course_not_found",
                        message=f"Course {course_id} not found",
                        courses=[str(course_id)],
                    )
                ],
                message="Cannot generate timetable - course not found",
            )

        sections_result = await db.execute(
            select(CourseSection).where(CourseSection.course_id == course_id)
        )
        sections = list(sections_result.scalars().all())

        if not sections:
            return TimetableGenerationResponse(
                success=False,
                partial=False,
                meetings=[],
                conflicts=[
                    TimetableConflictDetail(
                        type="no_sections",
                        message=f"No sections found for course {course.subject} {course.catalog}",
                        courses=[str(course_id)],
                    )
                ],
                message="Cannot generate timetable - missing sections",
            )

        # Convert CourseSection to SectionWithCourseInfo by adding course metadata
        sections_with_info = [
            SectionWithCourseInfo(
                id=s.id,
                course_id=s.course_id,
                class_nbr=s.class_nbr,
                section=s.section,
                component=s.component,
                class_pattern=s.class_pattern,
                day=s.day,
                mtg_start=s.mtg_start,
                mtg_end=s.mtg_end,
                exam_date=s.exam_date,
                exam_start=s.exam_start,
                exam_end=s.exam_end,
                instructor=s.instructor,
                room=s.room,
                cap_enrl=s.cap_enrl,
                tot_enrl=s.tot_enrl,
                subject=course.subject,
                catalog=course.catalog,
                title=course.title,
                max_units=float(course.max_units or 0),
            )
            for s in sections
        ]

        sections_by_course[course_id] = sections_with_info

    # Run generation
    generator = TimetableGeneratorV2(max_units=25.0)
    return generator.generate(sections_by_course)


async def fetch_sections_for_generation(
    global_db: AsyncSession,
    session_db: AsyncSession,
    course_ids: list[uuid.UUID],
    student_branches: list[str] | None = None,
) -> tuple[dict[uuid.UUID, list[SectionWithCourseInfo]] | None, str | None, list[str]]:
    """
    Fetch all section data needed for timetable generation.

    This function fetches all required data from the database and returns it
    as a dict that can be used for generation after the DB session is closed.

    Args:
        global_db: Database session for global database (courses)
        session_db: Database session for session schema (sections)
        course_ids: List of course IDs to fetch sections for
        student_branches: Optional list of student branch codes (e.g., ["A7"] or ["B2", "A3"])
                         Used to filter sections based on branch restrictions.

    Returns:
        Tuple of (sections_by_course, error_message, courses_with_issues)
        - sections_by_course: Dict mapping course IDs to their sections (None if error)
        - error_message: Error description (None if success)
        - courses_with_issues: List of course names that have issues
    """
    from app.core.section_restrictions import is_section_allowed_for_branch

    # Fetch all courses metadata from global database
    courses_result = await global_db.execute(
        select(Course).where(Course.id.in_(course_ids))
    )
    courses_map: dict[uuid.UUID, Course] = {
        c.id: c for c in courses_result.scalars().all()
    }

    # Check for missing courses
    missing_course_ids = set(course_ids) - set(courses_map.keys())
    if missing_course_ids:
        return (
            None,
            "Some courses were not found in the database",
            [str(cid) for cid in missing_course_ids],
        )

    # Fetch all sections from session database and combine with course metadata
    sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]] = {}
    courses_without_sections = []

    for course_id in course_ids:
        course = courses_map[course_id]

        sections_result = await session_db.execute(
            select(CourseSection).where(CourseSection.course_id == course_id)
        )
        sections = list(sections_result.scalars().all())

        if not sections:
            courses_without_sections.append(
                f"{course.subject} {course.catalog} - {course.title}"
            )
            continue

        # Filter sections based on branch restrictions if student_branches provided
        if student_branches:
            sections = [
                s
                for s in sections
                if is_section_allowed_for_branch(
                    section_name=s.section,
                    branches=student_branches,
                    component=s.component,
                )
            ]

            if not sections:
                courses_without_sections.append(
                    f"{course.subject} {course.catalog} - {course.title} (no sections available for your branch)"
                )
                continue

        # Convert CourseSection to SectionWithCourseInfo by adding course metadata
        sections_with_info = [
            SectionWithCourseInfo(
                id=s.id,
                course_id=s.course_id,
                class_nbr=s.class_nbr,
                section=s.section,
                component=s.component,
                class_pattern=s.class_pattern,
                day=s.day,
                mtg_start=s.mtg_start,
                mtg_end=s.mtg_end,
                exam_date=s.exam_date,
                exam_start=s.exam_start,
                exam_end=s.exam_end,
                instructor=s.instructor,
                room=s.room,
                cap_enrl=s.cap_enrl,
                tot_enrl=s.tot_enrl,
                subject=course.subject,
                catalog=course.catalog,
                title=course.title,
                max_units=float(course.max_units or 0),
            )
            for s in sections
        ]

        sections_by_course[course_id] = sections_with_info

    if courses_without_sections:
        error_msg = f"No sections available for {len(courses_without_sections)} course(s) in this session"
        return None, error_msg, courses_without_sections

    return sections_by_course, None, []


def run_bucket_enumeration(
    sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]] | None,
    max_units: float,
    max_buckets: int,
    max_search_nodes: int,
    fixed_sections: list[dict] | None,
) -> TimetableBucketGenerationResponse:
    """
    CPU-only enumeration of capacity-based timetable buckets (no DB access).

    See :meth:`TimetableGeneratorV2.enumerate_buckets` for semantics.
    """
    if sections_by_course is None:
        return TimetableBucketGenerationResponse(
            success=False,
            message="Failed to fetch course data",
            conflicts=[
                TimetableConflictDetail(
                    type="data_error",
                    message="Failed to fetch course data",
                )
            ],
        )

    fixed_dict: dict[uuid.UUID, dict[str, int]] = {}
    if fixed_sections:
        for fs in fixed_sections:
            cid = fs.get("course_id")
            if isinstance(cid, str):
                cid = uuid.UUID(cid)
            comp = fs.get("component")
            class_nbr = fs.get("class_nbr")
            if cid and comp and class_nbr is not None:
                if cid not in fixed_dict:
                    fixed_dict[cid] = {}
                fixed_dict[cid][comp] = int(class_nbr)

    generator = TimetableGeneratorV2(max_units=max_units, max_iterations=100000)
    return generator.enumerate_buckets(
        sections_by_course,
        max_buckets=max_buckets,
        max_search_nodes=max_search_nodes,
        fixed_sections=fixed_dict or None,
    )


def run_timetable_generation(
    sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]] | None,
) -> TimetableGenerationResponse:
    """
    Run the CPU-intensive timetable generation algorithm.

    This function does NOT access the database - it only does CPU work.
    Call this AFTER closing the database session to avoid holding locks.

    Uses iteration limit of 100k to prevent hanging on complex inputs.
    """
    if sections_by_course is None:
        return TimetableGenerationResponse(
            success=False,
            partial=False,
            meetings=[],
            conflicts=[
                TimetableConflictDetail(
                    type="data_error",
                    message="Failed to fetch course data",
                )
            ],
            message="Cannot generate timetable - data fetch failed",
        )

    # Run generation with iteration limit (CPU-intensive, no DB access)
    generator = TimetableGeneratorV2(max_units=25.0, max_iterations=100000)
    return generator.generate(sections_by_course)


def run_timetable_generation_v2(
    sections_by_course: dict[uuid.UUID, list[SectionWithCourseInfo]] | None,
    algorithm: str = "parallel_race",
    generate_multiple: bool = False,
    num_alternatives: int = 3,
    max_units: float = 25.0,
    fixed_sections: list[dict] | None = None,
    seat_preferences: dict | None = None,
) -> TimetableGenerationResponse:
    """
    Run timetable generation with configurable algorithm and multiple alternatives.

    Args:
        sections_by_course: Sections data grouped by course
        algorithm: Algorithm name (parallel_race, greedy, backtrack, backtrack_optimized, genetic, etc.)
        generate_multiple: Whether to generate multiple alternatives
        num_alternatives: Number of alternative timetables to generate
        max_units: Maximum units allowed (default 25)
        fixed_sections: List of pinned sections [{"course_id": uuid, "component": str, "class_nbr": int}]
        seat_preferences: Dict with prefer_lab_seats, prefer_tut_seats, prefer_lec_seats bools

    Returns:
        TimetableGenerationResponse with results and optionally alternatives
    """
    from app.core.algorithms import GenerationConstraints, get_algorithm
    from app.core.algorithms.base import SeatPreference, SectionData

    if sections_by_course is None:
        return TimetableGenerationResponse(
            success=False,
            partial=False,
            meetings=[],
            conflicts=[
                TimetableConflictDetail(
                    type="data_error",
                    message="Failed to fetch course data",
                )
            ],
            message="Cannot generate timetable - data fetch failed",
        )

    # Convert SectionWithCourseInfo to SectionData format for algorithms
    converted_sections: dict[uuid.UUID, list[SectionData]] = {}
    for course_id, sections in sections_by_course.items():
        converted_sections[course_id] = [
            SectionData(
                id=s.id,
                course_id=s.course_id,
                class_nbr=s.class_nbr,
                section=s.section,
                component=s.component,
                day=s.day,
                mtg_start=s.mtg_start,
                mtg_end=s.mtg_end,
                exam_date=s.exam_date,
                exam_start=s.exam_start,
                exam_end=s.exam_end,
                instructor=s.instructor,
                room=s.room,
                cap_enrl=s.cap_enrl or 0,
                tot_enrl=s.tot_enrl or 0,
                subject=s.subject,
                catalog=s.catalog,
                title=s.title,
                max_units=float(s.max_units or 0),
            )
            for s in sections
        ]

    # Build constraints
    fixed_dict: dict[uuid.UUID, dict[str, int]] = {}
    if fixed_sections:
        for fs in fixed_sections:
            cid = fs.get("course_id")
            if isinstance(cid, str):
                cid = uuid.UUID(cid)
            comp = fs.get("component")
            class_nbr = fs.get("class_nbr")
            if cid and comp and class_nbr:
                if cid not in fixed_dict:
                    fixed_dict[cid] = {}
                fixed_dict[cid][comp] = class_nbr

    seat_pref = None
    if seat_preferences:
        seat_pref = SeatPreference(
            prefer_lab_seats=seat_preferences.get("prefer_lab_seats", True),
            prefer_tut_seats=seat_preferences.get("prefer_tut_seats", True),
            prefer_lec_seats=seat_preferences.get("prefer_lec_seats", False),
        )

    constraints = GenerationConstraints(
        max_units=max_units,
        fixed_sections=fixed_dict,
        seat_preferences=seat_pref,
    )

    def convert_result_to_response(
        algo_result, algo_name: str
    ) -> TimetableGenerationResponse:
        """Convert AlgorithmResult to TimetableGenerationResponse"""
        meetings = []
        section_ids = []
        seen_ids = set()
        exam_info: list[dict] = []  # Track exam info for conflict detection

        for logical_section in algo_result.selected_sections:
            # Each LogicalSection contains a list of meeting times (SectionData)
            for meeting in logical_section.meetings:
                if meeting.id in seen_ids:
                    continue
                seen_ids.add(meeting.id)
                section_ids.append(meeting.id)

                cap = meeting.cap_enrl or 0
                tot = meeting.tot_enrl or 0
                available = max(cap - tot, 0)

                meetings.append(
                    GeneratedMeeting(
                        section_id=meeting.id,
                        course_id=meeting.course_id,
                        class_nbr=meeting.class_nbr,
                        subject=meeting.subject,
                        catalog=meeting.catalog,
                        title=meeting.title,
                        component=meeting.component,
                        section=meeting.section,
                        day=meeting.day,
                        mtg_start=meeting.mtg_start,
                        mtg_end=meeting.mtg_end,
                        exam_date=meeting.exam_date,
                        exam_start=meeting.exam_start,
                        exam_end=meeting.exam_end,
                        instructor=meeting.instructor,
                        room=meeting.room,
                        cap_enrl=cap,
                        tot_enrl=tot,
                        max_units=float(meeting.max_units or 0),
                        available_seats=available,
                    )
                )

                # Track exam info
                if meeting.exam_date:
                    exam_info.append(
                        {
                            "course": f"{meeting.subject} {meeting.catalog}",
                            "date": str(meeting.exam_date),
                            "start": str(meeting.exam_start)
                            if meeting.exam_start
                            else None,
                            "end": str(meeting.exam_end) if meeting.exam_end else None,
                        }
                    )

        conflicts = []
        for c in algo_result.conflicts:
            conflict = TimetableConflictDetail(
                type=c.get("type", "unknown"),
                message=c.get("message", "Unknown conflict"),
                courses=[c.get("course_id")] if c.get("course_id") else [],
                details={
                    "course_info": c.get("course_info", {}),
                    "reason": c.get("reason", ""),
                    "unit_conflict": c.get("unit_conflict"),
                },
            )
            # Add structured time clash details
            if c.get("time_clashes"):
                from app.schemas.timetable import ClashingCourseInfo, TimeClashDetail

                for tc in c.get("time_clashes", []):
                    conflict.time_clashes.append(
                        TimeClashDetail(
                            day=tc.get("day", "Unknown"),
                            course1=ClashingCourseInfo(
                                course_id=tc.get("course1", {}).get("course_id"),
                                subject=tc.get("course1", {}).get("subject", ""),
                                catalog=tc.get("course1", {}).get("catalog", ""),
                                title=tc.get("course1", {}).get("title", ""),
                                component=tc.get("course1", {}).get("component"),
                                section=tc.get("course1", {}).get("section"),
                                class_nbr=tc.get("course1", {}).get("class_nbr"),
                            ),
                            course1_time=tc.get("course1_time", "Unknown"),
                            course2=ClashingCourseInfo(
                                course_id=tc.get("course2", {}).get("course_id"),
                                subject=tc.get("course2", {}).get("subject", ""),
                                catalog=tc.get("course2", {}).get("catalog", ""),
                                title=tc.get("course2", {}).get("title", ""),
                                component=tc.get("course2", {}).get("component"),
                                section=tc.get("course2", {}).get("section"),
                                class_nbr=tc.get("course2", {}).get("class_nbr"),
                            ),
                            course2_time=tc.get("course2_time", "Unknown"),
                            overlap_time=tc.get("overlap_time", ""),
                        )
                    )
            # Add structured exam clash details
            if c.get("exam_clashes"):
                from datetime import datetime

                from app.schemas.timetable import ClashingCourseInfo, ExamClashDetail

                for ec in c.get("exam_clashes", []):
                    exam_date_str = ec.get("exam_date", "")
                    try:
                        exam_date = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        from datetime import date

                        exam_date = date.today()

                    conflict.exam_clashes.append(
                        ExamClashDetail(
                            exam_date=exam_date,
                            course1=ClashingCourseInfo(
                                course_id=ec.get("course1", {}).get("course_id"),
                                subject=ec.get("course1", {}).get("subject", ""),
                                catalog=ec.get("course1", {}).get("catalog", ""),
                                title=ec.get("course1", {}).get("title", ""),
                            ),
                            course1_exam_time=ec.get("course1_exam_time", "Unknown"),
                            course2=ClashingCourseInfo(
                                course_id=ec.get("course2", {}).get("course_id"),
                                subject=ec.get("course2", {}).get("subject", ""),
                                catalog=ec.get("course2", {}).get("catalog", ""),
                                title=ec.get("course2", {}).get("title", ""),
                            ),
                            course2_exam_time=ec.get("course2_exam_time", "Unknown"),
                            overlap_time=ec.get("overlap_time"),
                        )
                    )
            conflicts.append(conflict)

        # Validation errors
        validation_errors = []
        if algo_result.total_units > max_units:
            validation_errors.append(
                f"Total units ({algo_result.total_units}) exceeds limit ({max_units})"
            )

        return TimetableGenerationResponse(
            success=algo_result.success,
            partial=algo_result.partial,
            meetings=meetings,
            conflicts=conflicts,
            total_units=algo_result.total_units,
            course_count=algo_result.course_count,
            message=algo_result.message,
            algorithm_name=algo_name,
            execution_time_ms=algo_result.execution_time_ms,
            validation_errors=validation_errors,
            exam_conflicts=[],  # Will be populated if there are exam conflicts
            section_ids=section_ids,
        )

    # Run primary algorithm
    try:
        algo = get_algorithm(algorithm, constraints)
        result = algo.generate(converted_sections)
        response = convert_result_to_response(result, algorithm)
    except Exception as e:
        return TimetableGenerationResponse(
            success=False,
            partial=False,
            meetings=[],
            conflicts=[
                TimetableConflictDetail(
                    type="algorithm_error",
                    message=f"Algorithm '{algorithm}' failed: {str(e)}",
                )
            ],
            message=f"Generation failed with algorithm '{algorithm}'",
        )

    # Generate alternatives if requested
    if generate_multiple and response.success:
        alternative_algos = [
            "greedy",
            "random",
            "random_restart",
            "simulated_annealing",
        ]
        # Remove primary algorithm from alternatives
        alternative_algos = [a for a in alternative_algos if a != algorithm]

        alternatives = []
        seen_solutions = {frozenset(m.section_id for m in response.meetings)}

        for alt_algo in alternative_algos[:num_alternatives]:
            try:
                alt = get_algorithm(alt_algo, constraints)
                alt_result = alt.generate(converted_sections)
                if alt_result.success or alt_result.partial:
                    alt_response = convert_result_to_response(alt_result, alt_algo)
                    # Only add if solution is different
                    solution_key = frozenset(
                        m.section_id for m in alt_response.meetings
                    )
                    if solution_key not in seen_solutions:
                        seen_solutions.add(solution_key)
                        alternatives.append(alt_response)
            except Exception:
                pass  # Skip failed alternatives silently

        response.alternatives = alternatives

    return response


async def validate_timetable_completeness(
    db: AsyncSession,
    section_ids: list[uuid.UUID],
    max_units: float = 25.0,
) -> tuple[bool, str]:
    """
    Validate that timetable has all required components for each course.

    Also validates:
    - Total units do not exceed max_units limit
    - All meeting times for each section (class_nbr) are present
    - No time conflicts exist between meeting times
    - No exam conflicts exist

    Args:
        db: Database session
        section_ids: List of section IDs to validate
        max_units: Maximum units allowed (default 25.0)

    Returns:
        tuple[bool, str]: (is_valid, error_message)
    """
    if not section_ids:
        return False, "No sections provided"

    # Fetch all sections
    sections_result = await db.execute(
        select(CourseSection).where(CourseSection.id.in_(section_ids))
    )
    sections = list(sections_result.scalars().all())

    if len(sections) != len(section_ids):
        return False, "Some section IDs are invalid"

    # Group by course
    courses_map: dict[uuid.UUID, dict[str, dict[int, list[CourseSection]]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    )

    for section in sections:
        courses_map[section.course_id][section.component][section.class_nbr].append(
            section
        )

    # For each course, verify all components are present
    for course_id, components in courses_map.items():
        # Fetch course to get required components
        course_result = await db.execute(select(Course).where(Course.id == course_id))
        course = course_result.scalar_one_or_none()
        if not course:
            return False, f"Course {course_id} not found"

        # Get all available sections for this course to determine required components
        all_sections_result = await db.execute(
            select(CourseSection.component)
            .where(CourseSection.course_id == course_id)
            .distinct()
        )
        required_components = set(row[0] for row in all_sections_result.all())

        # Check if all required components are present in timetable
        selected_components = set(components.keys())
        missing_components = required_components - selected_components

        if missing_components:
            course_name = f"{course.subject} {course.catalog}"
            return (
                False,
                f"Course {course_name} is missing components: {', '.join(sorted(missing_components))}",
            )

    # Validate total units do not exceed limit
    total_units = 0.0
    courses_counted = set()
    for course_id in courses_map.keys():
        if course_id not in courses_counted:
            course_result = await db.execute(
                select(Course).where(Course.id == course_id)
            )
            course = course_result.scalar_one_or_none()
            if course and course.max_units:
                total_units += float(course.max_units)
            courses_counted.add(course_id)

    if total_units > max_units:
        return (
            False,
            f"Total units ({total_units}) exceeds maximum allowed ({max_units})",
        )

    # Validate no time conflicts
    day_masks: dict[str, int] = defaultdict(int)
    # Track exams per course (exam is for entire course, not per section)
    # Format: {course_id: (exam_date, exam_start, exam_end)}
    course_exams: dict[uuid.UUID, tuple] = {}

    for course_id, components in courses_map.items():
        for component, classes in components.items():
            for class_nbr, meeting_times in classes.items():
                # Check all meeting times for this section
                for section in meeting_times:
                    if section.day and section.mtg_start and section.mtg_end:
                        mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
                        mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
                        mask = TimetableGeneratorV2._mask_for_interval(
                            mtg_start_str, mtg_end_str
                        )

                        # Check for conflict with existing times on this day
                        if mask & day_masks.get(section.day, 0):
                            course_result = await db.execute(
                                select(Course).where(Course.id == course_id)
                            )
                            course = course_result.scalar_one_or_none()
                            course_name = (
                                f"{course.subject} {course.catalog}"
                                if course
                                else "Unknown"
                            )
                            return (
                                False,
                                f"Time conflict detected for {course_name} {section.component} {section.section} on {section.day} at {mtg_start_str}",
                            )

                        # Add to day mask
                        day_masks[section.day] |= mask

                    # Capture exam info for this course (only need to do once per course)
                    if (
                        course_id not in course_exams
                        and section.exam_date
                        and section.exam_start
                        and section.exam_end
                    ):
                        course_exams[course_id] = (
                            section.exam_date.isoformat(),
                            section.exam_start.strftime("%H:%M:%S"),
                            section.exam_end.strftime("%H:%M:%S"),
                        )

    # Check for exam conflicts between different courses
    course_ids = list(course_exams.keys())
    for i, cid1 in enumerate(course_ids):
        exam1 = course_exams[cid1]
        for cid2 in course_ids[i + 1 :]:
            exam2 = course_exams[cid2]
            if TimetableGeneratorV2._datetime_overlap(
                exam1[0],
                exam1[1],
                exam1[2],
                exam2[0],
                exam2[1],
                exam2[2],
            ):
                # Get course names for error message
                course1_result = await db.execute(
                    select(Course).where(Course.id == cid1)
                )
                course2_result = await db.execute(
                    select(Course).where(Course.id == cid2)
                )
                course1 = course1_result.scalar_one_or_none()
                course2 = course2_result.scalar_one_or_none()
                name1 = f"{course1.subject} {course1.catalog}" if course1 else "Unknown"
                name2 = f"{course2.subject} {course2.catalog}" if course2 else "Unknown"
                return (
                    False,
                    f"Exam conflict detected between {name1} and {name2}",
                )

    return True, ""


async def commit_timetable(
    db: AsyncSession,
    student_id: uuid.UUID,
    section_ids: list[uuid.UUID],
    created_by_id: uuid.UUID,
    max_units: float = 25.0,
) -> Timetable:
    """
    Commit a portal-generated timetable with specific sections.

    This creates a new timetable with source='portal_generated' and status='committed'.
    Validates that all required components are present, no conflicts exist,
    and total units do not exceed the max_units limit.

    Args:
        db: Database session
        student_id: Student UUID
        section_ids: List of section IDs to commit
        created_by_id: User who created/committed the timetable
        max_units: Maximum units allowed (default 25.0)

    Returns:
        The created Timetable object

    Raises:
        ValueError: If validation fails (missing components, conflicts, or unit limit exceeded)
    """
    # Validate timetable completeness, conflicts, and unit limit
    is_valid, error_msg = await validate_timetable_completeness(
        db, section_ids, max_units
    )
    if not is_valid:
        raise ValueError(f"Invalid timetable: {error_msg}")

    # Check if student already has a committed or edited timetable
    existing_timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if existing_timetable:
        raise ValueError(
            "Student already has a committed timetable. Please uncommit first or use edit operations."
        )

    # Calculate total units
    total_units = 0.0
    section_course_map = {}  # Track which courses sections belong to

    for section_id in section_ids:
        section = await db.get(CourseSection, section_id)
        if section:
            # Get course info to calculate units
            course = await db.get(Course, section.course_id)
            if course and course.max_units:
                # Only add units once per course
                if section.course_id not in section_course_map:
                    total_units += float(course.max_units)
                    section_course_map[section.course_id] = []
                section_course_map[section.course_id].append(section_id)

    # Create timetable
    timetable = Timetable(
        student_id=student_id,
        source="portal_generated",
        status="committed",
        created_by_id=created_by_id,
        total_units=total_units,
        created_at=datetime.now(timezone.utc),
    )

    db.add(timetable)
    await db.flush()

    # Create items and update enrollments
    for section_id in section_ids:
        item = TimetableItem(
            timetable_id=timetable.id,
            course_section_id=section_id,
        )
        db.add(item)

        # Update section enrollment with lock to prevent race condition
        section = await get_course_section_with_lock(db, section_id)
        if section:
            section.tot_enrl += 1

    await db.flush()

    # Log to audit trail
    await audit_trail_service.log_commit_timetable(
        db=db,
        timetable_id=timetable.id,
        changed_by_id=created_by_id,
        section_ids=section_ids,
        total_units=total_units,
    )

    await db.refresh(timetable)
    return timetable


async def uncommit_timetable(
    db: AsyncSession, student_id: uuid.UUID, changed_by_id: uuid.UUID
) -> bool:
    """
    Uncommit a student's timetable (portal-generated only).

    Note: For registration-sourced timetables, use edit operations instead.

    For buffer-assigned timetables:
    - Decrements the buffer's assigned_count
    - Only decreases enrollment if enrollment was NOT deducted on upload
    """
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return False

    # Only allow uncommitting portal-generated timetables
    if timetable.source == "from_registration":
        raise ValueError(
            "Cannot uncommit registration-sourced timetables. Use edit operations instead."
        )

    # Check if this was a buffer-assigned timetable
    buffer_tt = None
    should_decrease_enrollment = True

    if timetable.buffer_timetable_id:
        # Get the buffer timetable
        buffer_tt = await db.get(BufferTimetable, timetable.buffer_timetable_id)
        if buffer_tt:
            # Decrement assigned_count
            if buffer_tt.assigned_count > 0:
                buffer_tt.assigned_count -= 1

            # If enrollment was deducted on upload, don't decrease it now
            # (the seats were reserved at upload time, not assignment time)
            if buffer_tt.enrollment_deducted_on_upload:
                should_decrease_enrollment = False

    # Log to audit trail before deletion
    await audit_trail_service.log_uncommit_timetable(
        db=db,
        timetable_id=timetable.id,
        changed_by_id=changed_by_id,
    )

    # Decrease enrollment counts if applicable
    if should_decrease_enrollment:
        for item in timetable.items:
            section = await get_course_section_with_lock(db, item.course_section_id)
            if section and section.tot_enrl > 0:
                section.tot_enrl -= 1

    # Delete timetable (cascade will delete items and audit trail)
    await db.delete(timetable)
    await db.flush()

    return True


async def transfer_timetable(
    db: AsyncSession,
    source_student_id: uuid.UUID,
    target_student_id: uuid.UUID,
    changed_by_id: uuid.UUID,
) -> tuple[Timetable, Student, Student, list[int]]:
    """
    Transfer a committed/edited timetable from one student to another.

    Reassigns the timetable record (preserving enrollments and items).
    Works for both portal-generated and registration-sourced timetables.

    Returns:
        (timetable, source_student, target_student, unique_class_nbrs)

    Raises:
        ValueError: If validation fails
    """
    if source_student_id == target_student_id:
        raise ValueError("Cannot transfer timetable to the same student")

    source_student = await db.get(Student, source_student_id)
    target_student = await db.get(Student, target_student_id)
    if not source_student or not target_student:
        raise ValueError("Student not found")

    source_timetable = await get_timetable_by_student(
        db, source_student_id, include_all_statuses=False
    )
    if not source_timetable:
        raise ValueError("Source student has no committed timetable")

    target_timetable = await get_timetable_by_student(
        db, target_student_id, include_all_statuses=False
    )
    if target_timetable:
        raise ValueError(
            "Target student already has a committed timetable. "
            "Choose a student without a timetable."
        )

    class_nbrs = sorted(
        {
            item.course_section.class_nbr
            for item in source_timetable.items
            if item.course_section
        }
    )

    source_timetable.student_id = target_student_id
    source_timetable.updated_at = datetime.now(timezone.utc)
    source_timetable.updated_by_id = changed_by_id

    await audit_trail_service.log_transfer_timetable(
        db=db,
        timetable_id=source_timetable.id,
        changed_by_id=changed_by_id,
        from_student_id=source_student_id,
        to_student_id=target_student_id,
        class_nbrs=class_nbrs,
    )

    await db.flush()
    await db.refresh(source_timetable)

    return source_timetable, source_student, target_student, class_nbrs


async def list_committed_timetables(db: AsyncSession) -> Sequence[Timetable]:
    """List all committed or edited timetables in session (excludes drafts)"""
    result = await db.execute(
        select(Timetable)
        .where(
            or_(
                cast(Timetable.status, String) == "committed",
                cast(Timetable.status, String) == "edited",
            )
        )
        .options(
            selectinload(Timetable.items).selectinload(TimetableItem.course_section)
        )
    )
    return result.scalars().all()


# ==================== Timetable Editing Functions ====================


async def find_compatible_sections(
    db: AsyncSession,
    student_id: uuid.UUID,
    course_id: uuid.UUID,
    exclude_section_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """
    Find all sections of a course that are compatible with the student's current timetable.

    Returns sections grouped by component (grouped by class_nbr).
    Each entry represents ONE section with all its meeting times.
    """
    from app.schemas.timetable import CompatibleSectionInfo

    # Get current timetable
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )

    # Build current schedule's time masks (excluding the course being replaced)
    current_day_masks: dict[str, int] = defaultdict(int)
    current_exams: list[tuple] = []

    if timetable:
        for item in timetable.items:
            section = item.course_section
            if not section:
                continue
            # Skip sections from the course we're finding alternatives for
            if section.course_id == course_id:
                continue

            # Build time mask
            if section.day and section.mtg_start and section.mtg_end:
                mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
                mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
                mask = TimetableGeneratorV2._mask_for_interval(
                    mtg_start_str, mtg_end_str
                )
                current_day_masks[section.day] |= mask

            # Track exams
            if section.exam_date and section.exam_start and section.exam_end:
                current_exams.append(
                    (
                        section.exam_date.isoformat(),
                        section.exam_start.strftime("%H:%M:%S"),
                        section.exam_end.strftime("%H:%M:%S"),
                    )
                )

    # Fetch all sections for the target course
    sections_result = await db.execute(
        select(CourseSection).where(CourseSection.course_id == course_id)
    )
    all_sections = list(sections_result.scalars().all())

    # Fetch course info
    course = await db.execute(select(Course).where(Course.id == course_id))
    course_obj = course.scalar_one_or_none()

    if not course_obj:
        return {
            "success": False,
            "course_id": course_id,
            "subject": "",
            "catalog": "",
            "title": "",
            "components": {},
            "message": "Course not found",
        }

    exclude_ids = set(exclude_section_ids or [])

    # Group sections by component and class_nbr
    sections_by_component_and_class: dict[str, dict[int, list[CourseSection]]] = (
        defaultdict(lambda: defaultdict(list))
    )

    for section in all_sections:
        if section.id not in exclude_ids:
            sections_by_component_and_class[section.component][
                section.class_nbr
            ].append(section)

    # Check each class for compatibility
    compatible_by_component: dict[str, list[CompatibleSectionInfo]] = defaultdict(list)

    for component, classes_dict in sections_by_component_and_class.items():
        for class_nbr, meeting_times in classes_dict.items():
            # Check if ANY meeting time has a conflict
            has_clash = False

            # Check all meeting times for time conflicts
            for section in meeting_times:
                if section.day and section.mtg_start and section.mtg_end:
                    mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
                    mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
                    mask = TimetableGeneratorV2._mask_for_interval(
                        mtg_start_str, mtg_end_str
                    )
                    if mask & current_day_masks.get(section.day, 0):
                        has_clash = True
                        break

            # Use first meeting time for representative data
            representative = meeting_times[0]

            # Check exam clash (representative)
            if (
                not has_clash
                and representative.exam_date
                and representative.exam_start
                and representative.exam_end
            ):
                exam_date_str = representative.exam_date.isoformat()
                exam_start_str = representative.exam_start.strftime("%H:%M:%S")
                exam_end_str = representative.exam_end.strftime("%H:%M:%S")
                for ex_date, ex_start, ex_end in current_exams:
                    if TimetableGeneratorV2._datetime_overlap(
                        exam_date_str,
                        exam_start_str,
                        exam_end_str,
                        ex_date,
                        ex_start,
                        ex_end,
                    ):
                        has_clash = True
                        break

            if has_clash:
                continue

            # Section is compatible - build meeting times summary
            cap = representative.cap_enrl or 0
            tot = representative.tot_enrl or 0
            available = max(0, cap - tot)

            meeting_summary = (
                ", ".join(
                    f"{mt.day} {mt.mtg_start.strftime('%H:%M') if mt.mtg_start else 'TBA'}"
                    for mt in sorted(
                        meeting_times,
                        key=lambda x: (x.day or "", x.mtg_start or time(0, 0)),
                    )
                    if mt.day
                )
                or "TBA"
            )

            compatible_by_component[component].append(
                CompatibleSectionInfo(
                    class_nbr=class_nbr,
                    section_ids=[mt.id for mt in meeting_times],
                    course_id=representative.course_id,
                    subject=course_obj.subject,
                    catalog=course_obj.catalog,
                    title=course_obj.title,
                    component=representative.component,
                    section=representative.section,
                    meeting_times=meeting_summary,
                    exam_date=representative.exam_date,
                    exam_start=representative.exam_start,
                    exam_end=representative.exam_end,
                    instructor=representative.instructor,
                    room=representative.room,
                    cap_enrl=cap,
                    tot_enrl=tot,
                    available_seats=available,
                    max_units=float(course_obj.max_units or 0),
                )
            )

    # Sort each component's sections by available seats (descending)
    for comp in compatible_by_component:
        compatible_by_component[comp].sort(
            key=lambda s: s.available_seats, reverse=True
        )

    return {
        "success": True,
        "course_id": course_id,
        "subject": course_obj.subject,
        "catalog": course_obj.catalog,
        "title": course_obj.title,
        "components": dict(compatible_by_component),
        "message": f"Found compatible sections for {course_obj.subject} {course_obj.catalog}",
    }


async def find_compatible_sections_with_conflicts(
    db: AsyncSession,
    student_id: uuid.UUID,
    course_id: uuid.UUID,
    exclude_section_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """
    Find all sections of a course with compatibility status for multi-swap UI.

    Unlike find_compatible_sections, this returns ALL sections grouped by class_nbr with conflict info:
    - Compatible sections: is_compatible=True
    - Incompatible sections: is_compatible=False, conflict_reason, conflict_with_course

    Also returns current_sections mapping (component -> current class_nbr).
    """
    from app.schemas.timetable import CompatibleSectionInfoWithConflict

    # Get current timetable
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )

    # Build current schedule info - track which section conflicts with which course
    current_day_masks: dict[str, int] = defaultdict(int)
    current_exams: list[tuple] = []
    # Map: (day, mask) -> course name for conflict reporting
    day_mask_to_course: dict[str, dict[int, str]] = defaultdict(dict)
    # Map: (exam_date, exam_start, exam_end) -> course name
    exam_to_course: dict[tuple, str] = {}
    # Current sections for this course (component -> class_nbr)
    current_sections: dict[str, int] = {}

    if timetable:
        for item in timetable.items:
            section = item.course_section
            if not section:
                continue

            # Track current sections for the target course
            if section.course_id == course_id:
                current_sections[section.component] = section.class_nbr
                continue  # Skip this course's sections for conflict detection

            # Get course name for conflict reporting
            course_result = await db.execute(
                select(Course).where(Course.id == section.course_id)
            )
            course_obj = course_result.scalar_one_or_none()
            course_name = (
                f"{course_obj.subject} {course_obj.catalog}"
                if course_obj
                else "Unknown"
            )

            # Build time mask and track which course owns it
            if section.day and section.mtg_start and section.mtg_end:
                mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
                mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
                mask = TimetableGeneratorV2._mask_for_interval(
                    mtg_start_str, mtg_end_str
                )
                current_day_masks[section.day] |= mask
                # Store each bit position to course mapping
                for bit in range(48):  # 48 slots in a day (30-min intervals)
                    if mask & (1 << bit):
                        if bit not in day_mask_to_course[section.day]:
                            day_mask_to_course[section.day][bit] = course_name

            # Track exams
            if section.exam_date and section.exam_start and section.exam_end:
                exam_key = (
                    section.exam_date.isoformat(),
                    section.exam_start.strftime("%H:%M:%S"),
                    section.exam_end.strftime("%H:%M:%S"),
                )
                current_exams.append(exam_key)
                exam_to_course[exam_key] = course_name

    # Fetch all sections for the target course
    sections_result = await db.execute(
        select(CourseSection).where(CourseSection.course_id == course_id)
    )
    all_sections = list(sections_result.scalars().all())

    # Fetch course info
    course = await db.execute(select(Course).where(Course.id == course_id))
    course_obj = course.scalar_one_or_none()

    if not course_obj:
        return {
            "success": False,
            "course_id": course_id,
            "subject": "",
            "catalog": "",
            "title": "",
            "components": {},
            "current_sections": {},
            "message": "Course not found",
        }

    exclude_ids = set(exclude_section_ids or [])

    # Group sections by component and class_nbr first
    sections_by_component_and_class: dict[str, dict[int, list[CourseSection]]] = (
        defaultdict(lambda: defaultdict(list))
    )

    for section in all_sections:
        if section.id not in exclude_ids:
            sections_by_component_and_class[section.component][
                section.class_nbr
            ].append(section)

    # Now check each class (group of meeting times) for conflicts
    sections_by_component: dict[str, list[CompatibleSectionInfoWithConflict]] = (
        defaultdict(list)
    )

    for component, classes_dict in sections_by_component_and_class.items():
        for class_nbr, meeting_times in classes_dict.items():
            # Check if ANY meeting time has a conflict
            conflict_reason = None
            conflict_with_course = None
            has_any_conflict = False

            # Use first meeting time for representative data
            representative = meeting_times[0]

            # Check ALL meeting times for conflicts
            for section in meeting_times:
                if section.day and section.mtg_start and section.mtg_end:
                    mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
                    mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
                    mask = TimetableGeneratorV2._mask_for_interval(
                        mtg_start_str, mtg_end_str
                    )

                    if mask & current_day_masks.get(section.day, 0):
                        # Find which course causes the conflict
                        for bit in range(48):
                            if (mask & (1 << bit)) and bit in day_mask_to_course.get(
                                section.day, {}
                            ):
                                conflict_with_course = day_mask_to_course[section.day][
                                    bit
                                ]
                                break
                        conflict_reason = f"Time conflict on {section.day}"
                        has_any_conflict = True
                        break  # Stop checking other meeting times

            # Check exam clash if no time clash found (check representative)
            if (
                not has_any_conflict
                and representative.exam_date
                and representative.exam_start
                and representative.exam_end
            ):
                exam_date_str = representative.exam_date.isoformat()
                exam_start_str = representative.exam_start.strftime("%H:%M:%S")
                exam_end_str = representative.exam_end.strftime("%H:%M:%S")

                for ex_date, ex_start, ex_end in current_exams:
                    if TimetableGeneratorV2._datetime_overlap(
                        exam_date_str,
                        exam_start_str,
                        exam_end_str,
                        ex_date,
                        ex_start,
                        ex_end,
                    ):
                        conflict_with_course = exam_to_course.get(
                            (ex_date, ex_start, ex_end), "Unknown"
                        )
                        conflict_reason = "Exam time conflict"
                        has_any_conflict = True
                        break

            # Calculate seat availability for display (use representative)
            cap = representative.cap_enrl or 0
            tot = representative.tot_enrl or 0
            available = max(0, cap - tot)

            # Note: We no longer mark sections as incompatible due to seat availability
            # Users can swap to any section regardless of capacity

            is_compatible = not has_any_conflict

            # Build meeting times summary for display
            meeting_summary = (
                ", ".join(
                    f"{mt.day} {mt.mtg_start.strftime('%H:%M') if mt.mtg_start else 'TBA'}"
                    for mt in sorted(
                        meeting_times,
                        key=lambda x: (x.day or "", x.mtg_start or time(0, 0)),
                    )
                    if mt.day
                )
                or "TBA"
            )

            # Create one entry per class_nbr
            sections_by_component[component].append(
                CompatibleSectionInfoWithConflict(
                    class_nbr=class_nbr,
                    section_ids=[mt.id for mt in meeting_times],
                    course_id=representative.course_id,
                    subject=course_obj.subject,
                    catalog=course_obj.catalog,
                    title=course_obj.title,
                    component=representative.component,
                    section=representative.section,
                    meeting_times=meeting_summary,
                    exam_date=representative.exam_date,
                    exam_start=representative.exam_start,
                    exam_end=representative.exam_end,
                    instructor=representative.instructor,
                    room=representative.room,
                    cap_enrl=cap,
                    tot_enrl=tot,
                    available_seats=available,
                    max_units=float(course_obj.max_units or 0),
                    is_compatible=is_compatible,
                    conflict_reason=conflict_reason,
                    conflict_with_course=conflict_with_course,
                )
            )

    # Sort each component: compatible sections first (by seats), then incompatible
    for comp in sections_by_component:
        sections_by_component[comp].sort(
            key=lambda s: (not s.is_compatible, -s.available_seats)
        )

    return {
        "success": True,
        "course_id": course_id,
        "subject": course_obj.subject,
        "catalog": course_obj.catalog,
        "title": course_obj.title,
        "components": dict(sections_by_component),
        "current_sections": current_sections,
        "message": f"Found sections for {course_obj.subject} {course_obj.catalog}",
    }

    return {
        "success": True,
        "course_id": course_id,
        "subject": course_obj.subject,
        "catalog": course_obj.catalog,
        "title": course_obj.title,
        "components": dict(sections_by_component),
        "current_sections": current_sections,
        "message": f"Found sections for {course_obj.subject} {course_obj.catalog}",
    }


async def remove_course_from_timetable(
    db: AsyncSession,
    student_id: uuid.UUID,
    course_id: uuid.UUID,
    changed_by_id: uuid.UUID,
) -> tuple[bool, str, Timetable | None]:
    """
    Remove all sections of a course from a student's timetable.

    If timetable source is from_registration and status is committed, changes status to 'edited'.

    Returns (success, message, updated_timetable)
    """
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return False, "No timetable found for student", None

    # Find items to remove
    items_to_remove = []
    section_ids_removed = []
    for item in timetable.items:
        section = item.course_section
        if section and section.course_id == course_id:
            items_to_remove.append(item)
            section_ids_removed.append(item.course_section_id)

    if not items_to_remove:
        return False, "Course not found in timetable", None

    # Remove items and decrease enrollment with lock to prevent race condition
    for item in items_to_remove:
        section = await get_course_section_with_lock(db, item.course_section_id)
        if section and section.tot_enrl > 0:
            section.tot_enrl -= 1
        await db.delete(item)

    # Update timetable status if from registration
    if timetable.source == "from_registration" and timetable.status == "committed":
        timetable.status = "edited"
        timetable.updated_at = datetime.now(timezone.utc)
        timetable.updated_by_id = changed_by_id

    await db.flush()

    # Log to audit trail
    await audit_trail_service.log_remove_course(
        db=db,
        timetable_id=timetable.id,
        changed_by_id=changed_by_id,
        course_id=course_id,
        section_ids=section_ids_removed,
    )

    # Reload timetable
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    return True, "Course removed from timetable", timetable


async def add_course_to_timetable(
    db: AsyncSession,
    student_id: uuid.UUID,
    course_id: uuid.UUID,
    section_ids: list[uuid.UUID] | None = None,
    created_by_id: uuid.UUID | None = None,
) -> tuple[bool, str, Timetable | None, list[dict]]:
    """
    Add a course to a student's existing timetable.

    If section_ids is provided, use those specific sections.
    Otherwise, auto-select the best compatible sections.

    If timetable source is from_registration and status is committed, changes status to 'edited'.

    Returns (success, message, updated_timetable, conflicts)
    """
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return False, "No timetable found for student", None, []

    if not created_by_id:
        raise ValueError("created_by_id is required for audit trail")

    # Check if course is already in timetable
    existing_course_ids = set()
    for item in timetable.items:
        if item.course_section:
            existing_course_ids.add(item.course_section.course_id)

    if course_id in existing_course_ids:
        return False, "Course already in timetable", None, []

    # Check unit limit before proceeding
    course = await db.get(Course, course_id)
    if not course:
        return False, "Course not found", None, []

    current_units = float(timetable.total_units or 0)
    course_units = float(course.max_units or 0)
    max_allowed_units = 25.0

    if current_units + course_units > max_allowed_units:
        return (
            False,
            f"Adding this course would exceed the {max_allowed_units} unit limit "
            f"(current: {current_units}, course: {course_units})",
            None,
            [
                {
                    "type": "unit_limit_exceeded",
                    "message": f"Current units: {current_units}, Course units: {course_units}, Max: {max_allowed_units}",
                }
            ],
        )

    # Get compatible sections
    compat_result = await find_compatible_sections(db, student_id, course_id)

    if not compat_result["success"]:
        return False, compat_result["message"], None, []

    components = compat_result["components"]
    if not components:
        return (
            False,
            "No compatible sections found for this course",
            None,
            [
                {
                    "type": "no_compatible_sections",
                    "message": f"No sections of {compat_result['subject']} {compat_result['catalog']} fit into your current schedule",
                }
            ],
        )

    # Determine which sections to add
    if section_ids:
        # A class is selected as a whole: matching any of its meeting rows selects them all
        requested_ids = set(section_ids)
        sections_to_add = [
            sec
            for sections in components.values()
            for sec in sections
            if requested_ids & set(sec.section_ids)
        ]

        if not sections_to_add:
            return (
                False,
                "Provided sections are not compatible with current schedule",
                None,
                [],
            )
    else:
        # Auto-select: pick best section from each component
        sections_to_add = []
        for comp, sections in components.items():
            if sections:
                # Pick section with most available seats
                sections_to_add.append(sections[0])

    # Add sections to timetable
    added_section_ids = []
    for sec in sections_to_add:
        for meeting_id in sec.section_ids:
            item = TimetableItem(
                timetable_id=timetable.id,
                course_section_id=meeting_id,
            )
            db.add(item)
            added_section_ids.append(meeting_id)

            # Update enrollment with lock to prevent race condition
            section = await get_course_section_with_lock(db, meeting_id)
            if section:
                section.tot_enrl += 1

    # Update timetable total_units
    timetable.total_units = current_units + course_units

    # Update timetable status if from registration
    if timetable.source == "from_registration" and timetable.status == "committed":
        timetable.status = "edited"
        timetable.updated_at = datetime.now(timezone.utc)
        timetable.updated_by_id = created_by_id

    await db.flush()

    # Log to audit trail
    await audit_trail_service.log_add_course(
        db=db,
        timetable_id=timetable.id,
        changed_by_id=created_by_id,
        course_id=course_id,
        section_ids=added_section_ids,
    )

    # Expire first: eager loaders skip collections already loaded in the identity map,
    # so without this the reload would omit the items just added.
    db.expire(timetable)
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    return (
        True,
        f"Added {compat_result['subject']} {compat_result['catalog']} to timetable",
        timetable,
        [],
    )


async def swap_section_in_timetable(
    db: AsyncSession,
    student_id: uuid.UUID,
    old_class_nbr: int,
    new_class_nbr: int,
    changed_by_id: uuid.UUID,
) -> tuple[bool, str, Timetable | None]:
    """
    Swap one section (class) for another in a student's timetable.

    This swaps ALL meeting times of the old class with ALL meeting times of the new class.

    If timetable source is from_registration and status is committed, changes status to 'edited'.

    Returns (success, message, updated_timetable)
    """
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return False, "No timetable found for student", None

    # Find ALL items with the old class_nbr
    items_to_swap = []
    old_section_ids = []
    course_id = None
    component = None

    for item in timetable.items:
        section = item.course_section
        if section and section.class_nbr == old_class_nbr:
            items_to_swap.append(item)
            old_section_ids.append(section.id)
            if course_id is None:
                course_id = section.course_id
                component = section.component

    if not items_to_swap:
        return False, "Section not found in timetable", None

    # Get all sections for the new class_nbr
    new_sections_result = await db.execute(
        select(CourseSection).where(CourseSection.class_nbr == new_class_nbr)
    )
    new_sections = list(new_sections_result.scalars().all())

    if not new_sections:
        return False, "New section not found", None

    # Verify both classes are for the same course and component
    if any(s.course_id != course_id or s.component != component for s in new_sections):
        return False, "Cannot swap sections from different courses or components", None

    # Check if new sections are compatible with rest of schedule
    # Build masks excluding the old sections
    current_day_masks: dict[str, int] = defaultdict(int)
    current_exams: list[tuple] = []

    for item in timetable.items:
        section = item.course_section
        if not section or section.id in old_section_ids:
            continue

        if section.day and section.mtg_start and section.mtg_end:
            mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
            mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
            mask = TimetableGeneratorV2._mask_for_interval(mtg_start_str, mtg_end_str)
            current_day_masks[section.day] |= mask

        if section.exam_date and section.exam_start and section.exam_end:
            current_exams.append(
                (
                    section.exam_date.isoformat(),
                    section.exam_start.strftime("%H:%M:%S"),
                    section.exam_end.strftime("%H:%M:%S"),
                )
            )

    # Check ALL new sections for clashes
    has_clash = False
    for new_section in new_sections:
        if new_section.day and new_section.mtg_start and new_section.mtg_end:
            mtg_start_str = new_section.mtg_start.strftime("%H:%M:%S")
            mtg_end_str = new_section.mtg_end.strftime("%H:%M:%S")
            mask = TimetableGeneratorV2._mask_for_interval(mtg_start_str, mtg_end_str)
            if mask & current_day_masks.get(new_section.day, 0):
                has_clash = True
                break

    # Check exam conflicts (use first section as representative)
    if (
        not has_clash
        and new_sections[0].exam_date
        and new_sections[0].exam_start
        and new_sections[0].exam_end
    ):
        exam_date_str = new_sections[0].exam_date.isoformat()
        exam_start_str = new_sections[0].exam_start.strftime("%H:%M:%S")
        exam_end_str = new_sections[0].exam_end.strftime("%H:%M:%S")
        for ex_date, ex_start, ex_end in current_exams:
            if TimetableGeneratorV2._datetime_overlap(
                exam_date_str, exam_start_str, exam_end_str, ex_date, ex_start, ex_end
            ):
                has_clash = True
                break

    if has_clash:
        return False, "New section conflicts with existing schedule", None

    # Perform swap with locks to prevent race condition
    # Lock all old sections and decrease enrollment
    for old_section_id in old_section_ids:
        old_section_locked = await get_course_section_with_lock(db, old_section_id)
        if old_section_locked and old_section_locked.tot_enrl > 0:
            old_section_locked.tot_enrl -= 1

    # Lock all new sections and increase enrollment
    new_section_ids = []
    for new_section in new_sections:
        new_section_locked = await get_course_section_with_lock(db, new_section.id)
        if not new_section_locked:
            return False, "Could not acquire lock on new sections", None
        new_section_locked.tot_enrl += 1
        new_section_ids.append(new_section.id)

    # Delete old timetable items
    for item in items_to_swap:
        await db.delete(item)

    # Create new timetable items for all new meeting times
    for new_section_id in new_section_ids:
        new_item = TimetableItem(
            id=uuid.uuid4(),
            timetable_id=timetable.id,
            course_section_id=new_section_id,
        )
        db.add(new_item)

    # Update timetable status if from registration
    if timetable.source == "from_registration" and timetable.status == "committed":
        timetable.status = "edited"
        timetable.updated_at = datetime.now(timezone.utc)
        timetable.updated_by_id = changed_by_id

    await db.flush()

    # Log to audit trail
    await audit_trail_service.log_swap_section(
        db=db,
        timetable_id=timetable.id,
        changed_by_id=changed_by_id,
        course_id=course_id,
        old_section_id=old_section_ids[0],  # Use first as representative
        new_section_id=new_section_ids[0],  # Use first as representative
    )

    # Reload timetable
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    return (
        True,
        f"Swapped section {new_sections[0].section} (class #{old_class_nbr} → #{new_class_nbr})",
        timetable,
    )


async def swap_multiple_sections(
    db: AsyncSession,
    student_id: uuid.UUID,
    swaps: list[tuple[int, int]],  # [(old_class_nbr, new_class_nbr), ...]
    changed_by_id: uuid.UUID,
) -> tuple[bool, str, int, list[str], Timetable | None]:
    """
    Swap multiple sections at once atomically.

    This is used for the multi-component swap UI where users can change
    multiple sections of a course at once (e.g., change LEC and TUT together).

    All swaps must succeed or none are applied.

    Returns (success, message, completed_count, failed_reasons, updated_timetable)
    """
    if not swaps:
        return False, "No swaps provided", 0, ["No swaps provided"], None

    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return False, "No timetable found for student", 0, ["No timetable"], None

    # Build map of class_nbr to timetable items
    items_by_class_nbr: dict[int, list[TimetableItem]] = defaultdict(list)
    for item in timetable.items:
        section = item.course_section
        if section:
            items_by_class_nbr[section.class_nbr].append(item)

    # Validate all swaps first
    validated_swaps: list[
        tuple[int, int, list[CourseSection], list[CourseSection]]
    ] = []
    course_id = None
    failed_reasons: list[str] = []

    for old_class_nbr, new_class_nbr in swaps:
        # Skip no-op swaps
        if old_class_nbr == new_class_nbr:
            continue

        # Find items to swap
        if old_class_nbr not in items_by_class_nbr:
            failed_reasons.append(f"Class #{old_class_nbr} not in timetable")
            continue

        old_items = items_by_class_nbr[old_class_nbr]

        # Get old sections
        old_sections = []
        for item in old_items:
            if item.course_section:
                old_sections.append(item.course_section)

        if not old_sections:
            failed_reasons.append(f"No sections found for class #{old_class_nbr}")
            continue

        # Get new sections
        new_sections_result = await db.execute(
            select(CourseSection).where(CourseSection.class_nbr == new_class_nbr)
        )
        new_sections = list(new_sections_result.scalars().all())

        if not new_sections:
            failed_reasons.append(f"Class #{new_class_nbr} not found")
            continue

        # Verify same course
        if old_sections[0].course_id != new_sections[0].course_id:
            failed_reasons.append(
                f"Cannot swap classes from different courses (#{old_class_nbr} -> #{new_class_nbr})"
            )
            continue

        # Verify same component
        if old_sections[0].component != new_sections[0].component:
            failed_reasons.append(
                f"Cannot swap classes of different components ({old_sections[0].component} != {new_sections[0].component})"
            )
            continue

        # Set/verify course_id consistency
        if course_id is None:
            course_id = old_sections[0].course_id
        elif course_id != old_sections[0].course_id:
            failed_reasons.append("Multi-swap must be for same course")
            continue

        validated_swaps.append(
            (old_class_nbr, new_class_nbr, old_sections, new_sections)
        )

    if not validated_swaps:
        if failed_reasons:
            return False, "; ".join(failed_reasons), 0, failed_reasons, None
        return True, "No changes needed (same sections selected)", 0, [], timetable

    # Build time masks excluding sections being swapped
    old_class_nbrs = {old_nbr for old_nbr, _, _, _ in validated_swaps}
    current_day_masks: dict[str, int] = defaultdict(int)
    current_exams: list[tuple] = []

    for item in timetable.items:
        section = item.course_section
        if not section or section.class_nbr in old_class_nbrs:
            continue

        if section.day and section.mtg_start and section.mtg_end:
            mtg_start_str = section.mtg_start.strftime("%H:%M:%S")
            mtg_end_str = section.mtg_end.strftime("%H:%M:%S")
            mask = TimetableGeneratorV2._mask_for_interval(mtg_start_str, mtg_end_str)
            current_day_masks[section.day] |= mask

        if section.exam_date and section.exam_start and section.exam_end:
            current_exams.append(
                (
                    section.exam_date.isoformat(),
                    section.exam_start.strftime("%H:%M:%S"),
                    section.exam_end.strftime("%H:%M:%S"),
                )
            )

    # Check each new section group for conflicts with rest of schedule
    new_section_masks: dict[str, int] = defaultdict(int)
    new_section_exams: list[tuple] = []

    for old_class_nbr, new_class_nbr, old_sections, new_sections in validated_swaps:
        # Check all new meeting times against existing schedule
        for new_section in new_sections:
            if new_section.day and new_section.mtg_start and new_section.mtg_end:
                mtg_start_str = new_section.mtg_start.strftime("%H:%M:%S")
                mtg_end_str = new_section.mtg_end.strftime("%H:%M:%S")
                mask = TimetableGeneratorV2._mask_for_interval(
                    mtg_start_str, mtg_end_str
                )

                if mask & current_day_masks.get(new_section.day, 0):
                    failed_reasons.append(
                        f"Class #{new_class_nbr} conflicts with existing schedule on {new_section.day}"
                    )
                    break

                # Check against other new sections in this swap
                if mask & new_section_masks.get(new_section.day, 0):
                    failed_reasons.append(
                        f"Class #{new_class_nbr} conflicts with another selected section"
                    )
                    break

                new_section_masks[new_section.day] |= mask

        # Check exam conflicts (use first new section as representative)
        if (
            new_sections[0].exam_date
            and new_sections[0].exam_start
            and new_sections[0].exam_end
        ):
            exam_date_str = new_sections[0].exam_date.isoformat()
            exam_start_str = new_sections[0].exam_start.strftime("%H:%M:%S")
            exam_end_str = new_sections[0].exam_end.strftime("%H:%M:%S")

            # Check against existing exams
            for ex_date, ex_start, ex_end in current_exams:
                if TimetableGeneratorV2._datetime_overlap(
                    exam_date_str,
                    exam_start_str,
                    exam_end_str,
                    ex_date,
                    ex_start,
                    ex_end,
                ):
                    failed_reasons.append(f"Class #{new_class_nbr} has exam conflict")
                    break

            # Check against other new sections' exams
            for ex_date, ex_start, ex_end in new_section_exams:
                if TimetableGeneratorV2._datetime_overlap(
                    exam_date_str,
                    exam_start_str,
                    exam_end_str,
                    ex_date,
                    ex_start,
                    ex_end,
                ):
                    failed_reasons.append(
                        f"Class #{new_class_nbr} has exam conflict with another selected section"
                    )
                    break

            new_section_exams.append((exam_date_str, exam_start_str, exam_end_str))

    if failed_reasons:
        return False, "; ".join(failed_reasons), 0, failed_reasons, None

    # All validations passed - perform swaps atomically
    completed_count = 0
    swap_details = []

    for old_class_nbr, new_class_nbr, old_sections, new_sections in validated_swaps:
        # Lock and update enrollments for old sections
        for old_section in old_sections:
            old_locked = await get_course_section_with_lock(db, old_section.id)
            if old_locked and old_locked.tot_enrl > 0:
                old_locked.tot_enrl -= 1

        # Lock and update enrollments for new sections
        for new_section in new_sections:
            new_locked = await get_course_section_with_lock(db, new_section.id)
            if not new_locked:
                return (
                    False,
                    "Could not acquire lock on sections",
                    0,
                    ["Lock failed"],
                    None,
                )
            new_locked.tot_enrl += 1

        # Delete old timetable items
        for item in items_by_class_nbr[old_class_nbr]:
            await db.delete(item)

        # Create new timetable items
        for new_section in new_sections:
            new_item = TimetableItem(
                id=uuid.uuid4(),
                timetable_id=timetable.id,
                course_section_id=new_section.id,
            )
            db.add(new_item)

        completed_count += 1
        swap_details.append(
            f"{old_sections[0].component}: {old_sections[0].section} → {new_sections[0].section}"
        )

    # Update timetable status if from registration
    if timetable.source == "from_registration" and timetable.status == "committed":
        timetable.status = "edited"
        timetable.updated_at = datetime.now(timezone.utc)
        timetable.updated_by_id = changed_by_id

    await db.flush()

    # Log to audit trail - single entry for multi-swap
    if course_id:
        await audit_trail_service.log_multi_swap(
            db=db,
            timetable_id=timetable.id,
            changed_by_id=changed_by_id,
            course_id=course_id,
            swaps=[
                (s[2][0].id, s[3][0].id) for s in validated_swaps
            ],  # Use first sections as representatives
        )

    # Reload timetable
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )

    message = f"Swapped {completed_count} section(s): " + ", ".join(swap_details)
    return True, message, completed_count, [], timetable


async def get_timetable_courses(
    db: AsyncSession,
    student_id: uuid.UUID,
) -> list[dict]:
    """
    Get list of courses in a student's timetable with their sections.

    Returns list of course info with sections grouped by class_nbr.
    Each section entry represents one class with all its meeting times.
    """
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if not timetable:
        return []

    # Group by course, then by class_nbr
    courses_map: dict[uuid.UUID, dict] = {}

    for item in timetable.items:
        section = item.course_section
        if not section:
            continue

        course_id = section.course_id
        if course_id not in courses_map:
            courses_map[course_id] = {
                "course_id": course_id,
                "classes": {},  # class_nbr -> list of meeting times
            }

        class_nbr = section.class_nbr
        if class_nbr not in courses_map[course_id]["classes"]:
            courses_map[course_id]["classes"][class_nbr] = {
                "class_nbr": class_nbr,
                "component": section.component,
                "section": section.section,
                "instructor": section.instructor,
                "room": section.room,
                "exam_date": section.exam_date,
                "exam_start": section.exam_start,
                "exam_end": section.exam_end,
                "meeting_times": [],
            }

        # Add this meeting time
        courses_map[course_id]["classes"][class_nbr]["meeting_times"].append(
            {
                "section_id": section.id,
                "day": section.day,
                "mtg_start": section.mtg_start,
                "mtg_end": section.mtg_end,
            }
        )

    # Convert to list format
    result = []
    for course_data in courses_map.values():
        result.append(
            {
                "course_id": course_data["course_id"],
                "sections": list(course_data["classes"].values()),
            }
        )

    return result


# ==================== Revert to Registration ====================


async def get_registration_timetable(
    db: AsyncSession, student_id: uuid.UUID
) -> RegistrationTimetable | None:
    """
    Get registration timetable with items loaded.

    The registration timetable is the immutable baseline imported from registration data.
    """
    result = await db.execute(
        select(RegistrationTimetable)
        .where(RegistrationTimetable.student_id == student_id)
        .options(
            selectinload(RegistrationTimetable.items).selectinload(
                RegistrationTimetableItem.course_section
            )
        )
    )
    return result.scalar_one_or_none()


async def revert_to_registration(
    db: AsyncSession,
    student_id: uuid.UUID,
    changed_by_id: uuid.UUID,
) -> tuple[bool, str, int, Timetable | None]:
    """
    Revert a timetable to its original registration state.

    This operation:
    1. Gets the registration timetable (immutable baseline)
    2. Deletes the current timetable, decrementing enrollments
    3. Creates a new timetable from original registration items
    4. Increments enrollments, logs audit trail
    5. Returns the restored timetable

    Returns (success, message, restored_count, timetable)
    """
    # 1. Get registration timetable
    reg_timetable = await get_registration_timetable(db, student_id)
    if not reg_timetable:
        return (False, "No registration timetable found for this student", 0, None)

    # 2. Get original items from registration timetable
    original_items = reg_timetable.items
    if not original_items:
        return (False, "Registration timetable has no items", 0, None)

    # 3. Get current timetable and decrement enrollments
    current_timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    if current_timetable:
        # Decrement enrollment counts for current items with lock
        for item in current_timetable.items:
            section = await get_course_section_with_lock(db, item.course_section_id)
            if section and section.tot_enrl > 0:
                section.tot_enrl -= 1

        # Log the revert before deletion
        await audit_trail_service.log_audit_trail(
            db=db,
            timetable_id=current_timetable.id,
            action="revert_to_registration_delete",
            changed_by_id=changed_by_id,
            details={"section_count": len(current_timetable.items)},
            note="Deleting current timetable to revert to registration",
        )

        # Delete current timetable (cascade will delete items)
        await db.delete(current_timetable)
        await db.flush()

    # 4. Calculate total units from original registration items
    total_units = 0.0
    course_ids_seen: set[uuid.UUID] = set()
    section_ids: list[uuid.UUID] = []

    for item in original_items:
        section = item.course_section
        if section:
            section_ids.append(section.id)
            if section.course_id not in course_ids_seen:
                course = await db.get(Course, section.course_id)
                if course and course.max_units:
                    total_units += float(course.max_units)
                course_ids_seen.add(section.course_id)

    # 5. Create new timetable from registration
    new_timetable = Timetable(
        student_id=student_id,
        source="from_registration",
        status="committed",  # Reverted = committed state
        created_by_id=changed_by_id,
        total_units=total_units,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_timetable)
    await db.flush()

    # 6. Add items and increment enrollments
    for section_id in section_ids:
        item = TimetableItem(
            timetable_id=new_timetable.id,
            course_section_id=section_id,
        )
        db.add(item)

        # Increment enrollment with lock
        section = await get_course_section_with_lock(db, section_id)
        if section:
            section.tot_enrl += 1

    await db.flush()

    # 7. Log to audit trail
    await audit_trail_service.log_audit_trail(
        db=db,
        timetable_id=new_timetable.id,
        action="revert_to_registration",
        changed_by_id=changed_by_id,
        details={
            "restored_section_ids": [str(sid) for sid in section_ids],
            "total_units": total_units,
        },
        note="Reverted to original registration data",
    )

    await db.flush()

    # 8. Reload and return
    timetable = await get_timetable_by_student(
        db, student_id, include_all_statuses=False
    )
    return (True, "Reverted to original registration", len(section_ids), timetable)
