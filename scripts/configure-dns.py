#!/usr/bin/env python3
"""Подготовить DNS-файл для kubelet без loopback-адресов и при необходимости обновить его конфиг."""
import argparse
import ipaddress
import ujson
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


def resolver_text(text):
    servers = []
    other = []
    for line in text.splitlines():
        parts = re.split(r'[#;]', line, maxsplit=1)[0].split()
        if not parts:
            continue
        if parts[0] == 'nameserver' and len(parts) == 2:
            try:
                address = ipaddress.ip_address(parts[1])
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified or address.is_multicast or '%' in parts[1]:
                continue
            if str(address) not in servers:
                servers.append(str(address))
        elif parts[0] in ('search', 'domain', 'options'):
            other.append(' '.join(parts))
    if not servers:
        raise ValueError('В DNS-файле нет подходящих DNS-адресов вне loopback')
    # Для libc и kubelet оставляем не более трёх DNS-серверов.
    return '# Managed by devops-lab; refresh by running deploy.sh\n' + ''.join(
        'nameserver ' + server + '\n' for server in servers[:3]) + ''.join(line + '\n' for line in other)


def choose_resolver(sources):
    for path in sources:
        try:
            return resolver_text(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
    raise ValueError('В исходных файлах нет DNS-серверов. Настроить DNS на узле и повторить запуск. Публичные DNS автоматически не подставляются.')


def kubelet_text(text, resolver_path):
    if not re.search(r'^kind:\s*KubeletConfiguration\s*$', text, re.M):
        raise ValueError('Ожидался KubeletConfiguration, созданный kubeadm')
    pattern = r'^resolvConf:[^\n]*'
    matches = re.findall(pattern, text, re.M)
    if len(matches) > 1:
        raise ValueError('Найдены повторяющиеся ключи resolvConf, конфиг kubelet не изменён')
    line = 'resolvConf: ' + ujson.dumps(str(resolver_path), escape_forward_slashes=False)
    if matches:
        return re.sub(pattern, line, text, count=1, flags=re.M)
    return text.rstrip() + '\n' + line + '\n'


def write_atomic(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(text)
    try:
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def configure(output, sources, kubelet_config=None):
    if output.is_symlink():
        raise ValueError('Создаваемый DNS-файл не должен быть символической ссылкой')
    resolver = choose_resolver(sources)
    config = original = None
    if kubelet_config is not None:
        original = kubelet_config.read_text(encoding='utf-8')
        config = kubelet_text(original, output)
    # Проверяем все исходные данные до изменения файлов.
    changed = not output.exists() or output.read_text(encoding='utf-8') != resolver
    if changed:
        write_atomic(output, resolver)
    if config is not None and config != original:
        backup = output.parent / 'kubelet-config.before-dns'
        if not backup.exists():
            shutil.copy2(kubelet_config, backup)
        write_atomic(kubelet_config, config, stat.S_IMODE(kubelet_config.stat().st_mode))
        changed = True
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, action='append', help='Указать исходный DNS-файл; параметр можно повторить для запасного варианта')
    parser.add_argument('--kubelet-config', type=Path)
    args = parser.parse_args()
    sources = args.source or [Path('/run/systemd/resolve/resolv.conf'), Path('/etc/resolv.conf'), args.output]
    try:
        changed = configure(args.output, sources, args.kubelet_config)
    except (ValueError, OSError) as error:
        print(f'ERROR: настройка DNS kubelet: {error}', file=sys.stderr)
        return 1
    print('changed' if changed else 'unchanged')
    return 0


if __name__ == '__main__':
    sys.exit(main())
