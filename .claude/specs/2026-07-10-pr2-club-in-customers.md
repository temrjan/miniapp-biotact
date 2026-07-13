# PR-2: Biotact Club в списке «Клиенты» админки — Гейт-1 спека

**Дата:** 2026-07-10 · **Режим:** соло (`/solo`). **Направление ратифицировано Капитаном 2026-07-10**
(существующая страница «Клиенты», бейджик + базовый набор + фильтры; история активаций — НЕ v1).
**Природа:** read-only обогащение админ-списка. **0 миграций, 0 записей, денежный путь не тронут.**
Один PR: `packages/api` (crm/router.py + тесты) + `admin-panel-v2` (features/customers).

## 1. Задача (слова Капитана)

В админке видеть: кто участник программы лояльности, пришёл по QR-коду или из другого канала;
анкету (город/пол/возраст). «Самое простое — бейджик добавлять» + фильтр «весь список пришедших по QR».

## 2. Факты (код-проверено 2026-07-10)

- Страница «Клиенты» = `admin-panel-v2/features/customers` → `GET /api/v1/crm/customers`
  (`crm/router.py:121`) — читает `users` НАПРЯМУЮ («CRM» лишь имя роутера, «все, кто нажал /start»).
  Схемы `CustomerSchema`/`PaginatedCustomers` — инлайн в router.py (:79/:98). Свой `require_admin`
  (admin-JWT `type=='admin'`, :23). Единственный-клиент фронт: `customersApi` (getAll/getById/getStats).
- Лоялти-данные per-user УЖЕ в БД: `customer_profile` (user_id **unique** → JOIN без размножения строк;
  `registration_source` String(20), значения VALUE `'qr'|'direct'`, **nullable**), баланс = `SUM(point_ledger.amount)`.
- Прод: 3 участника — Тимур id3 (qr, анкета), Руслан id6 (qr, анкета), Ruslan id9 (direct, анкета).
- Тестов на CRM-роутер сейчас НЕТ — PR-2 создаёт первые; admin-JWT-паттерн зеркалится из
  `tests/test_analytics_loyalty.py` (jwt.encode + 401/403/200).
- Админ-фронт: vitest есть (`npm run test`), у loyalty-фичи компонент-тесты; у customers-фичи тестов нет.

## 3. Решение

### Бэкенд (`crm/router.py` — единственный прод-файл)

1. **`CustomerLoyaltySchema`** (инлайн рядом с CustomerSchema):
   `source: str | None` ('qr'|'direct'|null — колонка nullable, first-writer-wins) ·
   `status: str` (enum VALUE 'newbie'|'biotact_friend' — конвенция /loyalty/me) ·
   `lifetime_activations: int` · `gender: str | None` · `age: int | None` · `city: str | None`
   (**unconstrained echo** — то же решение и причина, что PR-1: ответ отражает БД) ·
   `balance: int` (SUM ledger, coalesce 0; может быть отрицательным при будущем REDEEM — тип НЕ ge=0).
2. **`CustomerSchema.loyalty: CustomerLoyaltySchema | None = None`** — None ⇔ не участник.
3. **Хелпер `_loyalty_by_user_id(db, user_ids) -> dict[int, CustomerLoyaltySchema]`** —
   ровно 2 запроса на страницу (профили `WHERE user_id IN` + балансы `GROUP BY user_id`), не N+1.
   Используется списком И одиночным `GET /customer/{telegram_id}`.
4. **Фильтр: query param `club: Literal['member','qr'] | None`** на `GET /customers`:
   `member` → `JOIN customer_profile ON user_id == User.id`; `qr` → тот же JOIN + `registration_source == RegistrationSource.QR.value` (колонка String(20) хранит VALUE — явный `.value`, не enum-член). [check-F4]
   Условие применяется К ОБОИМ запросам — данных И count (иначе total врёт; мутационно-чувствительно).
   Один параметр вместо двух bool: значения взаимоисключающие («по QR» ⊂ «участники»), зеркалит
   единственный Select-фильтр страницы (problem).
5. Импорты кросс-доменные явные (стиль файла: уже импортирует users/orders): `loyalty.models`
   (CustomerProfile, PointLedger) + `loyalty.schemas` (RegistrationSource).
6. `GET /customers/stats` НЕ трогаем (клубные цифры уже на странице «Biotact Club»).

### Фронт (`admin-panel-v2/features/customers`)

1. **types:** `CustomerLoyalty` (зеркало схемы) · `Customer.loyalty?: CustomerLoyalty | null`
   (**optional** — честность окна деплоя: старый API поля не шлёт, admin-api отдаёт response.data
   без маппинг-слоя) · `CustomersParams.club?: 'member' | 'qr' | undefined` (exactOptionalPropertyTypes: страница передаёт явный undefined, зеркало поля problem) [check-F1] ·
   RU-карты: `CLUB_STATUS_LABELS` {newbie: «Новичок», biotact_friend: «Друг»} + фолбэк `??` сырое значение,
   `GENDER_SHORT` {male: «муж», female: «жен»}.
2. **CustomersPage:** Select «Biotact Club» рядом с существующим problem-Select:
   «Все клиенты» / «Участники Клуба» / «Пришли по QR»; value Select'а = сентинел `'all'|'member'|'qr'` (Radix не принимает пустую строку; зеркало problem-фильтра), маппинг `'all' → undefined`; смена → `setPage(1)` (паттерн 1-в-1 с problem). [check-F6] useCustomers ключ включает params → рефетч сам.
3. **CustomerCard:** при `customer.loyalty`:
   - бейдж в пустом правом слоте хедера: `source==='qr'` → **«Biotact Club · QR»**, иначе → **«Biotact Club»**
     (Badge, amber-классы в стиле PROBLEM_COLORS);
   - строка анкеты (иконка ClipboardList): части собираются явными `!= null`-проверками (НЕ truthiness —
     age=0 из unconstrained-БД обязан отображаться, урок PR-1), join « · »; все три null → строка не рендерится; [check-F3]
   - строка статуса (иконка Award — та же, что у страницы Клуба): «Новичок · активаций: 2 · баллов: 5 500»
     (баланс `toLocaleString('ru-RU')`; форма «активаций: N» вместо «N активаций» — уходим от русских
     склонений числительных, лейблы не зависят от значения).
   Без `loyalty` (null/undefined) карточка ИДЕНТИЧНА текущей.

## 4. Крайние случаи

- **Участник без анкеты** (Ruslan direct-сценарий не таков, но возможен): бейдж есть, строки анкеты нет.
- **`source` NULL** (ранний/аномальный профиль): бейдж «Biotact Club» без пометки; фильтр qr его НЕ ловит (строго 'qr').
- **Окно деплоя:** старый API + новый фронт → `loyalty` undefined → guard `customer.loyalty &&` скрывает всё;
  неизвестный query param `club` FastAPI игнорирует → список просто не отфильтрован. Старый фронт + новый API →
  лишнее поле игнорируется. Порядок безразличен (и деплой всё равно атомарный).
- **Фильтр + поиск + problem** — композиция AND, каждый where добавляется независимо к обоим запросам.
- **Пагинация под фильтром:** total/pages считаются ОТФИЛЬТРОВАННЫМ count.
- **Пустой результат** фильтра → пустая страница (существующий UI-путь списка).

## 5. План тестов / DoD (правило №0: red→green; мутации)

**API — новый `tests/test_crm_customers_loyalty.py`** (переиспользует `admin_headers` из conftest.py:88 и maker-паттерн; НЕ дублирует) [check-F5]:
- [ ] Сиды: A=участник qr, анкета (male/47/Ташкент), ledger +6000/−500 → balance **5500** (ловит не-SUM);
      B=участник direct, БЕЗ анкеты, 0 ledger-строк → balance 0 (ловит не-coalesce);
      C=не участник → `loyalty is None`.
- [ ] Список без фильтра: все трое, точные значения A (source/status/acts/gender/age/city/balance).
- [ ] `club=member` → A+B, `total==2`; `club=qr` → только A, `total==1` (total под фильтром = мутационная цель).
- [ ] `club=qr` + `search=<имя B>` → пусто (композиция AND).
- [ ] `GET /customer/{tg}`: A обогащён, C → `loyalty: null`.
- [ ] authz: 401 без токена / 403 не-admin / 200 admin (первые CRM-тесты вообще).
- [ ] **red→green:** stash-прогон против кода без фичи; **мутации:** (1) count без club-условия → total-тест red;
      (2) qr-фильтр сравнивает с 'direct' → red; (3) balance без coalesce/суммы → red.

**Admin-фронт (vitest):**
- [ ] `CustomerCard.test.tsx`: бейдж «Biotact Club · QR» (qr) / «Biotact Club» (direct и source=null) /
      нет бейджа и лоялти-строк без loyalty / строка анкеты частичная (только город) / статус-строка с балансом.
- [ ] `CustomersPage.test.tsx` (mock customersApi: `getAll` И `getStats` — страница зовёт оба [check-F5]): выбор
      «Пришли по QR» → `getAll` вызван с `club:'qr'` и `page:1`. **Radix Select в jsdom без полифилов не кликается**
      (setup.ts пуст) → план-A: точечные полифилы в тест-файле (`Element.prototype.scrollIntoView=vi.fn()`,
      `hasPointerCapture`/`setPointerCapture`/`releasePointerCapture`); план-B (если Radix нестабилен): прокидку
      фильтра держат бэкенд-тесты + прод-smoke, из page-теста остаётся рендер триггера/опций. [check-F2]
- [ ] **red→green:** stash-прогон; мутация: бейдж не зависит от source → red.

**Гейты (локально, точь-в-точь CI):** api — `ruff check` / `ruff format --check` / `mypy --strict` /
`pytest` (parity postgres:17). admin — `npm run lint` / `npx tsc -b` / `npm run test` / `npm run build`.

## 6. Не в скоупе (v1)

История активаций по клику (второй шаг при желании); изменения `GET /customers/stats` и StatsCards;
отдельная страница участников; экспорт; direct-only фильтр; переделка legacy-CRM полей (problems/family/ai_notes);
`datetime.utcnow()` в stats (пред-существующий NIT — не трогаю).

## 7. Почему безопасно

Read-only (0 INSERT/UPDATE), 0 миграций → деплой no-op по схеме; за тем же `require_admin`;
PII в ответе — только то, что админ и так видит по клиентам, плюс их собственная анкета (админ-скоуп);
UI-изменения аддитивные внутри одной фичи; revert одного PR.
