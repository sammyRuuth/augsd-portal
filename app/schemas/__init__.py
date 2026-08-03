"""Pydantic schemas for request/response validation"""

from app.schemas.common import Message, PaginatedResponse
from app.schemas.course import (
    CourseBase,
    CourseCreate,
    CourseResponse,
    CourseWithPrerequisites,
)
from app.schemas.course_section import (
    CourseSectionCreate,
    CourseSectionResponse,
    CourseSectionUpdate,
    CourseSectionWithDetails,
)
from app.schemas.default_package import (
    DefaultPackageCreate,
    DefaultPackageResponse,
    DefaultPackageUpdate,
)
from app.schemas.prerequisite import (
    PrerequisiteCreate,
    PrerequisiteResponse,
    PrerequisiteWithCourse,
)
from app.schemas.registration import RegistrationDataCreate, RegistrationDataResponse
from app.schemas.session import SessionCreate, SessionResponse, SessionUpdate
from app.schemas.statistics import CourseStatistics, EnrollmentStatistics
from app.schemas.student import StudentCreate, StudentResponse, StudentSearch
from app.schemas.timetable import (
    TimetableCreate,
    TimetableItemResponse,
    TimetableResponse,
)
from app.schemas.user import Token, UserCreate, UserLogin, UserResponse

# Rebuild models to resolve forward references
CourseWithPrerequisites.model_rebuild()
CourseSectionWithDetails.model_rebuild()
PrerequisiteWithCourse.model_rebuild()
TimetableItemResponse.model_rebuild()
TimetableResponse.model_rebuild()

__all__ = [
    "Message",
    "PaginatedResponse",
    "UserCreate",
    "UserResponse",
    "UserLogin",
    "Token",
    "SessionCreate",
    "SessionResponse",
    "SessionUpdate",
    "CourseBase",
    "CourseCreate",
    "CourseResponse",
    "CourseSectionCreate",
    "CourseSectionResponse",
    "CourseSectionUpdate",
    "StudentCreate",
    "StudentResponse",
    "StudentSearch",
    "TimetableCreate",
    "TimetableResponse",
    "TimetableItemResponse",
    "PrerequisiteCreate",
    "PrerequisiteResponse",
    "DefaultPackageCreate",
    "DefaultPackageResponse",
    "DefaultPackageUpdate",
    "RegistrationDataCreate",
    "RegistrationDataResponse",
    "CourseStatistics",
    "EnrollmentStatistics",
]
