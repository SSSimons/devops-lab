#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -x /var/lib/devops-lab/venv/bin/python3 ]]; then export PATH="/var/lib/devops-lab/venv/bin:$PATH"; fi
[[ $EUID -eq 0 ]] || { echo 'Выполнить: sudo bash scripts/check-repeat.sh' >&2; exit 1; }
cd "$PROJECT_ROOT"
[[ ! -L .state ]] || { echo '.state не должна быть символической ссылкой.' >&2; exit 1; }
mkdir -p .state
finish_reports() {
  if [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ ]]; then
    chown -R --no-dereference -- "$SUDO_UID:$SUDO_GID" .state
  fi
}
trap finish_reports EXIT
# Удаляем старые отчёты: один успешный запуск не должен выглядеть как два.
rm -f .state/first-deploy.json .state/repeat-deploy.json
for attempt in first repeat; do
  echo "=== Запуск развёртывания: $attempt ==="
  bash deploy.sh "$@" 2>&1 | tee ".state/$attempt-deploy.log"
  cp .state/verification.json ".state/$attempt-deploy.json"
done
python3 scripts/collect-versions.py
echo 'Оба развёртывания и проверки прошли. Отчёты: .state/*-deploy.json и .state/versions.json.'
