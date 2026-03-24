# Biotact Frontend — Telegram Mini App

Telegram Mini App для продажи БАДов и товаров ЗОЖ. Работает внутри Telegram через WebApp SDK.

## Стек

| Технология | Версия | Назначение |
|------------|--------|------------|
| React | 19 | UI |
| Vite | 7 | Сборка |
| TypeScript | 5.9 | Типизация (strict) |
| Zustand | 5 | Состояние (persist + devtools) |
| TanStack Query | 5 | Серверный кеш |
| Tailwind CSS | 4.1 | Стили |
| Framer Motion | 12 | Анимации |
| Telegram WebApp SDK | 8.0 | Интеграция с Telegram |

## Страницы

| Страница | Путь | Описание |
|----------|------|----------|
| Главная | `/` | Баннеры, товары по категориям, FAQ |
| Каталог | `/catalog` | Фильтр по категориям, пагинация |
| Товар | `/product/:id` | Галерея, описание, добавление в корзину |
| Корзина | `/cart` | Список товаров, изменение количества |
| Оформление | `/checkout` | Геолокация, телефон, адрес, оплата |
| Заказы | `/orders` | История заказов со статусами |
| Профиль | `/profile` | Данные пользователя, язык |

## Структура

```
src/
├── pages/           # 9 страниц
├── components/
│   ├── ui/          # Button, Card, BottomSheet, Skeleton, ErrorBoundary
│   ├── features/    # ProductBottomSheet, LocationModal, PhoneInput
│   └── layouts/     # MainLayout (навигация, хедер)
├── stores/          # appStore, cartStore, userStore, locationStore, languageStore
├── api/             # HTTP-клиент с X-Init-Data авторизацией
├── hooks/           # useTelegram, useBackButton, useMainButton, useGeolocation
├── i18n/            # Переводы (русский + узбекский)
├── utils/           # Константы, геокодинг, валидация
└── types/           # TypeScript типы
```

## Команды

```bash
npm ci               # Установка зависимостей
npm run dev          # Dev-сервер (порт 5173)
npm run build        # Сборка (tsc + vite build)
npm run test         # Тесты (vitest, 129 тестов)
npm run lint         # ESLint
npm run type-check   # tsc --noEmit
npm run format       # Prettier
```

## Авторизация

Все API-запросы отправляют заголовок `X-Init-Data` с данными Telegram WebApp. Бэкенд валидирует подпись через HMAC-SHA256.

## Локализация

Поддержка русского и узбекского языков. Переключение через профиль. Заголовок `Accept-Language` передаётся в API.

## Особенности

- Корзина сохраняется в localStorage через Zustand persist
- Геолокация через браузер + Nominatim для обратного геокодинга
- Haptic feedback через Telegram SDK
- Neumorphic дизайн-система с поддержкой тёмной темы
- Code splitting: vendor, state, ui чанки
