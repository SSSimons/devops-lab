#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${1:-} == --help ]]; then
  echo 'Запуск: sudo bash deploy.sh [--node-ip IPv4] [--resume-bootstrap]'
  echo 'Создаёт одноузловой kubeadm-стенд на выделенной Ubuntu 24.04 VM.'
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo 'Выполнить: sudo bash deploy.sh' >&2; exit 1; }
[[ ! -L $PROJECT_ROOT/.state ]] || { echo '.state не должна быть символической ссылкой.' >&2; exit 1; }
# Возвращаем отчёты пользователю, вызвавшему sudo, для подготовки паспорта и Git.
finish_reports() {
  if [[ ${SUDO_UID:-} =~ ^[0-9]+$ && ${SUDO_GID:-} =~ ^[0-9]+$ && -d $PROJECT_ROOT/.state && ! -L $PROJECT_ROOT/.state ]]; then
    chown -R --no-dereference -- "$SUDO_UID:$SUDO_GID" "$PROJECT_ROOT/.state"
  fi
}
trap finish_reports EXIT
bash "$PROJECT_ROOT/scripts/bootstrap.sh" "$@"
export PATH="/var/lib/devops-lab/venv/bin:$PATH"
export KUBECONFIG=/etc/kubernetes/admin.conf
bash "$PROJECT_ROOT/scripts/deploy-stack.sh"
python3 "$PROJECT_ROOT/scripts/verify.py" --report "$PROJECT_ROOT/.state/verification.json"
