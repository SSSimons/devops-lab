#!/usr/bin/env python3
"""Настроить новый конфиг containerd v2/v3 с помощью tomllib и ujson."""
import argparse
import ujson
from pathlib import Path
import re
import sys
import tomllib


def table_path(header):
    """Разбираем имена плагинов через TOML, чтобы точки в кавычках не делили имя на части."""
    data = tomllib.loads(header + '\n__devops_probe__ = true\n')
    path = []
    while '__devops_probe__' not in data:
        if len(data) != 1:
            raise ValueError('Заголовок таблицы TOML не поддерживается')
        key, data = next(iter(data.items()))
        path.append(key)
    return tuple(path)


def set_option(text, section, key, value):
    lines = text.splitlines(keepends=True)
    start = end = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('['):
            continue
        if start is not None:
            end = index
            break
        if not stripped.startswith('[[') and table_path(stripped) == section:
            start = index + 1
    assignment = f'  {key} = {value}\n'
    if start is None:
        header = '.'.join(ujson.dumps(part, escape_forward_slashes=False) for part in section)
        return text.rstrip() + f'\n\n[{header}]\n' + assignment
    if end is None:
        end = len(lines)
    pattern = re.compile(r'^(\s*)' + re.escape(key) + r'\s*=')
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            lines[index] = f'{match.group(1)}{key} = {value}\n'
            return ''.join(lines)
    lines.insert(start, assignment)
    return ''.join(lines)


def configure(text, pause_image):
    if not re.fullmatch(r'registry\.k8s\.io/pause:[A-Za-z0-9_.-]+', pause_image):
        raise ValueError('От kubeadm ожидался ровно один образ registry.k8s.io/pause')
    data = tomllib.loads(text)
    version = data.get('version')
    if version == 2:
        runtime_plugin = 'io.containerd.grpc.v1.cri'
        image_section = ('plugins', runtime_plugin)
        image_key = 'sandbox_image'
        disabled = {'cri', runtime_plugin}
    elif version == 3:
        runtime_plugin = 'io.containerd.cri.v1.runtime'
        image_plugin = 'io.containerd.cri.v1.images'
        image_section = ('plugins', image_plugin, 'pinned_images')
        image_key = 'sandbox'
        disabled = {'cri', 'io.containerd.grpc.v1.cri', runtime_plugin, image_plugin}
        if image_plugin not in data.get('plugins', {}):
            raise ValueError('В конфиге containerd v3 нет плагина CRI images')
    else:
        raise ValueError(f'Версия конфига containerd не поддерживается: {version!r}; нужна 2 или 3')
    if disabled.intersection(data.get('disabled_plugins', [])):
        raise ValueError('CRI отключён в созданном конфиге containerd')
    runtime = data.get('plugins', {}).get(runtime_plugin, {}).get('containerd', {})
    if runtime.get('default_runtime_name') != 'runc':
        raise ValueError('В новом конфиге containerd runtime по умолчанию должен быть runc')
    if runtime.get('runtimes', {}).get('runc', {}).get('runtime_type') != 'io.containerd.runc.v2':
        raise ValueError('В новом конфиге containerd ожидался io.containerd.runc.v2')
    cgroup_section = ('plugins', runtime_plugin, 'containerd', 'runtimes', 'runc', 'options')
    result = set_option(text, cgroup_section, 'SystemdCgroup', 'true')
    result = set_option(result, image_section, image_key, ujson.dumps(pause_image, escape_forward_slashes=False))
    parsed = tomllib.loads(result)
    options = parsed['plugins'][runtime_plugin]['containerd']['runtimes']['runc']['options']
    image = parsed
    for part in image_section:
        image = image[part]
    if options['SystemdCgroup'] is not True or image[image_key] != pause_image:
        raise ValueError('Созданный конфиг containerd не прошёл проверку')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('pause_image')
    parser.add_argument('--check', action='store_true', help='Проверить настройки без изменения файла')
    args = parser.parse_args()
    try:
        original = args.config.read_text(encoding='utf-8')
        result = configure(original, args.pause_image)
        if args.check:
            if tomllib.loads(original) != tomllib.loads(result):
                raise ValueError('Runtime не сохранил нужные настройки cgroup и pause')
        else:
            temporary = args.config.with_name(args.config.name + '.tmp')
            temporary.write_text(result, encoding='utf-8')
            temporary.replace(args.config)
    except (ValueError, OSError) as error:
        print(f'ERROR: настройка containerd: {error}', file=sys.stderr)
        return 1
    action = 'Проверен' if args.check else 'Настроен'
    print(f'{action} containerd TOML v{tomllib.loads(result)["version"]}: systemd cgroups; {args.pause_image}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
