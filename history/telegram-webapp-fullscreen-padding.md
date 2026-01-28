# Отступ header в Telegram WebApp fullscreen режиме

## Синопсис

**Проблема:** В Telegram WebApp на мобильных устройствах при открытии через кнопку "Open" (fullscreen) header приложения перекрывался native header Telegram. При открытии через inline кнопку — всё работало корректно.

**Решение:** Динамический CSS padding-top через переменную `--tg-content-safe-area-inset-top`, которая устанавливается только для mobile + fullscreen режима.

---

## Дата
2026-01-23

## Платформа
- Telegram WebApp (Mini App)
- Android / iOS
- React + Vite + TypeScript

## Описание проблемы

### Симптомы
1. При открытии WebApp через синюю кнопку "Open" (fullscreen режим) — зелёный header приложения (с логотипом BIOTACT) оказывался под native header Telegram (с крестиком закрытия и названием бота)
2. При открытии через inline кнопку в чате (sheet режим) — всё отображалось корректно
3. На desktop — проблемы не было

### Скриншоты
- `photo_2026-01-23_16-37-18.jpg` — fullscreen режим (проблема)
- `photo_2026-01-23_16-37-19.jpg` — inline режим (корректно)

### Причина
Telegram WebApp в fullscreen режиме на мобильных устройствах имеет native header (~56px), который перекрывает контент приложения. Необходим отступ сверху, но только в этом конкретном режиме.

---

## Решение

### Архитектура

```
┌─────────────────────────────────┐
│   Telegram Native Header        │  ← ~56px (крестик, название бота)
├─────────────────────────────────┤
│   padding-top: 56px (только     │  ← Динамический отступ
│   mobile + fullscreen)          │
├─────────────────────────────────┤
│   App Header (зелёный)          │  ← sticky top-0
├─────────────────────────────────┤
│   Content                       │
└─────────────────────────────────┘
```

### Логика определения режима

| Платформа | Режим | Отступ |
|-----------|-------|--------|
| Mobile (iOS/Android) | Fullscreen (кнопка Open) | 56px |
| Mobile (iOS/Android) | Inline (кнопка в чате) | 0px |
| Desktop | Любой | 0px |

### Определение fullscreen
Используется соотношение viewport к screen height:
- `ratio >= 0.95` → fullscreen
- `ratio < 0.95` → sheet/inline

---

## Изменённые файлы

### 1. index.html
```html
<style>
  #root {
    padding-top: var(--tg-content-safe-area-inset-top, 0px);
  }
</style>
<script>
  (function() {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (!tg) return;

    var platform = tg.platform;
    var isMobile = platform === 'ios' || platform === 'android' || platform === 'android_x';
    if (!isMobile) return;

    var viewportHeight = tg.viewportStableHeight || tg.viewportHeight || window.innerHeight;
    var screenHeight = window.screen.height;
    var ratio = viewportHeight / screenHeight;

    var isFullscreenMode = ratio >= 0.95;

    if (isFullscreenMode) {
      document.documentElement.style.setProperty('--tg-content-safe-area-inset-top', '56px');
    }
  })();
</script>
```

### 2. src/styles/global.css
```css
#root {
  margin: 0;
  padding: var(--tg-content-safe-area-inset-top, 0px) 0 0 0;
  min-height: 100vh;
}
```

### 3. src/main.tsx
```typescript
// Отключить вертикальные свайпы (закрытие жестом)
if (typeof WebApp.disableVerticalSwipes === 'function') {
  WebApp.disableVerticalSwipes();
}
```

---

## Важные замечания

1. **CSS приоритет:** Изначально была ошибка — в global.css `padding: 0` перезаписывал `padding-top`. Решено через `padding: var(...) 0 0 0`

2. **viewport-fit=cover:** Добавлен в meta viewport для корректной работы с safe-area на iOS

3. **Кэширование:** Telegram может кэшировать WebApp — для проверки изменений нужно полностью закрыть и открыть бота

---

## Связанные изменения в этой сессии

1. Добавлена статистика пользователей в Dashboard админ-панели
2. Баннер на главной: убрано скругление на mobile, добавлен skeleton при загрузке
3. Исправлен отступ header в Telegram WebApp fullscreen

