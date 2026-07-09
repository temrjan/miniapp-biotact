# PR-D2 — Раздел «Biotact Club» в админ-панели (фронт) — Гейт-1 дизайн-мини

**Дата:** 2026-07-09 · **Статус:** DISCOVER → ожидает Гейт-1 (Ревьюер свежим контекстом через Капитана).
**Кресло:** Claude=Инженер. **Тип:** новая read-only админ-фича (визуализация лоялти-аналитики).
**Природа:** фронт-only. НЕ трогает бэкенд, НЕ трогает пользовательский лоялти-путь, 0 миграций.
Потребляет уже живой в проде `GET /api/v1/analytics/loyalty → LoyaltyStats` (PR-D1 #72 `b69c21d`).

---

## 1. Цель
Дать администратору внутри админ-панели видеть жизнь Biotact Club: активации QR, участников,
воронку регистрации, экономику баллов, демографию — на данных уже живого эндпоинта. Прод сейчас
почти пуст (qr used=0, profiles=1, phone_verified=0) — **пустое состояние = дефолтный вид v1**,
а не край. Прибор оживёт в момент тиража коробок.

## 2. Вход (что уже есть, проверено чтением)
- **Бэкенд:** `GET /analytics/loyalty` за `require_admin`, контракт `LoyaltyStats`
  (`packages/api/src/analytics/schemas.py:146`). Поля/подтипы — см. §5.
- **Эталон-слайс:** `admin-panel-v2/src/features/dashboard` = `types.ts → api/dashboardApi.ts
  (axios `apiClient`) → hooks/useDashboard.ts (TanStack `useQuery`) → components (StatsCard/recharts) → pages/DashboardPage.tsx`.
- **apiClient base** = `/api/v1` (`src/shared/api/client.ts:3`) → путь запроса `'/analytics/loyalty'`.
- **Стек фронта (проверено `package.json`):** React 19, recharts ^3.6, framer-motion ^12,
  lucide-react, @tanstack/react-query ^5.90, Radix (checkbox/dialog/select/separator/switch),
  vitest ^4.1 + @testing-library/react. Админ-панель **RU-only хардкод** (i18n нет — как в dashboard).
- **Эталон пустого состояния (идиома проекта):** `OrderStatusChart.tsx:29`
  `if (data.length === 0) return <Card>…«Нет данных»…</Card>`.

---

## 3. Ф2 — развилка интеграции (РЕШЕНА мной, несу Капитану на ратификацию)

> Реко из спеки PR-D1 была «вкладка Radix Tabs «Магазин | Biotact Club»». Разведка вскрыла факт,
> меняющий цену развилки — поэтому пересобираю рекомендацию (правило №7/№14, effectiveness-over-plan).

**🔑 Факт (проверено):** в проекте **НЕТ Tabs**: `@radix-ui/react-tabs` не в `package.json`,
`shared/components/ui/tabs.tsx` отсутствует. Плюс **каждый** домен админки — отдельная страница
сайдбара: Заказы `/orders`, **CRM `/customers`**, Пользователи `/users`, Товары `/products`
(`MainLayout.tsx:30` navItems + `App.tsx` `<Route>`). Вкладок в админке нет нигде.

| | **A) Вкладка внутри DashboardPage** | **B) Отдельная страница `/loyalty` + пункт сайдбара** |
|---|---|---|
| Новая зависимость | **да** (`@radix-ui/react-tabs`) | нет |
| Новый shared-примитив | **да** (`ui/tabs.tsx`) | нет |
| Трогает рабочий orders-дашборд | **да** (обернуть JSX в `<Tabs>`) | **нет** (0 правок) |
| Селектор периода / CSV | коллизия: у orders свой период (выручка), у лоялти свой (активации) — на одном экране два смысла | у каждой страницы свой, изолирован |
| Deep-link | нет (состояние вкладки эфемерно) | есть (`/loyalty`, закладка) |
| Консистентность с проектом | ломает (вкладок нет) | зеркалит все домены |
| Слайс-локация | расширить `features/dashboard` | новый `features/loyalty` (зеркалит внутр. паттерн dashboard) |

**Моя реко: B — отдельная страница `/loyalty`, пункт сайдбара «Biotact Club».**
- **Почему (просто):** вкладка требует новую зависимость + новый UI-примитив + переписать рабочий
  orders-дашборд — больше скоупа и риска ради read-only прибора; отдельная страница не трогает
  ничего рабочего и повторяет то, как уже устроен каждый домен (CRM — тоже отдельная страница, хотя
  это «аналитика по клиентам»). «В админке видно Клуб» одинаково выполняется пунктом сайдбара «Biotact Club».
- **Контраргумент против себя:** ментальная модель Капитана — «в дашборде», один экран; вкладка = на
  один пункт сайдбара меньше. — Не перевесил: пункт «Biotact Club» рядом с «CRM» так же «в админке»,
  а разделение orders-период vs loyalty-период на одном экране под вкладками создаёт двусмысленный
  контрол (два «за 30 дней» с разным смыслом).
- **Цена ошибки — низкая, обратимо:** слайс (types/api/hook/components) идентичен в обоих вариантах;
  различается только точка монтирования — `<Route>`+navItem (B) vs `<TabsContent>` в DashboardPage (A).
  Переключить потом = маленький локальный рефактор. Данные/схема/деньги не затронуты.
- **Факт-переключатель:** Капитан хочет именно единый экран-вкладку (принять новую зависимость
  `@radix-ui/react-tabs` + `tabs.tsx` + обёртку рабочего дашборда) — тогда A.

**⚠️ Ратификация Ф2 = одновременно ратификация слайс-локации** (A ⟹ живёт в dashboard-слайсе;
B ⟹ новый `features/loyalty` слайс). Это отклонение от формулировки хэндоффа «расширить
dashboard-слайс» — сознательное, по факту выше. **Жду решение Капитана перед кодом.**

Ниже спека написана под реко **B**; при выборе A слайс тот же, меняется только монтаж (навешу вкладку).

---

## 4. Архитектура слайса `features/loyalty` (зеркало dashboard: types → api → hook → components → page)

```
admin-panel-v2/src/features/loyalty/
├── types.ts                         # зеркало LoyaltyStats + подтипы (§5), 0 any
├── api/loyaltyApi.ts                # loyaltyApi.getLoyaltyStats(days) → GET /analytics/loyalty
├── hooks/useLoyalty.ts             # useLoyaltyStats(days) → useQuery(['loyalty','stats',days], staleTime 5м)
├── components/
│   ├── ActivationStats.tsx          # Блок 1 герой: StatsCard-плитки (generated/activated/rate/batches/7д/30д)
│   ├── ActivationTrendChart.tsx     # Блок 1 тренд: AreaChart activations_by_day (зеркало RevenueChart) + empty
│   ├── MembersStats.tsx             # Блок 2: profiles_total / friends / phone_verified (НИТ-2 подпись)
│   ├── RegistrationFunnel.tsx       # Блок 3: воронка (bar) + consents_ever_granted (НИТ-3 подпись) + empty
│   ├── PointsEconomy.tsx            # Блок 4: earned/spent/outstanding + points_by_reason (bar) + empty
│   ├── DemographicsBreakdown.tsx    # Блок 5: gender/age/cities/coverage (НИТ-4 малый N) + empty
│   ├── LoyaltyStatCard.tsx          # тонкая стат-плитка (см. решение ниже)
│   └── index.ts
└── pages/
    ├── LoyaltyPage.tsx              # период-Select (зеркало dashboard) + loading/error + композиция блоков
    └── index.ts
```
Проводка (2 правки рабочих файлов, обе — зеркало существующих строк, не переписывание):
- `src/App.tsx`: +`const LoyaltyPage = lazy(...)` и один `<Route path='/loyalty'>` (копия блока `/customers`).
- `src/app/layouts/MainLayout.tsx`: +1 элемент в `navItems` (`{ href:'/loyalty', label:'Biotact Club', icon:<lucide> }`).

**Микро-решение — стат-плитка (в скоупе, дефолт помечен):** `dashboard/components/StatsCard` и
`customers/components/StatsCards` — каждый слайс держит СВОЮ плитку (де-факто конвенция проекта =
per-slice). Чтобы не создавать cross-feature import (`loyalty → dashboard` = смелл feature-slicing) и
не расширять скоуп продвижением StatsCard в `shared/ui` (правка рабочего dashboard), делаю тонкий
локальный `LoyaltyStatCard` поверх shared `Card`/`Skeleton` (зеркало StatsCard, ~40 строк).
Альтернатива «поднять StatsCard в shared/ui» = отдельная задача, помечено в §8 (не в скоупе).

**Тема/чарты:** recharts + токены `hsl(var(--primary/--foreground/--border))` как в RevenueChart →
light/dark работают из коробки. framer-motion — как в эталоне (по желанию, не обязателен).

---

## 5. Контракт (зеркалим `LoyaltyStats` 1:1, имена полей = как в backend-схеме)

```ts
// types.ts — 0 any, interface для объектов
interface ActivationDataPoint { date: string; count: number }              // YYYY-MM-DD
interface FunnelStep          { step: string; users: number }               // joined|phone_shared|qr_activated|survey_done
interface PointsByReason      { reason: string; total: number }
interface GenderCount         { gender: string; count: number }             // 'unknown' = не указан
interface AgeBucketCount      { bucket: string; count: number }             // 18-24|25-34|35-44|45-54|55+|unknown
interface CityCount           { city: string; count: number }
interface Demographics {
  gender: GenderCount[]; age_buckets: AgeBucketCount[]; top_cities: CityCount[]; survey_coverage: number
}
interface LoyaltyStats {
  qr_generated: number; qr_activated: number; qr_activated_7d: number; qr_activated_30d: number
  activation_rate: number; batches_count: number; activations_by_day: ActivationDataPoint[]
  profiles_total: number; phone_verified: number; friends: number
  funnel: FunnelStep[]; consents_ever_granted: number
  points_earned: number; points_spent: number; points_outstanding: number; points_by_reason: PointsByReason[]
  demographics: Demographics
}
```
`days: number = 30` в `getLoyaltyStats(days)` (окно тренда `activations_by_day`), зеркало dashboard.
Селектор периода — те же опции 7/30/90, что в DashboardPage.

**Блок→UI:**
- **1 Активации (герой):** плитки `qr_activated` (крупнейшая, первая) · `qr_generated` · `activation_rate`% ·
  `batches_count` · `qr_activated_7d` · `qr_activated_30d`; ниже AreaChart тренда `activations_by_day`.
  ⚠ (Ф5) герой `qr_activated` = **коды** all-time (`status='used'`), а `qr_activated_7d/30d` = **события**
  (`customer_event`) — разные единицы; НЕ подавать 7д/30д как строгое подмножество all-time коробок
  (подпись 7д/30д: «Активаций за 7/30 дней»). ⚠ (Ф4) заголовок тренда несёт активное окно —
  **«Активации за {days} дней»** — т.к. `days`-селектор скоупит ТОЛЬКО этот график.
- **2 Участники:** `profiles_total` · `friends` · `phone_verified` (НИТ-2 подпись «по всей базе»).
- **3 Воронка:** горизонтальные бары `funnel` (4 ступени, distinct-люди) + плитка `consents_ever_granted`.
  ⚠ (Ф1) ступени считаются на бэке **независимо** (`count(distinct user_id)` на event_type,
  service.py:319-333), НЕ вложенными подмножествами → возможна немонотонность (`qr_activated > joined`).
  Рендер немонотонно-безопасный: каждый бар масштабируем к **max ступени**, НЕ к предыдущей; **никаких
  «% конверсии между ступенями»**; отрицательных ширин быть не может. Заголовок «Ступени регистрации», не «воронка-конверсия».
- **4 Баллы:** `points_earned` · `points_spent` · `points_outstanding` + бар `points_by_reason`.
- **5 Демография:** `gender` (bar/pie) · `age_buckets` (bar) · `top_cities` (список ≤5) · `survey_coverage`%.

---

## 6. Три UI-НИТа Ревьюера — как отрабатываю (каждый закреплён тестом)

**НИТ-3 — «коробок отсканено» vs «людей отсканило».** Герой `qr_activated` = `count(qr_codes
status='used')` = **коробки** (один код = одна коробка; участник, активировавший 3 коробки, даёт +3).
Ступень воронки `qr_activated` = **distinct-люди** (тот участник = +1). Числа РАЗНЫЕ → не путать.
→ Герой-плитка: подпись/subtitle **«коробок активировано»**. Ступень воронки: подпись **«Активировали
(людей)»**. Тест закрепляет обе подписи (мутация — убрать различающее слово → красный).

**НИТ-2 — `phone_verified` считается глобально.** Backend = `count(users WHERE phone_verified_at IS
NOT NULL)` по **всей** базе (507), не только по лоялти. Сегодня телефон верифицируется только ботом
лоялти (⇒ фактически = лоялти), но колонка глобальна. → Плитка «Телефон подтверждён» с subtitle
**«по всей базе»** (не «участников с телефоном»). Тест закрепляет квалификатор (мутация — убрать → красный).

**НИТ-4 — малый N деанонимизирует демографию.** При крошечном N срез (пол/возраст/город) может
указывать на конкретного человека. Admin-only принято (не прячем жёстко), но подаём честно: при
`profiles_total < SMALL_SAMPLE_THRESHOLD` (константа, дефолт **10**, задокументирована) — предупреждение
**«⚠️ Малая выборка (N=X): значения могут относиться к конкретным участникам»** **над ВСЕЙ секцией
демографии** (не над отдельным срезом), копирайт покрывает секцию целиком. Тест: N=3 → баннер есть;
N=50 → баннера нет (мутация порога `<`→`>` → красный).
⚠ (Ф3, осознанное решение) порог на `profiles_total` слеп к бакету-одиночке (напр. `top_city`=1 человек
при большом total, service.py:405-414). Остаточный риск ограничен тем, что API отдаёт только
**маргинальные** распределения (не joint) → деанонимизация не глубже «1 профиль в городе X». Для v1
при ратификации «admin-only, не прячем жёстко» — приемлемо; жёстче давить НЕ надо (противоречит ратификации).

---

## 6a. Карты RU-подписей enum-VALUE + фолбэк (условие Гейта-1, находка 2 — ОБЯЗАТЕЛЬНО)

Бэк отдаёт сырые enum-**VALUE** (`welcome`, `male`, `qr_activated`) — RU-админ не должен видеть коды.
Каждое enum-строковое поле мапится `Record<string,string>` **с фолбэком на сырое значение** (идиома
проекта `OrderStatusChart.tsx:51` `MAP[v] || v`) — чтобы новый VALUE на бэке не давал пустую подпись.
VALUE выверены по `packages/api/src/loyalty/models.py`:

- **`points_by_reason.reason` (LedgerReason, 10):** `cashback` Кэшбэк · `welcome` Welcome-бонус ·
  `friend_bonus` Бонус Друга · `referral` Реферал · `birthday` День рождения · `review` Отзыв ·
  `subscription` Подписка · `l3` L3 · `redeem` Списание · `expiry` Сгорание. *(мап covers все 10 —
  group-by reason может вернуть любой; фолбэк на сырое для будущих)*.
- **`funnel.step` (CustomerEventType, 4):** `joined` Вошёл · `phone_shared` Телефон · `qr_activated`
  Активировали (людей) *(НИТ-3)* · `survey_done` Анкета.
- **`gender` (Gender + синт. unknown):** `male` Муж. · `female` Жен. · `unknown` Не указан.
- **`age_buckets.bucket`:** `18-24`/`25-34`/`35-44`/`45-54`/`55+` как есть · `unknown` Не указан.

Тест: неизвестный VALUE (напр. `reason:"__new__"`) рендерится **сырым**, не пусто (страховка от
будущих бэкенд-добавлений). Мап-константы — рядом с компонентом (как STATUS_LABELS в OrderStatusChart).

## 7. Пустые состояния (prod ~0 = ДЕФОЛТНЫЙ вид, не край)
- **Скаляры** (обязательные `number` из бэка) → плитки показывают `0` / `0.0%` штатно.
- **Списки-графики** пустые → идиома проекта `if (data.length === 0) return <Card>«Пока нет
  <данных>»</Card>` (как OrderStatusChart): тренд → «Пока нет активаций»; `points_by_reason` →
  «Баллы ещё не начислялись»; каждый срез демографии → «Нет данных анкеты».
- **Воронка** — 4 фиксированные ступени: рисуем всегда (0/0/0/0 = осмысленное «ещё никого»), не прячем.
- Различаем 4 состояния: **loading** (Skeleton, зеркало StatsCard/RevenueChart) / **error** (как
  DashboardPage: текст ошибки) / **empty** (данные есть, нули → плитки+плейсхолдеры) / **populated**.

---

## 8. Скоуп

**Входит:** слайс `features/loyalty` (types/api/hook/components/page) + `/loyalty` route + navItem
«Biotact Club»; 5 блоков; 3 НИТа в копирайте/логике; пустые состояния; тесты (§9); RU-хардкод.

**Явно НЕ входит:** вкладки/`tabs.tsx`/новые зависимости и любые правки рабочего DashboardPage
(если Ф2=B); CSV-экспорт лоялти (dashboard имеет свой — добавлю, если Капитан попросит); i18n
(админка RU-only); per-product разбивки, realtime, materialized; продвижение `StatsCard` в `shared/ui`
(отдельная задача); любые правки бэкенда/схемы/миграций.

## 9. Тест-план (Vitest + RTL, правило №0 red→green; зеркало `StatsCard.test.tsx`)
- [ ] **НИТ-3:** `RegistrationFunnel` — ступень `qr_activated` подписана «(людей)»; герой-плитка «коробок».
      red: без различающей подписи. Показать red→green.
- [ ] **НИТ-2:** `MembersStats` — плитка `phone_verified` несёт «по всей базе». red: без квалификатора.
- [ ] **НИТ-4:** `DemographicsBreakdown` — N=3 → баннер малой выборки есть; N=50 → нет. Мутация порога → red.
- [ ] **Пустые состояния (критично, = прод):** каждый чарт с пустым массивом → плейсхолдер отрисован
      И график НЕ отрисован; плитки с нулями рендерятся; вся страница на all-zero данных не падает.
- [ ] **Демография `unknown`:** бакет/пол `unknown` отрисованы (не отфильтрованы молча).
- [ ] **(Ф1) Воронка на немонотонных данных** (`qr_activated > joined`) → рендер без слома, без
      отрицательных ширин, без ложных «% конверсии между ступенями» (бары к max, не к предыдущей ступени).
- [ ] **(Ф2) Неизвестный enum-VALUE рендерится сырым** (не пусто) — для `reason` и `gender` (страховка
      от будущих бэкенд-добавлений); известные VALUE дают RU-подпись.
- [ ] **(Ф+) Error-ветка:** состояние ошибки показывает сообщение (сейчас в плане были loading/empty — добираю error).
- [ ] **(опц.)** `activation_rate` рендерит `0.0%` на пустых данных.
- [ ] `LoyaltyPage` smoke: мок `loyaltyApi` (all-zero) + QueryClientProvider (переиспользовать хелпер
      из `src/test`, если есть; иначе обернуть инлайн) → рендер без ошибок, герой=0.
- [ ] Полный локальный CI фронта: `npx tsc -b` (strict), `npm run lint`, `npm run test` (vitest),
      `npm run build`. Вывод приложить в отчёт.

## 10. Definition of Done
- merged / ветка удалена; `tsc -b` + lint + vitest + build зелёные (вывод в отчёте).
- 5 блоков рендерятся; пустое состояние чистое (дефолтный прод-вид); 3 НИТа в копирайте/логике+тестах.
- 0 `any`, named exports, `interface` для props, компоненты <200 строк, семантический HTML/aria.
- Фронт-only: 0 бэкенда, 0 миграций → прод-деплой = статик-ребилд, эндпоинт уже живой.
- Отчёт → Гейт-2 отдельным Ревьюером свежим контекстом; **`/typescript-review` обязателен**
  (напоминание из линии проекта); read-only админ-UI — `/security-review` уместен точечно
  (проверить, что не показываем PII сверх агрегатов бэка; фронт их не добывает).

## 11. Открытые вопросы Капитану (ратификация Гейта-1)
1. **Ф2 (главный):** B — отдельная страница `/loyalty` (моя реко) vs A — вкладка. Ратифицируешь?
2. **Навигация:** label «Biotact Club»; иконка lucide — реко `Award` (или `Sparkles`/`Gift`); route `/loyalty`.
3. **CSV-экспорт лоялти в v1?** Реко: НЕ включать (add later по просьбе).

## 12. Почему безопасно
Read-only визуализация уже живого эндпоинта; 0 бэкенда/схемы/миграций → прод-деплой = чистый
статик-ребилд (нулевой риск инцидента-типа #30); при Ф2=B ноль правок рабочего кода кроме двух
зеркальных строк проводки (route + navItem). Полностью обратимо (revert одного PR).
