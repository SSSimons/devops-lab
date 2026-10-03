#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -x /var/lib/devops-lab/venv/bin/python3 ]]; then export PATH="/var/lib/devops-lab/venv/bin:$PATH"; fi
mkdir -p "$PROJECT_ROOT/.state"
trap 'echo "Развёртывание остановилось на строке $LINENO. Выполнить: bash scripts/diagnose.sh" >&2' ERR
cd "$PROJECT_ROOT"
sha256sum -c vendor/SHA256SUMS
kubectl get nodes -o wide
[[ $(kubectl get nodes --no-headers | wc -l) == 1 ]] || { echo 'Для этого стенда нужен ровно один узел.' >&2; exit 1; }
kubectl apply -f vendor/flannel.yaml
if [[ -f /var/lib/devops-lab/node-interface ]]; then
  NODE_IFACE=$(cat /var/lib/devops-lab/node-interface)
  kubectl -n kube-flannel patch daemonset kube-flannel-ds --type=json \
    -p "[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args\",\"value\":[\"--ip-masq\",\"--kube-subnet-mgr\",\"--iface=$NODE_IFACE\"]}]"
fi
kubectl -n kube-flannel rollout status daemonset/kube-flannel-ds --timeout=300s
kubectl wait nodes --all --for=condition=Ready --timeout=300s
kubectl -n kube-system rollout status deployment/coredns --timeout=300s
for manifest in vendor/gateway-*.yaml; do kubectl apply --server-side -f "$manifest"; done
kubectl apply --server-side -f vendor/nginx-crds.yaml
kubectl wait --for=condition=Established --timeout=120s crd --all
kubectl apply -f vendor/nginx-gateway.yaml
kubectl -n nginx-gateway rollout status deployment/nginx-gateway --timeout=300s
kubectl wait --for=condition=Accepted --timeout=120s gatewayclass/nginx
kubectl apply -f k8s/00-namespace.yaml
kubectl -n devops-lab create configmap web-log-rotation \
  --from-file=nginx-run.sh=scripts/nginx-run.sh \
  --dry-run=client -o yaml | kubectl apply --server-side -f -
kubectl -n devops-lab create configmap web-cats \
  --from-file=cats.html=web/cats.html --from-file=cat.png=web/cat.png \
  --dry-run=client -o yaml | kubectl apply --server-side -f -
kubectl apply -f k8s/10-app.yaml
kubectl apply -f k8s/20-gateway.yaml
kubectl apply -f k8s/30-monitoring.yaml
kubectl apply -f k8s/40-log-retention.yaml
# Обновляем hash до ожидания готовности: старый конфиг в subPath может блокировать запуск.
for workload in statefulset/web statefulset/prometheus; do
  # У каждого StatefulSet свой hash: изменение котика не перезапускает Prometheus.
  case "$workload" in
    statefulset/web) CONFIG_FILES=(k8s/10-app.yaml scripts/nginx-run.sh web/cats.html web/cat.png) ;;
    statefulset/prometheus) CONFIG_FILES=(k8s/30-monitoring.yaml) ;;
  esac
  CONFIG_HASH=$(sha256sum "${CONFIG_FILES[@]}" | sha256sum | cut -d' ' -f1)
  OLD_HASH=$(kubectl -n devops-lab get "$workload" -o jsonpath='{.spec.template.metadata.annotations.devops-lab/config-hash}')
  if [[ $OLD_HASH != "$CONFIG_HASH" ]]; then
    kubectl -n devops-lab patch "$workload" --type=merge \
      -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"devops-lab/config-hash\":\"$CONFIG_HASH\"}}}}}"
  fi
  python3 scripts/recover-statefulset.py "${workload#statefulset/}"
  kubectl -n devops-lab rollout status "$workload" --timeout=300s
done
kubectl -n devops-lab rollout status deployment/nginx-exporter --timeout=300s
kubectl -n devops-lab wait --for=condition=Programmed --timeout=180s gateway/lab
echo 'Компоненты развёрнуты. Проверка: python3 scripts/verify.py'
