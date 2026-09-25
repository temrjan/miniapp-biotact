"""Real PostgreSQL races: merged validation alone cannot protect concurrent PATCHes."""

import asyncio
from contextlib import suppress
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.database import get_db
from src.main import app
from src.products import service
from src.products.models import Product
from src.products.schemas import ProductUpdate
from tests.conftest import TEST_DATABASE_URL


async def _wait_for_lock_waiter(observer, name):
    async with asyncio.timeout(5):
        while True:
            await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await observer.scalar(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE application_name=:name AND wait_event_type='Lock'"
            ), {'name': name})
            if waiting:
                return
            await asyncio.sleep(0.01)


@pytest.mark.parametrize('via_http', [False, True])
async def test_concurrent_sale_patch_rejects_invalid_final_row(test_db, admin_headers, via_http):
    product = Product(name='Race', slug='race', price=Decimal('200'), is_sale=True, sale_price=Decimal('150'))
    test_db.add(product)
    await test_db.commit()
    pid = product.id
    name = 'test_sale_race_api' if via_http else 'test_sale_race_b'
    engine_a = create_async_engine(TEST_DATABASE_URL, connect_args={'server_settings': {'application_name': 'test_sale_race_a'}})
    engine_b = create_async_engine(TEST_DATABASE_URL, connect_args={'server_settings': {'application_name': name}})
    task = None
    try:
        async with AsyncSession(engine_a, expire_on_commit=False) as a, AsyncSession(engine_b, expire_on_commit=False) as b:
            await service.update_product(a, pid, ProductUpdate(price=Decimal('160')))
            async def patch():
                if not via_http:
                    return await service.update_product(b, pid, ProductUpdate(sale_price=Decimal('180')))
                async def request_db():
                    try:
                        yield b
                        await b.commit()
                    except Exception:
                        await b.rollback()
                        raise
                app.dependency_overrides[get_db] = request_db
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    return await client.patch(f'/api/v1/products/{pid}', headers=admin_headers, json={'sale_price': '180'})
            task = asyncio.create_task(patch())
            try:
                await _wait_for_lock_waiter(test_db, name)
                await a.commit()
                if via_http:
                    response = await asyncio.wait_for(task, 5)
                    assert response.status_code == 422
                    assert response.json()['detail'] == {'code': 'sale_invariant_violation'}
                else:
                    with pytest.raises(service.SaleInvariantError):
                        await asyncio.wait_for(task, 5)
                await b.rollback()
                row = (await b.execute(select(Product).where(Product.id == pid))).scalar_one()
                assert (row.price, row.sale_price) == (Decimal('160'), Decimal('150'))
                valid = await service.update_product(b, pid, ProductUpdate(sale_price=Decimal('140')))
                assert valid.sale_price == Decimal('140')
                await b.commit()
            finally:
                await a.rollback()
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine_a.dispose()
        await engine_b.dispose()
