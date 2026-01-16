# DEV STANDARDS - PROGRESS TRACKER

**Проект:** Biotact
**Последнее обновление:** 2025-12-29

---

## НЕДЕЛЯ 1 - ЗАВЕРШЕНА (100%)

- [x] Named Exports миграция (8 pages)
- [x] ErrorBoundary -> react-error-boundary  
- [x] Переиспользуемые компоненты (Modal, Table, Pagination, FileUpload)
- [x] Products.tsx: 511 -> 247 строк (-52%)

---

## НЕДЕЛЯ 2 - ЗАВЕРШЕНА (100%)

### Выполнено:

- [x] **Тесты:** Починены failing тесты Button.test.tsx (129/129 pass)
- [x] **CI/CD:** GitHub Actions workflow создан (требует токен с workflow scope)
- [x] **Categories.tsx:** 386 -> 95 строк (-75%)
- [x] **Banners.tsx:** 412 -> 137 строк (-67%)

### Итого по рефакторингу Admin Panel:

| Файл | Было | Стало | Улучшение |
|------|------|-------|-----------|
| Products.tsx | 511 | 247 | -52% |
| Categories.tsx | 386 | 95 | -75% |
| Banners.tsx | 412 | 137 | -67% |
| **Всего** | **1309** | **479** | **-63%** |

---

## НЕДЕЛЯ 3 - ПЛАН

### Приоритет 1: CI/CD завершение
- [ ] Добавить workflow scope к GitHub токену
- [ ] Push CI workflow
- [ ] Настроить автодеплой на staging

### Приоритет 2: Тестирование
- [ ] Добавить тесты для admin компонентов
- [ ] Интеграционные тесты API
- [ ] Coverage report

### Приоритет 3: Улучшения
- [ ] Добавить favicon.ico (есть 404 в логах)
- [ ] Error tracking (Sentry?)
- [ ] Performance monitoring

---

## ИЗВЕСТНЫЕ ПРОБЛЕМЫ

### WebView crash на Telegram Desktop
**Симптом:** Error: WebView has crashed
**Причина:** Устаревший WebView2 на Windows
**Решение для пользователей:**
1. Обновить Telegram Desktop
2. Установить WebView2: https://go.microsoft.com/fwlink/p/?LinkId=2124703
3. Или использовать web.telegram.org

---

## КОМАНДЫ

Frontend build:
  cd packages/frontend && npm run build

Admin build:
  cd packages/admin && npm run build

Тесты:
  cd packages/frontend && npm run test

---

**Commit:** 4e576bb
