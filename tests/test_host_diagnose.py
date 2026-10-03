import sys
import ujson
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts/diagnose-host.sh'


class HostDiagnoseTests(unittest.TestCase):
    def test_production_script_works_offline_and_only_reads_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'admin.conf'
            config.write_text('credentials must never be dumped')
            calls = root / 'calls'
            stub = root / 'stub'
            stub.write_text('''#!/usr/bin/env python3
import ujson, os, pathlib, sys
with open(os.environ['HOST_CALLS'], 'a') as file:
    file.write(ujson.dumps([pathlib.Path(sys.argv[0]).name] + sys.argv[1:]) + '\\n')
name = pathlib.Path(sys.argv[0]).name
if name == 'systemctl': sys.exit(3)
if name == 'kubectl': print('https://172.26.12.112:6443')
if name == 'crictl' and '--quiet' in sys.argv: print('abcdef123456')
''')
            stub.chmod(0o755)
            for name in ('ps', 'ip', 'kubectl', 'systemctl', 'ss', 'swapon', 'free', 'df', 'journalctl', 'crictl'):
                (root / name).symlink_to(stub)
            production = SCRIPT.read_text().replace('$EUID', '0').replace('/etc/kubernetes/admin.conf', str(config))
            script = root / 'diagnose-host.sh'
            script.write_text(production)
            result = subprocess.run(['bash', str(script)], capture_output=True, text=True, timeout=10,
                                    env=dict(os.environ, PATH=str(root) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'], HOST_CALLS=str(calls)))
            self.assertEqual(result.returncode, 0, result.stderr)
            executed = [ujson.loads(line) for line in calls.read_text().splitlines()]
            self.assertTrue(any(call[0] == 'journalctl' for call in executed))
            self.assertTrue(any(call[0] == 'crictl' and 'logs' in call for call in executed))
            kubectl_calls = [call for call in executed if call[0] == 'kubectl']
            self.assertEqual(len(kubectl_calls), 1)
            self.assertIn('config', kubectl_calls[0])
            self.assertNotIn('--raw', kubectl_calls[0])
            self.assertNotIn('credentials must never be dumped', result.stdout)
            self.assertTrue(all(call[1] in ('is-active', 'is-enabled') for call in executed if call[0] == 'systemctl'))
            self.assertFalse(any('restart' in call or 'reset' in call or 'delete' in call for call in executed))


if __name__ == '__main__':
    unittest.main()
