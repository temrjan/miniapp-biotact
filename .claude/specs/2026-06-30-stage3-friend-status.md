# Biotact Club — Этап 3: Статус «Друг»

> Источник истины верхнего уровня — план **ФИНАЛ v4** `.claude/specs/2026-06-25-biotact-club-plan.md` (§3 «Статус», §6 «граница 9→10», §8-таблица этап 3).
> Эта спека — инженерный срез Этапа 3 под Гейт-1.
> ⚠️ `merge→main = деплой на прод`. Но **Этап 3 не содержит миграций** (см. §«Почему без миграции») → schema-риска деплоя нет; Deploy gate всё равно проходит (happy-path).
> Скоуп подтверждён Капитаном (2026-06-30): backend-флип + read-эндпоинт + карта лояльности в Профиле мини-аппа + запись `customer_event(status_friend)`.
> **Гейт-1 ПРОЙДЕН (2026-06-30):** Ревьюер APPROVED-for-start (CoVe — все номера строк/факты VERIFIED против кода, гонка границы разобрана). Вкатаны 2 МИНОРА: **МИНОР-1** — семантика `points_awarded` (см. §Семантика ActivateResponse); **МИНОР-2** — отдельные имена роутеров + `require_loyalty_enabled` на `/me`. НИТ (синтетик newbie с lifetime≥10 кажет «0/10» до активации — само-лечится, тёмное) — без действия.

## Goal — один абзац

Включить **статус «Друг BIOTACT»**: при достижении **10 активаций lifetime** клиент навсегда переходит `newbie → biotact_friend`, его кэшбэк растёт **5% → 8%** (навсегда, не сгорает), и он разово получает **+5000 баллов**. Переход происходит **внутри уже существующей транзакции активации** под тем же `customer_profile FOR UPDATE` локом (forward-safe шов заложен в PR-2.3b). Дополнительно: read-эндпоинт `GET /api/v1/loyalty/me` и **карта лояльности в существующем разделе «Профиль»** мини-аппа (баланс + бейдж статуса + прогресс к «Другу»). Всё под `loyalty_enabled=false` (тёмное) до конца Этапа 4 — фронт-карта скрыта, пока эндпоинт отдаёт 404.

## Approach — выбран + почему

**Расширяем существующий денежный шов, не строим новый.** Вся механика статуса ложится в уже отгруженный `activate_code` (PR-2.3b): лок на `customer_profile` уже держится через `async with db.begin()`, счётчик `lifetime_activations` уже инкрементится под локом, reason `friend_bonus` и enum-значение `biotact_friend` уже объявлены в моделях и **уже на проде** (PG-enum создан с обоими значениями миграцией `c7cb4807f4ba`; `reason` — свободный `String`). Поэтому Этап 3 = **чистый сервис-слой + read-эндпоинт + фронт**, без единой миграции и без рефактора транзакции.

**Флип = идемпотентный порог-чек, не детект перехода.** Условие (locked в Stage-2 spec Q1): `status == 'newbie' AND lifetime_after >= 10` (НЕ «поймать 9→10»). Робастно к счётчику, уже перешагнувшему 10 (бэкфилл/ре-инжест): такой юзер флипнется на следующей активации. Флип однократен (гард по `status == newbie`), поэтому `friend_bonus` начисляется ровно раз; partial-`UNIQUE(qr_code_id, reason)` — DB-бэкстоп от гонки.

**Порядок на 10-й активации (план §3):** сначала flip, **затем** начислить эту же 10-ю активацию уже по 8%. То есть ставка кэшбэка считается по *эффективному* статусу (с учётом флипа этого вызова).

**Read-эндпоинт — чистое чтение, без сайд-эффектов.** `GET /loyalty/me` НЕ создаёт юзера/профиль (в отличие от `get_or_create_user` у `/orders`): неизвестный юзер или юзер без профиля → дефолт (Новичок, баланс 0, 0/10). Так открытие Профиля любым посетителем мини-аппа не плодит пустые строки.

**Отвергнуто:** (а) детект перехода 9→10 (хрупко к бэкфиллу); (б) хранить статус/баланс колонкой на `users` (дрейф; баланс = `SUM(ledger)`); (в) read-эндпоинт через `get_or_create_user` (запись на GET); (г) показывать карту лояльности при выключенном флаге (путает — заработать пока нельзя, активация тёмная).

## Почему без миграции (проверено в коде)

- `loyalty_status` PG-enum создан сразу с `("NEWBIE","BIOTACT_FRIEND")` — `c7cb4807f4ba:40`. `biotact_friend` уже валиден на проде.
- `customer_profile.status` (`server_default NEWBIE`) и `lifetime_activations` (`server_default 0`) уже есть (PR-1.2).
- `point_ledger.reason` — свободный `String`; `LedgerReason.FRIEND_BONUS = "friend_bonus"` уже объявлен (`models.py:181`); миграция на reason не нужна (как и задумано — словарь растёт без миграции).
- `customer_event` + `CustomerEventType.status_friend` уже есть (PR-1.3).
- ⇒ Все колонки/типы/значения на месте. Этап 3 ничего не добавляет в схему.

## Components (затронутые файлы)

**Backend (Python):**
- `packages/api/src/loyalty/service.py` — ядро флипа:
  - SELECT под локом расширить: читать `status` вместе с `lifetime_activations`.
  - `_accrue`: вычислить эффективный статус + ставку (500/800), при флипе — `friend_bonus`-строка (+5000, привязана к `qr_code_id`), `status=biotact_friend` в UPDATE профиля, строка `customer_event(status_friend)`.
  - Константы: `FRIEND_RATE_BPS = 800`, `FRIEND_THRESHOLD = 10`, `FRIEND_BONUS_POINTS = 5000` (рядом с `NEWBIE_RATE_BPS`/`WELCOME_RATE_BPS`).
  - `ActivationOutcome` += `tier: LoyaltyStatus`, `friend_bonus: int`.
- `packages/api/src/loyalty/schemas.py` — `ActivateResponse` += `tier`, `friend_bonus`; **семантика `points_awarded` — см. §Семантика ActivateResponse (МИНОР-1)**; новая схема `LoyaltySummaryResponse`.
- `packages/api/src/loyalty/router.py` — маппинг новых полей в `ActivateResponse`; **новый отдельный объект-роутер для чтения** (имя `summary_router`, НЕ переиспользовать/затенять существующий `router` активации) с `GET /me`; на `/me` навешен **тот же** `dependencies=[Depends(require_loyalty_enabled)]` (404-гейт до auth, непробируемо) + `get_init_data_user` (HMAC) + read-only (МИНОР-2).
- `packages/api/src/main.py` — смонтировать `summary_router` на `{api_v1_prefix}/loyalty` (тег `loyalty`) **под отдельным импортным именем** (напр. `from src.loyalty.router import router as loyalty_router, summary_router as loyalty_summary_router`), чтобы в `main.py` не было теневого `router`. `/qr/activate` (`loyalty_router` на `/qr`) остаётся как есть.
- `packages/api/src/loyalty/service.py` — read-функция `get_loyalty_summary(session, telegram_id) -> ...` (read-only).
- Тесты: `packages/api/tests/test_loyalty_*` (флип/гонки/идемпотентность/ставка/событие/read-дефолты).

**Frontend (TypeScript/React):**
- `packages/frontend/src/api/loyalty.ts` — **новый** `loyaltyApi.getMe()` → `api.get<LoyaltySummary>('/loyalty/me')`.
- `packages/frontend/src/types/` — тип `LoyaltySummary` (tier, balance, lifetimeActivations, activationsToFriend).
- `packages/frontend/src/hooks/` (или inline в компоненте) — `useLoyalty()` через TanStack `useQuery` (404 → карта скрыта, не ошибка).
- `packages/frontend/src/components/features/LoyaltyCard.tsx` (или в `pages/ProfilePage.tsx`) — карта: баланс, бейдж «Новичок/Друг», прогресс-бар N/10; neumorphic, в стиле `Card`.
- `packages/frontend/src/pages/ProfilePage.tsx` — вставить `<LoyaltyCard>` сразу под аватар-карточкой.
- `packages/frontend/src/i18n/ru.json` + `uz.json` — ключи `profile.loyalty.*`.
- Тесты: vitest на компонент/хук (рендер для newbie/friend, скрытие при 404).

## Механика (точная, под §6 «обязательные тесты»)

В `activate_code` (всё под одним `async with db.begin()` и `customer_profile FOR UPDATE`):
1. `lifetime_before, status_before = SELECT lifetime_activations, status … FOR UPDATE`.
2. claim кода (как сейчас). Если 0 строк → replay/conflict/404 (как сейчас; read-поля статуса в replay-ответе берём из профиля).
3. `lifetime_after = lifetime_before + 1`.
4. `flip = status_before == NEWBIE and lifetime_after >= FRIEND_THRESHOLD`.
5. `effective_tier = BIOTACT_FRIEND if flip else status_before`.
6. `rate_bps = FRIEND_RATE_BPS if effective_tier == BIOTACT_FRIEND else NEWBIE_RATE_BPS`.
7. cashback-строка по `rate_bps`; welcome-строка как сейчас (только при `lifetime_before == 0` — с флипом не пересекается).
8. Если `flip`: `friend_bonus`-строка (amount=5000, reason=`friend_bonus`, qr_code_id=код этой активации, user_id; `unit_price`/`rate_bps` NULL — не cashback, reconcile-хелпер её пропускает); `customer_event(status_friend, payload={lifetime_activations, bonus})`.
9. UPDATE профиля: `lifetime_activations+1`, `last_activation_at`, `last_accrual_at`, `first_activated_sku` (как сейчас) **+ при флипе `status=biotact_friend`**.
10. Outcome: `tier=effective_tier`, `points_awarded` (см. ниже), `welcome_bonus`, `friend_bonus`, `balance=SUM(ledger)`, `lifetime_activations=lifetime_after`.

### Семантика ActivateResponse (МИНОР-1, locked)
`points_awarded` = **всё, начисленное этой активацией** = `cashback + welcome + friend_bonus`. Инвариант `balance == prev_balance + points_awarded` обязан держаться (детектится приёмочным тестом). `welcome_bonus` и `friend_bonus` — **разбивка-подполя** того же `points_awarded` (для фронта «вы заработали X, из них приветственных Y / бонус Друга Z»), а не отдельные суммы поверх. На реплее (`already_activated`): `points_awarded` = что код начислил исходно (включая `friend_bonus`, если та активация флипнула), `welcome_bonus`/`friend_bonus` = 0 (нового начисления нет — баланс уже отражает прошлое). Без флипа `friend_bonus=0`.

`GET /api/v1/loyalty/me` (флаг-гейт → 404 пока тёмное): auth `get_init_data_user` (HMAC) → `get_user_by_telegram_id` (read-only). Нет юзера/профиля → `{tier: newbie, balance: 0, lifetime_activations: 0, activations_to_friend: 10}`. Есть профиль → `tier=status`, `balance=SUM(ledger)`, `lifetime_activations`, `activations_to_friend = max(0, 10 - lifetime)` (0 если уже Друг).

## Скоуп PR — что входит / что ЯВНО не входит

**Нарезка — 3 мелких обратимых PR:**

- **PR-3.1 (backend, Python) — движок статуса.** Флип newbie→friend под локом, ставка 500/800, `friend_bonus` +5000, `customer_event(status_friend)`, расширение `ActivateResponse`. Тесты гонок/идемпотентности §6. Цикл: `/python`+`/codex` → `/python-review`.
- **PR-3.2 (backend, Python) — read-эндпоинт.** `GET /api/v1/loyalty/me` (флаг-гейт, read-only, дефолты). Тесты дефолтов/friend. Цикл: `/python`+`/codex` → `/python-review`.
- **PR-3.3 (frontend, TypeScript) — карта лояльности в Профиле.** `loyaltyApi`+тип+хук+`LoyaltyCard`+вставка в `ProfilePage`+i18n RU/UZ+скрытие при 404. Цикл: `/typescript`+`/codex` → `/typescript-review`.

**ЯВНО НЕ входит (OUT):**
- ❌ Любая миграция БД (не нужна).
- ❌ Включение `loyalty_enabled` (мастер-флаг — конец Этапа 4).
- ❌ Вход/онбординг через бота, QR-deeplink, шеринг телефона (Этап 4).
- ❌ «Привилегии» сверх кэшбэка 8% + бонуса (не определены → откладываем до определения; план не специфицирует).
- ❌ Трата баллов, розыгрыш, билеты, рефералы, сгорание (Этапы 5-7).
- ❌ Прогрессивная анкета, регистрация-поток (Этап 4).

## Критерии приёмки — «PR готов, когда…»

**PR-3.1:**
- [ ] 10-я активация (`lifetime_before==9`, newbie): статус→`biotact_friend`, эта активация по 8%, `friend_bonus` +5000 одной строкой; `customer_event(status_friend)` записан.
- [ ] **Инвариант суммы (МИНОР-1):** на 10-й `points_awarded == cashback(8%) + friend_bonus(5000)` и `balance == prev_balance + points_awarded`; `friend_bonus`-подполе = 5000, `welcome_bonus` = 0.
- [ ] 11-я+ активация Друга: кэшбэк 8% (`rate_bps=800` в строке), без повторного `friend_bonus`/события.
- [ ] Идемпотентность: повторный скан 10-го кода → тот же ответ, без второго `friend_bonus` (партиал-UNIQUE + claim-гейт).
- [ ] Робастность порога: `lifetime>=10` но `status==newbie` (синтетика) → флип на ближайшей активации.
- [ ] До 10-й — кэшбэк 5%, статус newbie. welcome (1-я активация) по-прежнему корректен, с флипом не пересекается.
- [ ] Гонка двух claim на границе (под локом) → ровно один флип, один `friend_bonus`. Reconcile-инвариант не нарушен (`friend_bonus` пропускается).

**PR-3.2:**
- [ ] `GET /loyalty/me` при `loyalty_enabled=false` → 404. При флаге on: newbie-дефолт для неизвестного юзера (без записи в БД); реальные `tier/balance/lifetime/activations_to_friend` для существующего профиля; для Друга `activations_to_friend=0`.

**PR-3.3:**
- [ ] Карта в Профиле под аватаром: баланс, бейдж статуса, прогресс N/10. Корректный рендер для newbie и friend.
- [ ] Эндпоинт 404 (тёмное) → карта **скрыта**, Профиль работает как раньше (без ошибки/пустого блока).
- [ ] i18n RU/UZ; стиль neumorphic консистентен; `tsc --noEmit` + lint + build зелёные.

## Definition of Done
- Каждый PR: merged / ветка удалена / тесты+линт+тайпчек(+build для фронта) зелёные (вывод приложен) / Ревьюер «чисто» через Капитана.
- Deploy gate happy-path на проде без отката (backend-PR; миграций нет → `alembic upgrade head` no-op).
- Память/STATE обновлены; отклонения зафиксированы в спеке.

## Test plan
- **PR-3.1 (pytest, parity PG17):** флип на 10-й; 8% на 11-й; идемпотентный повтор 10-го (один friend_bonus); порог `>=10 & newbie`; до-10 newbie+5%; welcome×флип не пересекаются; гонка двух claim (один флип); `customer_event(status_friend)` записан; reconcile пропускает friend_bonus. + `alembic upgrade head` no-op (нет новой ревизии).
- **PR-3.2 (pytest):** 404 при флаге off; дефолт newbie без записи (проверить, что юзер НЕ создан); friend/`activations_to_friend`.
- **PR-3.3 (vitest):** рендер карты newbie/friend; прогресс N/10; скрытие при 404; i18n-ключи присутствуют в ru+uz.
- **Полные CI-гейты локально** перед каждым Гейтом-2 (api: ruff+`ruff format --check`+`mypy --strict`+pytest; frontend: eslint+`tsc --noEmit`+vitest+build) — по [[feedback-run-full-ci-gates-locally]].

## Открытые вопросы (не блокеры)
- Точная визуальная форма бейджа/прогресс-бара — на усмотрение в PR-3.3 (frontend-design в стиле neumorphic); согласуем на ревью PR-3.3.
- `payload` у `customer_event(status_friend)` — минимально `{lifetime_activations, bonus: 5000}`; расширим при появлении читателя таймлайна.
