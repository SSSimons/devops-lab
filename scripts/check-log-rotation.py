#!/usr/bin/env python3
"""Проверить ротацию под нагрузкой через Gateway и сохранность записей Fluentd."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid
import ujson

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / '.state/log-rotation.json'

# Подсчитываем на стороне Pod, чтобы не передавать все логи через Kubernetes API.
RUBY = r'''
require 'json'
marker, expected = ARGV
counts = {'nginx.access' => Hash.new(0), 'nginx.error' => Hash.new(0)}
pattern = /#{Regexp.escape(marker)}-([0-9]{6})/
Dir.glob('/collected/events*.log').each do |path|
  File.foreach(path) do |line|
    begin
      record = JSON.parse(line)
      source = record['source']
      next unless counts.key?(source)
      text = record['uri'] || record['message'] || ''
      match = text.match(pattern)
      counts[source][match[1].to_i] += 1 if match
    rescue JSON::ParserError
      next
    end
  end
end
result = counts.transform_values do |items|
  {'unique' => items.size, 'records' => items.values.sum,
   'complete' => items.keys.sort == (0...expected.to_i).to_a,
   'duplicates' => items.values.count { |count| count != 1 }}
end
puts JSON.generate(result)
'''


def run(*args):
    result = subprocess.run(['kubectl', '--request-timeout=30s', *args],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Команда kubectl завершилась с ошибкой.')
    return result.stdout


def send(url):
    try:
        response = urlopen(url, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        response.read()
        if response.status != 404 or response.headers.get('X-DevOps-Lab') != 'gateway-api':
            raise RuntimeError('Ожидался HTTP 404 через Gateway API с заголовком X-DevOps-Lab.')


def logs_complete(counts, expected):
    if not isinstance(counts, dict) or set(counts) != {'nginx.access', 'nginx.error'}:
        return False
    return all(isinstance(value, dict) and value.get('complete') is True
               and all(type(value.get(key)) is int for key in ('unique', 'records', 'duplicates'))
               and value['unique'] == value['records'] == expected and value['duplicates'] == 0
               for value in counts.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Адрес Gateway с портом, например http://172.26.12.112:30080')
    parser.add_argument('--requests', type=int, default=24576, help='Число запросов, по умолчанию 24576')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != 'http' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        parser.error('--url должен содержать только http://адрес:порт, без пути и credentials.')
    if not 1 <= args.requests <= 100000 or not 1 <= args.workers <= 32:
        parser.error('Допустимо 1-100000 запросов и 1-32 потока.')
    marker = 'rotation-' + uuid.uuid4().hex
    report = {'status': 'failed', 'time_utc': datetime.now(timezone.utc).isoformat(),
              'marker': marker, 'requests': args.requests}
    REPORT.parent.mkdir(exist_ok=True)
    # Старый passed не должен сохраниться после прерывания нового запуска.
    REPORT.write_text(ujson.dumps(report, indent=2) + '\n')
    try:
        base = args.url.rstrip('/') + '/does-not-exist-' + marker
        completed = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            # Даём ротатору обработать каждую порцию. Быстрая VM тоже должна сменить файлы
            # несколько раз, а не закончить все запросы до первой секундной проверки.
            for first in range(0, args.requests, 2048):
                urls = (f'{base}-{number:06d}?' + 'x' * 2048
                        for number in range(first, min(first + 2048, args.requests)))
                for _ in executor.map(send, urls):
                    completed += 1
                    if completed % 4096 == 0:
                        print(f'Отправлено {completed}/{args.requests} запросов через Gateway.', flush=True)
                time.sleep(1.1)
        deadline = time.monotonic() + 90
        while True:
            counts = ujson.loads(run('-n', 'devops-lab', 'exec', 'web-0', '-c', 'fluentd',
                                     '--', 'ruby', '-e', RUBY, marker, str(args.requests)))
            if logs_complete(counts, args.requests):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Fluentd не собрал ровно по одной access/error записи: ' + str(counts))
            time.sleep(3)
        files = run('-n', 'devops-lab', 'exec', 'web-0', '-c', 'nginx', '--', 'sh', '-c',
                    'for f in /logs/access.log* /logs/error.log*; do stat -c "%n %s" "$f"; done')
        sizes = {line.rsplit(' ', 1)[0]: int(line.rsplit(' ', 1)[1]) for line in files.splitlines()}
        for name in ('access', 'error'):
            names = [path for path in sizes if path.startswith('/logs/' + name + '.log')]
            if len(names) != 5 or '/logs/' + name + '.log' not in names:
                raise RuntimeError('Нужно увидеть активный файл и четыре архива для обоих логов. '
                                   'Повторить проверку со стандартными 24576 запросами.')
        if sum(sizes.values()) >= 128 * 1024 * 1024:
            raise RuntimeError('Размер исходных логов достиг лимита emptyDir 128 MiB.')
        report.update(status='passed', counts=counts, raw_files=sizes, raw_bytes=sum(sizes.values()))
        print('[OK] Ротация обоих логов; записи Fluentd без пропусков и дубликатов; объём меньше 128 MiB.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report['error'] = str(error)
        print('[ERROR] ' + str(error))
        return 1
    finally:
        REPORT.write_text(ujson.dumps(report, ensure_ascii=False, indent=2,
                                      escape_forward_slashes=False) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
