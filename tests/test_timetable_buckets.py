"""Tests for capacity-based timetable bucket enumeration."""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import select, text

from app.models.buffer_timetable import BufferTimetable
from app.services.timetable_bucket_save_service import (
    BucketSaveInput,
    apply_bucket_save_limits,
    delete_buffer_timetable_with_enrollment_release,
    save_buckets_as_buffer_timetables,
)
from app.services.timetable_service import SectionWithCourseInfo, TimetableGeneratorV2
from app.models.course_section import CourseSection


def _t(hhmm: str):
    return datetime.strptime(hhmm, "%H:%M").time()


def test_enumerate_buckets_single_solution_capacity():
    """Two non-conflicting courses → one bucket; capacity = min section availability."""
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()

    s1 = [
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=c1,
            class_nbr=101,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="M",
            mtg_start=_t("09:00"),
            mtg_end=_t("09:50"),
            exam_date=date(2025, 5, 1),
            exam_start=_t("09:00"),
            exam_end=_t("12:00"),
            instructor="A",
            room="R1",
            cap_enrl=100,
            tot_enrl=40,
            subject="AA",
            catalog="101",
            title="A",
            max_units=3.0,
        ),
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=c1,
            class_nbr=102,
            section="T1",
            component="TUT",
            class_pattern="T",
            day="T",
            mtg_start=_t("10:00"),
            mtg_end=_t("10:50"),
            exam_date=None,
            exam_start=None,
            exam_end=None,
            instructor="B",
            room="R2",
            cap_enrl=20,
            tot_enrl=5,
            subject="AA",
            catalog="101",
            title="A",
            max_units=3.0,
        ),
    ]
    s2 = [
        SectionWithCourseInfo(
            id=uuid.uuid4(),
            course_id=c2,
            class_nbr=201,
            section="L1",
            component="LEC",
            class_pattern="MWF",
            day="W",
            mtg_start=_t("11:00"),
            mtg_end=_t("11:50"),
            exam_date=date(2025, 5, 2),
            exam_start=_t("14:00"),
            exam_end=_t("17:00"),
            instructor="C",
            room="R3",
            cap_enrl=50,
            tot_enrl=10,
            subject="BB",
            catalog="102",
            title="B",
            max_units=3.0,
        ),
    ]

    gen = TimetableGeneratorV2(max_units=25.0)
    out = gen.enumerate_buckets({c1: s1, c2: s2}, max_buckets=50, max_search_nodes=100_000)

    assert out.success
    assert len(out.buckets) == 1
    b0 = out.buckets[0]
    # min(100-40, 20-5, 50-10) = min(60, 15, 40) = 15
    assert b0.capacity == 15
    assert b0.total_units == 6.0
    assert len(b0.section_ids) == 3


def test_enumerate_buckets_multiple_tuts():
    """Two disjoint TUT options with same LEC → two buckets."""
    c1 = uuid.uuid4()

    lec_m = SectionWithCourseInfo(
        id=uuid.uuid4(),
        course_id=c1,
        class_nbr=1,
        section="L1",
        component="LEC",
        class_pattern="M",
        day="M",
        mtg_start=_t("09:00"),
        mtg_end=_t("10:00"),
        exam_date=None,
        exam_start=None,
        exam_end=None,
        instructor="P",
        room="X",
        cap_enrl=80,
        tot_enrl=0,
        subject="X",
        catalog="1",
        title="X",
        max_units=2.0,
    )
    tut_t = SectionWithCourseInfo(
        id=uuid.uuid4(),
        course_id=c1,
        class_nbr=2,
        section="T1",
        component="TUT",
        class_pattern="T",
        day="T",
        mtg_start=_t("11:00"),
        mtg_end=_t("12:00"),
        exam_date=None,
        exam_start=None,
        exam_end=None,
        instructor="Q",
        room="Y",
        cap_enrl=10,
        tot_enrl=0,
        subject="X",
        catalog="1",
        title="X",
        max_units=2.0,
    )
    tut_w = SectionWithCourseInfo(
        id=uuid.uuid4(),
        course_id=c1,
        class_nbr=3,
        section="T2",
        component="TUT",
        class_pattern="W",
        day="W",
        mtg_start=_t("11:00"),
        mtg_end=_t("12:00"),
        exam_date=None,
        exam_start=None,
        exam_end=None,
        instructor="R",
        room="Z",
        cap_enrl=25,
        tot_enrl=0,
        subject="X",
        catalog="1",
        title="X",
        max_units=2.0,
    )

    gen = TimetableGeneratorV2(max_units=25.0)
    out = gen.enumerate_buckets(
        {c1: [lec_m, tut_t, tut_w]}, max_buckets=50, max_search_nodes=100_000
    )

    assert out.success
    assert len(out.buckets) == 2
    caps = sorted(b.capacity for b in out.buckets)
    assert caps == [10, 25]
    assert out.buckets_omitted_zero_capacity == 0


def test_apply_bucket_save_limits_prefers_exact_capacity():
    buckets = [
        BucketSaveInput(capacity=25, section_ids=[uuid.uuid4()]),
        BucketSaveInput(capacity=10, section_ids=[uuid.uuid4()]),
        BucketSaveInput(capacity=5, section_ids=[uuid.uuid4()]),
    ]
    out = apply_bucket_save_limits(buckets, max_buckets_to_save=None, max_total_capacity=10)
    assert len(out) == 1
    assert out[0].capacity == 10


def test_apply_bucket_save_limits_truncates_larger_bucket():
    sid = uuid.uuid4()
    buckets = [
        BucketSaveInput(capacity=25, section_ids=[sid]),
        BucketSaveInput(capacity=8, section_ids=[uuid.uuid4()]),
    ]
    out = apply_bucket_save_limits(buckets, max_buckets_to_save=None, max_total_capacity=10)
    assert len(out) == 1
    assert out[0].capacity == 10
    assert out[0].section_ids == [sid]


def test_apply_bucket_save_limits_combines_smaller_buckets():
    buckets = [
        BucketSaveInput(capacity=7, section_ids=[uuid.uuid4()]),
        BucketSaveInput(capacity=7, section_ids=[uuid.uuid4()]),
    ]
    out = apply_bucket_save_limits(buckets, max_buckets_to_save=None, max_total_capacity=10)
    assert [b.capacity for b in out] == [7, 3]


def test_apply_bucket_save_limits_count_only():
    buckets = [
        BucketSaveInput(capacity=5, section_ids=[]),
        BucketSaveInput(capacity=20, section_ids=[]),
        BucketSaveInput(capacity=10, section_ids=[]),
        BucketSaveInput(capacity=0, section_ids=[]),
    ]
    out = apply_bucket_save_limits(buckets, max_buckets_to_save=2, max_total_capacity=None)
    assert [b.capacity for b in out] == [20, 10]


def test_enumerate_buckets_no_positive_capacity():
    """Full sections yield capacity 0; response must not claim success with buckets."""
    c1 = uuid.uuid4()
    lec = SectionWithCourseInfo(
        id=uuid.uuid4(),
        course_id=c1,
        class_nbr=9,
        section="L1",
        component="LEC",
        class_pattern="M",
        day="M",
        mtg_start=_t("09:00"),
        mtg_end=_t("10:00"),
        exam_date=None,
        exam_start=None,
        exam_end=None,
        instructor="P",
        room="R",
        cap_enrl=30,
        tot_enrl=30,
        subject="Z",
        catalog="9",
        title="Z",
        max_units=3.0,
    )
    gen = TimetableGeneratorV2(max_units=25.0)
    out = gen.enumerate_buckets({c1: [lec]}, max_buckets=20, max_search_nodes=50_000)
    assert not out.success
    assert out.buckets == []
    assert out.buckets_omitted_zero_capacity >= 1


@pytest.mark.asyncio
async def test_save_buckets_reserves_seats_by_class_nbr(
    db,
    session_db,
    test_session_with_schema,
    admin_user,
    sample_sections,
):
    """Saving with enrollment_deducted_on_upload increases tot_enrl on all meeting rows."""
    schema = test_session_with_schema.schema_name
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetables (
                id UUID PRIMARY KEY,
                plan TEXT NOT NULL,
                timetable_id INTEGER NOT NULL,
                batch_size INTEGER NOT NULL DEFAULT 0,
                capacity_ceiling INTEGER NOT NULL DEFAULT 0,
                assigned_count INTEGER NOT NULL DEFAULT 0,
                is_variant BOOLEAN NOT NULL DEFAULT FALSE,
                enrollment_deducted_on_upload BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                created_by_id UUID NOT NULL,
                label TEXT
            )
            '''
        )
    )
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetable_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                buffer_timetable_id UUID NOT NULL REFERENCES "{schema}".buffer_timetables(id),
                course_section_id UUID NOT NULL,
                course_code TEXT NOT NULL,
                component TEXT NOT NULL,
                section TEXT NOT NULL
            )
            '''
        )
    )
    await session_db.flush()

    sec = sample_sections[0]
    bucket_cap = 5
    created, skipped, warnings = await save_buckets_as_buffer_timetables(
        db,
        session_db,
        plan="A7",
        buckets=[
            BucketSaveInput(
                capacity=bucket_cap,
                section_ids=sec["section_ids"],
            )
        ],
        created_by_id=admin_user.id,
        enrollment_deducted_on_upload=True,
    )
    assert created == 1
    assert skipped == 0
    assert not warnings

    result = await session_db.execute(
        select(CourseSection.tot_enrl).where(
            CourseSection.class_nbr == sec["class_nbr"]
        )
    )
    totals = [row[0] for row in result.fetchall()]
    assert len(totals) == 2
    assert all(t == sec["tot_enrl"] + bucket_cap for t in totals)


@pytest.mark.asyncio
async def test_save_buckets_skips_when_reservation_exceeds_capacity(
    db,
    session_db,
    test_session_with_schema,
    admin_user,
    sample_sections,
):
    """Second bucket is skipped if combined reservation would exceed section cap."""
    schema = test_session_with_schema.schema_name
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetables (
                id UUID PRIMARY KEY,
                plan TEXT NOT NULL,
                timetable_id INTEGER NOT NULL,
                batch_size INTEGER NOT NULL DEFAULT 0,
                capacity_ceiling INTEGER NOT NULL DEFAULT 0,
                assigned_count INTEGER NOT NULL DEFAULT 0,
                is_variant BOOLEAN NOT NULL DEFAULT FALSE,
                enrollment_deducted_on_upload BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                created_by_id UUID NOT NULL,
                label TEXT
            )
            '''
        )
    )
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetable_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                buffer_timetable_id UUID NOT NULL REFERENCES "{schema}".buffer_timetables(id),
                course_section_id UUID NOT NULL,
                course_code TEXT NOT NULL,
                component TEXT NOT NULL,
                section TEXT NOT NULL
            )
            '''
        )
    )
    await session_db.flush()

    sec = sample_sections[0]
    available = sec["cap_enrl"] - sec["tot_enrl"]
    first_cap = available - 2
    second_cap = 5

    created, skipped, warnings = await save_buckets_as_buffer_timetables(
        db,
        session_db,
        plan="A7",
        buckets=[
            BucketSaveInput(capacity=first_cap, section_ids=sec["section_ids"]),
            BucketSaveInput(capacity=second_cap, section_ids=sec["section_ids"]),
        ],
        created_by_id=admin_user.id,
        enrollment_deducted_on_upload=True,
    )
    assert created == 1
    assert skipped == 1
    assert any("only" in w and "seat" in w for w in warnings)


@pytest.mark.asyncio
async def test_release_buffer_enrollment_on_delete(
    db,
    session_db,
    test_session_with_schema,
    admin_user,
    sample_sections,
):
    """Deleting a buffer with reserved seats restores unassigned tot_enrl."""
    schema = test_session_with_schema.schema_name
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetables (
                id UUID PRIMARY KEY,
                plan TEXT NOT NULL,
                timetable_id INTEGER NOT NULL,
                batch_size INTEGER NOT NULL DEFAULT 0,
                capacity_ceiling INTEGER NOT NULL DEFAULT 0,
                assigned_count INTEGER NOT NULL DEFAULT 0,
                is_variant BOOLEAN NOT NULL DEFAULT FALSE,
                enrollment_deducted_on_upload BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL,
                created_by_id UUID NOT NULL,
                label TEXT
            )
            '''
        )
    )
    await session_db.execute(
        text(
            f'''
            CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetable_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                buffer_timetable_id UUID NOT NULL REFERENCES "{schema}".buffer_timetables(id),
                course_section_id UUID NOT NULL,
                course_code TEXT NOT NULL,
                component TEXT NOT NULL,
                section TEXT NOT NULL
            )
            '''
        )
    )
    await session_db.flush()

    sec = sample_sections[0]
    cap = 8
    created, _, _ = await save_buckets_as_buffer_timetables(
        db,
        session_db,
        plan="A7",
        buckets=[BucketSaveInput(capacity=cap, section_ids=sec["section_ids"])],
        created_by_id=admin_user.id,
        enrollment_deducted_on_upload=True,
    )
    assert created == 1

    result = await session_db.execute(
        select(CourseSection.tot_enrl).where(
            CourseSection.class_nbr == sec["class_nbr"]
        )
    )
    assert all(row[0] == sec["tot_enrl"] + cap for row in result.fetchall())

    bt_result = await session_db.execute(select(BufferTimetable))
    buffer_tt = bt_result.scalars().one()

    found, released = await delete_buffer_timetable_with_enrollment_release(
        session_db, buffer_tt.id
    )
    assert found
    assert released == cap

    result = await session_db.execute(
        select(CourseSection.tot_enrl).where(
            CourseSection.class_nbr == sec["class_nbr"]
        )
    )
    assert all(row[0] == sec["tot_enrl"] for row in result.fetchall())
