# ADR 0015 — UI 임계값 단일 진실 (body data-attribute 패턴)

상태: Accepted (2026-05-19).

## Context

- `static/js/pages/detail.js` 와 `static/js/pages/performance.js` 가 `USAGE_DANGER_PCT = 90` / `USAGE_WARN_PCT = 75` / `SWAP_DANGER_PCT = 0.1` 같은 임계 상수를 hardcoded.
- 같은 값이 backend `web/services/mappers.py` 의 `_USAGE_DANGER_PCT=90` / `_USAGE_WARN_PCT=75` 와 별도 정의 — double-source.
- CLAUDE.md #E1 P4 "비즈니스 임계값 분류 금지" + #E3 "UI badge 임계 단일 진실" 위반.
- 임계 변경 시 두 곳 동시 갱신 의무 (drift 위험 High) — 실제로 SWAP_DANGER_PCT 는 backend 에 없고 JS 만 정의.

## Decision

UI 임계 단일 진실 = backend `mappers` 상수. Jinja2 globals → base.html body data-attribute → JS `document.body.dataset` 패턴으로 SSR 시 주입.

### 데이터 흐름

```
mappers._USAGE_DANGER_PCT (Python)
  → template_setup.env.globals["ui_thresholds"]
  → base.html <body data-usage-danger-pct="{{ ui_thresholds.usage_danger_pct }}">
  → JS: parseFloat(document.body.dataset.usageDangerPct)
```

### 컴포넌트

- `mappers._SWAP_DANGER_PCT = 0.1` 신규 추가 (UI badge 도메인 카탈로그 보강 — 기존 USAGE_*와 같은 슬롯).
- `web/template_setup.py` 가 `env.globals["ui_thresholds"]` 신규 dict 등록:
  ```python
  env.globals["ui_thresholds"] = {
      "usage_danger_pct": _USAGE_DANGER_PCT,
      "usage_warn_pct":   _USAGE_WARN_PCT,
      "swap_danger_pct":  _SWAP_DANGER_PCT,
  }
  ```
- `base.html` `<body>` 가 3 data-attribute 자동 박음:
  ```html
  <body data-usage-danger-pct="{{ ui_thresholds.usage_danger_pct }}"
        data-usage-warn-pct="{{ ui_thresholds.usage_warn_pct }}"
        data-swap-danger-pct="{{ ui_thresholds.swap_danger_pct }}"
        {% block body_attrs %}{% endblock %}>
  ```
- `detail.js` / `performance.js` 가 dataset 에서 읽기 + hardcoded fallback (브라우저 캐시 base.html 옛 버전 대응):
  ```js
  const USAGE_DANGER_PCT = parseFloat(document.body.dataset.usageDangerPct) || 90;
  ```

## Consequences

장점:
- 임계 변경 시 `mappers.py` 한 곳 수정 → backend·UI 자동 동기. drift 위험 0.
- Jinja2 globals 메커니즘 활용 — 라우터마다 context 전달 불필요. 모든 페이지 자동 적용.
- SWAP_DANGER_PCT 도 backend 카탈로그 입성 — 새 임계 추가 시 같은 패턴 확장 가능.
- API 응답 스키마 변경 없음 — `/api/v1/servers/{id}/metrics/latest` 같은 raw 데이터 응답은 그대로. 색·분류는 여전히 클라이언트 측 산식 (P4 명시 예외) 이지만 임계 상수는 단일 진실.

단점:
- body data-attribute 가 모든 페이지 HTML 에 3 attribute 추가 — ~120 bytes 헤더 증가. 무시 수준.
- 임계 외 색 hex (`#ef4444` 등) 는 여전히 JS 정의 — 색 단일 진실까지는 본 ADR 범위 밖.
- HTML attribute 라 string→float parse 의무. `parseFloat` fallback 으로 방어.

## 대안 검토

(a) API endpoint 신설 (`GET /api/v1/config/ui-thresholds`) — 페이지 로드마다 추가 fetch. SSR 와 정합 떨어짐.

(b) API 응답 (`/metrics/latest` 등) 에 색 또는 분류 카테고리 precompute — mapper 단일 진실 회복 가장 정석. 다만 API 스키마 변경 + dashboard_from_json + SSE 응답 + cache_serializer 동시 변경 큰 작업. 본 ADR 보다 후속 작업 (P4 완전 회복) 으로 미룸.

(c) inline `<script>` 로 임계 주입 — #E6 "JS 외부화 의무" 위반. 본 ADR 패턴이 정석.

## 향후 작업

- 색 hex (`#ef4444` / `#f59e0b` 등) 까지 backend 단일 진실로 옮길지 검토. 다만 색은 design system 영역이라 별도 ADR 고려.
- API 응답에 색 precompute 포함 (P4 완전 회복) — 본 ADR 의 임계 단일 진실이 1단계, 후속이 2단계.
- 새 임계 상수 추가 시 의무 동시 갱신 위치:
  1. `mappers` 모듈 상단 상수
  2. `template_setup.env.globals["ui_thresholds"]` 새 키
  3. `base.html` `<body>` 새 data-attribute
  4. 사용처 JS 의 `parseFloat(document.body.dataset.xxxYyyZz)` (camelCase 변환 주의)
