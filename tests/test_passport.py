import importlib.util
import ujson
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location('passport_content',ROOT/'scripts/passport_content.py')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PassportTests(unittest.TestCase):
    def full_report(self):
        return {'status':'passed','gateway_features':{'rewrite':True},'cats':{'ok':True},
                'metrics':{'nginx_up':1},'logs':{'access':True,'error':True},
                'traffic':{'before':100,'after':105,'delta':5,'requests_sent':5},
                'prometheus_rules':{name:'ok' for name in (
                    'NginxUnavailable','GatewayControllerUnavailable',
                    'lab:nginx_requests_per_second:rate5m','lab:gateway_controller_memory_bytes')},
                'time_utc':'2026-10-03T08:00:00Z'}

    def fixture(self,directory):
        root=Path(directory)
        (root/'config').mkdir()
        (root/'config/versions.json').write_bytes((ROOT/'config/versions.json').read_bytes())
        (root/'.state').mkdir()
        return root

    def page_text(self,root):
        return '\n'.join(str(value) for page in MODULE.build_pages(root) for _,value in page)

    def test_old_smoke_without_cats_and_bad_log_shape_are_not_current_proof(self):
        report=self.full_report()
        self.assertTrue(MODULE.current_smoke(report))
        report.pop('cats')
        self.assertFalse(MODULE.current_smoke(report))
        report=self.full_report()
        report['logs']=None
        self.assertFalse(MODULE.current_smoke(report))

    def test_old_metrics_without_growth_and_rules_are_not_current_proof(self):
        report=self.full_report()
        report.pop('traffic')
        self.assertFalse(MODULE.current_smoke(report))
        report=self.full_report()
        report['prometheus_rules']['NginxUnavailable']='err'
        self.assertFalse(MODULE.current_smoke(report))

    def test_invalid_or_inconsistent_growth_is_not_current_proof(self):
        for change in ({'delta':float('inf')}, {'delta':float('nan')},
                       {'requests_sent':True}, {'before':-1}, {'after':106},
                       {'before':None}, {'delta':'5'}):
            with self.subTest(change=change):
                report=self.full_report()
                report['traffic'].update(change)
                self.assertFalse(MODULE.current_smoke(report))

    def test_failed_report_does_not_claim_current_full_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            (root/'.state/verification.json').write_text(ujson.dumps({'status':'failed'}))
            text=self.page_text(root)
            self.assertIn('завершилась ошибкой',text)
            self.assertNotIn('Полная автоматическая проверка прошла',text)

    def test_repeat_claim_requires_both_current_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            (root/'.state/first-deploy.json').write_text(ujson.dumps(self.full_report()))
            (root/'.state/repeat-deploy.json').write_text(ujson.dumps({'status':'failed'}))
            self.assertNotIn('подтверждены актуальными отчётами',self.page_text(root))
            (root/'.state/repeat-deploy.json').write_text(ujson.dumps(self.full_report()))
            self.assertIn('подтверждены актуальными отчётами',self.page_text(root))
