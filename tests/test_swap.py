import sys
import ujson
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts/configure-swap.sh'


class SwapTests(unittest.TestCase):
    def fixture(self, root, owner=True):
        (root / 'state').mkdir()
        (root / 'bin').mkdir()
        if owner:
            (root / 'state/runtime-owner').write_text('devops-lab-containerd-v1\n')
        (root / 'swap').write_text('/dev/sdc\n')
        stub = root / 'stub'
        stub.write_text('''#!/usr/bin/env python3
import ujson, os, pathlib, sys
name=pathlib.Path(sys.argv[0]).name
with open(os.environ['SWAP_CALLS'],'a') as file:
    file.write(ujson.dumps([name]+sys.argv[1:])+'\\n')
state=pathlib.Path(os.environ['SWAP_FIXTURE'])
if name=='swapon': print(state.read_text(),end='')
if name=='swapoff' and os.environ.get('KEEP_SWAP')!='yes': state.write_text('')
''')
        stub.chmod(0o755)
        for command in ('systemctl', 'swapon', 'swapoff'):
            (root / 'bin' / command).symlink_to(stub)
        script = root / 'configure-swap.sh'
        script.write_text(SCRIPT.read_text().replace('$EUID','0').replace('/var/lib/devops-lab',str(root/'state'))
                          .replace('/etc/systemd/system',str(root/'units')))
        env = dict(os.environ, PATH=str(root/'bin')+os.pathsep+str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                   SWAP_CALLS=str(root/'calls'),SWAP_FIXTURE=str(root/'swap'))
        return script, env

    def test_swap_is_disabled_and_kubelet_requires_boot_service_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script,env=self.fixture(root)
            result=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'changed')
            unit=root/'units/devops-lab-no-swap.service'
            drop=root/'units/kubelet.service.d/20-devops-lab-no-swap.conf'
            self.assertIn('ExecStart=/usr/sbin/swapoff -a',unit.read_text())
            self.assertIn('After=swap.target',unit.read_text())
            self.assertIn('Requires=devops-lab-no-swap.service',drop.read_text())
            before=(unit.stat().st_mtime_ns,drop.stat().st_mtime_ns)
            result=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'unchanged')
            self.assertEqual(before,(unit.stat().st_mtime_ns,drop.stat().st_mtime_ns))
            calls=[ujson.loads(line) for line in (root/'calls').read_text().splitlines()]
            self.assertEqual(calls.count(['systemctl','daemon-reload']),1)
            self.assertEqual(calls.count(['swapoff','-a']),1)

    def test_runtime_without_ownership_is_not_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script,env=self.fixture(root,owner=False)
            result=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'units').exists())
            self.assertFalse((root/'calls').exists())

    def test_managed_bootstrap_repairs_swap_before_checking_existing_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            _,env=self.fixture(root)
            (root/'admin.conf').touch()
            (root/'state/managed-cluster').write_text('1.35.9\n')
            (root/'check-cluster.py').write_text('''import ujson, os, pathlib, sys
assert not pathlib.Path(os.environ['SWAP_FIXTURE']).read_text(), 'API checked before disabling swap'
calls=[ujson.loads(line) for line in pathlib.Path(os.environ['SWAP_CALLS']).read_text().splitlines()]
assert ['systemctl','restart','kubelet'] in calls, 'API checked before restarting kubelet'
assert '--wait' in sys.argv and '--expected' in sys.argv
assert sys.argv[sys.argv.index('--expected')+1]=='1.35.9'
print('Existing API verified after recovery')
''')
            (root/'configure-dns.py').write_text("print('unchanged')\n")
            # Установка Python проверяется отдельно; этот fixture проверяет порядок swap/API.
            (root/'setup-python.sh').write_text('exit 0\n')
            source=(SCRIPT.parent/'bootstrap.sh').read_text()
            start=source.index('if [[ -f /etc/kubernetes/admin.conf ]]; then')
            branch=source[start:source.index('\nif dpkg-query',start)]
            branch=branch.replace('/etc/kubernetes/admin.conf',str(root/'admin.conf'))
            bootstrap=root/'managed-bootstrap.sh'
            bootstrap.write_text('set -Eeuo pipefail\nK8S_VERSION=1.35.9\n'
                                 'SCRIPT_DIR="$1"\nSTATE_DIR="$1/state"\n'
                                 'die() { echo "$*" >&2; exit 1; }\n'+branch)
            result=subprocess.run(['bash',str(bootstrap),str(root)],env=env,
                                  text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Existing API verified after recovery',result.stdout)
            self.assertIn('Повторная установка системных пакетов не требуется',result.stdout)

    def test_swapoff_not_effective_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script,env=self.fixture(root)
            env['KEEP_SWAP']='yes'
            result=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Swap всё ещё включён',result.stderr)
            self.assertNotIn('changed',result.stdout)

    def test_refuses_symlink_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script,env=self.fixture(root)
            (root/'units').mkdir()
            target=root/'foreign.service'
            target.write_text('foreign')
            (root/'units/devops-lab-no-swap.service').symlink_to(target)
            result=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(target.read_text(),'foreign')


if __name__=='__main__':
    unittest.main()
