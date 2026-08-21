# Укрепление прод-сервера biotact-main — спека на Гейт 1 (ops, не код)

> Дата: 2026-08-17 · Кресло: Инженер · База: аудит `.claude/reports/2026-08-17-prod-server-audit.md`
> (только чтение, 17.08). Прод = `33b4b9a`. Капитан: «Го, в стиле Инженера — через Ревьюера до и после».
> Статус: **Гейт 1 — раунд 2.** Раунд 1 (17.08): шаги 2–5 APPROVED, шаг 1 BLOCKED — моё утверждение «бэкапов нет»
> опровергнуто Ревьюером (ошибка пробы: `crontab -l` обрезан `tail`, `find` с `-size +1M`); §1/шаг 1 переписаны
> под факт существующего бэкапа. **Раунд 2 (17.08): APPROVED, «чисто»** (+2 минора, внесены ниже). **Исполнено: 1a (17.08, Гейт 2 APPROVED;
> отклонение 640→700 на скрипте — cron вызывает по пути, без x-бита упал бы даже root; принято Ревьюером),
> 1c и 1e (17.08, отчёт `2026-08-17-prod-hardening.md`, ждут Гейта 2). Капитан: SSH/4/5 — отложить; приоритет —
> бэкапы. Открыто: 1b (окно), 1d (age-ключ + bcv2).**

---

## 0. Онбординг Ревьюера — прочитай первым

**Роли и связь.** Капитан · Инженер (я, автор этого документа) · Ревьюер (ты). Прямого канала между нами
нет — всё через Капитана. Два гейта: **Гейт 1** — ты проверяешь этот план ДО того, как я трону сервер;
**Гейт 2** — ты независимо перепроверяешь результат ПОСЛЕ, по моему отчёту с выводом команд.
Особенность этой задачи: **это не код и не PR — это конфигурация живого прода** (заказы, платежи
Multicard, программа лояльности). Откат — не `git revert`, а обратная правка файла; поэтому каждый шаг
атомарен, с проверкой до/после и записанным откатом.

**Как тебе попасть на сервер.** На этой машине в `~/.ssh/config` есть алиас `biotact-main`
(`95.111.224.251:2222`, root, ключ `claude_migration`) — `ssh biotact-main '<команда>'` работает из
твоей сессии так же, как из моей. **Твоё правило на проде — только чтение**: никаких правок файлов,
рестартов, `pm2`, `systemctl` кроме `status/is-*`, `psql` только `SELECT`/`show`. Всё, что тебе нужно
для Гейта 2, — команды чтения, они перечислены у каждого шага.

**Что на сервере (снято 17.08).** Ubuntu 24.04, PM2 под root: `api` (uvicorn :3001), `bot`,
`multicard-backend`, `multicard-telegram`, `csv2txt`; Docker `voice-service` (127.0.0.1:8090);
Postgres 16.13 (`biotact_db` = 10 МБ, localhost, scram); Redis 7 (localhost); nginx 1.24 —
`app/admin/multicard/voice.biotact.uz`; ufw: только 2222/80/443; fail2ban (jail `sshd`); деплой —
GitHub Actions по SSH ключом `github-actions-deploy` как root на порт 2222 (`deploy.yml:52`).

**Что я прошу проверить на Гейте 1** (мои утверждения, на которых стоит план):
1. sshd берёт **первое** встреченное значение директивы; `Include sshd_config.d/*.conf` стоит на
   строке 12 (раньше `PermitRootLogin prohibit-password` на 42); файлы включаются в лексическом порядке →
   `00-hardening.conf` победит `50-cloud-init.conf`/`60-cloudimg-settings.conf`/`hardening.conf`.
   Проверка: `man sshd_config` («the first obtained value will be used»), `ssh biotact-main 'grep -nE
   "^Include|^PermitRootLogin" /etc/ssh/sshd_config; ls /etc/ssh/sshd_config.d/'`.
2. Никто не входит по паролю: `ssh biotact-main 'zgrep -ch "Accepted password" /var/log/auth.log* |
   paste -sd+ | bc'` → 0; за 90 дней журнала → 0; деплой ходит ключом (`Accepted publickey root`).
3. nginx **дописывает** реальный IP к присланному `X-Forwarded-For` (`$proxy_add_x_forwarded_for`,
   строки 22 и 85 `sites-enabled/biotact`), а `admin_router.py:127-136` берёт **первый** элемент →
   лимит 5 попыток/60 с (`:36-38`) обходится подменой заголовка. CDN перед сервером нет
   (`dig +short app.biotact.uz` → 95.111.224.251).
4. ~~Бэкапов нет~~ — **опровергнуто (раунд 1)**. Есть: root crontab `0 3 * * * /var/www/biotact/backups/backup.sh`,
   15 дампов `.sql.gz` (~80–92 КБ, 14 дней), скрипт 28.01.2026. Четыре дефекта — в переписанном шаге 1.
   Проверка: `ssh biotact-main 'crontab -l; ls -l /var/www/biotact/backups | head; sed -n 1,20p
   /var/www/biotact/backups/backup.sh | sed -E "s/(PGPASSWORD=)[^ ]+/\1<MASKED>/"'`.
5. `20auto-upgrades`: `Update-Package-Lists "0"` и `Unattended-Upgrade "0"` — оба.
6. journald 4,0 ГБ, `journald.conf.d` не существует.

---

## 1. Скоуп — ровно пять правок + одна по решению Капитана

| # | Что | Файлы на сервере | Откат |
|---|---|---|---|
| 1 | Бэкап `biotact_db` — **починка существующего**: 1a права · 1b ротация пароля БД · 1c замена скрипта + снятие старого cron · 1d копия на bcv2 (зашифрованная) · 1e проверка восстановления | 1a `/var/www/biotact/backups/*` (права) · 1b `ALTER ROLE biotact`, `.env` api/bot (+ref-bot) · 1c `/usr/local/sbin/biotact-pg-backup.sh`, `/var/backups/biotact/` (700), `/etc/cron.d/biotact-pg-backup`, старая строка crontab снята, старый скрипт → `/root/old-backup.sh.disabled` · 1d ключ `/root/.ssh/backup_bcv2`, `age`-публичный ключ `/root/.config/biotact-backup.age.pub`; на **bcv2**: пользователь `biotact-backup`, `/srv/backups/biotact-main`, `authorized_keys` с `rrsync -wo` | 1a `chmod` назад · 1b `ALTER ROLE` на старый + вернуть `.env` из копий · 1c вернуть строку crontab и скрипт · 1d удалить ключи/файлы; на bcv2 — `userdel` |
| 2 | SSH: закрыть вход по паролю и root-по-паролю | **новый** `/etc/ssh/sshd_config.d/00-hardening.conf` (существующие не трогаю) | `rm` файла + `systemctl reload ssh` из живой сессии |
| 3 | nginx: `X-Forwarded-For $remote_addr` (перезапись, не дописывание) | `sites-enabled/biotact` строки 22 **и** 85 | вернуть `$proxy_add_x_forwarded_for` |
| 4 | Автообновления security-only | `/etc/apt/apt.conf.d/20auto-upgrades` (обе директивы → 1) | вернуть 0/0 |
| 5 | journald лимит | `/etc/systemd/journald.conf.d/size.conf` (`SystemMaxUse=500M`) | `rm` + restart journald |
| 6 | Ключи root — **только после решения Капитана** по строкам «без подписи» и `claude-code-main` | `/root/.ssh/authorized_keys` (резервная копия рядом) | восстановить из копии |

**Явно НЕ делаю:** grep транскриптов `/root/.claude` на токены ботов/ключи Multicard и уборку 38 МБ (МИНОР-2
Ревьюера — отдельная задача по решению Капитана; `/root` = 700, не-root туда не попадёт) · сервисный пользователь вместо root · Node 22 · выравнивание Python 3.11/3.12 · CORS ·
Redis `requirepass` · уборка `referrals/`/`backups/*` · `pm2 reload` — всё это отдельные задачи через
гейты. Не трогаю `hardening.conf`, `50-…`, `60-…`, основной `sshd_config`, `multicard`/`voice`-блоки nginx.

---

## 2. Шаги — команды, ожидаемый вывод, проверка Ревьюером

### Шаг 1 · Бэкап БД — починка существующего механизма (порядок по риску, Ревьюер раунд 1)

**Что есть:** `/var/www/biotact/backups/backup.sh` (root, **755**), cron root `0 3 * * *`, дампы
`biotact_db_<дата>.sql.gz` (**644**, каталог **755**), 14 дней, `pg_dump -U biotact -h localhost` с
`PGPASSWORD=…` **открытым текстом в скрипте**; проверка успеха — `$?` от `gzip`, не от `pg_dump`; копии вне
сервера нет. Тот же пароль: `.env` у `api`, `bot`, `ref-bot` (stopped) и в транскриптах `/root/.claude/…`
на самом сервере (агента запускали на проде как root — наблюдение для Капитана, не в скоупе).

**1a · Права — первым, две команды, до появления любой второй учётки:**
```bash
chmod 700 /var/www/biotact/backups && chmod 600 /var/www/biotact/backups/*.sql.gz && chmod 640 /var/www/biotact/backups/backup.sh
ls -ld /var/www/biotact/backups; ls -l /var/www/biotact/backups | head -3
```
Откат: `chmod 755/644/755` обратно. Cron продолжает работать (root).

**1b · Ротация пароля БД** (пароль засвечен в скрипте 755, в переписке Гейта 1 и в транскриптах на сервере) —
**сразу после свежего дампа** (запустить `backup.sh` руками перед ротацией: точка возврата в минутах, не в сутках):
```bash
NEW=$(openssl rand -hex 24)                                    # только [0-9a-f] — безопасно для URL в .env
cp -a /var/www/biotact/packages/api/.env /root/api.env.bak-$(date +%F); cp -a /var/www/biotact/packages/bot/.env /root/bot.env.bak-$(date +%F)
sudo -u postgres psql -c "ALTER ROLE biotact WITH PASSWORD '$NEW';"
sed -i -E "s#(postgresql\+asyncpg://biotact:)[^@]+@#\1${NEW}@#" /var/www/biotact/packages/api/.env /var/www/biotact/packages/bot/.env
pm2 restart api bot && sleep 5 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3001/health   # 200
PGPASSWORD="<старый>" psql -U biotact -h localhost -d biotact_db -c "select 1"                                # ожидание: password authentication failed
```
`ref-bot/.env` — обновить той же `sed` (он остановлен и архивен; чтобы случайный `pm2 start` падал не тихо
на пароле, а по решению — оставляю на выбор Капитана; по умолчанию обновляю для консистентности).
**Полный инвентарь копий пароля (МИНОР-1 Ревьюера, раунд 2):** `api/.env`, `bot/.env`, `ref-bot/.env`,
старый `backups/backup.sh`, **`backups/ref-bot-pre-refactor-20260215/.env`** (февральская копия конфига —
после 1a недоступна не-root, в 1c удаляется), транскрипты `/root/.claude/…` (8 файлов; после ротации пароль там
мёртв). Ротация делает все копии бесполезными; инвентарь нужен, чтобы «мы всё нашли» не держалось на удаче.
`csv2txt`, `multicard-*`, `protocols-bot`, `voice` — пароль `biotact` не содержат (grep по `/opt/bots`,
`/etc` — пусто), не трогаю. Цена: `pm2 restart api bot` ≈ 12 с недоступности API — делать в согласованное окно
(можно совместить с окном шага 3). Откат: `ALTER ROLE … PASSWORD '<старый>'` + вернуть `.env` из копий + restart.
Секрет в чат не выводится: `NEW` живёт в переменной сессии; в отчёте — только факт и `select 1` под новым.

**1c · Замена скрипта + снятие старого cron — одним шагом (два молчаливых бэкапа одной базы недопустимы):**
новый `/usr/local/sbin/biotact-pg-backup.sh` (root, 700), **без пароля** (`sudo -u postgres`, peer-auth):
```bash
#!/usr/bin/env bash
set -euo pipefail
DIR=/var/backups/biotact; DB=biotact_db; KEEP=14
STAMP=$(date +%Y%m%d-%H%M%S); OUT="$DIR/${DB}-${STAMP}.dump"; TMP="$OUT.part"
mkdir -p "$DIR"; chmod 700 "$DIR"
if ! sudo -u postgres pg_dump -Fc "$DB" > "$TMP"; then rm -f "$TMP"; logger -p user.err -t biotact-pg-backup "pg_dump FAILED"; exit 1; fi
if ! pg_restore --list "$TMP" > /dev/null; then rm -f "$TMP"; logger -p user.err -t biotact-pg-backup "dump INVALID"; exit 1; fi
mv "$TMP" "$OUT"; chmod 600 "$OUT"
ls -1t "$DIR"/${DB}-*.dump | tail -n +$((KEEP+1)) | xargs -r rm -f
# offsite: шифруем публичным age-ключом (приватный — НЕ на проде), кладём в единственный каталог bcv2 (rrsync -wo)
ENC="$OUT.age"; age -R /root/.config/biotact-backup.age.pub -o "$ENC" "$OUT" \
  && rsync -a -e "ssh -i /root/.ssh/backup_bcv2 -p 2222 -o BatchMode=yes -o StrictHostKeyChecking=yes" "$ENC" biotact-backup@38.242.138.184:/ \
  || logger -p user.err -t biotact-pg-backup "offsite copy FAILED (local dump kept)"
rm -f "$ENC"
logger -t biotact-pg-backup "ok $(basename "$OUT") $(stat -c %s "$OUT")B"
```
- `.part` → `mv`: неполный дамп никогда не носит финальное имя · `pg_restore --list` — валидация **содержимого**
  (закрывает дефект «`$?` от gzip») · любой сбой — `user.err` в journal и код ≠ 0 · offsite-сбой не рушит
  локальный бэкап · `StrictHostKeyChecking=yes` — отпечаток bcv2 занесён в `known_hosts` **заранее руками**
  (замечание Ревьюера), не `accept-new`.
- Перенос истории: `mv /var/www/biotact/backups/*.sql.gz /var/backups/biotact/legacy/` (каталог 700 **и файлы 600** —
  защита не должна держаться на одном бите одного каталога, Гейт 2/1a) — старые дампы не теряем; `crontab -e` → строка `backup.sh` снята; старый скрипт → `/root/old-backup.sh.disabled` (600);
  подкаталог `backups/ref-bot-pre-refactor-20260215/` (копия конфига ref-bot от 02.2026 с паролем БД внутри,
  ref-bot архивен решением Капитана 10.08) — `ls -la` для протокола, затем **удаляется** (МИНОР-1); остальные
  `backups/pre-*` (кодовые копии без секретов — проверю grep'ом до) не трогаю, они в списке плановой уборки.
- `/etc/cron.d/biotact-pg-backup`: `10 3 * * * root /usr/local/sbin/biotact-pg-backup.sh` (03:10, чтобы не
  совпасть с бэкапом protocols-bot в 03:00; `expire_stale_orders` идёт в :20 каждые 3 ч — не пересекается).
- Ретеншн 14 — как было. Формат меняется `.sql.gz` → `-Fc` (сжатый, выборочное восстановление, `pg_restore`).

**1d · Копия вне сервера — bcv2 (38.242.138.184:2222), зашифрованная (условие Ревьюера):**
- `apt install age` на проде (кандидат `1.1.1` в Ubuntu 24.04 — проверено, сейчас не стоит).
- Пара `age`: генерируется **у Капитана** (рекомендация Ревьюера: приватный ключ не должен даже недолго жить на
  машине, где работают агенты). Запасной вариант: я генерирую **на этой машине, не на проде**, отдаю приватную
  часть Капитану вне чата и **стираю свою копию** (`shred`). **Никогда — на самом проде.** На прод едет только
  `age.pub` в `/root/.config/biotact-backup.age.pub`. Контроль в 1e: Капитан **расшифровывает** `.age`-копию своим
  ключом из своего менеджера паролей — «файл расшифровался», не «ключ сохранён». Восстановление из offsite
  требует приватный ключ → он у Капитана (менеджер паролей). Компрометация прода не отдаёт зашифрованные копии.
- Ключ транспорта на проде: `ssh-keygen -t ed25519 -N "" -f /root/.ssh/backup_bcv2 -C biotact-main-backup`.
- Отпечаток bcv2 — снять **изнутри bcv2** (`ssh bcv2 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub'`),
  занести в `/root/.ssh/known_hosts` прода (`ssh-keyscan -p 2222 38.242.138.184` **сверить** с отпечатком, потом
  добавить).
- На **bcv2** (второй прод, другой проект — отдельная строка приёмки): `useradd -m -s /bin/bash biotact-backup`,
  `mkdir -p /srv/backups/biotact-main && chown biotact-backup: … && chmod 700 …`, в `authorized_keys`:
  `command="<путь к rrsync> -wo /srv/backups/biotact-main",no-agent-forwarding,no-port-forwarding,no-pty,no-X11-forwarding ssh-ed25519 …`
  (`rrsync` из пакета `rsync`; путь `/usr/bin/rrsync` или `/usr/share/rsync/scripts/rrsync` — проверю на месте).
  Ретеншн на bcv2: `find /srv/backups/biotact-main -mtime +30 -delete` в cron `biotact-backup` (30 дней).
- Почему bcv2: данные Biotact остаются в инфраструктуре Biotact; контраргумент — тот же провайдер (Contabo);
  не перевесил: модель угроз — ошибка оператора/агента и компрометация прода, не отказ провайдера
  (Ревьюер согласен). Переключатель: воля Капитана → `netcup-1`, механизм тот же.

**1e · Проверка восстановления** (в пустую БД, затем удаляю) — из свежего дампа нового скрипта.
⚠️ Приватный `age`-ключ у Капитана — **единственный экземпляр**: потерян ключ = offsite-копии мусор (Ревьюер).
Капитану: хранить там, откуда достанешь при мёртвом проде (менеджер паролей + второе место), и в 1e лично
расшифровать копию своим ключом.
```bash
/usr/local/sbin/biotact-pg-backup.sh && ls -l /var/backups/biotact/ && journalctl -t biotact-pg-backup --no-pager | tail -2
sudo -u postgres createdb biotact_restore_test
sudo -u postgres pg_restore -d biotact_restore_test $(ls -t /var/backups/biotact/*.dump | head -1)
for db in biotact_restore_test biotact_db; do sudo -u postgres psql -d $db -tAc "select count(*) from users; select count(*) from lottery_tickets; select count(*) from orders;"; done   # тройки совпадают
sudo -u postgres dropdb biotact_restore_test
```
Плюс расшифровка offsite-копии на bcv2 **не** делается (приватного ключа там нет) — вместо этого на этой
машине/у Капитана: `age -d -i <priv> file.age | pg_restore --list | head` — доказательство, что копия
читаема с ключом.

**Гейт 2 — Ревьюеру (чтение):**
прод: `ssh biotact-main 'ls -ld /var/www/biotact/backups /var/backups/biotact; ls -l /var/backups/biotact | head; crontab -l; cat /etc/cron.d/biotact-pg-backup; ls -l /usr/local/sbin/biotact-pg-backup.sh /root/old-backup.sh.disabled; grep -c PGPASSWORD /usr/local/sbin/biotact-pg-backup.sh; pg_restore --list $(ls -t /var/backups/biotact/*.dump | head -1) | head -3; journalctl -t biotact-pg-backup --no-pager | tail -3; sshd -T >/dev/null; grep -c "38.242.138.184" /root/.ssh/known_hosts'`;
пароль: `ssh biotact-main 'PGPASSWORD="<старый из переписки>" psql -U biotact -h localhost -d biotact_db -c "select 1" 2>&1 | tail -1'` → `password authentication failed`;
bcv2: `ssh bcv2 'ls -l /srv/backups/biotact-main; grep -c rrsync ~biotact-backup/.ssh/authorized_keys; id biotact-backup'`.

### Шаг 2 · SSH — только по ключу

**Протокол против самозапирания:** до правки — (1) **Капитан открыл консоль Contabo и увидел приглашение логина**
(проверено открытием, не «она есть» — Гейт 2/1a); (2) **две** мои открытые сессии (рабочая + страховочная, не
закрываю до конца проверки); (3) **Ревьюер держит свою сессию** и после `reload` проверяет вход из нового процесса
независимо — два разных клиента видят результат с двух сторон. Правка — **новый файл**, чтобы откат был `rm`:
```bash
printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/00-hardening.conf
sshd -t && systemctl reload ssh
```
Проверка **из нового окна**: `ssh biotact-main 'sshd -T | grep -E "^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication) "'`
→ `prohibit-password` / `no` / `no`. Отрицательная проверка: `ssh -o PubkeyAuthentication=no -o PreferredAuthentications=password -p 2222 root@95.111.224.251` → отказ без запроса пароля (`Permission denied (publickey)`).
Деплой не затронут: он ключом. Откат из живой сессии: `rm /etc/ssh/sshd_config.d/00-hardening.conf && systemctl reload ssh`.

**Гейт 2 — Ревьюеру:** те же две команды из СВОЕЙ сессии (второе независимое подтверждение, что новые
подключения проходят) + `journalctl -u ssh --since "-30min" | grep -c "Failed password"` (счётчик должен
перестать расти: пароль теперь отклоняется до проверки).

### Шаг 3 · nginx — реальный IP в rate-limit

```bash
cp /etc/nginx/sites-enabled/biotact /root/biotact.nginx.bak-$(date +%F)
sed -i 's/proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;/proxy_set_header X-Forwarded-For $remote_addr;/' /etc/nginx/sites-enabled/biotact
grep -n "X-Forwarded-For" /etc/nginx/sites-enabled/biotact     # обе строки (22, 85) с $remote_addr
# + комментарий в конфиг над каждой строкой (замечание Ревьюера): "$remote_addr верен, пока перед сервером нет CDN;
#   поставите Cloudflare — rate-limit админ-логина схлопнется в один IP, вернуть доверенный XFF"

nginx -t && systemctl reload nginx
```
**Проверка (red → green на проде, согласовать момент с Капитаном):** 6 запросов
`POST https://admin.biotact.uz/api/v1/admin/auth/login` с неверным паролем и **разными** `X-Forwarded-For`.
До правки: все шесть → `401` (лимит не срабатывает — обход). После: шестой → `429`. ⚠️ Побочный эффект
проверки «после»: **наш внешний IP заблокирован для админ-логина на 15 минут** (`LOCKOUT_SECONDS = 900`);
делать в согласованное окно, Капитану в это время в админку не входить. Лимит хранится в памяти процесса
— `pm2 restart api` его сбрасывает (это не дыра, но знать).
Откат: `cp /root/biotact.nginx.bak-… /etc/nginx/sites-enabled/biotact && nginx -t && systemctl reload nginx`.

**Гейт 2 — Ревьюеру:** `ssh biotact-main 'grep -n "X-Forwarded-For" /etc/nginx/sites-enabled/biotact; nginx -t'`
+ мой вывод red/green (шесть кодов до, шесть после).

### Шаг 4 · Автообновления security-only

```bash
cp /etc/apt/apt.conf.d/20auto-upgrades /root/20auto-upgrades.bak
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' > /etc/apt/apt.conf.d/20auto-upgrades
unattended-upgrade --dry-run 2>&1 | tail -3
```
Источник уже только `${distro_id}:${distro_codename}-security` (проверено в `50unattended-upgrades`),
`Automatic-Reboot` = false (закомментирован). **Принятая цена:** минорные обновления `postgresql-16`/`nginx`
перезапускают службы на секунды — патчи безопасности БД важнее секунд.
**Гейт 2 — Ревьюеру:** `ssh biotact-main 'cat /etc/apt/apt.conf.d/20auto-upgrades; systemctl list-timers apt-daily* --no-pager'`.

### Шаг 5 · journald лимит

```bash
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/size.conf
systemctl restart systemd-journald && journalctl --disk-usage
```
Старые записи сверх 500 МБ будут удалены (осознанно). **Гейт 2 — Ревьюеру:** `ssh biotact-main 'cat /etc/systemd/journald.conf.d/size.conf; journalctl --disk-usage'`.

### Шаг 6 · Ключи root — после решения Капитана

`cp /root/.ssh/authorized_keys /root/.ssh/authorized_keys.bak-$(date +%F)`; удалить строки-обрывки 3 и 6
(`temrjan@claude-agent`, `biotact-miniapp` — по одному слову, sshd их игнорирует) и `root@vmi3025384`
(ключ самого сервера); строки «без подписи» (2) и `claude-code-main` (5) — по решению Капитана.
Ни один из известных ключей (`claude-migration`, `github-actions-deploy`, `multicard-cd`,
`protocols-bot-deploy`) не трогается. Проверка после: вход из нового окна + деплой-ключ цел
(`grep -c github-actions-deploy`).

---

## 3. Порядок и точки СТОП

**1a → 1b → 2 → 3 → 4 → 5 → 1c → 1d → 1e → (6)** — порядок Ревьюера: закрыть чтение дампов и ротировать пароль
раньше всего (учётка коллеги ближе по времени), offsite — после разбора со старым скриптом. После **каждого**
шага — отчёт-дельта Капитану с выводом до/после; Ревьюер перепроверяет чтением. Точки, где нужно слово
Капитана до старта: **1b** и **3** — общее окно (рестарт api/bot ≈ 12 с + 15-минутная блокировка нашего IP
для админ-логина; после ночного бэкапа, не в часы работы админки); **1d** — хост копии (реком. bcv2, Ревьюер
согласен) и кто хранит приватный `age`-ключ (реком. Капитан, менеджер паролей); **6** — судьба двух ключей.

## 4. Критерии приёмки

Права: каталог 700, дампы 600, скрипты 640/700 · пароль БД ротирован: `select 1` под новым проходит, под старым —
`password authentication failed`, `.env` api/bot обновлены, health 200 · новый скрипт без пароля, с валидацией и
громким отказом; старая строка crontab снята, старый скрипт выключен, история дампов перенесена · cron.d стоит,
первый запуск в journal · есть валидированный дамп и доказанное восстановление (тройки счётчиков совпадают) ·
**bcv2 (отдельная строка приёмки):** пользователь `biotact-backup`, каталог 700, `rrsync -wo` в ключе, копия `.age`
лежит и читаема приватным ключом у Капитана; отпечаток bcv2 в `known_hosts` прода занесён руками · `sshd -T` показывает `prohibit-password/no/no` и вход из **нового** окна проходит ·
парольный вход отвергается без запроса пароля · nginx: обе строки XFF = `$remote_addr`, red→green по 429 ·
`20auto-upgrades` = 1/1 · `journalctl --disk-usage` ≤ 500M · для каждого шага записан и **проверен**
откат-файл. Отчёт: `.claude/reports/2026-08-17-prod-hardening.md`.
