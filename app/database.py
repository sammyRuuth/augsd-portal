"""Database connection and session management"""

from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import get_settings

settings = get_settings()

# Create async engine for global database
# Use smaller pool with more overflow to handle bursty traffic better
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,  # Base pool size
    max_overflow=30,  # Allow burst of connections
    pool_recycle=1800,  # Recycle connections after 30 minutes
    pool_timeout=10,  # Wait max 10s for a connection from pool
    connect_args={
        "prepared_statement_cache_size": 0,  # Disable prepared statement cache to avoid schema change issues
        "server_settings": {
            "jit": "off",  # Disable JIT for faster simple queries
            "statement_timeout": "300000",  # 5 minutes timeout for statements
        },
    },
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base class for SQLAlchemy models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            # Set shorter lock timeout to fail fast rather than block
            await session.execute(text("SET lock_timeout = '5s'"))
            # Set shorter idle timeout to release connections faster
            await session.execute(
                text("SET idle_in_transaction_session_timeout = '30s'")
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_session_schema(schema_name: str) -> None:
    """Create a new PostgreSQL schema for a session"""
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))


async def drop_session_schema(schema_name: str) -> None:
    """Drop a PostgreSQL schema for a session"""
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))


async def get_session_db(schema_name: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session with schema search path set to session schema.

    IMPORTANT: Callers must commit/rollback explicitly. This generator does NOT
    auto-commit to avoid double-commit issues when callers manage transactions.
    """
    async with AsyncSessionLocal() as session:
        try:
            # Set search path to session schema, then public
            await session.execute(text(f'SET search_path TO "{schema_name}", public'))
            # Set shorter lock timeout to fail fast rather than block other requests
            await session.execute(text("SET lock_timeout = '5s'"))
            # Increase idle timeout for long-running upload operations
            await session.execute(
                text("SET idle_in_transaction_session_timeout = '300s'")
            )
            yield session
            # No auto-commit here - callers must commit explicitly to avoid
            # double-commit when they call commit() before break
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_session_db_read_only(
    schema_name: str,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Get a read-only database session with schema search path set to session schema.

    This uses READ COMMITTED isolation and disables locking for better concurrency
    during read-heavy operations.
    """
    async with AsyncSessionLocal() as session:
        try:
            # Set search path to session schema, then public
            await session.execute(text(f'SET search_path TO "{schema_name}", public'))
            # Use READ COMMITTED for better concurrency on reads
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            )
            await session.execute(text("SET TRANSACTION READ ONLY"))
            # Very short lock timeout since we shouldn't be locking anything
            await session.execute(text("SET lock_timeout = '1s'"))
            yield session
            # No commit needed for read-only
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
