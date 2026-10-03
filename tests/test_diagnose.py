import sys
import ujson
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts/diagnose.sh'


class DiagnoseTests(unittest.TestCase):
    def run_diagnose(self, explicit=False, fail_api=False, namespaces=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'admin.conf'
            config.write_text('test fixture')
            calls_file = root / 'calls.jsonl'
            kubectl = root / 'kubectl'
            kubectl.write_text('''#!/usr/bin/env python3
import ujson, os, sys
with open(os.environ['CALLS_FILE'], 'a') as stream:
    stream.write(ujson.dumps({'args': sys.argv[1:], 'config': os.environ.get('KUBECONFIG')}) + '\\n')
if sys.argv[2:4] == ['get', 'nodes'] and os.environ.get('FAIL_API') == 'yes':
    sys.exit(1)
if sys.argv[2:4] == ['get', 'namespace'] and os.environ.get('NAMESPACES') == 'no':
    sys.exit(1)
''')
            kubectl.chmod(0o755)
            script = root / 'diagnose.sh'
            # Подменяем UID и путь к конфигу, не меняя системный /etc.
            script.write_text(SCRIPT.read_text().replace('$EUID', '0').replace('/etc/kubernetes/admin.conf', str(config)))
            env = dict(os.environ, PATH=str(root) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                       CALLS_FILE=str(calls_file), FAIL_API='yes' if fail_api else 'no',
                       NAMESPACES='yes' if namespaces else 'no')
            env.pop('KUBECONFIG', None)
            if explicit:
                env['KUBECONFIG'] = '/explicit/test/config'
            result = subprocess.run(['bash', str(script)], env=env, capture_output=True, text=True)
            calls = [ujson.loads(line) for line in calls_file.read_text().splitlines()]
            return result, calls, str(config)

    def test_sudo_uses_admin_conf_when_config_is_unset(self):
        result, calls, expected = self.run_diagnose()
        self.assertEqual(result.returncode, 0)
        self.assertTrue(all(call['config'] == expected for call in calls))

    def test_explicit_config_is_preserved(self):
        _, calls, _ = self.run_diagnose(explicit=True)
        self.assertTrue(all(call['config'] == '/explicit/test/config' for call in calls))

    def test_api_failure_stops_repeated_requests(self):
        result, calls, _ = self.run_diagnose(fail_api=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn('Kubernetes API недоступен', result.stderr)

    def test_coredns_is_diagnosed_before_application_stack(self):
        result, calls, _ = self.run_diagnose()
        self.assertEqual(result.returncode, 0)
        args = [call['args'] for call in calls]
        dns = next(i for i, call in enumerate(args) if 'describe' in call and 'kube-system' in call)
        app = next(i for i, call in enumerate(args) if 'devops-lab' in call)
        self.assertLess(dns, app)
        self.assertTrue(all(call[0] == '--request-timeout=15s' for call in args))

    def test_missing_application_namespaces_skip_their_logs(self):
        result, calls, _ = self.run_diagnose(namespaces=False)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(any('-n' in call['args'] and
                             any(ns in call['args'] for ns in ('devops-lab', 'nginx-gateway')) for call in calls))


if __name__ == '__main__':
    unittest.main()
