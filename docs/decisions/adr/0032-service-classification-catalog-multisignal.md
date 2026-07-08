# ADR 0032 — 서비스 분류: 단일 카탈로그 + 다중 신호 (name/comm/port)

상태: Accepted

## Context

서비스 뱃지(web/db/cache/mq/container/monitor)는 `service_classifier.classify(unit)` 하나가 unit 이름 substring 매칭(`_PATTERNS` 33개 키워드)으로만 결정해 왔다. agent v4(ADR 0027)로 Windows 가 합류하면서 세 가지 구조적 한계가 드러났다.

1. 단일 신호 의존 — agent 메시지가 싣는 강한 신호인 `listen_ports[].comm`(프로세스 exe basename)과 `port`가 `matched_ports()`(포트 표시)에서만 쓰이고 분류엔 미사용. 이름 하나로만 판정.

2. Windows 이름 가변성 — agent 가 SCM(Service Control Manager) registry 이름을 정규화 없이 발행한다(`W3SVC`, `MSSQLSERVER`, `MSSQL$INSTANCE`, `sqlservr` 등). 인스턴스명마다 변형돼 이름 substring 패턴 추가가 끝없이 필요. 반면 포트(SQL Server=1433)·comm(`sqlservr`)은 인스턴스명과 무관하게 안정적.

3. 카탈로그 분산 — 분류(`_PATTERNS`)·포트(`SERVICE_PORTS`)·드롭다운(`SERVICE_CATEGORIES`)·뱃지 CSS(`filters._BADGE_CLASSES`)·템플릿 범례 하드코딩이 5곳에 흩어져, 서비스 하나 추가에 동기 수정이 다발(#F9 부담).

외부 표준 API 채택 검토 — 6개 운영 카테고리는 도메인 고유 분류라 그대로 주는 API/표준이 없다. 포트 사실관계는 IANA Service Name and Port Number Registry 가 권위 표준이나, 런타임 호출은 분류 hot-path(목록 렌더링마다)·#F6 외부 의존 부담이고 표준 포트는 거의 불변이라 부적합. IANA 포트를 큐레이션 시점에 반영한 in-code 단일 카탈로그가 정석.

agent 데이터 제약 (불변 전제) — agent 코드는 현실적으로 수정 불가다. agent 가 주는 것은 `services[]={unit, sub}`(Linux systemd unit / Windows SCM `lpServiceName`) 와 `listen_ports[]={proto,addr,port,uid,pid,comm}` 뿐. services 에 pid/exe 가 없어 services 와 listen_ports 를 잇는 join key 가 없다(Windows enumeration 은 `dwProcessId` 를 쥐고도 안 싣고, Linux 는 systemctl 파싱). 결정적으로, 깨끗하고 안정적인 식별자(comm=exe basename, port)는 지저분한 service 이름이 아니라 listen_ports 에 있다 — Windows `MSSQL$PROD` 의 listen 소켓 comm 은 인스턴스명·로케일 무관하게 `sqlservr`.

## Decision

분류를 단일 카탈로그 기반 다중 신호로 전환한다.

1. 단일 카탈로그 `SERVICE_CATALOG`(`CategoryDef` 튜플) — 카테고리별 `name_keywords`(unit·comm 공용 substring) / `port_names`(서비스명 -> well-known 포트) / `badge_class`. 흩어진 5곳을 모두 본 카탈로그 파생으로 대체. import 시점 1회 계산: `_NAME_INDEX`(옛 `_PATTERNS`) · `_NAME_PORTS`(옛 `SERVICE_PORTS`) · `_PORT_INDEX`(port -> category) · `SERVICE_CATEGORIES` · `BADGE_CLASS_BY_CATEGORY`(templating filter import). 템플릿 범례(`services.html`)는 라우터가 `SERVICE_CATEGORIES` 주입.

2. `classify(unit, listen_ports=None)` 다중 신호, 정밀도 우선순위: (1) unit 이름 키워드 (2) comm 키워드 (3) listen 포트. 소프트웨어 정체성(name/comm)이 프로토콜(port)보다 카테고리 정밀도가 높다 — haproxy 가 5432 를 프록시해도 web 이지 db 가 아님. port 는 name/comm 무정보 시 fallback. `listen_ports` 미제공(목록 화면 경량 SELECT) 시 name 신호만 — 현행 동작 보존.

3. per-unit 분류(services 탭 행별)의 comm/port 신호는 `_attributed_ports`(comm~name 양방향 substring 또는 name well-known 포트로 unit 에 귀속된 포트)에만 적용. multi-service 호스트에서 임의 포트의 오분류 방지.

4. Windows 주력 워크로드를 카탈로그 이름 키워드로 흡수 — db 에 `mssql`(MSSQLSERVER/MSSQL$ 변형)·`sqlservr`(exe basename)·`postgres`(comm basename) 추가, web `w3svc`/`iis` 유지.

5. 호스트 워크로드 union — agent join key 부재(위 Context)로 per-unit 만으론 opaque service 이름을 못 잡는다. 뱃지/role/환경분포는 per-unit 에 의존하지 않고 `detect_listen_categories(listen_ports)`(listen 소켓을 unit 무관하게 comm/port 로 직접 분류)와 services 이름 분류를 합집합(`workload_category_counter`: 이름 인스턴스 카운트 + 이름이 못 잡은 listen 카테고리 +1, 이중 카운트 회피)한다.
   - server detail 뱃지: `enrich_server_detail` 이 listen-only 카테고리를 unit 없는 합성 `ServiceItem` 으로 추가.
   - 환경요약 role 분포(`build_environment_overview`) · `infer_role`(export): union 카운터 사용.
   - 서버 목록 행 뱃지(`ServerSummary` 경량 partial SELECT, listen_ports 미보유)·보고서(`ReportRowRaw`)는 name 신호만 — 의도적 비대칭(T15).

unknown 발화(미등록 워크로드 별도 노출) UI 는 본 ADR 범위 밖 — 현행 동작(known 뱃지만, union 으로도 전부 미상이면 호스트 단위 unknown 뱃지)을 양 OS 통일 유지(E9 현행 보존).

## Consequences

- 서비스 추가 = 카탈로그 1곳 수정. 드롭다운·범례·뱃지·포트가 자동 동기 — #F9 부담 해소.
- Windows SQL Server·IIS 가 SCM 이름 변형과 무관하게 분류(이름 키워드 + listen union). opaque 한 SCM 이름도 listen 소켓(1433/`sqlservr`)으로 호스트 뱃지·role 에서 구제.
- 잔존 한계(T15): listen 안 하거나 localhost-only 바인드 + opaque 이름 워크로드는 union 두 소스 모두 못 잡아 미상. per-unit services 탭 행은 이름 기반 best-effort(행 단위 정확 귀속은 pid join 부재로 불가). 서버 목록 행 뱃지는 name 신호만(partial SELECT). agent 가 service 에 pid/exe 를 싣게 되면 정확 귀속으로 한계 소멸.
- `matched_ports` 반환·시그니처 불변(회귀 0). 기존 name-only 분류 케이스 비트 보존.
- 커버리지 보강 (본 ADR 후속, 카탈로그 1곳 수정): web/db/cache/mq/monitor 메이저 워크로드 다수 추가 + monitor 표준 포트 등재(prometheus 9090·node_exporter 9100·zabbix 10050/10051·alertmanager 9093). grafana 3000·php-fpm 9000 은 충돌 위험으로 이름 신호만(포트 미등재). cross-category 포트 충돌 0 회귀 테스트가 안전판.
- detail 뱃지 포트 = 카테고리 단위 집계(comm 귀속 + 그 카테고리 listen 포트). IIS `W3SVC`<->`System` 의 80 처럼 comm 귀속 실패한 워크로드 포트도 카테고리 뱃지에 붙음.
- 런타임 스택 카테고리(`CategoryDef.single_instance` — 현재 container)는 호스트당 1로 카운트. docker+containerd 등 1 런타임이 여러 서비스로 떠도 "container 2" 로 부풀리지 않음(`SINGLE_INSTANCE_CATEGORIES` — 카운터·목록 뱃지 category_count·detail 뱃지 일관). web/db 등 일반 카테고리는 인스턴스 카운트 유지.
