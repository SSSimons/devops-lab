import sys
import copy
import importlib.util
import ujson
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('recovery', Path(__file__).parents[1] / 'scripts/recover-statefulset.py')
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.workload = {'metadata': {'name': 'web', 'uid': 'owner-1', 'generation': 2},
                         'spec': {'replicas': 1},
                         'status': {'observedGeneration': 2, 'updateRevision': 'new'}}
        self.pod = {'metadata': {'uid': 'pod-1', 'resourceVersion': '42',
                                'labels': {'controller-revision-hash': 'old'},
                                'ownerReferences': [{'kind': 'StatefulSet', 'name': 'web',
                                                     'uid': 'owner-1', 'controller': True}]},
                    'status': {'conditions': [{'type': 'Ready', 'status': 'False'}]}}

    def test_stale_unready_pod_has_uid_and_version_preconditions(self):
        options = recovery.deletion_options(self.workload, self.pod)
        self.assertEqual(options['preconditions'], {'uid': 'pod-1', 'resourceVersion': '42'})
        self.assertNotIn('gracePeriodSeconds', options)

    def test_healthy_pod_is_left_for_normal_controller_rollout(self):
        self.pod['status']['conditions'][0]['status'] = 'True'
        self.assertIsNone(recovery.deletion_options(self.workload, self.pod))

    def test_failure_on_current_revision_is_not_restarted_in_a_loop(self):
        self.pod['metadata']['labels']['controller-revision-hash'] = 'new'
        self.assertIsNone(recovery.deletion_options(self.workload, self.pod))

    def test_missing_terminating_or_unknown_revision_is_not_deleted(self):
        self.assertIsNone(recovery.deletion_options(self.workload, None))
        for change in ({'deletionTimestamp': 'now'}, {'labels': {}}):
            pod = copy.deepcopy(self.pod)
            pod['metadata'].update(change)
            self.assertIsNone(recovery.deletion_options(self.workload, pod))

    def test_foreign_owner_is_rejected(self):
        for key, value in [('uid', 'other-owner'), ('kind', 'Deployment'), ('controller', False)]:
            pod = copy.deepcopy(self.pod)
            pod['metadata']['ownerReferences'][0][key] = value
            with self.assertRaises(ValueError):
                recovery.deletion_options(self.workload, pod)

    def test_multi_replica_or_partitioned_workload_is_rejected(self):
        for change in ({'replicas': 2}, {'updateStrategy': {'type': 'OnDelete'}},
                       {'updateStrategy': {'rollingUpdate': {'partition': 1}}}):
            workload = copy.deepcopy(self.workload)
            workload['spec'].update(change)
            with self.assertRaises(ValueError):
                recovery.deletion_options(workload, self.pod)

    def test_controller_observes_update_before_one_guarded_deletion(self):
        pending = copy.deepcopy(self.workload)
        pending['status']['observedGeneration'] = 1
        with patch.object(recovery, 'kubectl', side_effect=[pending, self.workload, self.pod, {}]) as call, \
                patch.object(recovery.time, 'sleep'):
            recovery.recover('web')
        self.assertEqual(call.call_count, 4)
        delete = call.call_args_list[-1]
        self.assertEqual(delete.args, ('delete', '--raw', '/api/v1/namespaces/devops-lab/pods/web-0', '-f', '-'))
        self.assertEqual(ujson.loads(delete.kwargs['body'])['preconditions']['uid'], 'pod-1')

    def test_new_pod_or_healthy_repeat_makes_no_delete_request(self):
        self.pod['metadata']['labels']['controller-revision-hash'] = 'new'
        with patch.object(recovery, 'kubectl', side_effect=[self.workload, self.pod]) as call:
            recovery.recover('web')
        self.assertEqual(call.call_count, 2)

    def test_unobserved_controller_timeout_does_not_touch_pod(self):
        self.workload['status']['observedGeneration'] = 1
        with patch.object(recovery, 'kubectl', return_value=self.workload) as call:
            with self.assertRaises(RuntimeError):
                recovery.recover('web', timeout=0)
        self.assertEqual(call.call_count, 1)

    def test_delete_conflict_stops_without_retry_or_force(self):
        with patch.object(recovery, 'kubectl', side_effect=[self.workload, self.pod, RuntimeError('409 Conflict')]) as call:
            with self.assertRaisesRegex(RuntimeError, '409 Conflict'):
                recovery.recover('web')
        self.assertEqual(call.call_count, 3)

    def test_deploy_applies_hash_and_recovery_before_waiting_for_broken_pod(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('scripts', 'vendor', 'k8s', 'bin', 'web'):
                (root / name).mkdir()
            production = Path(__file__).parents[1] / 'scripts/deploy-stack.sh'
            (root / 'scripts/deploy-stack.sh').write_text(production.read_text())
            (root / 'scripts/nginx-run.sh').write_text('rotation configuration')
            (root / 'vendor/SHA256SUMS').write_text('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  vendor/flannel.yaml\n')
            (root / 'vendor/flannel.yaml').touch()
            for name in ('10-app.yaml', '30-monitoring.yaml'):
                (root / 'k8s' / name).touch()
            for name in ('cats.html', 'cat.png'):
                (root / 'web' / name).touch()
            # Подменяем внешние команды контроллера, порядок запуска проверяем настоящим Bash-скриптом.
            fake = root / 'bin/kubectl'
            fake.write_text('''#!/usr/bin/env python3
import ujson, os, pathlib, sys
log = pathlib.Path(os.environ['RECOVERY_CALLS'])
args = sys.argv[1:]
previous = [ujson.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open('a') as stream:
    stream.write(ujson.dumps(args) + '\\n')
if args == ['get', 'nodes', '--no-headers']:
    print('node Ready control-plane')
if 'get' in args and any('config-hash' in arg for arg in args):
    workload = next(x for x in args if x.startswith('statefulset/'))
    patches = [call for call in previous if 'patch' in call and workload in call]
    if patches:
        print(ujson.loads(patches[-1][-1])['spec']['template']['metadata']['annotations']['devops-lab/config-hash'])
if 'rollout' in args and 'status' in args:
    workload = next((x for x in args if x.startswith('statefulset/')), None)
    if workload:
        assert any('patch' in call and workload in call for call in previous), 'wait before config hash patch'
        assert ['recover', workload.split('/')[1]] in previous, 'wait before recovery'
''')
            fake.chmod(0o755)
            (root / 'scripts/recover-statefulset.py').write_text('''import ujson, os, sys
with open(os.environ['RECOVERY_CALLS'], 'a') as stream:
    stream.write(ujson.dumps(['recover', sys.argv[1]]) + '\\n')
''')
            env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                       RECOVERY_CALLS=str(root / 'calls'))
            result = subprocess.run(['bash', str(root / 'scripts/deploy-stack.sh')],
                                    env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Повторный запуск без изменений не должен обновлять шаблоны Pod.
            result = subprocess.run(['bash', str(root / 'scripts/deploy-stack.sh')],
                                    env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Изменение страницы котика обновляет только web, конфиг метрик прежний.
            (root / 'web/cats.html').write_text('Новая страница котика')
            result = subprocess.run(['bash', str(root / 'scripts/deploy-stack.sh')],
                                    env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [ujson.loads(line) for line in (root / 'calls').read_text().splitlines()]
            for workload, expected in [('statefulset/web', 2), ('statefulset/prometheus', 1)]:
                patches = [call for call in calls if 'patch' in call and workload in call]
                self.assertEqual(len(patches), expected, workload)


if __name__ == '__main__':
    unittest.main()
