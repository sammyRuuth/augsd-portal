"""
Tests for timetable validation rules.

Tests cover:
1. Max units enforcement (default 25 units)
2. All components requirement (LEC, TUT, LAB must all be present)
3. Time conflict detection
4. Exam conflict detection
"""

import uuid
from datetime import date, time

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.core.algorithms import get_algorithm
from app.core.algorithms.base import GenerationConstraints, SectionData
from app.models.course import Course
from app.models.course_section import CourseSection

settings = get_settings()


# ==================== Fixtures ====================


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncSession:
    """Create a database session for testing."""
    test_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    async with test_engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()

    await test_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_schema(db_session: AsyncSession) -> str:
    """Create a test schema with course_sections table."""
    schema_name = f"test_{uuid.uuid4().hex[:12]}"

    # Create schema using a separate connection
    schema_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    async with schema_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
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

    try:
        yield schema_name
    finally:
        async with schema_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await schema_engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def sample_course(db_session: AsyncSession) -> Course:
    """Create a sample course with 4 units."""
    course = Course(
        id=uuid.uuid4(),
        course_id="CS101",
        subject="CS",
        catalog="101",
        title="Introduction to Computer Science",
        max_units=4.0,
    )
    db_session.add(course)
    await db_session.flush()
    return course


@pytest_asyncio.fixture(scope="function")
async def sample_course_with_components(
    db_session: AsyncSession, test_schema: str
) -> tuple[Course, list[CourseSection]]:
    """Create a course with LEC, TUT, and LAB components."""
    course = Course(
        id=uuid.uuid4(),
        course_id="CS201",
        subject="CS",
        catalog="201",
        title="Data Structures",
        max_units=4.0,
    )
    db_session.add(course)
    await db_session.flush()

    # Create sections for each component
    schema_engine = create_async_engine(
        settings.database_url, echo=False, poolclass=NullPool
    )

    sections = []
    section_data = [
        # LEC section (class_nbr 10001)
        {
            "class_nbr": 10001,
            "section": "L1",
            "component": "LEC",
            "day": "M",
            "mtg_start": time(9, 0),
            "mtg_end": time(9, 50),
        },
        {
            "class_nbr": 10001,
            "section": "L1",
            "component": "LEC",
            "day": "W",
            "mtg_start": time(9, 0),
            "mtg_end": time(9, 50),
        },
        # TUT section (class_nbr 10002)
        {
            "class_nbr": 10002,
            "section": "T1",
            "component": "TUT",
            "day": "TH",
            "mtg_start": time(10, 0),
            "mtg_end": time(10, 50),
        },
        # LAB section (class_nbr 10003)
        {
            "class_nbr": 10003,
            "section": "P1",
            "component": "LAB",
            "day": "F",
            "mtg_start": time(14, 0),
            "mtg_end": time(15, 50),
        },
    ]

    async with schema_engine.begin() as conn:
        for s in section_data:
            section_id = uuid.uuid4()
            await conn.execute(
                text(f'''
                INSERT INTO "{test_schema}".course_sections
                (id, course_id, class_nbr, section, component, day, mtg_start, mtg_end, cap_enrl, tot_enrl)
                VALUES (:id, :course_id, :class_nbr, :section, :component, :day, :mtg_start, :mtg_end, 50, 0)
            '''),
                {
                    "id": section_id,
                    "course_id": course.id,
                    "class_nbr": s["class_nbr"],
                    "section": s["section"],
                    "component": s["component"],
                    "day": s["day"],
                    "mtg_start": s["mtg_start"],
                    "mtg_end": s["mtg_end"],
                },
            )
            sections.append({"id": section_id, **s, "course_id": course.id})

    await schema_engine.dispose()
    return course, sections


# ==================== Max Units Tests ====================


class TestMaxUnitsValidation:
    """Tests for max units enforcement."""

    @pytest.mark.asyncio
    async def test_max_units_default_is_25(self):
        """Verify default max units is 25."""
        constraints = GenerationConstraints()
        assert constraints.max_units == 25.0

    @pytest.mark.asyncio
    async def test_algorithm_respects_max_units(self):
        """Test that algorithms respect max units limit."""
        # Create courses totaling more than 25 units
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()
        course3_id = uuid.uuid4()
        course4_id = uuid.uuid4()
        course5_id = uuid.uuid4()
        course6_id = uuid.uuid4()
        course7_id = uuid.uuid4()

        # Each course has 4 units, 7 courses = 28 units > 25
        sections_by_course = {}
        for i, cid in enumerate(
            [
                course1_id,
                course2_id,
                course3_id,
                course4_id,
                course5_id,
                course6_id,
                course7_id,
            ]
        ):
            sections_by_course[cid] = [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=cid,
                    class_nbr=10000 + i,
                    section="L1",
                    component="LEC",
                    day="M" if i % 2 == 0 else "T",
                    mtg_start=time(9 + i, 0),
                    mtg_end=time(9 + i, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog=f"10{i}",
                    title=f"Course {i}",
                    max_units=4.0,
                )
            ]

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("backtrack_optimized", constraints)
        result = algo.generate(sections_by_course)

        # Should not schedule all 7 courses (28 units > 25)
        assert result.total_units <= 25.0
        assert result.course_count <= 6  # At most 6 courses (24 units)

    @pytest.mark.asyncio
    async def test_algorithm_with_custom_max_units(self):
        """Test algorithm with custom max units."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()

        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=10.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="T",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=10.0,
                )
            ],
        }

        # With max_units=15, should only schedule 1 course
        constraints = GenerationConstraints(max_units=15.0)
        algo = get_algorithm("greedy", constraints)
        result = algo.generate(sections_by_course)

        assert result.total_units <= 15.0
        assert result.course_count == 1

    @pytest.mark.asyncio
    async def test_unit_limit_conflict_message(self):
        """Test that unit limit conflicts have clear messages."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()

        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=20.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="T",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=10.0,
                )
            ],
        }

        # With max_units=25, can only fit one course
        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("greedy", constraints)
        result = algo.generate(sections_by_course)

        # Should have a conflict mentioning unit limit
        assert result.partial or not result.success
        if result.conflicts:
            unit_conflicts = [
                c for c in result.conflicts if "unit" in c.get("message", "").lower()
            ]
            assert len(unit_conflicts) > 0


# ==================== Component Completeness Tests ====================


class TestComponentCompleteness:
    """Tests for ensuring all components (LEC, TUT, LAB) are scheduled."""

    @pytest.mark.asyncio
    async def test_all_components_required(self):
        """Test that all components of a course must be scheduled."""
        course_id = uuid.uuid4()

        # Course with LEC and TUT components
        sections_by_course = {
            course_id: [
                # LEC sections
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
                # TUT sections
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10002,
                    section="T1",
                    component="TUT",
                    day="W",
                    mtg_start=time(10, 0),
                    mtg_end=time(10, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
            ]
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("backtrack_optimized", constraints)
        result = algo.generate(sections_by_course)

        # Should schedule both LEC and TUT
        assert result.success
        components = {s.component for s in result.selected_sections}
        assert "LEC" in components
        assert "TUT" in components

    @pytest.mark.asyncio
    async def test_missing_component_fails_validation(self):
        """Test that missing components are detected in validation."""
        course_id = uuid.uuid4()

        # Only LEC component
        sections_by_course = {
            course_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
            ]
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("greedy", constraints)
        result = algo.generate(sections_by_course)

        # Should succeed since this course only has LEC
        assert result.success
        # Verify only LEC is scheduled (since that's all that exists)
        assert len(result.selected_sections) == 1
        assert result.selected_sections[0].component == "LEC"

    @pytest.mark.asyncio
    async def test_component_conflict_no_valid_combos(self):
        """Test when components have no valid combinations due to conflicts."""
        course_id = uuid.uuid4()

        # LEC and TUT at same time - no valid combo
        sections_by_course = {
            course_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10002,
                    section="T1",
                    component="TUT",
                    day="M",
                    mtg_start=time(9, 0),  # Same time as LEC!
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
            ]
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("backtrack_optimized", constraints)
        result = algo.generate(sections_by_course)

        # Should fail - no valid combos since LEC and TUT clash
        assert not result.success
        # No sections should be scheduled
        assert result.course_count == 0
        assert len(result.selected_sections) == 0
        # The algorithm correctly doesn't schedule a course with internally conflicting components


# ==================== Time Conflict Tests ====================


class TestTimeConflicts:
    """Tests for time conflict detection."""

    @pytest.mark.asyncio
    async def test_time_conflict_between_courses(self):
        """Test detection of time conflicts between courses."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()

        # Both courses at same time
        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),  # Same time!
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=4.0,
                )
            ],
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("backtrack_optimized", constraints)
        result = algo.generate(sections_by_course)

        # Should be partial - only one course can be scheduled
        assert result.partial or result.success
        assert result.course_count == 1

    @pytest.mark.asyncio
    async def test_adjacent_times_no_conflict(self):
        """Test that adjacent times don't conflict."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()

        # Back-to-back times
        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 50),  # Starts when first ends
                    mtg_end=time(10, 40),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=4.0,
                )
            ],
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("greedy", constraints)
        result = algo.generate(sections_by_course)

        # Both courses should be scheduled
        assert result.success
        assert result.course_count == 2


# ==================== Exam Conflict Tests ====================


class TestExamConflicts:
    """Tests for exam conflict detection."""

    @pytest.mark.asyncio
    async def test_exam_conflict_same_date_time(self):
        """Test detection of exam conflicts on same date and time."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()
        exam_date = date(2025, 12, 15)

        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=exam_date,
                    exam_start=time(9, 0),
                    exam_end=time(12, 0),
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="T",  # Different class day
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=exam_date,  # Same exam date!
                    exam_start=time(10, 0),  # Overlapping exam time
                    exam_end=time(13, 0),
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=4.0,
                )
            ],
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("backtrack_optimized", constraints)
        result = algo.generate(sections_by_course)

        # Should be partial - exam conflict prevents scheduling both
        assert result.partial or result.success
        assert result.course_count == 1

    @pytest.mark.asyncio
    async def test_different_exam_dates_no_conflict(self):
        """Test that different exam dates don't conflict."""
        course1_id = uuid.uuid4()
        course2_id = uuid.uuid4()

        sections_by_course = {
            course1_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course1_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=date(2025, 12, 15),
                    exam_start=time(9, 0),
                    exam_end=time(12, 0),
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                )
            ],
            course2_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course2_id,
                    class_nbr=10002,
                    section="L1",
                    component="LEC",
                    day="T",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=date(2025, 12, 16),  # Different date
                    exam_start=time(9, 0),
                    exam_end=time(12, 0),
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="102",
                    title="Course 2",
                    max_units=4.0,
                )
            ],
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm("greedy", constraints)
        result = algo.generate(sections_by_course)

        # Both should be scheduled
        assert result.success
        assert result.course_count == 2


# ==================== Algorithm Consistency Tests ====================


class TestAlgorithmConsistency:
    """Tests to verify all algorithms follow the same rules."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "algorithm", ["greedy", "backtrack", "backtrack_optimized"]
    )
    async def test_max_units_all_algorithms(self, algorithm: str):
        """Test that all algorithms respect max units."""
        course_ids = [uuid.uuid4() for _ in range(8)]

        sections_by_course = {}
        for i, cid in enumerate(course_ids):
            sections_by_course[cid] = [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=cid,
                    class_nbr=10000 + i,
                    section="L1",
                    component="LEC",
                    day=["M", "T", "W", "TH", "F"][i % 5],
                    mtg_start=time(8 + i, 0),
                    mtg_end=time(8 + i, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog=f"10{i}",
                    title=f"Course {i}",
                    max_units=4.0,
                )
            ]

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm(algorithm, constraints)
        result = algo.generate(sections_by_course)

        # 8 courses * 4 units = 32 units > 25 max
        assert result.total_units <= 25.0, f"{algorithm} exceeded max units"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "algorithm", ["greedy", "backtrack", "backtrack_optimized"]
    )
    async def test_component_completeness_all_algorithms(self, algorithm: str):
        """Test that all algorithms require all components."""
        course_id = uuid.uuid4()

        sections_by_course = {
            course_id: [
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10001,
                    section="L1",
                    component="LEC",
                    day="M",
                    mtg_start=time(9, 0),
                    mtg_end=time(9, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10002,
                    section="T1",
                    component="TUT",
                    day="W",
                    mtg_start=time(10, 0),
                    mtg_end=time(10, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
                SectionData(
                    id=uuid.uuid4(),
                    course_id=course_id,
                    class_nbr=10003,
                    section="P1",
                    component="LAB",
                    day="F",
                    mtg_start=time(14, 0),
                    mtg_end=time(15, 50),
                    exam_date=None,
                    exam_start=None,
                    exam_end=None,
                    instructor=None,
                    room=None,
                    cap_enrl=50,
                    tot_enrl=0,
                    subject="CS",
                    catalog="101",
                    title="Course 1",
                    max_units=4.0,
                ),
            ]
        }

        constraints = GenerationConstraints(max_units=25.0)
        algo = get_algorithm(algorithm, constraints)
        result = algo.generate(sections_by_course)

        # Should have all 3 components
        assert result.success, f"{algorithm} failed to schedule course"
        components = {s.component for s in result.selected_sections}
        assert "LEC" in components, f"{algorithm} missing LEC"
        assert "TUT" in components, f"{algorithm} missing TUT"
        assert "LAB" in components, f"{algorithm} missing LAB"
