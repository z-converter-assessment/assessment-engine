# right-sizing 재설계 — 다음 세션 핸드오프 (여기부터 읽어라)

성격: 임시 핸드오프. 세션 clear 후 재개 진입점.

상태: ADR 0052 자원 적정성 분류 재설계. Phase B(도메인)·Phase A(ingest) 완료·검증. Phase C/D 남음. 커밋됨 — working tree = 커밋 상태.

## 읽을 순서
1. `docs/adr/0052-right-sizing-principle-redesign.md` — 결정(전제 2개·5자원 USE·임계·근본원인·신뢰도 4종). 영구·self-contained.
2. `docs/temp/right-sizing-implementation-plan.md` — 로드맵·Phase별·field-source 매핑·체크포인트·규율.
3. `docs/temp/right-sizing-principle.md` — 상세 설계(모델·OS 신호 매트릭스·임계 근거·출처). 나중 `docs/architecture/right-sizing.md` 격상 원본.

## 완료 (커밋됨)
- Phase B 도메인: `recommendation.py` 신 모델 — `assess_cpu/memory/disk_capacity/disk_io/network` · `rollup_host`(근본원인) · `ConfidenceNote`(4종) · `downsize_prescribable` · `RS_*` 임계(전부 계층·출처 주석) · `RS_*_LABEL_KO`. additive strangler — 기존 `assess`/`classify` 유지(Phase E 제거). 단위 테스트 51(`tests/unit/test_right_sizing_model.py`).
- Phase A ingest: 시계열 4테이블 신 USE 신호 컬럼(server_metrics host-wide 11·disk_io await 4·net_io drops 2·mount inode 2) + migration `e6b8d0f2a4c7` + F9 체인(consumer/schemas·db/dtos/inbound·consumer/mappers·collect_repository) + `agent.md` 계약 표. testcontainer 검증(integration 18 + 단위 637 통과, ruff clean).
- 에이전트 계약: Linux/Windows 실코드(assessment-agent-temp/src/collect.c·windows-agent)로 필드명 일치 확인. 미구현=Windows per-core(NtQuery, 후속 트랙 — 엔진 None graceful).
- 코드리뷰: Error 0 · Warning 0 (Info 3 = 의도된 strangler 중간·Phase D confidence 확인·범위 밖 wire 완화).

## 다음 시작점 = Phase C (신 모델을 실데이터에 배선)
`report_aggregate` SQL이 신 신호 집계 -> `ReportRowRaw` 신 필드 -> `build_resource_stats`(web/services/mappers/report.py:392)가 신 `ResourceStats` 필드 채움 -> `rollup_host` 실동작.

주의(intricate): `report_aggregate`는 `server_metrics_5m`·`server_disk_io_5m`·`server_net_io_5m` continuous aggregate(cagg)를 탄다. 신 raw 컬럼은 그 cagg에 없어서, await·procs_blocked를 `counter_agg`로 집계하려면 cagg 재생성 마이그레이션이 필요(#C4·#C5) — 되돌리기 어렵고 신중. 사용자 검토 하에 진행 권장(오프램프 사유).

field-source 매핑(어느 필드가 cagg 집계 / raw / 엔진 산출인지)은 plan의 "agent 계약 확정" 절 참조. 엔진 산출 7필드(steal_p95·burst·trend·runway·history_hours·mem_swap_paging·mem_total_mb)는 agent 무관.

그다음: Phase D(화면 호출처 이관 — 서버목록·보고서·attention·도넛을 신 모델로, CP3 사용자 검토) -> Phase E(옛 `assess`/`classify` 제거 · `right-sizing.md` 격상 · CLAUDE.md #E3 갱신 · temp 삭제).

## 규율
feature 브랜치 · commit/PR 사용자 명시 · pytest authorization 시만 · F9 동시 갱신 체인 · P1~P4 · 임계 상수는 recommendation.py 단일.

## 독립 미해결 트랙
- Windows disk await: 구세대 viostor 5대(win2008/2012/2012R2/2012R2V11/2016) IOCTL 미부착 -> ETW 검증 대기(`../assessment-infra-temp/docs/etw-diskio-verification-request.md`). 엔진은 "포화 미관측"으로 처리.
- Windows per-core: agent NtQuery 후속.
