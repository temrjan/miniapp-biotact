# Biotact Admin Panel

Панель управления магазином Biotact. Управление товарами, заказами, пользователями и аналитика.

## Стек

| Технология | Версия | Назначение |
|------------|--------|------------|
| React | 19 | UI |
| Vite | 7 | Сборка |
| TypeScript | 5.9 | Типизация (strict) |
| Radix UI | — | Компоненты (Dialog, Select, Toast и др.) |
| Tailwind CSS | 3.4 | Стили |
| React Hook Form + Zod | 7 / 4 | Формы и валидация |
| TanStack Query | 5 | Серверный кеш |
| Zustand | 5 | Авторизация (persist) |
| Recharts | 3.6 | Графики |
| Axios | 1.13 | HTTP-клиент |

## Функции

| Раздел | Возможности |
|--------|-------------|
| Dashboard | Статистика, график выручки, последние заказы |
| Товары | CRUD, мультиязычность (рус/узб), загрузка изображений, drag-reorder, OFD-поля |
| Акции | Draft editor, saved-record publication, retained End, explicit uncertain-result recovery |
| Категории | CRUD, изображения, сортировка |
| Баннеры | CRUD, загрузка изображений, интервал ротации |
| Заказы | Список с фильтрами, детали заказа, смена статуса |
| Пользователи | Список, детали, управление ролями |
| CRM | Клиентская база, статистика, история покупок |
| Настройки | Название магазина, контакты, доставка, валюта |

## Структура

```
src/
├── app/
│   ├── layouts/     # MainLayout (сайдбар, тема, логаут)
│   ├── providers/   # QueryProvider, ThemeProvider
│   └── routes/      # PrivateRoute (auth guard)
├── features/        # Domain-driven модули
│   ├── auth/        # Логин, JWT-хранилище
│   ├── dashboard/   # Графики, статистика
│   ├── products/    # Список, создание, редактирование
│   ├── categories/  # CRUD
│   ├── banners/     # CRUD + upload
│   ├── orders/      # Список, детали, статусы
│   ├── users/       # Управление пользователями
│   ├── customers/   # CRM
│   └── settings/    # Настройки магазина
└── shared/
    ├── api/         # Axios клиент + JWT interceptor
    ├── components/  # UI (Radix), ImageUploader, ErrorBoundary
    └── types/       # Общие TypeScript типы
```

## Команды

```bash
npm ci               # Установка зависимостей
npm run dev          # Dev-сервер
npm run build        # Сборка (tsc + vite build)
npm run test         # Тесты (vitest)
npm run lint         # ESLint
```

## Авторизация

JWT Bearer Token. Логин по email/паролю через `/api/v1/auth/login`. Токен хранится в Zustand (localStorage). Axios interceptor добавляет `Authorization` заголовок. При 401 — редирект на логин.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `VITE_API_URL` | `/api/v1` | Базовый URL API |

## Campaign administration

The **Акции** menu opens `/campaigns`. Create a Draft at `/campaigns/new`, then
open its saved record at `/campaigns/:id`. A name alone is sufficient for a Draft.
Save edits before using the separate Publish confirmation. Published content is
immutable; End retains its record and history. Product selection only reads the
catalogue and does not change prices, sale flags or stock.

All campaign dates display and edit in **Tashkent · UTC+05:00**, independently of
the browser time zone. Manual images must be HTTPS shop-upload URLs; the server
checks their exact origin. The existing uploader accepts one file up to 5 MB for
campaigns. Clearing the image field does not delete the uploaded file.

An unavailable campaign connection shows an error and disables campaign writes.
Other admin navigation remains available. No campaign service credentials belong
in browser environment variables or builds.

A lost write response blocks further campaign writes in the same tab, including
after reload. Use the explicit fresh record/list inspection and confirmation.
Current state and matching names do not prove the original write's outcome.
Creating another Draft after inspection may duplicate a successful first create;
the follow-up opens a blank form and never resends the original payload.

See [the campaign feature guide](src/features/campaigns/README.md) for contracts,
recovery behavior and validation commands.
