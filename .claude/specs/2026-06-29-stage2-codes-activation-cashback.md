# Biotact Club — Этап 2: Коды + активация + кэшбэк

> Опирается на план ФИНАЛ v4 `.claude/specs/2026-06-25-biotact-club-plan.md` (§3-8) и закрытый Этап 1 (`consent`/`customer_profile`/`customer_event` на проде). Кресло: соло-инженер + отдельный Ревьюер (связь через Капитана). Метод: спека (этот файл, Gate-1) → серия мелких PR (implement → self-review → /python-review → Ревьюер → merge → verify).
> ⚠️ `merge→main = деплой на прод`. Все schema-PR проходят Deploy gate. **#34 (fail-rollback `deploy.yml`) закрыть ДО PR-2.1.**
> Источник истины DISCOVER — раунды Q1-Q6 этой сессии (2026-06-29). Все решения ниже — зафиксированные, не открытые.

## Goal

Заложить **денежный фундамент**: уникальные QR-коды (несущие SKU) + append-only `point_ledger` + эндпоинт активации, который идемпотентно начисляет кэшбэк (5% × цена SKU, `welcome ×1.5` на первую активацию) и инкрементит `lifetime_activations`. Всё под `loyalty_enabled=false` (тёмная инфраструктура). Пользователю ничего не видно; flip статуса/8%/+5000 — Этап 3; вход через бота — Этап 4; мастер-флаг включается **в конце Этапа 4**.

## Approach — выбран + почему

**Инкрементальная денежная модель на проводках (ledger-first).** Баланс не хранится колонкой — `balance = SUM(point_ledger.amount)`. Каждое начисление — самодостаточная неизменяемая строка со снапшотом входов расчёта (ставка/цена/`product_id`), что даёт tamper-evident аудит независимо от деплой-археологии. Идемпотентность активации — двухуровневая: атомарный conditional-`UPDATE` claim кода (первичный гейт) + partial-`UNIQUE(qr_code_id, reason)` на леджере (DB-бэкстоп от гонок). Цена кэшбэка — `products.price` по `product_id` (PK), DB-lookup (Q3-ревизия v2: всегда актуально, аудит держит снапшот; см. §Q3). Отвергнуты: хранение баланса колонкой (дрейф), исходный код-каталог цен (дубль/дрейф vs витрина — снят ревизией), `mxik` как ключ (классификатор, не идентификатор), JSONB для money-полей (теряет типы/индексы для агрегатов).

## Components

- **`src/loyalty/pricing.py`** — `get_unit_price(session, product_id) -> int` (**DB-lookup `products.price` по `products.id` (PK)**, async, громкий `UnknownProductError`) + `compute_cashback(unit_price, rate_bps) -> int` (чистая, ROUND_HALF_UP). Единый шов денежной математики. (PR-2.1 отгрузил config-версию — тёмная; PR-2.3a переписал на DB-lookup, см. §Q3-ревизия v2.)
- **`point_ledger`** (новая таблица) — append-only проводки; `balance = SUM(amount)`.
- **`code_batches` / `qr_codes`** (новые таблицы) — партии и уникальные коды (несут SKU, статус `generated→used`).
- **Генерация партий + CSV** (CLI) — ops печатает уникальные QR.
- **`POST /qr/activate`** (новый роутер) — initData-auth, гейт `loyalty_enabled`, идемпотентная транзакция активации.
- **Balance-сервис** — `SELECT COALESCE(SUM(amount),0) WHERE user_id`.

## Решения DISCOVER (Q1-Q6 — зафиксированы)

### Граница Этап 2 ↔ Этап 3 (Q1)
- Этап 2 = **только** `cashback 5%` + `welcome ×1.5` + инкремент `lifetime_activations`. **Активация в Этапе 2 всегда по ставке newbie (500 bps).**
- Flip `newbie→biotact_friend` / 8% / +5000 — **Этап 3.**
- **Условие forward-safety (locked):** PR-2.3 строит транзакцию активации с `SELECT … FOR UPDATE` на `customer_profile` **уже сейчас**. Этап 3 добавит ветку flip **внутри существующего лока** — без рефактора транзакции.
- **Условие (locked):** flip Этапа 3 = идемпотентный **порог-чек** `lifetime_activations >= 10 AND status == 'newbie'` под локом, **НЕ** детект перехода 9→10 (робастен к счётчику за 10: бэкфилл/ре-инжест). Счётчик — **lifetime** (правка плана §12.4 «annual»).

### Тайминг флага (Q2)
- Мастер `loyalty_enabled` → `true` **в конце Этапа 4** (вход реально достижим: deeplink+телефон+мини-апп). Минимальный связный срез = вход + earn 5-8% + видимый статус-прогресс.
- Трата (Этап 6) и розыгрыш (Этап 5) — **каждый под СВОИМ суб-гейтом**, добавляется когда фича приземляется (НЕ пред-создавать пустые флаги — переинженерка), зажигается независимо.
- **Флип ≈ необратим** (ledger пишет реальную liability; откат флага ≠ разначисление). Зажигать только при ВСЕХ трёх: (1) #34 доказан; (2) PR-2.3 идемпотентность/гонки зелёные на прод-схеме; (3) связный срез вход+earn+статус живой.

### Каталог цен (Q3 — ⚠️ РЕВИЗИЯ 2026-06-29 v2, подтверждена Капитаном + Ревьюером)
**Источник цены = `products.price` (DB-lookup), ключ = `products.id` (FK), НЕ код-каталог и НЕ `mxik`.** Прод-проверка (read-only SELECT): все **13 БАД** заведены в `products` с заполненными `price` (целые UZS, 66 500…123 000). Эволюция: config-каталог (исходный Q3) → products.price по mxik (v1) → **products.price по `product_id` (v2, финал)**.

**Почему products.price (а не код-каталог):**
- **Всегда актуальная цена + единый источник**, нет дрейфа «каталог ≠ витрина». Кэшбэк = % от реально списанной Multicard суммы.
- **Аудит сохранён снапшотом Q4** — каждая earn-строка фиксирует применённый `unit_price`/`rate_bps`; смена цены админом влияет ТОЛЬКО на будущие начисления. Снимает главную причину код-каталога («liability версионируема»).
- **Governance:** смена цены = админ-правка продукта (мгновенно, без PR-гейта); прошлое защищено снапшотом. Принято Капитаном.

**🔴 Почему `product_id`, а не `mxik` (БЛОКЕР Ревьюера, принят):**
- `mxik` — **налоговая классификация, не идентификатор товара**: nullable, БЕЗ unique, БЕЗ index; два товара (капс/стик одной добавки) штатно делят ИКПУ → недетерминированный money-lookup + seq-scan на hot-path. «Уникален на вариант» — случайность нынешних 13, не гарантия.
- `products.id` (PK) — уникален, индексирован, детерминирован; **FK даёт референц-целостность даром** (нельзя сгенерить QR на несуществующий продукт — заменяет ручную валидацию); робастно к переименованиям. (`slug` — тоже unique+index, для человекочитаемого входа.)
- **База = цена мини-аппа** (`products.price`); даже если аптечная иная — считаем от мини-аппа (Капитан). `vat`=NULL не мешает: `price` = gross/уплачено.

**Механика:**
1. **Ключ:** `qr_codes.product_id` (FK→`products.id`), снапшот `point_ledger.product_id` (FK). `code_batches.product_id` (FK). Заменяют `sku String` (тёмные таблицы → миграция бесплатна).
2. `get_unit_price(session, product_id) -> int` — **async DB-lookup** `SELECT price FROM products WHERE id=:product_id` (PK, индекс); **громкий `UnknownProductError`** если продукта нет/`price` NULL (НЕ 0, НЕ 500). Numeric(12,2)→int: `int(price)` **с assert-целостностью** (raise если у `price` дробная часть — дробные деньги тихо не усечь; UZS целые).
3. `compute_cashback(unit_price, rate_bps) -> int` — **остаётся чистой** (`Decimal` `ROUND_HALF_UP`, .5→вверх; целые UZS, без float; один шов 5%/8%).
4. **CLI** `--slug <product-slug>` → резолв в `product_id` → `generate_batch(product_id)`. Партия = на один продукт.
5. **Sentinel-каталог / гард-тест / startup-валидация / SkuPrice — ОТПАДАЮТ** (цены в БД).

⚠️ PR-2.1 отгрузил `pricing.py` config + PR-2.2 — `qr_codes.sku`/`code_batches.sku`/`generate_batch(sku, sync)` — всё **ТЁМНОЕ** (нет прод-данных, никто не активирует). Рефактор `sku String → product_id FK` + `pricing.py` config→async-DB-lookup + `codes.py` (async, product_id) — без риска прода. **codes.py — в scope PR-2.3 (источник, не только тесты).**

### Форма earn-строки леджера (Q4)
- Снапшот **трёх** колонок на earn: `rate_bps` (int 500/800), `unit_price` (int UZS с НДС), **`product_id`** (FK→products, заменил `sku`; Q3-ревизия v2). **Nullable** (заполняются только на earn; spend/expire/adjust — NULL).
- Типизированные queryable-колонки, **не JSONB** (нужны агрегаты «liability по 8%», «по продукту»).
- **Невосстановимость:** `rate_bps` зависит от статуса-в-момент; `unit_price` дрейфует деплоями → оба обязаны быть на строке. `product_id` восстановим через `qr_code_id→qr_codes.product_id`, но снапшотится для самодостаточности + join-free аналитики по продукту (BIFOLAK = 79%).
- ⚠️ PR-2.1 отгрузил колонку `sku String(32)` (тёмная) → **PR-2.3 мигрирует `point_ledger.sku → product_id Integer FK→products.id`** (бесплатно, нет данных).
- **Reconciliation-инвариант (тест PR-2.1):** ∀ earn-строки `amount == compute_cashback(unit_price, rate_bps)`. Детектор tamper/дрейфа/округления.
- **Один унифицированный ledger**, `balance = SUM(amount)` по всем reason. **Без таблицы-на-reason.** `reason` — `String` + константы `LedgerReason` (НЕ PG-enum: словарь растёт по этапам — `cashback`/`welcome` сейчас, `friend_bonus`/`redeem`/`expiry`/`referral`/… позже — избегаем миграции на тип; консистентно с `customer_event.event_type`).

### Идемпотентность + гонки активации (Q5)
Одна транзакция, per-user сериализация:
```
BEGIN
  INSERT customer_profile(user_id) ON CONFLICT(user_id) DO NOTHING   -- lazy-create race-safe
  SELECT … FROM customer_profile WHERE user_id=:uid FOR UPDATE        -- замок (НЕ qr_codes, НЕ balance-wide)
  UPDATE qr_codes SET status='used', used_at=now(), activated_by=:uid
    WHERE code=:code AND status='generated' RETURNING id, sku          -- атомарный claim (сам себя лочит)
  -- claimed (1 строка): unit_price=get_unit_price(sku); rate=500;
  --   INSERT cashback-строка; если lifetime_activations==0 → INSERT welcome-строка (rate=250);
  --   lifetime_activations++; last_activation_at/last_accrual_at=now(); first_activated_sku если первая
  -- 0 строк: SELECT qr_codes WHERE code=:code →
  --   activated_by==uid → idempotent replay (пересборка ответа из ledger по qr_code_id, 200, 0 начислений)
  --   activated_by!=uid → 409 generic «код уже активирован» (без утечки кто)
  --   нет строки      → 404
COMMIT
```
- **`UNIQUE(qr_code_id, reason)` partial** (`WHERE qr_code_id IS NOT NULL`) — DB-бэкстоп: одна `cashback` + одна `welcome` на код; дубль невозможен даже при гонке двух claim (TOCTOU).
- **Замок = строка `customer_profile`** (per-user): сериализует welcome-чек + инкремент + (Этап 3) flip. Порядок локов **профиль→код** (консистентен, без дедлока).
- **Same-user replay = пересборка из ledger**, не кэш ответа (стейтлесс, ноль рассинхрона).
- **welcome ×1.5 = ОТДЕЛЬНАЯ earn-строка** (`reason='welcome'`, `rate_bps=250` = половина базовой 500, `unit_price`, `amount=compute_cashback(unit_price, 250)`) — НЕ multiplier-поле; форма `(sku, unit_price, rate_bps, amount)` обобщается на активацию И welcome, обе **reconcile-совместимы** per-row. Итог первой активации = `cashback(unit_price,500) + cashback(unit_price,250)` (= 7.5%).

### Формат кода (Q6)
- **Непрозрачный случайный код** + `sku` авторитетной колонкой на `qr_codes`. Активация берёт SKU **из колонки**, не из парсинга строки (которую можно подменить).
- Энтропия **~50-60 бит** (Crockford base32, ~10-12 симв.) — неперечислимо при «без вооружения» + влезает в Telegram-deeplink `qr_<code>` (≪ лимит 64). НЕ UUID/128-бит. Коллизии — `UNIQUE` + ретрай при генерации.

---

## PR scope — нарезка (3 PR, по одной схеме на schema-PR)

### Prerequisite (ops, Капитан): #34 — fail-rollback `deploy.yml`
Прогнать ветку провала деплоя на одноразовой БД (заведомо падающая миграция → код ресетается на `PREV_COMMIT`). **Закрыть ДО мёржа PR-2.1** (первая схема Этапа 2 = реальный деплой).

### PR-2.1 — `point_ledger` + `pricing.py` + balance-сервис
**IN:**
- `pricing.py`: `SkuPrice` каталог (13 SKU, **sentinel-плейсхолдеры** — шипятся тёмно, гард-тест в PR-2.3) + startup-валидация + `get_unit_price` (громкий `UnknownProductError`) + `compute_cashback` (`ROUND_HALF_UP`) + единицы-докстринг.
- `point_ledger` модель: `id` bigint PK, `user_id` FK CASCADE+index, `amount` BIGINT (знаковый), `reason` `String(32)` + `LedgerReason` константы, `qr_code_id` BIGINT nullable+index (плейн-колонка, **FK добавит PR-2.2** после создания `qr_codes`), `sku`/`unit_price`/`rate_bps` nullable, `created_at`. **Partial `UNIQUE(qr_code_id, reason) WHERE qr_code_id IS NOT NULL`.**
- Balance-сервис `get_balance(user_id) -> int` (`COALESCE(SUM(amount),0)`).
- Reconciliation-хелпер + unit-тесты (валидирует/отвергает синтетические строки).
- Миграция (down_rev = текущая голова `70c5db3e31f0`).

**OUT:** активации/начислений ещё нет (нет писателя в ledger); FK на `qr_codes`; коды.
**Verify:** parity (`alembic upgrade head`+`check`), up/down/up, ruff/format/mypy --strict, pytest. **Size:** M.
**Files:** `src/loyalty/models.py`, `src/loyalty/pricing.py`, `src/loyalty/service.py`, `alembic/versions/<new>.py`, `tests/test_loyalty_ledger.py`.

### PR-2.2 — `code_batches` + `qr_codes` + генерация + CSV
**IN:**
- `code_batches` модель: `id`, `sku`, `quantity`, `label` nullable, `created_at`.
- `qr_codes` модель: `id` bigint PK, `code` `String` **UNIQUE**+index, `sku` index, `batch_id` FK→`code_batches`, `status` `String` (`generated`/`used`) server_default `generated`, `used_at` nullable, `activated_by` FK→`users.id` nullable+index. (Несёт ровно то, на чём стоят claim/replay PR-2.3.)
- Генерация: `generate_batch(sku, quantity)` — opaque code (Crockford base32 ~10-12 симв.), коллизии `UNIQUE`+ретрай; CSV-экспорт (code, sku). **Валидирует `sku` против `pricing.py` на входе (fail-fast: НЕ печатать QR для SKU, который не может начислить кэшбэк — дешёвый инвариант на границе генерации).**
- CLI `scripts/generate_qr_batch.py` (ops запускает; админ-UI — позже).
- **Миграция: создать `code_batches`/`qr_codes` + добавить FK `point_ledger.qr_code_id → qr_codes(id)`.**

**OUT:** активация (эндпоинт — 2.3); армирование/вооружение; админ-UI.
**Verify:** parity, up/down/up, ruff/format/mypy, pytest (генерация уникальна, CSV-формат, коллизия-ретрай). **Size:** M/L (если >5 файлов — выделить CSV в 2.2b).
**Files:** `src/loyalty/models.py`, `src/loyalty/codes.py`, `scripts/generate_qr_batch.py`, `alembic/versions/<new>.py`, `tests/test_loyalty_codes.py`.

> **Нарезка (после Q3-ревизии v2):** ключ-рефактор раздулся → PR-2.3 разбит на **PR-2.3a (рефактор ключа+pricing, тёмный)** + **PR-2.3b (активация)**. Дисциплина мелких обратимых PR; активация не тонет в churn'е схемы.

### PR-2.3a — рефактор ключа `sku → product_id` + `pricing.py` на DB-lookup (ТЁМНЫЙ)
**IN:**
- **Миграция (одна, 3 таблицы, все тёмные → бесплатно):** `code_batches.sku String` → `product_id Integer FK→products.id`; `qr_codes.sku` → `product_id FK→products.id`; `point_ledger.sku` → `product_id FK→products.id` (ondelete `RESTRICT` на money-связях — нельзя удалить продукт с кодами/проводками).
- **`pricing.py` → DB-lookup:** `get_unit_price(session, product_id) -> int` = `SELECT price FROM products WHERE id=:product_id`, async, громкий `UnknownProductError` (нет/`price` NULL); `int(price)` **с assert-целостностью** (raise на дробной). Убрать config-каталог/`SkuPrice`/sentinel/startup-валидацию. `compute_cashback` — без изменений (чистая).
- **`codes.py` → product_id:** `generate_batch(session, product_id, quantity)` (async-валидация через `get_unit_price`, FK заменяет ручную проверку); CLI `--slug` → резолв в `product_id`.
- Обновить `tests/test_loyalty_ledger.py` + `tests/test_loyalty_codes.py` на реальный сид-продукт (id) вместо `SENTINEL_SKU`/фейков.

**OUT:** эндпоинт активации (2.3b).
**Verify:** parity, up/down/up, ruff/format/mypy, pytest. **Size:** M (тронет ~6-7 файлов из-за двух тест-файлов — механический рефактор, приемлемо).
**Files:** `models.py`, `pricing.py`, `codes.py`, `scripts/generate_qr_batch.py`, миграция, `tests/test_loyalty_ledger.py`, `tests/test_loyalty_codes.py`.

### PR-2.3b — `POST /qr/activate` + кэшбэк + welcome + гонки
**IN:**
- Транзакция активации (Q5): lazy-create профиля `ON CONFLICT DO NOTHING`, `FOR UPDATE` на профиле, атомарный claim, `compute_cashback`, INSERT ledger (cashback [+welcome], снапшот `product_id`/`unit_price`/`rate_bps`), счётчики `customer_profile` (lifetime_activations++, last_activation_at, last_accrual_at, first_activated_sku).
- **Транзакционное ограничение (locked, МИНОР-4 — `with_for_update` в репо НОВЫЙ):** вся активация в ОДНОЙ явной транзакции (`async with session.begin()`). Иначе лок снимается по-стейтментно → per-user сериализация тихо ломается. Тест обязателен.
- Роутер `POST /qr/activate` под initData-HMAC — **переиспользует `src.auth.telegram.validate_init_data` через header `X-Init-Data`** (как `orders/router.py:33-36`). **Гейт `loyalty_enabled`** (false → **404**).
- Replay (same-user → 200, 0 начислений), 409 (чужой), 404 (нет кода).
- **Продукт не найден/`price` NULL при активации (МИНОР-2b):** `get_unit_price` → `UnknownProductError` → **claim откатывается** (`qr_codes.status` остаётся `generated`, начислений нет) + чёткая ошибка клиенту + **ops-лог**. Тест.
- Race-тесты §6: идемпотентность ре-скана, гонка первой активации (concurrent → ровно одна welcome), чужой код→409, reconciliation-интеграция.

**OUT:** flip статуса/8%/+5000 (Этап 3); бот-deeplink (Этап 4); трата/розыгрыш.
- **`customer_event(QR_ACTIVATED)` — ОТКЛАДЫВАЕМ явно (МИНОР-3).** ⚠️ **Tracked: добавить запись `QR_ACTIVATED` ДО включения мастер-флага (конец Этапа 4)** — иначе живые активации без таймлайн-события.
**Verify:** все гейты + гонки на прод-схеме; мастер-флаг `false`; **`/security-review`** (мутирует деньги + auth-граница). **Size:** L.
**Files:** `src/loyalty/service.py` (или `activation.py`), `src/loyalty/router.py`, `src/loyalty/schemas.py`, `src/main.py`, `tests/test_loyalty_activation.py`.

## Assumptions

- **Ставлю на:** `customer_profile` уже несёт `lifetime_activations`/`last_activation_at`/`last_accrual_at`/`first_activated_sku`/`status` (PR-1.2 на проде) → Этап 2 пишет в существующие колонки, **новой миграции `customer_profile` НЕ нужно**.
- **Может убить:** продукт по `mxik` отсутствует/`price` NULL на проде → активация падает (`UnknownProductError`). Митигировано: прод-проверка показала 13/13 БАД с `mxik`+`price`; новые SKU надо завести в `products` до печати QR.
- **Сознательно игнорирую (ок, потому что):** армирование кодов (риск утёкшего кода принят, вернём при абъюзе); админ-UI генерации (CLI достаточно для тёмной фазы); flip-логику Этапа 3 (но лок ставлю forward-safe сейчас).

## Acceptance criteria — «PR Этапа 2 готов, когда…»

- [ ] #34 закрыт до PR-2.1; каждый schema-PR прошёл Deploy gate (happy-path на проде, без отката).
- [ ] `point_ledger` создана; `balance = SUM(amount)` (пустой → 0); partial-`UNIQUE(qr_code_id, reason)` активна.
- [ ] `pricing.py`: `get_unit_price(session, mxik)` тянет `products.price` по `mxik`; незаведённый/`price`-NULL → `UnknownProductError` (не 0, не 500); `compute_cashback` целые UZS, ROUND_HALF_UP.
- [ ] `code_batches`/`qr_codes` создаются; коды уникальны (`UNIQUE`+ретрай), opaque ~50-60 бит; CSV экспортируется; FK `point_ledger.qr_code_id` подключён. **`generate_batch` отвергает непрайснутый SKU (fail-fast).**
- [ ] `POST /qr/activate`: ставка 5% + welcome ×1.5 на первую; идемпотентный ре-скан (same-user replay 200/0-начислений, чужой 409, нет кода 404); `lifetime_activations` инкрементится под локом. **Вся активация в одной явной транзакции (`async with session.begin()`); непрайснутый SKU при активации → claim откатывается (code остаётся `generated`, 0 начислений) + ops-лог.**
- [ ] **Гонки (§6):** конкурентная первая активация → ровно одна welcome; двойной claim → один набор начислений (бэкстоп `UNIQUE`).
- [ ] **Reconciliation:** ∀ earn-строки `amount == compute_cashback(unit_price, rate_bps)`.
- [ ] Все миграции up/down; колонки `users` не трогаются. Мастер `loyalty_enabled` остаётся `false` (эндпоинт тёмный).
- [ ] 4 CI-джоба + Migration parity зелёные; mypy --strict; ruff.

## Definition of Done

- Смёржено по одному PR в `main`; ветки удалены. Каждый schema-PR прошёл Deploy gate.
- Новый Python строго типизирован (mypy --strict), ruff. Документация модулей (единицы, ROUND_HALF_UP) на месте.
- Лоялти-функциональность **не видна пользователю** (мастер-флаг false; бот-вход — Этап 4).
- **Цикл каждого код-PR (Codex, обязательно — [[feedback-load-language-skills]] + CLAUDE.md):** `/python` (персона) + `/codex` (стандарт) **ДО кода** → реализация → само-ревью → `/python-review` → **`/security-review` на PR-2.3** (мутирует деньги + auth-граница, даже при reuse initData) → Ревьюер (через Капитана) → merge → verify. Весь стек Этапа 2 — Python (FastAPI/SQLAlchemy/Alembic) → достаточно Python-цепочки.

## Test plan

- **PR-2.1:** pricing unit-тесты (округление half-up в пользу клиента, `UnknownProductError`, startup-валидация, sentinel-гард); ledger create_all + balance (пустой→0, знаковая сумма); reconciliation-хелпер на синтетике; alembic up/down + parity.
- **PR-2.2:** генерация уникальна (коллизия→ретрай), CSV-формат, opaque-энтропия; alembic up/down (+FK) + parity.
- **PR-2.3:** активация happy-path (5%, фиксация SKU, счётчик++); welcome ×1.5 на первой, отсутствует на второй; идемпотентность ре-скана (same-user replay, чужой 409, 404); **гонки** (concurrent first activation → одна welcome; двойной claim → один набор) через параллельные сессии/`asyncio.gather` на postgres:17; reconciliation-интеграция; гейт `loyalty_enabled` (false → закрыт).

## Task breakdown

Порядок форсирован зависимостью схемы (ledger → codes → activate); высший риск — #34 (ops, до всего) и PR-2.3 (гонки/деньги, последний т.к. зависит от 2.1+2.2). Чекпоинт после каждого PR (Deploy gate + Ревьюер).

1. **#34 fail-rollback** (ops, Капитан) — before PR-2.1.
2. **PR-2.1** ledger+pricing+balance — M, ≤5 файлов, high-risk (форма строки вечная).
3. **PR-2.2** codes+генерация+CSV+FK — M/L, ≤5 файлов (split если >5).
4. **PR-2.3** activate+welcome+гонки — L, ≤5 файлов, highest-risk (деньги/гонки).

## References

- Леджер append-only / баланс=SUM: [pgledger](https://github.com/pgr0ss/pgledger), [django-hordak](https://github.com/adamcharnock/django-hordak), [shopback-cashback-ledger](https://github.com/RendaZhang/shopback-cashback-ledger) — деньги только проводками, баланс производный.
- Идемпотентность/гонки: стандартный Postgres — `UNIQUE`-констрейнт + атомарный `UPDATE … WHERE status=…` + `SELECT … FOR UPDATE` (план §6).
- Deeplink: [Telegram WebApps](https://core.telegram.org/bots/webapps) — лимит deeplink-параметра 64 симв.

## Open questions

1. **Розыгрыш = launch-blocker? (стратегия Капитана, НЕ блокирует PR Этапа 2).** Инженерный дефолт — мастер-флип в конце Этапа 4 (розыгрыш под своим суб-гейтом на Этапе 5 независимо). Если «розыгрыш в v1» → флип ждёт конца Этапа 5, 4→5 сшиваем плотно. Решение — до приземления Этапа 4.
2. ~~Реальные 13 цен SKU~~ — **РЕШЕНО (Q3-ревизия 2026-06-29):** цена не вводится — тянется из `products.price` по `mxik`. Прод подтверждён: 13 БАД заведены с `mxik`+`price`.
