# AUGSD Portal

FastAPI-based timetable generation and management system for academic sessions, course sections, student uploads, and timetable generation.

## What is in this repo

- `app/`: FastAPI app, models, services, templates, and API routes
- `scripts/`: admin and database utility scripts
- `tests/`: API, parser, generator, and integration tests
- `sample_files/`: sample inputs used by the parser and integration tests
- `docs/BULK_TIMETABLE_GENERATOR.md`: bulk timetable generation workflow

## Local Setup

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Docker Desktop or a local PostgreSQL instance

### 1. Configure environment

Copy the template and adjust values only if you are not using the default local setup.

```bash
cp .env.example .env
```

The default `.env.example` values expect PostgreSQL on `localhost:5432` with database `portal_global`.

### 2. Start PostgreSQL

If you want the repo to manage PostgreSQL for you:

```bash
docker compose up -d db
```

If you are using your own PostgreSQL instance instead, create the database yourself and point `DATABASE_URL` in `.env` at it.

### 3. Install dependencies

```bash
uv sync
```

### 4. Initialize the database

This creates the global tables and leaves any existing admin users alone.

```bash
uv run python scripts/init_db.py
```

### 5. Run the app

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 23090
```

Open:

- App/API: `http://localhost:23090`
- OpenAPI docs: `http://localhost:23090/docs`
- Health check: `http://localhost:23090/health`

## Docker Setup

To run the full stack in containers:

```bash
docker compose up -d
```

That starts:

- `db` on `127.0.0.1:5432`
- `app` on `http://localhost:23090`

## Verification

Run the full test suite:

```bash
uv run pytest
```

Useful checks:

```bash
docker compose ps
uv run ruff check app tests scripts
```

## Common Commands

Run the dev server:

```bash
make dev
```

Start only the database:

```bash
make up
```

Initialize users or inspect accounts:

```bash
make manage-users CMD="list"
```

## Data Model

### Global tables

- `users`
- `sessions`
- `courses`
- `prerequisites`
- `default_packages`

### Per-session schema tables

- `students`
- `course_sections`
- `timetables`
- `registration_data`
- related timetable item tables

## Notes

- Runtime directories such as `uploads/`, `exports/`, `logs/`, and `backups/` are created as needed.
- The test suite uses the sample Excel files in `sample_files/`.
- For bulk generation workflows, use `docs/BULK_TIMETABLE_GENERATOR.md`.
