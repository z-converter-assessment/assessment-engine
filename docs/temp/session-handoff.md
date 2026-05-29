# 세션 핸드오프 (2026-05-30)

> 임시 문서 (docs/temp). 컨텍스트 clear 후 새 세션이 읽는 진입점. "현재 상태" 단일 진실은 각 영역의
> 영구 docs·코드. 본 문서는 이번 세션이 무엇을 바꿨고·무엇이 미완이며·다음에 뭘 할지의 요약.
> 브랜치: chore/general-edits (원격에 동명 브랜치 존재 — rename 시 원격 정리 필요).

---

## 출발점

전 세션(2026-05-29)에서 agent 계약 정합 + dev 파이프라인 Windows 하이브리드 + 환경요약 UI 개선을
했고, 이번 세션은 그 위에 대시보드 "현황 모니터링" 축 신설(실시간 메트릭)·서버목록 UX·운영신호
정확화·dev 파이프라인 보강(Windows agent 빌드·시계·오프라인 VM)을 진행.

---

## 1. 환경 실시간 메트릭 카드 (신규)

목적: 14일 right-sizing(장기 사이징)과 분리된 "현재 현황 모니터링" 축.
- ViewModel `EnvironmentRealtime` + `RealtimePeak`/`RealtimePeakGroup` (`view_models/attention.py`)
- mapper `build_environment_realtime` (`mappers/attention.py`) — online 서버 최신 스냅샷만 표본
- service `get_environment_realtime` (`query_service.py`) — online 서버만 snapshot(오프라인 stale 제외)
- 카드 (`_dashboard_live.html`, 자동갱신 fragment 안) + `list_page.py` context(메인 + ?fragment=live)
- 구성: 유효 표본 N/M대(=online+메트릭) / 평균 활용률 도넛 3개(환경 평균과 동일 푸른 단색 컴포넌트) /
  현재 부하 상위 = CPU·메모리·디스크 각 탑3 3열 grid(hostname-값 점선 leader) / 빈 상태 empty_state(E9)

## 2. 운영신호 — agent 재시작 정확화

- Redis sliding 누적(부정확, 39회) -> DB fixed 1h 윈도우로 교체
- `agent_restart_counts_recent(server_ids, since)` = `COUNT(DISTINCT agent_started_at)-1`
  (`base_report.py` 추상 + `query/report.py` 구현), `get_attention_signals` 연결
- 라이브: 39 -> 13 (정확)

## 3. 서비스 뱃지 알파벳 정렬

- `_dedup_known` + `enrich_server_detail` (`mappers/server.py`) known 뱃지 category ASC 정렬

## 3-1. 환경 요약 — 역할 미분류(unknown) 호스트 수 노출

- `EnvironmentOverview.role_unknown_count` (`view_models/attention.py`) + `build_environment_overview`
  (`mappers/attention.py`): known 서비스 카테고리가 0인 호스트(서비스 없음·전부 unknown) 수.
- `_dashboard_live.html` 역할 분포에 `unknown N` 뱃지(서버목록 unknown 뱃지와 동일 색). offline-server
  2대처럼 서비스 없는 호스트가 잡힘. role_distribution 비어도 unknown>0이면 노출(E9).

## 4. 서버목록 UX

- 전체보기(client clip): 기본 CLIP_SIZE=5 행 표시 + "전체보기 (5/6)" 버튼(btn-action, 중앙)으로 전체.
  필터 활성 시 clip 해제(조건 맞는 전부). server 전체 로드(`_LIST_FETCH_LIMIT=10000`, list_page.py) +
  client(list.js) clip — E2 page 정책의 의식적 예외(대시보드 단일 화면).
- 정렬: 1차 online(온라인 우선) + 2차 hostname ASC (service 레이어, query_service.list_servers).
  online 판정이 Redis 기반이라 DB ORDER BY 불가 -> service 정렬, repo는 hostname ASC raw.
- 서버 발견 버튼: 필터 행(search-row) 우측 정렬(margin-left:auto)로 이동.
- 폐기: CPU hotspot 컬럼/정렬(LATERAL delta)은 구현했다가 사용자 결정으로 제거(롤백 완료).

## 5. dev 파이프라인 — Windows agent 빌드·시계·오프라인 VM

- Windows agent mingw 크로스빌드 통합 (`dev-up.sh build_win_agent`): vendor 재사용, dev-up 매 실행 시
  재빌드·교체. `AGENT_HOSTNAME_OVERRIDE` 반영(win-server-01). exe 교체는 staging scp + `win_ps`
  helper(`-EncodedCommand` UTF-16LE base64 — 공백 경로 quoting 회피).
- Windows 시계 UTC 동기화 (`dev-up.sh` deploy 단계 `w32tm /resync`): win VM(UTM RTC drift)이 미래
  타임스탬프 발행하던 문제 해결. agent 코드는 표준 `time()`이라 정상 — 원인은 VM 시계.
- 오프라인 시연 VM 2대 추가 (`offline-server-01/02`): 서비스 없이 agent만, 최초 메트릭 발행 후
  poweroff(install_offline_demo). 총 6대 -> 전체보기(6>5) 발현.

## 6. 네트워크 토폴로지 시각화 (설계만, 미구현)

- `docs/temp/network-topology-viz.md` — Cytoscape.js + Bipartite(서브넷 허브) 결정.
  NIC별 `IP/prefix`(CIDR) payload 정의, agent->engine 데이터 흐름, Cytoscape elements 모델, 구현 단계.
- 전제: agent 가 `x.x.x.x/xx` CIDR 추가 수집 대략 합의. 구현은 payload 계약 확정 후 별도.

---

## 환경 상태 (현재)

- 등록 6대: app-server-01(online), data-server-01(online), edge-server-01(offline-demo),
  offline-server-01/02(서비스 없음·오프라인), win-server-01(UTM, 시계 교정됨)
- DB win-server-01: 시계 교정 후 정상 metric 수집 중. 단 교정 전 미래 stale 행 약 32개 잔존
  (`collected_at > now()`) -> `max(collected_at)` 신선도가 미래로 보일 수 있음. dev DB cleanup 후보.
- dev compose 6 서비스 가동. 웹 http://localhost:8000.

## 미완·다음 작업 후보

- 네트워크 시각화 실제 구현 (payload 계약 -> consumer/DTO/DB/Alembic -> mapper/ViewModel ->
  /api/network/topology -> Cytoscape JS). #B·#F9 체크리스트.
- win 미래 stale 행 정리 (모든 시계열 테이블 `collected_at > now()` 삭제, dev 한정).
- 전체보기 실제 시연: 6대 환경에서 CLIP_SIZE=5라 발현(브라우저 확인).

## 영구 docs 포인터

- 렌더링 P1~P4 / 표시 표준: CLAUDE.md #E + `docs/architecture/web/*`
- repository / list_servers: `docs/architecture/db/repositories.md`
- dev 파이프라인 / Windows VM: `docs/development/windows-vm.md` · `docs/development/pipeline.md`
- 평가 윈도우 / 차트 옵션: CLAUDE.md #F10
