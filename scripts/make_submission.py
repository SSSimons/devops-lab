#!/usr/bin/env python3
"""Собрать архив для сдачи: Ссылка.txt + Паспорт.pdf."""
import argparse
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def validate_repo(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query
            or parsed.fragment or not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/tree/main/?', parsed.path)):
        raise ValueError('Указать https://github.com/OWNER/REPO/tree/main без логина в URL и параметров запроса.')
    return url.rstrip('/')


def validate_passport(path):
    if not path.exists() or path.stat().st_size > 15_000_000:
        raise ValueError('Нужен docs/passport.pdf размером не более 15 MB.')
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ValueError('Установить requirements-dev.txt в .venv и выполнить .venv/bin/python scripts/make_submission.py.') from error
    try:
        reader = PdfReader(path)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 4:
            raise ValueError('Паспорт должен содержать от 1 до 4 страниц, без шифрования.')
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('Паспорт не удалось прочитать как PDF.') from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='Публичная ссылка на ветку main в GitHub')
    parser.add_argument('--surname', required=True, help='Фамилия в точности как при регистрации')
    parser.add_argument('--check-public', action='store_true', help='Проверить доступ к README в main без авторизации')
    args = parser.parse_args()
    repo = validate_repo(args.repo)
    if not re.fullmatch(r'[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё -]{0,80}', args.surname):
        raise ValueError('Фамилия может содержать только буквы, пробелы и дефисы.')
    passport = ROOT / 'docs/passport.pdf'
    validate_passport(passport)
    if args.check_public:
        parts = urlparse(repo).path.split('/')
        raw = f'https://raw.githubusercontent.com/{parts[1]}/{parts[2]}/main/README.md'
        with urlopen(raw, timeout=20) as response:
            if response.status != 200 or not response.read(1):
                raise ValueError('README в main недоступен без авторизации.')
    output = ROOT / 'submission' / (args.surname + '.zip')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('Ссылка.txt', repo + '\n')
        archive.write(passport, 'Паспорт.pdf')
    if output.stat().st_size > 18_000_000:
        output.unlink()
        raise ValueError('Размер архива превышает 18 MB.')
    print(output)
    print('Перед сдачей выполнить scripts/evidence.sh на Ubuntu 24.04 и проверить публичный git clone по README.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        sys.exit(str(error))
