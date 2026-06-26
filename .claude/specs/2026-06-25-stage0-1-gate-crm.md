# Biotact Club — Детальная спека: Этап 0 (Гейт) + Этап 1 (CRM-фундамент)

> Опирается на план ФИНАЛ `.claude/specs/2026-06-25-biotact-club-plan.md`. Кресло: соло-инженер. Метод: этап → спека (этот файл, Gate-1) → серия мелких PR (implement → self-review → /check → merge → verify). **Код не начинаем до одобрения этой спеки.**
> ⚠️ `merge→main = деплой на прод`. Все schema-PR проходят Deploy gate (Этап 0).

## Goal

Заложить **безопасный фундамент**: (0) гейт против рассинхрона модель↔миграция + dev-контур + фича-флаг; (1) три CRM-таблицы (`consent`, `customer_profile`, `customer_event`) рядом с `users`. Пользовательской функциональности ещё нет — только данные и страховка. Всё под флагом `loyalty_enabled=false`.

---

## ЭТАП 0 — Гейт + dev-контур

### PR-0.1 — Базовая линия миграций + CI-паритет (БЛОКЕР всех схем)

**Проблема (проверено):** `alembic/env.py` импортирует не все модели — **`banners.Banner`, `banners.Settings`, `broadcasts.Broadcast` отсутствуют**. `target_metadata = Base.metadata` (env.py:28) поэтому неполон → `alembic check` покажет «drop banners/broadcasts/settings» (ложный дрейф). Колоночный дрейф **VERIFIED** (см. Addendum): `products.advantages/video_url`, `categories.is_purchasable`, `images`-тип, 2 GIN-индекса.

**Scope:**
1. Импортировать **все** модели в `alembic/env.py` (добавить `Banner`, `Settings`, `Broadcast`; на будущее — единый `src/models_registry.py`, который импортит все модели, и env.py + `main.py` импортят его — чтобы и `alembic check`, и `Base.metadata.create_all` видели всё).
2. Локально прогнать `alembic upgrade head` на пустом PG → `alembic check`. Погасить **остаточный дрейф** до «No new upgrade operations detected»: либо поправить модели под БД, либо одна **sync-миграция** (down_revision = `e1f2a3b4c5d6`). Не плодить функциональных изменений — только выравнивание.
3. **Новый CI-джоб `Migration parity`** в `ci.yml` (отдельный, со своим `postgres:17`): `alembic upgrade head` → `alembic check` (грепнуть «No new upgrade operations detected», иначе exit 1). Обязателен к прохождению.

**Verify:** новый CI-джоб зелёный; локально `alembic check` чист; `pytest` (create_all) не падает.
**Files:** `alembic/env.py`, (опц.) `src/models_registry.py`, `.github/workflows/ci.yml`, reconcile-миграция, `src/categories/models.py` (server_default), `src/products/models.py` (GIN-индексы в модели).
**Size:** M. **Риск:** средний (трогаем общий env.py — не функционально).

#### Addendum (Ревьюер, read-only prod biotact_db PG16 — VERIFIED). Решения — `.claude/decisions/2026-06-25-pr-0.1-reconcile.md`.
- **🔴 Что гейт ДОКАЗЫВАЕТ и что НЕТ (finding #1):** parity-джоб проверяет **цепочка↔модель на ЧИСТОЙ БД**, а НЕ **прод↔модель**. «Зелёный гейт ≠ прод соответствует модели». Прод = миграции + ручные правки; остаточные расхождения (nullability is_purchasable, 2 GIN-индекса, images-уже-jsonb) гейту невидимы → корень лечит только squash/baseline (см. follow-up).
- **К1 broadcasts:** забытый импорт → в registry/env.py.
- **К2 (model-ahead):** каст `images` TEXT→JSONB **безопасен** (прод уже jsonb, 0 битого JSON; реальный каст только на пустой parity-БД). reconcile-миграция: `ADD COLUMN IF NOT EXISTS` для `advantages`/`video_url`; **`is_purchasable` — вариант A′:** в модель (`categories/models.py:29`) добавить `server_default=text("true")` (остаётся NOT NULL `Mapped[bool]`), reconcile добавляет `NOT NULL DEFAULT true`; **GIN-индексы объявить в модели** + `CREATE INDEX IF NOT EXISTS`. **down = no-op** (`pass` + коммент) — симметричный DROP снёс бы живые `images`/`advantages` на проде.
- **🔴 К3 (сироты ЖИВЫЕ, не мёртвые — моя посылка была ложной):** `referrers`(60)/`referral_invites`(3)/`users.referral_code`(121)/`orders.referral_code`(6) **используются отдельным прод-сервисом `ref-bot`** (PM2, 3 мес uptime, своя модель, та же БД). **ДРОП ЗАПРЕЩЁН** (сломает живой сервис + потеря данных). → `include_object`-хук исключает их **по точным именам** + коммент «owned by ref-bot service, shared DB — НЕ автоген».
- **Follow-up (tracked, ДО Stage 1):** squash/baseline-миграция под **реальную прод-схему** + `alembic stamp` на проде — единственное, что лечит корень (прод≠цепочка). Отдельный PR/решение (stamp на живом проде = свой риск).
- **ESCALATE Капитану (вне PR-0.1):** (#5) владение схемой реферала размыто — api/alembic «владеет», ref-bot потребляет своими моделями в той же БД; (#6) `users.referral_code` пишется прод-кодом, которого **нет в этом репо** (в локальном bot/api — ноль ссылок на referral) → ещё один сигнал прод≠репо, стоит расследовать, кто пишет.

#### Round-2 (Ревьюер) — учесть при коде reconcile-миграции:
- **🟡 MINOR — `images` TYPE-change ГАРДИТЬ по типу.** Безусловный `ALTER COLUMN images TYPE jsonb USING images::jsonb` выполнится и на проде (где уже jsonb) → table rewrite под `ACCESS EXCLUSIVE` = прод-DDL, ломает «ноль прод-DDL». Обернуть в гард: `DO $$ BEGIN IF (SELECT data_type FROM information_schema.columns WHERE table_name='products' AND column_name='images') <> 'jsonb' THEN ALTER TABLE products ALTER COLUMN images TYPE jsonb USING images::jsonb; END IF; END $$;` → на parity-БД (text) конвертит, на проде (jsonb) **настоящий no-op**. (`ADD COLUMN`/`CREATE INDEX` уже `IF NOT EXISTS`; type-change такого гарда не имеет — добавить.)
- **🔵 НИТ — порядок:** сперва конверт `images`→jsonb + `ADD advantages` (jsonb)/`video_url`, ПОТОМ `CREATE INDEX ... USING gin` (gin не ложится на text). Имена индексов строго `idx_products_images_gin` / `idx_products_advantages_gin` (иначе name-drift в check).
- **🔵 НИТ — boolean server_default:** alembic иногда ложно дрейфит `true` vs `'true'`. Не блокер; докажет реальный зелёный Migration-parity лог. Если check покраснеет на этом → `compare_server_default`-хук в env.py.

### PR-0.2 — Фича-флаг `loyalty_enabled`

**Scope:** в `src/config.py` `Settings` добавить `loyalty_enabled: bool = False` (env `LOYALTY_ENABLED`). Пока ни на что не влияет; на него будут гейтиться лоялти-роутеры/экраны в Этапах ≥2. Документировать в `.env.example`.
**Verify:** mypy/ruff/pytest зелёные; `settings.loyalty_enabled is False` по умолчанию.
**Files:** `src/config.py`, `packages/api/.env.example`. **Size:** S.

### PR-0.3 — Dev-контур (боевой локальный, без моков)

**Scope:** `docker-compose.dev.yml` (Postgres `biotact_dev`) + `DEV.md` (runbook: uv py3.11, podman, миграции, туннель) + dev-proxy в `packages/frontend/vite.config.ts` (`server.proxy['/api'] → http://localhost:3001`). Туннель-origin → в `CORS_ORIGINS` api `.env`.
**Ops Капитана (не код, блокирует E2E Этапа ≥4):** тест-бот **@biotactdevbot** — задать **Mini App short name** на tunnel-URL; токен — только в `packages/api/.env`.
**Verify:** с телефона через туннель фронт грузится и зовёт API (200, не 404).
**Files:** `docker-compose.dev.yml`, `DEV.md`, `packages/frontend/vite.config.ts`. **Size:** S.

### Deploy gate — разовая проверка (после PR-0.1, до Этапа 1)

Цель — доказать, что укреплённый `deploy.yml` реально катит миграции на проде (не проверено на схеме).
1. **Happy-path:** смёржить безопасную **dummy-миграцию** (no-op / nullable-колонка на некритичную таблицу + чистый down) → по логу деплоя убедиться, что `alembic upgrade head` выполнился, прод-голова продвинулась, health-check зелёный. Затем откатить dummy отдельной down-миграцией.
2. **Fail-rollback:** **локально** (НЕ на проде) прогнать ветку провала из `deploy.yml` против одноразовой БД — заведомо падающая миграция → убедиться, что код ресетается на `PREV_COMMIT`. Зафиксировать как пройдено.

---

## ЭТАП 1 — CRM-фундамент (3 schema-PR, по одной миграции)

**Общие правила всех PR Этапа 1 (Deploy gate):**
- Модель на `Base`, **импортирована в `env.py`/registry** (иначе паритет красный) и в цепочке приложения (create_all).
- Alembic-миграция: `down_revision` цепляется по порядку (1.1 от текущей головы после PR-0.1 → 1.2 → 1.3); **up и down** проверены на копии прод-схемы вне авто-деплоя.
- Любая колонка в `users` — `nullable` или `server_default` (совместимость инсёртов бот-модели).
- PG-enum: по паттерну существующего `crm.LeadStatus` — `Enum(MyEnum, name="...", create_type=True)`, `server_default` = имя члена (StrEnum в PG хранится как NAME).
- `alembic check` зелёный после каждого PR.

### PR-1.1 — `consent`

Согласия построчно (закон РУз: по цели, версионно, с датами).

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK→`users.id` (CASCADE), index | |
| `purpose` | Enum `consent_purpose` (`data_processing`/`marketing`/`personalization`) | |
| `granted` | bool, default true | |
| `version` | str(50) | версия текста согласия |
| `source` | str(50) nullable | qr_activation/phone_share/... |
| `granted_at` | datetime tz, server_default now() | |
| `revoked_at` | datetime tz nullable | |
| `created_at`/`updated_at` | datetime tz | как в проекте |

`User`-модель: добавить relationship `consents` (back_populates). **Без эндпоинтов** в этом PR.
**Verify:** alembic up/down; `alembic check` чист; pytest (create_all создаёт таблицу); mypy --strict.
**Files:** `src/loyalty/models.py` (новый модуль), `src/users/models.py` (relationship), env.py/registry, `alembic/versions/<new>.py`, `tests/test_loyalty_schema.py`. **Size:** M.

### PR-1.2 — `customer_profile` (1:1 users) + `users.phone_verified_at`

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK→`users.id` (CASCADE), **unique** (1:1) | |
| `gender` | Enum `gender`(`male`/`female`) nullable | |
| `age` | int nullable (валидация 18–100 на уровне сервиса позже) | |
| `city` | str(100) nullable | |
| `purchase_goal` | str(50) nullable | кодифицированная |
| `purchase_for_whom` | str(50) nullable | кодифицированная |
| `status` | Enum `loyalty_status`(`newbie`/`biotact_friend`), default newbie, **server_default** | |
| `lifetime_activations` | int, default 0, server_default '0' | для порога «10» |
| `last_activation_at` | datetime tz nullable | |
| `last_accrual_at` | datetime tz nullable | для сгорания (whole-balance таймер) |
| `first_activated_sku` | str(32) nullable | |
| `registration_source` | str(20) nullable | qr_activation/phone_share/organic/ads |
| `survey_raw` | JSONB nullable | растущая анкета без миграций |
| `created_at`/`updated_at` | datetime tz | |

**`users`:** добавить `phone_verified_at` datetime tz **nullable** (единственная правка users; безопасна для бот-инсёртов).
`User`-модель: relationship `profile` (uselist=False). **Баланс НЕ хранит** (= `SUM(ledger)`, Этап 2).
**Verify:** alembic up/down; `alembic check` чист; pytest; mypy; **тест: существующие 462 юзера получают `status=newbie`/`lifetime_activations=0` из server_default без data-миграции**.
**Files:** `src/loyalty/models.py` (доп.), `src/users/models.py` (колонка+relationship), env.py/registry, миграция, тесты. **Size:** M.

### PR-1.3 — `customer_event` (append-only таймлайн)

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | bigint PK | будет много строк |
| `user_id` | FK→`users.id` (CASCADE), index | |
| `event_type` | str(40), index | joined/phone_shared/qr_activated/survey_done/status_friend/raffle_entry/order_placed/points_spent/points_expired |
| `payload` | JSONB nullable | детали события |
| `occurred_at` | datetime tz, server_default now(), index | |
| `created_at` | datetime tz | |

Append-only (без update/delete в коде). `event_type` — строка с константами в коде (не PG-enum: список будет расти, не хотим миграцию на каждый новый тип).
**Verify:** alembic up/down; `alembic check` чист; pytest; mypy.
**Files:** `src/loyalty/models.py` (доп.), env.py/registry, миграция, тесты. **Size:** M.

---

## Acceptance criteria (Этап 0+1 готов, когда)

- [ ] CI-джоб `Migration parity` существует и **зелёный**; локально `alembic check` = «No new upgrade operations detected».
- [ ] Все модели проекта импортированы в `env.py`/registry (banners/broadcasts/settings включены).
- [ ] `loyalty_enabled` в `Settings` (default false), задокументирован в `.env.example`.
- [ ] Dev-контур поднимается по `DEV.md`; фронт через туннель зовёт API (200).
- [ ] Deploy gate проверен: dummy-миграция доехала на прод (happy-path) + fail-rollback прогнан локально.
- [ ] Таблицы `consent`, `customer_profile`, `customer_event` создаются; `users.phone_verified_at` добавлена (nullable).
- [ ] Все миграции применяются **up и down**; каждая колонка `users` nullable/server_default.
- [ ] Существующие 462 юзера: профиль создаётся лениво (Этап ≥4); до тех пор отсутствие профиля ничего не ломает.
- [ ] 4 CI-джобы (api/bot/frontend/admin) + Migration parity — зелёные; mypy --strict; ruff.

## Definition of Done

- Смёржено в `main` по одному PR; ветки удалены. Каждый schema-PR прошёл Deploy gate.
- Новый Python строго типизирован (mypy --strict), отформатирован ruff. Документация (`DEV.md`, `.env.example`) обновлена.
- Лоялти-функциональность **не видна пользователю** (флаг false; эндпоинтов/UI нет).

## Test plan

- **PR-0.1:** parity-джоб на пустом PG; локальный `alembic check`; существующий `pytest` не падает.
- **Этап 1:** на каждый PR — pytest: таблица создаётся (create_all), миграция up/down на postgres:17, server_default-дефолты для users-колонок, `alembic check` чист. Юнит-тестов сервиса нет (сервис — Этапы ≥2).

## Открытые вопросы (к Gate-1)

- **Sync-миграция vs правка моделей** для остаточного дрейфа в PR-0.1 — решаем по факту вывода `alembic check` (предпочтительно правка моделей, если дрейф = «модель отстала»; sync-миграция, если БД опередила модели).
- **`src/models_registry.py`** — заводим единый реестр импортов (рекомендую, чтобы паритет/создание схемы не зависели от того, кто что импортнул) или ограничиваемся правкой env.py? (Рекомендую registry.)
- Имя нового модуля: `src/loyalty/` (как в откатанном PR-1) — переиспользуем имя, код пишем заново под customer-модель.
