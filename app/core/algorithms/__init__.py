"""Timetable generation algorithms package"""

from app.core.algorithms.base import (
    AlgorithmResult,
    GenerationConstraints,
    SectionData,
    TimetableAlgorithm,
)
from app.core.algorithms.registry import AlgorithmRegistry, get_algorithm

__all__ = [
    "TimetableAlgorithm",
    "AlgorithmResult",
    "GenerationConstraints",
    "SectionData",
    "AlgorithmRegistry",
    "get_algorithm",
]
