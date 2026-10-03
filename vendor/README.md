# Источники внешних конфигураций

Файлы сохранены из указанных тегов версий. deploy-stack.sh применяет локальные копии;
GitHub нужен для публикации исходников, а не для скачивания manifests при каждом запуске.
SHA256SUMS проверяет YAML и JSON в этой директории. Лицензии источников - в `licenses/`.

| Локальный файл | Источник |
|---|---|
| `flannel.yaml` | https://raw.githubusercontent.com/flannel-io/flannel/v0.28.4/Documentation/kube-flannel.yml |
| `nginx-gateway.yaml` | https://raw.githubusercontent.com/nginx/nginx-gateway-fabric/v2.7.2/deploy/nodeport/deploy.yaml |
| `nginx-crds.yaml` | https://raw.githubusercontent.com/nginx/nginx-gateway-fabric/v2.7.2/deploy/crds.yaml |
| `gateway-*.yaml` | https://github.com/kubernetes-sigs/gateway-api/tree/v1.6.1/config/crd/standard |
| `kubernetes-openapi.json` | https://raw.githubusercontent.com/kubernetes/kubernetes/v1.35.9/api/openapi-spec/swagger.json |

Изменения относительно исходного манифеста NGF NodePort:

- В NginxProxy добавлен `nodePorts: [{listenerPort: 80, port: 30080}]`.
- `spec.template.metadata.annotations: null` у certificate Job заменён пустым
  `spec.template.metadata: {}` для строгой проверки OpenAPI (поведение не изменяется).

Остальные YAML файлы сохранены без изменений. схема OpenAPI хранится в исходном виде;
validate.py адаптирует только тип Kubernetes IntOrString к JSON Schema union integer/string.
Проверка схемы CRD не исполняет CEL и не заменяет Kubernetes admission:
полная проверка реального применения выполняется при развёртывании/CI.

При обновлении зависимостей:

1. Выбрать совместимые версии выпусков в официальной таблице NGF.
2. Заменить manifests/CRDs, сохранив фиксированный NodePort в NginxProxy.
3. Обновить версии во всех собственных manifests, bootstrap, CI, versions.json и документации.
4. Пересчитать SHA256SUMS и выполнить validate.py, CI и установку на свежей Ubuntu VM.

```bash
sha256sum vendor/*.yaml vendor/*.json > vendor/SHA256SUMS
```
