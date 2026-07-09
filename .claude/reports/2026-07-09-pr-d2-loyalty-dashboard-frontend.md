# Отчёт: PR-D2 — Раздел «Biotact Club» в админ-панели (фронт)

**Дата:** 2026-07-09 · **Кресло:** Claude=Инженер · **Ветка:** `feat/loyalty-dashboard-frontend`
**Спека:** `.claude/specs/2026-07-09-pr-d2-loyalty-dashboard-frontend.md` (Гейт-1 APPROVED WITH CONDITIONS).
**Тип:** фронт-only, read-only. 0 бэкенда, 0 схемы, 0 миграций. Потребляет живой `GET /analytics/loyalty`.

## Сделано — по пунктам спеки
- **Слайс `features/loyalty`** (зеркало dashboard: types → api → hook → components → page):
  - `types.ts` — зеркало `LoyaltyStats` + подтипы 1:1 (сверено с `schemas.py:146`), 0 `any`.
  - `api/loyaltyApi.ts` — `getLoyaltyStats(days)` → `GET /analytics/loyalty` (base `/api/v1`).
  - `hooks/useLoyalty.ts` — `useLoyaltyStats(days)`, `useQuery(['loyalty','stats',days])`, staleTime 5м.
  - `components/`: `LoyaltyStatCard` (тонкая локальная плитка) · `ActivationStats` (герой) ·
    `ActivationTrendChart` (Area) · `MembersStats` · `RegistrationFunnel` · `PointsEconomy` ·
    `DemographicsBreakdown` · `index.ts`.
  - `pages/LoyaltyPage.tsx` (+`index.ts`) — период-Select 7/30/90, loading/error/render, композиция 5 блоков.
- **Проводка (Ф2=B, ратифицирована):** `App.tsx` — lazy `LoyaltyPage` + `<Route path='/loyalty'>`;
  `MainLayout.tsx` — navItem `{ /loyalty, «Biotact Club», Award }`. Рабочий DashboardPage НЕ тронут.
- **3 УИ-НИТа:** НИТ-3 герой «Активировано коробок» / воронка «Активировали (людей)»; НИТ-2
  `phone_verified` subtitle «по всей базе»; НИТ-4 баннер «Малая выборка (N)» над всей секцией демографии.
- **Условия Гейта-1 (МИНОР 1–2):** Ф1 воронка немонотонно-безопасна (масштаб к max, без %-конверсии);
  Ф2 карты RU-подписей для reason/step/gender/bucket + фолбэк `MAP[v] ?? v`.
- **НИТ 3–5 (на усмотрение, вложены):** Ф3 баннер над всей секцией; Ф4 заголовок тренда «Активации за
  {days} дней»; Ф5 герой=коробки all-time vs 7д/30д=события — подписи разделены.
- **Пустые состояния (= прод):** плейсхолдеры по идиоме `OrderStatusChart` во всех чартах; нули в плитках.

## Не сделано / отложено
- CSV-экспорт лоялти — вне v1 (реко, ратифицировано; add later).
- Продвижение `StatsCard` в `shared/ui` — отдельная задача (использован локальный `LoyaltyStatCard`).

## Замечено, не трогаю — вне скоупа
- 2 пред-существующих lint-warning (`react-hooks/incompatible-library`) в `categories/CategoryForm.tsx`
  и `products/ProductForm.tsx` — не мои файлы, не чиню.
- Пред-существующая незакоммиченная правка `docs/biotact-club-concept.html` (не моя) — не трогаю, не коммичу.
- `vendor-charts` >300kB warning на build — пред-существующий (recharts, уже в dashboard), не мой.

## Как тестировал — команды + вывод
Полный локальный CI admin-panel-v2 (ci.yml:77–86), рабочая директория `admin-panel-v2/`:
```
npx tsc -b        → exit 0 (strict; verbatimModuleSyntax/noUncheckedIndexedAccess/exactOptional ок)
npm run lint      → exit 0 (0 errors, 2 warnings — пред-существующие, не мои)
npm run test      → 14 files, 60 passed (мои 21 включены, 0 регрессий)
npm run build     → exit 0; dist/assets/LoyaltyPage-*.js 14.87 kB (lazy-чанк создан)
```
**red→green (правило №0) — доказано мутациями** (тесты писались после кода → показал фальсифицируемость,
затем откат; full-suite 60-green подтверждает чистый откат):
```
Ф1 maxUsers → scale-to-first-step   ⇒ RED: funnel width 40%→100% (1 failed)  → revert
Ф2 fallback `?? step.step` → `?? ''` ⇒ RED: unknown-step тест (1 failed)      → revert
НИТ-4 порог `<` → `>`               ⇒ RED: оба banner-теста (2 failed)         → revert
НИТ-2 subtitle «по всей базе»→другое ⇒ RED: members-тест (1 failed)           → revert
```
Тест-покрытие (21 тест): НИТ-3 подпись · Ф1 немонотонная воронка (бары к max, без отриц. ширин) ·
Ф2 неизвестный enum сырым (funnel+reason) · НИТ-2 квалификатор · НИТ-4 порог N<10 ×2 · gender unknown ·
пустые состояния всех блоков · error-ветка · LoyaltyPage smoke на all-zero (= прод).

## Отклонения от плана и почему
- **Ф2 = B (отдельная страница), НЕ вкладка** — ратифицировано Капитаном на Гейте-1 (Tabs в проекте нет:
  вкладка = новая зависимость + примитив + правка рабочего дашборда; B зеркалит все домены). Слайс —
  новый `features/loyalty` (следствие B), а не расширение dashboard-слайса.
- Дефолты микро-развилок (ратифицированы «go» Капитана): route `/loyalty`, иконка `Award`, label «Biotact Club».
- `/typescript-review` **не запускал сам** — по ратифицированной модели ролей judgment-review принадлежит
  Ревьюеру на Гейте-2 (свежий контекст = независимая верификация). Хук-напоминание закрывается там.

## Вопросы Ревьюеру
1. Ф1: я выбрал оборонительный рендер (масштаб к max) вместо доверия монотонности пайплайна событий —
   согласен, что это правильнее допущения о гарантиях эмиссии событий?
2. НИТ-4 порог 10 (`SMALL_SAMPLE_THRESHOLD`) — устраивает как дефолт, или хочешь другое значение?
3. Локальный `LoyaltyStatCard` vs cross-slice reuse — ок как v1-выбор?
