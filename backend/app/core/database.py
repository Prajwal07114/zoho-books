"""SQLAlchemy 2.0 Async Database Engine and Tenant Context Management."""

from typing import AsyncGenerator, Optional, Union
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings

# Runtime Engine: Uses non-superuser credentials (RLS enforced)
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
    echo=settings.DEBUG and settings.ENVIRONMENT == "development",
)

# Admin Engine: Uses superuser/migrator credentials (for DDL, migrations, admin setup)
admin_engine: AsyncEngine = create_async_engine(
    settings.ADMIN_DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def set_tenant_context(
    session: AsyncSession,
    organization_id: Optional[Union[UUID, str]] = None,
    user_id: Optional[Union[UUID, str]] = None,
) -> None:
    """Set transaction-local tenant context settings.

    Uses `is_local = true` to guarantee that settings are cleared at transaction end,
    preventing context leakage across pooled connections (PgBouncer compatible).
    """
    org_val = str(organization_id) if organization_id is not None else ""
    user_val = str(user_id) if user_id is not None else ""

    # Transaction-local configuration (cleared on COMMIT or ROLLBACK)
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": org_val},
    )
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": user_val},
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a database session within an explicit transaction."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
