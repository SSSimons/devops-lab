import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('publication', ROOT/'scripts/check-publication.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicationTests(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory)
        subprocess.run(['git','init','-q','-b','main',str(root)],check=True)
        for name in ('.gitignore','.gitattributes'):
            shutil.copy2(ROOT/name,root/name)
        (root/'docs').mkdir()
        (root/'README.md').write_text('Public project\n')
        (root/'deploy.sh').write_bytes(b'#!/bin/bash\r\necho hello\r\n')
        (root/'docs/passport.pdf').write_bytes(b'%PDF fixture')
        return root

    def stage(self,root,*args):
        subprocess.run(['git','-C',str(root),'add',*args],check=True,capture_output=True)

    def test_ignore_rules_keep_local_data_out_and_preserve_deliverables(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            for name in ('.state/private.json','.venv/config','submission/result.zip',
                         '.env.local','admin.conf','tls.key','diagnostics.log'):
                path=root/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('local')
            (root/'docs/passport.docx').write_bytes(b'PK fixture')
            self.stage(root,'.')
            names,problems=MODULE.check(root)
            self.assertFalse(problems)
            self.assertIn('docs/passport.docx',names)
            self.assertNotIn('admin.conf',names)
            self.assertNotIn('.env.local',names)
            blob=subprocess.check_output(['git','-C',str(root),'show',':deploy.sh'])
            self.assertNotIn(b'\r\n',blob)

    def test_already_tracked_sensitive_path_is_blocked_despite_gitignore(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            (root/'admin.conf').write_text('private config')
            self.stage(root,'.')
            self.stage(root,'-f','admin.conf')
            _,problems=MODULE.check(root)
            self.assertTrue(any(name=='admin.conf' for name,_ in problems))

    def test_scan_reads_staged_blob_and_does_not_print_token_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            token='gh'+'p_'+'A'*36
            (root/'credentials.txt').write_text(token)
            self.stage(root,'.')
            (root/'credentials.txt').write_text('working tree is now clean')
            result=subprocess.run(['python3',str(ROOT/'scripts/check-publication.py'),'--root',str(root)],
                                  capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('токен GitHub',result.stderr)
            self.assertNotIn(token,result.stderr+result.stdout)

    def test_symlink_is_not_followed_or_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            (root/'external-link').symlink_to('/etc/hostname')
            self.stage(root,'.')
            _,problems=MODULE.check(root)
            self.assertTrue(any(name=='external-link' and 'символическую ссылку' in reason for name,reason in problems))


if __name__=='__main__':
    unittest.main()
