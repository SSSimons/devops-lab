**# DevOps стенд для MTC ENGINEER HACK**



[![Проверка проекта и развёртывания]\(https\://github.com/SSSimons/devops-lab/actions/workflows/ci.yml/badge.svg?branch=main)]\(https\://github.com/SSSimons/devops-lab/actions/workflows/ci.yml)



Сделал одноузловой Kubernetes на **\*\*Ubuntu 24.04 amd64\*\*** через **\*\*kubeadm\*\***.

nginx принимает запросы через **\*\*Gateway API\*\***, Prometheus собирает метрики,

Fluentd сохраняет access/error-логи nginx. Основной запуск: \`sudo bash deploy.sh\`.



**\*\*kubeadm · containerd · Flannel · NGINX Gateway Fabric · Prometheus · Fluentd · Python + ujson\*\***



📄 Паспорт решения: [PDF]\(docs/passport.pdf) · [Word]\(docs/passport.docx)



**## 📋 Что реализовано**



\- **\*\*Kubernetes через kubeadm:\*\*** подготовка Ubuntu, containerd, CNI и одноузлового кластера.

\- **\*\*Gateway API:\*\*** \`GatewayClass\`, \`Gateway\` и три \`HTTPRoute\`; маршрутизация, заголовки, URLRewrite и HTTP 302.

\- **\*\*Мониторинг:\*\*** три источника метрик Prometheus, два alert и два recording rules.

\- **\*\*Логирование:\*\*** Fluentd читает реальные файлы nginx и сохраняет JSON-записи на диске узла.

\- **\*\*Автоматизация:\*\*** deploy одной командой, повторный запуск и отчёты проверки на Python.

\- **\*\*CI:\*\*** проверки конфигураций и тестов, временный kind-кластер, два deploy и нагрузочная проверка ротации.

\- **\*\*🐈 \`/cats\`:\*\*** страница с котиком и подписью «Котик был доставлен через Kubernetes».



Для проверяющего: [запуск]\(#-первый-запуск) · [Gateway API]\(#-проверка-gateway-api) · [метрики]\(#-проверка-prometheus) · [логи]\(#-проверка-fluentd) · [критерии оценки]\(docs/CRITERIA.md).



**## 🏗️ Архитектура**



\`\`\`mermaid

flowchart TD

    U[Пользователь] --> G[NGINX Gateway Fabric]

    G --> S[Service web]

    S --> W[nginx]

    W --> F[Fluentd sidecar]

    F --> L[JSON файлы на диске узла]

    W --> E[nginx exporter]

    E --> P[Prometheus]

    G -->|метрики контроллера| P

\`\`\`



\`GatewayClass nginx\` выбирает контроллер NGF. \`Gateway lab\` создаёт NGINX data plane,

доступный через **\*\*NodePort 30080\*\***. \`HTTPRoute web\` направляет запрос в \`Service web:80\`,

затем в nginx на 8080. Заголовок \`X-DevOps-Lab: gateway-api\` подтверждает прохождение Gateway.



nginx и Fluentd работают в одном StatefulSet Pod. Общий \`/logs\` хранит исходные логи,

Fluentd читает их и записывает события в \`/collected\`. Логи и база Prometheus сохраняются

в \`/var/lib/devops-lab\` на диске узла. Это сбор файлов приложения через sidecar.



Обоснование инструментов, хранение данных и ограничения: [docs/ARCHITECTURE.md]\(docs/ARCHITECTURE.md).



**## 🛠️ Технологии и версии**



\| Компонент | Версия |

\|---|---|

\| Ubuntu | 24.04 LTS, amd64 |

\| Kubernetes, kubeadm, kubelet, kubectl | 1.35.9; deb 1.35.9-1.1 |

\| containerd | 1.7.x / 2.x; TOML v2/v3 |

\| Flannel CNI | 0.28.4; CNI plugin 1.9.1-flannel1 |

\| Gateway API | 1.6.1, standard CRDs |

\| NGINX Gateway Fabric | 2.7.2, open source |

\| nginx приложения | 1.28.0-alpine |

\| nginx exporter | 1.4.2 |

\| Prometheus | 3.6.0 |

\| Fluentd | 1.18.0-debian-1.0 |

\| BusyBox | 1.37.0 |

\| Python / ujson | 3.12 / 6.0.0 |



Пакеты Kubernetes удерживаются через \`apt hold\`. Манифесты зависимостей находятся в

\`vendor/\` и проверяются по SHA-256; образы используют конкретные теги. Python-зависимости

закреплены в requirements и устанавливаются в отдельный venv. Для установки нужны

доступные Ubuntu mirrors и публичные registries.



\<details>

\<summary>📌 Фактически проверенное окружение в WSL2\</summary>



\<!-- BEGIN OBSERVED ENVIRONMENT -->

Фактическое окружение:



\| Компонент | Фактическая версия |

\|---|---|

\| Ubuntu | \`Ubuntu 24.04.5 LTS\` |

\| Ядро Linux | \`6.18.40.1-microsoft-standard-WSL2\` |

\| Среда | \`WSL2\` |

\| Kubernetes | \`v1.35.9\` |

\| containerd | \`containerd://2.2.1\` |

\| kubeadm | \`v1.35.9\` |

\| Клиент kubectl | \`v1.35.9\` |

\| API-сервер Kubernetes | \`v1.35.9\` |

\| Python | \`3.12.3\` |

\| ujson | \`6.0.0\` |

\| Gateway API | \`v1.6.1\` |

\| nginx приложения | \`1.28.0-alpine\` |

\| NGINX Gateway Fabric | \`2.7.2\` |

\| nginx Gateway | \`2.7.2\` |

\| Flannel | \`v0.28.4\` |

\| Prometheus | \`v3.6.0\` |

\| nginx exporter | \`1.4.2\` |

\| Fluentd | \`v1.18.0-debian-1.0\` |

\| BusyBox | \`1.37.0\` |



Дата сбора: не измерено.

Точные версии пакетов и digest образов: \`config/tested-environment.json\`.

Закреплённые версии: \`config/versions.json\`; сбор данных не меняет манифесты и контрольные суммы зависимостей.

\<!-- END OBSERVED ENVIRONMENT -->



\</details>



**## 🚀 Первый запуск**



**### Что нужно подготовить**



\- **\*\*Отдельная VM Ubuntu 24.04 amd64\*\***, sudo и systemd. Стенд также проверен в WSL2.

\- Минимум **\*\*2 vCPU / 4 GB RAM / 10 GB свободного места\*\***; рекомендую 4 vCPU / 8 GB RAM / 30 GB диска.

\- Стабильный IPv4 узла. Сеть не должна пересекаться с \`10.244.0.0/16\` и \`10.96.0.0/12\`.

\- Доступ к Ubuntu mirrors, \`pkgs.k8s.io\`, \`registry.k8s.io\`, \`ghcr.io\` и Docker Hub.

\- TCP **\*\*30080\*\*** доступен с компьютера проверяющего; TCP **\*\*6443\*\*** нужен для удалённого kubectl.



Bootstrap меняет системные настройки: отключает swap, настраивает cgroups и forwarding,

устанавливает пакеты и создаёт control plane. Чужой Docker/containerd или Kubernetes

не перезаписывается. Для установки использовать выделенную среду.



**### Установка из репозитория**



\`\`\`bash

git clone https\://github.com/SSSimons/devops-lab.git

cd devops-lab

sudo bash deploy.sh

\`\`\`



Из архива: распаковать \`devops-lab-fixed.zip\`, перейти в \`devops-lab\` и выполнить ту же команду.

Первый запуск обычно занимает 10-20 минут: скрипт ждёт готовности CNI, CoreDNS,

Gateway Controller, приложения и мониторинга, затем проверяет весь стек.



**\*\*Ожидаемый результат:\*\*** сообщения \`[OK]\` и \`.state/verification.json\` со \`status: passed\`.

При ошибке скрипт завершается с ненулевым кодом.



\<details>

\<summary>Сетевые интерфейсы, kubeconfig и доступ из Windows\</summary>



При нескольких интерфейсах можно явно выбрать IPv4, уже назначенный узлу:



\`\`\`bash

sudo bash deploy.sh --node-ip 192.168.1.50

\`\`\`



Подставить реальный IP своей VM. Скрипт проверит адрес и выберет интерфейс для Flannel.

При смене IP уже созданный control plane автоматически не перенастраивается.



Для пользователя, вызвавшего sudo, kubeconfig копируется в \`\~/.kube/config\`, если файла

ещё нет. Существующий kubeconfig сохраняется. Для команд с административным config:



\`\`\`bash

sudo env KUBECONFIG=/etc/kubernetes/admin.conf kubectl get nodes

\`\`\`



Кubeconfig содержит административный доступ и не должен попадать в Git или архив сдачи.



В Windows при запуске через WSL2 открыть \`http\://localhost:30080/cats\`.

Если localhost недоступен, использовать текущий IPv4 \`eth0\` из \`ip -o -4 addr show dev eth0\`.

Для VM нужен доступный IP, проброс порта при NAT или SSH-туннель:



\`\`\`bash

ssh -L 30080:127.0.0.1:30080 USER\@VM_IP

\`\`\`



UFW скрипт не перенастраивает. При активном firewall заранее настроить Kubernetes/CNI

forwarding и NodePort; для публичной VM ограничить доступ внешними правилами.



\</details>



**### Повторный запуск**



\`\`\`bash

sudo bash deploy.sh

\`\`\`



Скрипт распознаёт свой кластер, проверяет версию и обновляет ресурсы через \`apply\`.

Второй \`kubeadm init\` не выполняется, логи и метрики сохраняются. Раздельные хеши конфигураций

обновляют только нужный StatefulSet: изменение \`/cats\` не перезапускает Prometheus.

Изменение \`config/versions.json\` само по себе не обновляет манифесты или Kubernetes.



**## ✅ Общая проверка**



\`\`\`bash

export PATH="/var/lib/devops-lab/venv/bin:$PATH"

python3 scripts/verify.py

\`\`\`



Проверка подтверждает текущие статусы Gateway, HTTP-ответы и заголовки, JSON, 404,

rewrite/redirect и \`/cats\`. Проверяет три живых источника Prometheus, прирост счётчика

после пяти запросов, четыре правила и access/error-записи Fluentd с уникальной меткой.

Результат: \`.state/verification.json\`; при сбое \`status: failed\` заменяет прошлый успех.



Два последовательных deploy с отдельными отчётами:



\`\`\`bash

sudo bash scripts/check-repeat.sh

\`\`\`



Ожидается успех **\*\*обоих\*\*** запусков. Отчёты: \`.state/first-deploy.json\` и

\`.state/repeat-deploy.json\`. Окружение и digest образов собрать через \`bash scripts/evidence.sh\`.

Локальные отчёты в \`.state/\` исключены из Git.



**## 🌐 Проверка Gateway API**



\`\`\`bash

kubectl get nodes -o wide

kubectl get gatewayclass nginx

kubectl -n devops-lab get pods,svc,gateway,httproute

kubectl -n devops-lab describe gateway lab

kubectl -n devops-lab describe httproute web api-info legacy-redirect



NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

curl -i "http\://$NODE_IP:30080/"

curl -i "http\://$NODE_IP:30080/api/info"

curl -i "http\://$NODE_IP:30080/legacy"

curl -i "http\://$NODE_IP:30080/does-not-exist"

\`\`\`



Ожидаемые статусы: \`GatewayClass Accepted=True\`, \`Gateway Accepted=True / Programmed=True\`,

у всех \`HTTPRoute\` - \`Accepted=True / ResolvedRefs=True\`. \`verify.py\` также проверяет

\`observedGeneration\`: старый успешный статус не подтверждает новую конфигурацию.



\| Адрес | Результат |

\|---|---|

\| \`/\` | HTTP 200, \`Hello World!\`, заголовок \`X-DevOps-Lab: gateway-api\` |

\| \`/info\` | JSON с \`service: devops-lab\` |

\| \`/api/info\` | Gateway переписывает путь в \`/info\`; заголовок \`X-DevOps-Route: api-rewrite\` |

\| \`/legacy\` | HTTP 302 на \`/api/info\` с внешним портом 30080 |

\| \`/does-not-exist\` | HTTP 404 и запись в error-логе nginx |

\| \`/cats\` | Страница с котиком; \`verify.py\` проверяет также заголовок Gateway и байты PNG |



URLRewrite и redirect выполняются ресурсами Gateway API. nginx самостоятельно

\`/api/info\` не обслуживает. Порт 30080 публикует Gateway data plane.



**## 📊 Проверка Prometheus**



В первом терминале открыть локальный доступ:



\`\`\`bash

kubectl -n devops-lab port-forward svc/prometheus 9090:9090 --address=127.0.0.1

\`\`\`



Во втором терминале:



\`\`\`bash

curl -fsS http\://127.0.0.1:9090/api/v1/targets

curl -fsSG http\://127.0.0.1:9090/api/v1/query --data-urlencode 'query=up'

curl -fsSG http\://127.0.0.1:9090/api/v1/query --data-urlencode 'query=nginx_up'

curl -fsSG http\://127.0.0.1:9090/api/v1/query --data-urlencode 'query=nginx_http_requests_total'

\`\`\`



Открыть [Prometheus]\(http\://127.0.0.1:9090) и страницу \`/targets\`. У jobs **\*\*prometheus\*\***,

**\*\*nginx\*\*** и **\*\*gateway-controller\*\*** ожидается \`health=up\`, \`up=1\`, \`nginx_up=1\`.

Счётчик \`nginx_http_requests_total\` растёт после запросов; интервал сбора - **\*\*10 секунд\*\***.

Для браузера вне VM использовать \`ssh -L 9090:127.0.0.1:9090 USER\@VM_IP\`.



Два alert отслеживают недоступность nginx/экспортера и Gateway Controller, включая

исчезнувшие серии. Два recording rules рассчитывают RPS nginx и память контроллера.

Правила видны в Prometheus, проверяются через \`promtool\` и \`verify.py\`.

Нулевой RPS без трафика нормален; уведомления через Alertmanager не настроены.

Prometheus и \`stub_status:8081\` не публикуются через Gateway.



**## 📝 Проверка Fluentd**



После определения \`NODE_IP\` в разделе Gateway выполнить:



\`\`\`bash

curl "http\://$NODE_IP:30080/?check=my-request-001"

curl "http\://$NODE_IP:30080/does-not-exist-my-request-001"

kubectl -n devops-lab exec web-0 -c fluentd -- sh -c 'grep -h "my-request-001" /collected/events\*.log'

\`\`\`



Через несколько секунд ожидаются JSON-записи \`source=nginx.access\` с URI, HTTP status

и request_time, а также \`source=nginx.error\` с отсутствующим путём.

Проверяется **\*\*собранный лог приложения\*\*** в \`/collected/events\*.log\`.

\`kubectl logs web-0 -c fluentd\` показывает служебный лог агента.



На узле файлы лежат в \`/var/lib/devops-lab/logs/\`. Position files и file buffer

сохраняются там же; используются встроенные плагины Fluentd.



**### 🔄 Ротация исходных логов**



\`/logs\` использует **\*\*emptyDir 128 MiB\*\***. Раз в секунду скрипт nginx проверяет оба лога:

при **\*\*8 MiB\*\*** или через **\*\*10 секунд\*\*** для непустого файла сохраняет старый inode,

атомарно заменяет активный файл и отправляет USR1. Хранит **\*\*четыре архива на каждый лог\*\***.

Fluentd дочитывает старые файлы и следит за inode; pos_file уплотняется раз в час.



Нагрузочная проверка через Gateway отправляет 24576 HTTP 404 запросов с длинным URI:



\`\`\`bash

python3 scripts/check-log-rotation.py --url "http\://$NODE_IP:30080"

cat .state/log-rotation.json

\`\`\`



Использовать Python стенда из раздела «Общая проверка». Ожидается объём меньше 128 MiB,

четыре архива обоих логов и ровно одна access/error-запись каждого request ID.

CronJob удаляет собранные файлы старше 7 полных суток; Prometheus хранит блоки TSDB

до 7 дней / 1 GB. Исходные логи в emptyDir исчезают при пересоздании Pod.



**## 🔒 Надёжность и безопасность**



\- Readiness/liveness probes, startupProbe Prometheus, requests и limits контейнеров.

\- Основные контейнеры приложения и мониторинга работают без root: read-only rootfs,

  \`drop ALL\`, seccomp, без ServiceAccount token. Init containers используют root для подготовки каталогов.

\- Fluentd buffer/position и Prometheus TSDB сохраняются на hostPath.

\- Bootstrap проверяет принадлежность кластера/runtime; автоматические reset и upgrade не выполняются.

\- \`.gitignore\` исключает kubeconfig, ключи, локальные окружения и отчёты.

  \`scripts/check-publication.py\` проверяет подготовленные файлы Git на запрещённые пути и распознаваемые секреты.



**## 🧪 CI и локальные проверки**



[GitHub Actions]\(https\://github.com/SSSimons/devops-lab/actions/workflows/ci.yml) проверяет

Python/Bash, схемы Kubernetes и CRD, конфигурации nginx/Fluentd/Prometheus и правила мониторинга.

Затем создаёт kind-кластер с Flannel, выполняет deploy дважды, проверяет приложение,

Gateway, метрики, логи и ротацию. Отчёты сохраняются в artifacts, временный кластер удаляется.



CI использует Kubernetes **\*\*1.35.8\*\***, основной стенд - **\*\*kubeadm 1.35.9\*\***.

CI проверяет стек в контейнерах; установку системных пакетов и bootstrap VM он не заменяет.



\<details>

\<summary>Запустить статические проверки и тесты локально\</summary>



\`\`\`bash

sudo apt-get install -y python3-venv shellcheck

python3 -m venv .venv

.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python scripts/validate.py

.venv/bin/python -m unittest discover -s tests -v

shellcheck deploy.sh scripts/\*.sh

\`\`\`



\</details>



**## 🔧 Ограничения и восстановление**



\- Одна VM, один control plane и один web Pod. Высокая доступность не заявлена.

\- hostPath привязан к узлу; при потере VM теряются данные. Для нескольких узлов нужны PVC и сетевое хранилище.

\- Flannel не обеспечивает NetworkPolicy. HTTP работает без TLS и авторизации.

\- Размер логов может превысить порог между проверками. При резком потоке или отставании

  Fluentd возможны eviction и потеря событий из удалённых архивов. Ротация не гарантирует бесконечную нагрузку без потерь.

\- Retention Prometheus ограничивает блоки TSDB; WAL и активный head требуют дополнительного места.



При ошибке:



\`\`\`bash

sudo env KUBECONFIG=/etc/kubernetes/admin.conf bash scripts/diagnose.sh

\`\`\`



Если API недоступен: \`sudo bash scripts/diagnose-host.sh\`.

В WSL повторно включённый swap, смена IP и пустой DNS-файл могут нарушить работу кластера.



## 🚧 Развитие проекта

Сначала - проверить полный цикл установки и повторного запуска на отдельной Ubuntu VM.
После этого проект можно развивать по следующим направлениям:

- **HTTPS:** добавить TLS через Gateway API и автоматическое обновление сертификатов через cert-manager.
- **Мониторинг:** подключить Node Exporter и kube-state-metrics, добавить дашборды Grafana и уведомления через Alertmanager.
- **Логи:** подключить централизованное хранилище и сбор логов со всех узлов.
- **Хранение и восстановление:** перейти с hostPath на PVC с сетевым хранилищем, настроить резервные копии и проверить восстановление данных.
- **Несколько узлов и безопасность:** расширить кластер и выбрать CNI с поддержкой NetworkPolicy.
- **Телеком-сценарий:** добавить внешние HTTP/TCP-проверки, целевые показатели доступности и задержки, отдельную среду для проверки изменений маршрутов.

Подробности эксплуатации и развития: [docs/OPERATIONS.md](docs/OPERATIONS.md).

**## 📁 Файлы и документация**



\| Путь | Назначение |

\|---|---|

\| \`deploy.sh\` | Подготовка системы, установка компонентов и проверка |

\| \`k8s/\`, \`vendor/\` | Ресурсы проекта и закреплённые зависимости с SHA-256 |

\| \`scripts/bootstrap.sh\`, \`scripts/deploy-stack.sh\` | Подготовка Ubuntu и развёртывание стека |

\| \`scripts/verify.py\`, \`scripts/check-repeat.sh\` | Сквозная проверка и два последовательных deploy |

\| \`scripts/nginx-run.sh\`, \`scripts/check-log-rotation.py\` | Ротация и проверка полноты собранных логов |

\| \`scripts/collect-versions.py\`, \`scripts/evidence.sh\` | Фактические версии и результаты проверки |

\| \`scripts/diagnose.sh\`, \`scripts/diagnose-host.sh\` | Диагностика стека и узла без API |

\| \`scripts/validate.py\`, \`tests/\`, \`.github/workflows/ci.yml\` | Статические проверки, тесты и CI |

\| \`docs/ARCHITECTURE.md\` | Архитектура |

\| \`docs/passport.pdf\`, \`docs/passport.docx\` | Паспорт решения |



**## 📚 Первичные источники**



\- [Установка kubeadm]\(https\://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)

\- [Container runtimes и systemd cgroups]\(https\://kubernetes.io/docs/setup/production-environment/container-runtimes/)

\- [NGF версии и совместимость]\(https\://github.com/nginx/nginx-gateway-fabric/tree/v2.7.2)

\- [Установка NGF manifests]\(https\://docs.nginx.com/nginx-gateway-fabric/install/manifests/open-source/)

\- [Gateway API]\(https\://gateway-api.sigs.k8s.io/)

\- [Prometheus configuration]\(https\://prometheus.io/docs/prometheus/latest/configuration/configuration/)

\- [Fluentd tail]\(https\://docs.fluentd.org/input/tail) и [file output]\(https\://docs.fluentd.org/output/file)

\- [Flannel]\(https\://github.com/flannel-io/flannel/tree/v0.28.4)
