"""Tests for adding a course to an existing student timetable."""

import uuid
from datetime import datetime, time, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models.session import Session
from app.services.timetable_service import add_course_to_timetable

SCHEMA_DDL = [
    '''DO $$ BEGIN
        CREATE TYPE "{schema}".timetable_source AS ENUM ('portal_generated', 'from_registration');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$''',
    '''DO $$ BEGIN
        CREATE TYPE "{schema}".timetable_status AS ENUM ('draft', 'committed', 'edited');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$''',
    '''CREATE TABLE IF NOT EXISTS "{schema}".timetables (
        id UUID PRIMARY KEY,
        student_id UUID NOT NULL REFERENCES "{schema}".students(id),
        source "{schema}".timetable_source NOT NULL DEFAULT 'portal_generated',
        status "{schema}".timetable_status NOT NULL DEFAULT 'draft',
        created_at TIMESTAMPTZ NOT NULL,
        created_by_id UUID NOT NULL,
        total_units NUMERIC(5, 2),
        updated_at TIMESTAMPTZ,
        updated_by_id UUID,
        buffer_timetable_id UUID
    )''',
    '''CREATE TABLE IF NOT EXISTS "{schema}".timetable_items (
        id UUID PRIMARY KEY,
        timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
        course_section_id UUID NOT NULL REFERENCES "{schema}".course_sections(id)
    )''',
    '''CREATE TABLE IF NOT EXISTS "{schema}".timetable_audit_trail (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
        action TEXT NOT NULL,
        details JSONB,
        changed_at TIMESTAMPTZ NOT NULL,
        changed_by_id UUID NOT NULL,
        note TEXT
    )''',
]


async def _insert_class(conn, schema, course_id, class_nbr, component, section, hour):
    """Insert one class with two meeting rows (Mon + Wed) and return its row ids."""
    ids = []
    for day in ("M", "W"):
        section_id = uuid.uuid4()
        ids.append(section_id)
        await conn.execute(
            text(f'''
                INSERT INTO "{schema}".course_sections
                (id, course_id, class_nbr, section, component, day, mtg_start, mtg_end,
                 instructor, room, cap_enrl, tot_enrl)
                VALUES (:id, :course_id, :class_nbr, :section, :component, :day,
                        :mtg_start, :mtg_end, 'Dr. Test', 'R1', 50, 10)
            '''),
            {
                "id": section_id,
                "course_id": course_id,
                "class_nbr": class_nbr,
                "section": section,
                "component": component,
                "day": day,
                "mtg_start": time(hour, 0),
                "mtg_end": time(hour, 50),
            },
        )
    return ids


@pytest.fixture
def schema_engine(test_session_with_schema: Session):
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={
            "server_settings": {
                "search_path": f'"{test_session_with_schema.schema_name}", public'
            }
        },
    )


async def _make_course(conn, units):
    """Insert a course into the global catalog with a collision-free identity."""
    course_id = uuid.uuid4()
    tag = uuid.uuid4().hex[:10].upper()
    await conn.execute(
        text(
            "INSERT INTO public.courses "
            "(id, course_id, subject, catalog, title, max_units, created_at) "
            "VALUES (:id, :cid, :subject, :catalog, :title, :units, :created_at)"
        ),
        {
            "id": course_id,
            "cid": tag,
            "subject": f"TST{tag}",
            "catalog": tag,
            "title": f"Test Course {tag}",
            "units": units,
            "created_at": datetime.now(timezone.utc),
        },
    )
    return course_id


async def _setup(conn, schema, admin_user):
    """Create schema tables, a student, and a timetable holding one 09:00 course."""
    for stmt in SCHEMA_DDL:
        await conn.execute(text(stmt.format(schema=schema)))

    existing_course_id = await _make_course(conn, 3.0)
    new_course_id = await _make_course(conn, 4.0)

    student_id = uuid.uuid4()
    timetable_id = uuid.uuid4()

    await conn.execute(
        text(
            f'INSERT INTO "{schema}".students (id, student_id, campus_id, name) '
            "VALUES (:id, 1, '2024A3PS0001P', 'Test Student')"
        ),
        {"id": student_id},
    )
    await conn.execute(
        text(
            f'INSERT INTO "{schema}".timetables '
            "(id, student_id, source, status, created_at, created_by_id, total_units) "
            "VALUES (:id, :student_id, 'portal_generated', 'committed', :created_at, :by, 3.0)"
        ),
        {
            "id": timetable_id,
            "student_id": student_id,
            "created_at": datetime.now(timezone.utc),
            "by": admin_user.id,
        },
    )

    # Existing course occupies the 09:00 slot
    existing_ids = await _insert_class(
        conn, schema, existing_course_id, 30001, "LEC", "L1", 9
    )
    for section_id in existing_ids:
        await conn.execute(
            text(
                f'INSERT INTO "{schema}".timetable_items (id, timetable_id, course_section_id) '
                "VALUES (:id, :tt, :cs)"
            ),
            {"id": uuid.uuid4(), "tt": timetable_id, "cs": section_id},
        )

    # Candidate course sits at 11:00, so it fits alongside the existing one
    new_ids = await _insert_class(conn, schema, new_course_id, 30002, "LEC", "L1", 11)

    return student_id, new_course_id, new_ids


@pytest.mark.asyncio
async def test_add_course_adds_every_meeting_row(
    test_session_with_schema: Session,
    admin_user,
    schema_engine,
):
    """Selecting a class adds all of its meeting rows and bumps each row's enrollment."""
    schema = test_session_with_schema.schema_name

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        db = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            student_id, new_course_id, new_ids = await _setup(
                conn, schema, admin_user
            )

            # The UI sends one id per component; the whole class must still be added
            success, message, timetable, conflicts = await add_course_to_timetable(
                db,
                student_id,
                new_course_id,
                section_ids=[new_ids[0]],
                created_by_id=admin_user.id,
            )

            assert success, message
            assert conflicts == []

            added = {
                item.course_section_id
                for item in timetable.items
                if item.course_section_id in set(new_ids)
            }
            assert added == set(new_ids)
            assert len(timetable.items) == 4

            enrl = await db.execute(
                text(
                    f'SELECT id, tot_enrl FROM "{schema}".course_sections '
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": new_ids},
            )
            assert {row.tot_enrl for row in enrl} == {11}
        finally:
            await db.close()
            await trans.rollback()

    await schema_engine.dispose()


@pytest.mark.asyncio
async def test_add_course_auto_selects_sections(
    test_session_with_schema: Session,
    admin_user,
    schema_engine,
):
    """Omitting section_ids auto-picks a compatible class and adds all its meetings."""
    schema = test_session_with_schema.schema_name

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        db = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            student_id, new_course_id, _new_ids = await _setup(
                conn, schema, admin_user
            )

            success, message, timetable, _ = await add_course_to_timetable(
                db,
                student_id,
                new_course_id,
                section_ids=None,
                created_by_id=admin_user.id,
            )

            assert success, message
            assert len(timetable.items) == 4
            assert float(timetable.total_units) == 7.0
        finally:
            await db.close()
            await trans.rollback()

    await schema_engine.dispose()


@pytest.mark.asyncio
async def test_add_course_rejects_conflicting_class(
    test_session_with_schema: Session,
    admin_user,
    schema_engine,
):
    """A class overlapping the existing schedule is reported, not silently added."""
    schema = test_session_with_schema.schema_name

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        db = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            student_id, _new_course_id, _new_ids = await _setup(
                conn, schema, admin_user
            )

            # Third course only offered at 09:00, clashing with the existing course
            clashing_course_id = await _make_course(conn, 4.0)
            clashing_ids = await _insert_class(
                conn, schema, clashing_course_id, 30003, "LEC", "L1", 9
            )

            success, message, timetable, conflicts = await add_course_to_timetable(
                db,
                student_id,
                clashing_course_id,
                section_ids=[clashing_ids[0]],
                created_by_id=admin_user.id,
            )

            assert not success
            assert timetable is None
            assert conflicts and conflicts[0]["type"] == "no_compatible_sections"
        finally:
            await db.close()
            await trans.rollback()

    await schema_engine.dispose()
