"""
Pytest configuration and fixtures for AUGSD Portal tests.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, time, timedelta, timezone

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.core.security import hash_password
from app.database import get_db
from app.main import app
from app.models.course import Course
from app.models.session import Session
from app.models.user import User

settings = get_settings()


@pytest_asyncio.fixture(scope="function")
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for each test with transaction rollback."""
    # Create engine per test to avoid event loop issues
    # Use NullPool to avoid connection pool holding onto old event loops
    test_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    async with test_engine.connect() as conn:
        # Start a transaction
        trans = await conn.begin()
        # Create a session bound to this transaction
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            # Rollback the transaction to undo all changes
            await trans.rollback()

    # Dispose of the engine after the test
    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def admin_user(db: AsyncSession) -> User:
    """Create an admin user for testing."""
    user = User(
        id=uuid.uuid4(),
        email=f"admin-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass123"),
        role="admin",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    return user


@pytest_asyncio.fixture(scope="function")
async def staff_user(db: AsyncSession) -> User:
    """Create a staff user for testing."""
    user = User(
        id=uuid.uuid4(),
        email=f"staff-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass123"),
        role="staff",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    await db.flush()
    return user


@pytest_asyncio.fixture(scope="function")
async def test_session(db: AsyncSession, admin_user: User) -> Session:
    """Create a test session (metadata only, no schema)."""
    session = Session(
        id=uuid.uuid4(),
        name=f"Test Session {uuid.uuid4().hex[:8]}",
        term_code="2025-1",
        career="UG",
        schema_name=f"test_schema_{uuid.uuid4().hex[:8]}",
        is_enabled=True,
        created_at=datetime.now(timezone.utc),
        created_by_id=admin_user.id,
    )
    db.add(session)
    await db.flush()
    return session


@pytest_asyncio.fixture(scope="function")
async def test_session_with_schema(
    db: AsyncSession, admin_user: User
) -> AsyncGenerator[Session, None]:
    """
    Create a test session with actual PostgreSQL schema and tables.
    This fixture creates a real schema that can be used for integration tests.
    """
    schema_name = f"test_{uuid.uuid4().hex[:12]}"

    session = Session(
        id=uuid.uuid4(),
        name=f"Test Session {schema_name}",
        term_code="2025-1",
        career="UG",
        schema_name=schema_name,
        is_enabled=True,
        created_at=datetime.now(timezone.utc),
        created_by_id=admin_user.id,
    )
    db.add(session)
    await db.flush()

    # Create the schema using a separate connection (outside transaction)
    schema_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    async with schema_engine.begin() as conn:
        # Create schema
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

        # Create course_sections table
        await conn.execute(
            text(f'''
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
        ''')
        )

        # Create index on class_nbr
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS idx_cs_class_nbr ON "{schema_name}".course_sections(class_nbr)'
            )
        )

        # Create students table
        await conn.execute(
            text(f'''
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
        ''')
        )

    try:
        yield session
    finally:
        # Cleanup: drop the schema
        async with schema_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await schema_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def sample_courses(db: AsyncSession) -> list[Course]:
    """Create sample courses in the global database."""
    courses = [
        Course(
            id=uuid.uuid4(),
            course_id="CS101",
            subject="CS",
            catalog="101",
            title="Introduction to Computer Science",
            max_units=3.0,
        ),
        Course(
            id=uuid.uuid4(),
            course_id="CS201",
            subject="CS",
            catalog="201",
            title="Data Structures",
            max_units=4.0,
        ),
        Course(
            id=uuid.uuid4(),
            course_id="MATH101",
            subject="MATH",
            catalog="101",
            title="Calculus I",
            max_units=4.0,
        ),
        Course(
            id=uuid.uuid4(),
            course_id="PHY101",
            subject="PHY",
            catalog="101",
            title="Physics I",
            max_units=4.0,
        ),
    ]

    for course in courses:
        db.add(course)
    await db.flush()

    return courses


@pytest_asyncio.fixture(scope="function")
async def sample_sections(
    test_session_with_schema: Session, sample_courses: list[Course]
) -> AsyncGenerator[list[dict], None]:
    """
    Create sample course sections in the session schema.
    Returns a list of section data dictionaries.
    """
    schema_name = test_session_with_schema.schema_name

    # Create engine for schema operations
    schema_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    sections_data = []
    section_id_counter = 0

    async with schema_engine.begin() as conn:
        for course in sample_courses:
            # Create 2 sections per course, each with 2 meeting times
            for sec_num in range(1, 3):
                class_nbr = 10000 + section_id_counter
                section_id_counter += 1

                # Meeting time 1 (e.g., Monday)
                section_id_1 = uuid.uuid4()
                await conn.execute(
                    text(f'''
                    INSERT INTO "{schema_name}".course_sections
                    (id, course_id, class_nbr, section, component, day, mtg_start, mtg_end,
                     instructor, room, cap_enrl, tot_enrl)
                    VALUES (:id, :course_id, :class_nbr, :section, :component, :day,
                            :mtg_start, :mtg_end, :instructor, :room, :cap_enrl, :tot_enrl)
                '''),
                    {
                        "id": section_id_1,
                        "course_id": course.id,
                        "class_nbr": class_nbr,
                        "section": f"L{sec_num}",
                        "component": "LEC",
                        "day": "M",
                        "mtg_start": time(9, 0),
                        "mtg_end": time(9, 50),
                        "instructor": f"Dr. Smith {sec_num}",
                        "room": f"Room {100 + sec_num}",
                        "cap_enrl": 50,
                        "tot_enrl": 10,
                    },
                )

                # Meeting time 2 (e.g., Wednesday)
                section_id_2 = uuid.uuid4()
                await conn.execute(
                    text(f'''
                    INSERT INTO "{schema_name}".course_sections
                    (id, course_id, class_nbr, section, component, day, mtg_start, mtg_end,
                     instructor, room, cap_enrl, tot_enrl)
                    VALUES (:id, :course_id, :class_nbr, :section, :component, :day,
                            :mtg_start, :mtg_end, :instructor, :room, :cap_enrl, :tot_enrl)
                '''),
                    {
                        "id": section_id_2,
                        "course_id": course.id,
                        "class_nbr": class_nbr,
                        "section": f"L{sec_num}",
                        "component": "LEC",
                        "day": "W",
                        "mtg_start": time(9, 0),
                        "mtg_end": time(9, 50),
                        "instructor": f"Dr. Smith {sec_num}",
                        "room": f"Room {100 + sec_num}",
                        "cap_enrl": 50,
                        "tot_enrl": 10,
                    },
                )

                sections_data.append(
                    {
                        "class_nbr": class_nbr,
                        "course_id": course.id,
                        "section": f"L{sec_num}",
                        "component": "LEC",
                        "cap_enrl": 50,
                        "tot_enrl": 10,
                        "section_ids": [section_id_1, section_id_2],
                    }
                )

    await schema_engine.dispose()

    yield sections_data


@pytest_asyncio.fixture(scope="function")
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def authenticated_client(client: AsyncClient, admin_user: User) -> AsyncClient:
    """Create an authenticated client with admin token."""
    from app.core.security import create_access_token

    token = create_access_token(
        data={"sub": str(admin_user.id)},
        expires_delta=timedelta(hours=1),
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def staff_authenticated_client(
    client: AsyncClient, staff_user: User
) -> AsyncClient:
    """Create an authenticated client with staff token."""
    from app.core.security import create_access_token

    token = create_access_token(
        data={"sub": str(staff_user.id)},
        expires_delta=timedelta(hours=1),
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest_asyncio.fixture(scope="function")
async def session_db(
    test_session_with_schema: Session,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Create a database session connected to the test session's schema.
    This is useful for directly testing service functions.
    """
    schema_name = test_session_with_schema.schema_name

    # Create engine with search_path set to session schema
    schema_engine = create_async_engine(
        settings.database_url,
        echo=False,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f'"{schema_name}", public'}},
    )

    async with schema_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()

    await schema_engine.dispose()
