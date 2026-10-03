#!/usr/bin/env bash
# Диагностика узла и control plane без изменения настроек. Рабочий API не требуется.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo 'Выполнить: sudo bash scripts/diagnose-host.sh' >&2; exit 1; }
report() {
  local label=$1
  shift
  echo "=== $label ==="
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Команда недоступна: $1"
    return 0
  fi
  timeout 20s "$@" 2>&1 || true
}
report 'Процесс PID 1, для стенда нужен systemd' ps -p 1 -o comm=
report 'Текущие IPv4-адреса' ip -o -4 addr show
report 'Адрес узла для маршрута по умолчанию' ip -4 route get 1.1.1.1
if [[ -r /etc/kubernetes/admin.conf ]]; then
  report 'Адрес API из admin.conf, без секретов' kubectl --kubeconfig=/etc/kubernetes/admin.conf \
    config view --minify -o 'jsonpath={.clusters[0].cluster.server}{"\n"}'
fi
for manifest in /etc/kubernetes/manifests/kube-apiserver.yaml /etc/kubernetes/manifests/etcd.yaml; do
  if [[ -r $manifest ]]; then
    echo "=== Адреса в $manifest ==="
    awk '/--(advertise-address|bind-address|listen-client-urls|listen-peer-urls|advertise-client-urls|initial-advertise-peer-urls)=/ {print}' "$manifest"
  fi
done
report 'Состояние служб: containerd, kubelet' systemctl is-active containerd kubelet
report 'Автозапуск служб: containerd, kubelet' systemctl is-enabled containerd kubelet
report 'Открытые порты control plane' ss -lntp '( sport = :6443 or sport = :2379 or sport = :2380 )'
report 'Активный swap' swapon --show
report 'Оперативная память' free -h
report 'Место на диске' df -h /var
report 'Последние сообщения kubelet' journalctl -u kubelet -n 60 --no-pager
report 'Последние сообщения containerd' journalctl -u containerd -n 40 --no-pager
if command -v crictl >/dev/null 2>&1; then
  CRI=(crictl --runtime-endpoint=unix:///run/containerd/containerd.sock)
  for component in kube-apiserver etcd; do
    report "Контейнеры $component, включая завершённые" "${CRI[@]}" ps -a --name "$component"
    IDS=$(timeout 15s "${CRI[@]}" ps -a --name "$component" --state Exited --quiet 2>/dev/null) || IDS=''
    if [[ -z $IDS ]]; then
      IDS=$(timeout 15s "${CRI[@]}" ps -a --name "$component" --quiet 2>/dev/null) || IDS=''
    fi
    CONTAINER_ID=${IDS%%$'\n'*}
    if [[ $CONTAINER_ID =~ ^[a-f0-9]{8,64}$ ]]; then
      report "Лог контейнера $component" "${CRI[@]}" logs --tail=50 "$CONTAINER_ID"
    fi
  done
else
  echo 'crictl недоступен. Проверить состояние служб, порты и журналы выше.'
fi
echo 'Диагностика завершена, настройки не изменялись. В выводе есть локальные адреса, не добавлять его в публичный репозиторий.'
