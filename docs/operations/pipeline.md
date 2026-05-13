# 파이프라인 검증

본 문서는 운영자 절차 요약. VM 매트릭스·OS 다양성·합성 부하 프로파일·provisioning·운영 디버깅은 `docs/operations/lima.md` 단일 진실.

에이전트(C) → RabbitMQ → Consumer → DB → Web UI 전체 파이프라인을 실제 VM 환경에서 검증 + 시연용 분류·attention 분포 가시화.

```
HOST MACHINE
  Docker Compose (assessment-engine)
    FastAPI :8000  <----QUERY-----  PostgreSQL :5432
                                         ^
                                    PERSIST | (3)
                                         |
    RabbitMQ :5672 ---DISPATCH(2)---> Consumer
         ^
         | PUBLISH (1) Target: host.lima.internal
         |
  Lima VM x 7 (assessment-agent.service)
    web · offline · app · monitor · mq · cache · db
```

## 사전 요구

| 도구 | 설치 |
|------|------|
| Lima 1.0+ | `brew install lima` |
| Docker | Docker Desktop 또는 colima |

디렉토리 전제:
```
(작업 디렉토리)/
├── assessment-engine/   <- 본 레포
└── assessment-agent/    <- 에이전트 소스 (git clone 필요)
```

## 실행

```bash
cp .env.example .env                       # 엔진 환경변수
cp infra/agent.env.example infra/agent.env # 에이전트 secret 채널 (분리됨, #B)
./dev-up.sh                                # Docker → web 헬스체크 → Lima VM 7대
```

`dev-up.sh` 4단계:
1. `docker compose up --build -d` — 엔진 기동
2. `migrate(alembic upgrade head)` 완료 대기 (cap 180s)
3. web 헬스체크 통과 대기 (cap 180s)
4. `start_or_resume_vm` wrapper로 7 VM 순차 (cloud image 다운로드 포함 최초 5~15분)

## 결과 확인

- http://localhost:8000/servers/ — 서버 7대 온라인
- 60초 주기 메트릭 갱신
- 분류 분포 시연은 `/servers/report?period_days=1` (대시보드는 `recommendation.WINDOW_DAYS=14` 고정, #F10)
- attention 카드 상단 요약: web `agent_unstable` + offline `gap_warnings`(5m+ 후)

## 종료

```bash
./dev-down.sh   # Lima VM 제거 → Docker 볼륨 삭제 (DB 초기화)
```

부분 종료:

| 시나리오 | 명령 |
|---------|------|
| Docker만 종료, VM 유지 | `docker compose down` (데이터 유지) / `docker compose down -v` (삭제) |
| 특정 VM만 종료 | `limactl delete -f web-server-01` |
| VM 일시 정지 | `limactl stop <vm>` (재기동 시 yaml provision 안 함) |

## 트러블슈팅

상세는 `docs/operations/lima.md` "누적 사고 패턴" + "운영 노트·트러블슈팅". 흔한 케이스:

| 증상 | 해결 |
|------|------|
| broker 재기동 후 에이전트 publish 멈춤 | 7 VM 모두 `limactl shell <vm> sudo systemctl restart assessment-agent` |
| limactl start 5분+ stuck | `start_or_resume_vm` wrapper가 자동 우회 |
| `librabbitmq-devel` not found (RHEL family) | dev-up.sh dnf 분기가 EPEL + CRB/PowerTools 자동 활성화 — VM 삭제 후 재기동 |
