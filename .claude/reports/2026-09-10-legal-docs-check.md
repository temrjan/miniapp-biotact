# Отчёт /check: план размещения юридических документов

Дата: 2026-09-10. Target: план размещения и формата юридических документов
(нативные экраны /legal/*, два чекбокса, строка на чекауте, раздел «Документы»,
consent.version). Находок: 8 (2 blocking, 5 important, 1 nit).

## Finding 1 — Поведение при повторной активации QR не определено [blocking → actionable]
`ActivationPage.tsx:132-157` показывает экран согласия при КАЖДОЙ активации,
включая действующих членов (`record_consent` идемпотентно молчит,
`service.py:482-504`). План не определял, что видит член при новом QR.
Fix: `/loyalty/me` отдаёт состояние согласий; гейт — только при отсутствии
активного гранта; маркетинг-чекбокс только при первичной регистрации.

## Finding 2 — Состояние чекбокса теряется при переходе на документ [blocking → actionable]
Чекбокс — локальный `useState` (`ActivationPage.tsx:46`, `JoinCard.tsx:14`);
роут `/legal/*` размонтирует страницу → галочка сбрасывается, на активации
риск потери `?code=`. Fix: состояние в zustand-сторе.

## Finding 3 — Markdown потребовал бы новой зависимости [important → actionable]
В `packages/frontend/package.json` md-рендера нет. Fix: контент —
типизированные TS-объекты, ноль новых зависимостей.

## Finding 4 — Одно поле version на три документа; нет техданных сессии [important → actionable]
Обязательный чекбокс покрывает оферту+политику+ПД; `Consent.version` —
одно String(50) (`models.py:51`); политика п. 9.3 обещает версию текста и
техданные сессии. Fix: композитная строка `offer:<v>+policy:<v>`; техданные —
payload `customer_event` (JSONB есть), без миграции.

## Finding 5 — Действующие члены не увидят документы [important → trade-off, решение Капитана]
Идемпотентный пропуск (`service.py:482-504`) + JoinCard скрыт от членов →
база остаётся на «1.0» без акцепта оферты; оферта п. 13.2 требует
уведомления о новой редакции. Fix: одноразовый баннер (PR-3). Капитан: ДА.

## Finding 6 — Нет пути отзыва согласий [important → actionable]
Политика п. 16.1 — отзыв в любое время; endpoint'а нет, модель
(`revoked_at`, `models.py:55`) готова. Fix: маркетинг-тоггл в профиле
(grant/revoke API); отзыв основного — через поддержку.

## Finding 7 — Строка чекаута против нативной MainButton [important → actionable]
`CheckoutPage.tsx:219-225` — подтверждение через нативную MainButton вне DOM;
«рядом с кнопкой» не гарантирует показ до оформления (п. 3.1 Условий).
Fix: строка в блоке итога заказа + над WebButton.

## Finding 8 — UZ-пользователь видит RU-документ без пометки [nit → actionable]
Fix: плашка «документ на русском языке» при uz-локали (оферта 15.1 /
Условия 11.2 разрешают RU до публикации гос-версии).

## Category exhaustiveness
- Facts: свежие Read/Grep по ActivationPage, JoinCard, App.tsx, package.json,
  models.py, service.py, CheckoutPage, useActivation.ts
- Edge cases: повторная активация, возврат с документа, смена редакции,
  отзыв/повторный грант, uz-локаль, падение сети, гонка согласий
- Simplicity: md vs TS-структура, оверлей vs роут, отказ от миграции
- Compatibility: lazy-паттерн, named exports, i18n, существующие тесты,
  merge=deploy + Migration parity, zustand-паттерн
- Format: покрытие запроса, исключения, двучтения чекаута, скрытое состояние,
  откат

## Doubt theater check
2 blocking, 5 important, 1 nit → genuine critique; план чинится точечно.
