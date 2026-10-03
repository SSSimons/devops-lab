#!/usr/bin/env python3
"""Проверить версию API: отдельно сообщить об ошибке подключения и несовпадении версии."""
import argparse
import ujson
import subprocess
import sys
import time


class APIUnavailable(RuntimeError):
    pass


def check(kubeconfig, expected):
    result = subprocess.run(['kubectl', '--kubeconfig', kubeconfig, '--request-timeout=15s',
                             'version', '-o', 'json'], capture_output=True, text=True, timeout=20)
    if result.returncode:
        detail = result.stderr.strip() or 'kubectl не смог подключиться к API.'
        raise APIUnavailable('Kubernetes API недоступен: ' + detail)
    try:
        actual = ujson.loads(result.stdout).get('serverVersion', {}).get('gitVersion')
    except (ValueError, AttributeError, TypeError) as error:
        raise RuntimeError('Некорректный ответ kubectl version, версия сервера не проверена.') from error
    if not isinstance(actual, str) or not actual:
        raise RuntimeError('В ответе Kubernetes API нет serverVersion, версия кластера не проверена.')
    wanted = 'v' + expected.removeprefix('v')
    if actual != wanted:
        raise RuntimeError(f'Версия кластера отличается: нужна {wanted}, получена {actual}. Автоматическое обновление отключено.')
    return actual


def wait_for_cluster(kubeconfig, expected, seconds=0):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return check(kubeconfig, expected)
        except (APIUnavailable, subprocess.TimeoutExpired):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            print(f'Ждём API существующего кластера, осталось {int(remaining)} с...', file=sys.stderr)
            time.sleep(min(3, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--wait', type=int, default=0, help='Время ожидания API при ошибке подключения, от 0 до 300 секунд.')
    args = parser.parse_args()
    if not 0 <= args.wait <= 300:
        parser.error('--wait должен быть от 0 до 300 секунд.')
    try:
        print('Версия API своего кластера: ' + wait_for_cluster(args.kubeconfig, args.expected, args.wait))
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        print('Выполнить: sudo bash scripts/diagnose-host.sh (работает даже при недоступном API).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
