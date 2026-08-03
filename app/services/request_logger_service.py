"""Request logger service for timetable operations.

Logs all timetable-related requests to enable:
1. Flow reconstruction per student
2. Debugging and auditing
3. Request replay for testing
"""

import json
import threading
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import get_settings


class OperationType(str, Enum):
    """Types of timetable operations to log."""

    # Generation
    GENERATE_TIMETABLE = "generate_timetable"
    ADVANCED_GENERATE = "advanced_generate"
    COMPARE_ALGORITHMS = "compare_algorithms"

    # Commit/Uncommit
    COMMIT_TIMETABLE = "commit_timetable"
    UNCOMMIT_TIMETABLE = "uncommit_timetable"
    TRANSFER_TIMETABLE = "transfer_timetable"
    ASSIGN_BUFFER_TIMETABLE = "assign_buffer_timetable"

    # Editing
    SWAP_SECTION = "swap_section"
    MULTI_SWAP = "multi_swap"
    ADD_COURSE = "add_course"
    REMOVE_COURSE = "remove_course"
    REVERT_TO_REGISTRATION = "revert_to_registration"

    # Compatibility checks
    FIND_COMPATIBLE_SECTIONS = "find_compatible_sections"
    FIND_COMPATIBLE_WITH_CONFLICTS = "find_compatible_with_conflicts"

    # Registration timetable
    UPDATE_REGISTRATION_TIMETABLE = "update_registration_timetable"


class RequestLogEntry(BaseModel):
    """A single request log entry."""

    # Identifiers
    log_id: str
    timestamp: str
    sequence: int  # For ordering within session

    # Context
    session_id: str
    student_id: str
    user_id: str | None
    user_email: str | None

    # Operation
    operation: str
    endpoint: str

    # Request data (serializable)
    request_data: dict[str, Any]

    # Response summary
    success: bool | None = None
    response_summary: dict[str, Any] | None = None
    error: str | None = None

    # Timing
    duration_ms: float | None = None


class RequestLoggerService:
    """
    Service to log timetable requests for reconstruction and auditing.

    Logs are stored in JSONL format (one JSON object per line) for:
    - Easy appending without loading entire file
    - Stream processing of large log files
    - Simple parsing with standard tools

    Directory structure:
    logs/
    ├── {session_id}/
    │   ├── all_requests.jsonl      # All requests for this session
    │   └── students/
    │       └── {student_id}.jsonl  # Per-student request log
    └── global_requests.jsonl       # All requests across sessions
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern for thread safety."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._sequence_counters: dict[str, int] = {}
        self._sequence_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._base_path = Path(get_settings().log_dir)
        self._ensure_directories()

    def _ensure_directories(self):
        """Create base log directory if it doesn't exist."""
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _get_sequence(self, session_id: str) -> int:
        """Get next sequence number for a session."""
        with self._sequence_lock:
            key = str(session_id)
            if key not in self._sequence_counters:
                self._sequence_counters[key] = 0
            self._sequence_counters[key] += 1
            return self._sequence_counters[key]

    def _serialize_value(self, value: Any) -> Any:
        """Recursively serialize values for JSON."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._serialize_value(v) for k, v in value.items()}
        if hasattr(value, "model_dump"):
            # Pydantic model
            return self._serialize_value(value.model_dump())
        if hasattr(value, "__dict__"):
            return self._serialize_value(value.__dict__)
        # Fallback to string
        return str(value)

    def _write_log(self, path: Path, entry: RequestLogEntry):
        """Write log entry to file (thread-safe)."""
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")

    def log_request(
        self,
        session_id: uuid.UUID,
        student_id: uuid.UUID,
        operation: OperationType,
        endpoint: str,
        request_data: dict[str, Any],
        user_id: uuid.UUID | None = None,
        user_email: str | None = None,
        success: bool | None = None,
        response_summary: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> str:
        """
        Log a timetable request.

        Args:
            session_id: The session UUID
            student_id: The student UUID
            operation: Type of operation
            endpoint: API endpoint path
            request_data: The request body/parameters
            user_id: User who made the request
            user_email: User's email
            success: Whether the operation succeeded
            response_summary: Summary of the response
            error: Error message if failed
            duration_ms: Request duration in milliseconds

        Returns:
            The log entry ID
        """
        log_id = str(uuid.uuid4())
        session_str = str(session_id)
        student_str = str(student_id)

        entry = RequestLogEntry(
            log_id=log_id,
            timestamp=datetime.utcnow().isoformat(),
            sequence=self._get_sequence(session_str),
            session_id=session_str,
            student_id=student_str,
            user_id=str(user_id) if user_id else None,
            user_email=user_email,
            operation=operation.value,
            endpoint=endpoint,
            request_data=self._serialize_value(request_data),
            success=success,
            response_summary=self._serialize_value(response_summary)
            if response_summary
            else None,
            error=error,
            duration_ms=duration_ms,
        )

        # Write to multiple log files
        # 1. Global log
        self._write_log(self._base_path / "global_requests.jsonl", entry)

        # 2. Session-specific log
        session_path = self._base_path / session_str / "all_requests.jsonl"
        self._write_log(session_path, entry)

        # 3. Student-specific log (within session)
        student_path = (
            self._base_path / session_str / "students" / f"{student_str}.jsonl"
        )
        self._write_log(student_path, entry)

        return log_id

    def get_student_requests(
        self,
        session_id: uuid.UUID,
        student_id: uuid.UUID,
        operation_filter: list[OperationType] | None = None,
        limit: int | None = None,
    ) -> list[RequestLogEntry]:
        """
        Get all requests for a specific student in a session.

        Args:
            session_id: The session UUID
            student_id: The student UUID
            operation_filter: Only include these operations
            limit: Max number of entries to return (most recent)

        Returns:
            List of log entries sorted by sequence
        """
        student_path = (
            self._base_path / str(session_id) / "students" / f"{student_id}.jsonl"
        )

        if not student_path.exists():
            return []

        entries = []
        with open(student_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = RequestLogEntry(**data)

                    if operation_filter and entry.operation not in [
                        op.value for op in operation_filter
                    ]:
                        continue

                    entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue

        # Sort by sequence
        entries.sort(key=lambda e: e.sequence)

        if limit:
            entries = entries[-limit:]

        return entries

    def get_session_requests(
        self,
        session_id: uuid.UUID,
        operation_filter: list[OperationType] | None = None,
        student_id_filter: uuid.UUID | None = None,
        limit: int | None = None,
    ) -> list[RequestLogEntry]:
        """
        Get all requests for a session.

        Args:
            session_id: The session UUID
            operation_filter: Only include these operations
            student_id_filter: Only include this student
            limit: Max number of entries

        Returns:
            List of log entries sorted by sequence
        """
        session_path = self._base_path / str(session_id) / "all_requests.jsonl"

        if not session_path.exists():
            return []

        entries = []
        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entry = RequestLogEntry(**data)

                    if operation_filter and entry.operation not in [
                        op.value for op in operation_filter
                    ]:
                        continue

                    if student_id_filter and entry.student_id != str(student_id_filter):
                        continue

                    entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue

        entries.sort(key=lambda e: e.sequence)

        if limit:
            entries = entries[-limit:]

        return entries

    def export_student_flow(
        self,
        session_id: uuid.UUID,
        student_id: uuid.UUID,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Export a student's complete request flow for reconstruction.

        Args:
            session_id: The session UUID
            student_id: The student UUID
            output_path: Optional path to write JSON file

        Returns:
            Dictionary with student flow data
        """
        entries = self.get_student_requests(session_id, student_id)

        flow = {
            "session_id": str(session_id),
            "student_id": str(student_id),
            "exported_at": datetime.utcnow().isoformat(),
            "total_requests": len(entries),
            "requests": [entry.model_dump() for entry in entries],
        }

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(flow, f, indent=2)

        return flow

    def list_logged_students(self, session_id: uuid.UUID) -> list[str]:
        """List all student IDs that have logged requests in a session."""
        students_dir = self._base_path / str(session_id) / "students"
        if not students_dir.exists():
            return []

        return [
            f.stem  # filename without .jsonl extension
            for f in students_dir.glob("*.jsonl")
        ]

    def get_request_stats(
        self,
        session_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Get statistics about logged requests.

        Args:
            session_id: Optional session to filter by

        Returns:
            Statistics dictionary
        """
        if session_id:
            entries = self.get_session_requests(session_id)
        else:
            entries = []
            global_path = self._base_path / "global_requests.jsonl"
            if global_path.exists():
                with open(global_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(RequestLogEntry(**json.loads(line)))
                        except (json.JSONDecodeError, ValueError):
                            continue

        # Compute stats
        ops_count: dict[str, int] = {}
        students_set: set[str] = set()
        success_count = 0
        failure_count = 0

        for entry in entries:
            ops_count[entry.operation] = ops_count.get(entry.operation, 0) + 1
            students_set.add(entry.student_id)
            if entry.success is True:
                success_count += 1
            elif entry.success is False:
                failure_count += 1

        return {
            "total_requests": len(entries),
            "unique_students": len(students_set),
            "operations_breakdown": ops_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_count / len(entries) if entries else 0,
        }


# Singleton instance
request_logger = RequestLoggerService()


def log_timetable_request(
    session_id: uuid.UUID,
    student_id: uuid.UUID,
    operation: OperationType,
    endpoint: str,
    request_data: dict[str, Any],
    user_id: uuid.UUID | None = None,
    user_email: str | None = None,
    success: bool | None = None,
    response_summary: dict[str, Any] | None = None,
    error: str | None = None,
    duration_ms: float | None = None,
) -> str:
    """Convenience function to log a request."""
    return request_logger.log_request(
        session_id=session_id,
        student_id=student_id,
        operation=operation,
        endpoint=endpoint,
        request_data=request_data,
        user_id=user_id,
        user_email=user_email,
        success=success,
        response_summary=response_summary,
        error=error,
        duration_ms=duration_ms,
    )
