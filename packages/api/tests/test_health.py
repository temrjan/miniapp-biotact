"""Health endpoint tests."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "app" in data


@pytest.mark.asyncio
async def test_health_check_fails_when_db_unavailable(client: AsyncClient):
    """Health check must fail when the database is unreachable."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))

    with patch("src.main.async_session_maker", return_value=mock_session):
        response = await client.get("/health")

    assert response.status_code == 503
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    """Test root endpoint."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "version" in data
