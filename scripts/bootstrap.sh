#!/usr/bin/env bash
set -Eeuo pipefail
K8S_VERSION=1.35.9
K8S_PACKAGE_VERSION=1.35.9-1.1
STATE_DIR=/var/lib/devops-lab
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
NODE_IP=''
RESUME_BOOTSTRAP=false
while (($#)); do
  case "$1" in
    --node-ip) [[ $# -ge 2 ]] || exit 2; NODE_IP=$2; shift 2 ;;
    --resume-bootstrap) RESUME_BOOTSTRAP=true; shift ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
done
die() { echo "ERROR: $*" >&2; exit 1; }
has_static_pods() {
  local directory=$1
  local candidate
  [[ -d $directory ]] || return 1
  # kubelet пропускает скрытые файлы, остальные читает независимо от расширения.
  candidate=$(find -L "$directory" -mindepth 1 -maxdepth 1 ! -name '.*' -type f -print -quit) || die 'Не удалось проверить каталог статических Pod.'
  [[ -n $candidate ]]
}
[[ $EUID -eq 0 ]] || die 'Запустить команду с sudo.'
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die 'Для стенда нужна Ubuntu 24.04.'
[[ $(uname -m) == x86_64 ]] || die 'Стенд рассчитан на архитектуру amd64.'
[[ $(nproc) -ge 2 ]] || die 'Нужно не менее 2 vCPU.'
awk '/MemTotal/ {exit ($2 < 3800000)}' /proc/meminfo || die 'Нужно не менее 4 GB RAM, рекомендую 8 GB.'
[[ $(df --output=avail -B1 /var | tail -1) -gt 10737418240 ]] || die 'Нужно не менее 10 GB свободного места.'
if [[ -f /etc/kubernetes/admin.conf && ! -f $STATE_DIR/managed-cluster ]]; then
  die 'Найден чужой Kubernetes-кластер. Для стенда использовать свежую выделенную VM.'
fi
if [[ -f $STATE_DIR/managed-cluster && ! -f /etc/kubernetes/admin.conf ]] && has_static_pods /etc/kubernetes/manifests; then
  die 'Найден незавершённый kubeadm init. Восстановление описано в README; автоматический сброс отключён.'
fi
if command -v ufw >/dev/null && LC_ALL=C ufw status | head -1 | grep -q 'Status: active'; then
  die 'UFW включён. Перед запуском настроить firewall по инструкции для выделенной VM в README.'
fi
if swapon --noheadings --show=NAME | grep -q '/dev/zram'; then
  die 'Сначала отключить генератор zram на VM. Порядок описан в README.'
fi
if [[ -f /etc/kubernetes/admin.conf ]]; then
  [[ $(cat "$STATE_DIR/managed-cluster") == "$K8S_VERSION" ]] || die 'Версия своего кластера отличается от закреплённой. Автоматическое обновление отключено.'
  # На существующем стенде новая зависимость также должна появиться до Python-проверок.
  bash "$SCRIPT_DIR/setup-python.sh"
  export PATH="$STATE_DIR/venv/bin:$PATH"
  export KUBECONFIG=/etc/kubernetes/admin.conf
  # WSL может снова включить swap при загрузке. Отключаем его до обращения к API.
  SWAP_CHANGED=$(bash "$SCRIPT_DIR/configure-swap.sh")
  if [[ $SWAP_CHANGED == changed ]]; then
    echo 'Swap отключён. Перезапускаем kubelet и ждём API существующего кластера.'
    systemctl restart kubelet
  fi
  python3 "$SCRIPT_DIR/check-cluster.py" --kubeconfig /etc/kubernetes/admin.conf --expected "$K8S_VERSION" --wait 120 \
    || die 'Существующий кластер не готов. Выполнить sudo bash scripts/diagnose-host.sh; см. docs/RECOVERY.md.'
  DNS_CHANGED=$(python3 "$SCRIPT_DIR/configure-dns.py" --output "$STATE_DIR/resolv.conf" --kubelet-config /var/lib/kubelet/config.yaml)
  if [[ $DNS_CHANGED == changed ]]; then
    echo 'DNS-файл kubelet обновлён. Перезапускаем kubelet и CoreDNS.'
    systemctl restart kubelet
    kubectl --request-timeout=30s -n kube-system rollout restart deployment/coredns
  fi
  echo 'Свой kubeadm-кластер уже создан. Повторная установка системных пакетов не требуется.'
  exit 0
fi
if dpkg-query -W -f='${Status}' containerd.io 2>/dev/null | grep -q 'install ok installed'; then
  die 'Найден containerd.io от Docker. Использовать выделенную VM без Docker, чтобы сохранить его конфиг.'
fi
if [[ ! -f $STATE_DIR/runtime-owner && ! -f $STATE_DIR/managed-cluster ]]; then
  if $RESUME_BOOTSTRAP; then
    # Проверяем следы предыдущего сбоя containerd, который произошёл до kubeadm init.
    [[ -s $STATE_DIR/containerd.new && -f $STATE_DIR/fstab.before && -s $STATE_DIR/kubernetes-release.key ]] || die 'Нет файлов предыдущего запуска bootstrap. Существующий runtime не перенастраивается.'
    [[ -f /etc/modules-load.d/devops-lab.conf && -f /etc/sysctl.d/99-devops-lab.conf ]] || die 'Не найдены системные настройки предыдущего запуска bootstrap.'
    [[ ! -f /etc/kubernetes/admin.conf && ! -f $STATE_DIR/managed-cluster ]] || die 'Продолжение bootstrap разрешено только до kubeadm init.'
    if has_static_pods /etc/kubernetes/manifests; then
      die 'Найдены манифесты статических Pod. Автоматическое восстановление отключено.'
    fi
    command -v containerd >/dev/null || die 'Не найден ранее установленный containerd из Ubuntu.'
    command -v ctr >/dev/null || die 'Не найден ранее установленный ctr из Ubuntu.'
    if systemctl is-active --quiet containerd; then
      NAMESPACES=$(timeout 15s ctr --address /run/containerd/containerd.sock namespaces list --quiet) || die 'Не удалось безопасно проверить существующий runtime.'
      while IFS= read -r namespace; do
        [[ -n $namespace ]] || continue
        CONTAINERS=$(timeout 15s ctr --address /run/containerd/containerd.sock --namespace "$namespace" containers list --quiet) || die 'Не удалось безопасно проверить существующие контейнеры.'
        TASKS=$(timeout 15s ctr --address /run/containerd/containerd.sock --namespace "$namespace" tasks list --quiet) || die 'Не удалось безопасно проверить задачи containerd.'
        [[ -z $CONTAINERS && -z $TASKS ]] || die 'Найдены работающие или сохранённые контейнеры. Их runtime не перенастраивается.'
      done <<< "$NAMESPACES"
    elif [[ -s /var/lib/containerd/io.containerd.metadata.v1.bolt/meta.db ]]; then
      die 'containerd остановлен, сохранённые контейнеры проверить нельзя. Выполнить sudo systemctl start containerd, затем повторить --resume-bootstrap.'
    fi
    echo 'Продолжаем bootstrap после предыдущей ошибки настройки containerd, до kubeadm init.'
  elif [[ -f /etc/containerd/config.toml ]] || dpkg-query -W -f='${Status}' containerd 2>/dev/null | grep -q 'install ok installed'; then
    die 'Найден чужой containerd. После прежней ошибки настройки можно использовать --resume-bootstrap. В остальных случаях нужна свежая выделенная VM.'
  fi
fi
if [[ -f $STATE_DIR/runtime-owner ]]; then
  [[ $(cat "$STATE_DIR/runtime-owner") == devops-lab-containerd-v1 ]] || die 'Признак владельца runtime не соответствует этому стенду.'
fi
if [[ -z $NODE_IP ]]; then
  NODE_IP=$(ip -4 route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')
fi
python3 - "$NODE_IP" <<'PY'
import ipaddress, sys
ip = ipaddress.IPv4Address(sys.argv[1])
if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
    sys.exit('Указать стабильный IPv4 узла вне loopback.')
PY
ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$NODE_IP" || die 'NODE_IP не назначен ни одному сетевому интерфейсу узла.'
export DEBIAN_FRONTEND=noninteractive
# Сохраняем признак своего runtime до установки пакетов, чтобы разрешить повторный запуск.
install -d -m 0755 "$STATE_DIR"
printf 'devops-lab-containerd-v1\n' > "$STATE_DIR/runtime-owner"
apt-get update
apt-get install -y ca-certificates curl gnupg containerd python3 python3-venv git iproute2 conntrack socat
bash "$SCRIPT_DIR/setup-python.sh"
export PATH="$STATE_DIR/venv/bin:$PATH"
NODE_IFACE=$(ip -j -4 addr show | python3 -c 'import ujson,sys; ip=sys.argv[1]; print(next(i["ifname"] for i in ujson.load(sys.stdin) if any(a.get("local")==ip for a in i["addr_info"])))' "$NODE_IP")
[[ $NODE_IFACE =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Имя сетевого интерфейса не поддерживается.'
install -d -m 0755 /etc/apt/keyrings /etc/containerd "$STATE_DIR"
curl --fail --show-error --silent --location --retry 3 --max-time 120 \
  https://pkgs.k8s.io/core:/stable:/v1.35/deb/Release.key -o "$STATE_DIR/kubernetes-release.key"
gpg --batch --yes --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg "$STATE_DIR/kubernetes-release.key"
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.35/deb/ /' > /etc/apt/sources.list.d/kubernetes.list
apt-get update
apt-get install -y "kubelet=$K8S_PACKAGE_VERSION" "kubeadm=$K8S_PACKAGE_VERSION" "kubectl=$K8S_PACKAGE_VERSION"
apt-mark hold kubelet kubeadm kubectl
if [[ ! -f $STATE_DIR/fstab.before ]]; then cp -p /etc/fstab "$STATE_DIR/fstab.before"; fi
awk '$1 !~ /^#/ && $3 == "swap" {print "# devops-lab отключил swap: " $0; next} {print}' /etc/fstab > "$STATE_DIR/fstab.new"
cat "$STATE_DIR/fstab.new" > /etc/fstab
bash "$SCRIPT_DIR/configure-swap.sh"
printf 'overlay\nbr_netfilter\n' > /etc/modules-load.d/devops-lab.conf
modprobe overlay
modprobe br_netfilter
cat > /etc/sysctl.d/99-devops-lab.conf <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
sysctl --system
containerd config default > "$STATE_DIR/containerd.new"
PAUSE_IMAGE=$(kubeadm config images list --kubernetes-version "v$K8S_VERSION" | grep '/pause:')
python3 "$SCRIPT_DIR/configure-containerd.py" "$STATE_DIR/containerd.new" "$PAUSE_IMAGE"
# Проверяем конфиг установленным containerd до замены файла и перезапуска службы.
containerd --config "$STATE_DIR/containerd.new" config dump > "$STATE_DIR/containerd.validated.toml"
python3 "$SCRIPT_DIR/configure-containerd.py" "$STATE_DIR/containerd.validated.toml" "$PAUSE_IMAGE" --check
containerd --version
if [[ -f /etc/containerd/config.toml && ! -e $STATE_DIR/containerd.config.before ]]; then
  cp -p /etc/containerd/config.toml "$STATE_DIR/containerd.config.before"
fi
install -m 0644 "$STATE_DIR/containerd.new" /etc/containerd/config.toml
systemctl enable --now containerd kubelet
systemctl restart containerd
python3 "$SCRIPT_DIR/configure-dns.py" --output "$STATE_DIR/resolv.conf"
echo "$K8S_VERSION" > "$STATE_DIR/managed-cluster"
cat > "$STATE_DIR/kubeadm.yaml" <<EOF
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: "$NODE_IP"
  bindPort: 6443
nodeRegistration:
  criSocket: unix:///run/containerd/containerd.sock
  kubeletExtraArgs:
    - name: node-ip
      value: "$NODE_IP"
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
clusterName: devops-lab
kubernetesVersion: v$K8S_VERSION
networking:
  podSubnet: 10.244.0.0/16
  serviceSubnet: 10.96.0.0/12
apiServer:
  certSANs:
    - "$NODE_IP"
---
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
resolvConf: "$STATE_DIR/resolv.conf"
containerLogMaxSize: 10Mi
containerLogMaxFiles: 3
EOF
ip -j -4 route show | python3 -c '
import ipaddress, ujson, sys
reserved = [ipaddress.ip_network("10.244.0.0/16"), ipaddress.ip_network("10.96.0.0/12")]
for route in ujson.load(sys.stdin):
    dst = route.get("dst", "default")
    if dst == "default": continue
    network = ipaddress.ip_network(dst, strict=False)
    if any(network.overlaps(cidr) for cidr in reserved):
        sys.exit("Route " + dst + " overlaps pod/service CIDRs. Use an isolated VM/network.")
'
kubeadm init --config "$STATE_DIR/kubeadm.yaml"
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl taint nodes --all node-role.kubernetes.io/control-plane- >/dev/null 2>&1 || true
printf '%s\n' "$NODE_IFACE" > "$STATE_DIR/node-interface"
if [[ -n ${SUDO_USER:-} && $SUDO_USER != root ]]; then
  USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
  install -d -m 0700 -o "$SUDO_USER" -g "$(id -gn "$SUDO_USER")" "$USER_HOME/.kube"
  if [[ ! -e $USER_HOME/.kube/config ]]; then
    install -m 0600 -o "$SUDO_USER" -g "$(id -gn "$SUDO_USER")" /etc/kubernetes/admin.conf "$USER_HOME/.kube/config"
  else
    echo 'Существующий kubeconfig пользователя сохранён. В root-shell выполнить export KUBECONFIG=/etc/kubernetes/admin.conf.'
  fi
fi
