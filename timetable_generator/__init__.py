"""
Timetable Generator - A modular system for generating student timetables.

This package provides tools for:
- Parsing course packages, student counts, and section data
- Generating conflict-free timetables with capacity constraints
- Multi-strategy optimization for optimal allocation
- Comprehensive analytics and diagnostics
- Clean CSV and PDF exports
"""

__version__ = "2.0.0"
__author__ = "AUGSD Portal Team"

from .config import Config, load_config
from .models import Meeting, Section, Timetable, TimetableAssignment

__all__ = [
    "Section",
    "Meeting",
    "Timetable",
    "TimetableAssignment",
    "Config",
    "load_config",
]
