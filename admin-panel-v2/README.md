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
