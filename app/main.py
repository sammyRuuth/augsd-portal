"""Main FastAPI application"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    admin,
    advanced_generation,
    auth,
    courses,
    export,
    frontend,
    registration_timetables,
    saved_drafts,
    sessions,
    statistics,
    students,
    timetable_buckets,
    timetables,
)
from app.config import get_settings
from app.database import Base, engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown"""
    # Startup
    async with engine.begin() as conn:
        # Create all tables in global database
        await conn.run_sync(Base.metadata.create_all)

    # Run schema migrations for all existing sessions
    await migrate_all_session_schemas()

    yield

    # Shutdown
    await engine.dispose()


async def migrate_all_session_schemas():
    """Run schema migrations for all existing session schemas on startup"""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.session import Session
    from app.services.session_service import ensure_schema_columns

    async with AsyncSessionLocal() as db:
        try:
            # Get all sessions
            result = await db.execute(select(Session.schema_name))
            schema_names = [row[0] for row in result.fetchall()]

            if schema_names:
                print(f"Running schema migrations for {len(schema_names)} sessions...")
                for schema_name in schema_names:
                    try:
                        await ensure_schema_columns(schema_name)
                    except Exception as e:
                        print(f"Warning: Failed to migrate schema {schema_name}: {e}")
                print("Schema migrations complete.")
        except Exception as e:
            # Don't fail startup if migrations fail (table might not exist yet)
            print(f"Schema migration skipped: {e}")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="AUGSD Portal - Timetable Generation and Management System",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # type: ignore[arg-type]
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:23090",
        "http://localhost:8000",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include API routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(sessions.router)
app.include_router(students.router)
app.include_router(timetables.router)
app.include_router(registration_timetables.router)
app.include_router(courses.router)
app.include_router(statistics.router)
app.include_router(export.router)
app.include_router(advanced_generation.router)
app.include_router(saved_drafts.router)
app.include_router(timetable_buckets.router)

# Include frontend routes (must be last to avoid conflicts with API routes)
app.include_router(frontend.router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}
