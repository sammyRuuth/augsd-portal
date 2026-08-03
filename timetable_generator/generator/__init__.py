"""
Timetable generation engine.

This module provides:
- Constraint checking (time clashes, capacity limits)
- Course combo generation
- Multi-strategy optimization
- Batch allocation
"""

from .allocator import TimetableAllocator
from .combos import generate_course_combos
from .constraints import (
    CapacityTracker,
    combo_clashes_with_current,
    create_time_mask,
    has_internal_clash,
    sections_clash,
)
from .optimizer import TimetableOptimizer

__all__ = [
    "create_time_mask",
    "sections_clash",
    "combo_clashes_with_current",
    "has_internal_clash",
    "CapacityTracker",
    "generate_course_combos",
    "TimetableOptimizer",
    "TimetableAllocator",
]
