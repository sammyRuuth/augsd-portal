"""
Parsers for input data files.

This module provides parsers for:
- packages.json: Course packages per academic plan
- count.csv: Student counts per plan
- Excel timetable: Section details with schedules and capacities
"""

from .counts import parse_counts
from .excel import parse_excel_timetable
from .packages import group_plans_by_packages, parse_packages

__all__ = [
    "parse_packages",
    "group_plans_by_packages",
    "parse_counts",
    "parse_excel_timetable",
]
