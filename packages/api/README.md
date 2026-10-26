# Biotact API

REST API для Telegram Mini App магазина Biotact. Каталог, заказы, платежи, CRM, админка.

## Стек

| Технология | Версия | Назначение |
|------------|--------|------------|
| FastAPI | 0.124 | Web-фреймворк |
| SQLAlchemy | 2.0 | ORM (async, asyncpg) |
| Alembic | 1.17 | Миграции БД |
| Pydantic | 2.12 | Валидация + Settings |
| PostgreSQL | 16 | База данных |
| python-jose | 3.5 | JWT токены |
| httpx | 0.28 | Async HTTP-клиент |
| uvicorn | 0.38 | ASGI-сервер |

## Структура

```
src/
├── main.py              # App factory, роутеры, middleware
├── config.py            # Pydantic Settings (.env)
├── database.py          # SQLAlchemy engine + sessions
├── auth/                # Telegram X-Init-Data + Admin JWT
├── users/               # Профили пользователей Mini App
├── products/            # Каталог товаров (i18n: рус/узб)
├── categories/          # Категории товаров
├── orders/              # Заказы + OrderItems (snapshot pattern)
├── payments/            # Multicard интеграция
├── banners/             # Баннеры на главной
├── crm/                 # Лиды, клиенты, семьи
├── analytics/           # Статистика для дашборда
├── uploads/             # Загрузка изображений
├── notifications/       # Telegram-уведомления о заказах
└── common/              # Логгер, исключения, зависимости
```

## API эндпоинты

### Публичные (без авторизации)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Health check |
| GET | `/api/v1/products` | Список товаров (пагинация, фильтры) |
| GET | `/api/v1/products/{id}` | Товар по ID |
| GET | `/api/v1/products/slug/{slug}` | Товар по slug |
| GET | `/api/v1/categories` | Категории |
| GET | `/api/v1/banners` | Баннеры |
| GET | `/api/v1/settings` | Настройки магазина |

### Mini App (X-Init-Data)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/v1/users/me` | Профиль пользователя |
| PATCH | `/api/v1/users/me/phone` | Обновить телефон |
| PATCH | `/api/v1/users/me/address` | Обновить адрес |
| POST | `/api/v1/orders` | Создать заказ |
| GET | `/api/v1/orders` | Заказы пользователя |

### Платежи

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/v1/payments/create` | Создать платёж (Multicard) |
| POST | `/api/v1/payments/callback` | Webhook от Multicard |
| GET | `/api/v1/payments/status/{uuid}` | Статус платежа |

### Админка (JWT Bearer)

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/v1/auth/login` | Логин (email + пароль) |
| POST | `/api/v1/auth/register` | Регистрация админа |
| POST | `/api/v1/auth/refresh` | Обновить access token |
| GET | `/api/v1/auth/me` | Текущий админ |
| POST | `/api/v1/products` | Создать товар |
| PATCH | `/api/v1/products/{id}` | Обновить товар |
| DELETE | `/api/v1/products/{id}` | Удалить товар |
| GET | `/api/v1/admin/orders` | Все заказы |
| PATCH | `/api/v1/admin/orders/{id}/status` | Сменить статус |
| GET | `/api/v1/crm/customers` | Клиентская база |

## База данных

| Таблица | Назначение |
|---------|------------|
| users | Пользователи Mini App (telegram_id, телефон, адрес) |
| admin_users | Админы (email, password_hash, JWT) |
| products | Товары (i18n, OFD-поля, JSONB images) |
| categories | Категории (i18n) |
| orders | Заказы (статус, оплата, доставка) |
| order_items | Позиции заказа (snapshot: имя + цена на момент заказа) |
| leads | CRM-лиды от AI-ассистента |
| family_members | Семья клиента (симптомы, возраст) |
| banners | Баннеры на главной |

## Авторизация

**Telegram Mini App** — заголовок `X-Init-Data`, HMAC-SHA256 валидация с токеном бота, проверка `auth_date` (24 часа).

**Админ-панель** — JWT Bearer Token. Access token (30 мин) + Refresh token (7 дней). Алгоритм HS256.

**Бот** — заголовок `X-Bot-Token`, сравнение с токеном бота.

## Команды

```bash
pip install -r requirements.txt   # Установка
alembic upgrade head              # Миграции
uvicorn src.main:app --port 3001  # Запуск
ruff check src/                   # Линтер
mypy src/                         # Проверка типов
pytest tests/ -v                  # Тесты
```

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | PostgreSQL (asyncpg) |
| `SECRET_KEY` | HMAC для админ-токенов |
| `JWT_SECRET` | Подпись JWT |
| `TELEGRAM_BOT_TOKEN` | Токен Mini App бота |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Токен бота уведомлений |
| `TELEGRAM_CHAT_ID` | Chat ID для уведомлений |
| `MULTICARD_APPLICATION_ID` | Multicard ID |
| `MULTICARD_SECRET` | Multicard секрет |
| `MULTICARD_STORE_ID` | Multicard магазин |
| `CORS_ORIGINS` | Разрешённые домены |
| `SENTRY_DSN` | Sentry (опционально) |


## Browser Telegram sign-in (disabled by default)

The existing buyer session issuer owns browser authentication. Telegram entry uses
`BUYER_SESSIONS_ENABLED=true` and `BUYER_TELEGRAM_AUTH_ENABLED=true`, a separate
`BUYER_PROTECTION_KEY`, the exact HTTPS `BUYER_PUBLIC_ORIGIN`, trusted ingress peers,
`BUYER_TELEGRAM_BOT_USERNAME` and the main `TELEGRAM_BOT_TOKEN`. Email entry remains
independently gated by `BUYER_EMAIL_AUTH_ENABLED`; Telegram entry needs no SMTP.
The matching bot entry switch and its trusted HTTPS API URL must be configured
before any separately authorized activation. Startup validation makes no Telegram call.

Successful POST `/api/v1/buyer/auth/context` retains its CSRF/expiry fields and
adds boolean `telegram_enabled`; clients show Telegram only when it is true.

Browser routes under `/api/v1/buyer/auth/telegram` are POST `requests` (request_key),
`status`, `cancel` and `complete` (request_id). Each needs the initiating context and
its CSRF token. Only completion issues the existing protected buyer cookie. The
main-bot bridge exposes POST `/api/v1/bot/buyer/telegram/present` and `/decision`,
authenticated by `X-Bot-Token`. Bridge actor IDs never come from browser input.
The same protected bridge accepts POST `/retry-after` with strict
`{telegram_id,retry_after}` (positive integer seconds), returning `{}`. This reports
a bot-observed Telegram throttle and only extends the actor's existing shared SQL
prompt cooldown. It creates no request, customer, decision or session. Direct
API sends use the same cooldown; stronger restrictions remain effective. No
automatic resend is scheduled.
GET `/api/v1/buyer/auth/club` returns only the current buyer's existing Club summary;
it does not enrol a member or authorize business/admin writes.

A completed context cannot initiate another account. Explicit logout revokes its
completed session even if the successful cookie response was lost. After cookie
clearing, obtain a fresh context before another login. A delayed old cookie may
interrupt the newer browser session but cannot authorize the revoked account.

Migration `d8a14f90bc21` is additive on PostgreSQL 16. Populated downgrade is refused;
retain the new head and repair forward. After disabling/quiescing Telegram entry,
run `python -m src.buyer_auth.cleanup --cancel-telegram` repeatedly until it returns
zero before re-enabling. A flag change alone does not cancel pending approvals.
The default `python -m src.buyer_auth.cleanup` removes at most 500 eligible retained
proofs, sessions and expired limits per invocation; schedule that existing CLI in
an authorized operational environment. It retains completed context barriers and
active session/customer state. Keep entry disabled until paired source/schema,
CI, HTTPS cookies and designated-account delivery have been verified for activation.
