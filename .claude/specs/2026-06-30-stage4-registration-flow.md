# Этап 4 — Регистрация-поток (Biotact Club)

> Источник верхнего уровня: план **ФИНАЛ v4** `.claude/specs/2026-06-25-biotact-club-plan.md` (роадмап, строка «4. Регистрация-поток»).
> Эта спека — инженерный Гейт-1 на весь Этап 4 (как было в Этапах 2/3); реализация — серией маленьких PR, каждый через Гейт-2.
> Дата: 2026-06-30. Кресло: Инженер (Claude) · команда через Ревьюера (связь только через Капитана).

---

## Цель

Замкнуть боевой вход в программу: коробка с QR → бот ловит `qr_<code>` → пользователь делится телефоном (верифицированный номер) → открывает мини-апп с кодом → даёт согласие → активирует код и видит кэшбэк. Плюс вход без QR (регистрация по телефону, без баллов). В **конце** этапа единственным пользователь-видимым рубильником `loyalty_enabled=true` программа становится живой.

---

## Контекст и инварианты (что уже есть)

- API активации готова и **тёмная** под `loyalty_enabled=false`: `POST /api/v1/qr/activate{code}` + `GET /api/v1/loyalty/me` → 404, пока флаг off. initData-HMAC-auth (`get_init_data_user`, без БД).
- Таблицы готовы, **новых таблиц/колонок Этап 4 НЕ требует** (Verified Ревьюером + grep):
  - `consent` — типизированная per-purpose append-rows; enum `consent_purpose` уже несёт `data_processing`/`marketing`/`personalization`.
  - `customer_profile` — вся анкета (`gender`/`age`/`city`/`purchase_goal`/`purchase_for_whom` + `survey_raw` JSONB) + `registration_source` (`String(20)`) + счётчики/статус.
  - `users.phone` + `users.phone_verified_at` существуют.
  - `customer_event` — словарь уже содержит `joined`/`phone_shared`/`qr_activated`/`survey_done`/`status_friend`.
- **Бот и API делят таблицу `users`** (одна БД; у бота своя зеркальная модель `User` в `packages/bot/bot/models/user.py`).
- Тёмный гейт делает риск-первый порядок безопасным: PR-4.0…4.4 живут на проде под `loyalty_enabled=false` (эндпоинты 404, карта скрыта), 4.5 — единственный видимый флип.

---

## Ключевые решения Гейта-1 (5 пунктов пред-спек-разбора Ревьюера — закрыты)

### Решение 1 — F4 (`first_activated_sku` → `first_activated_product_id`): ретайп String→BigInteger (вариант «b-честный»)
- **Что:** реальная миграция; модель → `Mapped[int | None] = mapped_column(BigInteger)`; `service.py` пишет `product_id` (int, без `str()`); 2 теста обновить.
- **Порядок SQL (МИНОР-2 — ретайп ДО ренейма, иначе `USING` сослался бы на несуществующее имя):**
  - **upgrade:** `ALTER TABLE customer_profile ALTER COLUMN first_activated_sku TYPE BIGINT USING first_activated_sku::bigint;` затем `ALTER TABLE customer_profile RENAME COLUMN first_activated_sku TO first_activated_product_id;`
  - **downgrade (зеркально):** `RENAME COLUMN first_activated_product_id TO first_activated_sku;` затем `ALTER COLUMN first_activated_sku TYPE VARCHAR(32) USING first_activated_sku::varchar(32);`
  - (Колонка пуста → каст no-op; но дословный скетч обязан быть корректным.)
- **Почему именно ретайп, а не code-only-`mapped_column("…")` (вариант «a») и не rename-only-String:**
  - Колонка на проде **пустая** (лоялти тёмное → `activate_code` ни разу не выполнялся) → `USING …::bigint` тривиально безопасен.
  - Поле **чисто внутреннее**: НЕ в Pydantic-схемах, НЕ в админке, НЕ во фронте (grep подтвердил) → ноль внешних контрактов задето.
  - Сейчас хранится `str(product_id)` в `String(32)` — это и кривое имя, и кривой тип (Stage-1 legacy). Честный фикс = имя+тип сразу; rename-only оставил бы «смелл наизнанку» (String с именем `…_product_id`), чего Ревьюер прямо просил не делать.
  - Производственный стандарт (no-MVP, «самый правильный способ») > минимальность правки, когда риск нулевой.
  - Побочно = намеренная 5-я репетиция deploy-гейта на безопаснейшем изменении (даёт серии изолированный риск-первый PR-4.0).
- **FK на `products.id`?** НЕТ в PR-4.0. Это денормализованный историчный указатель (не денежная провенанс-строка как `point_ledger.product_id`), целостность не денежно-критична → plain `BigInteger`, скоуп риск-PR минимален. (FK SET NULL — возможный follow-up, не блокер.)
- **Альтернатива на случай возражения Ревьюера:** вариант «a» (`mapped_column("first_activated_sku")`, ноль миграции) — тогда PR-4.0 исчезает, серия стартует с бота. Решение выношу на финальный Гейт-1.

### Решение 2 — Точки записи событий (4 точки прибиты; каждое — в своей транзакции-источнике истины)
1. **`joined`** — ровно один раз на пользователя. Пишет **та транзакция, что фактически вставила строку `customer_profile`** (`pg_insert(...).on_conflict_do_nothing().returning(id)`; `RETURNING` пуст при конфликте → вставивший = победитель). И consent-эндпоинт, и `activate_code` пытаются создать профиль; `joined` пишет тот, кто реально вставил. Race-safe по `UNIQUE(user_id)`. Payload: `{registration_source}`.
2. **`phone_shared`** — бот в loyalty-таблицы НЕ лезет (Решение-дефолт #2): пишет phone+`phone_verified_at` своей моделью. API пишет `phone_shared` **идемпотентно**, впервые увидев `phone_verified_at IS NOT NULL` и отсутствие прежнего `phone_shared`-события — в consent-эндпоинте, под per-user `customer_profile FOR UPDATE`-локом (guard-then-insert race-safe; регистрация на пользователя одноразова). Payload: `{}`.
3. **`qr_activated`** — внутри `activate_code`, в транзакции claim'а (под тем же локом), **только на реальном claim** (`status==ACTIVATED`), НЕ на replay/conflict/not-found. По событию на успешную активацию (несколько активаций → несколько событий — корректно). Payload: `{qr_code_id, product_id, points_awarded}`. ⚠️ НЕ дублирует существующий `status_friend` (он пишется только на флипе).
4. **`survey_done`** — в survey-эндпоинте (PR-4.4), когда впервые сохранена непустая анкета; идемпотентно (guard на существующее `survey_done`). Payload: `{fields:[…]}`.

### Решение 3 — Согласие до PII (денежный шов не усложняем)
- `POST /qr/activate` **НЕ хардблочит** отсутствие consent-строки (денежный путь остаётся простым и race-safe).
- Порядок «согласие→PII» гарантируется **UI + consent-эндпоинтом**: happy-path мини-аппа сперва `POST /loyalty/consent` (пишет `data_processing` + создаёт профиль + `joined`/`phone_shared`), затем `POST /qr/activate`.
- Согласие = одна обязательная галочка `data_processing` (минимум по закону РУз). `marketing`/`personalization` — прогрессивно позже, не блокируют (enum уже их содержит → без будущей миграции).
- **PII-трактовка (зафиксировано для аудита):** телефон (PII) сохраняется ботом на шаге contact-share ДО галочки `data_processing` в мини-аппе. Это приемлемо: Telegram contact-share — явный аффирмативный акт пользователя; чекбокс `data_processing` покрывает loyalty-профилирование. Формулировка «согласие до PII» относится к профилированию, не к самому факту хранения номера для связи.

### Решение 4 — Гейт бот-handler'а `qr_` (MINOR)
- Бот **gated собственным флагом** `loyalty_enabled` (env, default `False`) в `packages/bot/bot/core/config.py`. Пока off — `qr_<code>` проваливается в обычный `/start` (код игнорируется), всё тёмно и на стороне бота.
- На go-live (PR-4.5) флаг ставится в `true` в env **обоих** сервисов. ⚠️ **API и бот деплоятся по-разному:** CD редеплоит API по merge в main; **бот — отдельный PM2-процесс, CD его НЕ трогает**. Значит флип бот-env = **ручной ops-шаг Капитана** на проде (правка `packages/bot/.env` + `pm2 restart` бота), синхронно с флипом API-env. PR-4.5 это явно прописывает в чек-листе go-live.
- **Бот-CI = только `ruff check bot/`** (нет pytest/mypy). Значит pytest-тесты на (а) диспетчеризацию `qr_` vs `auth_` (`auth.py:54`), (б) `contact→phone+phone_verified_at` — **обязательны и гоняются локально** (доказательство в отчёте).

### Решение 5 — `?code=` в URL WebApp-кнопки (note)
- Допустимо: код single-use, claim атомарный, целостность НЕ зависит от подписи initData. **Код НЕ логировать** (ни бот, ни API).

### Дефолты (подтверждены Капитаном)
- `registration_source`: `qr` при входе по коду, `direct` без кода; ставится один раз (`COALESCE`); источник передаёт мини-апп в consent-эндпоинт (он знает наличие `?code=`).
- `is_active` активацию НЕ гейтит (False = блок нотификаций, не бан).
- Непрайснутый продукт → **503** (как сейчас), не 409.

---

## Объём Этапа 4

**Входит:** бот `qr_<code>` + верифиц. телефон + запуск мини-аппа с кодом; consent-эндпоинт + lifecycle-события; экран активации в мини-аппе; вход без QR + минимальная прогрессивная анкета; финальный флип флага.

**ЯВНО НЕ входит (границы):**
- Розыгрыш и **билеты** (таблица/начисление/commit-reveal) → **Этап 5**. В Этапе 4 — только событийный таймлайн.
- Трата баллов / `orders.points_applied` → Этап 6.
- `marketing`/`personalization`-согласия, полная анкета (только минимум — телефон; пол/возраст/город как опциональная прогрессивная надстройка).
- FK `first_activated_product_id → products.id` (возможный follow-up).
- Любые новые таблицы/колонки.

---

## Нарезка на PR (риск-первый порядок)

### PR-4.0 — F4 ретайп миграция (API, schema-PR) · риск: deploy-гейт (5-й раз)
- **Скоуп:** миграция rename+retype (`down_revision = e881ee3fc9a5` — текущий head, проверить `alembic heads` при реализации); `models.py` поле→`BigInteger`/новое имя; `service.py:264/274` пишет int `product_id`; обновить `test_loyalty_schema.py` (seed int) и `test_loyalty_activation.py` (`== pid`); enum-ничего (полей-enum нет).
- **Файлы:** `alembic/versions/<new>.py`, `src/loyalty/models.py`, `src/loyalty/service.py`, `tests/test_loyalty_schema.py`, `tests/test_loyalty_activation.py`.
- **Приёмка:** migration parity зелёный; `alembic upgrade head` + `downgrade` чисто на parity-БД; деплой-гейт прошёл; прод тёмный без изменений поведения.
- **Тест-план:** обновлённые 2 теста + полный api-CI локально.

### PR-4.1 — Бот: `qr_<code>` + верифиц. телефон + запуск мини-аппа (Bot) · риск: средний
- **Скоуп:** новый handler `CommandStart(deep_link=True)` для `qr_*` (рядом с `auth_`, диспетчеризация по префиксу); флаг `loyalty_enabled` в `core/config.py` (gated); FSM хранит `code`; если нет верифиц. телефона → `get_contact_keyboard` → на `F.contact` сохранить `phone`+`phone_verified_at` (расширить зеркальную модель `User` колонкой `phone_verified_at` — без миграции, колонка в БД есть); затем сообщение с inline WebApp-кнопкой `WebAppInfo(url=f"{webapp_url}?code={code}")`. Если телефон уже верифицирован → сразу кнопка с кодом.
- **Verified на Гейт-1:** `packages/bot/bot/models/user.py` сейчас несёт `phone`, но НЕ `phone_verified_at`; `users.phone_verified_at` в БД есть (`src/users/models.py:31`) → добавление колонки в зеркальную модель безопасно, миграции не требует. `phone_verified_at` ставим **только** на пути `F.contact` (верифицированный номер от Telegram); ручной ввод номера (OrderState) verified НЕ ставит.
- **Файлы:** `packages/bot/bot/handlers/auth.py` (или новый `loyalty.py` + регистрация в `handlers/__init__.py`), `core/config.py`, `models/user.py`, `keyboards/inline.py` (кнопка-лаунчер с кодом), `tests/test_handlers.py` (+ новые).
- **Приёмка:** `qr_` диспетчеризуется отдельно от `auth_`; контакт ставит обе колонки; код доезжает в URL; под флагом-off `qr_` ведёт себя как обычный `/start`; **код не в логах**.
- **Тест-план:** pytest локально: dispatch `qr_` vs `auth_`, `contact→phone+phone_verified_at`, gated-off проброс; `ruff check bot/`.

### PR-4.2 — API: `POST /loyalty/consent` + `joined`/`qr_activated`-события (API) · риск: средний (трогает денежный шов `activate_code` под локом)
- **Скоуп:** новый `POST /api/v1/loyalty/consent` (под тем же 404-гейтом + initData-auth): пишет `data_processing` consent-строку идемпотентно (только если нет действующей), создаёт профиль `on_conflict_do_nothing` + пишет `joined` (победитель вставки), пишет `phone_shared` идемпотентно под локом, ставит `registration_source` (`COALESCE`, источник из тела). Внутри `activate_code`: добавить `qr_activated` на реальном claim **И** (МИНОР-1) `joined`-если-вставил — у `activate_code` уже есть `pg_insert(CustomerProfile).on_conflict_do_nothing()` (service.py:155–159); добавить `.returning(CustomerProfile.id)` и писать `joined`, если строка реально вставлена. Так инвариант «`joined` = транзакция-вставки профиля» (Решение-2) держится на ОБОИХ путях, и клиент с активацией-без-consent не теряет `joined`.
- **Файлы:** `src/loyalty/router.py` (+`summary_router`/новый роут), `src/loyalty/schemas.py` (ConsentRequest/Response), `src/loyalty/service.py` (consent-сервис + `qr_activated`), `src/main.py` (монтаж при необходимости), `tests/`.
- **Приёмка:** тёмный 404; согласие пишется один раз; `joined`/`phone_shared` идемпотентны (повтор не дублирует); `joined` пишется и на пути activate-без-consent (МИНОР-1); `qr_activated` пишется на claim и НЕ на replay; activate НЕ хардблочит без consent.
- **Тест-план:** pytest на реальном PG: идемпотентность событий (повторные вызовы), consent-дедуп, гонка двух consent-вызовов (один профиль/один `joined`), **гонка двух `activate` + claim vs replay** (`qr_activated` ровно один на claim, ноль на replay), `joined` ровно один при activate-первым-без-consent. **Денежный шов → `/review` (fleet) обязателен перед Гейт-2** (+ `/security-review` по необходимости).

### PR-4.3 — Мини-апп: экран активации (Frontend) · риск: низкий
- **Скоуп:** читать `?code=` из URL; экран: чекбокс согласия (обязателен) → «Активировать» → `POST /loyalty/consent` → `POST /qr/activate` → показать кэшбэк/welcome/флип «Друг»; обработка 404 (тёмный → нейтральный экран), 409 (чужой код), 503 (непрайс — «попробуйте позже»). i18n ru/uz. Карта лояльности уже есть (Stage 3).
- **Файлы:** `packages/frontend/src/...` (новый route/экран активации, `api/loyalty.ts` +consent/activate, типы, i18n), vitest.
- **Приёмка:** код считан; happy-path рисует кэшбэк; ошибки 404/409/503 — корректные состояния; `tsc/lint/vitest/build` зелёные.
- **Тест-план:** vitest на компонент + api-адаптер (consent→activate последовательность, маппинг ошибок).

### PR-4.4a — Вход без QR (Front+API) · риск: низкий
> Разнесено с анкетой по Q4 (две независимо-обратимые единицы, rules #13).
- **Скоуп:** мини-апп работает без `?code=` — регистрация = `POST /loyalty/consent` с `source=direct`, без баллов; без кода экран активации не показывается, открывается профиль/карта.
- **Файлы:** front (ветка «без кода»), `api/loyalty.ts`, tests.
- **Приёмка:** без кода регистрация проходит, баллов нет, `registration_source=direct`; тёмный 404.
- **Тест-план:** vitest (ветка без кода); pytest consent `source=direct`.

### PR-4.4b — Прогрессивная анкета (Front+API) · риск: низкий
- **Скоуп:** опциональная анкета (пол/возраст/город) → новый `POST /loyalty/survey` (пишет в `customer_profile`, событие `survey_done` идемпотентно). Анкета НЕ блокирует активацию/регистрацию.
- **Файлы:** front (секция анкеты + api), `src/loyalty/{router,schemas,service}.py` (survey), tests.
- **Приёмка:** анкета пишет поля + `survey_done` ровно один раз (повтор не дублирует); пропуск анкеты ничего не ломает; тёмный 404.
- **Тест-план:** pytest survey-эндпоинт (валидация/идемпотентность `survey_done`); vitest анкета.

### PR-4.5 — 🚀 Флип `loyalty_enabled=true` (config) · риск: высокий (UX-необратимо)
- **Скоуп:** включить флаг в env api + bot на проде; go-live. Отдельный **явный «да» Капитана** (видимо пользователям).
- **Приёмка:** `/qr/activate`+`/loyalty/me`+`/loyalty/consent` отвечают (не 404); карта видна; бот ловит `qr_`; smoke на проде по реальному коду.
- **Тест-план:** прод-smoke (после ops: печать тест-кода + Mini App short name, если нужно для прямого диплинка).

---

## Потоки (словами)

**С QR:** скан → `t.me/<bot>?start=qr_<code>` → бот: нет верифиц. телефона? → запрос контакта → сохранить phone+verified → кнопка «Открыть и получить кэшбэк» (`?code=`) → мини-апп: согласие(чекбокс) → `POST /loyalty/consent`(joined/phone_shared, source=qr) → `POST /qr/activate`(claim, qr_activated, кэшбэк/welcome/флип) → экран результата + карта.

**Без QR:** `/start` (или кнопка) → бот: телефон (как сейчас, +verified на contact) → мини-апп: согласие → `POST /loyalty/consent`(source=direct, без баллов) → профиль/карта; опц. анкета → `survey_done`.

---

## Критерии приёмки (стадия)
PR-готов, когда: каждый PR смержен на зелёном CI и прод-верифицирован тёмным (404/скрыто) до 4.5; на 4.5 — go-live smoke зелёный; 4 событийные точки идемпотентны и привязаны к источнику истины; согласие пишется до PII в happy-path; код нигде не логируется; `mypy --strict`/ruff/pytest (api), `ruff check bot/`+локальный pytest (bot), tsc/lint/vitest/build (front) зелёные.

## Definition of Done
Все 7 PR (4.0/4.1/4.2/4.3/4.4a/4.4b/4.5) merged; ветки удалены; тесты+линт+тайпчек зелёные локально и в CI; deploy-гейт пройден на 4.0; прод-verify каждого PR; план/память/STATE обновлены; флаг включён (4.5) и smoke зелёный.

## Тест-план (по слою)
- **API:** pytest на реальном PG (podman postgres:17): идемпотентность событий, consent-дедуп, гонки (consent×2, activate-claim vs replay), F4 round-trip int; migration parity (upgrade+downgrade на чистой parity-БД); полный набор CI-джобов локально перед Гейт-2.
- **Bot:** локальный pytest (диспетчеризация, contact→2 колонки, gated-off), `ruff check bot/`.
- **Frontend:** vitest (экран активации, маппинг 404/409/503, анкета), tsc/lint/build.
- **Денежные/auth PR (4.2, 4.3):** `/review` (fleet) + при необходимости `/security-review`.

## Ответы Гейта-1 (Ревьюер через Капитана, 2026-06-30 — APPROVED WITH CONDITIONS, закрыто)
1. **F4 ретайп vs code-only** → **ретайп BigInteger ОК** (колонка на проде пуста, поле внутреннее, `USING ::bigint` тривиально безопасен; мотив Капитана — намеренная 5-я репетиция deploy-гейта). При условии фикса МИНОР-2 (порядок SQL — внесён в Решение-1).
2. **FK `first_activated_product_id→products.id`** → **вне Этапа 4** (денормализованный историчный указатель, не денежная провенанс-строка; целостность не денежно-критична). Plain `BigInteger`; FK SET NULL — опциональный follow-up.
3. **Бот-флаг как механизм гейта** → **да, правильно** (собственный env-флаг бота = настоящий гейт; «коды не напечатаны» = надежда, не гейт). Ops флипа бот-env на go-live прописан в Решении-4 + PR-4.5 (ручной шаг Капитана, PM2-рестарт).
4. **Гранулярность 4.4** → **разнести** на 4.4a (вход-без-QR) / 4.4b (анкета) — две независимо-обратимые единицы (rules #13). Внесено в нарезку.

Закрытые условия Гейта-1: **МИНОР-1** (`joined` в `activate_code` — Решение-2 + PR-4.2), **МИНОР-2** (порядок SQL миграции — Решение-1), **PII-трактовка** (Решение-3), риск PR-4.2→средний, race-тесты+`/review` fleet на 4.2. Ре-ревью спеки не требуется — дельту подтверждает Капитан.
