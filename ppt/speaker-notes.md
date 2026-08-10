# ZConverter Assessment 발표 요약 노트

## 운영 방식

- 기본 발표 시간: 18~20분
- 축약 발표 시간: 12분
- 확장 발표 상한: 20분
- 본문 슬라이드: 32장, 소제목 표지 5장 포함
- 질문 대응 부록: 섹션 표지 1장과 기술 부록 A1~A11
- 화면 이동: 좌우 방향키, PageUp/PageDown, Home/End

## 1. ZConverter Assessment

- 한 문장: 고객 내부망 서버를 지속 수집하고 공통 규칙으로 평가하는 시스템이다.
- 시각 순서: 제목 -> 부제 -> 2팀과 날짜.
- 기본 발화: 발표의 중심이 기술 구성보다 배경과 사용 사례임을 안내.
- 12분 축약: 두 번째 문장을 생략.
- 20분 확장: 프로젝트가 Engine, Agent, Infra의 3개 저장소로 나뉜다는 점을 짧게 설명.
- 출처: `README.md`, `AGENTS.md` A절.

## 2. 배경과 프로젝트 정의

- 한 문장: 현재 환경 정보의 필요성에서 프로젝트 목표와 제품 역할까지 설명하는 장이다.
- 기본 발화: 장 제목만 읽고 바로 다음 페이지로 이동.

## 3. 기업 연계 과제의 출발점

- 한 문장: 서버 정보와 실사용량을 함께 파악해 클라우드 이전과 재해복구 사양의 근거를 만든다.
- 시각 순서: 고객 서버군 -> 현재 환경 파악 -> Target VM.
- 기본 발화: OS, 명목 사양, 실사용량이 함께 필요하다는 점을 설명.
- 12분 축약: 마지막 문단만 발화.
- 20분 확장: 서버 수가 늘 때 수작업 조사 비용과 오류 가능성이 함께 증가하는 상황을 예시로 설명.
- 출처: `docs/reference/contracts/assessment-api.md`, `docs/explanation/products/json-export.md`.

## 4. 의사결정 정보의 공백

- 한 문장: 자원 구성, 실제 사용량, 변화 추세를 파악하지 않으면 대상 VM 사양 판단이 과소 또는 과다 할당으로 왜곡된다.
- 시각 순서: 위쪽 정보 3가지 -> 중앙 원인 문장 -> 아래쪽 판단 왜곡 2가지.
- 기본 발화: 위쪽 3개 입력 정보와 아래쪽 2개 결과가 별개 층위임을 먼저 설명.
- 12분 축약: 3개 정보와 과소, 과다 할당의 연결만 발화.
- 20분 확장: 자원별 피크, 추세, 포화 신호를 예시로 설명.
- 출처: 기존 발표자료 p4, `docs/reference/right-sizing.md`, `docs/reference/right-sizing-thresholds.md`.

## 5. 프로젝트의 목표

- 한 문장: 수집한 현재 환경을 원하는 범위와 수신자에 맞는 평가 결과로 제공한다.
- 시각 순서: 현재 환경의 사실 3가지 -> 제공 범위 3가지 -> 고객과 엔지니어 수신자.
- 기본 발화: 환경 전체, 선택한 서버 묶음, 개별 서버와 고객, 엔지니어의 두 축을 설명.
- 12분 축약: 중앙 범위와 오른쪽 수신자만 발화.
- 20분 확장: 고객은 결론과 조치, 엔지니어는 근거와 세부 진단을 받는다는 차이를 설명.
- 출처: `README.md`, `docs/reference/contracts/assessment-api.md`, `docs/explanation/products/json-export.md`.

## 6. ZConverter Assessment

- 한 문장: 모니터링 Agent가 전달한 현재 환경 정보를 수집 백엔드 서버가 규칙 기반으로 평가하고 결과로 제공한다.
- 시각 순서: 현재 환경 정보 전달 -> 규칙 기반 평가 -> 결과 전달.
- 기본 발화: 모니터링 Agent, 수집 백엔드 서버, Web Dashboard와 Report, API의 역할을 설명.
- 12분 축약: 3단계 이름과 각 단계의 주체만 발화.
- 20분 확장: Dashboard는 탐색, Report는 고객/엔지니어 공유, API와 JSON Export는 자동화 도구 입력이라는 차이를 설명.
- 출처: `README.md` 운영 산출물, `docs/reference/web/routers.md`.

## 7. Assessment 아키텍처

- 한 문장: 고객 내부망의 모든 Monitoring Agent가 AMQP 메시지를 RabbitMQ에 발행하고, Assessment Engine이 저장, 평가와 결과 제공을 이어서 처리한다.
- 시각 순서: 고객 내부망 경계 -> Agent fleet의 개별 Agent 화살표 -> RabbitMQ -> Consumer -> TimescaleDB -> FastAPI 조회 경로.
- 기본 발화: Agent fleet에서 RabbitMQ로 들어오는 메시지 흐름을 먼저 설명한 뒤, 수집 경로와 조회 및 백그라운드 구성 요소를 설명.
- 12분 축약: Agent fleet -> RabbitMQ -> Consumer -> TimescaleDB와 TimescaleDB -> FastAPI -> Web / REST API만 발화.
- 20분 확장: 본문 18페이지로 이동해 Web, Consumer, Worker의 프로세스 분리와 Redis를 설명.
- 출처: `README.md` 아키텍처, `docs/reference/consumer.md`, `docs/reference/web/layering.md`.

## 8. Assessment 기능

- 한 문장: 현재 환경 정보 수집, 시계열 저장, 자원 적정성 평가가 주 기능이고, 탐색, 공유, 외부 연계, 전환 작업 지원이 파생 기능이다.
- 시각 순서: 주 기능 3개 흐름 -> 파생 기능 4개.
- 기본 발화: 평가로 이어지는 주 기능 흐름을 먼저 설명하고, 현황 탐색과 서비스 분류, 공유, 외부 연계, 전환 작업 지원을 설명.
- 12분 축약: 주 기능 3개와 파생 기능 4개 이름만 발화.
- 20분 확장: API와 JSON Export의 외부 연계, Report의 수신자별 깊이를 설명.
- 출처: `README.md`, `docs/reference/web/routers.md`, `docs/explanation/products/install-task.md`.

## 9. 현장 조건과 검증

- 한 문장: 불확실한 현장 조건을 검증 환경으로 옮기고 수집 경로를 확인하는 장이다.
- 기본 발화: 장의 흐름만 안내하고 바로 다음 페이지로 이동.

## 10. 현장 조건

- 한 문장: OS coverage와 Native Agent는 확정된 조건이고, 내부망 플랫폼과 접근성은 현장 확인 사항이며 배포 경로는 그 조건에서 도출한다.
- 시각 순서: 확정된 조건 2개 -> 현장 확인 사항 2개 -> 도출한 배포 경로.
- 기본 발화: CentOS와 RHEL 6부터 현대 Linux, Windows Server 2003 SP1부터 현대 버전까지 OS별 C 기반 단일 Native binary Agent를 실행하는 OS coverage는 요구로 확정하고, 물리 환경, public cloud, OpenStack private cloud 여부 및 인터넷과 SSH 접근성은 현장 확인 사항임을 설명. GitHub 직접 다운로드는 배제하고 SSH, Ansible, offline injection을 검증했다고 설명.
- 12분 축약: OS coverage, 플랫폼 미확정, GitHub 직접 다운로드 제외만 발화.
- 출처: `assessment-agent-temp/deploy/SUPPORTED_OS.md`, 검증 환경 구성 기록.

## 11. 검증 환경 구성

- 한 문장: 현장 조건에 대응할 가용 자원인 OpenStack private cloud로 69대 혼합 OS 서버 fleet과 단일 Engine VM을 구성했다.
- 시각 순서: 현장 조건 -> 가용 자원 OpenStack -> OpenStack fleet -> assessment-router -> 단일 Engine VM.
- 기본 발화: 현장 조건에서 바로 시험대를 가정하지 않고, 가용한 OpenStack 자원으로 이를 구성했다는 선택 근거를 먼저 설명. 다이어그램 아래 OpenStack 관리 계층에서 Horizon dashboard 또는 API client가 Nova와 Neutron control plane을 조작하고, Compute hypervisor와 virtual network가 Agent fleet, Router, Engine VM 전체를 제공한다는 점을 설명. Linux 58대와 Windows 11대의 각 VM에 C 기반 Native Agent binary가 설치됐음을 명시한 뒤 7개 subnet과 메시지 경로를 설명. Agent는 SSH, Ansible, offline injection으로 검증하고, Engine stack은 반입과 기동이 간단한 Docker Compose로 구성했다고 설명.
- 12분 축약: OpenStack, 69대, 7개 subnet, 단일 Engine VM만 발화.
- 출처: OpenStack Horizon 화면, 검증 환경 구성 기록.

## 12. 검증 환경 접근 경로

- 한 문장: Windows jump host의 RDP 접근 권한으로 Linux control VM을 1회 만든 뒤, 이를 VPN SSH와 subnet routing의 제어 지점으로 사용해 API와 Horizon으로 같은 VM fleet을 제어했다.
- 시각 순서: 제공 조건 RDP -> Windows jump host -> Horizon browser -> 1회 Linux control VM 생성, 반복 흐름 원격 작업 환경 -> VPN -> Linux control VM -> SSH 또는 subnet routing 기반 Horizon -> OpenStack VM fleet.
- 기본 발화: Horizon 접근용 Windows jump host RDP 권한이 제공 조건이었고, Horizon browser에서 Linux control VM을 1회 생성했다고 설명. 이후 Linux control VM은 VPN SSH와 subnet routing의 제어 지점이며 VS Code Remote를 이용한 API 호출과 원격 개발 환경이라고 설명. OpenStack API 호출과 Horizon은 여러 Agent VM과 단일 Engine VM으로 이루어진 같은 VM fleet을 생성 및 관리하는 수단임을 설명.
- 12분 축약: Windows jump host RDP, SSH, API와 Horizon의 공통 VM fleet 제어만 발화.
- 출처: 검증 환경 구성 기록.

## 13. OpenStack Horizon과 검증 fleet

- 한 문장: OpenStack Horizon의 인스턴스 목록에서 혼합 OS와 Windows Server 세대별 검증 대상을 확인했다.
- 시각 순서: 혼합 OS fleet -> Windows Server 세대별 fleet.
- 기본 발화: 11페이지 구성도를 실제 Horizon 화면으로 이어, Linux, Windows, Engine VM과 Windows Server 2003부터 현대 버전까지의 검증 대상을 설명.
- 12분 축약: 제목과 혼합 OS, Windows Server 세대별 대상만 발화.
- 출처: `ppt/capture-20260726/스크린샷 2026-07-26 오후 2.28.29.png`, `ppt/capture-20260726/스크린샷 2026-07-26 오후 2.30.06.png`.

## 14. 검증 환경 재현

- 한 문장: Terraform -> OpenStack API가 검증 환경을 생성하고, cloud-init 또는 offline injection으로 Agent를 준비한다.
- 시각 순서: Terraform -> OpenStack API -> Agent fleet VM, Network, Engine VM, Agent 설치와 초기 준비에서 cloud-init OR offline injection.
- 기본 발화: Terraform이 Agent fleet VM, 네트워크, 단일 Engine VM을 선언하고 OpenStack API가 이를 생성하는 주 흐름이라고 설명. Agent 설치와 초기 준비는 대상 조건에 따라 cloud-init 또는 offline injection 중 하나를 사용한다고 설명. cloud-init은 Linux Agent fleet 준비를 자동화하고, offline injection은 rescue volume attach와 Windows registry hive를 포함하는 Agent 주입 방식이다.
- 12분 축약: Terraform으로 환경 재현, cloud-init과 offline injection만 발화.
- 출처: 검증 환경 구성 기록.

## 15. Agent-Engine 배포 및 수집 경로 검증

- 한 문장: Agent는 SSH, Ansible, offline injection으로, Engine은 단일 VM의 Docker Compose로 배포한 뒤 Agent service부터 TimescaleDB 저장까지 확인했다.
- 시각 순서: Agent 배포와 Engine 배포 -> Agent service -> RabbitMQ -> Consumer -> TimescaleDB -> Engine 등록 -> 검증 경계.
- 기본 발화: Linux와 Windows Agent service 기동, 단일 Engine VM의 Web, Consumer, Worker와 RabbitMQ, Redis, TimescaleDB 기동, inventory와 metrics 메시지 발행 및 DB 저장을 설명. 판정 정확도, 장기 운영 안정성, 고객 운영 배포 전체는 범위가 아니라고 구분.
- 12분 축약: Agent 배포, Docker Compose Engine, DB 저장만 발화.
- 출처: 검증 환경 구성 기록.

## 16. Agent와 Engine

- 한 문장: Agent의 관측과 Engine의 처리 책임을 컴포넌트와 흐름으로 설명하는 장이다.
- 기본 발화: Agent와 Engine의 책임 경계를 보겠다고 안내하고 바로 다음 페이지로 이동.

## 17. Agent 동작

- 한 문장: Monitoring Agent는 Linux 파일과 OS 제공 명령 또는 Windows API를 이용해 공통 메시지로 정규화하고 RabbitMQ에 원시 관측값만 발행한다.
- 시각 순서: Linux 파일과 서비스 정보 보완, Windows API 기반 수집 -> 공통 정규화 -> RabbitMQ -> inventory, metrics, envelope 구조.
- 기본 발화: Linux는 CPU, 메모리, 디스크, 네트워크 측정값을 `/proc`, `/sys`, `/etc`에서 직접 파싱하고, 서비스 목록과 일부 식별 정보는 systemctl 및 메타데이터 조회로 보완한다고 설명. Windows는 registry, Windows API, IOCTL, 성능 계측값을 질의하며 단일 i686 binary가 런타임 세대를 분기한다고 설명. Agent는 Engine과의 스키마 계약에 맞춰 공통 단위로 정규화하고, rate, utilization, 평가는 계산하지 않은 inventory 및 raw metrics를 RabbitMQ로 발행한다고 설명.
- 12분 축약: Linux 파일, Windows API, raw 관측값 RabbitMQ 발행만 발화.
- 출처: 관련 저장소 `assessment-agent-temp`의 `docs/architecture.md`.

## 18. Engine 컴포넌트 구조

- 한 문장: RabbitMQ와 TimescaleDB를 중심으로 수집, 평가, background 작업, 보조 상태의 책임을 컴포넌트별로 분리한다.
- 시각 순서: Monitoring Agent와 RabbitMQ 관계 -> RabbitMQ -> Consumer -> TimescaleDB -> FastAPI -> 사용자와 연계 도구. Worker와 Redis는 각각 TimescaleDB와 FastAPI에 연결.
- 기본 발화: Agent의 server 이벤트 발행과 task 수신, RabbitMQ의 routing, Consumer의 schema/idempotency/DLQ, TimescaleDB source of truth, FastAPI와 Worker, Redis 역할을 설명.
- 12분 축약: Agent, RabbitMQ, Consumer, DB, FastAPI의 관계만 발화.
- 출처: `README.md`, `docs/reference/consumer.md`, `docs/reference/redis.md`.

## 19. 수집 메시지 처리 시퀀스

- 한 문장: Agent가 발행한 수집 메시지 1건은 RabbitMQ, Consumer와 TimescaleDB를 거쳐 저장되고 정상 처리 뒤 ACK 된다.
- 시각 순서: Agent 발행 -> RabbitMQ 전달 -> Consumer schema 검증 및 중복 처리 확인 -> DB 저장 -> ACK. 형식 오류 또는 재시도 한도 초과는 DLQ 분기.
- 기본 발화: 정상 수집 경로만 설명하고, Consumer가 DB 저장을 끝낸 뒤 ACK 한다는 순서를 강조. 설치 작업과 보고서 생성은 뒤의 별도 페이지가 담당한다고 안내.
- 12분 축약: Agent 발행, RabbitMQ 전달, Consumer 저장, ACK과 DLQ 분기만 발화.
- 출처: `docs/reference/consumer.md`, `docs/reference/rabbitmq.md`.

## 20. Engine 아키텍처의 특징

- 한 문장: Broker 수집, DB 기반 background job, 영속 데이터와 보조 상태의 분리로 Engine의 처리 지연과 장애 범위를 제한한다.
- 시각 순서: RabbitMQ durable queue -> FastAPI, DB Job, Worker -> TimescaleDB 영속 데이터와 Redis 보조 상태.
- 기본 발화: RabbitMQ가 Consumer 재시작과 처리 속도 차이를 흡수하는 구조, FastAPI가 등록한 DB Job을 Worker가 가져와 실행하는 구조, TimescaleDB source of truth와 Redis fail-open 보조 상태를 설명.
- 12분 축약: 3개 설계 선택의 제목과 효과만 발화.
- 출처: `README.md`, `docs/explanation/products/environment-report.md`, `docs/reference/redis.md`.

## 21. 핵심 기능 구현

- 한 문장: 환경과 서버 현황, 평가 규칙, 비동기 보고서, 제한된 설치 작업이라는 구현 핵심 4개를 설명하는 장이다.
- 기본 발화: 4개 키워드만 읽고 바로 다음 페이지로 이동.

## 22. 환경과 서버 현황 조회

- 한 문장: 같은 인벤토리와 메트릭을 환경 전체 집계와 개별 서버 상세라는 2개 범위로 제공한다.
- 시각 순서: 환경 전체의 규모와 분포 -> 개별 서버의 구성과 자원 상태 -> 환경 집계에서 서버 상세로 내려가는 조사 흐름.
- 기본 발화: 환경에서는 서버와 자원 합계, OS와 워크로드, 실시간 및 기간 메트릭을 집계하고 서버에서는 시스템, 스토리지, 네트워크, 서비스와 포트 및 메트릭을 상세히 확인한다고 설명.
- 12분 축약: 환경 집계와 서버 상세가 같은 수집 데이터의 서로 다른 조회 범위라는 사실만 발화.
- 화면 출처: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.23.21.png`, `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.25.48.png`.
- 출처: `docs/reference/web/routers.md`, `docs/reference/web/services.md`, `docs/reference/web/view-models.md`.

## 23. 자원 적정성 평가

- 한 문장: 5개 자원의 이용률, 포화, 오류 신호를 15개 영역에서 관측하고 사이징, 상태와 오류 진단으로 구분한다.
- 시각 순서: 5개 자원 x 3개 평가 축 -> 신호 성격별 결과 -> 서버 단위 종합 -> 평가 전제 5개.
- 기본 발화: 15개 관측 영역의 대표 신호, 사이징 판정과 성능 및 품질 상태 및 오류 진단의 구분, 서버 단위 종합, 기간 대표값, OS별 신호 해석, 오탐 방지 조건, 미측정 신뢰도, 안전 우선 처방을 설명.
- 12분 축약: 5개 자원 x 3개 축과 서버별 판정 및 처방만 발화.
- 화면 출처: `ppt/deck/assets/screenshots/environment-assessment.png`.
- 출처: `docs/reference/right-sizing.md`, `docs/reference/right-sizing-thresholds.md`.

## 24. 환경 자원 평가 화면

- 한 문장: 환경 분포와 서버별 분류, 권고, 상태 및 신뢰도를 실제 웹페이지에서 확인한다.
- 시각 순서: 평가 대상과 기간 -> 환경 전체 분포 -> 서버별 자원 적정성 표 -> 하단 안내 3개.
- 기본 발화: 23페이지의 관측 영역이 사이징 판정, 성능 및 품질 상태와 오류 진단으로 구분되어 화면에 제공된 결과임을 설명.
- 12분 축약: 환경 분포와 서버별 분류 및 권고만 발화.
- 화면 출처: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.23.40.png` 원본.

## 25. 보고서 스냅샷 생성

- 한 문장: 여러 서버의 집계와 보고서 조립을 HTTP 요청에서 분리하고 생성 상태와 발행 결과를 DB에 보존한다.
- 시각 순서: 비동기 처리, DB job 상태, 정적 스냅샷 -> 발행 요청 -> pending 등록 -> 보고서 생성 -> 완료 결과 조회 -> 중복 생성 방지와 정적 결과 보존.
- 기본 발화: 비동기 처리는 요청과 생성을 분리하고, DB job 상태는 진행과 실패를 추적하며, 정적 스냅샷은 발행 결과를 고정한다는 책임 구분을 설명.
- 12분 축약: 응답과 생성 분리, pending -> running -> succeeded 또는 failed, 결과 JSON 보존만 발화.
- 20분 확장: 부록 A6으로 이동해 중복 발행과 다중 서버 처리의 상태 계약만 답변.
- 출처: `docs/explanation/products/environment-report.md`, `docs/explanation/products/server-report.md`.

## 26. 보고서 유형과 용도

- 한 문장: 같은 평가 결과를 환경 또는 개별 서버 범위와 고객 또는 엔지니어 독자에 맞춰 4가지 보고서로 구성한다.
- 시각 순서: Customer와 Engineer 열 -> Environment 행 -> Server 행 -> 하단 구현 계약.
- 고객용 환경 화면: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.25.23.png` 원본.
- 엔지니어용 환경 화면: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.24.56.png` 원본의 환경 부하 추이와 자원 적정성 영역.
- 고객용 개별 화면: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.25.09.png` 원본.
- 엔지니어용 개별 화면: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.24.34.png` 원본의 CPU와 메모리 정량 통계 영역.
- 기본 발화: 환경과 개별 서버의 범위 차이, 고객용의 결론 중심 구성, 엔지니어용이 같은 평가 결과를 바탕으로 상세 메트릭, 시스템 구성과 판정 근거를 추가한다는 관계를 설명.
- 12분 축약: 범위 2개 x 독자 2개와 같은 계산을 공유한다는 사실만 발화.
- 출처: `docs/explanation/products/environment-report.md`, `docs/explanation/products/server-report.md`.

## 27. ZDM 설치 작업과 결과 회신

- 한 문장: 서버 목록에서 대상을 선택해 ZDM 설치를 발행하고 Agent의 실행 결과를 서버별로 저장한다.
- 시각 순서: 체크박스 대상 선택 -> Install 버튼 -> Engine 작업 발행 -> RabbitMQ 서버별 전달 -> Agent 검증 및 설치 -> Engine 결과 저장.
- 기본 발화: 실제 서버 목록에서 일괄 작업을 시작하고 Agent의 결과가 다시 Engine에 저장되는 흐름을 설명.
- 12분 축약: 대상 선택, Install 발행, Agent 실행과 결과 저장만 발화.
- 출처: `docs/explanation/products/install-task.md`, `docs/reference/web/routers.md`.

## 28. ZDM 설치 상태 확인

- 한 문장: 서버 목록의 상태 배지에서 결과를 확인하고 상세 화면에서 종료 정보와 로그 tail까지 추적한다.
- 시각 순서: 실제 ZDM Install 칼럼 -> 진행 중, 성공, 실패 상태 -> 상세 결과 확인 항목.
- 화면 출처: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.23.33.png`.
- 기본 발화: 목록 polling과 상세 결과의 발행 및 완료 시각, 소요 시간, 종료 정보, 실패 사유를 설명.
- 12분 축약: 배지 자동 갱신과 실패 상세 확인만 발화.
- 출처: `docs/explanation/products/install-task.md`, `docs/reference/web/routers.md`.

## 29. 유즈케이스와 결과

- 한 문장: 수집과 평가 결과가 환경 진단 및 조치 우선순위, 클라우드 이전 및 재해복구 설계에 쓰이는 2가지 흐름을 설명하는 장이다.
- 기본 발화: 환경 진단과 이전 및 재해복구만 읽고 바로 다음 페이지로 이동.

## 30. 환경 진단과 조치 우선순위

- 한 문장: 환경 수준에서 조치 후보를 정한 뒤 필요한 서버의 근거만 상세 확인한다.
- 시각 순서: 왼쪽 4단계 -> 오른쪽 실제 평가 표 -> 하단 결론.
- 화면 출처: `ppt/capture-20260726/스크린샷 2026-07-26 오후 3.23.40.png`.
- 화면 범위: 전역 header와 총 70 수치를 제외한 서버별 평가 표.
- 기본 발화: 부족, 과다 할당, 유휴, 정상, 표본 부족과 조치 방향을 설명.
- 12분 축약: 4단계를 빠르게 읽고 상세 자원 예시는 생략.
- 20분 확장: 부록 A4와 A5로 이동해 5개 평가 영역과 보수적 다운사이즈 조건을 설명.
- 출처: `docs/explanation/products/dashboard.md`, `docs/reference/right-sizing.md`.

## 31. 클라우드 이전 및 재해복구 설계

- 한 문장: API와 Export는 개별 서버의 규칙 기반 분류와 실사용량을 클라우드 이전과 재해복구 설계 근거로 제공한다.
- 시각 순서: 원천 환경 -> JSON 계약 -> 대상 설계 -> 책임 경계.
- 기본 발화: Engine은 설계 근거까지만 제공하고 VM 생성과 실제 클라우드 이전은 클라우드 이전 및 재해복구 솔루션 책임임을 명시.
- 12분 축약: API와 Export의 차이는 생략하고 책임 경계만 발화.
- 20분 확장: 부록 A7로 이동해 API 직접 조회와 JSON Export 파일 전달의 선택 기준을 설명.
- 출처: `docs/reference/contracts/assessment-api.md`, `docs/explanation/products/json-export.md`.

## 32. ZConverter Assessment 시스템이 만드는 결과

- 한 문장: ZConverter Assessment는 같은 현재 환경 정보를 운영, 마이그레이션, 자동화 도구에 각기 다른 결과로 제공한다.
- 시각 순서: 현재 환경 정보 -> 운영 판단, 설계 근거, 자동화 입력 -> 업무별 재사용.
- 기본 발화: 운영 담당자는 Dashboard와 Report로 판단하고, 마이그레이션 설계자는 VM 사양과 구성안의 근거로 쓰며, 자동화 도구는 Assessment API와 JSON Export를 입력으로 쓴다는 점을 강조.
- 12분 축약: 현재 환경 정보와 운영, 마이그레이션, 자동화 도구 카드만 읽고 질문으로 전환.
- 20분 확장: 확장하지 않고 질문으로 전환.
- 출처: 본문 2~31장의 종합.

## 기술 부록

- 목적: 본문에서 소개한 업무 흐름을 반복하지 않고, 질문에 필요한 구현 계약과 한계를 설명한다.
- 구성: Agent와 OS 지원, 메시지 계약, 저장과 중복 방지, 평가 기준, 보고서 동시성, API와 Export, ZDM 작업, 릴리스와 배포, 시험대, 적용 범위와 한계.

## 질문별 부록 이동

| 질문 | 부록 |
|---|---|
| 전체 아키텍처 | 본문 7, 18, 19 |
| Agent 설치와 OS 지원 | A1, A10 |
| 메시지 형식과 RabbitMQ 흐름 | A2 |
| 중복 메시지와 DB 저장 | A3 |
| 자원 평가 기준 | A4, A5 |
| 보고서 생성 방식 | A6 |
| Assessment API와 Export | A7 |
| ZDM 설치 보안과 상태 | A8 |
| CI, 이미지, 배포 절차 | A9 |
| 현재 한계 | A11 |
