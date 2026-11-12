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
async def test_create_product(client: AsyncClient, admin_headers: dict[str, str]):
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
    response = await client.post("/api/v1/products", json=product_data, headers=admin_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "iPhone 15"
    assert data["slug"] == "iphone-15"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_product_duplicate_slug(client: AsyncClient, admin_headers: dict[str, str]):
    """Test creating product with duplicate slug."""
    product_data = {
        "name": "Test Product",
        "slug": "test-product",
        "price": "100.00",
    }
    response = await client.post("/api/v1/products", json=product_data, headers=admin_headers)
    assert response.status_code == 201

    response = await client.post("/api/v1/products", json=product_data, headers=admin_headers)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_get_product_by_id(client: AsyncClient, admin_headers: dict[str, str]):
    """Test getting product by ID."""
    product_data = {"name": "Samsung", "slug": "samsung", "price": "5000000.00"}
    create_response = await client.post(
        "/api/v1/products", json=product_data, headers=admin_headers
    )
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
async def test_update_product(client: AsyncClient, admin_headers: dict[str, str]):
    """Test updating a product."""
    product_data = {"name": "Original", "slug": "original-product", "price": "1000.00"}
    create_response = await client.post(
        "/api/v1/products", json=product_data, headers=admin_headers
    )
    created = create_response.json()

    update_data = {"name": "Updated", "price": "2000.00"}
    response = await client.patch(
        f"/api/v1/products/{created['id']}", json=update_data, headers=admin_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated"
    assert float(data["price"]) == 2000.00


@pytest.mark.asyncio
async def test_delete_product(client: AsyncClient, admin_headers: dict[str, str]):
    """Test deleting a product."""
    product_data = {"name": "ToDelete", "slug": "to-delete-product", "price": "100.00"}
    create_response = await client.post(
        "/api/v1/products", json=product_data, headers=admin_headers
    )
    created = create_response.json()

    response = await client.delete(f"/api/v1/products/{created['id']}", headers=admin_headers)
    assert response.status_code == 204

    get_response = await client.get(f"/api/v1/products/{created['id']}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_search_products(client: AsyncClient, admin_headers: dict[str, str]):
    """Test searching products."""
    await client.post(
        "/api/v1/products",
        json={"name": "Apple iPhone", "slug": "apple-iphone", "price": "1000.00"},
        headers=admin_headers,
    )
    await client.post(
        "/api/v1/products",
        json={"name": "Samsung Galaxy", "slug": "samsung-galaxy", "price": "800.00"},
        headers=admin_headers,
    )

    response = await client.get("/api/v1/products?search=Apple")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any("Apple" in item["name"] for item in data["items"])


@pytest.mark.parametrize('sale_price', [None, '0', '-1', '100', '101', '80.001'])
@pytest.mark.parametrize('method', ['create', 'patch'])
async def test_reject_invalid_sale(client, admin_headers, sale_price, method):
    payload = {'name': 'Sale', 'slug': 'sale', 'price': '100.00'}
    sale = {'is_sale': True, 'sale_price': sale_price}
    if method == 'create':
        response = await client.post('/api/v1/products', json=payload | sale, headers=admin_headers)
    else:
        created = (await client.post('/api/v1/products', json=payload, headers=admin_headers)).json()
        response = await client.patch(f"/api/v1/products/{created['id']}", json=sale, headers=admin_headers)
        current = (await client.get(f"/api/v1/products/{created['id']}")).json()
        assert current['price'] == '100.00'
    assert response.status_code == 422


async def test_sale_merged_validation_and_dormant_price(client, admin_headers):
    created = await client.post('/api/v1/products', headers=admin_headers, json={
        'name': 'Sale', 'slug': 'sale', 'price': '200.00', 'old_price': '250.00',
        'is_sale': True, 'sale_price': '150.00',
    })
    assert created.status_code == 201
    pid = created.json()['id']
    for patch in [{'price': '100.00'}, {'sale_price': None}]:
        response = await client.patch(f'/api/v1/products/{pid}', headers=admin_headers, json=patch)
        assert response.status_code == 422
        assert response.json()['detail']['code'] == 'sale_invariant_violation'
    response = await client.patch(f'/api/v1/products/{pid}', headers=admin_headers, json={'is_sale': False})
    assert response.status_code == 200
    assert response.json()['sale_price'] == '150.00'
    response = await client.patch(f'/api/v1/products/{pid}', headers=admin_headers, json={'is_sale': True, 'price': '160.00'})
    assert response.status_code == 200
    assert response.json()['old_price'] == '250.00'


async def test_public_sale_fields_and_filter(client, admin_headers):
    for slug, sale, active in [('sale', True, True), ('regular', False, True), ('inactive', True, False)]:
        response = await client.post('/api/v1/products', headers=admin_headers, json={
            'name': slug, 'slug': slug, 'price': '200.00', 'old_price': '250.00',
            'is_sale': sale, 'sale_price': '150.00', 'is_active': active,
        })
        assert response.status_code == 201
    response = await client.get('/api/v1/products?on_sale=true&active_only=false')
    assert [p['slug'] for p in response.json()['items']] == ['sale']
    assert response.json()['total'] == 1
    sale = response.json()['items'][0]
    for url in [f"/api/v1/products/{sale['id']}", '/api/v1/products/slug/sale']:
        data = (await client.get(url)).json()
        assert data['is_sale'] is True
        assert data['sale_price'] == '150.00'
        assert data['price'] == '200.00'
        assert data['old_price'] == '250.00'
    assert (await client.get('/api/v1/products')).json()['total'] == 2


async def test_sale_patch_keeps_other_integrity_conflicts(client, admin_headers, test_db):
    products = []
    for slug in ['first', 'second']:
        data = (await client.post('/api/v1/products', headers=admin_headers, json={
            'name': slug, 'slug': slug, 'price': '200.00',
        })).json()
        products.append(data['id'])
    await test_db.commit()
    response = await client.patch(f'/api/v1/products/{products[1]}', headers=admin_headers, json={'slug': 'first'})
    assert response.status_code == 409
    await test_db.rollback()
    data = (await client.get(f'/api/v1/products/{products[1]}')).json()
    assert data['slug'] == 'second'


@pytest.mark.parametrize("identity", ["missing", "user"])
async def test_sale_patch_requires_admin_and_preserves_product(
    client: AsyncClient, admin_headers: dict[str, str], identity: str
) -> None:
    from src.config import settings
    from tests.test_admin_auth_boundary import _user_access_token

    created = await client.post("/api/v1/products", headers=admin_headers, json={
        "name": "Protected sale", "slug": "protected-sale", "price": "200.00",
    })
    assert created.status_code == 201
    url = f"/api/v1/products/{created.json()['id']}"
    before = (await client.get(url)).json()
    headers = {} if identity == "missing" else {
        "Authorization": f"Bearer {_user_access_token(secret=settings.secret_key.get_secret_value())}",
    }
    patch = {"is_sale": True, "sale_price": "150.00"}
    denied = await client.patch(url, headers=headers, json=patch)
    assert denied.status_code == 401
    assert (await client.get(url)).json() == before
    allowed = await client.patch(url, headers=admin_headers, json=patch)
    assert allowed.status_code == 200
    after = (await client.get(url)).json()
    assert after["is_sale"] is True
    assert after["sale_price"] == "150.00"


@pytest.mark.asyncio
async def test_search_matches_across_scripts_fields_and_spacing(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """Storefront search (2026-11-12): Cyrillic finds Latin names, spaces, hyphens and ®
    do not count, and the description and category name are searched too."""
    category = await client.post(
        "/api/v1/categories",
        json={"name": "Нутриенты и витаминные добавки", "slug": "nutrients"},
        headers=admin_headers,
    )
    assert category.status_code == 201, category.text
    other = await client.post(
        "/api/v1/categories",
        json={"name": "Техника", "slug": "technique"},
        headers=admin_headers,
    )
    assert other.status_code == 201, other.text
    for name, slug, category_id, description in (
        ("IMMUNOCOMPLEX", "immuno", category.json()["id"], None),
        ("DERMACOMPLEX", "derma", category.json()["id"], None),
        ("NEUROCOMPLEX", "neuro", category.json()["id"], None),
        ("BIFOLAK® MAGNIY", "magniy", other.json()["id"], None),
        ("Рюкзак-сумка", "bag", other.json()["id"], "Для мамы и малыша"),
    ):
        created = await client.post(
            "/api/v1/products",
            json={
                "name": name,
                "slug": slug,
                "price": "1000.00",
                "category_id": category_id,
                "description": description,
            },
            headers=admin_headers,
        )
        assert created.status_code == 201, created.text

    async def found(query: str) -> set[str]:
        response = await client.get("/api/v1/products", params={"search": query, "size": 100})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == len(data["items"])
        return {item["name"] for item in data["items"]}

    complexes = {"IMMUNOCOMPLEX", "DERMACOMPLEX", "NEUROCOMPLEX"}
    # The four zero-result queries of the task.
    assert await found("иммуно") == {"IMMUNOCOMPLEX"}
    assert await found("дерма") == {"DERMACOMPLEX"}
    assert await found("витамин") == complexes
    assert await found("neuro complex") == {"NEUROCOMPLEX"}
    # Name matches of before stay.
    assert await found("IMMUNO") == {"IMMUNOCOMPLEX"}
    assert await found("рюкзак") == {"Рюкзак-сумка"}
    assert await found("комплекс") == complexes
    assert await found("магний") == {"BIFOLAK® MAGNIY"}
    assert await found("bifolak magniy") == {"BIFOLAK® MAGNIY"}
    assert await found("рюкзак сумка") == {"Рюкзак-сумка"}
    # The description is searched.
    assert await found("мамы") == {"Рюкзак-сумка"}
    # A query without letters or digits filters nothing, as before; % is no pattern.
    every = complexes | {"BIFOLAK® MAGNIY", "Рюкзак-сумка"}
    assert await found("®") == every
    assert await found("%") == every
    assert await found("100%") == set()
