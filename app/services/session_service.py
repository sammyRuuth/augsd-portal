"""Session service for session management operations"""

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import generate_schema_name
from app.database import create_session_schema, drop_session_schema
from app.models.session import Session
from app.schemas.session import SessionCreate, SessionUpdate


async def create_session(
    db: AsyncSession, session_create: SessionCreate, created_by_id: uuid.UUID
) -> Session:
    """
    Create a new session with its own PostgreSQL schema.
    """
    # Generate unique schema name
    schema_name = generate_schema_name()

    # Create session record
    session = Session(
        name=session_create.name,
        term_code=session_create.term_code,
        career=session_create.career,
        schema_name=schema_name,
        created_by_id=created_by_id,
        is_enabled=True,
    )

    db.add(session)
    await db.flush()

    # Create PostgreSQL schema
    await create_session_schema(schema_name)

    # Create tables in session schema
    await create_session_tables(schema_name)

    await db.refresh(session)
    return session


async def ensure_schema_columns(schema_name: str) -> None:
    """
    Auto-migrate: Ensure all expected columns exist in session schema tables.

    This function checks for missing columns and adds them automatically,
    enabling smooth schema updates without Alembic migrations.
    """
    from sqlalchemy import text

    from app.database import engine

    # Define expected columns for each table: (column_name, column_type, default)
    expected_columns = {
        "course_sections": [
            ("exam_start", "TIME", None),
            ("exam_end", "TIME", None),
        ],
        "timetables": [
            ("buffer_timetable_id", "UUID", None),
        ],
    }

    async with engine.begin() as conn:
        await conn.execute(text(f'SET search_path TO "{schema_name}", public'))

        for table_name, columns in expected_columns.items():
            for col_name, col_type, default in columns:
                # Check if column exists
                check_sql = text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = :schema
                    AND table_name = :table
                    AND column_name = :column
                """)
                result = await conn.execute(
                    check_sql,
                    {"schema": schema_name, "table": table_name, "column": col_name},
                )
                exists = result.fetchone()

                if not exists:
                    # Add missing column
                    default_clause = f" DEFAULT {default}" if default else ""
                    alter_sql = text(
                        f'ALTER TABLE "{schema_name}".{table_name} '
                        f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}{default_clause}"
                    )
                    print(f"  Adding column {table_name}.{col_name} to schema {schema_name}")
                    await conn.execute(alter_sql)
                    print(f"  ✓ Added {table_name}.{col_name}")


async def create_session_tables(schema_name: str) -> None:
    """
    Create tables in session-specific schema.

    This creates the session-specific tables (students, course_sections, etc.)
    """
    from sqlalchemy import text

    from app.database import engine

    async with engine.begin() as conn:
        # Set search path
        await conn.execute(text(f'SET search_path TO "{schema_name}", public'))

        # Create tables (using SQL directly for simplicity)
        # In production, you'd use Alembic migrations

        # Students table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".students (
                id UUID PRIMARY KEY,
                student_id BIGINT UNIQUE NOT NULL,
                campus_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                sex TEXT,
                birthdate DATE,
                admission_category TEXT
            )
        """
            )
        )

        # Course sections table
        # Note: class_nbr is NOT unique because a section can have multiple meeting times
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".course_sections (
                id UUID PRIMARY KEY,
                course_id UUID NOT NULL,
                class_nbr INTEGER NOT NULL,
                section TEXT NOT NULL,
                component TEXT NOT NULL,
                class_pattern TEXT,
                day TEXT,
                mtg_start TIME,
                mtg_end TIME,
                exam_date DATE,
                exam_start TIME,
                exam_end TIME,
                instructor TEXT,
                room TEXT,
                cap_enrl INTEGER,
                tot_enrl INTEGER DEFAULT 0 NOT NULL
            )
        """
            )
        )

        # Create index on class_nbr for efficient lookups
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_course_sections_class_nbr ON "{schema_name}".course_sections(class_nbr)'
            )
        )

        # Create unique index for upsert support (class_nbr, day, mtg_start)
        # Use NULLS NOT DISTINCT to treat NULL values as equal for uniqueness
        await conn.execute(
            text(
                f'CREATE UNIQUE INDEX IF NOT EXISTS ix_course_sections_class_nbr_day_mtg_start ON "{schema_name}".course_sections(class_nbr, day, mtg_start) NULLS NOT DISTINCT'
            )
        )

        # Create ENUM types for timetables (if they don't exist)
        await conn.execute(
            text(
                f"""
            DO $$ BEGIN
                CREATE TYPE "{schema_name}".timetable_source AS ENUM ('portal_generated', 'from_registration');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """
            )
        )

        await conn.execute(
            text(
                f"""
            DO $$ BEGIN
                CREATE TYPE "{schema_name}".timetable_status AS ENUM ('draft', 'committed', 'edited');
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """
            )
        )

        # Timetables table (unified - contains all timetables)
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".timetables (
                id UUID PRIMARY KEY,
                student_id UUID NOT NULL REFERENCES "{schema_name}".students(id),
                source "{schema_name}".timetable_source NOT NULL DEFAULT 'portal_generated',
                status "{schema_name}".timetable_status NOT NULL DEFAULT 'draft',
                created_at TIMESTAMPTZ NOT NULL,
                created_by_id UUID NOT NULL,
                total_units NUMERIC(5, 2),
                updated_at TIMESTAMPTZ,
                updated_by_id UUID,
                buffer_timetable_id UUID
            )
        """
            )
        )

        # Create indexes for timetables
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_timetables_student ON "{schema_name}".timetables(student_id)'
            )
        )
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_timetables_source ON "{schema_name}".timetables(source)'
            )
        )
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_timetables_status ON "{schema_name}".timetables(status)'
            )
        )

        # Add buffer_timetable_id column for existing schemas (migration)
        await conn.execute(
            text(
                f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = '{schema_name}'
                    AND table_name = 'timetables'
                    AND column_name = 'buffer_timetable_id'
                ) THEN
                    ALTER TABLE "{schema_name}".timetables
                    ADD COLUMN buffer_timetable_id UUID;
                END IF;
            END $$;
            """
            )
        )

        # Timetable items table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".timetable_items (
                id UUID PRIMARY KEY,
                timetable_id UUID NOT NULL REFERENCES "{schema_name}".timetables(id) ON DELETE CASCADE,
                course_section_id UUID NOT NULL REFERENCES "{schema_name}".course_sections(id)
            )
        """
            )
        )

        # Create index for timetable items
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_timetable_items_timetable ON "{schema_name}".timetable_items(timetable_id)'
            )
        )

        # Timetable audit trail table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".timetable_audit_trail (
                id UUID PRIMARY KEY,
                timetable_id UUID NOT NULL REFERENCES "{schema_name}".timetables(id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                details JSONB,
                changed_at TIMESTAMPTZ NOT NULL,
                changed_by_id UUID NOT NULL,
                note TEXT
            )
        """
            )
        )

        # Create indexes for audit trail
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_audit_trail_timetable ON "{schema_name}".timetable_audit_trail(timetable_id)'
            )
        )
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_audit_trail_action ON "{schema_name}".timetable_audit_trail(action)'
            )
        )
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_audit_trail_changed_at ON "{schema_name}".timetable_audit_trail(changed_at)'
            )
        )

        # Registration timetables table (imported from registration data)
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".registration_timetables (
                id UUID PRIMARY KEY,
                student_id UUID NOT NULL UNIQUE REFERENCES "{schema_name}".students(id),
                created_at TIMESTAMPTZ NOT NULL,
                created_by_id UUID NOT NULL,
                total_units NUMERIC(5, 2),
                updated_at TIMESTAMPTZ,
                updated_by_id UUID
            )
        """
            )
        )

        # Registration timetable items table (immutable baseline)
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".registration_timetable_items (
                id UUID PRIMARY KEY,
                timetable_id UUID NOT NULL REFERENCES "{schema_name}".registration_timetables(id) ON DELETE CASCADE,
                course_section_id UUID NOT NULL REFERENCES "{schema_name}".course_sections(id)
            )
        """
            )
        )

        # Create index for registration timetable items
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_reg_tt_items_timetable ON "{schema_name}".registration_timetable_items(timetable_id)'
            )
        )

        # Registration data table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".registration_data (
                id UUID PRIMARY KEY,
                campus_id TEXT NOT NULL,
                course_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                catalog TEXT NOT NULL,
                section TEXT NOT NULL,
                component TEXT NOT NULL,
                class_nbr INTEGER NOT NULL,
                add_dt DATE,
                drop_dt DATE,
                unit_taken NUMERIC(5, 2),
                grade_in TEXT,
                instructor_name TEXT,
                admit_sem TEXT,
                last_reg_sem TEXT,
                degree1 TEXT,
                degree2 TEXT
            )
        """
            )
        )

        # Uploaded files table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".uploaded_files (
                id UUID PRIMARY KEY,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                uploaded_at TIMESTAMPTZ NOT NULL,
                uploaded_by_id UUID NOT NULL,
                status TEXT NOT NULL DEFAULT 'processing',
                error_message TEXT
            )
        """
            )
        )

        # Saved timetable drafts table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".saved_timetable_drafts (
                id UUID PRIMARY KEY,
                student_id UUID NOT NULL REFERENCES "{schema_name}".students(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                notes TEXT,
                selected_course_ids JSONB NOT NULL DEFAULT '[]',
                pinned_sections JSONB NOT NULL DEFAULT '{{}}',
                seat_preferences JSONB NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ,
                created_by_id UUID NOT NULL
            )
        """
            )
        )

        # Create index for saved drafts
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_saved_drafts_student ON "{schema_name}".saved_timetable_drafts(student_id)'
            )
        )

        # Buffer timetables table (pre-generated timetable templates)
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".buffer_timetables (
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
        """
            )
        )

        # Create indexes for buffer timetables
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_buffer_timetables_plan ON "{schema_name}".buffer_timetables(plan)'
            )
        )

        # Buffer timetable items table
        await conn.execute(
            text(
                f"""
            CREATE TABLE IF NOT EXISTS "{schema_name}".buffer_timetable_items (
                id UUID PRIMARY KEY,
                buffer_timetable_id UUID NOT NULL REFERENCES "{schema_name}".buffer_timetables(id) ON DELETE CASCADE,
                course_section_id UUID NOT NULL REFERENCES "{schema_name}".course_sections(id),
                course_code TEXT NOT NULL,
                component TEXT NOT NULL,
                section TEXT NOT NULL
            )
        """
            )
        )

        # Create index for buffer timetable items
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_buffer_tt_items_timetable ON "{schema_name}".buffer_timetable_items(buffer_timetable_id)'
            )
        )

    # Run auto-migration to add any missing columns
    await ensure_schema_columns(schema_name)


async def get_session_by_id(db: AsyncSession, session_id: uuid.UUID) -> Session | None:
    """Get session by ID"""
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession, is_enabled: bool | None = None
) -> Sequence[Session]:
    """List sessions, optionally filtered by enabled status"""
    query = select(Session).order_by(Session.created_at.desc())

    if is_enabled is not None:
        query = query.where(Session.is_enabled == is_enabled)

    result = await db.execute(query)
    return result.scalars().all()


async def update_session(
    db: AsyncSession, session_id: uuid.UUID, session_update: SessionUpdate
) -> Session | None:
    """Update session (enable/disable)"""
    session = await get_session_by_id(db, session_id)
    if session:
        session.is_enabled = session_update.is_enabled
        await db.flush()
        await db.refresh(session)
    return session


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Delete session and its schema"""
    session = await get_session_by_id(db, session_id)
    if not session:
        return False

    # Drop schema
    await drop_session_schema(session.schema_name)

    # Delete session record
    await db.delete(session)
    await db.flush()

    return True
