"""Tests for timetable transfer functionality."""

import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models.session import Session
from app.services.export_service import export_transfer_timetables
from app.services.timetable_service import get_timetable_by_student, transfer_timetable


@pytest.mark.asyncio
async def test_export_transfer_timetables_creates_named_files(tmp_path, monkeypatch):
    """Transfer export writes ddmm_hhmm delete/add xlsx files and a zip bundle."""
    monkeypatch.chdir(tmp_path)
    timestamp = "1706_1430"

    delete_path, add_path, zip_path, ts = export_transfer_timetables(
        source_campus_id="2024A3PS0309P",
        target_campus_id="2024A3PS0310P",
        class_nbrs=[12345, 67890],
        term_code="2025-1",
        career="UG",
        timestamp=timestamp,
    )

    assert ts == timestamp
    assert delete_path.name == f"{timestamp}_delete.xlsx"
    assert add_path.name == f"{timestamp}_add.xlsx"
    assert zip_path.name == f"{timestamp}_transfer.zip"
    assert delete_path.exists()
    assert add_path.exists()
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert names == {delete_path.name, add_path.name}


@pytest.mark.asyncio
async def test_transfer_timetable_moves_record(
    test_session_with_schema: Session,
    admin_user,
    sample_sections,
):
    """Transfer reassigns timetable from source to target without changing enrollments."""
    schema = test_session_with_schema.schema_name
    settings = get_settings()
    schema_engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f'"{schema}", public'}},
    )

    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    timetable_id = uuid.uuid4()
    section_id = sample_sections[0]["section_ids"][0]
    class_nbr = sample_sections[0]["class_nbr"]
    initial_enrollment = sample_sections[0]["tot_enrl"]

    async with schema_engine.begin() as conn:
        await conn.execute(
            text(
                f'''
                DO $$ BEGIN
                    CREATE TYPE "{schema}".timetable_source AS ENUM (
                        'portal_generated', 'from_registration'
                    );
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                DO $$ BEGIN
                    CREATE TYPE "{schema}".timetable_status AS ENUM (
                        'draft', 'committed', 'edited'
                    );
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetables (
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
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetable_items (
                    id UUID PRIMARY KEY,
                    timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
                    course_section_id UUID NOT NULL REFERENCES "{schema}".course_sections(id)
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetable_audit_trail (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    details JSONB,
                    changed_at TIMESTAMPTZ NOT NULL,
                    changed_by_id UUID NOT NULL,
                    note TEXT
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                INSERT INTO "{schema}".students
                (id, student_id, campus_id, name)
                VALUES
                (:source_id, 1, '2024A3PS0309P', 'Source Student'),
                (:target_id, 2, '2024A3PS0310P', 'Target Student')
                '''
            ),
            {"source_id": source_id, "target_id": target_id},
        )
        await conn.execute(
            text(
                f'''
                INSERT INTO "{schema}".timetables
                (id, student_id, source, status, created_at, created_by_id, total_units)
                VALUES
                (:id, :student_id, 'portal_generated', 'committed', :created_at, :created_by_id, 3.0)
                '''
            ),
            {
                "id": timetable_id,
                "student_id": source_id,
                "created_at": datetime.now(timezone.utc),
                "created_by_id": admin_user.id,
            },
        )
        await conn.execute(
            text(
                f'''
                INSERT INTO "{schema}".timetable_items
                (id, timetable_id, course_section_id)
                VALUES (:id, :timetable_id, :course_section_id)
                '''
            ),
            {
                "id": uuid.uuid4(),
                "timetable_id": timetable_id,
                "course_section_id": section_id,
            },
        )

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        session_db = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            timetable, source_student, target_student, class_nbrs = await transfer_timetable(
                session_db,
                source_student_id=source_id,
                target_student_id=target_id,
                changed_by_id=admin_user.id,
            )
            await session_db.commit()

            assert timetable.student_id == target_id
            assert source_student.campus_id == "2024A3PS0309P"
            assert target_student.campus_id == "2024A3PS0310P"
            assert class_nbrs == [class_nbr]

            source_tt = await get_timetable_by_student(session_db, source_id)
            target_tt = await get_timetable_by_student(session_db, target_id)
            assert source_tt is None
            assert target_tt is not None
            assert target_tt.id == timetable_id

            enrl_result = await session_db.execute(
                text(
                    f'SELECT tot_enrl FROM "{schema}".course_sections WHERE id = :id'
                ),
                {"id": section_id},
            )
            assert enrl_result.scalar_one() == initial_enrollment
        finally:
            await session_db.close()
            await trans.rollback()

    await schema_engine.dispose()


@pytest.mark.asyncio
async def test_transfer_timetable_rejects_target_with_timetable(
    test_session_with_schema: Session,
    admin_user,
    sample_sections,
):
    """Transfer fails when target already has a committed timetable."""
    schema = test_session_with_schema.schema_name
    settings = get_settings()
    schema_engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f'"{schema}", public'}},
    )

    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    section_id = sample_sections[0]["section_ids"][0]

    async with schema_engine.begin() as conn:
        await conn.execute(
            text(
                f'''
                DO $$ BEGIN
                    CREATE TYPE "{schema}".timetable_source AS ENUM (
                        'portal_generated', 'from_registration'
                    );
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                DO $$ BEGIN
                    CREATE TYPE "{schema}".timetable_status AS ENUM (
                        'draft', 'committed', 'edited'
                    );
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetables (
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
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetable_items (
                    id UUID PRIMARY KEY,
                    timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
                    course_section_id UUID NOT NULL REFERENCES "{schema}".course_sections(id)
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                CREATE TABLE IF NOT EXISTS "{schema}".timetable_audit_trail (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    timetable_id UUID NOT NULL REFERENCES "{schema}".timetables(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    details JSONB,
                    changed_at TIMESTAMPTZ NOT NULL,
                    changed_by_id UUID NOT NULL,
                    note TEXT
                )
                '''
            )
        )
        await conn.execute(
            text(
                f'''
                INSERT INTO "{schema}".students
                (id, student_id, campus_id, name)
                VALUES
                (:source_id, 1, '2024A3PS0309P', 'Source Student'),
                (:target_id, 2, '2024A3PS0310P', 'Target Student')
                '''
            ),
            {"source_id": source_id, "target_id": target_id},
        )
        now = datetime.now(timezone.utc)
        for student_id in (source_id, target_id):
            tt_id = uuid.uuid4()
            await conn.execute(
                text(
                    f'''
                    INSERT INTO "{schema}".timetables
                    (id, student_id, source, status, created_at, created_by_id, total_units)
                    VALUES (:id, :student_id, 'portal_generated', 'committed', :created_at, :created_by_id, 3.0)
                    '''
                ),
                {
                    "id": tt_id,
                    "student_id": student_id,
                    "created_at": now,
                    "created_by_id": admin_user.id,
                },
            )
            await conn.execute(
                text(
                    f'''
                    INSERT INTO "{schema}".timetable_items
                    (id, timetable_id, course_section_id)
                    VALUES (:id, :timetable_id, :course_section_id)
                    '''
                ),
                {
                    "id": uuid.uuid4(),
                    "timetable_id": tt_id,
                    "course_section_id": section_id,
                },
            )

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        session_db = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            with pytest.raises(ValueError, match="Target student already has"):
                await transfer_timetable(
                    session_db,
                    source_student_id=source_id,
                    target_student_id=target_id,
                    changed_by_id=admin_user.id,
                )
        finally:
            await session_db.close()
            await trans.rollback()

    await schema_engine.dispose()
