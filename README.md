# Biotact - Telegram Mini App E-commerce

## Обзор проекта

Telegram Mini App для продажи БАДов (биологически активных добавок) с интегрированной платёжной системой Multicard.

## Карта фич

Таблица — что где живёт. Подробности по каждой фиче — в её feature-README (если есть),
иначе смотри код по указанному пути.

### Backend (`packages/api/src/`)

| Фича | Путь | Feature README | Что делает |
|------|------|----------------|------------|
| Authorization | `auth/` | [README](packages/api/src/auth/README.md) | Customer initData; named/compat password and Telegram-code administrator JWT with current rights |
| Товары | `products/` | — | CRUD, поиск, категории, featured |
| Категории | `categories/` | — | Дерево категорий |
| Заказы | `orders/` | — | Корзина → заказ → статусы |
| Платежи | `payments/` | — | Multicard: создание invoice + callback |
| Пользователи | `users/` | — | Telegram users + админы (`is_admin`) |
| Баннеры | `banners/` | — | Главный экран Mini App |
| Аналитика | `analytics/` | — | Метрики заказов, выручка, топ-товары |
| Рассылки | `broadcasts/` | [README](packages/api/src/broadcasts/README.md) | Массовая рассылка с фото/видео/кнопкой |
| CRM | `crm/` | — | Операторский роутер для звонков |
| Уведомления | `notifications/` | — | Telegram-оповещения админам о заказах |
| Загрузки | `uploads/` | — | Upload изображений товаров |
| Campaign administration | `campaigns/` | [README](packages/api/src/campaigns/README.md) | Current shop admin authorization and bounded server-only portal proxy |
| Employee referral registry/capture | `employee_referrals/` | [README](packages/api/src/employee_referrals/README.md) | K1/capture, retained paid/returns, K2 worker and independent notices; activation OFF by default |

### Admin Panel (`admin-panel-v2/src/features/`)

| Раздел | Путь |
|--------|------|
| Dashboard | `dashboard/` |
| Заказы | `orders/` |
| Товары | `products/` |
| Категории | `categories/` |
| Клиенты | `customers/` |
| Пользователи | `users/` |
| Баннеры | `banners/` |
| **Рассылки** | `broadcasts/` — см. [Broadcasts README](packages/api/src/broadcasts/README.md) |
| Настройки | `settings/` |
| Авторизация | `auth/` |

### Mini App (`packages/frontend/src/pages/`)

| Страница | Файл |
|----------|------|
| Главная | `HomePage.tsx` |
| Каталог | `CatalogPage.tsx` |
| Карточка товара | `ProductPage.tsx` |
| Корзина | `CartPage.tsx` |
| Оформление | `CheckoutPage.tsx` |
| Мои заказы | `OrdersPage.tsx` |
| Рамадан-распродажа | `RamadanPage.tsx` |

### Telegram Bot (`packages/bot/bot/handlers/`)

| Handler | Что делает |
|---------|------------|
| `common.py` | `/start`, deeplinks, video file_id reply для админа |
| `auth.py` | Авторизация для админки по email-коду |

---

## Добавление feature-README

Когда реализуем новую значимую фичу (или углубляем существующую),
добавляем `README.md` рядом с кодом и ссылку сюда в таблицу.

Шаблон содержимого: архитектура, как пользоваться, HTTP endpoints,
где что хранится, ограничения, troubleshooting.
Пример: [`packages/api/src/broadcasts/README.md`](packages/api/src/broadcasts/README.md).

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                     Telegram Bot                            │
│                   (7748404469)                              │
│              Запускает Mini App                             │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                 Frontend (React + Vite)                     │
│                  app.biotact.uz                             │
│         /var/www/biotact/packages/frontend                  │
└─────────────────────┬───────────────────────────────────────┘
                      │ X-Init-Data header
┌─────────────────────▼───────────────────────────────────────┐
│                  API (FastAPI + Python)                     │
│                   :3001 → Nginx                             │
│           /var/www/biotact/packages/api                     │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  PostgreSQL Database                        │
│                     biotact_db                              │
└─────────────────────────────────────────────────────────────┘
```

## Сервер

- **IP:** 95.111.224.251
- **SSH порт:** 2222
- **SSH ключ:** ~/.ssh/claude_migration
- **OS:** Ubuntu 24.04 LTS

## Домены

| Домен | Назначение |
|-------|------------|
| app.biotact.uz | Frontend Mini App |
| admin.biotact.uz | Admin панель |
| multicard.biotact.uz | Proxy для платёжного callback |

## Структура проекта

```
/var/www/biotact/
├── packages/
│   ├── frontend/          # React + Vite + TypeScript
│   │   ├── src/
│   │   │   ├── pages/     # Страницы (CheckoutPage, OrdersPage, etc.)
│   │   │   ├── api/       # API клиент с X-Init-Data
│   │   │   └── stores/    # Zustand stores
│   │   ├── dist/          # Build output (nginx root)
│   │   └── .env           # VITE_API_URL=/api/v1
│   │
│   ├── api/               # FastAPI backend
│   │   ├── src/
│   │   │   ├── auth/      # Telegram X-Init-Data validation
│   │   │   ├── orders/    # Заказы
│   │   │   ├── payments/  # Multicard интеграция
│   │   │   ├── products/  # Товары
│   │   │   └── notifications/  # Telegram уведомления
│   │   ├── .env           # Конфигурация
│   │   └── venv/          # Python virtual environment
│   │
│   └── bot/               # Telegram Bot (aiogram)
│       ├── .env           # BOT_TOKEN
│       └── venv/
│
└── ecosystem.config.js    # PM2 конфигурация
```

## PM2 процессы

```bash
pm2 status

# biotact namespace:
# - api         # FastAPI на порту 3001
# - bot         # Telegram бот
# - employee-referral-worker # K2 и независимые campaign notices

# bots namespace:
# - multicard-backend     # Платёжный proxy
# - multicard-telegram    # Telegram listener для Multicard
# - protocols-bot         # Бот сертификатов
# - csv2txt               # CSV конвертер бот
```

## Telegram боты

| Бот | Token ID | Назначение |
|-----|----------|------------|
| Mini App Bot | 7748404469 | Основной бот, запускает Mini App, валидация X-Init-Data |
| Notification Bot | 8175184075 | Отправка уведомлений админам о заказах |

## Конфигурация API (.env)

```env
# Валидация X-Init-Data (Mini App бот)
TELEGRAM_BOT_TOKEN=7748404469:...

# Уведомления (отдельный бот)
TELEGRAM_NOTIFICATION_BOT_TOKEN=8175184075:...
TELEGRAM_CHAT_ID=-1002975914107

# База данных
DATABASE_URL=postgresql+asyncpg://biotact:...@localhost:5432/biotact_db

# Multicard платежи
MULTICARD_APPLICATION_ID=biotact
MULTICARD_SECRET=...
MULTICARD_STORE_ID=2435
```

## Авторизация пользователей

Frontend отправляет заголовок `X-Init-Data` с каждым запросом через API клиент (`/api/client.ts`).

Backend валидирует X-Init-Data через HMAC-SHA256 с токеном Mini App бота.

**ВАЖНО:** API `TELEGRAM_BOT_TOKEN` должен совпадать с токеном бота, который запускает Mini App!

## Платёжный процесс

1. Пользователь оформляет заказ → POST /api/v1/orders
2. Создаётся invoice в Multicard → POST /api/v1/payments/create
3. Пользователь переходит на payment_url (WebApp.openLink)
4. После оплаты Multicard вызывает callback → POST /api/v1/payments/callback
5. Заказ обновляется, уведомление отправляется в Telegram

## Деплой и миграции

Деплой запускается GitHub Actions (`deploy.yml`) автоматически после успешного CI на push в `main`.

Чтобы не деплоить на каждую мелочь, CI (а значит, и деплой) пропускает изменения,
которые касаются только документации: `*.md`, `docs/**`, `.claude/**`, `.kimi/**`.

Перед началом деплоя проверяется наличие обязательных секретов: `DEPLOY_SSH_KEY`,
`DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`.

### Порядок деплоя

1. Сохранить предыдущую ревизию и остановить affected API/employee-referral-worker writers.
2. Подтянуть код и собрать frontend/admin, установить зависимости Python.
3. Выполнить `alembic upgrade head` при остановленных writers.
4. `bash ops/employee-referral-release.sh start`: named worker startOrRestart,
   API restart, actual worker runtime/health и `pm2 save`; затем прежний bot restart.
5. Проверить `/health`, `/api/v1/products` и worker runtime/health.

Подробные default-OFF настройки, CLI, cutover и ограничения — в
[feature runbook](packages/api/src/employee_referrals/README.md#k2-delivery-and-independent-campaign-notices).

### Дисциплина миграций (expand-contract)

В одном релизе допускаются только **аддитивные** изменения схемы:
- новые nullable/defaulted колонки;
- новые пустые/неиспользуемые таблицы;
- новые индексы.

Удаление колонок, переименование обязательных полей и другие
**обратно-несовместимые** изменения выносятся в отдельный поздний деплой,
когда старый код уже не работает со старой схемой.

Это нужно, потому что rollback откатывает **только код**, не миграции.
При аддитивных миграциях старый код продолжает работать со старой схемой.

### Откат (rollback)

При неудаче deployment возвращает только совместимый код и проверяет named worker.
До schema cutover допустим pre-worker rollback с удалением только нового worker.
После cutover возврат к producer без delivery hooks запрещён: affected API/worker
остаются paused до установки совместимого head. Facts/jobs/schema сохраняются;
production downgrade не выполняется. Ошибка health не считается успешным выпуском.
При совместимом rollback frontend/admin пересобираются из предыдущей ревизии,
поскольку `dist/` не хранится в Git. См. feature runbook и named ops helper.

## База данных

```bash
sudo -u postgres psql -d biotact_db

# Основные таблицы:
# - users (telegram_id, first_name, phone)
# - orders (order_number, user_id, total, payment_status)
# - order_items
# - products
# - categories
```

## Частые операции

### Перезапуск сервисов
```bash
pm2 restart api
pm2 restart bot
pm2 restart all
```

### Просмотр логов
```bash
pm2 logs api --lines 50
pm2 logs bot --lines 50
pm2 logs --lines 50
```

### Пересборка frontend
```bash
cd /var/www/biotact/packages/frontend
npm run build
```

### Проверка заказов
```bash
sudo -u postgres psql -d biotact_db -c "SELECT id, order_number, user_id, payment_status, created_at FROM orders ORDER BY id DESC LIMIT 10;"
```

## Известные особенности

1. **WebApp.openLink()** - открывает платёжную страницу во внешнем браузере, поэтому return_url не возвращает в Mini App автоматически.

2. **X-Init-Data** - все API запросы должны использовать клиент из `/api/client.ts`, который добавляет заголовок авторизации.

3. **Два бота** - Mini App бот и Notification бот разные. API использует оба токена для разных целей.

## Backup

Cron job для protocols-bot:
```
0 3 * * * /opt/bots/protocols-bot/scripts/backup.sh
```

## SSL сертификаты

Certbot (Let's Encrypt), автообновление. Истекают 2026-04-16.

```bash
certbot certificates
certbot renew --dry-run
```
