"""Common Pydantic schemas used across the application"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel


class Message(BaseModel):
    """Generic message response"""

    message: str
    detail: str | None = None


class UploadResult(BaseModel):
    """Result from file upload with statistics"""

    message: str
    total_rows: int = 0
    records_created: int = 0
    duplicates_removed: int = 0
    duplicate_details: list[dict[str, Any]] | None = None
    warnings: list[str] | None = None
    errors: list[str] | None = None


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response"""

    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int
