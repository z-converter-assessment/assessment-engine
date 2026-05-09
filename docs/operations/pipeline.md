# 파이프라인 검증 가이드

## 파이프라인 검증 (Vagrant VM)

에이전트(C 바이너리) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증한다.

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
│   │  Agent (C)    │   │  Agent (C)    │   │  Agent (C)    │                  │
│   └───────────────┘   └───────────────┘   └───────────────┘                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Vagrant NAT 환경에서 VM → 호스트 접근 주소: `10.0.2.2`
- 3개 VM이 동시에 각자의 메트릭을 발행 → Web UI에서 서버 3대로 확인
- `vagrant up` 완료 시 각 VM에서 에이전트가 자동 빌드 → systemd 서비스 등록 → 시작

### 사전 요구사항

| 소프트웨어 | 설치 방법 | 용도 |
|-----------|----------|------|
| VirtualBox 7.1+ | [virtualbox.org](https://www.virtualbox.org/) | VM 하이퍼바이저 |
| Vagrant 2.4.x | [vagrantup.com](https://www.vagrantup.com/) | VM 프로비저닝 |

> Apple Silicon (ARM64): VirtualBox 7.1+부터 ARM VM 지원. bento 박스가 arm64 변형을 자동으로 선택한다.

### 디렉토리 구조 전제

```
(작업 디렉토리)/
├── assessment-engine/   ← Vagrantfile 위치
└── assessment-agent/    ← 에이전트 소스 (git clone 필요)
```

### VM 구성

| VM | Box | OS | 설치 서비스 |
|----|-----|----|----------|
| `cache-server-01` | bento/ubuntu-22.04 | Ubuntu 22.04 | redis-server |
| `app-server-01` | bento/rockylinux-9 | Rocky Linux 9 | (없음) |
| `web-server-01` | bento/debian-12 | Debian 12 | nginx |

### 실행

이하 모든 명령은 `assessment-engine/` 루트에서 실행한다.

```bash
# 환경변수 설정 (최초 1회)
cp .env.example .env                       # 엔진(web/consumer) 환경변수
cp infra/agent.env.example infra/agent.env # 에이전트(VM) 전용 secret 채널

# 전체 환경 기동 (Docker → web 헬스체크 → Vagrant VM 순서)
./dev-up.sh
```

> 두 .env 파일을 분리하는 이유: 엔진의 `.env`는 docker-compose로 web/consumer에 주입,
> `infra/agent.env`는 Vagrantfile이 read해 VM 안 `/etc/assessment-agent.env`로 옮긴다.
> 정책·근거는 [docs/operations/dev-prod.md](dev-prod.md) #9 (에이전트 secret 채널 분리).

`dev-up.sh` 실행 순서:
1. `docker compose up --build -d`
2. web 헬스체크 통과 대기 (최대 120초) — web이 먼저 올라와야 DB 스키마가 생성됨
3. `vagrant up` — VM 기동 후 에이전트 자동 시작 (최초 1회는 OS·패키지 설치·에이전트 빌드로 수분 소요)

VM 프로비저닝 중 자동으로 수행되는 작업:
1. 빌드 의존성 패키지 설치 (gcc, librabbitmq-dev, libcjson-dev 등)
2. `infra/agent.env` 값으로 `/etc/assessment-agent.env` 생성 (`RABBITMQ_HOST=10.0.2.2` 별도 주입)
3. `make` — 에이전트 빌드
4. systemd `assessment-agent.service` 등록 및 시작

### 결과 확인

http://localhost:8000/servers/ 에서 서버 3대 온라인 확인.
60초 주기로 메트릭이 갱신되며 각 서버의 상세 페이지에서 CPU·메모리·디스크·네트워크 확인.

### 환경 종료

`assessment-engine/` 루트에서 실행한다.

```bash
# 전체 환경 종료 (Vagrant VM 제거 → Docker 볼륨 삭제, 데이터 초기화)
./dev-down.sh
```

`dev-down.sh` 실행 순서:
1. `vagrant destroy -f` — VM 3대 제거 (각 VM의 `/etc/assessment-agent.env`·바이너리·systemd unit 모두 사라짐)
2. `docker compose down -v` — 컨테이너 + `postgres_data` 볼륨 + 네트워크 제거 (DB 데이터 삭제)

#### 부분 종료 (선택)

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM은 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (데이터 삭제) |
| VM만 종료, Docker는 유지 | `vagrant destroy -f` 또는 일시 정지 `vagrant halt` |
| 특정 VM만 종료 | `vagrant destroy -f cache-server-01` |

VM `halt`(일시 정지) 후 `vagrant up`은 프로비저닝 다시 안 함 (재기동만). `destroy` 후엔 다음 `vagrant up`이 풀 프로비저닝 (수분 소요).
