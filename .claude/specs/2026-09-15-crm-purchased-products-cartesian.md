# CRM purchased_products: cartesian product orders × order_items

```
STATUS: УТВЕРЖДЕНА · ревью: чисто (2026-09-15) · go: Капитан (2026-09-15) · sha256: f5a4dc26037e77c86e31ad8824ec025d3ef065754ddb895e76f6285b1b932a84
```

## Вид задачи

**Багфикс, короткий путь** (план+спека одним файлом, один раунд у Ревьюера: верификация руками + /check).

## Честное признание о порядке

Код в `packages/api/src/crm/router.py` был написан **до** этой спеки — вчера, в разгар разбора продового алерта, до того как Капитан включил воркфлоу. Дифф не закоммичен, регрессионного теста ещё нет. Прошу Ревьюера проверить уже существующий дифф против этой спеки — порядок нарушен, содержимое прошу проверить как есть.

## Пересказ (что трону / чего НЕ трону)

Трону: два запроса в `packages/api/src/crm/router.py` (ручки `GET /api/v1/crm/customers` и `GET /api/v1/crm/customer/{telegram_id}`), один регрессионный тест, одна строка докстринга тестового файла.

НЕ трону: схемы ответа (`CustomerSchema.purchased_products` — список имён, контракт не меняется), статус-фильтр `delivered/shipped/processing`, лояльность, фронт, админку, миграции, продовую БД. Фикстуры в conftest НЕ выношу (третий файл, вне скоупа — только с «да» Капитана).

## Репродукция (подтверждено эмпирически)

Repro-скрипт на SQLite (ORM, те же модели, SQLAlchemy 2.0.45 — версия совпадает с продом):

```
select(Order.user_id, Order.items).where(Order.user_id.in_([7,8]), Order.status.in_(...))
→ SAWarning: cartesian product between FROM element(s) "order_items" and FROM element "orders"
→ строки: user_id=7  row.items=True (bool!), 4 строки на 2 заказа × 2 позиции
```

То есть вторая колонка приходит **булевой**, а код-хозяин ждёт итерируемое и проверяет `isinstance(item, dict)` — при любом заказе в статусе фильтра ручка падает с TypeError (или возвращает пусто, если строк нет). Прод: SAWarning наблюдался в `api-error.log` после рестарта 14.09; ручка вызывалась крайне редко (1 запрос за сентябрь), 200 — потому что строк в выборке не было.

## Поиск готовых решений — исход

| Кандидат | Откуда | Мерка силы | Вердикт |
|---|---|---|---|
| `analytics/service.py:133-138` — `select(...OrderItem...).join(Order, OrderItem.order_id == Order.id)` | внутрь | работает в живом коде проекта | **адаптировать** |
| `orders/service.py:158-162` — `select(Order).options(selectinload(Order.items)...)` | внутрь | работает, но решает «заказ целиком», а не «имена по многим юзерам» | не подошёл (N+1 для списка) |
| внешние источники | наружу | — | не искал: тривиальный ORM-inner-join, живой образец есть внутри |

## Подход

Оба запроса переписаны на явный join по `OrderItem.order_id == Order.id`, выборка колонки `OrderItem.product_name` вместо relationship. Сбор `purchased_products` — set имён через `setdefault` (список) / set comprehension (одиночная ручка). Контракт ответа не меняется.

## Где это живёт

| Модуль | Что меняется | Почему владелец именно он |
|---|---|---|
| `packages/api/src/crm/router.py` | два запроса + импорт OrderItem | баг здесь, владелец запроса — роутер |
| `packages/api/tests/test_crm_customers_loyalty.py` | + регрессионный тест, строка докстринга | единственный тестовый файл CRM-роутера, все нужные фикстуры уже там (решено грилем, Капитан передал ответ Ревьюера 15.09) |

**Не трогаю:** всё остальное (см. пересказ).

## Допущения

1. Тестовая БД — локальный podman-контейнер `biotact-pg` (localhost:5432, `biotact_test_db`, пароль `biotact_dev`). Пароль в `packages/api/.env` от него отличается → гейты прогоняются с `DATABASE_URL=...biotact_dev...` в env (без правки .env).
2. Prod БД не трогаю ни на одном шаге; верификация на проде после деплоя — только HTTP-вызов ручки и чтение логов.
3. Деплой = push в main + `git pull` + `pm2 restart api` на сервере (SERVER_README.md), выполняется только после «чисто» и «го» Капитана (merge = deploy).

## Скоуп PR

`fix(crm): purchased_products — явный join вместо cartesian product` — два запроса, тест, докстринг. Явно не входит: вынесение фикстур, PydanticDeprecation warnings, фронт, правка .env, деплой (отдельным «го»).

## Команды гейтов

Из `packages/api/` (venv `.venv`, контейнер `biotact-pg` поднят):

```bash
export DATABASE_URL="postgresql+asyncpg://biotact:biotact_dev@localhost:5432/biotact_test_db"
.venv/bin/python -m ruff check src/ tests/
.venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/ --strict
.venv/bin/python -m pytest tests/ -q
```

## Критерии приёмки

- [ ] Регрессионный тест падает на коде ДО фикса (красная фаза) и зелёный ПОСЛЕ
- [ ] Все 4 гейта зелёные, вывод приложен к отчёту Гейта-2
- [ ] В ответе обеих ручек `purchased_products` = имена товаров из delivered/shipped/processing заказов; cancelled/чужие юзеры — исключены

## Definition of Done

Merged в main после «чисто» + «го» Капитана; ветка удалена; тесты+линт зелёные после последней правки; докстринг теста приведён в соответствие.

## Тест-план

Один тест-функцией (возможно двумя: список + одиночная ручка) в `test_crm_customers_loyalty.py`:
1. Seed: admin + 2 клиента; у клиента A — delivered-заказ с 2 позициями + cancelled-заказ с позицией; у клиента B — delivered-заказ с 1 позицией.
2. `GET /crm/customers` → A: purchased_products = {оба имени из delivered, без cancelled}; B: {своё имя}; нет перекрёста между юзерами.
3. `GET /crm/customer/{telegram_id A}` → те же имена.
4. Клиент без заказов → `purchased_products == []`.
5. Красная фаза: тест против `git stash`нутого фикса падает (SAWarning/пусто/ошибка).

## Открытые вопросы

1. Красная фаза: старый код на тестовых данных падает TypeError'ом (не «молча пусто») — если Ревьюер сочтёт проверку красной фазы достаточной формально, ок; если попросит продемонстрировать — приложу вывод отдельно.
