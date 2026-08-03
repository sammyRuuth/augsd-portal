"""
Tools for timetable analysis and debugging.

Provides standalone scripts for:
- Finding available time slots for rescheduling
- Analyzing conflicts
- Debugging generation issues
"""

from .find_available_slots import find_available_slots

__all__ = ["find_available_slots"]
