#!/usr/bin/env python3
"""Проверить подготовленные к коммиту файлы, не изменяя их и не выводя секреты."""
import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DIRS = {'.state', '.venv', 'venv', 'env', '.kube', '.ssh', '__pycache__',
                '.pytest_cache', '.mypy_cache', '.ruff_cache', 'submission', 'output', 'dist', 'build'}
BLOCKED_NAMES = {'admin.conf', 'super-admin.conf', 'kubeconfig', 'secrets.yaml', 'secrets.yml'}
PATTERNS = {
    'приватный ключ': rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----',
    'токен GitHub': rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{40,255})\b',
    'ключ доступа AWS': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'секреты внутри kubeconfig': rb'(?m)^\s*client-(?:key|certificate)-data:\s*[A-Za-z0-9+/=]{20,}',
}


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, timeout=20).stdout


def blocked_path(name):
    path = PurePosixPath(name)
    return (bool(BLOCKED_DIRS.intersection(path.parts))
            or path.name in BLOCKED_NAMES
            or path.name.startswith(('id_rsa', 'id_ed25519', 'kubeconfig.'))
            or (path.name.startswith('.env') and path.name not in {'.env.example', '.env.sample'})
            or '.log.' in path.name
            or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx', '.kubeconfig', '.zip', '.rar', '.log'})


def check(root):
    """Читаем именно подготовленные Git-объекты: правка рабочего файла не очищает старый index."""
    resolved = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if resolved != root.resolve():
        raise ValueError('Запустить из репозитория проекта, а не из родительского репозитория.')
    entries = git(root, 'ls-files', '--stage', '-z').split(b'\0')
    problems = []
    names = set()
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode().split()
        name = raw_name.decode('utf-8', errors='replace')
        names.add(name)
        if stage != '0':
            problems.append((name, 'конфликт слияния не разрешён'))
            continue
        if mode not in {'100644', '100755'}:
            problems.append((name, 'символическую ссылку или подмодуль нужно проверить отдельно'))
            continue
        if blocked_path(name):
            problems.append((name, 'локальный файл или файл с секретами нужно убрать из индекса Git'))
            continue
        size = int(git(root, 'cat-file', '-s', oid).strip())
        if size >= 100_000_000:
            problems.append((name, 'размер файла достигает ограничения GitHub в 100 MB'))
            continue
        content = git(root, 'cat-file', 'blob', oid)
        for label, pattern in PATTERNS.items():
            if re.search(pattern, content):
                problems.append((name, label))
        if (re.search(rb'(?m)^kind:\s*Config\s*$', content)
                and re.search(rb'(?m)^clusters:', content) and re.search(rb'(?m)^users:', content)):
            problems.append((name, 'kubeconfig должен храниться только локально'))
    for required in ('README.md', 'deploy.sh', 'docs/passport.pdf'):
        if required not in names:
            problems.append((required, 'обязательный файл проекта не добавлен в индекс Git'))
    return names, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        names, problems = check(args.root)
        if problems:
            for name, reason in problems:
                print(f'ERROR: {name}: {reason}', file=sys.stderr)
            return 1
        print(f'OK: {len(names)} файлов в индексе; запрещённых путей и распознанных секретов не найдено.')
        print('Перед коммитом проверить git diff --cached. Историю Git этот скрипт не проверяет.')
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Проверка перед публикацией завершилась ошибкой: ' + str(error), file=sys.stderr)
        print('При необходимости выполнить git init -b main, затем git add . и повторить проверку.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
