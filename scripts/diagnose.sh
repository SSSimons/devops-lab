#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -z ${KUBECONFIG:-} && $EUID -eq 0 && -r /etc/kubernetes/admin.conf ]]; then
  export KUBECONFIG=/etc/kubernetes/admin.conf
fi
k() { kubectl --request-timeout=15s "$@"; }
if ! k get nodes -o wide; then
  echo 'Kubernetes API недоступен с текущим kubeconfig.' >&2
  echo 'Диагностика узла без изменения настроек: sudo bash scripts/diagnose-host.sh' >&2
  echo 'Для этого kubeadm-стенда выполнить: sudo env KUBECONFIG=/etc/kubernetes/admin.conf bash scripts/diagnose.sh' >&2
  exit 1
fi
k get pods -A -o wide
# Установка могла остановиться до создания пространства имён приложения.
k -n kube-system get pods -l k8s-app=kube-dns -o wide
k -n kube-system describe pods -l k8s-app=kube-dns
k -n kube-system logs -l k8s-app=kube-dns --tail=60 --prefix=true
k -n kube-system logs -l k8s-app=kube-dns --previous --tail=60 --prefix=true 2>/dev/null || true
if [[ $EUID -eq 0 ]]; then
  echo 'Путь к DNS-файлу kubelet:'
  awk '/^resolvConf:/ {print}' /var/lib/kubelet/config.yaml 2>/dev/null || true
  echo 'DNS-серверы, настроенные на узле:'
  for resolver in /var/lib/devops-lab/resolv.conf /run/systemd/resolve/resolv.conf /etc/resolv.conf; do
    if [[ -r $resolver ]]; then
      echo "$resolver"
      awk '/^nameserver[[:space:]]/ {print}' "$resolver"
    fi
  done
fi
k -n kube-system get events --sort-by=.metadata.creationTimestamp | tail -40
if k get namespace nginx-gateway >/dev/null 2>&1; then
  k -n nginx-gateway logs deployment/nginx-gateway --tail=60
fi
if k get namespace devops-lab >/dev/null 2>&1; then
  k -n devops-lab get gateway,httproute,svc
  k -n devops-lab describe gateway lab
  k -n devops-lab describe httproute web
  k -n devops-lab describe httproute api-info legacy-redirect
  k -n devops-lab get statefulset web prometheus -o yaml
  k -n devops-lab describe pod web-0
  k -n devops-lab get endpointslices -l kubernetes.io/service-name=web -o yaml
  k -n devops-lab get events --sort-by=.metadata.creationTimestamp | tail -40
  k -n devops-lab logs web-0 -c fluentd --tail=60
  k -n devops-lab logs web-0 -c fluentd --previous --tail=60 2>/dev/null || true
  k -n devops-lab logs web-0 -c nginx --tail=30
  k -n devops-lab logs deployment/nginx-exporter --tail=30
  k -n devops-lab logs prometheus-0 --tail=30
  k -n devops-lab describe pods -l app=nginx-exporter
  k -n devops-lab describe pod prometheus-0
  k -n devops-lab get endpointslices -l kubernetes.io/service-name=nginx-exporter -o yaml
  if k get namespace nginx-gateway >/dev/null 2>&1; then
    k -n nginx-gateway get service nginx-gateway -o yaml
    k -n nginx-gateway get endpointslices -l kubernetes.io/service-name=nginx-gateway -o yaml
  fi
  # Используем тот же ujson, что и deploy, без установки пакетов при диагностике.
  if [[ -f $SCRIPT_DIR/diagnose-prometheus.py ]] && k -n devops-lab get service prometheus >/dev/null 2>&1; then
    diagnostic_python=python3
    for candidate in /var/lib/devops-lab/venv/bin/python3 "$SCRIPT_DIR/../.venv/bin/python3"; do
      if [[ -x $candidate ]]; then
        diagnostic_python=$candidate
        break
      fi
    done
    if "$diagnostic_python" -c 'import ujson' >/dev/null 2>&1; then
      "$diagnostic_python" "$SCRIPT_DIR/diagnose-prometheus.py" || true
    else
      echo 'Для чтения источников нужен Python с ujson: sudo bash scripts/setup-python.sh' >&2
    fi
  fi
fi
