"""Central API Router for Ledgerly."""

from fastapi import APIRouter
from app.api.endpoints import health

api_router = APIRouter()

# Health endpoints mounted at root and prefixed
api_router.include_router(health.router)
