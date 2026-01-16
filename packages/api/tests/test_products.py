"""Product API tests."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_products_empty(client: AsyncClient):
    """Test getting products when empty."""
    response = await client.get("/api/v1/products")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_create_product(client: AsyncClient):
    """Test creating a product."""
    product_data = {
        "name": "iPhone 15",
        "name_uz": "iPhone 15",
        "slug": "iphone-15",
        "description": "Latest iPhone",
        "price": "12000000.00",
        "currency": "UZS",
        "is_active": True,
    }
    response = await client.post("/api/v1/products", json=product_data)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "iPhone 15"
    assert data["slug"] == "iphone-15"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_product_duplicate_slug(client: AsyncClient):
    """Test creating product with duplicate slug."""
    product_data = {
        "name": "Test Product",
        "slug": "test-product",
        "price": "100.00",
    }
    # Create first
    response = await client.post("/api/v1/products", json=product_data)
    assert response.status_code == 201

    # Try to create duplicate
    response = await client.post("/api/v1/products", json=product_data)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_get_product_by_id(client: AsyncClient):
    """Test getting product by ID."""
    product_data = {"name": "Samsung", "slug": "samsung", "price": "5000000.00"}
    create_response = await client.post("/api/v1/products", json=product_data)
    created = create_response.json()

    response = await client.get(f"/api/v1/products/{created['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Samsung"


@pytest.mark.asyncio
async def test_get_product_not_found(client: AsyncClient):
    """Test getting non-existent product."""
    response = await client.get("/api/v1/products/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_product(client: AsyncClient):
    """Test updating a product."""
    product_data = {"name": "Original", "slug": "original-product", "price": "1000.00"}
    create_response = await client.post("/api/v1/products", json=product_data)
    created = create_response.json()

    update_data = {"name": "Updated", "price": "2000.00"}
    response = await client.patch(f"/api/v1/products/{created['id']}", json=update_data)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated"
    assert float(data["price"]) == 2000.00


@pytest.mark.asyncio
async def test_delete_product(client: AsyncClient):
    """Test deleting a product."""
    product_data = {"name": "ToDelete", "slug": "to-delete-product", "price": "100.00"}
    create_response = await client.post("/api/v1/products", json=product_data)
    created = create_response.json()

    response = await client.delete(f"/api/v1/products/{created['id']}")
    assert response.status_code == 204

    get_response = await client.get(f"/api/v1/products/{created['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_search_products(client: AsyncClient):
    """Test searching products."""
    # Create products
    await client.post("/api/v1/products", json={
        "name": "Apple iPhone", "slug": "apple-iphone", "price": "1000.00"
    })
    await client.post("/api/v1/products", json={
        "name": "Samsung Galaxy", "slug": "samsung-galaxy", "price": "800.00"
    })

    # Search
    response = await client.get("/api/v1/products?search=Apple")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any("Apple" in item["name"] for item in data["items"])
