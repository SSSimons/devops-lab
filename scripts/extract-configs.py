#!/usr/bin/env python3
"""Извлечь конфигурации из ConfigMap для локальной проверки nginx, Prometheus и Fluentd."""
from pathlib import Path
import sys
import yaml

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[1] if len(sys.argv) > 1 else root / '.state/configs')
output.mkdir(parents=True, exist_ok=True)
for name in ('10-app.yaml', '30-monitoring.yaml'):
    for item in yaml.safe_load_all((root / 'k8s' / name).read_text()):
        if item and item['kind'] == 'ConfigMap':
            for filename, value in item['data'].items():
                (output / filename).write_text(value)
print(output.resolve())
