#!/usr/bin/env python3
"""Проверить YAML, схемы CRD, связи ресурсов, версии образов и контрольные суммы зависимостей."""
import hashlib
import base64
import ujson
from pathlib import Path
import subprocess
import sys

import yaml
from jsonschema import Draft4Validator, Draft7Validator

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return [item for item in yaml.safe_load_all(path.read_text()) if item]


def main():
    versions = ujson.loads((ROOT / 'config/versions.json').read_text())
    assert (ROOT / 'requirements-runtime.txt').read_text().strip() == 'ujson==' + versions['ujson']
    documents = [(p, d) for p in sorted((ROOT / 'k8s').glob('*.yaml')) for d in load(p)]
    cats_html = (ROOT / 'web/cats.html').read_text()
    cats_image = (ROOT / 'web/cat.png').read_bytes()
    assert len(cats_html.encode()) + len(cats_image) <= 1_048_576, 'ConfigMap страницы /cats превышает 1 MiB.'
    assert cats_image.startswith(b'\x89PNG\r\n\x1a\n'), 'Изображение /cats должно быть PNG.'
    # deploy-stack.sh собирает этот ConfigMap из тех же двух файлов проекта.
    documents.append((ROOT / 'web', {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'web-cats', 'namespace': 'devops-lab'},
        'data': {'cats.html': cats_html}, 'binaryData': {'cat.png': base64.b64encode(cats_image).decode()}}))
    documents.append((ROOT / 'scripts/nginx-run.sh', {'apiVersion': 'v1', 'kind': 'ConfigMap',
        'metadata': {'name': 'web-log-rotation', 'namespace': 'devops-lab'},
        'data': {'nginx-run.sh': (ROOT / 'scripts/nginx-run.sh').read_text()}}))
    identities = set()
    resources = {}
    for path, doc in documents:
        assert all(k in doc for k in ('apiVersion', 'kind', 'metadata')), path
        key = (doc['kind'], doc['metadata'].get('namespace', ''), doc['metadata']['name'])
        assert key not in identities, ('Повторяющийся ресурс', key)
        identities.add(key)
        resources[key] = doc
        assert 'status' not in doc, ('Не добавлять сгенерированный status в исходный манифест', path)
        for containers in ('containers', 'initContainers'):
            pod = doc.get('spec', {}).get('template', {}).get('spec', {})
            if doc['kind'] == 'CronJob':
                pod = doc['spec']['jobTemplate']['spec']['template']['spec']
            for container in pod.get(containers, []):
                image = container['image']
                assert ':' in image and not image.endswith(':latest'), ('Версия образа не закреплена', image)
                assert 'requests' in container.get('resources', {}), ('Не заданы requests', image)
                assert 'limits' in container.get('resources', {}), ('Не заданы limits', image)
    schemas = {}
    for path in (ROOT / 'vendor').glob('*.yaml'):
        for doc in load(path):
            if doc['kind'] == 'CustomResourceDefinition':
                spec = doc['spec']
                for version in spec['versions']:
                    schemas[(spec['group'] + '/' + version['name'], spec['names']['kind'])] = version['schema']['openAPIV3Schema']
    for path, doc in documents:
        schema = schemas.get((doc['apiVersion'], doc['kind']))
        if schema:
            Draft7Validator(schema).validate(doc)
    for doc in load(ROOT / 'vendor/nginx-gateway.yaml'):
        schema = schemas.get((doc['apiVersion'], doc['kind']))
        if schema:
            Draft7Validator(schema).validate(doc)
    # В Kubernetes Swagger IntOrString указан как строка. Для проверки JSON Schema
    # разрешаем также число: официальные манифесты используют числовые targetPort.
    openapi = ujson.loads((ROOT / 'vendor/kubernetes-openapi.json').read_text())
    openapi['definitions']['io.k8s.apimachinery.pkg.util.intstr.IntOrString'] = {
        'anyOf': [{'type': 'integer'}, {'type': 'string'}]}
    kinds = {}
    for key, definition in openapi['definitions'].items():
        for item in definition.get('x-kubernetes-group-version-kind', []):
            version = (item['group'] + '/' if item['group'] else '') + item['version']
            kinds[(version, item['kind'])] = key
    builtin_count = 0
    for doc in [d for _, d in documents] + load(ROOT / 'vendor/nginx-gateway.yaml') + load(ROOT / 'vendor/flannel.yaml'):
        key = kinds.get((doc['apiVersion'], doc['kind']))
        if key:
            Draft4Validator({'$ref': '#/definitions/' + key,
                             'definitions': openapi['definitions']}).validate(doc)
            builtin_count += 1
    for (kind, namespace, _), route in resources.items():
        if kind != 'HTTPRoute':
            continue
        for rule in route['spec']['rules']:
            for backend in rule.get('backendRefs', []):
                svc = resources[('Service', namespace, backend['name'])]
                assert backend['port'] in [port['port'] for port in svc['spec']['ports']]
    app = resources[('StatefulSet', 'devops-lab', 'web')]
    assert app['spec']['replicas'] == 1, 'Для hostPath и sidecar нужна одна реплика web.'
    images = [c['image'] for c in app['spec']['template']['spec']['containers']]
    assert f"nginx:{versions['nginx']}" in images
    assert f"fluent/fluentd:{versions['fluentd']}" in images
    prom_images = resources[('StatefulSet', 'devops-lab', 'prometheus')]['spec']['template']['spec']['containers']
    assert f"prom/prometheus:v{versions['prometheus'].removeprefix('v')}" in [c['image'] for c in prom_images]
    exporter = resources[('Deployment', 'devops-lab', 'nginx-exporter')]
    assert f"nginx/nginx-prometheus-exporter:{versions['nginx_prometheus_exporter']}" in [
        c['image'] for c in exporter['spec']['template']['spec']['containers']]
    bootstrap = (ROOT / 'scripts/bootstrap.sh').read_text()
    assert f"K8S_VERSION={versions['kubernetes']}\n" in bootstrap
    assert f"K8S_PACKAGE_VERSION={versions['kubernetes']}-1.1\n" in bootstrap
    assert 'check-cluster.py" --kubeconfig /etc/kubernetes/admin.conf --expected "$K8S_VERSION"' in bootstrap
    prom = resources[('ConfigMap', 'devops-lab', 'prometheus-config')]
    cfg = yaml.safe_load(prom['data']['prometheus.yml'])
    assert {j['job_name'] for j in cfg['scrape_configs']} == {'nginx', 'gateway-controller', 'prometheus'}
    yaml.safe_load(prom['data']['alerts.yml'])
    for line in (ROOT / 'vendor/SHA256SUMS').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
    for path in [ROOT / 'deploy.sh', *sorted((ROOT / 'scripts').glob('*.sh'))]:
        subprocess.run(['bash', '-n', str(path)], check=True)
    print(f'OK: {len(documents)} ресурсов проекта; {builtin_count} встроенных ресурсов по Kubernetes OpenAPI; '
          'схемы CRD; связи backend; версии образов; контрольные суммы; синтаксис Bash')
    return 0


if __name__ == '__main__':
    sys.exit(main())
