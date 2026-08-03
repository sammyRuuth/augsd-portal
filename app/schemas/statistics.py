"""Statistics Pydantic schemas"""

from pydantic import BaseModel


class CourseStatistics(BaseModel):
    """Schema for course-wise statistics"""

    course_id: str
    subject: str
    catalog: str
    title: str
    total_sections: int
    total_capacity: int
    total_enrolled: int
    available_seats: int
    enrollment_percentage: float
    is_overfilled: bool


class EnrollmentStatistics(BaseModel):
    """Schema for overall enrollment statistics"""

    total_courses: int
    total_sections: int
    total_capacity: int
    total_enrolled: int
    average_enrollment_percentage: float
    overfilled_sections: int


class SectionStatistics(BaseModel):
    """Schema for section-level statistics"""

    class_nbr: int
    section: str
    component: str
    instructor: str | None
    capacity: int
    enrolled: int
    available: int
    enrollment_percentage: float
    is_overfilled: bool
