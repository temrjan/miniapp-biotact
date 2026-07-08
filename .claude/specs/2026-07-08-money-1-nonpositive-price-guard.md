# money-1 — guard на неположительную цену в расчёте кэшбэка (аудит MINOR)

**Дата:** 2026-07-08 · **Статус:** Гейт-1 ✅ APPROVED-for-start (Ревьюер, 0 БЛОКЕР / 1 МИНОР; 4 вопроса Инженера verified по коду, 503-путь VERIFIED). МИНОР-1 (тест −5.50 обязателен) внесён ниже text-only → coding.
**Источник:** предзапусковый аудит 2026-07-03, money-1 (`.claude/reports/2026-07-03-prelaunch-audit.md`;
план долгов `...-audit-followups-plan-and-handoff.md` §4). Кресло: Claude=Инженер.

## 1. Проблема

`get_unit_price` (`src/loyalty/pricing.py`) — денежная точка: возвращает базу кэшбэка = `products.price`.
Сейчас три исхода: `price is None` → `UnknownProductError`, дробная → `FractionalPriceError`, иначе
`return int(price)`. **Нулевая и отрицательная цена не проверяются:**
- `price = 0` → `0 != 0.to_integral_value()` = False (не дробная) → `return 0`. Активация проходит **200 OK**,
  QR **сгорает навсегда** (одноразовый), кэшбэк+welcome = **0 баллов**, ни ошибки, ни алярма. Молчаливая потеря.
- `price < 0` → `return int(price)` (напр. −66500). Отрицательный `amount` **коммитится в append-only
  `point_ledger`** (баланс портится), затем `ActivateResponse` (`points_awarded: Field(ge=0)`) роняет
  ответ в **500** — уже после коммита. Клиент видит «что-то пошло не так», баланс битый, QR сожжён,
  чинить = компенсирующей записью вручную.

Плюс докстринг `get_unit_price` утверждает «**never returns 0**» — при `price = 0` это **ложь**.

## 2. Достижимость (почему MINOR, не блокер)

Через админку/API цену `≤ 0` не создать — `products.price` валидируется `Field(gt=0)`
(`src/products/schemas.py:17,68`). Правило Капитана 2026-07-08: **источник истины цены/ИКПУ = только
админка**. Остаточный путь (вне скоупа этого guard): прямой `psql`/бэкап/будущий скрипт мимо Pydantic.
Guard в расчёте — защита денежной ТОЧКИ ПОТРЕБЛЕНИЯ (не входа): если битая цена ЛЮБЫМ путём оказалась в
колонке, деньги не считаются криво. **DB `CHECK(price>0)` — НЕ делаем** (снят по правилу «только админка»;
факт-переключатель: массовый импорт/restore мимо админки ⇒ вернуть CHECK на стол).

## 3. Решение (предрешено)

**(A) РЕКОМЕНДУЮ** — новый подкласс `NonPositivePriceError(PricingError)` (симметрично
`FractionalPriceError`) + guard в `get_unit_price` порядком `None → ≤0 → fractional → return`:
```python
if price is None:
    raise UnknownProductError(product_id)
if price <= 0:
    raise NonPositivePriceError(product_id, price)
if price != price.to_integral_value():
    raise FractionalPriceError(product_id, price)
return int(price)
```
- `≤ 0` проверяется ДО дробной: отрицательная цена — фундаментально «не деньги», а не «дробная»
  (напр. −5.50 → `NonPositivePriceError`, не `FractionalPriceError`).
- `NonPositivePriceError` наследует `PricingError` ⇒ роутер (`router.py`, `except PricingError`) **уже**
  маппит её в **503 + громкий лог + откат claim'а транзакцией** — тот же безопасный путь, что у двух
  существующих ошибок. Роутер НЕ меняется.
- Докстринг `get_unit_price` и класс-докстринг `PricingError` — уточнить: перечислить «non-positive»
  и убрать/исправить ложное «never returns 0» (после guard 0 → raise, а не return 0 — станет правдой,
  но формулировку делаю явной).

**(B) ОТВЕРГНУТО** — расширить `UnknownProductError` на `price ≤ 0`: семантически неверно (цена ЕСТЬ,
но невалидна ≠ «продукт неизвестен/без цены»); ломает точность ops-лога.

**Само-атака:** «guard мёртв — админка уже не пускает ≤0». Да, вход закрыт; guard защищает не вход, а
момент выдачи денег от не-админских путей (см. §2) + чинит ложь докстринга. Цена ≈ 1 строка + класс,
0 миграций, 0 изменений роутера/схем. **Цена ошибки бездействия:** низкая вероятность × болезненный
исход (сожжённый QR + молчаливый 0 / битый баланс + 500). **Факт-переключатель:** см. §2 (CHECK).

## 4. Скоуп

1 файл прод-кода: `src/loyalty/pricing.py` (+класс `NonPositivePriceError`, +guard, докстринги).
Тесты: `tests/test_loyalty_ledger.py` (+ кейсы). Роутер/схемы/модели/миграции — НЕ трогаем.

## 5. План тестов / DoD (правило №0: red→green)

- [ ] `test_get_unit_price_rejects_zero` — `_make_product(price="0")` → `pytest.raises(NonPositivePriceError)`.
      **RED до фикса** (сейчас `get_unit_price` возвращает 0, не бросает).
- [ ] `test_get_unit_price_rejects_negative` — `price="-100"` → `raises(NonPositivePriceError)`. RED до фикса.
- [ ] **(ОБЯЗАТЕЛЕН, МИНОР-1)** отрицательная+дробная `price="-5.50"` → `NonPositivePriceError` (НЕ `Fractional`).
      Единственный тест, пиннящий design-инвариант «≤0 перед fractional»: без него будущая перестановка
      двух guard'ов классифицирует −5.50 как `Fractional` и ни один тест не покраснеет.
- [ ] `NonPositivePriceError.product_id` доступен (ops-лог), как у sibling-ошибок.
- [ ] Существующие pricing-тесты (fractional/unknown/known) — зелёные без правок.
- [ ] Полный локальный CI api (`ruff`/`ruff format --check`/`mypy --strict`/`pytest`) на parity-PG (`biotact:biotact_ci`, БД `biotact_test_db`).
- [ ] Отчёт → Гейт-2 отдельным Ревьюером свежим контекстом (`/python-review` + при желании `/security-review` — money-path).

## 6. Не в скоупе
DB `CHECK(price>0)` (снят); прочий бэклог аудита (security-1/4, lock-reorder) — свои задачи.
