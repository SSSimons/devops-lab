import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

HELPER = Path(__file__).parents[1] / 'scripts/configure-containerd.py'
spec = importlib.util.spec_from_file_location('configure_containerd', HELPER)
configurator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configurator)
PAUSE = 'registry.k8s.io/pause:3.10.1'


def fixture(version, quote='"', cgroup='false'):
    runtime = 'io.containerd.grpc.v1.cri' if version == 2 else 'io.containerd.cri.v1.runtime'
    image = (f'[plugins.{quote}{runtime}{quote}]\n  sandbox_image = {quote}old:pause{quote}\n'
             if version == 2 else
             f'[plugins.{quote}io.containerd.cri.v1.images{quote}.pinned_images]\n  sandbox = {quote}old:pause{quote}\n')
    return (f'version = {version}\nroot = {quote}/var/lib/containerd{quote}\ndisabled_plugins = []\n'
            + image + f'[plugins.{quote}{runtime}{quote}.containerd]\n  default_runtime_name = {quote}runc{quote}\n'
            f'[plugins.{quote}{runtime}{quote}.containerd.runtimes.runc]\n  runtime_type = {quote}io.containerd.runc.v2{quote}\n'
            f'[plugins.{quote}{runtime}{quote}.containerd.runtimes.runc.options]\n  SystemdCgroup = {cgroup} # existing setting\n'
            f'  BinaryName = {quote}/usr/bin/runc{quote}\n[metrics]\n  address = {quote}127.0.0.1:1338{quote}\n')


class ContainerdTests(unittest.TestCase):
    def test_versions_and_quote_styles(self):
        for version in (2, 3):
            for quote in ('"', "'"):
                with self.subTest(version=version, quote=quote):
                    result = tomllib.loads(configurator.configure(fixture(version, quote), PAUSE))
                    runtime = 'io.containerd.grpc.v1.cri' if version == 2 else 'io.containerd.cri.v1.runtime'
                    self.assertTrue(result['plugins'][runtime]['containerd']['runtimes']['runc']['options']['SystemdCgroup'])
                    image = (result['plugins'][runtime]['sandbox_image'] if version == 2 else
                             result['plugins']['io.containerd.cri.v1.images']['pinned_images']['sandbox'])
                    self.assertEqual(image, PAUSE)
                    self.assertEqual(result['metrics']['address'], '127.0.0.1:1338')
                    self.assertEqual(result['plugins'][runtime]['containerd']['runtimes']['runc']['options']['BinaryName'], '/usr/bin/runc')

    def test_missing_cgroup_setting_is_added(self):
        for version in (2, 3):
            source = fixture(version).replace('  SystemdCgroup = false # existing setting\n', '')
            result = configurator.configure(source, PAUSE)
            self.assertIn('SystemdCgroup = true', result)
            tomllib.loads(result)

    def test_missing_options_table_is_added(self):
        source = fixture(3)
        source = source[:source.index('[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runc.options]')]
        result = tomllib.loads(configurator.configure(source, PAUSE))
        self.assertTrue(result['plugins']['io.containerd.cri.v1.runtime']['containerd']['runtimes']['runc']['options']['SystemdCgroup'])

    def test_missing_pinned_image_table_is_added(self):
        source = fixture(3).replace('[plugins."io.containerd.cri.v1.images".pinned_images]\n  sandbox = "old:pause"\n',
                                    '[plugins."io.containerd.cri.v1.images"]\n  snapshotter = "overlayfs"\n')
        result = tomllib.loads(configurator.configure(source, PAUSE))
        self.assertEqual(result['plugins']['io.containerd.cri.v1.images']['pinned_images']['sandbox'], PAUSE)

    def test_repeated_configuration_is_idempotent(self):
        for version in (2, 3):
            result = configurator.configure(fixture(version, cgroup='true'), PAUSE)
            self.assertEqual(configurator.configure(result, PAUSE), result)

    def test_disabled_cri_is_rejected(self):
        for version in (2, 3):
            source = fixture(version).replace('disabled_plugins = []', 'disabled_plugins = ["cri"]')
            with self.assertRaisesRegex(ValueError, 'CRI отключён'):
                configurator.configure(source, PAUSE)

    def test_custom_runtime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'runtime по умолчанию должен быть runc'):
            configurator.configure(fixture(3).replace('default_runtime_name = "runc"', 'default_runtime_name = "other"'), PAUSE)

    def test_unknown_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Версия конфига containerd не поддерживается'):
            configurator.configure(fixture(3).replace('version = 3', 'version = 4'), PAUSE)

    def test_invalid_toml_is_rejected(self):
        with self.assertRaises(tomllib.TOMLDecodeError):
            configurator.configure('version = [', PAUSE)

    def test_invalid_pause_input_is_rejected(self):
        for value in ('', PAUSE + '\n' + PAUSE, 'other/image:tag', 'registry.k8s.io/pause:3.10"'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                configurator.configure(fixture(3), value)

    def test_cli_does_not_overwrite_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            path.write_text('version = [')
            result = subprocess.run([sys.executable, str(HELPER), str(path), PAUSE], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), 'version = [')

    def test_check_mode_rejects_incorrect_settings_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            original = fixture(3)
            path.write_text(original)
            command = [sys.executable, str(HELPER), str(path), PAUSE, '--check']
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(path.read_text(), original)
            path.write_text(configurator.configure(original, PAUSE))
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
