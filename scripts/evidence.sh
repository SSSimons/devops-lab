#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -x /var/lib/devops-lab/venv/bin/python3 ]]; then export PATH="/var/lib/devops-lab/venv/bin:$PATH"; fi
mkdir -p "$PROJECT_ROOT/.state"
{
  date -u --iso-8601=seconds
  cat /etc/os-release
  uname -a
  kubectl version -o yaml
  kubectl get nodes -o wide
  dpkg-query -W containerd kubeadm kubelet kubectl 2>/dev/null || true
  kubectl -n devops-lab get pods,svc,gateway,httproute -o wide
  kubectl -n devops-lab get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{range .status.containerStatuses[*]}{.image}{" "}{.imageID}{"\n"}{end}{end}'
} > "$PROJECT_ROOT/.state/environment.txt"
python3 "$PROJECT_ROOT/scripts/verify.py" --report "$PROJECT_ROOT/.state/verification.json"
python3 "$PROJECT_ROOT/scripts/collect-versions.py"
echo "Результаты проверки сохранены в $PROJECT_ROOT/.state. Эта папка исключена из Git."
