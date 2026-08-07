# JSON Export

JSON Export 산출물의 존재 의의를 담는다. 응답 구조·필드·단위·불변식·버전 규약 정본은 `docs/reference/contracts/assessment-api.md`(얼어붙은 계약)다.

## 위치

- UI 진입점 — 서버 목록 페이지에서 N대를 선택하고 Export 버튼을 누르면 선택한 서버만 필터로 실려 나간다.
- API — `POST /api/exports/inventory` (요청 body 가 필터, 응답이 다운로드용 JSON).
- 산출물은 `GET /api/assessment` 와 같은 envelope 이다. 같은 매퍼가 만들고 같은 계약을 따르며, 다른 것은 전달 방식뿐이다.

## 존재 의의

`/api/assessment` 는 재해복구·마이그레이션 소비자가 HTTP 로 실시간 소비하는 계약이다. Export 는 같은 계약을 파일로 떨군다 — 소비 도구가 엔진에 직접 접속하지 않는 상황을 위해서다.

- 오프라인·에어갭 자동화: 소스 관리망과 분리된 환경에서 Terraform/Ansible/CSP SDK 를 돌릴 때, 운영자가 파일을 받아 옮겨 입력한다.
- 스냅샷 보관: 마이그레이션 착수 시점의 소스 상태를 파일로 고정해 감사·재현에 남긴다.

실시간 질의는 GET 으로, 들고 나갈 때는 파일로 받되 데이터는 같다. 별도 스키마가 아니라 같은 계약의 전달 모드 하나다.

## 계약과의 관계

- 스키마·필드·단위·불변식·버전은 `docs/reference/contracts/assessment-api.md` 가 단일 진실이다. Export 는 그 계약 8절(같은 데이터, 파일 전달)과 10절(운영 계약: 무인증·pagination 없음·필터 스코프)을 그대로 따른다.
- 필터도 GET 과 동일하다(계약 3절). 요청만 query 대신 body 배열이다.

## 사람용 보고서와의 분기

| 항목 | JSON Export | 보고서 (환경/서버) |
|------|------------|------------------|
| 형식 | JSON (machine-readable) | HTML SSR (human-readable) |
| 의도 | 자동화 도구 입력 | 운영자·고객 의사결정 |
| 가공 | 재현 팩트 + 사이징 axes[] (계약 그대로) | KPI·위험도·진단 텍스트 |

같은 데이터 source 지만 산출 형태와 수신자가 다르다. 보고서는 사람이 읽고 Export 는 도구가 받는다.

## 한계

- PII·secret: envelope 에 hostname 과 internal IP 가 담긴다. 외부로 들고 나갈 때의 sanitize 는 수신 인프라 책임이고, 엔진은 계약 데이터를 그대로 낸다 (#F8 은 로그·캐시 대상이며 명시적 export 는 계약 산출물이라 별개다).
- 스냅샷 1회성: 발행 시점 상태다. 시계열 분포는 차트 endpoint 가 따로 있다.
- 무인증: 관리망 전용 전제다 (계약 10절, tradeoffs T19). 외부 노출 시에는 앞단 인증 게이트웨이가 필요하다.

## 관련 문서

- `docs/reference/contracts/assessment-api.md` — 응답 계약 단일 진실 (스키마·필드·버전)
- `docs/explanation/products/environment-report.md` / `server-report.md` — 같은 source 의 사람용 출력
- `docs/reference/web/routers.md` — 구현 위치 카탈로그
