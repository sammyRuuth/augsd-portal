"""Background task manager for timetable generation"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.algorithms import AlgorithmResult


class TaskStatus(str, Enum):
    """Status of a background task"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GenerationTask:
    """Represents a timetable generation task"""

    id: str
    session_id: uuid.UUID
    student_id: uuid.UUID
    course_ids: list[uuid.UUID]
    algorithm: str
    constraints: dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0  # 0.0 to 1.0
    message: str = ""
    result: AlgorithmResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response"""
        return {
            "id": self.id,
            "session_id": str(self.session_id),
            "student_id": str(self.student_id),
            "algorithm": self.algorithm,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "result": self._serialize_result() if self.result else None,
            "error": self.error,
        }

    def _serialize_result(self) -> dict[str, Any] | None:
        """Serialize AlgorithmResult for API response"""
        if not self.result:
            return None

        return {
            "success": self.result.success,
            "partial": self.result.partial,
            "meetings": [
                {
                    "section_id": str(m.id),
                    "course_id": str(sec.course_id),
                    "class_nbr": sec.class_nbr,
                    "subject": sec.subject,
                    "catalog": sec.catalog,
                    "title": sec.title,
                    "component": sec.component,
                    "section": sec.section,
                    "day": m.day,
                    "mtg_start": m.mtg_start.isoformat() if m.mtg_start else None,
                    "mtg_end": m.mtg_end.isoformat() if m.mtg_end else None,
                    "exam_date": m.exam_date.isoformat() if m.exam_date else None,
                    "exam_start": m.exam_start.isoformat() if m.exam_start else None,
                    "exam_end": m.exam_end.isoformat() if m.exam_end else None,
                    "instructor": m.instructor,
                    "room": m.room,
                    "cap_enrl": m.cap_enrl,
                    "tot_enrl": m.tot_enrl,
                    "max_units": sec.max_units,
                }
                for sec in self.result.selected_sections
                for m in sec.meetings
            ],
            "section_ids": [str(sid) for sid in self.result.section_ids],
            "conflicts": self.result.conflicts,
            "total_units": self.result.total_units,
            "course_count": self.result.course_count,
            "algorithm_name": self.result.algorithm_name,
            "execution_time_ms": self.result.execution_time_ms,
            "iterations": self.result.iterations,
            "message": self.result.message,
        }


class TaskManager:
    """
    In-memory task manager for background generation.

    This is a simple implementation suitable for single-server deployment.
    For multi-server deployment, use Redis or a database-backed queue.
    """

    def __init__(self, max_tasks: int = 1000, task_ttl_seconds: int = 3600):
        self._tasks: dict[str, GenerationTask] = {}
        self._lock = asyncio.Lock()
        self._max_tasks = max_tasks
        self._task_ttl = task_ttl_seconds
        self._running_tasks: set[str] = set()
        self._max_concurrent = 4  # Max concurrent generations

    async def create_task(
        self,
        session_id: uuid.UUID,
        student_id: uuid.UUID,
        course_ids: list[uuid.UUID],
        algorithm: str = "backtrack_optimized",
        constraints: dict[str, Any] | None = None,
    ) -> GenerationTask:
        """Create a new generation task"""
        async with self._lock:
            # Cleanup old tasks if needed
            await self._cleanup_old_tasks()

            task_id = str(uuid.uuid4())
            task = GenerationTask(
                id=task_id,
                session_id=session_id,
                student_id=student_id,
                course_ids=course_ids,
                algorithm=algorithm,
                constraints=constraints or {},
            )
            self._tasks[task_id] = task
            return task

    async def get_task(self, task_id: str) -> GenerationTask | None:
        """Get a task by ID"""
        return self._tasks.get(task_id)

    async def update_task(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: AlgorithmResult | None = None,
        error: str | None = None,
    ):
        """Update task status"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return

            if status is not None:
                task.status = status
                if status == TaskStatus.RUNNING:
                    task.started_at = datetime.now(timezone.utc)
                elif status in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                ):
                    task.completed_at = datetime.now(timezone.utc)
                    self._running_tasks.discard(task_id)

            if progress is not None:
                task.progress = progress
            if message is not None:
                task.message = message
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc)
                self._running_tasks.discard(task_id)
                return True

            return False

    async def list_tasks(
        self,
        session_id: uuid.UUID | None = None,
        student_id: uuid.UUID | None = None,
        status: TaskStatus | None = None,
    ) -> list[GenerationTask]:
        """List tasks with optional filters"""
        tasks = list(self._tasks.values())

        if session_id:
            tasks = [t for t in tasks if t.session_id == session_id]
        if student_id:
            tasks = [t for t in tasks if t.student_id == student_id]
        if status:
            tasks = [t for t in tasks if t.status == status]

        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def can_start_task(self) -> bool:
        """Check if we can start a new task"""
        return len(self._running_tasks) < self._max_concurrent

    async def mark_running(self, task_id: str):
        """Mark task as running"""
        async with self._lock:
            self._running_tasks.add(task_id)

    async def _cleanup_old_tasks(self):
        """Remove old completed tasks"""
        now = datetime.now(timezone.utc)
        to_remove = []

        for task_id, task in self._tasks.items():
            if task.completed_at:
                age = (now - task.completed_at).total_seconds()
                if age > self._task_ttl:
                    to_remove.append(task_id)

        for task_id in to_remove:
            del self._tasks[task_id]

        # Also remove if we have too many tasks
        if len(self._tasks) > self._max_tasks:
            # Remove oldest completed tasks first
            completed = [
                (tid, t)
                for tid, t in self._tasks.items()
                if t.status
                in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            ]
            completed.sort(key=lambda x: x[1].completed_at or x[1].created_at)

            to_remove = [
                tid for tid, _ in completed[: len(self._tasks) - self._max_tasks + 100]
            ]
            for task_id in to_remove:
                del self._tasks[task_id]


# Global task manager instance
task_manager = TaskManager()
