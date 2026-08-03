"""Persist enumerated timetable buckets as buffer timetables for student assignment."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.buffer_timetable import BufferTimetable, BufferTimetableItem
from app.models.course import Course
from app.models.course_section import CourseSection


@dataclass
class BucketSaveInput:
    capacity: int
    section_ids: list[uuid.UUID]


def _bucket_copy(b: BucketSaveInput, *, capacity: int) -> BucketSaveInput:
    return BucketSaveInput(capacity=capacity, section_ids=list(b.section_ids))


def _select_for_total_capacity(
    positive: list[BucketSaveInput],
    target: int,
    max_buckets_to_save: int | None,
) -> list[BucketSaveInput]:
    """
    Pick bucket(s) whose saved capacities sum to ``target``.

    1. Prefer one bucket with capacity == target.
    2. Else one bucket with capacity > target (smallest such), saved at ``target``.
    3. Else combine smaller buckets (largest first), truncating the last if needed.
    """
    if target < 1:
        return []

    exact = [b for b in positive if b.capacity == target]
    if exact:
        pick = max(exact, key=lambda b: len(b.section_ids))
        return [_bucket_copy(pick, capacity=target)]

    larger = sorted(
        [b for b in positive if b.capacity > target],
        key=lambda b: (b.capacity, -len(b.section_ids)),
    )
    if larger:
        return [_bucket_copy(larger[0], capacity=target)]

    ordered = sorted(positive, key=lambda b: (-b.capacity, -len(b.section_ids)))
    out: list[BucketSaveInput] = []
    remaining = target
    for b in ordered:
        if max_buckets_to_save is not None and len(out) >= max_buckets_to_save:
            break
        if remaining <= 0:
            break
        take = min(b.capacity, remaining)
        out.append(_bucket_copy(b, capacity=take))
        remaining -= take

    if remaining > 0:
        return []
    return out


def apply_bucket_save_limits(
    buckets: list[BucketSaveInput],
    *,
    max_buckets_to_save: int | None,
    max_total_capacity: int | None,
) -> list[BucketSaveInput]:
    """
    Drop non-positive capacity, then apply save limits.

    When ``max_total_capacity`` is set, select bucket(s) whose saved capacities
    sum to that target (exact single bucket preferred, else truncate a larger
    bucket, else combine smaller ones).

    When only ``max_buckets_to_save`` is set, take highest-capacity buckets up
    to that count.
    """
    positive = [b for b in buckets if b.capacity > 0]

    if max_total_capacity is not None:
        selected = _select_for_total_capacity(
            positive, max_total_capacity, max_buckets_to_save
        )
        if max_buckets_to_save is not None:
            return selected[:max_buckets_to_save]
        return selected

    ordered = sorted(
        positive,
        key=lambda b: (-b.capacity, -len(b.section_ids)),
    )
    if max_buckets_to_save is not None:
        return ordered[:max_buckets_to_save]
    return ordered


async def save_buckets_as_buffer_timetables(
    global_db: AsyncSession,
    session_db: AsyncSession,
    *,
    plan: str,
    buckets: list[BucketSaveInput],
    created_by_id: uuid.UUID,
    enrollment_deducted_on_upload: bool,
) -> tuple[int, int, list[str]]:
    """
    Create BufferTimetable + BufferTimetableItem rows from enumerated buckets.

    Each bucket becomes one buffer timetable with ``batch_size == capacity`` so
    ``assign-buffer`` allows that many students before ``is_full``.

    ``section_ids`` may list every meeting row; items are deduped to one row per
    ``class_nbr`` (same convention as CSV buffer upload).

    Returns:
        (created_count, skipped_count, warnings)
    """
    warnings: list[str] = []
    if not buckets:
        return 0, 0, ["No buckets to save"]

    max_tid = await session_db.scalar(
        select(func.coalesce(func.max(BufferTimetable.timetable_id), 0)).where(
            BufferTimetable.plan == plan
        )
    )
    next_tid = int(max_tid or 0)

    created = 0
    skipped = 0
    enrollment_updates: dict[int, int] = {}
    pending_reservations: dict[int, int] = {}

    for bucket in buckets:
        cap = bucket.capacity
        if cap < 1:
            warnings.append(
                f"Skipped one bucket with capacity {cap} (need at least 1 assignable seat)"
            )
            skipped += 1
            continue

        if not bucket.section_ids:
            warnings.append("Skipped one bucket with no section IDs")
            skipped += 1
            continue

        sec_result = await session_db.execute(
            select(CourseSection).where(CourseSection.id.in_(bucket.section_ids))
        )
        sections = list(sec_result.scalars().all())
        if not sections:
            warnings.append("Skipped one bucket: no matching course sections in DB")
            skipped += 1
            continue

        by_class_nbr: dict[int, CourseSection] = {}
        for s in sections:
            if s.class_nbr not in by_class_nbr:
                by_class_nbr[s.class_nbr] = s

        course_ids = {s.course_id for s in by_class_nbr.values()}
        crs_result = await global_db.execute(select(Course).where(Course.id.in_(course_ids)))
        course_map = {c.id: f"{c.subject} {c.catalog}" for c in crs_result.scalars().all()}

        item_specs: list[tuple[CourseSection, str]] = []
        for s in by_class_nbr.values():
            code = course_map.get(s.course_id, "")
            if not code:
                warnings.append(
                    f"Missing global course row for class_nbr={s.class_nbr}; "
                    "that section was omitted from one bucket"
                )
                continue
            item_specs.append((s, code))

        if not item_specs:
            warnings.append("Skipped one bucket: no valid items after deduplication")
            skipped += 1
            continue

        if enrollment_deducted_on_upload:
            over_capacity = False
            for s, _ in item_specs:
                cap_enrl = s.cap_enrl
                if cap_enrl is None:
                    continue
                reserved = pending_reservations.get(s.class_nbr, 0)
                available = max(0, cap_enrl - (s.tot_enrl or 0) - reserved)
                if cap > available:
                    warnings.append(
                        f"Skipped one bucket (cap {cap}): section {s.section} "
                        f"{s.component} class_nbr={s.class_nbr} has only {available} "
                        f"seat(s) left after prior reservations in this save"
                    )
                    over_capacity = True
                    break
            if over_capacity:
                skipped += 1
                continue

        next_tid += 1
        label = f"Bucket · cap {cap} · {len(item_specs)} section(s)"
        bt = BufferTimetable(
            plan=plan,
            timetable_id=next_tid,
            batch_size=cap,
            capacity_ceiling=cap,
            assigned_count=0,
            is_variant=False,
            enrollment_deducted_on_upload=enrollment_deducted_on_upload,
            created_at=datetime.now(timezone.utc),
            created_by_id=created_by_id,
            label=label,
        )
        session_db.add(bt)
        await session_db.flush()

        for s, code in item_specs:
            session_db.add(
                BufferTimetableItem(
                    buffer_timetable_id=bt.id,
                    course_section_id=s.id,
                    course_code=code,
                    component=s.component,
                    section=s.section,
                )
            )
            if enrollment_deducted_on_upload:
                enrollment_updates[s.class_nbr] = (
                    enrollment_updates.get(s.class_nbr, 0) + cap
                )
                pending_reservations[s.class_nbr] = (
                    pending_reservations.get(s.class_nbr, 0) + cap
                )

        created += 1

    if enrollment_deducted_on_upload and enrollment_updates:
        for class_nbr, count in enrollment_updates.items():
            await session_db.execute(
                update(CourseSection)
                .where(CourseSection.class_nbr == class_nbr)
                .values(tot_enrl=CourseSection.tot_enrl + count)
            )

    return created, skipped, warnings


async def release_buffer_timetable_enrollment(
    session_db: AsyncSession,
    buffer_tt: BufferTimetable,
) -> int:
    """
    Release seats still held by a buffer when it is deleted.

    If seats were reserved on save/upload (``enrollment_deducted_on_upload``),
    decreases ``tot_enrl`` by the unassigned portion
    (``batch_size - assigned_count``) for each logical section (``class_nbr``).

    Assigned students keep their enrollment; only unused buffer capacity is freed.

    Returns:
        Total seats released per logical section (``release_count``), or 0 if none.
    """
    if not buffer_tt.enrollment_deducted_on_upload:
        return 0

    release_count = max(0, buffer_tt.batch_size - buffer_tt.assigned_count)
    if release_count == 0:
        return 0

    items_result = await session_db.execute(
        select(BufferTimetableItem.course_section_id).where(
            BufferTimetableItem.buffer_timetable_id == buffer_tt.id
        )
    )
    section_ids = {row[0] for row in items_result.fetchall()}
    if not section_ids:
        return 0
    sec_result = await session_db.execute(
        select(CourseSection.class_nbr).where(CourseSection.id.in_(section_ids))
    )
    class_nbrs = {row[0] for row in sec_result.fetchall()}

    for class_nbr in class_nbrs:
        await session_db.execute(
            update(CourseSection)
            .where(CourseSection.class_nbr == class_nbr)
            .values(
                tot_enrl=func.greatest(0, CourseSection.tot_enrl - release_count)
            )
        )

    return release_count


async def delete_buffer_timetable_with_enrollment_release(
    session_db: AsyncSession,
    buffer_timetable_id: uuid.UUID,
) -> tuple[bool, int]:
    """
    Delete a buffer timetable and release any reserved-but-unassigned seats.

    Returns:
        (found, seats_released_per_section)
    """
    buffer_tt = await session_db.get(BufferTimetable, buffer_timetable_id)
    if not buffer_tt:
        return False, 0

    released = await release_buffer_timetable_enrollment(session_db, buffer_tt)
    await session_db.delete(buffer_tt)
    return True, released
