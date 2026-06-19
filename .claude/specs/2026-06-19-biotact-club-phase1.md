# Biotact Club — Фаза 1 (ядро оцифровки, пилот 33К коробок)

> Источник требований: `Biotact_Club_Strategy_Final` v3.1 (разделы 5, 7, 8, 10, 11, 12, 18.5).
> Проект: `miniapp-biotact` (brownfield). Кресло: Соло-инженер. Состояние workflow: discover → planning после approve.

## Goal

Превратить анонимного аптечного покупателя BIOTACT в верифицированный CRM-профиль через одноразовый QR на клапане коробки. Пользователь сканирует QR → попадает прямо в существующий Telegram mini-app (`@BiotactBot`) по Direct Link `startapp=<code>` → код активируется → начисляется кэшбэк баллами → заполняет двухуровневую анкету (L1 обязательна для билета, L2 за +1 билет) → участвует в ежемесячном доказуемом розыгрыше Аэрогриля. Админ генерирует партии уникальных кодов, экспортирует CSV для VDP-печати, «вооружает» партию после отгрузки и проводит розыгрыш с commit-reveal. Фаза 1 — это **только оцифровка и накопление** (earn-only): без списания баллов, без статуса «Друг», без рефералов, без L3, без переработки витрины. Цель пилота — реальные scan rate / Cost-per-Profile / распределение заполнения анкеты.

## Approach — выбранный + почему

**Approach A — единый поток в миниаппе, бот как тонкий лаунчер, append-only леджер баллов.** (Выбран.)

- QR кодирует Direct Link `t.me/BiotactBot/<app>?startapp=<code>`. Значение приезжает во фронт как `WebApp.initDataUnsafe.start_param` ([core.telegram.org/bots/webapps](https://core.telegram.org/bots/webapps)). Фронт вызывает `POST /qr/activate` с заголовком `X-Init-Data`. Активация, анкета и билеты — целиком в миниаппе под Telegram-авторизацией.
- Бот в лоялти-таблицы **не пишет** — единственная правка бота в Фазе 1 находится вне кода (BotFather: задать Mini App short name, чтобы Direct Link работал). Это исключает дрейф между копией `User`-модели бота и API.
- Баланс баллов — **материализованная сумма append-only леджера** `point_transactions`, а не свободное поле. `users.balance` обновляется в той же транзакции, что и запись леджера. Любой баланс всегда объясним и аудируем.
- Коды **генерируем мы** (сервер) → экспорт CSV типографии (ТЗ 6.4). Полный контроль формата/уникальности.
- **Базис кэшбэка — на партии**, не на каталоге продуктов: `qr_codes` batch несёт `sku_code` + `unit_price` (дефолт из каталога цен 8.6). Начисление не зависит от чистоты таблицы `products`. `products.sku_code` — связь для витрины — отложена в Фазу 2.
- Телефон L1: если `users.phone` уже есть — переиспользуем без запроса. Если нет — фронт зовёт `Telegram.WebApp.requestContact()`, но **номер Telegram доставляет боту, не во фронт** (колбэк даёт только boolean). Поэтому Фаза 1 включает **минимальный always-on бот-хендлер** `@router.message(F.contact)`, апсертящий `users.phone` (бот уже пишет `users.phone` через `UserService.update_phone` — это существующая таблица users, не лоялти; развязка #6 цела). Фронт после `true` поллит `GET /users/me`, пока не появится phone, затем продолжает L1.
- Розыгрыш — **ручное админ-действие** + commit-reveal SHA-256: публикуем `SHA-256(server_seed ‖ снимок билетов)` за ≥24ч (commit), после — раскрываем seed + список (reveal), любой воспроизводит выбор. random.org signed-draw — опциональное усиление публичной энтропии. ([chain.link](https://chain.link/article/provably-fair-randomness), [conduition.io](https://conduition.io/scriptless/lottery/)).

**Отклонённые альтернативы:**
- **Approach B — активация через бота** (`?start=<code>`, бот парсит и зовёт API): лишний round-trip, бот пишет лоялти (нарушает развязку), анкета всё равно в миниаппе. Минус «один тап».
- **Approach C — гибридный handoff** (бот кладёт pending-код, миниапп подхватывает): больше движущихся частей, YAGNI на пилоте.

## Components

**API (`packages/api/src/loyalty/`, новый модуль):**
- `models.py`: `QrCode`, `CodeBatch`, `PointTransaction` (леджер), `SurveyAnswer`, `LotteryDraw`, `LotteryTicket`; enum `LoyaltyStatus`, `TransactionReason`, `DrawStatus`.
- Расширение `users` (`src/users/models.py`): `balance` (int, default 0), `status` (enum, default newbie), `last_activation_at`, `lifetime_activations` (int, default 0), `gender`, `age`, `city`, `purchase_goal`, `purchase_for_whom`.
- `products.sku_code` (nullable) — добавить колонку, backfill отложен (Фаза 2).
- `router.py` (user, X-Init-Data): `POST /qr/activate`, `POST /survey/level1`, `POST /survey/level2`, `GET /lottery/my-tickets`.
- `admin_router.py` (JWT): `POST /admin/codes/batch`, `GET /admin/codes/batch/{id}/export` (CSV), `POST /admin/codes/batch/{id}/arm`, `GET /admin/codes/batches`, `POST /admin/lottery/draw/commit`, `POST /admin/lottery/draw/execute`, `GET /admin/lottery/draws`.
- `service.py`: атомарная активация (row-lock/idempotent), начисление по леджеру, выдача билетов, commit-reveal.
- `schemas.py`, Alembic-миграция (down_revision = `e1f2a3b4c5d6`), каталог цен 13 SKU (config-константа).

**Auth (PR-0):** правка `src/auth/telegram.py` — исключать из data-check-string и `hash`, и `signature`.

**Admin panel (`admin-panel-v2/src/features/loyalty/`):** страницы «Партии кодов» (создать/таблица/экспорт CSV/вооружить) и «Розыгрыши» (открытый розыгрыш/commit/execute/победители). По паттерну `broadcasts`/`orders`. CSV-аплоад НЕ нужен (мы экспортируем, не импортируем).

**Frontend (`packages/frontend/src/`):** `stores/loyaltyStore.ts`; экран активации/успеха (читает `start_param`); формы анкеты L1/L2 (паттерн чекаута + in-app contact request); блок «баланс + билеты + обратный отсчёт» в `ProfilePage`; i18n-ключи `loyalty.*`/`survey.*` в `ru.json`+`uz.json`.

**Bot:** **минимальная правка** — always-on хендлер `@router.message(F.contact)` → `UserService.update_phone` (для телефона L1, см. Approach). В лоялти-таблицы бот по-прежнему не пишет. Ops: BotFather — Mini App short name для Direct Link (вне репозитория). APScheduler (пуши о сгорании/ре-активации) — опциональный хвост, см. PR-7.

## Assumptions

**Betting on (ставим):**
1. Активация даёт баллы **независимо** от анкеты; L1 даёт билет, L2 — ещё билет (ТЗ 7.1, 5.2). Баллы = 5% × `unit_price` партии; первая в жизни активация — welcome ×1.5. **Округление — round-half-up до целого на финальной сумме** (`5662.5 → 5663`, как ТЗ 8.7). Баланс/баллы — integer. **Welcome-проверку (`lifetime_activations == 0`) и инкремент делаем под тем же row-lock на `users`, что и начисление** — иначе параллельные первые активации дадут двойной welcome.
2. Один «открытый» розыгрыш в момент времени (`LotteryDraw` несёт `period` + `draw_at` для обратного отсчёта). Билет выдаётся при заполнении L1 для текущего открытого розыгрыша (в штатном джорни активация предшествует L1, условие «активация в окне» выполнено); L2 — +1. Макс 2 билета/аккаунт/розыгрыш в Фазе 1. **Снимок участников для commit-reveal замораживается на момент commit**; билеты после commit — в следующий розыгрыш. Execute при 0 билетов → статус «без участников», без падения.
3. `requestContact()` (есть в `@twa-dev/sdk` 8.x) возвращает только статус-boolean; номер уходит **боту** → нужен always-on бот-хендлер контакта (см. Approach, телефон L1) + фронт-поллинг `GET /users/me`. Это правка бота в Фазе 1 — НЕ «бот не трогаем».
4. Тесты используют `Base.metadata.create_all` → новые модели подхватятся, если зарегистрированы на `Base`. Миграция Alembic — для реальной БД, держим в синхроне с моделями.
5. 13 SKU: каталог `sku_code → unit_price` (раздел 8.6, прайс с НДС май 2026) как config-константа; админ при создании партии видит дефолт-цену, может переопределить.
6. Существующие 462 юзера: статус newbie + balance 0 приходят бесплатно из дефолтов колонок (ТЗ 18.6) — отдельной data-миграции не требуется.

**Could kill this (риски):**
- BotFather Mini App short name не сконфигурён → Direct Link `startapp` не доставляет код. Ops-блокер, не код. Митигация: проверить ДО PR-6a; запасной ввод 12-значного кода вручную в миниаппе (ТЗ 5.3) как страховка.
- `requestContact()` не поддержан SDK 8.0.2 → нужен бот-баунс (раздувает PR-6b).
- Гонка двойной активации одного кода → строгий uniqueness + row-lock + idempotency (тест обязателен в PR-2).

**Consciously ignoring (осознанно вне Фазы 1, и почему это ок):**
- Списание баллов / лимиты чекаута (ТЗ 8.8): продажи миниаппа ≈ 0, не на критическом пути, лезет в Multicard/OFD — отдельная поздняя фаза.
- Статус «Друг»/8%, L3-анкета, рефералы, билет за подписку на канал, авто-подписка, переработка витрины, Kitchen Tech — Приоритет 2 ТЗ.
- Сиротские `referrers`/`referral_invites` (миграция `8efc0a708641`) — другая сущность (сотрудничий реферал), не трогаем (тех-долг помечен отдельно).

## PR scope

**IN (Фаза 1):** модель данных лоялти + расширение users; генерация/экспорт/вооружение партий кодов; активация QR с леджером баллов и welcome ×1.5; анкета L1/L2 с денормализацией в users; билеты текущего розыгрыша; доказуемый commit-reveal розыгрыш (ручной); экраны миниаппа (активация/успех/анкета/баланс+билеты); админ-страницы партий и розыгрышей; i18n RU/UZ; фикс initData `signature`.

**OUT (явно):** списание баллов, статус «Друг», L3, рефералы, подписка-билет, авто-подписка BIFOLAK, переработка витрины/баннеров, Kitchen Tech как товар, APScheduler-пуши (кроме опц. PR-7), интеграция 1С, импорт чужих CSV-кодов, backfill `products.sku_code`.

## Acceptance criteria

Фаза 1 готова, когда:
- [ ] Реальный код из сгенерированной партии: скан QR → миниапп открывается по `startapp` → `POST /qr/activate` начисляет 5% (welcome ×1.5 при первой) → запись в `point_transactions` → `users.balance == SUM(ledger)`.
- [ ] Повторная активация того же кода отклоняется; гонка двух параллельных активаций даёт ровно одно начисление (тест).
- [ ] L1 (телефон/пол/причина/для кого/18+) сохраняется в `survey_answers` + денормализуется в `users`, выдаёт ровно 1 билет на текущий розыгрыш; L2 (возраст/город) — +1 билет; повторная отправка не дублирует билеты; потолок 2 билета/аккаунт/розыгрыш.
- [ ] Новый юзер без телефона получает in-app contact request; существующий с `users.phone` — без повторного запроса.
- [ ] Админ: создать партию (SKU+кол-во+цена) → экспорт CSV уникальных кодов → «вооружить» партию; до вооружения код не активируется даже при сканировании.
- [ ] Админ: commit (публикуется `SHA-256` снимка) → execute (reveal seed+список, выбор N победителей); хеш из commit совпадает с пересчётом по revealed-данным.
- [ ] Миниапп: экран успеха показывает баланс + билет(ы); профиль показывает баланс, билеты и обратный отсчёт до розыгрыша; всё на RU и UZ.
- [ ] initData с присутствующим полем `signature` валидируется успешно (регресс-тест падает на старом коде).
- [ ] Лоялти-роутер зарегистрирован в `main.py`, модели импортированы в цепочке приложения → `Base.metadata.create_all` создаёт лоялти-таблицы (тесты не падают `relation does not exist`).
- [ ] Параллельные первые активации одного юзера дают welcome ×1.5 ровно один раз (тест гонки).
- [ ] Округление баллов round-half-up подтверждено тестом (`5662.5 → 5663`).
- [ ] Хеш commit воспроизводится из revealed-снимка (снимок заморожен на commit); execute при 0 билетов не падает.
- [ ] Боевой E2E с @biotactdevbot: vite-proxy `/api`→localhost:3001 + tunnel-origin в CORS → активация/анкета/билеты проходят с телефона.

## Definition of Done

- Смёржено в `main`, ветки удалены.
- Зелёные **все 4 CI-джобы**: ruff check + `ruff format --check` + **mypy --strict** + pytest (api), ruff + pytest (bot), tsc + lint + vitest + build (frontend, admin).
- Новый Python-код строго типизирован (mypy --strict), отформатирован ruff; TS без `any`, named exports.
- i18n: паритет ru.json/uz.json.
- CLAUDE.md/README обновлены, если менялись затронутые контракты; миграция применяется up и down.

## Local dev & testing (прод не трогаем, тестируем «по боевому»)

Приложение гоняем как в проде — через **реальный тестовый Telegram-бот**, без dev-обходов/моков initData (заглушки запрещены, целимся в боевое). Прод (`@BiotactBot`, прод-БД, `app.biotact.uz`, PM2 `/var/www/biotact`) не задействован.

**Изоляция от прода:** отдельный **тест-бот** (BotFather: свой токен + Mini App short name), отдельная локальная БД `biotact_dev`, отдельный tunnel-URL. Прод-бот НЕ перенастраивается, локальный API НИКОГДА не указывает на прод-`DATABASE_URL`.

**Боевой локальный контур:**
- Локальный Postgres (`biotact_dev`) + `uvicorn` (API) на `127.0.0.1:3001`.
- **API недостижим из webview телефона напрямую** (`API_BASE_URL = VITE_API_URL || '/api/v1'`, `constants.ts:11`; в `vite.config.ts` нет proxy). Поэтому **добавляем dev-proxy в `vite.config.ts`**: `server.proxy['/api'] → http://localhost:3001`, чтобы фронт и API делили один tunnel-origin (`VITE_API_URL` не задаём → относительный `/api/v1`). Альтернатива — туннелировать API отдельно и добавить его origin в `CORS_ORIGINS`.
- **Туннель** (`cloudflared`/`ngrok`) → локальный фронт (Vite HTTPS-дев, `basicSsl` уже настроен). tunnel-origin добавляем в `CORS_ORIGINS` api `.env`. Mini App URL тест-бота (@biotactdevbot) указывает на tunnel-URL.
- В локальном api `.env` — **токен тест-бота** (@biotactdevbot): `validate_init_data` обязан использовать тот же токен, которым Telegram подписал initData, иначе HMAC не сойдётся. (Боевая проверка авторизации, без обходов.)
- E2E: реальный `t.me/biotactdevbot/<app>?startapp=<code>` открывается в настоящем Telegram → реальный initData → активация/анкета/билеты в боевом потоке. QR для скана генерим на тест-партии.

**Автотесты (pytest) — это не обход Telegram, а юнит/интеграция эндпоинтов:** подписываем initData тест-юзера тем же dev-токеном и проверяем валидатор/активацию/леджер/анкету/розыгрыш (паттерн уже в `tests/conftest.py`/`test_auth.py`). Гоняются в CI на postgres:17.

**Админка** локально — Vite-дев + локальный API, вход username/password (Telegram не нужен для админ-части).

## Test plan

- **API (pytest, postgres:17):** активация (happy path, double-activate, race, welcome ×1.5, batch-not-armed → reject, unknown code → reject); начисление = SUM(ledger); L1/L2 идемпотентность билетов + потолок; eligibility розыгрыша; commit-reveal (хеш воспроизводим, выбор детерминирован при заданном seed). Регресс initData `signature` в `test_auth.py`.
- **Admin (vitest):** формы/таблицы партий и розыгрышей рендерятся, мутации зовут правильные эндпоинты.
- **Frontend (vitest):** парсинг `start_param`, экран успеха с балансом, формы L1/L2 (валидация), профиль с билетами; толерантность к отсутствию `start_param` (прямой заход без активации не падает).
- **Ручная проверка:** тестовый код end-to-end в dev-миниаппе (скан → активация → анкета → билет → админ-розыгрыш).

## Task breakdown

Высокорисковые — первыми (auth → схема → деньги/баллы). Чекпоинт каждые 2-3 PR.

- **PR-S (рано, разблокирует боевой локальный контур) — DEV.md runbook + `docker-compose.dev.yml` (Postgres `biotact_dev`) + vite dev-proxy `/api`→localhost:3001 + tunnel-origin в CORS.** Ops-шаг Капитана (не код): тест-бот @biotactdevbot + Mini App short name на tunnel-URL. Verify: с телефона через туннель фронт грузится и зовёт API (200, не 404). Files: `docker-compose.dev.yml`, `DEV.md`, `packages/frontend/vite.config.ts`. **S.**
1. **PR-0 — фикс initData `signature` + регресс-тест.** IN: `telegram.py` исключает `hash`+`signature`; тест с валидным `signature`. Verify: `pytest tests/test_auth.py`, mypy, ruff. Files: `src/auth/telegram.py`, `tests/test_auth.py`. **S.**
2. **PR-1 — модель данных лоялти + расширение users + миграция + каталог 13 SKU.** Verify: alembic up/down; pytest (create_all); mypy. Files: `src/loyalty/models.py`, `src/loyalty/catalog.py`, `src/users/models.py`, `src/products/models.py`, `alembic/versions/<new>.py`. **M.**
3. **PR-2 — `POST /qr/activate` + леджер баллов.** IN: атомарная активация, 5%/welcome×1.5, balance=SUM(ledger), idempotency/race. Verify: pytest (вкл. race). Files: `src/loyalty/{router,schemas,service}.py`, `tests/test_loyalty_activate.py`. **M.** ⛳ чекпоинт.
4. **PR-3 — анкета L1/L2 + билеты + `GET /lottery/my-tickets`.** IN: сохранение+денормализация, выдача билетов, потолок, идемпотентность. Verify: pytest. Files: `src/loyalty/{router,schemas,service}.py` (доп.), `tests/test_loyalty_survey.py`. **M.**
5. **PR-4a — админ: партии кодов (generate/export CSV/arm).** Verify: pytest. Files: `src/loyalty/admin_router.py`, `service.py` (доп.), `schemas.py` (доп.), `tests/test_loyalty_batches.py`. **M.** ⛳ чекпоинт.
6. **PR-4b — админ: доказуемый розыгрыш (commit/execute).** Verify: pytest (хеш воспроизводим, выбор детерминирован). Files: `src/loyalty/admin_router.py` (доп.), `draw_service.py`, `tests/test_loyalty_draw.py`. **M.**
7. **PR-5 — админ-UI: партии + розыгрыши.** Files: `admin-panel-v2/src/features/loyalty/{api,hooks,pages,types}` + навигация + роуты. **M.** ⛳ чекпоинт.
8. **PR-6a — фронт: активация + экран успеха.** IN: `loyaltyStore`, чтение `start_param`, вызов `/qr/activate`, успех с балансом. Files: `stores/loyaltyStore.ts`, `pages/LoyaltyActivationPage.tsx`, `api/loyalty.ts`, роут, i18n. **M.**
9. **PR-6b — фронт: анкета L1/L2 + телефон + always-on бот-хендлер контакта.** IN: формы L1/L2; `requestContact()` → поллинг `GET /users/me`; бот `@router.message(F.contact)` → `update_phone`. Files: `pages/SurveyPage.tsx`, компоненты форм, i18n, `bot/handlers/common.py` (доп. хендлер), `bot/tests/`. **M.**
10. **PR-6c — фронт: баланс + билеты + отсчёт в профиле.** Files: `ProfilePage.tsx` (доп.), компоненты, i18n. **M.** ⛳ чекпоинт.
11. **PR-7 (опционально, хвост) — APScheduler в боте: пуши о сгорании/ре-активации.** Только если остаётся бюджет; не блокирует пилот. **M.**

## References

- [core.telegram.org/bots/webapps](https://core.telegram.org/bots/webapps) — `startapp`→`start_param`/`tgWebAppStartParam`; `signature` — новое поле; стандартная HMAC-валидация исключает `hash` и `signature`.
- [docs.telegram-mini-apps.com — Validating](https://docs.telegram-mini-apps.com/packages/tma-js-init-data-node/validating) — правило исключения обоих полей подтверждено.
- [chain.link — Provably Fair Randomness](https://chain.link/article/provably-fair-randomness), [conduition.io — Provably Fair Off-Chain Lottery](https://conduition.io/scriptless/lottery/) — commit-reveal + публичный seed.
- Внутренние: текущий `src/auth/telegram.py:48-55` (баг), `alembic` head `e1f2a3b4c5d6`, conftest `Base.metadata.create_all`, CI 4 джобы (mypy --strict гейт).

## Open questions / зависимости

- **OPS — тест-бот для дева (блокер боевого локального E2E / PR-6a):** Капитан создаёт отдельный тест-бот в BotFather + задаёт Mini App short name на tunnel-URL; токен тест-бота кладём в локальный api `.env`. Прод-`@BiotactBot` не трогаем.
- **OPS — прод-бот к запуску:** перед боевым стартом у прод-`@BiotactBot` задать Mini App short name для Direct Link `startapp`; страховка на случай повреждённого QR = ручной ввод 12-значного кода в миниаппе (ТЗ 5.3).
- Нужен ли random.org signed-draw на пилоте, или commit-reveal достаточно? (Дефолт: commit-reveal достаточно, random.org — позже.)
- Точный формат CSV для конкретной типографии (колонки) — уточняется при выборе типографа (ТЗ 20.2); экспорт делаем гибко-колоночным.
