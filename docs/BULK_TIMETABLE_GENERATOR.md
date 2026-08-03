# Bulk Timetable Generator

## Overview

The Bulk Timetable Generator is an automated system for generating conflict-free class timetables for students across multiple academic plans/branches. It reads course requirements, student counts, and available sections from input files, then uses optimization algorithms to assign students to sections while respecting capacity constraints.

## Key Features

- **Multi-Strategy Optimization**: Tests 10+ different allocation strategies and picks the best one
- **Conflict-Free Scheduling**: Ensures no time overlaps within any student's timetable
- **Capacity Management**: Tracks section capacity globally across all plans
- **Component Completeness**: Ensures each timetable includes all required components (LEC, TUT, LAB) for each course
- **Flexible Overfill Rules**: Allows lecture overfilling while strictly enforcing lab capacity limits
- **Self-Adaptive Balancing**: Distributes students evenly across sections to avoid overloading
- **Variant Mixing**: Generates multiple timetable variants per plan (default 10) and records each timetable's capacity ceiling for later assignment

---

## Input Files

### 1. `data/packages.json`

Defines which courses are required for each academic plan.

```json
{
  "2024": {
    "A3,A4,A5,A7,A8,AA,AD,AJ": [
      "BIO F101",
      "BITS F101",
      "BITS F111",
      "BITS K101",
      "CS F111",
      "MATH F101",
      "BITS F103"
    ],
    "A1,A2,AB,B1,B2,B3,B4,B5,B7,D2": [
      "BITS F101",
      "BITS F111",
      "BITS K101",
      "CHEM F101",
      "EEE F111",
      "MATH F101",
      "PHY F101",
      "BITS F103"
    ]
  }
}
```

### 2. `data/count.csv`

Contains the number of students in each plan.

```csv
Plan,Count
"A3,A4,A5,A7,A8,AA,AD,AJ",543
"A1,A2,AB,B1,B2,B3,B4,B5,B7",523
```

### 3. `data/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx`

The master timetable Excel file containing all available sections with:

- Course codes (Subject + Catalog)
- Class numbers (unique identifiers)
- Components (LEC, TUT, LAB, PRO)
- Section names (L1, T1, P1, etc.)
- Meeting times (days, start time, end time)
- Capacity (Cap Enrl)
- Room assignments
- Instructor names

---

## Output Files

All outputs are saved to `exports/bulk_timetables/`:

| File                         | Description                                                                                     |
| ---------------------------- | ----------------------------------------------------------------------------------------------- |
| `timetables_incremental.csv` | Complete list of all generated timetables (includes batch size, capacity ceiling, variant flag) |
| `timetables_summary.csv`     | Summary view of all timetables with capacity ceilings and variant flag                          |
| `timetables_classnbrs.csv`   | Class numbers for registration                                                                  |
| `timetable_<PLAN>.csv`       | Individual CSV per plan (includes batch size, capacity ceiling, variant flag)                   |
| `all_timetables.pdf`         | Visual timetable grids + capacity report                                                        |
| `capacity_report.csv`        | Detailed section-wise capacity usage                                                            |

---

## Technical Architecture

### Core Algorithm

```
1. MULTI-STRATEGY OPTIMIZATION
   ├── Test multiple plan orderings:
   │   ├── Original order
   │   ├── Smallest plans first
   │   ├── Largest plans first
   │   ├── Most constrained first
   │   ├── Least constrained first
   │   └── Random permutations (5x)
   │
   └── For each strategy:
       ├── Run single-pass generation
       ├── Calculate fitness score
       └── Track best result

2. SINGLE-PASS GENERATION (per plan)
   ├── Resolve course codes (BITS F101 → BITS F101-1)
   ├── While students remain:
   │   ├── Generate valid section combinations
   │   ├── Check time conflicts
   │   ├── Check capacity constraints
   │   ├── Calculate batch size (min capacity of strict sections)
   │   ├── Assign students
   │   └── Update global capacity
   │
   └── If stuck, enable TUT overfill (second pass)

3. FITNESS EVALUATION
   Score = (Assignment Ratio × 100) + (Balance Score × 20) - (Overfill Penalty × 5)
```

### Capacity Rules

| Component        | Overfill Allowed  | Notes                                   |
| ---------------- | ----------------- | --------------------------------------- |
| LEC (Lecture)    | Yes               | Can always overfill, distributed evenly |
| TUT (Tutorial)   | Yes (last resort) | Only after LEC-only overfill exhausted  |
| LAB (Laboratory) | No                | Hard limit for safety/equipment         |
| PRO (Practical)  | No                | Hard limit                              |

### Time Conflict Detection

Uses 5-minute resolution bitmasks for each day:

- Each day is a 288-bit vector (24 hours × 12 slots/hour)
- Sections are represented as bit ranges
- Conflict = bitwise AND of two sections is non-zero

### Self-Adaptive Balancing

The algorithm uses a balance score function:

```python
def balance_score(section):
    remaining = remaining_capacity[section.class_nbr]
    original = section.cap_enrl

    if remaining < 0:
        return remaining * 10  # Heavy penalty for overfilled
    elif remaining < original * 0.1:
        return remaining - original  # Penalty for near-full
    else:
        return remaining  # Prefer higher remaining capacity
```

---

## Usage

### Basic Usage

```bash
uv run python bulk_timetable_generator.py
```

### With Custom Paths

```bash
uv run python bulk_timetable_generator.py \
    --packages data/2025-1/packages.json \
    --count data/2025-1/count.csv \
    --timetable data/2025-1/BITS_TIME_TABLE_WITHFACILITY_01122025.xlsx \
    --output exports/bulk_timetables \
    --capacity 40 \
    --min-timetables-per-plan 10 \
    --variant-retries 3
```

### Verification

```bash
uv run python verify_timetables.py
```

### Capacity Report

```bash
uv run python capacity_report.py
```

---

## Verification Checks

The `verify_timetables.py` script performs these validations:

1. **Class Number Validation**: All class numbers in timetables exist in Excel
2. **Course Completeness**: All required courses from package are present
3. **Component Completeness**: All components (LEC, TUT, LAB) for each course are present
4. **Time Conflict Detection**: No overlapping meetings within a timetable
5. **Capacity Verification**: Cumulative enrollment doesn't exceed limits
   - LEC: Warning only (allowed to overfill)
   - TUT: Warning only (allowed as last resort)
   - LAB/PRO: Error (hard limit)

---

## Data Structures

### Section

```python
@dataclass
class Section:
    course_code: str      # "MATH F101"
    class_nbr: int        # 2813
    component: str        # "LEC", "TUT", "LAB"
    section: str          # "L1", "T3", "P5"
    cap_enrl: int         # 50
    tot_enrl: int         # 0 (ignored, start fresh)
    available_seats: int  # 50
    instructor: str
    room: str
    meetings: list[dict]  # [{"day": "Monday", "start": "09:00", "end": "10:00"}]
```

### GeneratedTimetable

```python
@dataclass
class GeneratedTimetable:
    plan: str             # "A3,A4,A5,A7,A8,AA,AD,AJ"
    timetable_id: int     # 1
    sections: list[Section]
    batch_size: int       # 44 students assigned
    capacity_ceiling: int # Max students this timetable could host
    is_variant: bool      # True if generated for mixing (may have 0 assigned)
```

---

## Performance Metrics

For the current dataset (1223 students, 8 plans, 13 courses):

| Metric            | Value            |
| ----------------- | ---------------- |
| Students Assigned | 1223/1223 (100%) |
| Total Timetables  | 52               |
| Generation Time   | ~30 seconds      |
| Strategies Tested | 10               |
| Best Strategy     | Random #3        |
| Balance Score     | 0.960            |
| Overfill Penalty  | 0.022            |

---

## Error Handling

### Common Issues

1. **"Course not found"**: Course in packages.json doesn't exist in Excel

   - Solution: Check course code spelling, may need mapping (BITS F101 → BITS F101-1)

2. **"No valid combination"**: All section combinations have conflicts

   - Solution: Increase random attempts, check for scheduling issues

3. **"LAB capacity exhausted"**: No more LAB sections available

   - Solution: Add more LAB sections or reduce student count

4. **"Cannot generate more timetables"**: Hit capacity limits
   - Solution: Allow TUT overfill (automatic), or add sections

---

## Configuration Constants

Located in `bulk_timetable_generator.py`:

```python
# Courses that can always exceed capacity
UNLIMITED_CAPACITY_COURSES = {"BITS F101-1", "BITS K101-1"}

# Components that can always be overfilled
OVERFILLABLE_COMPONENTS = {"LEC"}

# Components that can overfill as last resort
SOFT_STRICT_COMPONENTS = {"TUT"}

# Components with hard capacity limits
HARD_STRICT_COMPONENTS = {"LAB", "PRO", "PRA"}

# Number of random attempts for finding valid combinations
max_attempts = 50

# Number of optimization strategies to test
num_strategies = 10
```

---

## PDF Output Format

The generated PDF includes:

1. **Timetable Grids**: One page per timetable

   - 6 days × 10 time slots (8:00-18:00)
   - Color-coded by course
   - Shows course code, component-section, room

2. **Capacity Report** (at the end):
   - Course-wise breakdown
   - Component-wise tables (LEC, TUT, LAB)
   - Section-level details with fill percentages
   - Color-coded rows:
     - Red: Overfilled (>100%)
     - Yellow: Near full (90-100%)
     - Green: Moderate (50-90%)
     - White: Low (<50%)

---

## Dependencies

```
pandas>=2.0.0
openpyxl>=3.1.0
reportlab>=4.0.0
```

Install with:

```bash
uv add pandas openpyxl reportlab
```

---

## File Structure

```
portal/
├── bulk_timetable_generator.py   # Main generation script
├── verify_timetables.py          # Verification script
├── capacity_report.py            # Capacity analysis script
├── data/
│   ├── packages.json             # Course requirements per plan
│   ├── count.csv                 # Student counts per plan
│   └── BITS_TIME_TABLE_*.xlsx    # Master timetable
├── exports/
│   └── bulk_timetables/          # Generated outputs
│       ├── timetables_incremental.csv
│       ├── all_timetables.pdf
│       └── capacity_report.csv
└── docs/
    └── BULK_TIMETABLE_GENERATOR.md  # This documentation
```

---

## Authors

Generated with automated tools for BITS Pilani Academic Division.

---

## Version History

| Version | Date     | Changes                           |
| ------- | -------- | --------------------------------- |
| 1.0     | Dec 2025 | Initial implementation            |
| 1.1     | Dec 2025 | Added multi-strategy optimization |
| 1.2     | Dec 2025 | Added self-adaptive balancing     |
| 1.3     | Dec 2025 | Added TUT overfill as second pass |
| 1.4     | Dec 2025 | Added capacity report to PDF      |
