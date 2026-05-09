# 작업 등록 및 Agent 워크플로우

## 개요

대시보드에서 서버를 선택하고 ZConverter Install 작업을 등록하면,
Agent가 주기적으로 task 존재 여부를 확인하여 자동으로 설치를 수행하는 구조다.

---

## 워크플로우 단계

### 1. Web UI — 작업 등록 진입점
- 서버 목록에서 체크박스로 대상 서버를 1개 이상 선택
- `ZConverter Install` 버튼 클릭
- 모달에서 ZDM 주소(IPv4 형식) 입력 후 확인
- 프론트엔드는 선택 서버 목록과 ZDM IP를 payload로 구성하여 `POST /api/tasks/install` 호출

payload 구조:
```json
[
  {
    "target_server": "db-server-01",
    "task_type": "zconverter_install",
    "zdm_ip": "192.168.0.3",
    "status": "pending"
  }
]
```

### 2. FastAPI — task 생성 및 저장
- 요청 수신 후 선택 서버별로 task 레코드 생성
- DB: 작업 이력 저장 (영속성)
- Redis: pending task 등록 (Agent 폴링 대상)

### 3. Redis — pending task 관리
- 서버 hostname 기준으로 pending task를 키로 등록
- Agent가 주기적으로 자신의 hostname에 해당하는 task를 조회
- task 실행 완료 후 상태 업데이트

### 4. Agent — 주기적 폴링 및 task 확인
Agent는 두 가지 역할을 주기적으로 수행한다:

1. metrics 수집 → MQ(Message Queue)로 publish
2. `HTTP GET /api/tasks/{hostname}` 호출 → pending task 존재 여부 확인

task가 없으면 다음 주기까지 대기.
task가 있으면 5단계 실행으로 진입.

### 5. Execute & Report — 설치 실행 및 결과 보고
task 확인 시 다음 순서로 실행:
```
curl {zdm_ip}/zconverter.tar.gz
tar -xzf zconverter.tar.gz
bash install.sh
```

실행 결과는 다음 중 하나로 보고:
- FastAPI 엔드포인트로 HTTP POST
- MQ로 결과 메시지 publish

---

## 전체 흐름 요약

```
Web UI
  → POST /api/tasks/install
FastAPI
  → DB (이력 저장)
  → Redis (pending task 등록)
Agent (주기 실행)
  → HTTP GET으로 task 확인
  → task 있음: curl → tar → install.sh 실행
  → 결과를 FastAPI 또는 MQ로 보고
```

---

## 설계 전제

- Agent는 서버에 상주하며 pull 방식으로 task를 확인한다 (push가 아님)
- Agent가 metrics 전송과 task 확인을 같은 주기에서 수행한다
- Redis는 task의 실시간 상태 관리, DB는 이력 영속성 담당으로 역할이 분리된다
- ZDM 주소는 task 등록 시점에 지정되며 Agent가 설치 스크립트 다운로드에 사용한다
