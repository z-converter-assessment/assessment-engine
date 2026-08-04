# ADR 0031 — OS EOL 운영신호: endoflife.date 스냅샷 카탈로그

상태: Refined by ADR 0061 (2026-08-04, 경계 3개 기준 4상태 판정) — 스냅샷 방식·매칭 규약은 유효

## Context

운영신호 `os_eol_warnings` 는 "지원 종료(EOL)된 OS 호스트"를 마이그레이션 검토 대상으로 표시한다. 초기 구현은 수동 정적 매핑이었다 — Linux 는 `(os_id, os_version_prefix) -> EOL 날짜` dict 6종, Windows 는 `kernel build -> EOL` dict 7종을 코드에 하드코딩.

수동 매핑의 한계가 누적됐다:

1. 커버리지 — 등록한 OS 만 판정. SUSE·Alpine·Fedora·Amazon Linux 등 미등록 OS 는 EOL 경고가 안 떠서, "지원 중"과 "카탈로그 미등록"이 구분 안 되는 false negative.
2. 정확성 결함 — 카탈로그 매칭만으로 발화했고, Linux 카탈로그가 우연히 전부 과거 날짜라 "이미 EOL"과 맞아떨어졌다. Windows(미래 EOL 포함)를 넣자 Server 2025(EOL 2034)도 경고로 오발화하는 게 드러남.
3. 갱신 부담 — 벤더가 EOL 을 연장하면 코드 수정. Windows edition(Home/Pro vs Server)·release 조합도 수동 관리.
4. Windows 버전 체계 — DisplayVersion(`24H2`)은 Server 2016+ 에만 있어 2012 이하를 못 잡는다. build 번호가 전 버전 고유 식별자.

근본 원인 — OS 의 EOL/지원상태는 벤더 lifecycle 이라는 외부 지식이라 engine 이 자동 측정할 수 없고, 어떤 신호든 카탈로그(외부 지식 내장)나 agent 측정(설치일=부정확) 둘 중 하나가 불가피하다. 모든 OS·버전을 손으로 매핑하는 건 비현실적.

## Decision

EOL 날짜 카탈로그를 endoflife.date 스냅샷으로 대체한다. 빌드/갱신 시점에 endoflife.date 에서 fetch 해 정적 JSON 으로 repo 에 commit 하고, 런타임은 그 정적 파일만 읽는다 (외부 의존 0).

- 데이터 출처 — endoflife.date. 벤더 공식 문서 기반(Microsoft/Red Hat/Canonical/SUSE 등), 충돌 시 가장 보수적 날짜 채택, 분기 검토. community 위키·스크래핑을 1차 출처로 쓰지 않음 (신뢰성 확인됨, 2019~ 500+ 기여자 456 product).
- 스냅샷 — `scripts/snapshot_os_eol.py` 가 주요 distro(debian/ubuntu/rhel/rocky-linux/almalinux/centos/centos-stream/sles/opensuse/amazon-linux/fedora) + windows-server 를 fetch -> `src/assessment_engine/web/services/mappers/os_eol_catalog.json` 생성. git commit. wheel 은 hatchling packages 로 자동 포함 (templates 와 동일). 도구 위치 = `scripts/` (빌드·릴리스 maintenance — endoflife.date 외부 fetch 라 인터넷 되는 사측 빌드 환경 실행, dev 로컬 개발도·운영 폐쇄망 런타임도 아님).
- 런타임 — `shared.resolve_os_eol(os_id, os_version, kernel_version, today)` 단일 판정 (attention 카드 + 보고서 정성 요약 공용). 카탈로그 조회 + EOL 경과 비교.
  - Linux: `os_id -> endoflife product slug`(`_OS_ID_TO_EOL_PRODUCT`, 대부분 동일·예외만: rocky->rocky-linux, amzn->amazon-linux). `os_version == cycle` 또는 `startswith(cycle+".")` (rocky "9.7" -> cycle "9").
  - Windows: 운영 환경은 항상 Windows Server 가정 (client Home/Pro 미존재) — `windows-server` 카탈로그에서 `kernel build == latest build` 매칭. endoflife `latest`("10.0.26100")에서 build("26100") 추출, agent `kernel_version`("26100.8457")에서 build("26100") 추출. build 가 2008 R2(7601)~2025(26100) 전 버전 고유라 DisplayVersion 체계 문제를 우회.
  - EOL 미도래(아직 지원 중)는 None — 미래 EOL 오발화 방지. 미등록 OS 도 None.

### 외부 의존 실패 모드 (#F6)

endoflife.date 의존은 빌드/갱신 시점만 — 런타임은 정적 카탈로그라 외부 의존 0 (고객사 폐쇄 내부망 #A0 에서 동작). 스냅샷 실패(네트워크·endoflife.date 장애) 시 기존 카탈로그가 그대로 유지되어 운영 영향 없음 (fail-safe). 갱신은 `snapshot_os_eol.py` 재실행 + commit — 분기 1회 권장 (endoflife 데이터 변동 빈도 낮음).

## Consequences

- Linux 커버리지가 endoflife.date 등록 distro 전체로 확대 (수동 6종 -> 주요 11 product).
- Windows 가 build 기반이라 Server 2008 R2~2025 전 버전 일관 매칭 (DisplayVersion 누락 버전 포함).
- 정확성 — EOL 경과 비교로 미래 EOL 오발화 제거. attention 카드와 보고서가 동일 판정(resolve_os_eol).
- 수동 EOL 날짜 관리 소멸 — 벤더 EOL 연장도 스냅샷 재실행으로 반영.

## Tradeoffs / 한계

- 미등록 OS 침묵 — endoflife.date 에 없거나 스냅샷 product 목록에 없는 OS(예: alpine)는 EOL 판정 불가(None). EOL 경고 부재가 "최신"과 "미등록"을 구분 못 하는 false negative 는 의식적 트레이드오프. 필요 시 `_LINUX_PRODUCTS` 목록 확장.
- 정적 스냅샷 — 런타임에 최신이 아님. 분기 갱신 사이의 신규 EOL 은 다음 스냅샷까지 반영 안 됨. 폐쇄망 제약상 동적 API(런타임 endoflife.date 호출)는 부적합이라 정적이 현실적 선택.
- Windows Server 가정 — client(Home/Pro) edition 은 미고려. build 가 client/server 겹치는 경우(26100 = Win11 24H2 = Server 2025) Server 로 해석. 운영 환경이 client 면 EOL 이 다를 수 있음.
- 대안 검토 — "출시 N년 경과" 신호도 release 카탈로그가 필요해 매핑 부담은 동일(다만 출시일은 불변·edition 무관). endoflife.date 스냅샷이 release·EOL 둘 다 제공하므로 향후 "출시 N년" 신호로 전환·병행도 가능 (본 ADR 은 EOL 경과 채택).

## 관련

ADR 0029(OS-aware right-sizing), #C1(운영신호 정책 — engineer os_eol 표시), #E7(service_classifier), #F6(외부 의존 실패 모드).
