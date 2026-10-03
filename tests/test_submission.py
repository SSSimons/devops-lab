import importlib.util
from pathlib import Path
import tempfile
import unittest
from pypdf import PdfWriter

spec = importlib.util.spec_from_file_location('submission', Path(__file__).parents[1] / 'scripts/make_submission.py')
submission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submission)


class SubmissionTests(unittest.TestCase):
    def test_passport_limits_and_invalid_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'passport.pdf'
            for pages in (0, 3, 5):
                writer = PdfWriter()
                for _ in range(pages):
                    writer.add_blank_page(width=595, height=842)
                writer.write(path)
                if pages == 3:
                    submission.validate_passport(path)
                else:
                    with self.assertRaises(ValueError):
                        submission.validate_passport(path)
            path.write_bytes(b'not a PDF')
            with self.assertRaises(ValueError):
                submission.validate_passport(path)

    def test_encrypted_passport_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'passport.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            writer.encrypt('secret')
            writer.write(path)
            with self.assertRaises(ValueError):
                submission.validate_passport(path)

    def test_valid_main_link(self):
        self.assertEqual(submission.validate_repo('https://github.com/user/lab/tree/main/'),
                         'https://github.com/user/lab/tree/main')

    def test_credentials_wrong_branch_and_host_rejected(self):
        for url in ('https://token@github.com/user/lab/tree/main',
                    'https://github.com/user/lab/tree/dev',
                    'https://github.com/user/lab/tree/main?token=secret',
                    'https://github.com.evil.example/user/lab/tree/main'):
            with self.assertRaises(ValueError):
                submission.validate_repo(url)
