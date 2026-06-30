# Отчёт: ci — security scanning (Phase 1)

Ветка `ci/add-security-scanning` @ `97f3ae4` (поверх main `f34ca12`). Кресло: Инженер. Задача от Капитана: «собери PR `ci: add security scanning`, убедись что ничего не сломаем». Research: фоновый агент (верифицированные SHA) + локальная эмпирическая разведка.

## Сделано
- **`.github/workflows/security.yml`** (новый, ОТДЕЛЬНЫЙ workflow — не трогает `ci.yml`/`deploy.yml` → CI→Deploy-цепочка цела):
  - **pip-audit** (matrix: `packages/api` + `packages/bot` requirements) — `pypa/gh-action-pip-audit@…v1.1.0`.
  - **npm audit** `--audit-level=high --omit=dev` (matrix: `packages/frontend` + `admin-panel-v2`).
  - **gitleaks** `@…v3.0.0` (Node 24; v2 EOL 2026-09-16) — секрет-скан истории, `fetch-depth: 0`, `.gitleaks.toml`.
  - Триггеры: PR + push(main) + **weekly schedule** + `workflow_dispatch`. `permissions: contents:read`, per-job timeouts, concurrency.
- **`.gitleaks.toml`**: `[extend] useDefault=true` + allowlist (`.env.example` плейсхолдеры; удалённый `devops-agent/` с doc-примером `sk_live_…`).

## 🔑 Главное решение — Phase 1 REPORT-ONLY (все джобы `continue-on-error: true`)
**Почему:** локальная разведка нашла МНОГО пред-существующих уязвимостей в депсах:
- pip-audit api ~24 (starlette/python-multipart/urllib3/idna/mako/pydantic-settings/…), bot ~15 (aiohttp×10/…).
- npm audit frontend 17 (10 high, 1 critical — vitest/ws), admin 16 (10 high — vite).
→ **blocking покрасил бы CI на день 1 = заблокировал всю команду** (ровно то, что Капитан запретил). Поэтому видимость без блока; фикс депсов = отдельный follow-up (Phase 2 триаж → Phase 3 PR-scoped blocking).

## Секрет-скан — НЕ инцидент (проверено)
- Локальный `.env` (токен дев-бота) — **gitignored, не в git, никогда не коммитился**.
- 1 находка истории — **doc-пример** `API_KEY="sk_live_…"  # ❌ НИКОГДА!` в удалённом `devops-agent/` стайлгайде (правило `generic-api-key`, FP). Аллоулистнут. **Реального секрета в репо нет.**

## Как проверял (локально — насколько CI вообще проверяем вне GitHub)
- `python -c yaml.safe_load` security.yml → valid; `tomllib` .gitleaks.toml → valid.
- **actionlint 1.7.12** на `security.yml` + `ci.yml` → **0 замечаний** (exit 0).
- gitleaks `git`-scan (режим CI) с конфигом → **no leaks found** (доказал, что allowlist гасит находку).
- recon pip-audit/npm/gitleaks — определил non-blocking-дизайн эмпирически.
- ⚠️ **Живой прогон на PR — финальное подтверждение** (workflow исполняется только на GitHub). Безопасно: отдельный workflow + continue-on-error → даже сбой джоба не блокирует и не трогает деплой.

## Не сделано / follow-up (Phase 2-3)
- **Триаж dep-уязвимостей** (бамп версий / `ignore-vulns` / `npm audit fix`) — отдельный PR (бамп рискован, не в этом скоупе).
- После триажа — снять `continue-on-error` с PR-джобов → blocking (PR-scoped).
- Вернуть `mypy bot/` (было в списке, отдельно).
- ⚠️ **При миграции репо в org `biotact-de`** — gitleaks-action потребует секрет `GITLEAKS_LICENSE` (для личного репо не нужен).

## Живой прогон на PR #45 — 2 бага найдены и исправлены (force-push `945c8ef`)
Первый прогон (`97f3ae4`) дал 5 красных security-чеков → диагностика по логам:
1. **gitleaks `fail` = НЕ утечка, а 403 прав:** `Resource not accessible by integration`, `x-accepted-github-permissions: pull_requests=read`. gitleaks-action на `pull_request` перечисляет коммиты PR через GitHub API → нужен `pull-requests: read` (был только `contents: read`). **Фикс:** job-level `permissions: {contents: read, pull-requests: read}` на gitleaks.
2. **«5 failing» = job-level `continue-on-error` даёт красное-видимое** (не зелёное, как я/Капитан думали — это степ-vs-job нюанс). Мёрж не блокировался («Merging can be performed automatically»), но шумно. **Фикс:** перенёс `continue-on-error` на **step-level** dep-аудитов → джобы **зелёные** (тихий Phase-1). gitleaks НЕ глушу (репо чист → зелёный; реальный секрет → красный).

**Итог (re-run): 10/10 зелёные** (CI 5 + Security 5), Security overall=success. Проверено по логам: gitleaks «1 commits scanned / no leaks found» + SARIF; pip-audit логирует находки (cryptography/starlette/…) — report-only работает. actionlint чист.

## Решение Капитана
Маршрут — **fast-track** (ops-домен Капитана; report-only, не блокирует, не трогает CI→Deploy). Самочек 2 пунктов (fetch-depth:0 ✅, SHA-пины ✅) пройден. **PR #45 чистый, готов к мёржу.**
