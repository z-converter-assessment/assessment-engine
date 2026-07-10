"""시스템 계약 버전 단일 진실 — 엔진 레포 기준 통일 버전.

네 계약이 모두 이 버전 문자열을 단다:
- wire (agent -> engine 메시지): consumer/schemas.py schema_version. 에이전트도 "1.0" emit.
- assessment API (engine -> DR): GET /api/assessment 응답 contract_version.
- export (engine -> 운영자): POST /api/exports/inventory 파일 = assessment envelope 이라 동일.
- task.install (engine -> agent 특권 명령): 페이로드 schema_version. 에이전트가 실행 전 major 게이트.

형식은 "major.minor"(예 "1.0"). 게이트는 major(점 앞 정수)만 — 소비자/게이트는 major 만 비교해 같으면 수용
(minor 무관), 다르면 거부. minor(.0/.1/...)는 additive 변경 추적용(필드/enum/사이징축 추가) — 게이트에 영향
없고 소비자는 무시. 구조 파괴 변경만 major 범프 — 엔진+에이전트+DR 동시 flag-day.
"""

CONTRACT_VERSION = "1.0"
