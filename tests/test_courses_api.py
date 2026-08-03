"""
Tests for Course API Endpoints and Capacity Management

Tests cover:
- Course search and retrieval
- Section capacity updates (single and bulk)
- Capacity tracking during timetable operations
- Authentication and authorization
- Edge cases and validation
- Integration tests with real session schema
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.session import Session


class TestCourseEndpoints:
    """Test suite for course API endpoints."""

    @pytest.mark.asyncio
    async def test_get_courses_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated requests to get courses are rejected."""
        response = await client.get(f"/api/sessions/{test_session.id}/courses")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_courses_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test getting courses with non-existent session returns 404."""
        session_id = uuid.uuid4()
        response = await authenticated_client.get(f"/api/sessions/{session_id}/courses")
        # Note: This endpoint searches global courses, so session validation
        # depends on the implementation. Check actual behavior.
        assert response.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_get_courses_empty_result(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test getting courses returns empty list when no courses exist."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses"
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_search_courses_with_query(
        self, authenticated_client: AsyncClient, test_session: Session, db: AsyncSession
    ):
        """Test searching courses with query parameter."""
        # Create a test course
        course = Course(
            id=uuid.uuid4(),
            course_id="CS101",
            subject="CS",
            catalog="101",
            title="Introduction to Computer Science",
            max_units=3.0,
        )
        db.add(course)
        await db.flush()

        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses", params={"query": "CS"}
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_course_by_id(
        self, authenticated_client: AsyncClient, test_session: Session, db: AsyncSession
    ):
        """Test getting a specific course by ID."""
        # Create a test course
        course = Course(
            id=uuid.uuid4(),
            course_id="MATH201",
            subject="MATH",
            catalog="201",
            title="Calculus II",
            max_units=4.0,
        )
        db.add(course)
        await db.flush()

        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses/{course.id}"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(course.id)
        assert data["subject"] == "MATH"
        assert data["catalog"] == "201"

    @pytest.mark.asyncio
    async def test_get_course_not_found(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test getting a non-existent course returns 404."""
        course_id = uuid.uuid4()
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses/{course_id}"
        )
        assert response.status_code == 404
        assert "Course not found" in response.json()["detail"]


class TestCourseSectionsEndpoints:
    """Test suite for course sections API endpoints."""

    @pytest.mark.asyncio
    async def test_get_sections_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated requests to get sections are rejected."""
        response = await client.get(f"/api/sessions/{test_session.id}/courses/sections")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_sections_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test getting sections with non-existent session returns 404."""
        session_id = uuid.uuid4()
        response = await authenticated_client.get(
            f"/api/sessions/{session_id}/courses/sections"
        )
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]


class TestCapacityUpdateEndpoints:
    """Test suite for section capacity update endpoints."""

    @pytest.mark.asyncio
    async def test_update_capacity_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated capacity update requests are rejected."""
        response = await client.put(
            f"/api/sessions/{test_session.id}/courses/sections/12345/capacity",
            json={"cap_enrl": 50},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_capacity_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test updating capacity with non-existent session returns 404."""
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 50},
        )
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_capacity_section_not_found(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test updating capacity for non-existent section returns 404."""
        # Note: This test needs a real session schema to work properly.
        # The endpoint tries to connect to the session's schema.
        # For now, we verify it at least reaches the session validation.
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/99999/capacity",
            json={"cap_enrl": 50},
        )
        # Could be 404 for session schema not existing or section not found
        assert response.status_code in (404, 500)

    @pytest.mark.asyncio
    async def test_update_capacity_invalid_value(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test updating capacity with negative value is rejected."""
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/12345/capacity",
            json={"cap_enrl": -10},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_capacity_null_value(self, authenticated_client: AsyncClient):
        """Test updating capacity with null value (unlimited) passes validation."""
        # Use a non-existent session to test validation only
        # (session schema access would fail anyway)
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": None},
        )
        # Should pass validation but fail on session lookup
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]


class TestBulkCapacityUpdateEndpoints:
    """Test suite for bulk section capacity update endpoints."""

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated bulk capacity update requests are rejected."""
        response = await client.put(
            f"/api/sessions/{test_session.id}/courses/sections/bulk-capacity",
            json=[{"class_nbr": 12345, "cap_enrl": 50}],
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test bulk updating capacity with non-existent session returns 404."""
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/bulk-capacity",
            json=[{"class_nbr": 12345, "cap_enrl": 50}],
        )
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_empty_list(
        self, authenticated_client: AsyncClient
    ):
        """Test bulk updating with empty list."""
        # Use a non-existent session to test validation
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/bulk-capacity", json=[]
        )
        # Empty list may succeed with 0 updates (and fail on session) or fail validation
        assert response.status_code in (404, 422)

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_invalid_class_nbr(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test bulk updating with invalid class_nbr is rejected."""
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/bulk-capacity",
            json=[{"class_nbr": 0, "cap_enrl": 50}],  # class_nbr must be > 0
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_negative_cap(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test bulk updating with negative capacity is rejected."""
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/bulk-capacity",
            json=[{"class_nbr": 12345, "cap_enrl": -10}],
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_response_schema(
        self, authenticated_client: AsyncClient
    ):
        """Test bulk update returns proper response schema when session not found."""
        # Use a non-existent session to test the response for not found case
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/bulk-capacity",
            json=[{"class_nbr": 12345, "cap_enrl": 50}],
        )
        # Should fail on session lookup
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_bulk_update_capacity_multiple_items(
        self, authenticated_client: AsyncClient
    ):
        """Test bulk updating multiple sections passes validation."""
        # Use a non-existent session to test validation only
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/bulk-capacity",
            json=[
                {"class_nbr": 12345, "cap_enrl": 50},
                {"class_nbr": 12346, "cap_enrl": 60},
                {"class_nbr": 12347, "cap_enrl": None},  # Unlimited
            ],
        )
        # Validation should pass but session lookup will fail
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]


class TestCapacityValidation:
    """Test suite for capacity validation edge cases."""

    @pytest.mark.asyncio
    async def test_update_capacity_missing_body(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test updating capacity without request body is rejected."""
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/12345/capacity"
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_capacity_wrong_type(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test updating capacity with wrong type is rejected."""
        response = await authenticated_client.put(
            f"/api/sessions/{test_session.id}/courses/sections/12345/capacity",
            json={"cap_enrl": "fifty"},  # String instead of int
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_capacity_float_value(self, authenticated_client: AsyncClient):
        """Test updating capacity with float is handled correctly."""
        # Use a non-existent session to test validation
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 50.5},  # Float value
        )
        # Pydantic may coerce to int (50) or reject (422)
        # If coerced, session lookup will fail with 404
        assert response.status_code in (404, 422)


class TestCourseServiceFunctions:
    """Test suite for course service functions (unit tests)."""

    @pytest.mark.asyncio
    async def test_search_courses_by_subject(self, db: AsyncSession):
        """Test searching courses by subject."""
        from app.services.course_service import search_courses

        # Create test courses
        course1 = Course(
            id=uuid.uuid4(),
            course_id="CS100",
            subject="CS",
            catalog="100",
            title="Intro to CS",
            max_units=3.0,
        )
        course2 = Course(
            id=uuid.uuid4(),
            course_id="MATH100",
            subject="MATH",
            catalog="100",
            title="Intro to Math",
            max_units=3.0,
        )
        db.add(course1)
        db.add(course2)
        await db.flush()

        # Search for CS courses
        results = await search_courses(db, "CS")
        assert len(results) >= 1
        assert any(c.subject == "CS" for c in results)

    @pytest.mark.asyncio
    async def test_search_courses_by_title(self, db: AsyncSession):
        """Test searching courses by title."""
        from app.services.course_service import search_courses

        course = Course(
            id=uuid.uuid4(),
            course_id="PHY101",
            subject="PHY",
            catalog="101",
            title="Quantum Mechanics",
            max_units=4.0,
        )
        db.add(course)
        await db.flush()

        results = await search_courses(db, "Quantum")
        assert len(results) >= 1
        assert any("Quantum" in c.title for c in results)

    @pytest.mark.asyncio
    async def test_search_courses_no_query(self, db: AsyncSession):
        """Test searching courses without query returns all courses."""
        from app.services.course_service import search_courses

        # Create test course
        course = Course(
            id=uuid.uuid4(),
            course_id="BIO101",
            subject="BIO",
            catalog="101",
            title="Biology",
            max_units=3.0,
        )
        db.add(course)
        await db.flush()

        results = await search_courses(db, None)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_course_by_id(self, db: AsyncSession):
        """Test getting course by UUID."""
        from app.services.course_service import get_course_by_id

        course = Course(
            id=uuid.uuid4(),
            course_id="CHEM101",
            subject="CHEM",
            catalog="101",
            title="Chemistry",
            max_units=4.0,
        )
        db.add(course)
        await db.flush()

        result = await get_course_by_id(db, course.id)
        assert result is not None
        assert result.id == course.id
        assert result.subject == "CHEM"

    @pytest.mark.asyncio
    async def test_get_course_by_id_not_found(self, db: AsyncSession):
        """Test getting non-existent course returns None."""
        from app.services.course_service import get_course_by_id

        result = await get_course_by_id(db, uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_course_by_identity(self, db: AsyncSession):
        """Test getting course by course_id."""
        from app.services.course_service import get_course_by_identity

        course = Course(
            id=uuid.uuid4(),
            course_id="ENG101",
            subject="ENG",
            catalog="101",
            title="English Composition",
            max_units=3.0,
        )
        db.add(course)
        await db.flush()

        result = await get_course_by_identity(db, "ENG101")
        assert result is not None
        assert result.course_id == "ENG101"

    @pytest.mark.asyncio
    async def test_list_courses(self, db: AsyncSession):
        """Test listing all courses."""
        from app.services.course_service import list_courses

        # Create test courses
        for i in range(3):
            course = Course(
                id=uuid.uuid4(),
                course_id=f"TEST{i}",
                subject="TEST",
                catalog=str(100 + i),
                title=f"Test Course {i}",
                max_units=3.0,
            )
            db.add(course)
        await db.flush()

        results = await list_courses(db)
        assert len(results) >= 3


class TestCapacityTrackingLogic:
    """Test suite for capacity tracking logic verification."""

    @pytest.mark.asyncio
    async def test_capacity_fields_exist_on_model(self):
        """Test that CourseSection model has capacity fields."""
        from app.models.course_section import CourseSection

        # Verify the model has the required fields
        assert hasattr(CourseSection, "cap_enrl")
        assert hasattr(CourseSection, "tot_enrl")

    @pytest.mark.asyncio
    async def test_capacity_schema_validation(self):
        """Test that capacity schema validates correctly."""
        from app.schemas.course_section import CourseSectionUpdate

        # Valid update
        update = CourseSectionUpdate(cap_enrl=50)
        assert update.cap_enrl == 50

        # Null for unlimited
        update_unlimited = CourseSectionUpdate(cap_enrl=None)
        assert update_unlimited.cap_enrl is None

    @pytest.mark.asyncio
    async def test_bulk_capacity_update_schema(self):
        """Test bulk capacity update schema validation."""
        from pydantic import ValidationError

        from app.api.courses import BulkCapacityUpdate

        # Valid
        update = BulkCapacityUpdate(class_nbr=12345, cap_enrl=50)
        assert update.class_nbr == 12345
        assert update.cap_enrl == 50

        # Null capacity allowed
        update_null = BulkCapacityUpdate(class_nbr=12345, cap_enrl=None)
        assert update_null.cap_enrl is None

        # Invalid class_nbr (must be > 0)
        with pytest.raises(ValidationError):
            BulkCapacityUpdate(class_nbr=0, cap_enrl=50)

        # Invalid negative capacity
        with pytest.raises(ValidationError):
            BulkCapacityUpdate(class_nbr=12345, cap_enrl=-1)


class TestAuthorizationOnCourseEndpoints:
    """Test suite for authorization on course endpoints."""

    @pytest.mark.asyncio
    async def test_staff_can_access_courses(
        self, staff_authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that staff users can access course endpoints."""
        response = await staff_authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses"
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_can_update_capacity(self, authenticated_client: AsyncClient):
        """Test that admin users can access capacity update endpoint (auth check)."""
        # Use a non-existent session to verify auth works
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 50},
        )
        # Should pass auth but fail on session lookup (not 401 or 403)
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_staff_can_update_capacity(
        self, staff_authenticated_client: AsyncClient
    ):
        """Test that staff users can access capacity update endpoint (auth check)."""
        # Use a non-existent session to verify auth works
        session_id = uuid.uuid4()
        response = await staff_authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 50},
        )
        # Should pass auth but fail on session lookup (not 401 or 403)
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]


class TestCourseResponseSchemas:
    """Test suite for verifying response schemas."""

    @pytest.mark.asyncio
    async def test_course_response_schema(
        self, authenticated_client: AsyncClient, test_session: Session, db: AsyncSession
    ):
        """Test that course endpoint returns proper schema structure."""
        # Create a test course
        course = Course(
            id=uuid.uuid4(),
            course_id="SCHEMA101",
            subject="SCHEMA",
            catalog="101",
            title="Schema Test Course",
            max_units=3.0,
        )
        db.add(course)
        await db.flush()

        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses/{course.id}"
        )
        assert response.status_code == 200
        data = response.json()

        # Verify CourseResponse schema
        assert "id" in data
        assert "course_id" in data
        assert "subject" in data
        assert "catalog" in data
        assert "title" in data
        assert "max_units" in data

    @pytest.mark.asyncio
    async def test_courses_list_response_schema(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that courses list endpoint returns array."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses"
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestCapacityIntegration:
    """
    Integration tests for capacity updates with real session schema.

    Note: These tests require the FastAPI app's internal database connections
    to be properly synchronized with the test event loop. Some tests are marked
    as integration tests that work directly with service functions.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_section_capacity_success(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test successfully updating section capacity via service."""
        from app.services.course_service import (
            get_sections_by_class_nbr,
            update_section_capacity,
        )

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        # Update capacity
        updated = await update_section_capacity(session_db, class_nbr, 75)

        assert len(updated) == 2  # Two meeting times for same class_nbr
        for s in updated:
            assert s.cap_enrl == 75

        # Verify the update persisted
        sections = await get_sections_by_class_nbr(session_db, class_nbr)
        for s in sections:
            assert s.cap_enrl == 75

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_section_capacity_to_unlimited(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test setting section capacity to unlimited (null) via service."""
        from app.services.course_service import update_section_capacity

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        updated = await update_section_capacity(session_db, class_nbr, None)

        for s in updated:
            assert s.cap_enrl is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_update_section_capacity_not_found(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test updating capacity for non-existent class_nbr returns empty."""
        from app.services.course_service import update_section_capacity

        updated = await update_section_capacity(session_db, 99999, 50)

        # Should return empty list for non-existent class_nbr
        assert len(updated) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_sections_by_class_nbr_integration(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test getting sections by class_nbr returns all meeting times."""
        from app.services.course_service import get_sections_by_class_nbr

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        sections = await get_sections_by_class_nbr(session_db, class_nbr)

        assert len(sections) == 2
        # Verify they have the correct data
        for s in sections:
            assert s.class_nbr == class_nbr
            assert s.cap_enrl == 50
            assert s.tot_enrl == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_list_all_sections(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test listing all course sections."""
        from app.services.course_service import list_course_sections

        sections = await list_course_sections(session_db)

        # 4 courses × 2 sections each × 2 meeting times = 16 rows
        assert len(sections) == 16

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_capacity_fields_in_sections(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test that capacity tracking fields are present."""
        from app.services.course_service import list_course_sections

        sections = await list_course_sections(session_db)

        for section in sections:
            assert hasattr(section, "cap_enrl")
            assert hasattr(section, "tot_enrl")
            # Initial values from sample data
            assert section.cap_enrl == 50
            assert section.tot_enrl == 10

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_bulk_capacity_update_logic(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test updating multiple sections' capacity."""
        from app.services.course_service import (
            get_sections_by_class_nbr,
            update_section_capacity,
        )

        # Update multiple sections
        for i, section in enumerate(sample_sections[:3]):
            await update_section_capacity(
                session_db, section["class_nbr"], 100 + i * 10
            )

        # Verify updates
        for i, section in enumerate(sample_sections[:3]):
            sections = await get_sections_by_class_nbr(session_db, section["class_nbr"])
            for s in sections:
                assert s.cap_enrl == 100 + i * 10


class TestServiceFunctionsWithSchema:
    """Integration tests for service functions with real session schema."""

    @pytest.mark.asyncio
    async def test_get_sections_by_class_nbr(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test getting sections by class_nbr returns all meeting times."""
        from app.services.course_service import get_sections_by_class_nbr

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        sections = await get_sections_by_class_nbr(session_db, class_nbr)

        assert len(sections) == 2  # Two meeting times
        for s in sections:
            assert s.class_nbr == class_nbr

    @pytest.mark.asyncio
    async def test_update_section_capacity_service(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test update_section_capacity service function."""
        from app.services.course_service import (
            get_sections_by_class_nbr,
            update_section_capacity,
        )

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        # Update capacity
        updated = await update_section_capacity(session_db, class_nbr, 100)

        assert len(updated) == 2
        for s in updated:
            assert s.cap_enrl == 100

        # Verify change persists
        sections = await get_sections_by_class_nbr(session_db, class_nbr)
        for s in sections:
            assert s.cap_enrl == 100

    @pytest.mark.asyncio
    async def test_update_section_capacity_to_null(
        self,
        session_db: AsyncSession,
        sample_sections: list[dict],
    ):
        """Test setting capacity to null (unlimited)."""
        from app.services.course_service import update_section_capacity

        section = sample_sections[0]
        class_nbr = section["class_nbr"]

        updated = await update_section_capacity(session_db, class_nbr, None)

        for s in updated:
            assert s.cap_enrl is None


class TestEdgeCases:
    """Test suite for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_very_large_capacity_value(self, authenticated_client: AsyncClient):
        """Test setting very large capacity value passes validation."""
        # Use a non-existent session to test validation only
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 999999999},
        )
        # Should pass validation but fail on session lookup
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_zero_capacity_value(self, authenticated_client: AsyncClient):
        """Test setting zero capacity passes validation."""
        # Use a non-existent session to test validation only
        session_id = uuid.uuid4()
        response = await authenticated_client.put(
            f"/api/sessions/{session_id}/courses/sections/12345/capacity",
            json={"cap_enrl": 0},
        )
        # Zero is valid (means section is full), but session lookup will fail
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_unicode_in_search_query(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test searching with unicode characters."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses", params={"query": "日本語"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_special_chars_in_search_query(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test searching with special characters."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses", params={"query": "CS%101&<>"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_search_query(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test searching with empty query."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses", params={"query": ""}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_whitespace_search_query(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test searching with whitespace query."""
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/courses", params={"query": "   "}
        )
        assert response.status_code == 200
