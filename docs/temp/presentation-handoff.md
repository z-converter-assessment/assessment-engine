임시 발표자료 핸드오프 메모. 삭제 자유.

# ZConverter Assessment 발표자료 핸드오프

## 현재 맥락

- 대상: 프로그래밍 경험이 있는 청중에게 20분 이내로 ZConverter Assessment의 배경, 목적, 사용 사례를 설명하는 발표자료
- 우선순위: 기술 상세 토론보다 배경, 제품 성격, 실제 사용 사례를 먼저 이해시키는 구성
- 생성 산출물: `ppt/deck/index.html`, `ppt/deck/styles.css`, `ppt/speaker-script.md`, `ppt/ZConverter_Assessment_2team.pdf`
- 생성 명령: `node ppt/deck/render.mjs`
- 현재 렌더 결과: 본문 32장과 부록 섹션 표지 1장 및 기술 부록 11장, 총 44장. 폰트 로드, 외부 요청, 화면 넘침, PDF 페이지 수 검증 완료

## 본문 장 구성

| 장 | 페이지 | 전달할 내용 |
|---|---|---|
| 표지 | 1 | ZConverter Assessment와 발표 범위 |
| 01 배경과 프로젝트 정의 | 2~8 | 현재 환경 정보의 필요성, 문제, 목표, 제품 역할과 기능 |
| 02 현장 조건과 검증 | 9~15 | 현장 조건, OpenStack 검증 환경, 접근과 재현, 배포 및 수집 경로 검증 |
| 03 Agent와 Engine | 16~20 | Agent 동작, Engine 컴포넌트와 업무 흐름, 아키텍처 특징 |
| 04 핵심 기능 구현 | 21~28 | 환경과 서버 현황, 자원 적정성 평가, 비동기 보고서 스냅샷과 ZDM 설치 작업 |
| 05 유즈케이스와 결과 | 29~32 | 환경 진단과 조치 우선순위, 클라우드 이전 및 재해복구 설계, 프로젝트 결과 |

## 용어 규칙

- 제품명: ZConverter Assessment
- 제품 전체 구조: Assessment 아키텍처
- 수집 백엔드의 구성 영역: Assessment Engine
- 수집 대상 서버 집합: Agent fleet
- 서버에 설치되는 수집기: Monitoring Agent
- Agent가 전달하는 데이터: 현재 환경 정보
- 5페이지는 구현체명을 최소화한 제품 역할 소개. 6페이지부터 RabbitMQ, Consumer 등 구현체와 Assessment Engine을 소개

## 장별 상세

소제목 표지 5장은 장의 경계를 표시하며 발화는 장의 목적을 안내하는 1문장으로 제한한다.

- 10페이지 현장 조건: OS coverage와 C 기반 단일 Native Agent는 확정된 조건. 내부망 플랫폼, 인터넷과 SSH 접근성은 현장 확인 사항
- 11페이지 검증 환경 구성: Linux 58대와 Windows 11대의 Agent fleet, 단일 Engine VM, 7개 subnet, Router를 OpenStack private cloud에 구성
- 12페이지 검증 환경 접근 경로: Windows jump host RDP 권한에서 Linux control VM, VPN SSH, subnet routing, OpenStack API와 Horizon으로 확장
- 13페이지 OpenStack Horizon과 검증 fleet: 혼합 OS fleet과 Windows Server 세대별 Horizon 인스턴스 목록
- 14페이지 Agent-Engine 배포 및 수집 경로 검증: 인터넷 연결을 전제하지 않고 SSH, Ansible, offline injection으로 Agent를 배포하며, 수집 서버의 Docker Compose Engine에서 Agent service -> RabbitMQ -> Consumer -> TimescaleDB 저장 확인. USB 직접 주입은 검증 범위에서 제외
- 15페이지 검증 환경 재현: Terraform -> OpenStack API로 같은 VM 환경을 반복 생성하고, 최신 Linux는 cloud-init, 레거시 Linux와 Windows는 offline injection으로 초기 준비
- 17페이지 Agent 동작: Linux의 커널 및 시스템 파일 직접 파싱과 서비스 정보 OS 명령 보완, Windows native API 질의, 공통 단위 정규화와 inventory, raw metrics, envelope 발행
- 기술 부록 A1 Agent 수집과 OS 지원 범위: Linux x86_64 musl static과 Windows i686 단일 바이너리 2종, SSH, Ansible, offline 배포 경로를 함께 표시
- 18페이지 Engine 컴포넌트 구조: Monitoring Agent, RabbitMQ, Consumer, TimescaleDB, FastAPI, Worker, Redis, 사용자와 연계 도구의 기준 관계
- 19페이지 수집 메시지 처리 시퀀스: Agent가 발행한 inventory 또는 raw metrics 메시지 1건이 RabbitMQ 전달, Consumer schema 검증, Redis SET NX(키가 없을 때만 저장) 1차 중복 차단, TimescaleDB 저장, ACK으로 끝나는 정상 경로와 DLQ 예외 분기를 표시. Redis 장애 시 DB UNIQUE 제약으로 중복을 차단하는 fail-open 보조 경로를 함께 표시. 설치와 보고서 흐름은 각각 25, 27페이지에서 설명
- 20페이지 Engine 아키텍처의 특징: Broker 수집, DB 기반 background job, 영속 데이터와 Redis fail-open 보조 상태 분리
- 기술 부록 A3 데이터 저장과 중복 방지: Redis marker가 DB commit보다 먼저 생성되므로 그 사이 Consumer가 중단되면 재전송이 차단돼 DB 저장이 0회로 끝날 수 있는 한계를 함께 표시
- 기술 부록 A4 5개 자원 평가 영역: CPU MCE, 메모리 OOM과 손상, 파일시스템과 디스크 오류, Linux device %util과 Windows IOCTL 응답시간, 네트워크 drop, conntrack, 재전송과 NIC 오류를 USE 축에 맞게 배치
- 기술 부록 A6 보고서 작업의 동시성 제어: 범위와 입력 조건으로 hash를 계산하고 scope, hash, 보고서 유형의 active UNIQUE로 진행 중 작업 중복을 차단
- 기술 부록 A9 Engine과 Agent 릴리스 및 배포: Engine은 OCI 이미지로 VM에 배포하고 Agent는 Linux와 Windows 바이너리 2종을 태그 릴리스로 게시. Agent 릴리스는 두 메시지 계약 검증과 SHA-256 체크섬을 포함
- 기술 부록 A10 OpenStack 69대 시험대: Agent 배포, 서비스 기동, inventory와 metrics의 Engine 등록까지 확인한 범위를 긍정형 문장으로 표시
- 22페이지 환경과 서버 현황 조회: 실제 환경 개요와 서버 상세 화면을 나란히 배치. 환경 범위의 서버 및 자원 합계, OS 및 워크로드 분포, 메트릭 집계와 서버 범위의 시스템, 스토리지, 네트워크, 서비스 및 포트, 메트릭 상세를 비교
- 23페이지 자원 적정성 평가: USE Method의 이용률(Utilization), 포화(Saturation), 오류 신호(Errors) 3가지를 5개 자원에 적용해 15개 평가 영역을 만든다는 구조를 설명. 세부 측정값 표 대신 사양 권고, 성능 상태, 오류 진단의 역할 분리와 사양 권고를 서버 단위 처방으로 종합하는 흐름을 표시. 기간 기반 평가, OS 차이 해석, 보수적 감축의 안전 장치를 함께 표시
- 24페이지 환경 자원 평가 화면: 검증 환경 69대에 촬영 당시 별도 테스트 VM 1대가 추가되어 70개 서버가 표시된 화면에서 평가 대상과 기간, 환경 분포, 서버별 분류, 권고, 네트워크와 디스크 I/O 상태, 신뢰도를 한 페이지에 크게 노출
- 25페이지 보고서 스냅샷 생성: 비동기 처리는 요청과 생성을 분리하고, DB job 상태는 진행과 실패를 추적하며, 정적 스냅샷은 발행 결과를 고정하는 구조. FastAPI가 pending job을 등록하고 Worker가 대기 중인 작업을 가져와 결과 JSON을 보존. pending, running, succeeded, failed 상태와 진행 중 job 중복 방지 표시
- 26페이지 보고서 유형과 용도: 환경과 개별 서버 범위, 고객과 엔지니어 독자의 2 x 2 조합을 3420 x 2224 원본에서 만든 1760 x 550 확대 crop 4개로 비교. 엔지니어용은 같은 평가 결과를 바탕으로 상세 메트릭, 시스템 구성과 판정 근거를 추가하는 관계
- 27페이지 ZDM 설치 작업과 결과 회신: 실제 서버 목록에서 체크박스로 대상을 선택하고 Install 버튼으로 발행하는 화면을 강조. task.install 서버별 전달, Agent의 package 검증과 task.result 결과 저장 흐름 표시
- 28페이지 ZDM 설치 상태 확인: 실제 서버 목록의 ZDM Install 성공 배지를 확대하고 진행 중, 성공, 실패 상태와 상세 결과 확인 항목을 설명
- 29페이지 유즈케이스와 결과: 환경 진단과 조치 우선순위, 클라우드 이전 및 재해복구 설계, 프로젝트 결과의 2개 업무 흐름과 결과 구분
- 30페이지 환경 진단과 조치 우선순위: 환경 평가에서 조치 후보를 선별하고 필요한 서버 근거까지 확인
- 31페이지 클라우드 이전 및 재해복구 설계: 현재 서버 정보와 규칙 기반 평가를 API/JSON Export로 외부 설계 도구의 입력에 제공하고 Engine과 외부 도구의 책임 경계 표시
- 32페이지 ZConverter Assessment 시스템이 만드는 결과: 하나의 현재 환경 정보가 운영 판단, 마이그레이션 설계 근거, 자동화 도구 입력으로 재사용되는 구조
- 기술 부록 표지: A1부터 A11까지의 주제와 페이지 번호를 2열 인덱스로 제공. 질문이 나오면 인덱스에서 해당 부록으로 바로 이동
- 기술 부록 A11 판정 기준과 처방 방식: CPU, 메모리, 디스크 용량의 대표 부족 기준과 목표 사양, 충분한 이력에서의 디스크 365일 목표, Linux device %util과 Windows IOCTL 응답시간의 디스크 I/O 판정, 네트워크 트래픽 게이트와 다운사이즈 게이트를 요약

## 이후 개선 방향

- 10~28페이지의 발화 시간을 합쳐 10분 이내로 유지
- 사용자 피드백에 따라 Agent와 Engine 설명의 정보 밀도 및 시각 구성을 조정
- 사용 사례와 프로젝트 결과는 30~32페이지에 위치. 본문은 32장이고, 기술 부록은 섹션 표지 1장과 질문 대응 11장으로 구성해 총 44장

## 주의 사항

- 표지와 푸터 제품명은 `ZConverter Assessment`. `ZConverter Cloud Assessment`는 사용하지 않음
- 7페이지 제목은 `Assessment 아키텍처`. `Assessment Engine 아키텍처`로 쓰지 않음
- 7페이지의 주 실행 흐름은 `Agent fleet -> RabbitMQ -> Consumer -> TimescaleDB`. 조회 흐름은 `TimescaleDB -> FastAPI -> Web / REST API`
- 7페이지에 ZDM 설치 제어 흐름을 넣지 않음
- 8페이지는 주 기능과 파생 기능의 논리적 분류를 유지. JSON Export는 외부 연계에 포함
- `docs/guides/local-dev.md`는 사용자의 별도 변경 사항이므로 건드리지 않음
