"""Проверяем настоящий shell-wrapper с процессом, который пишет и переоткрывает логи."""
import sys
import os
import importlib.util
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts/nginx-run.sh'
SPEC = importlib.util.spec_from_file_location('rotation', SCRIPT.with_name('check-log-rotation.py'))
ROTATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ROTATION)


def eventually(check, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.03)
    raise AssertionError('Не дождались ожидаемого состояния ротации.')


class LogRotationTests(unittest.TestCase):
    def test_collection_proof_requires_both_sources_all_ids_and_exactly_one_record(self):
        good = {'unique': 100, 'records': 100, 'duplicates': 0, 'complete': True}
        self.assertTrue(ROTATION.logs_complete({'nginx.access': good, 'nginx.error': good}, 100))
        for bad in ({}, {'nginx.access': good},
                    {'nginx.access': good, 'nginx.error': dict(good, duplicates=1)},
                    {'nginx.access': good, 'nginx.error': dict(good, records=101)},
                    {'nginx.access': good, 'nginx.error': dict(good, complete=False)},
                    {'nginx.access': good, 'nginx.error': dict(good, unique='100')}):
            with self.subTest(bad=bad):
                self.assertFalse(ROTATION.logs_complete(bad, 100))

    def fixture(self, root):
        (root / 'bin').mkdir()
        (root / 'logs').mkdir()
        fake = root / 'bin/nginx'
        fake.write_text('''#!/usr/bin/env python3
import os, signal, time
from pathlib import Path
root = Path(os.environ['LOG_DIR'])
files = {}
def reopen(*args):
    for stream in files.values(): stream.close()
    for name in ('access', 'error'):
        files[name] = (root / (name + '.log')).open('a', buffering=1)
def stop(*args):
    for stream in files.values(): stream.close()
    (root / 'quit').touch()
    raise SystemExit(0)
signal.signal(signal.SIGUSR1, reopen)
signal.signal(signal.SIGQUIT, stop)
reopen()
number = 0
while True:
    number += 1
    for stream in files.values(): stream.write(str(number) + ': record abcdefghijklmnop\\n')
    time.sleep(.02)
''')
        fake.chmod(0o755)
        return dict(os.environ, PATH=str(root / 'bin') + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                    LOG_DIR=str(root / 'logs'), LOG_ROTATE_BYTES='128', LOG_ROTATE_INTERVAL='0.1')

    def test_rename_preserves_records_new_files_receive_data_and_archives_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root)
            with (root / 'stderr').open('w') as output:
                process = subprocess.Popen(['sh', str(SCRIPT)], env=env, stderr=output,
                                           start_new_session=True)
                try:
                    eventually(lambda: all(len(list((root / 'logs').glob(name + '.log.*'))) == 4
                                           for name in ('access', 'error')))
                    time.sleep(.3)
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertTrue((root / 'logs/quit').exists())
                    for name in ('access', 'error'):
                        paths = sorted((root / 'logs').glob(name + '.log*'))
                        self.assertEqual(len(paths), 5)
                        self.assertLessEqual(sum(p.stat().st_size for p in paths), 128 * 10)
                        records = [int(line.partition(':')[0]) for p in paths
                                   for line in p.read_text().splitlines()]
                        self.assertEqual(len(records), len(set(records)))
                        # Удаляется начало истории, оставшиеся последовательные записи целы.
                        self.assertEqual(sorted(records), list(range(min(records), max(records) + 1)))
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()

    def test_nginx_failure_is_reported_instead_of_hanging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root)
            (root / 'bin/nginx').write_text('#!/bin/sh\nexit 7\n')
            result = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 7)

    def test_oversized_or_invalid_rotation_budget_is_rejected_before_starting_nginx(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.fixture(root)
            for value in ('0', '8388609', '-1', 'bad'):
                with self.subTest(value=value):
                    env['LOG_ROTATE_BYTES'] = value
                    result = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 2)
                    self.assertFalse((root / 'logs/access.log').exists())
