"""
Configuration management for the timetable generator.

Supports YAML configuration files with sensible defaults.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class CapacityConfig:
    """Configuration for capacity limits and overfill rules."""

    # Default section capacity when not specified
    default_capacity: int = 40

    # Extra seats added to TUT sections automatically
    tutorial_bonus_seats: int = 8

    # Maximum overfill limits per component type
    max_overfill: dict[str, int] = field(
        default_factory=lambda: {
            "LAB": 0,
            "PRO": 3,
            "PRA": 3,
            "TUT": 0,
        }
    )

    # Course-specific overfill overrides (stricter limits)
    course_overfill_overrides: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "CHEM F101": {"LAB": 0},
            "PHY F101": {"LAB": 0},
        }
    )

    # Section capacity overrides (replaces Excel values)
    section_capacity_overrides: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "BIO F101": {"TUT": 58, "LAB": 50},
            "PHY F101": {"TUT": 58, "LAB": 50},
            "CHEM F101": {"TUT": 58, "LAB": 60},
            "MATH F102": {"TUT": 58},
            "MATH F113": {"TUT": 58},
        }
    )

    # Courses with unlimited capacity (no restrictions)
    unlimited_capacity_courses: set[str] = field(
        default_factory=lambda: {"BITS F101-1", "BITS K101-1"}
    )

    # Allow negative capacity mode (for analysis)
    allow_negative_capacity: bool = True


@dataclass
class GeneratorConfig:
    """Configuration for the generation algorithm."""

    # Number of different strategies to try
    num_strategies: int = 25

    # Minimum timetables per plan (for mixing options)
    min_timetables_per_plan: int = 1

    # Attempts to diversify section combos
    variant_retry_attempts: int = 3

    # Maximum greedy attempts per timetable
    max_greedy_attempts: int = 100

    # Enable batch size randomization
    batch_randomness: bool = True

    # Minimum batch size (None = auto)
    min_batch_size: Optional[int] = None

    # Compactness weight (0 = disabled)
    compactness_weight: float = 0.0


@dataclass
class OutputConfig:
    """Configuration for output generation."""

    # Output directory
    output_dir: str = "exports/timetables"

    # Generate individual plan CSV files
    generate_plan_csvs: bool = True

    # Generate PDF timetables
    generate_pdf: bool = True

    # PDF settings
    pdf_page_size: str = "A4"
    pdf_orientation: str = "landscape"

    # Time slot settings for PDF grid
    time_slot_start: str = "08:00"
    time_slot_end: str = "18:00"

    # Days to include in timetable
    days: list[str] = field(
        default_factory=lambda: [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]
    )


@dataclass
class Config:
    """Main configuration container."""

    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    generator: GeneratorConfig = field(default_factory=GeneratorConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    # Day pattern mappings for parsing
    day_patterns: dict[str, str] = field(
        default_factory=lambda: {
            "M": "Monday",
            "T": "Tuesday",
            "W": "Wednesday",
            "TH": "Thursday",
            "F": "Friday",
            "S": "Saturday",
            "SU": "Sunday",
        }
    )

    def get_max_overfill(self, course_code: str, component: str) -> int:
        """Get the max overfill for a course/component."""
        # Check course-specific overrides first
        for course_prefix, overrides in self.capacity.course_overfill_overrides.items():
            if course_prefix in course_code:
                if component in overrides:
                    return overrides[component]
        # Fall back to global limit
        return self.capacity.max_overfill.get(component, 999999)

    def get_section_capacity_override(
        self, course_code: str, component: str
    ) -> Optional[int]:
        """Get capacity override for a section (replaces Excel value)."""
        for (
            course_prefix,
            overrides,
        ) in self.capacity.section_capacity_overrides.items():
            if course_prefix in course_code:
                if component in overrides:
                    return overrides[component]
        return None

    def is_unlimited_capacity(self, course_code: str) -> bool:
        """Check if a course has unlimited capacity."""
        return course_code in self.capacity.unlimited_capacity_courses


def load_config(path: Optional[Path] = None) -> Config:
    """
    Load configuration from a YAML file.

    Args:
        path: Path to YAML config file. If None, uses defaults.

    Returns:
        Config object with loaded or default settings.
    """
    config = Config()

    if path is None:
        return config

    if not path.exists():
        return config

    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}

    # Load capacity config
    if "capacity" in data:
        cap_data = data["capacity"]
        config.capacity = CapacityConfig(
            default_capacity=cap_data.get("default_capacity", 40),
            tutorial_bonus_seats=cap_data.get("tutorial_bonus_seats", 8),
            max_overfill=cap_data.get("max_overfill", config.capacity.max_overfill),
            course_overfill_overrides=cap_data.get(
                "course_overfill_overrides", config.capacity.course_overfill_overrides
            ),
            section_capacity_overrides=cap_data.get(
                "section_capacity_overrides", config.capacity.section_capacity_overrides
            ),
            unlimited_capacity_courses=set(
                cap_data.get(
                    "unlimited_capacity_courses",
                    list(config.capacity.unlimited_capacity_courses),
                )
            ),
            allow_negative_capacity=cap_data.get("allow_negative_capacity", True),
        )

    # Load generator config
    if "generator" in data:
        gen_data = data["generator"]
        config.generator = GeneratorConfig(
            num_strategies=gen_data.get("num_strategies", 25),
            min_timetables_per_plan=gen_data.get("min_timetables_per_plan", 10),
            variant_retry_attempts=gen_data.get("variant_retry_attempts", 3),
            max_greedy_attempts=gen_data.get("max_greedy_attempts", 100),
            batch_randomness=gen_data.get("batch_randomness", True),
            min_batch_size=gen_data.get("min_batch_size"),
            compactness_weight=gen_data.get("compactness_weight", 0.0),
        )

    # Load output config
    if "output" in data:
        out_data = data["output"]
        config.output = OutputConfig(
            output_dir=out_data.get("output_dir", "exports/timetables"),
            generate_plan_csvs=out_data.get("generate_plan_csvs", True),
            generate_pdf=out_data.get("generate_pdf", True),
            pdf_page_size=out_data.get("pdf_page_size", "A4"),
            pdf_orientation=out_data.get("pdf_orientation", "landscape"),
            time_slot_start=out_data.get("time_slot_start", "08:00"),
            time_slot_end=out_data.get("time_slot_end", "18:00"),
            days=out_data.get("days", config.output.days),
        )

    # Load day patterns
    if "day_patterns" in data:
        config.day_patterns = data["day_patterns"]

    return config


def save_config(config: Config, path: Path) -> None:
    """
    Save configuration to a YAML file.

    Args:
        config: Config object to save.
        path: Path to write YAML file.
    """
    data: dict[str, Any] = {
        "capacity": {
            "default_capacity": config.capacity.default_capacity,
            "tutorial_bonus_seats": config.capacity.tutorial_bonus_seats,
            "max_overfill": config.capacity.max_overfill,
            "course_overfill_overrides": config.capacity.course_overfill_overrides,
            "section_capacity_overrides": config.capacity.section_capacity_overrides,
            "unlimited_capacity_courses": list(
                config.capacity.unlimited_capacity_courses
            ),
            "allow_negative_capacity": config.capacity.allow_negative_capacity,
        },
        "generator": {
            "num_strategies": config.generator.num_strategies,
            "min_timetables_per_plan": config.generator.min_timetables_per_plan,
            "variant_retry_attempts": config.generator.variant_retry_attempts,
            "max_greedy_attempts": config.generator.max_greedy_attempts,
            "batch_randomness": config.generator.batch_randomness,
            "min_batch_size": config.generator.min_batch_size,
            "compactness_weight": config.generator.compactness_weight,
        },
        "output": {
            "output_dir": config.output.output_dir,
            "generate_plan_csvs": config.output.generate_plan_csvs,
            "generate_pdf": config.output.generate_pdf,
            "pdf_page_size": config.output.pdf_page_size,
            "pdf_orientation": config.output.pdf_orientation,
            "time_slot_start": config.output.time_slot_start,
            "time_slot_end": config.output.time_slot_end,
            "days": config.output.days,
        },
        "day_patterns": config.day_patterns,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
