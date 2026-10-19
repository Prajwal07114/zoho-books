"""Health and System Status Endpoints."""

from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=Dict[str, Any])
async def health_check() -> Dict[str, Any]:
    """Basic service liveness check."""
    return {
        "status": "healthy",
        "service": "ledgerly-backend",
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/db", response_model=Dict[str, Any])
async def database_health_check(
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Database readiness check verifying live PostgreSQL connection."""
    try:
        result = await db.execute(text("SELECT version();"))
        version = result.scalar()
        return {
            "status": "healthy",
            "database": "connected",
            "version": version,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database health check failed: {str(e)}",
        )
