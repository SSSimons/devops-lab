import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

spec = importlib.util.spec_from_file_location('verify', Path(__file__).parents[1] / 'scripts/verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerificationTests(unittest.TestCase):
    def targets_response(self, health='up', error=''):
        import ujson
        return ujson.dumps({'status': 'success', 'data': {'activeTargets': [
            {'labels': {'job': job}, 'health': health, 'lastError': error,
             'scrapeUrl': f'http://{job}:9113/metrics'}
            for job in ('prometheus', 'nginx', 'gateway-controller')]}}), {}

    def test_targets_wait_for_first_scrape_after_restart(self):
        with patch.object(verify, 'http_get', side_effect=[
                self.targets_response('unknown'), self.targets_response()]) as request:
            targets = verify.retry(lambda: verify.check_prometheus_targets('http://prom'), interval=0)
        self.assertEqual(len(targets), 3)
        self.assertEqual(request.call_count, 2)

    def test_unavailable_target_error_identifies_job_endpoint_and_reason(self):
        with patch.object(verify, 'http_get', return_value=self.targets_response('down', 'connection refused')):
            with self.assertRaises(verify.VerificationError) as error:
                verify.retry(lambda: verify.check_prometheus_targets('http://prom'), seconds=0)
        for detail in ('gateway-controller', 'http://nginx:9113/metrics', 'connection refused', 'health=down'):
            self.assertIn(detail, str(error.exception))

    def test_three_copies_of_one_job_do_not_replace_missing_jobs(self):
        import ujson
        response, _ = self.targets_response()
        data = ujson.loads(response)
        for target in data['data']['activeTargets']:
            target['labels']['job'] = 'nginx'
        with patch.object(verify, 'http_get', return_value=(ujson.dumps(data), {})):
            with self.assertRaisesRegex(verify.VerificationError, 'Отсутствуют источники: gateway-controller, prometheus'):
                verify.check_prometheus_targets('http://prom')

    def test_empty_or_malformed_targets_response_is_rejected(self):
        import ujson
        for data in ([], {}, {'status': 'error', 'data': {'activeTargets': []}},
                     {'status': 'success', 'data': {'activeTargets': []}},
                     {'status': 'success', 'data': {'activeTargets': [None]}},
                     {'status': 'success', 'data': {'activeTargets': [{'labels': None}]}}):
            with self.subTest(data=data), patch.object(verify, 'http_get', return_value=(ujson.dumps(data), {})):
                with self.assertRaises(verify.VerificationError):
                    verify.check_prometheus_targets('http://prom')

    def test_up_with_scrape_error_is_rejected(self):
        with patch.object(verify, 'http_get', return_value=self.targets_response('up', 'timeout')):
            with self.assertRaisesRegex(verify.VerificationError, 'timeout'):
                verify.check_prometheus_targets('http://prom')

    def test_additional_down_target_is_not_ignored(self):
        import ujson
        response, _ = self.targets_response()
        data = ujson.loads(response)
        data['data']['activeTargets'].append({'labels': {'job': 'extra'}, 'health': 'down'})
        with patch.object(verify, 'http_get', return_value=(ujson.dumps(data), {})):
            with self.assertRaisesRegex(verify.VerificationError, 'extra: health=down'):
                verify.check_prometheus_targets('http://prom')

    def test_redirect_handling_over_real_http_connection(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/cats/cat.png':
                    body = (Path(__file__).parents[1] / 'web/cat.png').read_bytes()
                elif self.path == '/cats':
                    body = (Path(__file__).parents[1] / 'web/cats.html').read_bytes()
                else:
                    body = b'{"service":"devops-lab"}'
                if self.path == '/legacy':
                    self.send_response(302)
                    self.send_header('Location', '/api/info')
                else:
                    self.send_response(200)
                    self.send_header('X-DevOps-Route', 'api-rewrite')
                    self.send_header('X-DevOps-Lab', 'gateway-api')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            self.assertIn('redirect', verify.check_gateway_features(base))
            self.assertEqual(verify.http_get(base + '/legacy')[0], '{"service":"devops-lab"}')
            self.assertEqual(verify.check_cats(base)['page'], '/cats')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def resource(self, generation=2, observed=2):
        return {'metadata': {'generation': generation}, 'status': {'conditions': [
            {'type': kind, 'status': 'True', 'observedGeneration': observed}
            for kind in ('Accepted', 'Programmed', 'ResolvedRefs')]}}

    def test_stale_gateway_status_is_not_success(self):
        self.assertFalse(verify.conditions_ready(self.resource(observed=1), ('Accepted', 'Programmed')))
        self.assertTrue(verify.conditions_ready(self.resource(), ('Accepted', 'Programmed')))

    def test_route_must_belong_to_correct_controller_and_gateway(self):
        route = self.resource()
        route['status']['parents'] = [{'parentRef': {'name': 'lab'},
                                      'controllerName': 'gateway.nginx.org/nginx-gateway-controller',
                                      'conditions': route['status']['conditions']}]
        self.assertTrue(verify.route_ready(route))
        route['status']['parents'][0]['parentRef']['name'] = 'other'
        self.assertFalse(verify.route_ready(route))

    def test_hello_without_gateway_header_is_rejected(self):
        with patch.object(verify, 'http_get', return_value=('Hello World!\n', {})):
            with self.assertRaises(verify.VerificationError):
                verify.check_hello('http://test/')

    def test_wrong_hello_body_is_rejected(self):
        with patch.object(verify, 'http_get', return_value=('Hello World?', {'X-DevOps-Lab': 'gateway-api'})):
            with self.assertRaises(verify.VerificationError):
                verify.check_hello('http://test/')

    def test_retry_recovers_from_eventual_consistency(self):
        with patch.object(verify, 'check_hello', side_effect=[verify.VerificationError('not ready'), 'ok']):
            self.assertEqual(verify.retry(lambda: verify.check_hello('http://test'), interval=0), 'ok')

    def test_zero_or_empty_metric_is_not_success(self):
        import ujson
        for items in ([], [{'value': [1, '0']}], [{'value': [1, 'NaN']}],
                      [{'value': [1, '+Inf']}], [{'value': [1, '-Inf']}]):
            body = ujson.dumps({'status': 'success', 'data': {'resultType': 'vector', 'result': items}})
            with patch.object(verify, 'http_get', return_value=(body, {})):
                with self.assertRaises(verify.VerificationError):
                    verify.metric_value('http://test', 'nginx_up')

    def test_request_growth_records_measured_before_and_after(self):
        with patch.object(verify, 'metric_value', side_effect=[100, 105]), \
                patch.object(verify, 'check_hello') as hello:
            result = verify.check_request_growth('http://lab:30080', 'http://prom', 'marker', seconds=0)
        self.assertEqual(result, {'before': 100, 'after': 105, 'delta': 5, 'requests_sent': 5})
        self.assertEqual(hello.call_count, 5)
        self.assertTrue(all('verify=marker' in call.args[0] for call in hello.call_args_list))

    def test_old_counter_or_reset_does_not_confirm_new_traffic(self):
        for after in (100, 104, 0):
            with patch.object(verify, 'metric_value', side_effect=[100, after]), \
                    patch.object(verify, 'check_hello'):
                with self.assertRaises(verify.VerificationError):
                    verify.check_request_growth('http://lab', 'http://prom', 'marker', seconds=0)

    def test_missing_or_unhealthy_prometheus_rule_is_rejected(self):
        import ujson
        names = ('NginxUnavailable', 'GatewayControllerUnavailable',
                 'lab:nginx_requests_per_second:rate5m', 'lab:gateway_controller_memory_bytes')
        rules = [{'name': name, 'health': 'ok', 'lastError': ''} for name in names]
        for variant in (rules[:-1], [dict(rule, health='err') for rule in rules],
                        [dict(rule, lastError='evaluation failed') for rule in rules], rules):
            body = ujson.dumps({'status': 'success', 'data': {'groups': [{'rules': variant}]}})
            with patch.object(verify, 'http_get', return_value=(body, {})):
                if variant == rules:
                    self.assertEqual(set(verify.check_prometheus_rules('http://prom')), set(names))
                else:
                    with self.assertRaises(verify.VerificationError):
                        verify.check_prometheus_rules('http://prom')

    def test_rewrite_must_have_correct_route_header(self):
        with patch.object(verify, 'http_get', return_value=('{"service":"devops-lab"}', {})):
            with self.assertRaises(verify.VerificationError):
                verify.check_gateway_features('http://lab:30080')

    def test_redirect_cannot_leak_to_another_host_or_listener_port(self):
        for location in ('http://other:30080/api/info', 'http://lab/api/info', '/wrong', ''):
            with patch.object(verify, 'http_get', side_effect=[
                    ('{"service":"devops-lab"}', {'X-DevOps-Route': 'api-rewrite'}),
                    ('', {'Location': location})]):
                with self.assertRaises(verify.VerificationError):
                    verify.check_gateway_features('http://lab:30080')

    def test_redirect_is_checked_without_automatic_following(self):
        with patch.object(verify, 'http_get', side_effect=[
                ('{"service":"devops-lab"}', {'X-DevOps-Route': 'api-rewrite'}),
                ('', {'Location': 'http://lab:30080/api/info'})]) as request:
            self.assertIn('rewrite', verify.check_gateway_features('http://lab:30080'))
        self.assertEqual(request.call_args.kwargs, {'expected': 302, 'follow_redirects': False})


if __name__ == '__main__':
    unittest.main()
