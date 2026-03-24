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
