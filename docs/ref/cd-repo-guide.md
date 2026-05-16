# CD Repo 생성 가이드라인

본 문서는 본 repo와 별개로 CD(Continuous Deployment) repo를 신설할 때의 외부 통합 관점 가이드라인. 본 repo 코드 영역 밖이며, 본 repo의 release artifact + contract 문서를 받아 prod 운영하는 외부 인프라 repo가 따라야 할 ref.

본 문서가 위치한 `docs/ref/`는 본 repo 안 다른 문서·코드에서 인용하지 않는다 — 외부 통합 시 참고만 (CLAUDE.md 정책).

## 1. CD repo 책임 범위

| 영역 | 본 repo (CI) | CD repo |
|------|-------------|---------|
| 코드 quality·테스트 | 책임 | 무관 |
| Python wheel 빌드 | 책임 (release.yml) | 받음만 |
| schema migration 자동화 | wheel 동봉 | `alembic upgrade head` 실행 |
| 환경변수 카탈로그 정의 | 책임 (`env.md`) | 운영 값 채워서 inject |
| secret 주입 채널 | contract만 (`prod-contract.md`) | 책임 — Vault·EnvironmentFile 등 자유 |
| VM provisioning·서브넷·SG | 무관 | 책임 |
| systemd unit·배포 자동화 | reference 예시만 (`deployment.md` 4절) | 책임 — Ansible·SaltStack·k8s 자유 |
| TLS·외부 ingress·도메인 | 무관 | 책임 |
| log aggregator·Prometheus scrape | endpoint 제공 (`/metrics`·`LOG_FORMAT=json`) | 책임 — Loki·ELK·CloudWatch 자유 |
| 헬스체크 외부 모니터링 | endpoint 제공 (`/health`) | 책임 — systemd watchdog·외부 알림 |
| 롤백·blue-green·canary | 무관 | 책임 |

본 repo는 wheel + contract만 책임. CD repo는 그 contract만 충족하면 어떤 도구·OS·secret 채널이든 자유.

## 2. CD repo 디렉토리 구조 (추천)

도구별 추천 패턴:

### 2.1. Ansible 기반 (가장 단순)

```
cd-assessment-engine/
├── ansible.cfg
├── inventory/
│   ├── group_vars/
│   │   └── all/
│   │       └── shared.yml          ← 공통 환경변수 (env.md 카탈로그 기반)
│   └── host_vars/
│       ├── web-01.yml              ← web 노드 한정 (deployment.md 4절 multi-node)
│       ├── consumer-01.yml
│       ├── diagnostic-worker-01.yml
│       └── diagnostic-scheduler-01.yml
├── playbooks/
│   ├── deploy.yml                  ← release artifact 다운로드 + install + systemd
│   ├── migrate.yml                 ← alembic upgrade head 1회 실행
│   ├── rollback.yml                ← 이전 version 복원
│   └── health-check.yml            ← 배포 후 verification
├── roles/
│   ├── prereq/                     ← Python 3.12 install·venv 디렉토리
│   ├── artifact-fetch/             ← gh release download + sha256 + sigstore 검증
│   ├── env-inject/                 ← /etc/assessment-engine/*.env 작성
│   ├── alembic/                    ← schema 마이그레이션
│   ├── systemd-unit/               ← unit file template + enable·start
│   └── monitoring/                 ← Prometheus scrape config·log shipper
├── templates/
│   ├── assessment-engine-web.service.j2
│   ├── assessment-engine-consumer.service.j2
│   ├── assessment-engine-diagnostic-worker.service.j2
│   ├── assessment-engine-diagnostic-scheduler.service.j2
│   ├── shared.env.j2
│   └── <component>.env.j2
├── vault/                          ← Ansible Vault 암호화 secret
│   └── secrets.yml
└── docs/
    ├── runbook.md                  ← prod 운영 절차·인시던트 대응
    └── secret-policy.md            ← Vault 키 회전 정책
```

### 2.2. Terraform + Ansible (혼합)

```
cd-assessment-engine/
├── terraform/                      ← VM provisioning·서브넷·SG (인프라 layer)
│   ├── main.tf
│   ├── variables.tf
│   └── modules/
└── ansible/                        ← OS 측 install·운영 (configuration layer)
    └── (위 2.1과 동일)
```

### 2.3. k8s manifest (컨테이너 운영 결정 시)

본 repo는 wheel 산출물이라 k8s 운영 시 CD repo가 자체 Dockerfile 작성 (wheel install + entrypoint). 단, ADR 0012는 "registry push 별도 결정"이라 인프라 측 자유.

```
cd-assessment-engine/
├── Dockerfile                      ← wheel install + 4 컴포넌트 entrypoint 분기
├── manifests/
│   ├── web-deployment.yaml
│   ├── consumer-deployment.yaml
│   ├── diagnostic-worker-deployment.yaml
│   ├── diagnostic-scheduler-deployment.yaml
│   ├── postgres-statefulset.yaml   ← (또는 외부 매니지드)
│   ├── rabbitmq-statefulset.yaml
│   ├── redis-statefulset.yaml
│   └── ingress.yaml
└── helm/                           ← chart 추상화 (선택)
```

## 3. 핵심 워크플로 (도구 무관)

### 3.1. release artifact 다운로드 + 검증

```bash
# 본 repo의 GitHub Release에서 모든 artifact fetch
gh release download v1.2.3 --repo <owner>/assessment-engine \
  --pattern '*.whl' \
  --pattern '*.tar.gz' \
  --pattern 'SHA256SUMS' \
  --pattern '*.sigstore' \
  --pattern 'sbom.cdx.json' \
  --dir /tmp/release

# 무결성 검증 (의무)
cd /tmp/release && sha256sum -c SHA256SUMS

# Sigstore signature 검증 (의무)
for whl in *.whl; do
  cosign verify-blob \
    --signature "${whl}.sigstore" \
    --certificate-identity-regexp 'https://github.com/<owner>/assessment-engine/.+' \
    --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
    "$whl"
done

# SBOM 분석 (선택 — license·CVE audit)
syft attest --output cyclonedx-json sbom.cdx.json | grype
```

### 3.2. wheel install

```bash
# venv 격리 (권장) 또는 system Python 직접
python3.12 -m venv /opt/assessment-engine
/opt/assessment-engine/bin/pip install /tmp/release/assessment_engine-1.2.3-py3-none-any.whl
```

### 3.3. 환경변수 inject

본 repo의 `docs/operations/env.md` 카탈로그를 입력으로 `/etc/assessment-engine/*.env` 작성.

multi-node 분리 패턴은 `docs/operations/deployment.md` 4절 (layered shared.env + component.env). Ansible group_vars/host_vars와 자연 정합.

secret 채널은 `docs/operations/prod-contract.md` 7절의 카탈로그(systemd EnvironmentFile·Vault·k8s Secret·Docker secrets 등) 중 CD repo가 선택. 본 repo는 결과(weak default 거부)만 검증.

### 3.4. schema migration

```bash
# wheel 안 동봉된 alembic.ini 경로 동적 해석
ALEMBIC_INI=$(/opt/assessment-engine/bin/python -c \
  'from importlib.resources import files; print(files("assessment_engine") / "_alembic.ini")')

/opt/assessment-engine/bin/python -m alembic -c "$ALEMBIC_INI" upgrade head
```

CD repo는 본 명령을 systemd one-shot service 또는 Ansible task 또는 k8s Job으로 wrapping.

### 3.5. systemd unit 작성

본 repo `docs/operations/deployment.md` 4절 inline 예시 templating. 4 컴포넌트(web·consumer·diagnostic-worker·diagnostic-scheduler) 각각 unit 파일.

reference 형태 (Ansible Jinja2):
```ini
[Unit]
Description=Assessment Engine {{ component }}
After=network-online.target

[Service]
Type=simple
User=assessment
WorkingDirectory=/opt/assessment-engine
EnvironmentFile=/etc/assessment-engine/shared.env
EnvironmentFile=/etc/assessment-engine/{{ component }}.env
ExecStart=/opt/assessment-engine/bin/python -m assessment_engine.{{ module }}
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

[Install]
WantedBy=multi-user.target
```

graceful shutdown 정합 — `KillSignal=SIGTERM` + `TimeoutStopSec` 충분히 (본 repo CLAUDE.md #F11).

### 3.6. 헬스체크

```bash
curl -fsS http://<engine-host>:8000/health
# {"status":"ok"} → 정상
```

systemd watchdog 또는 외부 모니터(Prometheus alert·Pingdom 등)로 연속 검증.

### 3.7. 관측 연동

| endpoint | 외부 인프라 작업 |
|---------|-------------|
| `GET /metrics` (Prometheus) | Prometheus scrape config 등록 — `prod 외부 노출 금지` (reverse proxy internal-only) |
| `LOG_FORMAT=json` 활성 | log aggregator (Loki·Fluent Bit·CloudWatch agent) — json line indexing |
| `GET /health` | systemd `Restart=always` + 외부 watchdog |

## 4. 운영 체크리스트 (prod 배포 직전)

본 repo의 prod 운영 contract 충족 검증:

- [ ] `APP_ENV=prod` 환경변수 명시
- [ ] `POSTGRES_PASSWORD`·`RABBITMQ_PASSWORD` 강한 random secret 주입 (Vault·EnvironmentFile 등 자유)
- [ ] `POSTGRES_USER`·`RABBITMQ_USER`도 dev default("assessment") 아닌 값
- [ ] `alembic upgrade head` 사전 실행 — `_alembic.ini` 활용
- [ ] DB·MQ·Redis 외부 포트 노출 없음 — reverse proxy 뒤
- [ ] `/metrics` 외부 노출 금지 — internal-only 라우트
- [ ] `LOG_FORMAT=json` (log aggregator 운영 시)
- [ ] Sigstore signature 검증 통과
- [ ] SHA256SUMS 통과
- [ ] systemd `KillSignal=SIGTERM` + `TimeoutStopSec` 적용
- [ ] graceful shutdown 검증 (`systemctl stop` 후 in-flight 작업 손실 0 확인)
- [ ] `Settings()` 생성 시점 `ValueError` 발생 없음 — secret 주입 채널 정합 확인

## 5. release 갱신 워크플로 (CD repo 일상 운영)

본 repo의 새 release 발사 시 CD repo가 따라야 할 절차:

1. 본 repo GitHub Release 페이지에서 새 `v*` tag·CHANGELOG 확인
2. CD repo의 inventory·playbook에 새 version 명시 (예: `assessment_engine_version: 1.2.3`)
3. staging 환경에 dry-run (Ansible `--check` 또는 k8s `--dry-run=server`)
4. staging 헬스체크 통과 후 prod 적용
5. prod 헬스체크 + 관측 지표(/metrics) 5분 이상 안정 확인
6. 이상 시 rollback playbook으로 이전 version 복원

## 6. 본 repo 변경이 CD repo에 미치는 영향

본 repo와 CD repo는 release artifact + contract 문서를 통해서만 연결. drift 발생 가능 지점:

| 본 repo 변경 | CD repo 영향 | 감지 방법 |
|------------|-------------|---------|
| 환경변수 추가 (`env.md`) | secret·config 카탈로그 갱신 의무 | `Settings()` `ValueError` (weak default 거부) |
| 환경변수 rename·삭제 | 옛 키 무시되거나 fail | CHANGELOG breaking change |
| secret 채널 권장 변경 | `prod-contract.md` 7절 카탈로그 확장 | docs review |
| 새 컴포넌트 추가 | systemd unit·env·multi-node 매트릭스 확장 | `deployment.md` 4절 표 변경 |
| 메시지 schema 변경 | agent 측 영향 (CD repo 무관, agent repo 책임) | `agent.md` 표 변경 |
| schema 변경 | `alembic upgrade head` 자동 적용 | release notes |
| `_validate_prod_*` 강화 | weak default 거부 추가 | runtime `ValueError` |
| BREAKING change | major bump (1.x.x) | semver tag |

CD repo는 본 repo CHANGELOG·release notes 매 release마다 검토 의무. semver MAJOR bump 시 contract drift 가능성 가장 큼.

## 7. 도구 선택 가이드

| 도구 | 적합 환경 | 본 contract 충족도 |
|------|---------|----------------|
| Ansible (Vault) | VM + systemd 단순 운영 | 정합 (`deployment.md` 4절과 자연 정합) |
| SaltStack | 대규모 fleet 분산 | 정합 |
| Terraform + Ansible | IaC + 구성 분리 | 정합 |
| k8s manifest + Helm | 컨테이너 오케스트레이션 | 정합 (Dockerfile 별도 작성 의무) |
| systemd 직접 (수동) | 1~3 node 작은 환경 | 정합 (deployment.md 그대로 따라 하면 됨) |

도구 선택은 CD repo 운영자 자유. 본 repo contract만 충족하면 어떤 도구든 OK.

## 8. CD repo 시작 시 참고할 본 repo 문서

순서대로:
1. `docs/operations/release.md` — release artifact 카탈로그 (받을 것)
2. `docs/operations/env.md` — 환경변수 카탈로그 (inject할 것)
3. `docs/operations/prod-contract.md` — secret 채널·`_validate_prod_*` 정책
4. `docs/operations/deployment.md` — install·실행 단계 + 4절 multi-node 분리 inject 예시
5. `docs/operations/alembic.md` — schema 운영
6. `docs/operations/observability.md` — `/metrics`·log format
7. `docs/operations/github-setup.md` — 본 repo GitHub UI 정책 (CD repo와 분리 인지)
8. CLAUDE.md `#F11` — graceful shutdown (systemd `KillSignal` 정합)
9. ADR 0012 — wheel artifact 채택 사유
10. ADR 0013 — release-please 자동화 (semver tag 발사 시점 이해)

본 repo와 CD repo는 별개 lifecycle. CD repo가 본 repo source clone할 필요 0 — release artifact + contract 문서 ref만으로 충분.
