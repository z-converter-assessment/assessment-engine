# 문서 인덱스

본 디렉토리는 프로젝트의 영구 문서. 단일 진실은 코드 + `.claude/CLAUDE.md` (원칙·금지 사항) + 본 디렉토리의 deep dive.

## 어떤 문서를 언제 보는가

| 상황 | 위치 |
|------|------|
| 시스템 한눈에 / 처음 진입 | 루트 `README.md` |
| 결정된 규약·금지 사항·계층 책임 | `.claude/CLAUDE.md` |
| 특정 컴포넌트가 어떻게 동작하나 (consumer / web / db / redis / rabbitmq / agent) | `architecture/` |
| 어떻게 띄우고·배포하고·검증하나 (Docker / Vagrant / dev·prod 분리 / env / testing / E2E pipeline) | `operations/` |
| 왜 그렇게 결정했나 (의사결정 기록) | `adr/` |
| 의식적 트레이드오프와 한계 (T1~T11) | `adr/tradeoffs.md` |
| 협의·미팅 메모 (임시) | `meetings/` |

## 디렉토리

```
docs/
├── README.md              ← 본 파일 (인덱스)
├── architecture/          컴포넌트별 deep dive (영구·갱신)
│   ├── agent.md           에이전트 메시지 스키마·포트 수집·디스크 필터링
│   ├── consumer.md        schemas / handler / main / 멱등성 / 재시도
│   ├── db.md              ORM 모델 / DTO / Repository / TimescaleDB
│   ├── rabbitmq.md        vhost·권한 모델 / 토폴로지 / dev·prod 분기
│   ├── redis.md           키 설계 / TTL / PUB/SUB / 멱등성 / 캐시 무효화 / mget
│   └── web.md             레이어 원칙 / 서비스 모듈 / ViewModel / Jinja2 / 차트 UI
├── operations/            운영·환경·검증 (영구·갱신)
│   ├── dev-prod.md        dev/prod 환경 전략 + secret 정책 + 운영 체크리스트
│   ├── docker.md          Dockerfile / docker-compose (볼륨·헬스체크·기동·env)
│   ├── env.md             환경변수 전체 키 카탈로그
│   ├── pipeline.md        E2E 파이프라인 검증 (Vagrant VM)
│   ├── testing.md         단위·통합 테스트 실행·Fixture·작성 패턴
│   └── vagrant.md         Vagrant 사용 맥락 / VM 구성 / 프로비저닝
├── adr/                   Architecture Decision Records (영구·불변)
│   ├── README.md          ADR 인덱스
│   ├── 0001-redis-decoupling.md
│   └── tradeoffs.md       의식적 설계 선택과 한계 (T1~T11)
└── meetings/              협의·메모 (임시, 현재 비어있음)
```

## 라이프사이클 규약

- `architecture/`, `operations/` → 영구·갱신. 코드 변경 시 동시 업데이트.
- `adr/` → 영구·불변. 결정이 바뀌면 새 ADR 추가 (이전은 Status: Superseded).
- `meetings/` → 임시. 영구 정책으로 승격되면 다른 영구 문서로 이동.
- `temp` 키워드 들어간 파일(`docs/temp.md` 등) → 작업 중 임시 메모. 항상 무시.