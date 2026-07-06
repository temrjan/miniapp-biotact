# Biotact Club — Предзапусковый аудит (финальный синтез)

Дата: 2026-07-03 · Флаг: `loyalty_enabled=false` (система ТЁМНАЯ) · Ставка: балл = 1 сум, go-live необратим, staging нет.
Аудит только читал код; динамика — на локальной parity-БД (podman postgres:17). Прод/SSH/флаг не тронуты.

---

## 1. Executive summary + ФИНАЛЬНЫЙ ВЕРДИКТ

### ВЕРДИКТ: **GO-WITH-CONDITIONS**

Денежное ядро корректно **сегодня**. Все три несущих замка `SELECT customer_profile FOR UPDATE` присутствуют в коде (service.py:174/284/390), включая единственный per-user money-serializer в `activate_code` (:390). Динамическая фаза доказала мутационно: 4/4 последовательных денежных инварианта (friend-flip порог, welcome-однократность, атомарный claim, consent-дедуп) **краснеют** при поломке; замок активации **защищён** конкурентными race-тестами (снятие → race-тесты падают 3/3); dark-gating эмпирически пишет **ноль** строк в 3 таблицы при флаге off; миграции forward-only (прод-путь) — `alembic upgrade head` + `alembic check` чисто.

**Ни одного подтверждённого пути к неверным деньгам / утечке / необратимой порче в штатном потоке не найдено → это НЕ BLOCKER, вердикт не понижается до NO-GO.**

Почему не чистый GO: остаются условия, которые должны быть выполнены/подтверждены **до** флипа (см. §4). Главное — операционный стык: флип нужен в ДВУХ независимых процессах (api + bot), а `bot/.env.example` не документирует флаг → риск полу-включённого состояния. Плюс подтверждённая (не блокирующая go-live) поломка up→down→up цикла миграций и test-honesty дыра на денежном инварианте (прод-код верен, но будущий рефактор замка пройдёт зелёным).

Найдено: **0 BLOCKER, 2 MAJOR, 4 MINOR, 15 NIT.**

---

## 2. Таблица находок по severity

| id | severity | линза | file:line | сценарий отказа | repro | рекомендация |
|----|----------|-------|-----------|-----------------|-------|--------------|
| race-lock (tests-1+conc-1+conc-2) | MAJOR | L2/L6 | packages/api/src/loyalty/service.py:390 | Будущий рефактор сужает/снимает FOR UPDATE (или get_or_create_user перестаёт делать side-UPDATE) → две активации разными кодами на границе 9→10 обе флипают +5000 (переплата 10000) / две первые активации обе пишут welcome; вся сьюта остаётся ЗЕЛЁНОЙ | На parity-PG снять `.with_for_update()` в activate (:390) — race-тесты падают (замок реально защищён); НО seed-тесты consent(:174)/survey(:284) с снятым замком остаются зелёными (маскируются последующим UPDATE customer_profile) | Добавить детерминированный race-тест, изолирующий именно замок: сид юзера с first_name идентичным TelegramUser (нет side-UPDATE) → снятие замка должно краснить welcome-once/boundary-flip. Прод-код верен сегодня; если принять как остаточный — задокументировать, что INV-10 держится на коде+defense-in-depth, а не на падающем тесте |
| migration-roundtrip (dyn) | MAJOR | L5 | packages/api/alembic/versions/ce14a3d28633 (initial, down) | `upgrade head`→`downgrade base`→`upgrade head` падает `DuplicateObjectError: type "orderstatus" already exists`. Причина: initial-миграция в downgrade дропает таблицы, но не `DROP TYPE orderstatus`, а повторный CREATE TYPE не защищён. Бьёт DR / dev-reset, НЕ прод go-live | На чистой БД: upgrade head → downgrade base → upgrade head → падение на orderstatus | Прод-путь forward-only и ЧИСТ (не блокер флипа). До будущих миграций: добавить `DROP TYPE orderstatus` в downgrade initial-миграции ИЛИ guard `checkfirst` на CREATE TYPE; все 6 loyalty-enum'ов роллбэчатся чисто — дефект в старой базовой цепочке |
| money-1 | MINOR | L1 | packages/api/src/loyalty/pricing.py:63 | Отрицательная цена продукта (не через API — прямой psql/import/миграция) проходит NULL/fractional-гарды → `_accrue` коммитит отрицательные CASHBACK/WELCOME строки (append-only, неисправимо), затем Pydantic ge=0 роняет 500 ВМЕСТО обещанного 503 | Прямой `UPDATE products SET price=-100`; активация → 500 + отрицательные ledger-строки. API (ProductUpdate gt=0) сам негатив блокирует — нужен out-of-band write | `if price <= 0: raise UnknownProductError` в get_unit_price (→503, откат) + DB `CHECK(price>0)` на products.price + регресс-тест price='-100'/'0'→503, 0 строк. Низкая достижимость → допустимо после флипа |
| security-1+design-1 | MINOR | L3/L8 | packages/api/src/loyalty/router.py:51 | get_init_data_user (чистый HMAC) НЕ грузит user и НЕ проверяет is_active; забаненный (is_active=false) со своим валидным 24ч initData POST-ит /qr/activate и продолжает копить кэшбэк. get_or_create_user не сбрасывает is_active | Деактивировать U (admin PATCH/broadcast 403) → POST /qr/activate валидным initData → 200 + начисление; при этом /users/me тем же initData отдаёт 403 | Явное решение: либо `if not user.is_active: raise ForbiddenException` после get_or_create_user в activate/consent/survey, либо задокументировать намеренное игнорирование (is_active смешивает бан и «заблокировал бота»). Нужен пред-бан + свой initData → не блокер |
| security-4 | MINOR | L3 | packages/api/src/loyalty/router.py:145 | /qr/activate НЕ имеет серверного consent-gate: создаёт профиль, копит баллы и пишет PII timeline-события без строки data_processing consent (порядок consent→activate только на фронте) | POST /qr/activate валидным initData без предварительного /consent → 200 + профиль + начисление, consent-строк = 0 | Юридическое ревью: нужен ли серверный consent-gate до обработки ПДн, либо задокументировать законное основание (условия на коробке + шеринг телефона = affirmative act). Трекать; не блокер флипа |
| correctness-1 | MINOR | L4/L7 | packages/frontend/src/pages/ActivationPage.tsx:23 | После флипа 404 от /qr/activate = «неизвестный код», но фронт мапит 404→«Программа лояльности скоро будет доступна» (dark, no-retry). Пробел загрузки батча/опечатка → пользователь думает, что программа не запущена, и уходит | Флаг on, скан кода, которого нет в БД → «coming soon» вместо «код недействителен». Тест ActivationPage.test.tsx:139 фиксирует ошибочное поведение как верное | Для живой программы 404→`activation.error.invalidCode` (как 409), либо отдельный маркер dark-состояния, чтобы фронт не путал «фича off» и «код не найден». Денег не теряет; чинить после флипа, но источник ложных тикетов на go-live |
| security-3+design-3 | NIT | L3/L8 | packages/bot/bot/handlers/common.py:307 | handle_contact_received без чека contact.user_id==from_user.id → спуфнутый чужой номер пишется в свой users.phone (unverified) и в CRM. НЕ пересекает loyalty-границу (phone_verified_at не ставится) | В waiting_phone приложить чужой контакт → номер в CRM. Тот же результат достижим набором номера вручную (handle_enter_phone/handle_ai_message) → не даёт новой поверхности | Общий примитив `_normalize_and_store_contact(message, verified)` всегда с чеком user_id (loyalty verified=True, common verified=False). Не loyalty-блокер |
| security-2 | NIT | L3 | packages/api/src/loyalty/router.py:78 | Нет rate-limit/lockout/anomaly-detection на /qr/activate; аутентифицированный юзер льёт случайные коды — все 404, без блокировки/алерта. Деньги не сливаются (60 бит → десятилетия до попадания), только DB-нагрузка | Цикл POST /qr/activate случайными кодами валидным initData → бесконечные 404, без 429. 404 = handled HTTPException → без ERROR-лога/Sentry | После флипа: per-user throttle + алерт на аномальную долю 404; зафиксировать 60-бит brute-force drain как принятый риск |
| migrations-1 | NIT | L5 | packages/bot/.env.example:(нет) | bot/.env.example не содержит LOYALTY_ENABLED (api содержит). Оператор реконфигурит бот из шаблона / правит только api/.env → бот остаётся off при api on → мини-апп активирует, но бот роняет qr_-deeplink в обычный /start → активация коробки недостижима при «включённой» системе | Скопировать bot/.env.example→.env, включить только api → скан qr_ проваливается в cmd_start | Добавить `LOYALTY_ENABLED=false` (+коммент «флипать вместе с API на PR-4.5») в bot/.env.example. Уже в списке deliverables спеки PR-4.5. Тривиально, ДО флипа |
| tests-3 | NIT | L6 | packages/bot/tests/test_loyalty_handler.py:5 | Докстринги утверждают ложь: bot CI «no pytest job» (ci.yml:238 гоняет pytest); consent:293/survey:251 «снятие FOR UPDATE роняет тест» (не роняет — маскирует UPDATE). Мейнтейнер снимет не тот замок, доверясь несуществующей сети | Статически: ci.yml:238 vs docstring; динамически: снять :174/:284 → race-тесты зелёные | Исправить docstring бота (pytest гоняется); поправить consent/survey race-докстринги (замок замаскирован следующим UPDATE, прод-safety = defense-in-depth) или де-маскировать тесты |
| tests-2 | NIT | L6 | packages/api/tests/test_loyalty_activation.py:262 | Dark-тесты проверяют только HTTP 404, не count==0 в point_ledger/customer_event/consent при off. Будущий side-write на shared pre-gate пути запишет строку при off, тест останется зелёным | Добавить middleware-запись customer_event до gate → dark-тест зелёный при записанной строке | Один flag-off тест: POST по 4 роутам + assert `COUNT(*)==0` на 3 таблицах |
| frontend-1 | NIT | L7 | packages/frontend/src/components/features/SurveyCard.tsx:24 | age '0'/'150'/'25.5'/city >100 симв. проходят инертные HTML-хинты → backend 422 → «что-то пошло не так» без указания поля/диапазона | Ввести age=150 → Save активна → 422 → generic error | Валидировать age 1..120 и city ≤100 до mutate; disable Save/показать поле. Без денег/данных; после флипа |
| frontend-2 | NIT | L7 | packages/frontend/src/pages/ActivationPage.tsx:32 | 401 (истёкший initData) падает в default retry-ветку → «Повторить» шлёт тот же протухший initData → бесконечная петля; реальный фикс (переоткрыть) не показан. Недостижимо при dark (404 раньше) | Флаг on, мини-апп открыт >24ч → Активировать → 401 → generic+Повторить → тот же 401 | `case 401` → «сессия истекла, переоткройте», canRetry=false. Это тот PR-4.5, на который ссылается коммент — сделать сейчас или зафиксировать как принятый интерим |
| migrations-2 | NIT | L5 | .github/workflows/ci.yml:192 | migration-parity гоняет только upgrade+check, никогда downgrade → будущая миграция со сломанным down пройдёт CI незамеченной | Миграция с забытым enum-DROP в down → CI зелёный | Round-trip шаг (upgrade→downgrade base_of_loyalty→upgrade→check) на чистой БД, либо явно задокументировать downgrade=unsupported (rollback=forward-fix). После флипа |
| migrations-3 | NIT | L5 | .github/workflows/deploy.yml:85 | deploy откатывает КОД (git reset), не схему. На health-fail пути (миграция уже закоммичена) схема ведёт код. Безопасно для аддитивных миграций, опасно для будущей non-additive | health-check падает после успешного upgrade → git reset без downgrade → старый код + новая схема | Явно принять forward-only + задокументировать в runbook (rollback=code+forward-fix; миграции строго аддитивны). После флипа |
| tests-4 | NIT | L6 | packages/frontend/src/components/features/JoinCard.test.tsx:39 | Ни один тест не кликает mutation-кнопку дважды при in-flight → «только один запрос» не проверено. Регресс отключения disable проходит зелёным; сервер-идемпотентность поглощает дубль | Удалить `isLoading={join.isPending}` → сьюта зелёная | Тест: mock pending, два клика, assert вызван 1 раз + кнопка disabled |
| design-2 | NIT | L8 | packages/api/src/loyalty/service.py:271 | submit_survey mass-assign'ит payload в `update(...).values(**updates)`. Будущее поле SurveyRequest без колонки (email / rename city→town) → CompileError → каждый /survey 500-ит в проде, не ловится ни ревью, ни типами | Добавить `email` в SurveyRequest → POST {email} → CompileError 500 | Инвариант-тест `set(SurveyRequest.model_fields) ⊆ set(CustomerProfile.columns)` или явный маппинг. 3 текущих поля безопасны; после флипа |
| design-4 | NIT | L8 | packages/api/src/loyalty/models.py:95 | purchase_goal/purchase_for_whom/survey_raw — ноль прод-читателей/писателей; докстринг survey_raw «extend without migrations» противоречит shipped типизированному подходу → ловушка «какой источник истины» | grep: только models/migration/schema-тест | Дропнуть 3 колонки до появления писателя (YAGNI) ИЛИ поправить докстринг survey_raw (типизированные колонки = источник истины). Косметика; после флипа |
| frontend-3 | NIT | L7 | packages/frontend/src/pages/ActivationPage.tsx:61 | Баланс/баллы форматируются хардкодом 'ru-RU' независимо от выбранного uz. Для целых баллов вывод байт-в-байт идентичен uz-локали → визуально 0 разницы | uz-язык → ActivationPage → toLocaleString('ru-RU') | Брать локаль из активного языка / нейтральный форматтер. Косметика; когда угодно (паттерн по всему кодбейсу) |

Плюс два LOW-замечания динамики (не отдельные находки): (a) избыточный замок record_consent:174 + вводящие в заблуждение комменты (пересекается с tests-3); (b) working-tree гигиена — на момент старта мутаций локальный service.py отклонялся от HEAD на одну строку (отсутствовал activate-замок :390); восстановлено из HEAD, все 3 замка на месте, `git diff packages/api` пуст. Проверено при синтезе: замки 174/284/390 присутствуют сейчас.

---

## 3. Per-area readiness L1–L8

| Линза | Статус | Комментарий |
|-------|--------|-------------|
| L1 — Целостность денег/данных | ⚠️ с условиями | Ядро верно: баланс=Σ(ledger), append-only, Decimal ROUND_HALF_UP, атомарный claim + partial UNIQUE. Один MINOR: отрицательная цена (out-of-band) → коррумпированные строки + 500 вместо 503. Форвард-риск orders total (вне скоупа, Этап 6) |
| L2 — Конкурентность/гонки | ⚠️ с условиями | Прод-замки реальны и несущи (activate :390 доказан race-тестами). MAJOR — test-honesty: consent/survey замки замаскированы, их снятие не краснит тесты; риск тихого регресса будущего рефактора |
| L3 — Безопасность | ⚠️ с условиями | HMAC (hash+signature exclusion, timing-safe, 24ч), IDOR-proof, replay-idempotent, generic 409 без утечки владельца, SQLi чисто, dark-gate до auth. MINOR: is_active не проверяется на money-пути; consent не enforced на сервере. NIT: нет rate-limit, contact-guard в common.py |
| L4 — Корректность/логика | ✅ чисто | Friend-flip порог (ровно 10-я, off-by-one нет), welcome-однократность, activations_to_friend, маппинг 404/409/503, null-обработка, границы схем, FSM бота, идемпотентность JOINED — всё верно. Мутации 4/4 краснеют |
| L5 — Миграции/Ops/Deploy | 🔴 блокер (для DR) / ⚠️ (для флипа) | Прод forward-only путь ЧИСТ (upgrade+check зелёные, go-live без новых миграций). MAJOR: up→down→up падает на orderstatus (старая базовая цепочка). NIT: CI не гоняет downgrade, deploy откатывает код не схему, bot/.env.example без флага |
| L6 — Тесты | ⚠️ с условиями | Сьюта сильна и честна на ПОСЛЕДОВАТЕЛЬНЫХ денежных гардах (мутации краснеют), фронт-ветки, bot-dispatch. Дыра: concurrency-замки consent/survey не под падающим тестом; dark count==0 не проверяется; double-submit не кликается дважды. api 152 / bot 17 / front 158 — все зелёные |
| L7 — Frontend/UX | ⚠️ с условиями | MINOR: 404→«coming soon» после флипа. NIT: 401 retry-петля, SurveyCard без клиент-валидации, ru-RU локаль. Ветки статуса, инвалидация кэша, double-submit guard на Button — корректны |
| L8 — Дизайн/сопровождаемость | ✅ чисто (с nit) | Транзакционный паттерн единообразен, SRP соблюдён, seed #2 устранён как класс, имена честные, границы модулей чисты. NIT: mass-assign survey, спекулятивные неиспользуемые колонки, дубль contact-хендлеров |

---

## 4. ДВА СПИСКА: до флипа vs после

### MUST-FIX / ПОДТВЕРДИТЬ ДО ФЛИПА
1. **Флип в ДВУХ процессах одной согласованной операцией** (api config.py:25 env + bot core/config.py:31 env через PM2). Полу-включённое состояние = функциональная поломка go-live (INV-6). Проверить flag-on smoke по обоим входам (QR + direct) на первой активации.
2. **Добавить `LOYALTY_ENABLED=false` в `packages/bot/.env.example`** (одна строка, с комментом про совместный флип на PR-4.5). Устраняет главный операционный footgun. Уже в deliverables спеки PR-4.5.
3. **Подтвердить, что деплой-источник (origin/main / прод) содержит все три `.with_for_update()` замка**, особенно activate_code:390 — единственный per-user money-serializer. Динамика нашла его отсутствующим в локальном working-tree (восстановлено из HEAD). Это денежно-критичный гейт: без замка activate:390 — реальная переплата +5000 под гонкой. (При синтезе локально подтверждено: 174/284/390 на месте, diff пуст — но проверить именно на прод-коммите.)

### МОЖНО ПОСЛЕ ФЛИПА (трекать, не блокирует go-live)
- money-1: guard `price<=0`→503 + DB CHECK(price>0) (низкая достижимость, API уже блокирует негатив).
- security-1: решение по is_active на money-пути (проверять или задокументировать намеренное игнорирование).
- security-4: юр-ревью серверного consent-gate / фиксация законного основания.
- correctness-1: 404→invalidCode для живой программы (источник ложных тикетов, но не деньги) — желательно раньше.
- frontend-2: `case 401`→«переоткройте», no-retry.
- frontend-1: клиент-валидация SurveyCard (age 1..120, city ≤100).
- Test-hardening: детерминированный race-тест на consent/survey замки (де-маскировать); dark count==0 тест; double-submit тест; исправить ложные docstrings (tests-3).
- migration-roundtrip: `DROP TYPE orderstatus` в down старой initial-миграции + CI round-trip шаг (нужно до СЛЕДУЮЩЕЙ миграции, не для этого env-флипа).
- migrations-3: задокументировать forward-only rollback в runbook.
- design-2: инвариант-тест keys⊆columns для survey.
- design-4: дропнуть/задокументировать спекулятивные колонки.
- security-2: per-user throttle + алерт на 404-долю.
- security-3: общий contact-примитив с user_id-чеком.
- frontend-3: локаль из активного языка.

---

## 5. Динамические доказательства

**Тесты (parity-PG postgres:17, podman):** api pytest **152 passed / 0 failed**; bot `ruff check`=clean + pytest **17 passed** (вкл. test_loyalty_handler); frontend vitest **158 passed** (17 файлов) + `npm run build` OK + `tsc --noEmit` 0 ошибок.

**Мутационная таблица (backup→mutate→run→restore, md5-verified):**

| Гард (service.py) | Мутация | Пойман | Итог |
|-------------------|---------|--------|------|
| friend-flip порог :449 | `>=`→`>` (10→11) | ✅ | test_tenth_activation_flips_to_friend упал |
| атомарный claim :401 | убран `status==GENERATED` из WHERE | ✅ | replay пере-начисляет → IntegrityError partial-UNIQUE |
| welcome-once :462 | `==0`→`>=0` | ✅ | test_second_activation_has_no_welcome упал |
| consent-дедуп :202 | условие → всегда True | ✅ | 2-я строка data_processing consent |
| activate FOR UPDATE :390 (одно-поток) | снят замок | ❌ (ожидаемо) | seed-тест 3/3 зелёный — одно-сценарный тест не видит снятие |
| activate FOR UPDATE :390 (конкурентно) | тот же снятый замок | ✅ | race-тесты 3/3 упали (asyncio.gather, отдельные PG-конекшны) — **замок реально защищён** |
| consent FOR UPDATE :174 | снят замок | ❌ | race 3/3 зелёный — маскируется последующим UNCONDITIONAL UPDATE customer_profile; дедуп safe, но замок избыточен, комменты :169-175 и test:293 НЕВЕРНЫ |

**Dark-gating (PASS):** флаг off, ASGI-клиент по 4 роутам → все **404** (require_loyalty_enabled до auth); post-call row counts **point_ledger=0, customer_event=0, consent=0** — тёмная фича не пишет ничего.

**Миграции:** single head `28abc54f3dce`. Чистая БД: `upgrade head`=OK, `alembic check`=«No new upgrade operations detected» (**прод-гейт проходит**, parity модель↔миграции чиста). `downgrade base`=OK, но оставляет enum `orderstatus` [BUG, старая initial-миграция] + banners/settings [BY DESIGN, reconcile-baseline]. **Второй `upgrade head` после downgrade base ПАДАЕТ** DuplicateObjectError orderstatus. Все 6 loyalty-enum/таблиц роллбэчатся чисто — дефект в старой базовой цепочке, не в loyalty.

---

## 6. Clean-декларации (смотрели и чисто)

- **L2 конкуренция:** атомарный claim (same-code idempotency), partial UNIQUE backstop, порядок локов profile→code (deadlock-free), гонка разных юзеров на один код (409 без утечки), DB-side счётчик bump (нет ORM-staleness), replay-чтения стабильны, брендновый юзер + две первые активации (недостижимо в bot-first).
- **L3 безопасность:** INV-12 HMAC (hash+signature exclusion, timing-safe, auth_date подписан, expiry 24ч, tz-бага нет), IDOR (личность только из подписанного initData), replay/идемпотентность, QR-энтропия 60 бит CSPRNG (product = FK не в строке), утечка кода в логи отсутствует, разделение auth_/qr_ deeplink, dark-gating security-дименсия, SQLi (параметризовано), consent/survey dedup под FOR UPDATE + phone_shared строго на phone_verified_at.
- **L4 корректность:** friend-flip порог, welcome-однократность, activations_to_friend, ветвление статусов, маппинг ошибок, null-обработка, границы схем, submit_survey member-only (не создаёт профиль), async/await, bot FSM (отдельные StatesGroup, отмена/повторный /start, dark-ветка), идемпотентность JOINED, frontend ветки статуса.
- **L5 миграции (прод-путь):** upgrade head + `alembic check` чисто, enum-DROP в loyalty down корректны, единый линейный head, f1e2 reconcile idempotent-guards, phone_verified_at nullable аддитивно, NOT NULL с server_default, паритет имени флага LOYALTY_ENABLED в обоих config, deploy secrets+health-check, partial UNIQUE переживает round-trip.
- **L8 дизайн:** транзакционный паттерн, seed #2 устранён как класс, честный naming, earn_row_reconciles не мёртвый, чистые границы модулей, frontend loyalty-слой, два роутера обоснованы, activate_code NOTE честно документирует допущение.

---

## 7. Остаточные принятые риски

1. **60-бит brute-force drain** — вычислительно инфизибелен (~десятилетия до попадания), анти-фрод/armed снят осознанно. Принято; defense-in-depth rate-limit — после флипа.
2. **is_active смешивает бан и «заблокировал бота»** (broadcasts ставит false на TG-403) — строгий чек залочит легитимных клиентов, только замьютивших нотификации. Требует явного решения, не механического фикса.
3. **Consent не hard-block на money-пути** — намеренный дизайн (router.py:145-147); достаточность законного основания = юр-вопрос, self-limited на свою же consent-запись.
4. **Forward-only rollback** — миграции строго аддитивны (политика команды); схема может вести код на health-fail пути. Принято при аддитивности.
5. **Брендновый юзер + две первые активации** гонка на UNIQUE(telegram_id) — недостижимо в bot-first потоке (юзер создан ботом до мини-аппа), задокументировано в коде.
6. **orders.create_order доверяет клиентскому total** — форвард-риск Этапа 6, loyalty сегодня orders не читает/пишет. Зафиксировано, не чинится в этом флипе.
7. **Test-honesty дыра на consent/survey замках** — если не закрыта до флипа, принимается как остаточная: прод-код верен, INV-10 держится на коде + defense-in-depth (маскирующий UPDATE), а не на падающем тесте.

---

*Аудит завершён. Флаг `loyalty_enabled` остаётся False в обоих процессах. Ничего не флипнуто, не закоммичено, не задеплоено.*
