#!/usr/bin/env bash
# Для выделенного стенда отключаем swap перед kubelet, в том числе после перезагрузки.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустить команду с sudo.' >&2; exit 1; }
[[ -f /var/lib/devops-lab/runtime-owner && $(cat /var/lib/devops-lab/runtime-owner) == devops-lab-containerd-v1 ]] \
  || { echo 'Для настройки нужен runtime, созданный этим стендом devops-lab.' >&2; exit 1; }
TEMP_DIR=$(mktemp -d)
trap 'rm -rf -- "$TEMP_DIR"' EXIT
cat > "$TEMP_DIR/no-swap.service" <<'UNIT'
[Unit]
Description=Отключение swap перед kubelet на выделенном узле devops-lab
After=swap.target
Before=kubelet.service
ConditionPathExists=/var/lib/devops-lab/runtime-owner

[Service]
Type=oneshot
ExecStart=/usr/sbin/swapoff -a
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
cat > "$TEMP_DIR/kubelet.conf" <<'UNIT'
[Unit]
Requires=devops-lab-no-swap.service
After=devops-lab-no-swap.service
UNIT
install -d -m 0755 /etc/systemd/system/kubelet.service.d
FILES_CHANGED=false
for pair in 'no-swap.service:/etc/systemd/system/devops-lab-no-swap.service' \
            'kubelet.conf:/etc/systemd/system/kubelet.service.d/20-devops-lab-no-swap.conf'; do
  SOURCE=${pair%%:*}
  DESTINATION=${pair#*:}
  [[ ! -L $DESTINATION ]] || { echo "Символическая ссылка не заменяется: $DESTINATION" >&2; exit 1; }
  if ! cmp -s "$TEMP_DIR/$SOURCE" "$DESTINATION"; then
    install -m 0644 "$TEMP_DIR/$SOURCE" "$DESTINATION"
    FILES_CHANGED=true
  fi
done
if $FILES_CHANGED; then systemctl daemon-reload; fi
ACTIVE_SWAP=$(swapon --noheadings --show=NAME)
if [[ -n $ACTIVE_SWAP ]]; then swapoff -a; fi
systemctl enable --now devops-lab-no-swap.service
[[ -z $(swapon --noheadings --show=NAME) ]] || { echo 'Swap всё ещё включён, kubelet не сможет запуститься.' >&2; exit 1; }
if [[ -n $ACTIVE_SWAP ]]; then echo changed; else echo unchanged; fi
