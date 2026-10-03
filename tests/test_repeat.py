import sys
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RepeatTests(unittest.TestCase):
    def execute(self, fail_repeat):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / '.state').mkdir()
            (root / '.state/repeat-deploy.json').write_text('old successful proof')
            production = Path(__file__).parents[1] / 'scripts/check-repeat.sh'
            (root / 'scripts/check-repeat.sh').write_text(production.read_text().replace('$EUID', '0'))
            (root / 'scripts/collect-versions.py').write_text('print("versions collected")')
            (root / 'deploy.sh').write_text('''#!/bin/bash
python3 - <<'PY'
import ujson, os
from pathlib import Path
counter = Path('.state/count')
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
failed = attempt == 2 and os.environ['FAIL_REPEAT'] == 'yes'
Path('.state/verification.json').write_text(ujson.dumps({'status':'failed' if failed else 'passed'}))
raise SystemExit(1 if failed else 0)
PY
''')
            result = subprocess.run(['bash', str(root / 'scripts/check-repeat.sh')],
                                    env=dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'], FAIL_REPEAT='yes' if fail_repeat else 'no'),
                                    capture_output=True, text=True, timeout=20)
            return result.returncode, (root / '.state/first-deploy.json').exists(), (root / '.state/repeat-deploy.json').exists()

    def test_two_successful_deployments_save_two_reports(self):
        self.assertEqual(self.execute(False), (0, True, True))

    def test_failed_repeat_removes_old_success_proof(self):
        self.assertEqual(self.execute(True), (1, True, False))


if __name__ == '__main__':
    unittest.main()
