import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import unittest
import ujson


class PrometheusDiagnoseTests(unittest.TestCase):
    def test_real_http_diagnosis_reports_all_targets_and_returns_failure_for_down(self):
        for healthy in (False, True):
            targets = [{'labels': {'job': job}, 'health': 'up', 'lastError': '',
                        'scrapeUrl': f'http://{job}:9113/metrics', 'lastScrape': '2026-10-03T16:00:00Z'}
                       for job in ('prometheus', 'nginx', 'gateway-controller')]
            if not healthy:
                targets[-1].update(health='down', lastError='dial tcp: connection refused')
            body = ujson.dumps({'status': 'success', 'data': {'activeTargets': targets}}).encode()

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                script = Path(__file__).parents[1] / 'scripts/diagnose-prometheus.py'
                result = subprocess.run([sys.executable, str(script), '--url',
                                         f'http://127.0.0.1:{server.server_port}'],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0 if healthy else 1, result.stderr)
                for job in ('prometheus', 'nginx', 'gateway-controller'):
                    self.assertIn(f'job={job}', result.stdout)
                if not healthy:
                    self.assertIn('connection refused', result.stdout)
                    self.assertIn('gateway-controller: health=down', result.stderr)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
