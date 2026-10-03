# Как объяснить и обслуживать решение

## Связь с твоим опытом

Python здесь решает знакомую задачу автоматизации: запускает kubectl, делает HTTP/API
запросы, разбирает JSON и сохраняет отчёт. В отличие от Python backend разработка
HTTP приложения не нужна - nginx уже даёт проверяемый ответ и access/error-логи.
Docker используется в CI для проверки конфигураций и временного kind-кластера;
на основной VM контейнеры запускает containerd, а не Docker Compose.

| Инструмент | Что сказать на защите |
|---|---|
| kubeadm | Инициализирует Kubernetes control plane; после этого workloads управляет Kubernetes |
| kubelet | Агент на узле, поддерживает Pod в нужном состоянии через containerd |
| kubectl | CLI к Kubernetes API; apply декларативно создаёт/обновляет ресурсы |
| Flannel | Создаёт сеть Pod; без CNI узел остаётся NotReady и Pod не имеют нормальной сети |
| GatewayClass | Указывает, какой контроллер реализует Gateway API |
| Gateway | Описывает listener; NGF создаёт NGINX data plane и NodePort Service |
| HTTPRoute | Направляет HTTP к Service и добавляет заголовок подтверждения маршрута |
| Service | Стабильный сетевой адрес и выбор backend Pod через labels |
| Prometheus | Периодически получает /metrics и хранит временные ряды |
| nginx exporter | Переводит stub_status nginx в формат метрик Prometheus |
| Fluentd | Читает логи nginx, добавляет source и записывает их в отдельные JSON файлы |
| StatefulSet | Стабильное имя Pod и последовательное обновление; одна копия не конкурирует за file buffer |
| hostPath | Сохраняет данные на диске VM; не подходит для свободного переноса Pod между узлами |

## Для чего нужен .github/workflows/ci.yml

Файл `ci.yml` содержит инструкцию для GitHub Actions:
при push в main, pull request или ручном запуске GitHub проверяет код и конфиги,
создаёт временный kind-кластер и дважды разворачивает в нём стек. Результат виден
во вкладке Actions. Этот файл не запускает установку в твоём WSL и не заменяет
`sudo bash deploy.sh`; он проверяет репозиторий на отдельной машине GitHub.

## Быстрая демонстрация

```bash
kubectl get nodes -o wide
kubectl -n devops-lab get pods
/var/lib/devops-lab/venv/bin/python3 scripts/verify.py
cat .state/verification.json
```

Объяснить: проверка сначала убеждается, что контроллер принял текущую конфигурацию,
потом отправляет HTTP запрос, проверяет ответ/заголовок, метрики и записи Fluentd.
У каждого запроса уникальная метка: старые логи не могут дать ложное подтверждение.

## Частые проблемы

| Симптом | С чего начать |
|---|---|
| connection refused на 6443 после запуска WSL | `sudo bash scripts/diagnose-host.sh`; при swap в журнале kubelet - [RECOVERY.md](RECOVERY.md) |
| Node NotReady | `kubectl -n kube-flannel get pods`; `journalctl -u kubelet -u containerd` |
| Pod Pending | `kubectl -n devops-lab describe pod web-0`: taints, RAM, CPU |
| ImagePullBackOff | describe Pod: image name, сеть registry, временный rate limit Docker Hub |
| Gateway Programmed=False | describe Gateway, HTTPRoute; logs deployment/nginx-gateway |
| HTTP 502/503 | Service endpoints и readiness web Pod |
| `up{job="nginx"}=0` | источники метрик Prometheus, exporter logs, Service nginx-exporter |
| `nginx_up=0` при `up=1` | Exporter доступен, но не может прочитать `/stub_status` nginx |
| Нет собранных логов | Fluentd служебный лог, `/logs`, `/collected`, права и file buffer |
| Permission denied на данных | Init containers, владельцы hostPath: 1000 для логов, 65534 для TSDB |
| kubectl обращается к чужому кластеру | Проверить `kubectl config current-context` и KUBECONFIG |

## Развитие

Сначала подтвердить базовый стенд на Ubuntu VM. Затем можно добавить TLS/cert-manager,
централизованное хранилище логов, Node Exporter/kube-state-metrics, Grafana,
Alertmanager и резервные копии. Для нескольких узлов заменить hostPath на PVC,
перенести Fluentd в агентный/централизованный сбор и выбрать CNI с NetworkPolicy.
Для телеком-сценария добавить blackbox probes внешних HTTP/TCP endpoint, latency/availability
SLO и отдельные среды для тестирования изменений сетевых маршрутов.
