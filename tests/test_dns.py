import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('configure_dns', Path(__file__).parents[1] / 'scripts/configure-dns.py')
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)
KUBELET = 'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\ncgroupDriver: systemd\nresolvConf: /run/systemd/resolve/resolv.conf\n'


class DNSTests(unittest.TestCase):
    def test_empty_resolved_file_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'нет подходящих DNS-адресов'):
            dns.resolver_text('# No DNS servers known.\nsearch .\n')

    def test_wsl_empty_resolved_falls_back_to_host_nameserver(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            resolved, host = path / 'resolved', path / 'host'
            resolved.write_text('# No DNS servers known.\nsearch .\n')
            host.write_text('nameserver 9.9.9.9\noptions edns0 trust-ad\nsearch .\n')
            result = dns.choose_resolver([resolved, host])
            self.assertIn('nameserver 9.9.9.9\n', result)
            self.assertIn('options edns0 trust-ad\n', result)

    def test_local_dns_stubs_are_excluded(self):
        result = dns.resolver_text('nameserver 127.0.0.53\nnameserver ::1\nnameserver 9.9.9.9\n')
        self.assertNotIn('127.0.0.53', result)
        self.assertNotIn('::1', result)
        self.assertIn('9.9.9.9', result)

    def test_only_local_or_invalid_addresses_fail(self):
        for text in ('nameserver 127.0.0.53\n', 'nameserver ::1\n', 'nameserver example.com\n',
                     'nameserver 0.0.0.0\n', 'nameserver 224.0.0.1\n', 'nameserver fe80::1%eth0\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                dns.resolver_text(text)

    def test_private_and_global_ipv6_dns_are_supported(self):
        result = dns.resolver_text('nameserver 10.255.255.254\nnameserver 2620:fe::fe\n')
        self.assertIn('10.255.255.254', result)
        self.assertIn('2620:fe::fe', result)

    def test_nameservers_are_deduplicated_and_limited(self):
        result = dns.resolver_text('nameserver 9.9.9.9\nnameserver 9.9.9.9\nnameserver 1.1.1.1\nnameserver 8.8.8.8\nnameserver 8.8.4.4\n')
        self.assertEqual(result.count('nameserver '), 3)
        self.assertEqual(result.count('nameserver 9.9.9.9'), 1)

    def test_missing_source_uses_next_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / 'host'
            source.write_text('nameserver 9.9.9.9\n')
            self.assertIn('9.9.9.9', dns.choose_resolver([path / 'missing', source]))

    def test_kubelet_resolver_is_replaced_and_other_settings_preserved(self):
        result = dns.kubelet_text(KUBELET, Path('/var/lib/devops-lab/resolv.conf'))
        self.assertIn('resolvConf: "/var/lib/devops-lab/resolv.conf"', result)
        self.assertIn('cgroupDriver: systemd', result)
        self.assertNotIn('/run/systemd/resolve/resolv.conf', result)

    def test_missing_resolver_key_is_added(self):
        result = dns.kubelet_text(KUBELET.replace('resolvConf: /run/systemd/resolve/resolv.conf\n', ''), Path('/test/resolv.conf'))
        self.assertIn('resolvConf: "/test/resolv.conf"', result)

    def test_duplicate_resolver_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'повторяющиеся ключи'):
            dns.kubelet_text(KUBELET + 'resolvConf: /other\n', Path('/test/resolv.conf'))

    def test_repeated_configuration_preserves_original_backup_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            host, config, output = path / 'host', path / 'config.yaml', path / 'managed/resolv.conf'
            host.write_text('nameserver 9.9.9.9\n')
            config.write_text(KUBELET)
            config.chmod(0o600)
            self.assertTrue(dns.configure(output, [host], config))
            self.assertFalse(dns.configure(output, [host], config))
            self.assertEqual((output.parent / 'kubelet-config.before-dns').read_text(), KUBELET)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(host.read_text(), 'nameserver 9.9.9.9\n')

    def test_missing_upstream_does_not_modify_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, config, output = path / 'empty', path / 'config.yaml', path / 'managed/resolv.conf'
            source.write_text('# No DNS servers known.\n')
            config.write_text(KUBELET)
            with self.assertRaises(ValueError):
                dns.configure(output, [source], config)
            self.assertEqual(config.read_text(), KUBELET)
            self.assertFalse(output.exists())

    def test_wrong_config_kind_fails_before_creating_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, config, output = path / 'host', path / 'wrong.yaml', path / 'managed/resolv.conf'
            source.write_text('nameserver 9.9.9.9\n')
            config.write_text('kind: ConfigMap\n')
            with self.assertRaisesRegex(ValueError, 'KubeletConfiguration'):
                dns.configure(output, [source], config)
            self.assertFalse(output.exists())
            self.assertEqual(config.read_text(), 'kind: ConfigMap\n')


if __name__ == '__main__':
    unittest.main()
