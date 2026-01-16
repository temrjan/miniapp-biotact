"""Category API tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_categories_empty(client: AsyncClient):
    """Test getting categories when empty."""
    response = await client.get("/api/v1/categories")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_create_category(client: AsyncClient):
    """Test creating a category."""
    category_data = {
        "name": "Electronics",
        "name_uz": "Elektronika",
        "slug": "electronics",
        "description": "Electronic devices",
        "is_active": True,
    }
    response = await client.post("/api/v1/categories", json=category_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Electronics"
    assert data["slug"] == "electronics"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_category_duplicate_slug(client: AsyncClient):
    """Test creating category with duplicate slug."""
    category_data = {
        "name": "Test Category",
        "slug": "test-slug",
    }
    # Create first
    response = await client.post("/api/v1/categories", json=category_data)
    assert response.status_code == 201

    # Try to create duplicate
    response = await client.post("/api/v1/categories", json=category_data)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_get_category_by_id(client: AsyncClient):
    """Test getting category by ID."""
    # Create category
    category_data = {"name": "Clothing", "slug": "clothing"}
    create_response = await client.post("/api/v1/categories", json=category_data)
    created = create_response.json()

    # Get by ID
    response = await client.get(f"/api/v1/categories/{created['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Clothing"


@pytest.mark.asyncio
async def test_get_category_not_found(client: AsyncClient):
    """Test getting non-existent category."""
    response = await client.get("/api/v1/categories/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_category(client: AsyncClient):
    """Test updating a category."""
    # Create category
    category_data = {"name": "Original", "slug": "original"}
    create_response = await client.post("/api/v1/categories", json=category_data)
    created = create_response.json()

    # Update
    update_data = {"name": "Updated"}
    response = await client.patch(
        f"/api/v1/categories/{created['id']}", json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated"
    assert data["slug"] == "original"  # Unchanged


@pytest.mark.asyncio
async def test_delete_category(client: AsyncClient):
    """Test deleting a category."""
    # Create category
    category_data = {"name": "ToDelete", "slug": "to-delete"}
    create_response = await client.post("/api/v1/categories", json=category_data)
    created = create_response.json()

    # Delete
    response = await client.delete(f"/api/v1/categories/{created['id']}")
    assert response.status_code == 204

    # Verify deleted
    get_response = await client.get(f"/api/v1/categories/{created['id']}")
    assert get_response.status_code == 404
