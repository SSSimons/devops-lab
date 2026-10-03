import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('cluster_check', Path(__file__).parents[1] / 'scripts/check-cluster.py')
cluster = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cluster)


class ClusterCheckTests(unittest.TestCase):
    def response(self, body, code=0, error=''):
        return subprocess.CompletedProcess(['kubectl'], code, body, error)

    def test_verified_server_version_and_bounded_request(self):
        with patch.object(cluster.subprocess, 'run', return_value=self.response(
                '{"serverVersion":{"gitVersion":"v1.35.9"}}')) as call:
            self.assertEqual(cluster.check('/fixture/admin.conf', '1.35.9'), 'v1.35.9')
        self.assertIn('--request-timeout=15s', call.call_args.args[0])
        self.assertEqual(call.call_args.kwargs['timeout'], 20)

    def test_refused_connection_is_not_reported_as_version_mismatch(self):
        with patch.object(cluster.subprocess, 'run', return_value=self.response(
                '{"clientVersion":{"gitVersion":"v1.35.9"}}', 1, 'connection refused')):
            with self.assertRaisesRegex(RuntimeError, 'API недоступен: connection refused'):
                cluster.check('/fixture/admin.conf', '1.35.9')

    def test_client_only_json_is_not_a_verified_cluster(self):
        with patch.object(cluster.subprocess, 'run', return_value=self.response(
                '{"clientVersion":{"gitVersion":"v1.35.9"}}')):
            with self.assertRaisesRegex(RuntimeError, 'нет serverVersion'):
                cluster.check('/fixture/admin.conf', '1.35.9')

    def test_real_version_mismatch_still_blocks_automatic_upgrade(self):
        with patch.object(cluster.subprocess, 'run', return_value=self.response(
                '{"serverVersion":{"gitVersion":"v1.35.8"}}')):
            with self.assertRaisesRegex(RuntimeError, 'нужна v1.35.9, получена v1.35.8'):
                cluster.check('/fixture/admin.conf', '1.35.9')

    def test_malformed_responses_produce_useful_errors(self):
        for body in ('not-json', '[]', '{"serverVersion":null}', '{"serverVersion":"unexpected"}'):
            with patch.object(cluster.subprocess, 'run', return_value=self.response(body)):
                with self.assertRaisesRegex(RuntimeError, 'Некорректный ответ kubectl version'):
                    cluster.check('/fixture/admin.conf', '1.35.9')

    def test_cli_timeout_has_no_traceback(self):
        with patch.object(cluster.sys, 'argv', ['check-cluster.py', '--kubeconfig', '/fixture/admin.conf',
                                               '--expected', '1.35.9']), \
                patch.object(cluster.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['kubectl'], 20)), \
                patch.object(cluster.sys, 'stderr') as stderr:
            self.assertEqual(cluster.main(), 1)
        self.assertIn('diagnose-host.sh', ''.join(str(call) for call in stderr.write.call_args_list))

    def test_api_startup_can_recover_during_bounded_wait(self):
        with patch.object(cluster, 'check', side_effect=[cluster.APIUnavailable('starting'), 'v1.35.9']), \
                patch.object(cluster.time, 'sleep'):
            self.assertEqual(cluster.wait_for_cluster('/fixture/admin.conf', '1.35.9', 30), 'v1.35.9')

    def test_version_mismatch_is_not_retried(self):
        with patch.object(cluster, 'check', side_effect=RuntimeError('version differs')) as call:
            with self.assertRaisesRegex(RuntimeError, 'version differs'):
                cluster.wait_for_cluster('/fixture/admin.conf', '1.35.9', 30)
        self.assertEqual(call.call_count, 1)

    def test_api_wait_deadline_is_enforced(self):
        with patch.object(cluster, 'check', side_effect=cluster.APIUnavailable('refused')) as call, \
                patch.object(cluster.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(cluster.APIUnavailable, 'refused'):
                cluster.wait_for_cluster('/fixture/admin.conf', '1.35.9', 30)
        self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
