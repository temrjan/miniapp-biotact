#!/usr/bin/env bash
# Named worker lifecycle; called only by an authorized release in this checkout.
set -euo pipefail
cd /var/www/biotact
worker='employee-referral-worker'
python='packages/api/venv/bin/python'
case "${1:-}" in
  pause)
    pm2 stop api --namespace biotact >/dev/null 2>&1
    if pm2 pid "$worker" --namespace biotact | awk '$1+0>0 {found=1} END {exit !found}'; then
      pm2 stop "$worker" --namespace biotact >/dev/null 2>&1
    fi
    ;;
  start)
    pm2 startOrRestart ecosystem.config.js --only "$worker" --update-env >/dev/null 2>&1
    pm2 restart api --namespace biotact --update-env >/dev/null 2>&1
    bash ops/employee-referral-release.sh check
    pm2 save >/dev/null 2>&1
    ;;
  check)
    pm2 jlist | "$python" ops/employee-referral-runtime.py
    (cd packages/api && venv/bin/python -m src.employee_referrals.worker health)
    ;;
  rollback)
    previous="${2:?previous revision required}"
    cutover="${3:?cutover status required}"
    if git cat-file -e "$previous:packages/api/src/employee_referrals/worker.py" 2>/dev/null; then
      git reset --hard "$previous" >/dev/null
      pm2 startOrRestart ecosystem.config.js --only "$worker" --update-env >/dev/null 2>&1
      pm2 restart api --namespace biotact --update-env >/dev/null 2>&1
      bash ops/employee-referral-release.sh check
      pm2 save >/dev/null 2>&1
    elif [ "$cutover" = 'false' ]; then
      # No schema cutover happened; remove only the newly introduced process.
      if pm2 pid "$worker" --namespace biotact | awk '$1+0>0 {found=1} END {exit !found}'; then
        pm2 delete "$worker" --namespace biotact >/dev/null 2>&1
      fi
      git reset --hard "$previous" >/dev/null
      pm2 restart api --namespace biotact --update-env >/dev/null 2>&1
      pm2 save >/dev/null 2>&1
    else
      pm2 stop api --namespace biotact >/dev/null 2>&1
      pm2 stop "$worker" --namespace biotact >/dev/null 2>&1 || true
      echo 'Referral cutover requires a compatible rollback; affected writers paused'
      exit 1
    fi
    ;;
  *) echo 'Invalid referral release operation'; exit 2 ;;
esac
