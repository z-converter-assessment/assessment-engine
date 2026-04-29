# 테스트 가이드

## 사전 준비

로컬 개발 환경이 세팅되어 있지 않다면 먼저 설치한다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install uv
uv pip install -e ".[test]"
```

---

## 단위 테스트

Docker 없이 실행 가능. Mock을 사용해 외부 의존성(DB·Redis·RabbitMQ)을 제거한다.

```bash
pytest tests/unit/ -v
```

## 통합 테스트 (DB)

testcontainers가 TimescaleDB 컨테이너를 자동으로 기동·종료한다. **Docker daemon 필요.**

```bash
pytest tests/integration/ -v
```

## 전체 실행

```bash
pytest tests/ -v
```

## 마커로 선택 실행

```bash
pytest tests/ -m unit          # 단위 테스트만
```
```bash
pytest tests/ -m integration   # 통합 테스트만
```

---

## E2E 테스트 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인 검증.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                 HOST MACHINE                                 │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐   │
│   │                 DOCKER COMPOSE (assessment-engine)                   │   │
│   │                                                                      │   │
│   │        ┌─────────────┐              ┌─────────────┐                  │   │
│   │        │   FastAPI   │              │   RabbitMQ  │                  │   │
│   │        │   (:8000)   │              │   (:5672)   │                  │   │
│   │        └──────┬──────┘              └──────┬──────┘                  │   │
│   │               ▲                            │                         │   │
│   │               │ (4) QUERY                  │ (2) DISPATCH            │   │
│   │               │                            ▼                         │   │
│   │        ┌──────┴──────┐              ┌─────────────┐                  │   │
│   │        │ PostgreSQL  │              │   Consumer  │                  │   │
│   │        │   (:5432)   │ <─────────── │             │                  │   │
│   │        └─────────────┘  (3) PERSIST └─────────────┘                  │   │
│   │                                                                      │   │
│   └───────────────────────────▲──────────────────────────────────────────┘   │
│                               │                                              │
│                               │ (1) PUBLISH                                  │
│                               │     (Target: 10.0.2.2)                       │
│           ┌───────────────────┴───────────────────┐                          │
│           │                   │                   │                          │
│   ┌───────┴───────┐   ┌───────┴───────┐   ┌───────┴───────┐                  │
│   │     VM 01     │   │     VM 02     │   │     VM 03     │                  │
│   │   (Ubuntu)    │   │    (Rocky)    │   │   (Debian)    │                  │
│   ├───────────────┤   ├───────────────┤   ├───────────────┤                  │
│   │  Agent (C99)  │   │  Agent (C99)  │   │  Agent (C99)  │                  │
│   └───────────────┘   └───────────────┘   └───────────────┘                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Vagrant NAT 환경에서 VM → 호스트 접근 주소: **`10.0.2.2`**
- 3개 VM이 동시에 각자의 메트릭을 발행 → Web UI에서 서버 3대로 확인
- `vagrant up` 완료 시 각 VM에서 에이전트가 **자동 빌드 → systemd 서비스 등록 → 시작**

### 추가 사전 요구사항

| 소프트웨어 | 설치 방법 | 용도 |
|-----------|----------|------|
| VirtualBox 7.1+ | [virtualbox.org](https://www.virtualbox.org/) | VM 하이퍼바이저 |
| Vagrant 2.4.x | [vagrantup.com](https://www.vagrantup.com/) | VM 프로비저닝 |

> **Apple Silicon (ARM64)**: VirtualBox 7.1+부터 ARM VM 지원. bento 박스가 arm64 변형을 자동으로 선택한다.

### 디렉토리 구조 전제

```
(작업 디렉토리)/
├── assessment-engine/   ← Vagrantfile 위치
└── assessment-agent/    ← 에이전트 소스 (git clone 필요)
```

### VM 구성

| VM | Box | OS | 시뮬레이션 |
|----|-----|----|----------|
| `web-server-01` | bento/ubuntu-22.04 | Ubuntu 22.04 | 웹 서버 |
| `db-server-01` | bento/rockylinux-9 | Rocky Linux 9 | DB 서버 (RHEL 계열) |
| `backup-server-01` | bento/debian-12 | Debian 12 | 백업 서버 |

### 실행

```bash
# 1. assessment-engine 기동
cd assessment-engine
cp .env.example .env
docker compose up --build -d
```

```bash
# 2. VM 기동 (최초 1회 — OS·패키지 설치·에이전트 빌드로 수분 소요)
vagrant up
```

프로비저닝 중 자동으로 수행되는 작업:
1. 빌드 의존성 패키지 설치 (gcc, librabbitmq-dev, libcjson-dev 등)
2. `.env` 생성 (`RABBITMQ_HOST=10.0.2.2` 포함)
3. `make` — 에이전트 빌드
4. systemd `assessment-agent.service` 등록 및 시작

### 결과 확인

```bash
# 에이전트 로그
vagrant ssh web-server-01 -c "journalctl -u assessment-agent -f"
```

http://localhost:8000/servers/ 에서 서버 3대 온라인 확인.
60초 주기로 메트릭이 갱신되며 각 서버의 상세 페이지에서 CPU·메모리·디스크·네트워크 확인.

### VM 관리

```bash
vagrant halt                    # 전체 정지 (machine-id 유지)
vagrant reload web-server-01    # 특정 VM 재기동
vagrant destroy -f              # 전체 삭제 (재기동 시 새 machine-id → 새 서버로 DB 등록)
```