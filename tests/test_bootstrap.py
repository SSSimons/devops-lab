from pathlib import Path
import re
import subprocess
import tempfile
import unittest

BOOTSTRAP = Path(__file__).parents[1] / 'scripts/bootstrap.sh'
# Выполняем проверку из bootstrap, не запуская настройку системы.
FUNCTION = re.search(r'^has_static_pods\(\) \{.*?^\}', BOOTSTRAP.read_text(), re.M | re.S).group(0)


def has_static_pods(path):
    result = subprocess.run(['bash', '-c',
                             'set -Eeuo pipefail\ndie() { exit 2; }\n' + FUNCTION + '\nhas_static_pods "$1"',
                             'test-bootstrap', str(path)], capture_output=True)
    if result.returncode not in (0, 1):
        raise AssertionError(result.stderr.decode())
    return result.returncode == 0


class BootstrapTests(unittest.TestCase):
    def test_empty_directory_does_not_block_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(has_static_pods(directory))

    def test_missing_directory_does_not_block_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(has_static_pods(Path(directory) / 'missing'))

    def test_kubelet_keep_and_hidden_manifests_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '.kubelet-keep').touch()
            (path / '.backup.yaml').write_text('apiVersion: v1\nkind: Pod\n')
            self.assertFalse(has_static_pods(path))

    def test_control_plane_manifest_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '.kubelet-keep').touch()
            (path / 'kube-apiserver.yaml').write_text('apiVersion: v1\nkind: Pod\n')
            self.assertTrue(has_static_pods(path))

    def test_files_without_yaml_extension_also_block_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'kube-apiserver.backup').write_text('apiVersion: v1\nkind: Pod\n')
            self.assertTrue(has_static_pods(path))

    def test_visible_symlink_to_manifest_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / '.source').write_text('apiVersion: v1\nkind: Pod\n')
            (path / 'linked.yaml').symlink_to(path / '.source')
            self.assertTrue(has_static_pods(path))


if __name__ == '__main__':
    unittest.main()
