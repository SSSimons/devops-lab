#!/usr/bin/env python3
"""Показываем текущее состояние и ошибку каждого источника метрик Prometheus."""
import argparse
import subprocess
import sys
from urllib.error import URLError

from verify import (VerificationError, port_forward, read_prometheus_targets,
                    require_prometheus_targets)


def diagnose(base: str) -> None:
    active = read_prometheus_targets(base)
    print('Текущее состояние источников Prometheus:', flush=True)
    for target in active:
        print(f"job={target['labels'].get('job', '?')} health={target.get('health', 'unknown')} "
              f"адрес={target.get('scrapeUrl', '?')} последний сбор={target.get('lastScrape', '?')} "
              f"длительность={target.get('lastScrapeDuration', '?')}с "
              f"ошибка={target.get('lastError') or '(нет)'}", flush=True)
    require_prometheus_targets(active)
    print('[OK] Все три источника доступны.', flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Адрес Prometheus, если port-forward уже запущен')
    parser.add_argument('--kubeconfig')
    args = parser.parse_args()
    try:
        if args.url:
            diagnose(args.url.rstrip('/'))
        else:
            kubectl = ['kubectl'] + (['--kubeconfig', args.kubeconfig] if args.kubeconfig else [])
            with port_forward(kubectl) as base:
                diagnose(base)
    except (VerificationError, URLError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f'[FAIL] {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
