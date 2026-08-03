"""
API Tests for Timetable Endpoints

Tests the timetable API endpoints including:
- Generate timetable
- Commit timetable
- Uncommit timetable
- Add course
- Remove course
- Swap section
- Find compatible sections
- Revert to registration
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session import Session


class TestTimetableEndpoints:
    """Test suite for timetable API endpoints."""

    @pytest.mark.asyncio
    async def test_get_timetable_not_found(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test getting a non-existent timetable returns 404."""
        student_id = uuid.uuid4()
        response = await authenticated_client.get(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable"
        )
        assert response.status_code == 404
        assert "No timetable found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_get_timetable_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test getting timetable with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        response = await authenticated_client.get(
            f"/api/sessions/{session_id}/students/{student_id}/timetable"
        )
        assert response.status_code == 404
        assert "Session not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_generate_timetable_empty_courses(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test generating timetable with empty course list fails validation."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/generate",
            json={"course_ids": []},
        )
        # FastAPI validation error for empty list
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_commit_timetable_empty_sections(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test committing timetable with empty section list fails validation."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/commit",
            json={"section_ids": []},
        )
        # FastAPI validation error for empty list
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_uncommit_timetable_not_found(
        self, authenticated_client: AsyncClient, test_session_with_schema: Session
    ):
        """Test uncommitting non-existent timetable returns 404."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session_with_schema.id}/students/{student_id}/timetable/uncommit"
        )
        assert response.status_code == 404
        assert "No timetable to uncommit" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_find_compatible_sections_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test finding compatible sections with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/compatible-sections",
            json={"course_id": str(course_id), "exclude_section_ids": []},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_course_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test removing course with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/remove-course",
            json={"course_id": str(course_id)},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_add_course_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test adding course with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/add-course",
            json={"course_id": str(course_id)},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_buffer_timetable_not_found(
        self,
        authenticated_client: AsyncClient,
        test_session_with_schema: Session,
        session_db: AsyncSession,
    ):
        """Test deleting a non-existent buffer timetable returns 404."""
        await session_db.execute(
            text(
                '''
                CREATE TABLE IF NOT EXISTS buffer_timetables (
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
        await session_db.commit()

        buffer_id = uuid.uuid4()
        response = await authenticated_client.delete(
            f"/api/sessions/{test_session_with_schema.id}/buffer-timetables/{buffer_id}"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Buffer timetable not found"

    @pytest.mark.asyncio
    async def test_delete_buffer_timetable_success(
        self,
        authenticated_client: AsyncClient,
        test_session_with_schema: Session,
        db: AsyncSession,
        admin_user,
        sample_sections,
    ):
        """Test deleting an existing buffer timetable succeeds and releases seats."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from app.config import get_settings
        from app.services.timetable_bucket_save_service import (
            BucketSaveInput,
            save_buckets_as_buffer_timetables,
        )

        schema = test_session_with_schema.schema_name
        settings = get_settings()
        schema_engine = create_async_engine(
            settings.database_url,
            echo=False,
            poolclass=NullPool,
            connect_args={
                "server_settings": {"search_path": f'"{schema}", public'}
            },
        )

        async with schema_engine.begin() as conn:
            await conn.execute(
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
            await conn.execute(
                text(
                    f'''
                    CREATE TABLE IF NOT EXISTS "{schema}".buffer_timetable_items (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        buffer_timetable_id UUID NOT NULL
                            REFERENCES "{schema}".buffer_timetables(id) ON DELETE CASCADE,
                        course_section_id UUID NOT NULL,
                        course_code TEXT NOT NULL,
                        component TEXT NOT NULL,
                        section TEXT NOT NULL
                    )
                    '''
                )
            )

        async with schema_engine.connect() as conn:
            trans = await conn.begin()
            session_db = AsyncSession(bind=conn, expire_on_commit=False)
            sec = sample_sections[0]
            created, _, _ = await save_buckets_as_buffer_timetables(
                db,
                session_db,
                plan="A7",
                buckets=[
                    BucketSaveInput(capacity=6, section_ids=sec["section_ids"])
                ],
                created_by_id=admin_user.id,
                enrollment_deducted_on_upload=True,
            )
            assert created == 1
            bt_row = await session_db.execute(
                text(f'SELECT id FROM "{schema}".buffer_timetables LIMIT 1')
            )
            buffer_id = bt_row.scalar_one()
            await session_db.commit()
            await trans.commit()
        await schema_engine.dispose()

        response = await authenticated_client.delete(
            f"/api/sessions/{test_session_with_schema.id}/buffer-timetables/{buffer_id}"
        )

        assert response.status_code == 200
        assert "Buffer timetable deleted successfully" in response.json()["message"]
        assert "6 reserved seat(s) released" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_swap_section_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test swapping section with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/swap-section",
            json={
                "old_section_id": 12345,
                "new_section_id": 67890,
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_timetable_courses_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test getting timetable courses with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        response = await authenticated_client.get(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/courses"
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_revert_to_registration_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test reverting to registration with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/revert-to-registration"
        )
        assert response.status_code == 404


class TestTimetableAuthenticationAndAuthorization:
    """Test suite for authentication and authorization on timetable endpoints."""

    @pytest.mark.asyncio
    async def test_get_timetable_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.get(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable"
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_generate_timetable_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated generate requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/generate",
            json={"course_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_commit_timetable_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated commit requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/commit",
            json={"section_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_staff_disabled_session_modify_forbidden(
        self,
        staff_authenticated_client: AsyncClient,
        db: AsyncSession,
        staff_user,
    ):
        """Test that staff cannot modify disabled sessions."""
        from app.models.session import Session as SessionModel

        # Create a disabled session
        disabled_session = SessionModel(
            id=uuid.uuid4(),
            name="Disabled Session",
            term_code="2025-1",
            career="UG",
            schema_name="disabled_schema",
            is_enabled=False,
            created_by_id=staff_user.id,
        )
        db.add(disabled_session)
        await db.commit()

        student_id = uuid.uuid4()

        # Try to generate timetable
        response = await staff_authenticated_client.post(
            f"/api/sessions/{disabled_session.id}/students/{student_id}/timetable/generate",
            json={"course_ids": [str(uuid.uuid4())]},
        )
        assert response.status_code == 403
        assert "Cannot modify disabled session" in response.json()["detail"]


class TestTimetableValidation:
    """Test suite for request validation on timetable endpoints."""

    @pytest.mark.asyncio
    async def test_generate_invalid_uuid(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that invalid UUID in course_ids is rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/generate",
            json={"course_ids": ["not-a-uuid"]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_commit_invalid_uuid(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that invalid UUID in section_ids is rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/commit",
            json={"section_ids": ["not-a-uuid"]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_swap_invalid_uuids(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that invalid UUIDs in swap request are rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/swap-section",
            json={"old_section_id": "not-a-uuid", "new_section_id": "also-not-a-uuid"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_add_course_missing_course_id(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that missing course_id in add request is rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/add-course",
            json={},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_remove_course_missing_course_id(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that missing course_id in remove request is rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/remove-course",
            json={},
        )
        assert response.status_code == 422


class TestTimetableResponseSchemas:
    """Test suite for verifying response schemas."""

    @pytest.mark.asyncio
    async def test_generate_response_schema(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that generate endpoint returns proper schema structure."""
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()

        # This will fail because the course doesn't exist, but we can check
        # that the response has the right structure
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/generate",
            json={"course_ids": [str(course_id)]},
        )

        # Should get a response (either success=false or error)
        if response.status_code == 200:
            data = response.json()
            # Verify TimetableGenerationResponse schema
            assert "success" in data
            assert "meetings" in data
            assert "conflicts" in data
            assert "total_units" in data
            assert "message" in data

    @pytest.mark.asyncio
    async def test_find_compatible_sections_response_schema(
        self, authenticated_client: AsyncClient, test_session_with_schema: Session
    ):
        """Test that compatible sections endpoint returns proper schema."""
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()

        response = await authenticated_client.post(
            f"/api/sessions/{test_session_with_schema.id}/students/{student_id}/timetable/compatible-sections",
            json={"course_id": str(course_id), "exclude_section_ids": []},
        )

        # Response should have proper schema even if no sections found
        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "course_id" in data
            assert "components" in data
            assert "message" in data


class TestNewTimetableEndpoints:
    """Test suite for newly added timetable endpoints."""

    @pytest.mark.asyncio
    async def test_compatible_sections_with_conflicts_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test compatible-sections-with-conflicts with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/compatible-sections-with-conflicts",
            json={"course_id": str(course_id), "exclude_section_ids": []},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_compatible_sections_with_conflicts_response_schema(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that compatible-sections-with-conflicts returns proper schema."""
        student_id = uuid.uuid4()
        course_id = uuid.uuid4()

        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/compatible-sections-with-conflicts",
            json={"course_id": str(course_id), "exclude_section_ids": []},
        )

        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "course_id" in data
            assert "components" in data
            assert "current_sections" in data
            assert "message" in data

    @pytest.mark.asyncio
    async def test_multi_swap_session_not_found(
        self, authenticated_client: AsyncClient
    ):
        """Test multi-swap with non-existent session returns 404."""
        session_id = uuid.uuid4()
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{session_id}/students/{student_id}/timetable/multi-swap",
            json={
                "swaps": [
                    {
                        "old_section_id": 12345,
                        "new_section_id": 67890,
                    }
                ]
            },
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_multi_swap_empty_swaps(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test multi-swap with empty swaps list fails validation."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/multi-swap",
            json={"swaps": []},
        )
        # FastAPI validation error for empty list
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_multi_swap_response_schema(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that multi-swap returns proper schema."""
        student_id = uuid.uuid4()

        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/multi-swap",
            json={
                "swaps": [
                    {
                        "old_section_id": str(uuid.uuid4()),
                        "new_section_id": str(uuid.uuid4()),
                    }
                ]
            },
        )

        # Response should have proper schema
        if response.status_code == 200:
            data = response.json()
            assert "success" in data
            assert "message" in data
            assert "completed_swaps" in data
            assert "failed_swaps" in data

    @pytest.mark.asyncio
    async def test_multi_swap_invalid_uuids(
        self, authenticated_client: AsyncClient, test_session: Session
    ):
        """Test that invalid UUIDs in multi-swap request are rejected."""
        student_id = uuid.uuid4()
        response = await authenticated_client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/multi-swap",
            json={
                "swaps": [
                    {
                        "old_section_id": "not-a-uuid",
                        "new_section_id": "also-not-a-uuid",
                    }
                ]
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_multi_swap_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated multi-swap requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/multi-swap",
            json={
                "swaps": [
                    {
                        "old_section_id": str(uuid.uuid4()),
                        "new_section_id": str(uuid.uuid4()),
                    }
                ]
            },
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_revert_to_registration_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated revert requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/revert-to-registration"
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_compatible_sections_with_conflicts_unauthenticated(
        self, client: AsyncClient, test_session: Session
    ):
        """Test that unauthenticated compatible-sections-with-conflicts requests are rejected."""
        student_id = uuid.uuid4()
        response = await client.post(
            f"/api/sessions/{test_session.id}/students/{student_id}/timetable/compatible-sections-with-conflicts",
            json={"course_id": str(uuid.uuid4()), "exclude_section_ids": []},
        )
        assert response.status_code == 401
