#!/usr/bin/env bash
# Подготавливаем файлы к коммиту. Команды публикации описаны в docs/GITHUB.md.
set -Eeuo pipefail
[[ $EUID -ne 0 ]] || { echo 'Запустить обычным пользователем, без sudo.' >&2; exit 1; }
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
command -v git >/dev/null || { echo 'Сначала установить git.' >&2; exit 1; }
if git rev-parse --show-toplevel >/dev/null 2>&1; then
  [[ $(git rev-parse --show-toplevel) == "$PROJECT_ROOT" ]] \
    || { echo 'Проект находится внутри другого репозитория. Перенести его в отдельную папку.' >&2; exit 1; }
else
  git init -b main
fi
git add --renormalize .
git add --all
python3 scripts/check-publication.py
git diff --cached --check
git status --short
echo 'Файлы подготовлены к коммиту. Проверка: git diff --cached. Команды публикации: docs/GITHUB.md.'
