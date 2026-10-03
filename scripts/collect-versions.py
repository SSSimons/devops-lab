#!/usr/bin/env python3
"""Собрать фактические версии без секретов и обновить описание окружения."""
import argparse
from datetime import datetime, timezone
import ujson
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BEGIN = '<!-- BEGIN OBSERVED ENVIRONMENT -->'
END = '<!-- END OBSERVED ENVIRONMENT -->'


def command(args, optional=False):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=25)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        if optional:
            return ''
        raise
    if result.returncode and not optional:
        raise RuntimeError(result.stderr.strip() or 'Команда завершилась с ошибкой: ' + args[0])
    return result.stdout.strip()


def os_info(path=Path('/etc/os-release')):
    result = {}
    for line in path.read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            result[key] = ' '.join(shlex.split(value))
    return result


def image_inventory(pods):
    """Сохраняем версии и digest образов без имён узла, Pod и их адресов."""
    images = {}
    readiness = {}
    for pod in pods['items']:
        namespace = pod['metadata']['namespace']
        if namespace not in ('devops-lab', 'nginx-gateway', 'kube-flannel', 'kube-system'):
            continue
        status = pod.get('status', {})
        declared = {c['name']: c['image'] for c in pod.get('spec', {}).get('containers', [])
                    + pod.get('spec', {}).get('initContainers', [])}
        readiness.setdefault(namespace, {'pods': 0, 'ready': 0})
        readiness[namespace]['pods'] += 1
        readiness[namespace]['ready'] += int(any(c.get('type') == 'Ready' and c.get('status') == 'True'
                                                for c in status.get('conditions', [])))
        for container in status.get('containerStatuses', []) + status.get('initContainerStatuses', []):
            item = {'namespace': namespace, 'container': container['name'],
                    'image': declared.get(container['name'], container['image']),
                    'runtime_image': container['image'], 'image_id': container.get('imageID', '')}
            images[(namespace, item['container'], item['image'], item['image_id'])] = item
    return list(images.values()), readiness


def image_version(images, repository, namespace=None):
    values = set()
    for item in images:
        image = item['image'].removeprefix('docker.io/').removeprefix('library/')
        if image.startswith(repository + ':') and (namespace is None or item['namespace'] == namespace):
            values.add(image[len(repository) + 1:])
    return next(iter(values)) if len(values) == 1 else None


def collect(kubectl):
    version = ujson.loads(command(kubectl + ['version', '-o', 'json']))
    nodes = ujson.loads(command(kubectl + ['get', 'nodes', '-o', 'json']))['items']
    if len(nodes) != 1:
        raise ValueError('Для проекта нужен ровно один узел.')
    node = nodes[0]['status']['nodeInfo']
    pods = ujson.loads(command(kubectl + ['get', 'pods', '-A', '-o', 'json']))
    images, readiness = image_inventory(pods)
    crd = ujson.loads(command(kubectl + ['get', 'crd', 'gateways.gateway.networking.k8s.io', '-o', 'json']))
    os_release = os_info()
    packages = {}
    dpkg = command(['dpkg-query', '-W', '-f=${Package}\t${Version}\n',
                    'containerd', 'containerd-app', 'kubeadm', 'kubelet', 'kubectl', 'python3-venv'], optional=True)
    for line in dpkg.splitlines():
        name, _, value = line.partition('\t')
        if value:
            packages[name] = value
    observed = {
        'ubuntu': os_release.get('PRETTY_NAME'), 'kernel': node['kernelVersion'],
        'environment': 'WSL2' if 'microsoft' in node['kernelVersion'].lower() else 'Linux VM / host',
        'kubernetes': node['kubeletVersion'], 'containerd': node['containerRuntimeVersion'],
        'kubeadm': command(['kubeadm', 'version', '-o', 'short'], optional=True) or None,
        'kubectl_client': version['clientVersion']['gitVersion'],
        'api_server': version.get('serverVersion', {}).get('gitVersion'),
        'python': platform.python_version(),
        'ujson': ujson.__version__,
        'gateway_api': crd['metadata'].get('annotations', {}).get('gateway.networking.k8s.io/bundle-version'),
    }
    for key, repository, namespace in [
        ('nginx', 'nginx', 'devops-lab'),
        ('nginx_gateway_fabric', 'ghcr.io/nginx/nginx-gateway-fabric', 'nginx-gateway'),
        ('nginx_data_plane', 'ghcr.io/nginx/nginx-gateway-fabric/nginx', 'devops-lab'),
        ('flannel', 'ghcr.io/flannel-io/flannel', 'kube-flannel'),
        ('prometheus', 'prom/prometheus', 'devops-lab'),
        ('nginx_prometheus_exporter', 'nginx/nginx-prometheus-exporter', 'devops-lab'),
        ('fluentd', 'fluent/fluentd', 'devops-lab'),
        ('busybox', 'busybox', 'devops-lab')]:
        observed[key] = image_version(images, repository, namespace)
    component_keys = ('gateway_api', 'nginx', 'nginx_gateway_fabric', 'nginx_data_plane',
                      'flannel', 'prometheus', 'nginx_prometheus_exporter', 'fluentd', 'busybox')
    if any(observed[key] is None for key in component_keys):
        raise ValueError('Не найдена однозначная версия обязательного компонента. Сначала успешно развернуть стенд.')
    expected = ujson.loads((ROOT / 'config/versions.json').read_text())
    differences = []
    for key in ('kubernetes', 'kubeadm', 'kubectl_client', 'api_server', 'ujson') + component_keys:
        pin = ('kubernetes' if key in ('kubeadm', 'kubectl_client', 'api_server') else
               'nginx_gateway_fabric' if key == 'nginx_data_plane' else key)
        if observed[key] is not None and observed[key].removeprefix('v') != expected[pin].removeprefix('v'):
            differences.append(key)
    return {'schema_version': 1, 'captured_at_utc': datetime.now(timezone.utc).isoformat(),
            'source': 'collect-versions.py: фактические данные узла и Kubernetes API',
            'observed': observed, 'packages': packages, 'images': images,
            'readiness': readiness, 'differences_from_pins': differences}


def markdown(data):
    def cell(value):
        return str(value if value is not None else 'не измерено').replace('|', '\\|').replace('\n', ' ').replace('`', '')
    lines = ['Фактическое окружение: ' + cell(data['source']) + '.', '',
             '| Компонент | Фактическая версия |', '|---|---|']
    labels = {'ubuntu': 'Ubuntu', 'kernel': 'Ядро Linux', 'environment': 'Среда',
              'kubernetes': 'Kubernetes', 'containerd': 'containerd', 'kubeadm': 'kubeadm',
              'kubectl_client': 'Клиент kubectl', 'api_server': 'API-сервер Kubernetes',
              'python': 'Python', 'ujson': 'ujson', 'gateway_api': 'Gateway API', 'nginx': 'nginx приложения',
              'nginx_gateway_fabric': 'NGINX Gateway Fabric', 'nginx_data_plane': 'nginx Gateway',
              'flannel': 'Flannel', 'prometheus': 'Prometheus',
              'nginx_prometheus_exporter': 'nginx exporter', 'fluentd': 'Fluentd', 'busybox': 'BusyBox'}
    lines += [f'| {cell(labels.get(key, key))} | `{cell(value)}` |' for key, value in data['observed'].items()]
    lines += ['', 'Дата сбора: ' + cell(data.get('captured_at_utc', 'не указана')) + '.',
              'Точные версии пакетов и digest образов: `config/tested-environment.json`.',
              'Закреплённые версии: `config/versions.json`; сбор данных не меняет манифесты и контрольные суммы зависимостей.']
    if data.get('differences_from_pins'):
        lines += ['Расхождения с пинами: ' + ', '.join(data['differences_from_pins']) + '. Проверить перед сдачей.']
    return '\n'.join(lines) + '\n'


def update_readme(text, data):
    block = BEGIN + '\n' + markdown(data) + END
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError('В README должен быть ровно один блок фактических версий окружения.')
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before + block + after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig')
    parser.add_argument('--update-docs', action='store_true')
    args = parser.parse_args()
    if not args.kubeconfig and not os.environ.get('KUBECONFIG') and os.geteuid() == 0 and Path('/etc/kubernetes/admin.conf').exists():
        args.kubeconfig = '/etc/kubernetes/admin.conf'
    kubectl = ['kubectl', '--request-timeout=15s'] + (['--kubeconfig', args.kubeconfig] if args.kubeconfig else [])
    try:
        data = collect(kubectl)
        content = ujson.dumps(data, ensure_ascii=False, indent=2) + '\n'
        readme = update_readme((ROOT / 'README.md').read_text(), data) if args.update_docs else None
        (ROOT / '.state').mkdir(exist_ok=True)
        (ROOT / '.state/versions.json').write_text(content)
        if args.update_docs:
            (ROOT / 'config/tested-environment.json').write_text(content)
            (ROOT / 'docs/ENVIRONMENT.md').write_text('# Фактические версии стенда\n\n' + markdown(data))
            (ROOT / 'README.md').write_text(readme)
        print(markdown(data))
        return 1 if data['differences_from_pins'] else 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
