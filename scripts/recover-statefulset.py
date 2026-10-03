#!/usr/bin/env python3
"""Восстановить старый неготовый Pod после обновления шаблона одноузлового стенда."""
import argparse
import ujson
import subprocess
import sys
import time

NAMESPACE = 'devops-lab'


def kubectl(*args, body=None):
    result = subprocess.run(['kubectl', '--request-timeout=15s', *args],
                            input=body, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'kubectl завершился с ошибкой')
    return ujson.loads(result.stdout) if result.stdout.strip() else None


def deletion_options(statefulset, pod):
    spec, status = statefulset['spec'], statefulset.get('status', {})
    if spec.get('replicas', 1) != 1:
        raise ValueError('Восстановление поддерживает только одну реплику.')
    strategy = spec.get('updateStrategy', {})
    if strategy.get('type', 'RollingUpdate') != 'RollingUpdate' or strategy.get('rollingUpdate', {}).get('partition', 0):
        raise ValueError('Для восстановления нужен RollingUpdate без разделения на partition.')
    if not pod or pod['metadata'].get('deletionTimestamp'):
        return None
    meta = pod['metadata']
    owner = statefulset['metadata']
    if not any(ref.get('controller') is True and ref.get('kind') == 'StatefulSet'
               and ref.get('name') == owner['name'] and ref.get('uid') == owner['uid']
               for ref in meta.get('ownerReferences', [])):
        raise ValueError('Pod не принадлежит этому StatefulSet, восстановление остановлено.')
    if any(c.get('type') == 'Ready' and c.get('status') == 'True'
           for c in pod.get('status', {}).get('conditions', [])):
        return None
    revision = meta.get('labels', {}).get('controller-revision-hash')
    target = status.get('updateRevision')
    if not revision or not target or revision == target:
        return None
    # Завершаем Pod штатно. Проверки UID и resourceVersion защищают от удаления другого Pod
    # или Pod, изменившегося после проверки. В таком случае API возвращает HTTP 409.
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'preconditions': {'uid': meta['uid'], 'resourceVersion': meta['resourceVersion']}}


def recover(name, timeout=60):
    deadline = time.monotonic() + timeout
    while True:
        statefulset = kubectl('-n', NAMESPACE, 'get', 'statefulset', name, '-o', 'json')
        status = statefulset.get('status', {})
        if (status.get('observedGeneration', 0) >= statefulset['metadata']['generation']
                and status.get('updateRevision')):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Контроллер StatefulSet не успел обработать новый шаблон.')
        time.sleep(1)
    pod_name = name + '-0'
    pod = kubectl('-n', NAMESPACE, 'get', 'pod', pod_name, '-o', 'json', '--ignore-not-found')
    options = deletion_options(statefulset, pod)
    if options:
        print(f'Восстанавливаем {NAMESPACE}/{pod_name}: неготовый Pod использует старый шаблон.', flush=True)
        kubectl('delete', '--raw', f'/api/v1/namespaces/{NAMESPACE}/pods/{pod_name}',
                '-f', '-', body=ujson.dumps(options))
    else:
        print(f'{NAMESPACE}/{pod_name}: старого неготового Pod для восстановления нет.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name', choices=['web', 'prometheus'])
    args = parser.parse_args()
    try:
        recover(args.name)
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
