#!/bin/sh
# Запускаем nginx и ротируем его файловые логи в том же контейнере и от того же UID.
set -eu
umask 0027
LOG_DIR=${LOG_DIR:-/logs}
LOG_ROTATE_BYTES=${LOG_ROTATE_BYTES:-8388608}
LOG_ROTATE_KEEP=4
LOG_ROTATE_INTERVAL=${LOG_ROTATE_INTERVAL:-1}
LOG_ROTATE_SECONDS=10
ROTATION_SEQUENCE=0
ACCESS_ROTATED_AT=$(date +%s)
ERROR_ROTATED_AT=$ACCESS_ROTATED_AT
# Порог можно уменьшить для проверки, но нельзя превысить бюджет emptyDir 128 MiB.
case "$LOG_ROTATE_BYTES" in ''|*[!0-9]*) echo 'Некорректный размер ротации.' >&2; exit 2 ;; esac
if [ "$LOG_ROTATE_BYTES" -lt 1 ] || [ "$LOG_ROTATE_BYTES" -gt 8388608 ]; then
  echo 'Размер ротации должен быть от 1 до 8388608 байт.' >&2
  exit 2
fi
case "$LOG_ROTATE_INTERVAL" in 1|0.1) ;; *) echo 'Интервал проверки должен быть 1 или 0.1 секунды.' >&2; exit 2 ;; esac

nginx -g 'daemon off;' "$@" &
NGINX_PID=$!
STOP_REQUESTED=0
# Функция вызывается из trap, ShellCheck 0.10 не распознаёт этот косвенный вызов.
# shellcheck disable=SC2317
stop_nginx() {
  trap '' TERM INT
  # QUIT даёт nginx завершить текущие запросы; Kubernetes ограничивает время ожидания.
  kill -QUIT "$NGINX_PID" 2>/dev/null || true
  wait "$NGINX_PID" 2>/dev/null || true
}
# Завершаем текущую ротацию до остановки: TERM между ln и mv не оставит два имени
# одного активного файла и не прервёт удаление лишних архивов.
trap 'STOP_REQUESTED=1' TERM INT
trap stop_nginx EXIT

while kill -0 "$NGINX_PID" 2>/dev/null && [ "$STOP_REQUESTED" -eq 0 ]; do
  sleep "$LOG_ROTATE_INTERVAL"
  REOPEN=0
  for LOG_NAME in access error; do
    LOG_FILE="$LOG_DIR/$LOG_NAME.log"
    [ -f "$LOG_FILE" ] || continue
    LOG_SIZE=$(stat -c %s "$LOG_FILE")
    [ "$LOG_SIZE" -gt 0 ] || continue
    NOW=$(date +%s)
    case "$LOG_NAME" in access) LAST_ROTATED=$ACCESS_ROTATED_AT ;; error) LAST_ROTATED=$ERROR_ROTATED_AT ;; esac
    # При редких запросах агент получит записи не позднее следующей ротации по времени.
    if [ "$LOG_SIZE" -lt "$LOG_ROTATE_BYTES" ] && [ "$((NOW - LAST_ROTATED))" -lt "$LOG_ROTATE_SECONDS" ]; then
      continue
    fi
    # Сохраняем старый inode. copytruncate здесь не используется.
    # Архив получает уникальное имя и больше не переименовывается: это важно для in_tail.
    while :; do
      ROTATION_SEQUENCE=$((ROTATION_SEQUENCE + 1))
      ROTATED_FILE="$LOG_FILE.$(date +%s)-$$-$ROTATION_SEQUENCE"
      [ -e "$ROTATED_FILE" ] || break
    done
    NEXT_FILE="$LOG_DIR/.nginx-$LOG_NAME-$$.next"
    : > "$NEXT_FILE"
    ln "$LOG_FILE" "$ROTATED_FILE"
    # Атомарно заменяем файл: активный путь всегда существует, без гонки in_tail.
    mv "$NEXT_FILE" "$LOG_FILE"
    case "$LOG_NAME" in access) ACCESS_ROTATED_AT=$NOW ;; error) ERROR_ROTATED_AT=$NOW ;; esac
    REOPEN=1
  done
  if [ "$REOPEN" -eq 1 ]; then
    # Мастер и workers переоткрывают файлы. Никакие дополнительные capabilities не нужны.
    kill -USR1 "$NGINX_PID"
    for LOG_NAME in access error; do
      # Имена создаём сами, пробелов и переводов строк в них нет.
      # shellcheck disable=SC2012
      ls -1t "$LOG_DIR/$LOG_NAME.log."* 2>/dev/null | tail -n +$((LOG_ROTATE_KEEP + 1)) |
        while IFS= read -r OLD_LOG; do rm -f "$OLD_LOG"; done
    done
    echo 'Ротация логов nginx выполнена.' >&2
  fi
done
if [ "$STOP_REQUESTED" -eq 1 ]; then
  stop_nginx
  trap - EXIT
  exit 0
fi
STATUS=0
wait "$NGINX_PID" || STATUS=$?
exit "$STATUS"
