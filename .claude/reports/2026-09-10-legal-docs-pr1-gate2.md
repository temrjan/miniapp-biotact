# Отчёт Гейта-2: PR-1 legal-docs — статика фронта за флагом

Ветка: `feat/legal-docs-pr1-static` · коммит `1f8e17e` (не запушен — ждёт «чисто» Ревьюера).
Спека: `.claude/specs/2026-09-10-legal-docs.md` (печать сверена на старте сессии: sha256 `b24a41ec…7019` совпал в сейфе и в рабочей копии).

## Сделано — по пунктам спеки (разбиение, PR-1)

- `src/legal/`: контент-файлы `offer.ts` (18 секций), `policy.ts` (19), `terms.ts` (13) —
  тексты проектов от 20.08.2026 перенесены дословно; `version: 'draft-2026-08-20'` (16 ≤ 18
  символов) живёт только здесь (F1); `contacts.ts` — контакты раздела 16 оферты; `index.ts` —
  реестр `LEGAL_DOCS`, `getLegalDoc(slug)`, `buildConsentVersion()` (композит 46 ≤ 50 символов).
- `LegalListPage` — список из трёх документов с редакциями + блок контактов.
- `LegalDocPage` — заголовок, редакция, оглавление-якоря, секции, uz-плашка; неизвестный
  slug → редирект на `/legal`.
- Роуты `/legal`, `/legal/:slug` (constants.ts + App.tsx, lazy по паттерну RamadanPage):
  флаг OFF → прямой URL редиректит на HOME (Finding 1 закрыт поведенчески).
- `isLegalEnabled()` — единая точка чтения флага (`src/legal/flag.ts`).
- Пункт «Документы» в профиле (menuItems, за флагом).
- Строка акцепта условий на чекауте: блок итога, под суммой, над WebButton (за флагом).
- i18n: ключи `legal.*`, `profile.legalDocs*`, `checkout.acceptTerms*` в ru.json и uz.json;
  тест паритета зелёный.

## Не сделано / отложено (по спеке — это PR-2/PR-3)

- Два чекбокса, consentStore, гейт по гранту, маркетинг-тоггл, расширение API — PR-2.
- Баннер членам — PR-3.
- Блок «Согласия» в профиле (только is_member) — PR-2 (нужен `/loyalty/me`).

## Замечено, не трогаю — вне скоупа

- `CheckoutPage` при пустой корзине вызывает `navigate('/cart')` прямо в рендере — вне
  `<Routes>` это даёт бесконечный цикл (словил в тесте), в приложении безопасно, потому что
  страница размонтируется роутером. Работает, но хрупко; кандидат на отдельную правку
  (early return через `<Navigate>`).
- В `constants.ts` смешаны кавычки и окончания строк (CRLF/LF) — не трогал, diff минимален.

## Как тестировал — команды + вывод

Новые/обновлённые тесты (4 файла, 16 тестов), прогон изолированно, флаг OFF:

```
npx vitest run src/pages/LegalListPage.test.tsx src/pages/LegalDocPage.test.tsx \
  src/App.test.tsx src/pages/CheckoutPage.test.tsx
→ Test Files 4 passed (4), Tests 16 passed (16)
```

Red-доказательства: до появления контент-файлов те же тесты падали на импорте
(`ERR_MODULE_NOT_FOUND ./offer`); флаг-проба `expect(isLegalEnabled()).toBe(true)` —
зелёная при `VITE_LEGAL_DOCS=true`, КРАСНАЯ без него (доказывает, что ON-сьют реально
видит включённый флаг, а не дублирует OFF).

Гейты (последнее действие перед коммитом; после старта прогона ни один файл не трогался —
проверено `find -newer`, пусто). Полный лог: `/tmp/gates-pr1.log`.

```
OFF: npm run test   → Test Files 29 passed (29), Tests 296 passed (296)
OFF: npm run lint   → чисто (exit 0)
OFF: npx tsc -b     → чисто (exit 0)
OFF: npm run build  → ✓ built in 4.41s
ON:  VITE_LEGAL_DOCS=true npm run test  → 29 passed (29), 296 passed (296)
ON:  VITE_LEGAL_DOCS=true npm run build → ✓ built in 4.42s
```

Существующие тесты зелёные без правок их ожиданий (критерий приёмки 1): единственные
изменённые тестовые файлы — `App.test.tsx` (добавлен describe LegalRoutes, старый тест не
тронут) и новые файлы.

## Отклонения от плана и почему

- `pages/index.ts` не дополнен новыми страницами: в нём нет и RamadanPage, App.tsx импортирует
  страницы напрямую — следовал фактическому паттерну, а не частичному реестру.

## Решения, которые принял сам (спека молчала)

- Хелпер флага в отдельном `src/legal/flag.ts`, а не в `index.ts` — чтобы App.tsx не тянул
  контент документов в основной чанк при OFF.
- `LegalRoutes` в App.tsx используется как `{LegalRoutes()}` (функция, возвращающая фрагмент
  с `<Route>`), а не как JSX-компонент: `<Routes>` принимает только Route/фрагменты детьми.
  Экспортирована для тестов — по образцу `HomeOrActivate`.
- uz-плашка показывается ТОЛЬКО при локали `uz` (в ru-локали предупреждать не о чем).
- Шапки документов (город, «Редакция от ___», вводные абзацы) — в секции `intro` с пустым
  `heading`: рендерится без заголовка, в оглавление не входит.
- Таблицы источников (статусы, билеты, реквизиты) сплющены в абзацы «Ключ — значение.»;
  порядок пунктов раздела 5 Условий (5.5, 5.7, 5.6, 5.8 — опечатка нумерации в исходном txt)
  сохранён дословно, не переставлял. `ОКЭД: [ЗАПОЛНИТЬ]` перенесён как есть.
- Пункт «Документы» добавлен в конец массива menuItems (после «Настройки»).
- Регистр заголовка оферты: «Публичная оферта…» (в источнике капс «ПУБЛИЧНАЯ ОФЕРТА») —
  по образцу из задания контент-субагенту.
- Тесты на `fireEvent`, не `userEvent`: пакета `@testing-library/user-event` нет в
  package.json, новую зависимость не добавлял (паттерн ActivationPage.test.tsx).
- В CheckoutPage.test.tsx сторы zustand реальные (setState), моки только на границах API:
  `usePayment`, `useLoyalty` (правило «сторы — реальные»).

## Опись коммита — кладу 18 файлов (коммит 1f8e17e)

| Файл | Что | Почему в этом PR |
|---|---|---|
| `src/legal/{offer,policy,terms}.ts` | контент документов + версии | ядро PR-1 (F1) |
| `src/legal/contacts.ts` | контакты раздела 16 | список документов + будущий профиль |
| `src/legal/index.ts` | типы, реестр, buildConsentVersion | контракт спеки |
| `src/legal/flag.ts` | isLegalEnabled() | единая точка флага |
| `src/pages/LegalListPage.tsx` | список + контакты | спека |
| `src/pages/LegalDocPage.tsx` | экран документа | спека |
| `src/App.tsx` | lazy-роуты за флагом (LegalRoutes) | Finding 1 |
| `src/utils/constants.ts` | ROUTES.LEGAL, LEGAL_DOC | реестр роутов |
| `src/pages/ProfilePage.tsx` | пункт «Документы» за флагом | спека |
| `src/pages/CheckoutPage.tsx` | строка акцепта за флагом | спека |
| `src/i18n/{ru,uz}.json` | ключи legal.*/profile.*/checkout.* | лейблы ключами |
| `src/App.test.tsx` | +describe LegalRoutes (гейт флага OFF/ON) | тест-план |
| `src/pages/LegalListPage.test.tsx` | новый: список, контакты, реестр, бюджеты версий | тест-план |
| `src/pages/LegalDocPage.test.tsx` | новый: секции/редакция/оглавление/плашка/редирект | тест-план |
| `src/pages/CheckoutPage.test.tsx` | новый: строка акцепта ON/OFF (Finding 6: существующего не было) | тест-план |

**НЕ кладу:** `.claude/specs/2026-09-10-legal-docs{,-plan}.md` и 2 отчёта в `.claude/reports/` —
артефакты круга, по HANDOFF коммитятся отдельным docs-PR по решению Капитана (копии в сейфе).

## Вопросы Ревьюеру

1. Достаточно ли поведенческого закрытия Finding 1 (роут формально регистрируется, но
   element = `<Navigate to=HOME>` при OFF), или требовалось именно «не регистрировать Route»?
2. `{LegalRoutes()}` как вызов функции в JSX — приемлемо или предпочесть иной паттерн?
3. Оглавление-якоря: scrollIntoView без polyfill — ок для Telegram WebView?
