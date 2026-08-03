"""Service layer for global settings management"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.global_settings import GlobalSettings
from app.schemas.global_settings import GlobalSettingsUpdate


async def get_or_create_global_settings(db: AsyncSession) -> GlobalSettings:
    """
    Get the global settings row, creating it if it doesn't exist.

    There should only be one row in the global_settings table.
    """
    result = await db.execute(select(GlobalSettings).limit(1))
    settings = result.scalar_one_or_none()

    if not settings:
        settings = GlobalSettings()
        db.add(settings)
        await db.flush()
        await db.refresh(settings)

    return settings


async def update_global_settings(
    db: AsyncSession, settings_data: GlobalSettingsUpdate
) -> GlobalSettings:
    """Update global settings"""
    settings = await get_or_create_global_settings(db)

    if settings_data.default_term_code is not None:
        settings.default_term_code = settings_data.default_term_code
    if settings_data.default_career is not None:
        settings.default_career = settings_data.default_career
    if settings_data.institution_name is not None:
        settings.institution_name = settings_data.institution_name
    if settings_data.session_name_template is not None:
        settings.session_name_template = settings_data.session_name_template
    if settings_data.auto_generate_session_names is not None:
        settings.auto_generate_session_names = settings_data.auto_generate_session_names

    await db.flush()
    await db.refresh(settings)
    return settings
