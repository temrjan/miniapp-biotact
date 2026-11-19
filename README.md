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
│              Запускает Mini App                             │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                 Frontend (React + Vite)                     │
│                  packages/frontend                          │
└─────────────────────┬───────────────────────────────────────┘
                      │ X-Init-Data header
┌─────────────────────▼───────────────────────────────────────┐
│                  API (FastAPI + Python)                     │
│                    packages/api                             │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                  PostgreSQL Database                        │
└─────────────────────────────────────────────────────────────┘
```

## Структура репозитория

```
packages/
├── frontend/              # Mini App: React + Vite + TypeScript
│   └── src/
│       ├── pages/         # Страницы (CheckoutPage, OrdersPage, etc.)
│       ├── api/           # API клиент с X-Init-Data
│       └── stores/        # Zustand stores
├── api/                   # FastAPI backend
│   └── src/
│       ├── auth/          # Telegram X-Init-Data validation
│       ├── orders/        # Заказы
│       ├── payments/      # Multicard интеграция
│       ├── products/      # Товары
│       └── notifications/ # Telegram уведомления
└── bot/                   # Telegram Bot (aiogram)
admin-panel-v2/            # Admin панель: React + Vite + TypeScript
ops/                       # Скрипты выпуска и эксплуатации
ecosystem.config.js        # PM2 конфигурация
```

## Конфигурация

Переменные окружения и их назначение — в `packages/api/.env.example` и
`packages/bot/.env.example`. Настоящие `.env` не хранятся в Git.

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

## Дисциплина миграций (expand-contract)

В одном релизе допускаются только **аддитивные** изменения схемы:
- новые nullable/defaulted колонки;
- новые пустые/неиспользуемые таблицы;
- новые индексы.

Удаление колонок, переименование обязательных полей и другие
**обратно-несовместимые** изменения выносятся в отдельный поздний релиз,
когда старый код уже не работает со старой схемой.

Это нужно, потому что rollback откатывает **только код**, не миграции.
При аддитивных миграциях старый код продолжает работать со старой схемой.

## Известные особенности

1. **WebApp.openLink()** - открывает платёжную страницу во внешнем браузере, поэтому return_url не возвращает в Mini App автоматически.

2. **X-Init-Data** - все API запросы должны использовать клиент из `/api/client.ts`, который добавляет заголовок авторизации.

3. **Два бота** - Mini App бот и Notification бот разные. API использует оба токена для разных целей.
