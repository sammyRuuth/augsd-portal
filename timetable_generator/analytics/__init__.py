"""
Analytics and diagnostics for timetable generation.

Provides:
- Section utilization analysis
- Bottleneck identification
- Capacity deficit reports
- Suggestions for new sections
- Time slot conflict analysis
"""

from .diagnostics import TimetableAnalyzer
from .report import AnalyticsReport

__all__ = [
    "TimetableAnalyzer",
    "AnalyticsReport",
]
