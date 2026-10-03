import importlib.util
import ujson
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('versions', Path(__file__).parents[1] / 'scripts/collect-versions.py')
versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(versions)


class VersionTests(unittest.TestCase):
    def fixture_collect(self, kubelet='v1.35.9'):
        pins = ujson.loads((versions.ROOT / 'config/versions.json').read_text())
        repos = [('nginx', 'devops-lab', 'nginx'),
                 ('nginx_gateway_fabric', 'nginx-gateway', 'ghcr.io/nginx/nginx-gateway-fabric'),
                 ('nginx_gateway_fabric', 'devops-lab', 'ghcr.io/nginx/nginx-gateway-fabric/nginx'),
                 ('flannel', 'kube-flannel', 'ghcr.io/flannel-io/flannel'),
                 ('prometheus', 'devops-lab', 'prom/prometheus'),
                 ('nginx_prometheus_exporter', 'devops-lab', 'nginx/nginx-prometheus-exporter'),
                 ('busybox', 'devops-lab', 'busybox'),
                 ('fluentd', 'devops-lab', 'fluent/fluentd')]
        pods = {'items': []}
        for key, ns, repo in repos:
            image = repo + ':' + pins[key]
            pods['items'].append({'metadata': {'namespace': ns, 'name': 'private-pod'},
                                  'spec': {'containers': [{'name': key, 'image': image}]},
                                  'status': {'containerStatuses': [{'name': key, 'image': image,
                                                                   'imageID': repo + '@sha256:abc'}]}})

        def command(args, optional=False):
            if args[0] == 'dpkg-query':
                return 'kubeadm\t1.35.9-1.1\ncontainerd-app\t2.2.1-0ubuntu1'
            if args[0] == 'kubeadm':
                return 'v1.35.9'
            if args[1] == 'version':
                return ujson.dumps({'clientVersion': {'gitVersion': 'v1.35.9'},
                                   'serverVersion': {'gitVersion': 'v1.35.9'}})
            if args[2] == 'nodes':
                return ujson.dumps({'items': [{'metadata': {'name': 'private-node'}, 'status': {
                    'nodeInfo': {'kernelVersion': '6.18-microsoft-standard-WSL2',
                                 'kubeletVersion': kubelet, 'containerRuntimeVersion': 'containerd://2.2.1'}}}]})
            if args[2] == 'pods':
                return ujson.dumps(pods)
            return ujson.dumps({'metadata': {'annotations': {'gateway.networking.k8s.io/bundle-version': pins['gateway_api']}}})

        with patch.object(versions, 'command', side_effect=command), patch.object(versions, 'os_info',
                return_value={'PRETTY_NAME': 'Ubuntu 24.04.5 LTS'}):
            return versions.collect(['kubectl'])

    def test_live_collection_joins_sources_without_private_identifiers(self):
        data = self.fixture_collect()
        self.assertEqual(data['observed']['containerd'], 'containerd://2.2.1')
        self.assertEqual(data['observed']['environment'], 'WSL2')
        self.assertEqual(data['observed']['ujson'], ujson.__version__)
        self.assertEqual(data['packages']['kubeadm'], '1.35.9-1.1')
        self.assertEqual(data['differences_from_pins'], [])
        self.assertNotIn('private-node', ujson.dumps(data))
        self.assertNotIn('private-pod', ujson.dumps(data))
        self.assertIn('sha256:abc', ujson.dumps(data))

    def test_live_collection_reports_version_mismatch(self):
        data = self.fixture_collect(kubelet='v1.35.8')
        self.assertEqual(data['differences_from_pins'], ['kubernetes'])

    def test_wrong_python_environment_reports_ujson_pin_mismatch(self):
        with patch.object(versions.ujson, '__version__', '5.9.0'):
            data = self.fixture_collect()
        self.assertEqual(data['differences_from_pins'], ['ujson'])

    def test_private_host_details_are_not_in_image_inventory(self):
        data = {'items': [{'metadata': {'namespace': 'devops-lab', 'name': 'private-host', 'uid': 'private-uid'},
                           'spec': {'containers': [{'name': 'nginx', 'image': 'nginx:1.28.0-alpine'}]},
                           'status': {'podIP': '10.0.0.1', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                                      'containerStatuses': [{'name': 'nginx', 'image': 'docker.io/library/nginx:1.28.0-alpine',
                                                             'imageID': 'docker.io/library/nginx@sha256:abc'}]}}]}
        images, readiness = versions.image_inventory(data)
        self.assertEqual(versions.image_version(images, 'nginx', 'devops-lab'), '1.28.0-alpine')
        self.assertEqual(readiness['devops-lab']['ready'], 1)
        self.assertNotIn('private-host', ujson.dumps(images))
        self.assertNotIn('10.0.0.1', ujson.dumps(images))
        self.assertIn('sha256:abc', ujson.dumps(images))

    def test_ambiguous_images_do_not_claim_one_version(self):
        items = [{'namespace': 'devops-lab', 'image': 'nginx:1.28.0-alpine'},
                 {'namespace': 'devops-lab', 'image': 'docker.io/library/nginx:1.27.0-alpine'}]
        self.assertIsNone(versions.image_version(items, 'nginx'))

    def test_document_update_preserves_the_rest_of_readme(self):
        text = 'before\n' + versions.BEGIN + '\nold\n' + versions.END + '\nafter'
        data = {'source': 'fixture', 'observed': {'python': '3.12.3'}, 'captured_at_utc': 'now'}
        result = versions.update_readme(text, data)
        self.assertTrue(result.startswith('before\n'))
        self.assertTrue(result.endswith('\nafter'))
        self.assertIn('3.12.3', result)
        self.assertNotIn('\nold\n', result)

    def test_missing_markers_do_not_overwrite_readme(self):
        with self.assertRaises(ValueError):
            versions.update_readme('unmarked', {'source': 'fixture', 'observed': {}})

    def test_os_release_quotes_are_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'os-release'
            path.write_text('PRETTY_NAME="Ubuntu 24.04.5 LTS"\nVERSION_ID="24.04"\n')
            self.assertEqual(versions.os_info(path)['PRETTY_NAME'], 'Ubuntu 24.04.5 LTS')


if __name__ == '__main__':
    unittest.main()
