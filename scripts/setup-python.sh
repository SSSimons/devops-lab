#!/usr/bin/env bash
# Для deploy нужен отдельный Python с закреплённым ujson. Системный pip не меняем.
set -Eeuo pipefail
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_DIR=/var/lib/devops-lab/venv
[[ $EUID -eq 0 ]] || { echo 'Выполнить: sudo bash scripts/setup-python.sh' >&2; exit 1; }
[[ ! -L /var/lib/devops-lab && ! -L $RUNTIME_DIR ]] || { echo 'Каталог Python не должен быть символической ссылкой.' >&2; exit 1; }
EXPECTED_UJSON=$(sed -n 's/^ujson==\([0-9.]*\)$/\1/p' "$PROJECT_ROOT/requirements-runtime.txt")
[[ -n $EXPECTED_UJSON ]] || { echo 'Версия ujson не закреплена в requirements-runtime.txt.' >&2; exit 1; }
if [[ -x $RUNTIME_DIR/bin/python3 ]] && "$RUNTIME_DIR/bin/python3" -c \
    'import sys,ujson; sys.exit(ujson.__version__ != sys.argv[1])' "$EXPECTED_UJSON"; then
  exit 0
fi
if ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null | grep -q 'install ok installed'; then
  apt-get update
  apt-get install -y python3-venv
fi
install -d -m 0755 /var/lib/devops-lab
if [[ ! -x $RUNTIME_DIR/bin/python3 ]]; then
  /usr/bin/python3 -m venv "$RUNTIME_DIR"
fi
"$RUNTIME_DIR/bin/python3" -m pip install --disable-pip-version-check --only-binary=:all: \
  --timeout 30 --retries 3 -r "$PROJECT_ROOT/requirements-runtime.txt"
"$RUNTIME_DIR/bin/python3" -c 'import ujson; print("Python стенда: ujson " + ujson.__version__)'
