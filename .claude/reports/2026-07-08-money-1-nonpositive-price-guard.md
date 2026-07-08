# Отчёт Инженера — money-1: guard на неположительную цену (аудит MINOR)

**Дата:** 2026-07-08 · **Кресло:** Claude=Инженер · **Спека:** `.claude/specs/2026-07-08-money-1-nonpositive-price-guard.md` (Гейт-1 ✅ APPROVED-for-start, Ревьюер, 4 вопроса verified по коду, МИНОР-1 внесён).
**Состояние:** код готов, гейты зелёные, red→green доказан → ожидает Гейт-2 (Ревьюер свежим контекстом; `/python-review` + опц. `/security-review` — money-path).

## Сделано — ровно по спеке (решение A), 2 файла

1. **`src/loyalty/pricing.py`** (+27/−4):
   - Новый `NonPositivePriceError(PricingError)` — **одиночное** наследование (в отличие от `FractionalPriceError(PricingError, ValueError)`; `ValueError` там только ради PR-2.1-тестов, здесь не нужен — Ревьюер подтвердил).
   - Guard в `get_unit_price` порядком **`None → ≤0 → fractional → return`**:
     ```python
     if price <= 0:
         raise NonPositivePriceError(product_id, price)
     ```
     `≤0` ДО fractional: отрицательная цена фундаментально «не деньги» (−5.50 → `NonPositive`, не `Fractional`).
   - Докстринги обновлены: `get_unit_price` (перечислен non-positive; ложное «never returns 0» → «Never returns a non-positive or fractional value») + класс-докстринг `PricingError` (добавлен non-positive-случай).
   - **Роутер/схемы/модели/миграции НЕ тронуты.** `NonPositivePriceError` наследует `PricingError` ⇒ `router.py` `except PricingError` уже даёт **503 + откат claim'а транзакцией + лог `product_id`** — тот же безопасный путь. Фикс строго сильнее: `raise` теперь ДО любой записи в ledger.

2. **`tests/test_loyalty_ledger.py`** (+31/−1): импорт `NonPositivePriceError` + 3 теста.

## red → green (правило №0)

Мутация: backup → удалил `if price <= 0` guard → прогон 3 новых тестов:
- **RED (без фикса):** `3 failed` —
  `rejects_zero` (возвращал `int(0)=0`, не бросал), `rejects_negative` (возвращал `-100`),
  `negative_fractional_is_nonpositive_not_fractional` (−5.50 уходил в `FractionalPriceError` ≠ ожидаемый `NonPositive`).
- restore (файл идентичен backup) → **GREEN (с фиксом):** `3 passed`.

⇒ 3-й тест — единственный, пиннящий инвариант «≤0 перед fractional» (МИНОР-1): при перестановке guard'ов краснеет.

## Локальные CI-гейты (api-джоб, команды точь-в-точь как ci.yml)

- `ruff check src/` → **All checks passed**
- `ruff format --check src/` → **75 files already formatted**
- `mypy src/ --strict --ignore-missing-imports` → **Success, no issues (75 files)** (`Decimal <= 0` типизируется чисто)
- `pytest tests/` → **158 passed** (было 155, +3; те же 3 пред-существующих Pydantic-deprecation warning, не из дифа)
- `ruff check tests/test_loyalty_ledger.py` → чисто (промежуточный F401 из хука снят вторым Edit — импорт используется 3 тестами)

Джобы `bot`/`frontend`/`admin`/`migration-parity` дифом не затронуты (только `src/loyalty/pricing.py` + тест).

## Security-relevant surface (для Ревьюера)

- Money-path: guard срабатывает ДО `db.add` ledger-строки, в пределах `activate_code`-транзакции (`db.begin()`) → откат claim'а, ноль частичной записи. 503 (не 500), без утечки.
- Достижимость `price≤0` через админку/API закрыта (`Field(gt=0)`), правило Капитана «источник истины = только админка». Guard — защита денежной точки от out-of-band-цены; **DB `CHECK(price>0)` намеренно НЕ добавлен** (снят по правилу; факт-переключатель в спеке §2).
- Изменений сети/эндпоинтов/схем ответа нет.

## Не сделано / вне скоупа
DB `CHECK(price>0)`; прочий бэклог аудита (security-1/4, lock-reorder, common.py:300) — свои задачи.

## Как воспроизвести (для Ревьюера)
```bash
podman run -d --name pg -e POSTGRES_USER=biotact -e POSTGRES_PASSWORD=biotact_ci \
  -e POSTGRES_DB=biotact_parity -p 5432:5432 docker.io/library/postgres:17
# psql -c "CREATE DATABASE biotact_test_db;"  (tests build schema via create_all)
cd packages/api; export DATABASE_URL=postgresql+asyncpg://biotact:biotact_ci@localhost:5432/biotact_parity
.venv/bin/pytest tests/test_loyalty_ledger.py -k "zero or negative or nonpositive"
# mutation: delete the `if price <= 0` block in get_unit_price → the 3 tests go red.
```
