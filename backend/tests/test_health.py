"""Tests for service health endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_endpoint(client: AsyncClient):
    """Confirm the basic service health endpoint responds with 200 OK."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "ledgerly-backend"
    assert data["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_api_v1_health_check_endpoint(client: AsyncClient):
    """Confirm the /api/v1/health endpoint also responds correctly."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_database_health_check_endpoint(client: AsyncClient):
    """Confirm database connectivity endpoint performs a real query."""
    response = await client.get("/health/db")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert "PostgreSQL" in data["version"]
