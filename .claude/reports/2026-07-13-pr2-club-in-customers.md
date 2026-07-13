# Отчёт: PR-2 — Biotact Club в списке «Клиенты» админки

**Спека:** `.claude/specs/2026-07-10-pr2-club-in-customers.md` · **Ветка:** `feat/pr2-club-in-customers`
**Природа:** read-only обогащение админ-списка. 0 миграций, 0 записей, денежный путь не тронут.
**Гейт-2:** APPROVED WITH NITS (Ревьюер через Капитана, 0 блокеров, 3 НИТ — все закрыты, см. ниже).

## Сделано — по пунктам спеки

**Бэкенд (`packages/api/src/crm/router.py`) — §3:**
1. `CustomerLoyaltySchema` — source / status / lifetime_activations / gender / age / city / balance.
   Анкетные поля — unconstrained echo БД (то же решение, что в `GET /loyalty/me`); `balance` без `ge=0`,
   чтобы будущий REDEEM мог уйти в минус, а не сломать сериализацию.
2. `CustomerSchema.loyalty: CustomerLoyaltySchema | None = None` — `None` ⇔ не участник Клуба.
3. Хелпер `_loyalty_by_user_id` — ровно 2 запроса на страницу (профили `WHERE user_id IN` + балансы
   `GROUP BY`), не N+1. Переиспользован одиночным `GET /customer/{telegram_id}`.
4. Фильтр `club: Literal['member','qr'] | None` — JOIN на `customer_profile`, применён **к обоим**
   запросам (данные И count), иначе `total`/`pages` врут под фильтром. Для `qr` сравнение с
   `RegistrationSource.QR.value` — колонка `String(20)` хранит VALUE, не имя члена энама.
5. `GET /customers/stats` не тронут (клубные цифры уже на странице «Biotact Club»).

**Фронт (`admin-panel-v2/src/features/customers`) — §3:**
1. `types`: `CustomerLoyalty` (зеркало схемы), `Customer.loyalty?` — **optional**, честность окна деплоя;
   `CustomersParams.club?`; RU-карты `CLUB_STATUS_LABELS`/`GENDER_SHORT` с фолбэком на сырое значение.
2. `CustomersPage`: Select «Biotact Club» (Все клиенты / Участники Клуба / Пришли по QR), сентинел `'all'`
   → `undefined`, смена фильтра → `setPage(1)`; пустой результат под активным фильтром даёт «Клиенты не
   найдены», а не «Пока нет клиентов».
3. `CustomerCard`: amber-бейдж «Biotact Club · QR» / «Biotact Club»; строка анкеты (город · пол · возраст)
   через явные `!= null` — `age=0` из unconstrained-БД обязан отображаться (урок PR-1); строка статуса
   «Новичок · активаций: 2 · баллов: 5 500». Без `loyalty` карточка идентична прежней.

## Не сделано / отложено

Ничего из скоупа. Явно вне v1 (спека §6): история активаций по клику, изменения `/customers/stats` и
StatsCards, отдельная страница участников, экспорт, direct-only фильтр.

## Замечено, не трогаю — вне скоупа

- **Индекс на `customer_profile.registration_source`** — находка perf-флота, отклонена: это миграция,
  вне read-only скоупа PR. Факт-переключатель: десятки тысяч участников → отдельный schema-PR.
- **`CLAUDE.md` §Biotact Club стейл** — говорит «Этап 1 в работе, next PR-1.2», реальность: Этапы 0–4
  отгружены, go-live 2026-07-06. Предлагаю отдельной docs-задачей.
- **`tests/test_analytics_loyalty.py` не ruff-format-чист на `main`** — пред-существующее; CI проверяет
  только `src/`. Не мой файл в этом PR, не правлю (rules #11).
- **`datetime.utcnow()` в `/customers/stats`** — пред-существующий NIT, не трогаю.

## Как тестировал — команды + вывод

**API (точь-в-точь команды CI, parity postgres:17):**
- `ruff check src/ tests/test_crm_customers_loyalty.py` → `All checks passed!`
- `ruff format --check src/` → `75 files already formatted`
- `mypy src/ --strict --ignore-missing-imports` → `Success: no issues found in 75 source files` (0 глушилок)
- `pytest tests/ --no-header` → **184 passed** (из них 13 новых — первые тесты CRM-роутера вообще)

**Admin (точь-в-точь команды CI):**
- `npm run lint` → 0 ошибок (2 warning — пред-существующие, categories/products)
- `npx tsc -b` → чисто · `npm run test` → **72 passed** · `npm run build` → ✓ built

**red→green (доказано, не заявлено):** stash-прогон против кода без фичи — api 6/8 red (2 authz-теста
кроют пред-существующее поведение), admin 7/9 red. **Мутации — 5 из 5 убиты:**
1. убрать club-условие из count-запроса → `test_customers_filtered_pagination_math` red (total врёт);
2. qr-фильтр сравнивает с `'direct'` → red;
3. balance без SUM/coalesce → red;
4. бейдж не зависит от `source` → red;
5. удалить `setPage(1)` при смене фильтра → red.

## Отклонения от плана и почему

- **Формат статус-строки:** спека давала «Новичок · 2 активации · 5 500 баллов», код рендерит
  «Новичок · активаций: 2 · баллов: 5 500» — уход от русских склонений числительных (лейбл не зависит
  от значения). Текст спеки приведён к факту.
- **Radix Select в jsdom** — сработал план-A, заранее записанный в спеке (`[check-F2]`): точечные полифилы
  (`PointerEvent extends MouseEvent` с копией `pointerType`/`pointerId` + заглушки `scrollIntoView` /
  `hasPointerCapture`). Полифил достраивает отсутствующий в jsdom браузерный API, не подменяет исход
  assertion. План-B (держать фильтр только бэкенд-тестами) не понадобился.

## НИТы Гейта-2 — закрыты (ре-ревью не требовалось)

1. Тест-файл приведён к `ruff format` (CI гейтит только `src/`, но код наш).
2. Коммит `test:`, нёсший 1-строчную прод-правку empty-state, переименован в `fix(admin):` с раскрытием
   в теле. Ветка не была запушена — история переписана без последствий.
3. Пример статус-строки в спеке приведён к факту (см. «Отклонения»).

Дельта против отревьюенного дерева — **только перенос строки** в `text(...)` тест-файла (SQL байт-в-байт
тот же) и текст спеки. Прод-код не менялся ни на байт; все api-гейты перегнаны после переписывания.

## Вопросы Ревьюеру

Нет. Все 5 названных зон риска подтверждены источником, блокеров не найдено.

## После мержа — прод-verify (бизнес-smoke)

Админка → «Клиенты»: бейдж «Biotact Club · QR» у Тимура (id3) и Руслана (id6), «Biotact Club» у Ruslan
(id9, direct); фильтр «Пришли по QR» → двое; «Участники Клуба» → трое.
