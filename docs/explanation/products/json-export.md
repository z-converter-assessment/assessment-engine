# JSON Export

본 문서는 JSON Export 산출물의 존재 의의를 정리한다. 응답 구조·필드·단위·불변식·버전 규약 정본은
`docs/reference/contracts/assessment-api.md`(얼어붙은 계약). 여기선 "왜 파일로도 내는가"만 담는다.

## 위치

- UI 진입점: 대시보드 list 페이지에서 N대 선택 -> "Export" 버튼 -> 다운로드
- API: `POST /api/exports/inventory` (요청 body = 필터, 응답 = 다운로드 JSON 파일)
- 산출물 = `GET /api/assessment` 와 데이터·구조가 동일한 envelope 을 파일로 전달. 최상위 구조 byte-identical,
  전달 방식만 파일(Content-Disposition attachment).

## 존재 의의

`/api/assessment` 는 재해복구/마이그레이션 소비자가 HTTP 로 실시간 소비하는 계약이다. Export 는 같은 계약을
파일로 떨군다 — 소비 도구가 엔진에 직접 접속하지 않는 상황을 위해서다.

- 오프라인/에어갭 자동화: 소스 관리망과 분리된 환경에서 Terraform/Ansible/CSP SDK 를 돌릴 때, 운영자가 파일을
  받아 옮겨 입력한다.
- 스냅샷 보관: 마이그레이션 착수 시점의 소스 상태를 파일로 고정해 감사·재현에 남긴다.

즉 "실시간 질의는 GET, 들고 나갈 땐 파일"이고 데이터는 같다. 별도 스키마가 아니라 같은 계약의 전달 모드 하나다.

## 계약과의 관계

- 스키마/필드/단위/불변식/버전 = `docs/reference/contracts/assessment-api.md` 단일 진실. Export 는 그 계약 8절
  (같은 데이터, 파일 전달)·10절(운영 계약: 무인증·pagination 없음·필터 스코프)을 그대로 따른다.
- 필터도 GET 과 동일(계약 3절) — 요청만 query 대신 body 배열.

## 사람용 보고서와의 분기

| 항목 | JSON Export | 보고서 (환경/서버) |
|------|------------|------------------|
| 형식 | JSON (machine-readable) | HTML SSR (human-readable) |
| 의도 | 자동화 도구 입력 | 운영자·고객 의사결정 |
| 가공 | 재현 팩트 + 사이징 axes[] (계약 그대로) | KPI·위험도·진단 텍스트 |

같은 데이터 source 지만 산출 형태·수신자가 다르다. 보고서는 "사람이 읽는다", Export 는 "도구가 받는다".

## 한계

- PII·secret: envelope 에 hostname·internal IP 가 담긴다. 외부로 들고 나갈 때 sanitize 는 수신 인프라 책임 —
  엔진은 계약 데이터를 그대로 낸다(#F8 은 로그/캐시 대상, 명시적 export 는 계약 산출물이라 별개).
- 스냅샷 1회성: 발행 시점 상태. 시계열 분포는 차트 endpoint 별도.
- 무인증: 관리망 전용 전제(계약 10절, tradeoffs T19). 외부 노출 시 앞단 인증 게이트웨이.

## 관련 문서

- `docs/reference/contracts/assessment-api.md` — 응답 계약 단일 진실 (스키마·필드·버전)
- `docs/explanation/products/environment-report.md` / `server-report.md` — 같은 source 의 사람용 출력
- 구현 위치는 `docs/reference/web/routers.md` 카탈로그가 갖는다
