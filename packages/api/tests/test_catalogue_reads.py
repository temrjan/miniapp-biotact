"""S5 real HTTP/PostgreSQL catalogue contracts and migrated-schema freshness."""

import asyncio
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src import database
from src.banners.models import Banner
from src.categories.models import Category
from src.main import app
from src.products.cache import catalogue_headers
from src.products.models import Product, ProductSize
from tests import test_category_schema_migration as migration

scratch = migration.scratch

PRODUCT_KEYS = {
    "id",
    "name",
    "slug",
    "description",
    "price",
    "is_sale",
    "sale_price",
    "old_price",
    "currency",
    "stock_quantity",
    "requires_size",
    "available_sizes",
    "is_in_stock",
    "is_purchasable",
    "image_url",
    "images",
    "category_id",
    "mxik",
    "package_code",
    "vat",
    "advantages",
    "video_url",
    "additional_info_title",
    "additional_info",
    "disclaimer",
    "sort_order",
    "is_active",
    "is_featured",
    "created_at",
    "updated_at",
}
TRANSLATIONS = {
    "name_uz",
    "description_uz",
    "additional_info_title_uz",
    "additional_info_uz",
    "disclaimer_uz",
}
CATEGORY_KEYS = {
    "id",
    "name",
    "name_uz",
    "slug",
    "description",
    "description_uz",
    "image_url",
    "sort_order",
    "is_active",
    "is_purchasable",
    "created_at",
    "updated_at",
}
BANNER_KEYS = {
    "id",
    "title",
    "title_uz",
    "subtitle",
    "subtitle_uz",
    "image_url",
    "image_mobile_url",
    "video_url",
    "video_mobile_url",
    "gradient",
    "link_url",
    "sort_order",
    "is_active",
    "created_at",
    "updated_at",
}


def assert_headers(response, *, localized=True):
    assert response.headers["cache-control"] == "no-store"
    if localized:
        assert {"accept-language", "x-language"} <= {
            token.strip().lower() for token in response.headers["vary"].split(",")
        }


async def traverse(client, path, *, params=None, size=17):
    result = []
    total = None
    for page in range(1, 20):
        response = await client.get(path, params={**(params or {}), "page": page, "size": size})
        assert response.status_code == 200, response.text
        assert_headers(response)
        data = response.json()
        assert data["page"] == page
        assert data["size"] == size
        if total is not None:
            assert data["total"] == total
        total = data["total"]
        assert data["pages"] == max(1, (total + size - 1) // size)
        result.extend(item["id"] for item in data["items"])
        if page == data["pages"]:
            past = await client.get(path, params={**(params or {}), "page": page + 1, "size": size})
            assert past.json() == {**data, "page": page + 1, "items": []}
            return result
    pytest.fail("Traversal did not terminate")


@pytest.mark.asyncio
async def test_complete_ordered_ties_and_boundaries(client: AsyncClient, test_db: AsyncSession):
    # Reverse physical insertion order; independently known IDs resolve every tie.
    for i in range(102, 0, -1):
        test_db.add(Category(id=i, name="A" if i <= 51 else "B", slug=f"c-{i}", sort_order=i % 2))
        test_db.add(
            Product(
                id=i,
                name="A" if i <= 51 else "B",
                slug=f"p-{i}",
                price=Decimal(100 if i <= 51 else 200),
                sort_order=i % 2,
            )
        )
        test_db.add(Banner(id=i, title=f"B{i}", sort_order=i % 2, is_active=i > 100))
    await test_db.commit()
    ids = list(range(1, 103))
    expected = {
        "": sorted(ids, key=lambda i: (i % 2, i > 51, i)),
        "unknown": sorted(ids, key=lambda i: (i % 2, i > 51, i)),
        "id_asc": ids,
        "id_desc": ids[::-1],
        "name_asc": ids,
        "name_desc": list(range(52, 103)) + list(range(1, 52)),
        "price_asc": ids,
        "price_desc": list(range(52, 103)) + list(range(1, 52)),
    }
    for sort, ordered in expected.items():
        actual = await traverse(client, "/api/v1/products", params={"sort_by": sort})
        assert actual == ordered
        assert len(set(actual)) == 102
    assert await traverse(client, "/api/v1/categories") == expected[""]
    # Short last pages and repeat traversal agree with the exact-multiple selection.
    assert await traverse(client, "/api/v1/products", size=100) == expected[""]
    banners = []
    for skip in range(0, 103, 17):
        response = await client.get("/api/v1/banners", params={"skip": skip, "limit": 17})
        assert_headers(response, localized=False)
        assert response.json()["total"] == 102
        banners.extend(item["id"] for item in response.json()["items"])
    assert banners == sorted(ids, key=lambda i: (i % 2, i))
    active = await client.get("/api/v1/banners?is_active=true")
    assert active.json()["total"] == 2
    assert [row["id"] for row in active.json()["items"]] == [102, 101]
    assert (await client.get("/api/v1/banners?is_active=false")).json()["total"] == 100
    assert (await client.get("/api/v1/banners?skip=103")).json() == {"items": [], "total": 102}


@pytest.mark.asyncio
async def test_membership_legacy_filters_and_empty_single_sets(client, test_db):
    assert await traverse(client, "/api/v1/products") == []
    assert await traverse(client, "/api/v1/categories") == []
    assert (await client.get("/api/v1/banners")).json() == {"items": [], "total": 0}
    test_db.add(Category(id=1, name="Inactive", slug="inactive", is_active=False))
    await test_db.flush()
    for i in range(1, 9):
        test_db.add(
            Product(
                id=i,
                name="Русский" if i == 1 else "Other",
                name_uz="UZ match",
                slug=f"p-{i}",
                price=Decimal(100),
                is_sale=i % 2 == 1,
                sale_price=Decimal(80) if i % 2 == 1 else None,
                is_active=i <= 4,
                is_featured=i in {1, 2, 5},
                category_id=1 if i != 4 else None,
            )
        )
    await test_db.commit()
    for params, expected in [
        ({"sort_by": "id_asc"}, [1, 2, 3, 4]),
        ({"active_only": "false", "sort_by": "id_asc"}, list(range(1, 9))),
        ({"on_sale": "true", "active_only": "false", "sort_by": "id_asc"}, [1, 3]),
        ({"category_id": 1, "featured_only": "true", "on_sale": "true"}, [1]),
        ({"category_id": 999}, []),
        ({"category_id": 0, "sort_by": "id_asc"}, [1, 2, 3, 4]),
        ({"search": "Русс"}, [1]),
        # Storefront search (2026-11-12) also reads the Uzbek name.
        ({"search": "UZ match", "sort_by": "id_asc"}, [1, 2, 3, 4]),
        ({"search": "%", "sort_by": "id_asc"}, [1, 2, 3, 4]),
    ]:
        assert await traverse(client, "/api/v1/products", params=params, size=2) == expected
    assert await traverse(client, "/api/v1/categories") == []
    assert await traverse(client, "/api/v1/categories", params={"active_only": "false"}) == [1]
    # Inactive detail/full-translation reads stay public, and garment size is not a filter.
    assert (
        await client.get("/api/v1/products/5?include_all_translations=true&sort_by=name_desc")
    ).status_code == 200
    assert (await client.get("/api/v1/categories/1")).status_code == 200
    assert (await client.get("/api/v1/products?categoryId=999&limit=1&size=2")).json()["total"] == 4


@pytest.mark.asyncio
async def test_language_projection_media_inventory_and_cache(client, test_db):
    media = "https://app.biotact.uz/uploads/catalogue-s5.png"
    test_db.add(
        Category(
            id=1,
            name="RU category",
            name_uz="UZ category",
            slug="category",
            description="RU category description",
            description_uz="",
            image_url=media,
            is_purchasable=False,
        )
    )
    await test_db.flush()
    p = Product(
        id=1,
        name="RU name",
        name_uz="UZ name",
        slug="localized",
        price=Decimal("100.25"),
        old_price=Decimal("120.00"),
        is_sale=True,
        sale_price=Decimal("80.50"),
        description="RU description",
        description_uz="",
        category_id=1,
        image_url=media,
        images=[media],
        video_url="https://example.test/video",
        additional_info_title="RU title",
        additional_info_title_uz=None,
        additional_info="RU info",
        additional_info_uz="UZ info",
        disclaimer="RU disclaimer",
        disclaimer_uz="",
        stock_quantity=41,
        mxik="12345678901234567",
        package_code="1234567",
        vat=12,
    )
    test_db.add(p)
    test_db.add(
        Product(
            id=2, name="Uncategorized", slug="uncategorized", price=Decimal(9), stock_quantity=7
        )
    )
    test_db.add(
        Banner(id=1, title="RU banner", title_uz="UZ banner", subtitle=None, image_url=media)
    )
    await test_db.flush()
    for size, quantity in [("2XL", 4), ("XL", 0), ("L", 2)]:
        test_db.add(ProductSize(product_id=1, size=size, available_quantity=quantity, revision=8))
    await test_db.commit()
    for headers, name, description, info in [
        ({}, "RU name", "RU description", "RU info"),
        ({"Accept-Language": "UZ-uz"}, "UZ name", "RU description", "UZ info"),
        ({"Accept-Language": "uz", "X-Language": "RU"}, "RU name", "RU description", "RU info"),
        ({"Accept-Language": "ru", "X-Language": "Uz"}, "UZ name", "RU description", "UZ info"),
    ]:
        projected = []
        for path in ("/products?category_id=1", "/products/1", "/products/slug/localized"):
            response = await client.get("/api/v1" + path, headers=headers)
            assert_headers(response)
            data = response.json()["items"][0] if "?" in path else response.json()
            assert set(data) == PRODUCT_KEYS
            assert (data["name"], data["description"], data["additional_info"]) == (
                name,
                description,
                info,
            )
            assert data["additional_info_title"] == "RU title"
            assert data["disclaimer"] == "RU disclaimer"
            assert data["stock_quantity"] is None
            assert data["available_sizes"] == ["L", "2XL"]
            assert data["requires_size"] is True
            assert data["is_in_stock"] is True
            assert data["is_purchasable"] is False
            assert data["price"] == "100.25"
            assert data["sale_price"] == "80.50"
            assert data["image_url"] == media
            assert data["images"] == [media]
            projected.append(data)
        assert projected[0] == projected[1] == projected[2]
        for path in ("/products?category_id=1&", "/products/1?", "/products/slug/localized?"):
            data = (
                await client.get(
                    "/api/v1" + path + "include_all_translations=true", headers=headers
                )
            ).json()
            data = data["items"][0] if "items" in data else data
            assert set(data) == PRODUCT_KEYS | TRANSLATIONS
            assert data["name"] == "RU name"
            assert data["name_uz"] == "UZ name"
            assert data["description_uz"] == ""
            assert data["additional_info_uz"] == "UZ info"
            assert data["stock_quantity"] is None
            assert data["is_purchasable"] is False
    ordinary = (await client.get("/api/v1/products/2")).json()
    assert ordinary["stock_quantity"] == 7
    assert ordinary["is_purchasable"] is True
    for merchant in (False, True):
        p.is_in_stock = merchant
        await test_db.commit()
        assert (await client.get("/api/v1/products/1")).json()["is_in_stock"] is merchant
    for row in (await test_db.execute(select(ProductSize))).scalars():
        row.available_quantity = 0
    await test_db.commit()
    depleted = (await client.get("/api/v1/products/1")).json()
    assert depleted["available_sizes"] == []
    assert depleted["is_in_stock"] is False
    categories = []
    for path in ("/categories", "/categories/1", "/categories/slug/category"):
        response = await client.get("/api/v1" + path, headers={"X-Language": "uz"})
        assert_headers(response)
        data = response.json()
        data = data["items"][0] if "items" in data else data
        assert set(data) == CATEGORY_KEYS
        assert data["name"] == data["name_uz"] == "UZ category"
        assert data["description"] == "RU category description"
        assert data["description_uz"] == ""
        assert data["image_url"] == media
        categories.append(data)
    assert categories[0] == categories[1] == categories[2]
    for path in ("/banners", "/banners/1"):
        response = await client.get("/api/v1" + path)
        assert_headers(response, localized=False)
        data = response.json()
        data = data["items"][0] if "items" in data else data
        assert set(data) == BANNER_KEYS
        assert data["image_url"] == media
        assert data["title_uz"] == "UZ banner"


@pytest.mark.asyncio
async def test_invalid_pagination_and_not_found_cache(client):
    for path in (
        "/products/999",
        "/products/slug/missing",
        "/categories/999",
        "/categories/slug/missing",
        "/banners/999",
    ):
        response = await client.get("/api/v1" + path)
        assert response.status_code == 404
        assert_headers(response, localized=not path.startswith("/banners"))
    for path in ("/products", "/categories"):
        for query in ("page=0", "size=0", "size=101", "size=L"):
            assert (await client.get("/api/v1" + path + "?" + query)).status_code == 422
    for query in ("skip=-1", "limit=0", "limit=101"):
        assert (await client.get("/api/v1/banners?" + query)).status_code == 422
    assert catalogue_headers(
        {"vary": "Origin, accept-language", "Other": "kept"}, localized=True
    ) == {
        "Vary": "Origin, accept-language, X-Language",
        "Other": "kept",
        "Cache-Control": "no-store",
    }


@pytest.mark.asyncio
async def test_migrated_fresh_membership_and_projection(scratch, monkeypatch):
    await asyncio.to_thread(scratch.migrate, target="head")
    scratch.assert_native()
    engine = create_async_engine(scratch.url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", maker)
    try:
        async with maker() as session:
            session.add(
                Category(
                    id=1, name="Native", name_uz="Native UZ", slug="native", is_purchasable=False
                )
            )
            await session.commit()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://s5.test"
        ) as client:
            assert await traverse(client, "/api/v1/products") == []
            # Independent committed writer; no prior product ID is used for the new traversal.
            async with maker() as session:
                session.add(
                    Product(
                        name="New RU",
                        name_uz="New UZ",
                        slug="new",
                        price=Decimal("30.25"),
                        category_id=1,
                        is_sale=True,
                        sale_price=Decimal("20.25"),
                    )
                )
                await session.commit()
            fresh = await client.get("/api/v1/products?on_sale=true", headers={"X-Language": "uz"})
            assert_headers(fresh)
            data = fresh.json()["items"]
            assert len(data) == 1
            assert data[0]["name"] == "New UZ"
            assert set(data[0]) == PRODUCT_KEYS
            assert data[0]["is_purchasable"] is False
            assert data[0]["sale_price"] == "20.25"
            assert (
                await client.get("/api/v1/products/slug/new", headers={"X-Language": "uz"})
            ).json() == data[0]
            assert (
                await client.get(f"/api/v1/products/{data[0]['id']}", headers={"X-Language": "uz"})
            ).json() == data[0]
            async with maker() as session:
                product = await session.get(Product, data[0]["id"])
                product.is_active = False
                product.description = "Edited value"
                await session.commit()
            assert (
                await traverse(
                    client, "/api/v1/products", params={"on_sale": "true", "active_only": "false"}
                )
                == []
            )
            assert (await client.get("/api/v1/products/slug/new")).json()[
                "description"
            ] == "Edited value"
            category = await client.get(
                "/api/v1/categories/slug/native", headers={"X-Language": "uz"}
            )
            assert category.json()["name"] == "Native UZ"
    finally:
        await engine.dispose()
