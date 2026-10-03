#!/usr/bin/env python3
"""Сквозная проверка: Gateway API -> nginx -> Prometheus + Fluentd."""
from __future__ import annotations

import argparse
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
import ujson
import math
from pathlib import Path
import re
import select
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen
import uuid


class VerificationError(RuntimeError):
    pass


def run(command: list[str], timeout: int = 30) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise VerificationError(f"{' '.join(command)}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def retry(function, seconds: float = 90, interval: float = 2):
    deadline = time.monotonic() + seconds
    last_error = None
    while True:
        try:
            return function()
        except (VerificationError, URLError, OSError, ValueError) as error:
            last_error = error
        if time.monotonic() >= deadline:
            raise VerificationError(f'Истекло время ожидания: {last_error}') from last_error
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def conditions_ready(resource: dict, required: tuple[str, ...]) -> bool:
    """Принимаем успешный статус только для текущего поколения конфигурации."""
    generation = resource['metadata']['generation']
    conditions = resource.get('status', {}).get('conditions', [])
    return all(any(c.get('type') == kind and c.get('status') == 'True'
                   and c.get('observedGeneration') == generation for c in conditions)
               for kind in required)


def route_ready(resource: dict) -> bool:
    """Проверяем принятие маршрута нужным Gateway и контроллером NGF."""
    generation = resource['metadata']['generation']
    for parent in resource.get('status', {}).get('parents', []):
        if (parent.get('parentRef', {}).get('name') != 'lab'
                or parent.get('controllerName') != 'gateway.nginx.org/nginx-gateway-controller'):
            continue
        candidate = {'metadata': {'generation': generation},
                     'status': {'conditions': parent.get('conditions', [])}}
        if conditions_ready(candidate, ('Accepted', 'ResolvedRefs')):
            return True
    return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def http_get(url: str, expected: int = 200, follow_redirects: bool = True, as_bytes: bool = False):
    try:
        response = (urlopen(url, timeout=10) if follow_redirects
                    else build_opener(NoRedirect()).open(url, timeout=10))
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        if not as_bytes:
            body = body.decode('utf-8')
        if response.status != expected:
            raise VerificationError(f'{url}: ожидался HTTP {expected}, получен {response.status}: {body[:200]}')
        return body, dict(response.headers)


def check_hello(url: str):
    body, headers = http_get(url)
    if body != 'Hello World!\n':
        raise VerificationError(f'Неожиданный ответ: {body!r}')
    if {k.lower(): v for k, v in headers.items()}.get('x-devops-lab') != 'gateway-api':
        raise VerificationError('В ответе нет заголовка, заданного HTTPRoute Gateway.')
    return body.strip()


def check_gateway_features(base: str):
    body, headers = http_get(base.rstrip('/') + '/api/info')
    headers = {k.lower(): v for k, v in headers.items()}
    if ujson.loads(body).get('service') != 'devops-lab' or headers.get('x-devops-route') != 'api-rewrite':
        raise VerificationError('URLRewrite или заголовок его маршрута не прошли проверку.')
    _, headers = http_get(base.rstrip('/') + '/legacy', expected=302, follow_redirects=False)
    location = {k.lower(): v for k, v in headers.items()}.get('location', '')
    redirected = urlsplit(urljoin(base.rstrip('/') + '/legacy', location))
    origin = urlsplit(base)
    if not location or redirected.netloc != origin.netloc or redirected.scheme != origin.scheme or redirected.path != '/api/info':
        raise VerificationError(f'Неожиданный адрес перенаправления Gateway: {location!r}')
    return {'rewrite': '/api/info -> /info', 'redirect': '/legacy -> /api/info (302)'}


def check_cats(base: str):
    body, headers = http_get(base.rstrip('/') + '/cats')
    if ('Котик был доставлен через Kubernetes' not in body or 'src="/cats/cat.png"' not in body
            or {k.lower(): v for k, v in headers.items()}.get('x-devops-lab') != 'gateway-api'):
        raise VerificationError('Страница /cats или её заголовок Gateway не прошли проверку.')
    image, headers = http_get(base.rstrip('/') + '/cats/cat.png', as_bytes=True)
    expected = (Path(__file__).resolve().parents[1] / 'web/cat.png').read_bytes()
    if (image != expected or not image.startswith(b'\x89PNG\r\n\x1a\n')
            or {k.lower(): v for k, v in headers.items()}.get('x-devops-lab') != 'gateway-api'):
        raise VerificationError('Изображение /cats отличается от файла проекта или в ответе нет заголовка Gateway.')
    return {'page': '/cats', 'image_sha256': hashlib.sha256(image).hexdigest()}


def metric_value(base: str, query: str, minimum: float = 1) -> float:
    body, _ = http_get(base + '/api/v1/query?' + urlencode({'query': query}))
    data = ujson.loads(body)
    if data.get('status') != 'success' or data.get('data', {}).get('resultType') != 'vector':
        raise VerificationError(f'Некорректный ответ Prometheus на запрос {query}')
    values = [float(item['value'][1]) for item in data['data']['result']]
    if not values or any(value < minimum or not math.isfinite(value) for value in values):
        raise VerificationError(f'{query}: ожидались значения >= {minimum}, получены {values}')
    return min(values)


def read_prometheus_targets(base: str) -> list[dict]:
    """Читаем текущее состояние источников, а не прошлые значения из TSDB."""
    body, _ = http_get(base + '/api/v1/targets')
    response = ujson.loads(body)
    data = response.get('data') if isinstance(response, dict) else None
    active = data.get('activeTargets') if isinstance(data, dict) else None
    if (not isinstance(response, dict) or response.get('status') != 'success'
            or not isinstance(active, list)
            or any(not isinstance(target, dict) or not isinstance(target.get('labels'), dict)
                   for target in active)):
        raise VerificationError('Prometheus вернул некорректный список источников метрик.')
    return active


def require_prometheus_targets(active: list[dict]) -> list[dict]:
    """Все три источника должны пройти сбор метрик; down и unknown не считаются успехом."""
    expected_jobs = {'prometheus', 'nginx', 'gateway-controller'}
    missing = expected_jobs - {target['labels'].get('job') for target in active}
    errors = ['Отсутствуют источники: ' + ', '.join(sorted(missing))] if missing else []
    for target in active:
        if target.get('health') != 'up' or target.get('lastError'):
            errors.append(f"{target['labels'].get('job', '?')}: health={target.get('health', 'unknown')}; "
                          f"адрес={target.get('scrapeUrl', '?')}; "
                          f"ошибка={target.get('lastError') or 'успешный сбор метрик ещё не подтверждён'}")
    if errors:
        raise VerificationError('Источники Prometheus не готовы: ' + '; '.join(errors))
    return active


def check_prometheus_targets(base: str) -> list[dict]:
    return require_prometheus_targets(read_prometheus_targets(base))


def check_request_growth(base: str, prometheus: str, token: str, requests: int = 5, seconds: float = 90):
    """Сравниваем счётчик до запросов и после следующего сбора метрик."""
    query = 'nginx_http_requests_total{job="nginx"}'
    before = retry(lambda: metric_value(prometheus, query, minimum=0), seconds=seconds)
    for index in range(requests):
        check_hello(base.rstrip('/') + '/?' + urlencode({'verify': token, 'request': index}))

    def changed():
        after = metric_value(prometheus, query, minimum=0)
        if after - before < requests:
            raise VerificationError(f'Счётчик nginx ещё не вырос на {requests}: было {before}, стало {after}.')
        return after

    after = retry(changed, seconds=seconds)
    return {'before': before, 'after': after, 'delta': after - before, 'requests_sent': requests}


def check_prometheus_rules(base: str):
    """Проверяем, что Prometheus загрузил и успешно вычисляет правила стенда."""
    body, _ = http_get(base + '/api/v1/rules')
    data = ujson.loads(body)
    if data.get('status') != 'success':
        raise VerificationError('Prometheus не вернул список правил.')
    rules = {rule['name']: rule for group in data.get('data', {}).get('groups', [])
             for rule in group.get('rules', [])}
    names = ('NginxUnavailable', 'GatewayControllerUnavailable',
             'lab:nginx_requests_per_second:rate5m', 'lab:gateway_controller_memory_bytes')
    for name in names:
        if name not in rules or rules[name].get('health') != 'ok' or rules[name].get('lastError'):
            raise VerificationError(f'Правило {name} отсутствует или вычисляется с ошибкой.')
    return {name: rules[name]['health'] for name in names}


@contextmanager
def port_forward(kubectl: list[str]):
    """Открываем локальный доступ к Prometheus и закрываем процесс после проверки."""
    process = subprocess.Popen(kubectl + ['-n', 'devops-lab', 'port-forward',
                               'service/prometheus', '0:9090', '--address=127.0.0.1'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = []
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise VerificationError('port-forward завершился: ' + ''.join(output))
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if not ready:
                continue
            line = process.stdout.readline()
            output.append(line)
            match = re.search(r'Forwarding from 127\.0\.0\.1:(\d+)', line)
            if match:
                yield f'http://127.0.0.1:{match.group(1)}'
                return
        raise VerificationError('Истекло время запуска port-forward: ' + ''.join(output))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()


def verify(args):
    kubectl = ['kubectl'] + (['--kubeconfig', args.kubeconfig] if args.kubeconfig else [])

    def resource(kind, name, namespace=None):
        command = kubectl + (['-n', namespace] if namespace else [])
        return ujson.loads(run(command + ['get', kind, name, '-o', 'json']))

    def routes():
        if not conditions_ready(resource('gatewayclass', 'nginx'), ('Accepted',)):
            raise VerificationError('GatewayClass не принял текущую версию конфигурации.')
        if not conditions_ready(resource('gateway', 'lab', 'devops-lab'), ('Accepted', 'Programmed')):
            raise VerificationError('Gateway не готов обслуживать текущую версию конфигурации.')
        for name in ('web', 'api-info', 'legacy-redirect'):
            if not route_ready(resource('httproute', name, 'devops-lab')):
                raise VerificationError(f'HTTPRoute {name}: Accepted/ResolvedRefs не подтверждены для текущей конфигурации.')

    retry(routes)
    nodes = ujson.loads(run(kubectl + ['get', 'nodes', '-o', 'json']))['items']
    if len(nodes) != 1:
        raise VerificationError('Для проверки нужен одноузловой кластер.')
    node_ip = next(a['address'] for a in nodes[0]['status']['addresses'] if a['type'] == 'InternalIP')
    url = args.url or f'http://{node_ip}:30080'
    if not re.fullmatch(r'https?://[^/]+/?', url):
        raise VerificationError('--url должен содержать адрес HTTP(S) и порт, без пути.')
    token = uuid.uuid4().hex
    hello = retry(lambda: check_hello(url.rstrip('/') + '/?' + urlencode({'verify': token})))
    info, _ = http_get(url.rstrip('/') + '/info')
    if ujson.loads(info).get('service') != 'devops-lab':
        raise VerificationError('/info вернул данные другого сервиса.')
    http_get(url.rstrip('/') + '/does-not-exist-' + token, expected=404)
    features = retry(lambda: check_gateway_features(url))
    cats = retry(lambda: check_cats(url))
    print('[OK] GatewayClass, Gateway и 3 HTTPRoute; Hello World; JSON; HTTP 404; URLRewrite; HTTP 302; /cats и изображение', flush=True)

    with port_forward(kubectl) as prometheus:
        print('Ожидаю успешный сбор метрик от prometheus, nginx и gateway-controller (до 90 секунд)...', flush=True)
        active = retry(lambda: check_prometheus_targets(prometheus))
        queries = ['up{job="nginx"}', 'nginx_up', 'nginx_http_requests_total',
                   'up{job="gateway-controller"}']
        metrics = {query: retry(lambda q=query: metric_value(prometheus, q)) for query in queries}
        traffic = check_request_growth(url, prometheus, token)
        rules = retry(lambda: check_prometheus_rules(prometheus))
        metrics['lab:nginx_requests_per_second:rate5m'] = retry(
            lambda: metric_value(prometheus, 'lab:nginx_requests_per_second:rate5m', minimum=0))
        metrics['lab:gateway_controller_memory_bytes'] = retry(
            lambda: metric_value(prometheus, 'lab:gateway_controller_memory_bytes'))
        active = retry(lambda: check_prometheus_targets(prometheus))
    print('[OK] Источники Prometheus, рост счётчика nginx и вычисление правил мониторинга', flush=True)

    ruby = """token = ARGV.fetch(0)
found = {'access' => false, 'error' => false}
Dir.glob('/collected/events*.log').sort.reverse_each do |path|
  File.foreach(path) do |line|
    begin
      record = JSON.parse(line)
      if record['source'] == 'nginx.access' && record['uri'].to_s.include?(token) && record['status'] == 200
        found['access'] = true
      end
      if record['source'] == 'nginx.error' && record['message'].to_s.include?('does-not-exist-' + token)
        found['error'] = true
      end
    rescue JSON::ParserError
      next
    end
  end
  break if found.values.all?
end
puts JSON.generate(found)
exit(found.values.all? ? 0 : 1)
"""
    command = kubectl + ['-n', 'devops-lab', 'exec', 'web-0', '-c', 'fluentd', '--',
                          'ruby', '-rjson', '-e', ruby, token]
    logs = ujson.loads(retry(lambda: run(command)))
    print('[OK] Fluentd собрал access- и error-логи с уникальной меткой запроса', flush=True)
    return {'status': 'passed', 'time_utc': datetime.now(timezone.utc).isoformat(),
            'url': url, 'request_marker': token, 'hello': hello,
            'kubernetes': nodes[0]['status']['nodeInfo']['kubeletVersion'],
            'metrics': metrics, 'traffic': traffic, 'prometheus_rules': rules,
            'prometheus_targets': [{'job': target['labels'].get('job', '?'), 'health': target['health'],
                                    'last_scrape': target.get('lastScrape')}
                                   for target in active],
            'logs': logs, 'gateway_features': features, 'cats': cats}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Указать адрес узла, например http://127.0.0.1:30080 для проверки в kind')
    parser.add_argument('--kubeconfig')
    parser.add_argument('--report', default='.state/verification.json')
    args = parser.parse_args()
    try:
        report = verify(args)
    except (VerificationError, subprocess.TimeoutExpired, OSError, ValueError, KeyError, StopIteration) as error:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ujson.dumps({'status': 'failed', 'time_utc': datetime.now(timezone.utc).isoformat(),
                                    'error': str(error)}, indent=2, ensure_ascii=False) + '\n')
        print(f'[FAIL] {error}\nВыполнить: bash scripts/diagnose.sh', file=sys.stderr)
        return 1
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ujson.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(f'[OK] Отчёт: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
