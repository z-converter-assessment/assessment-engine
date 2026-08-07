"""Mapper 공용 표시 상수·라벨 카탈로그 (P2 단일 진실).

값만 둔다 — 판정을 표시로 바꾸는 계산은 `assessment_display.py`, 호스트 raw 파생은 `host_display.py` 소관.
여러 sub-module 이 공유하는 것만 여기 두고, 한 모듈 안에서만 쓰는 상수는 그 모듈에 유지한다.
"""

from typing import Literal

from assessment_engine.domain import right_sizing

# 보고서 표시 전용 임계 — 행 변환(report)과 요약 불릿(report_summary)이 같은 값을 본다.
_VARIANCE_BURST_RATIO = 1.5  # peak/p95 비
_REBOOT_UNSTABLE_COUNT = 3  # 창내 재시작 횟수 — agent 불안정 신호

# templating/setup.py 가 Jinja2 globals 로 노출한다 — base.html body data-attribute 와 같은 값.
_USAGE_DANGER_PCT = 90  # 사용률 위험 — disk_warning · 서버 상세 badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의

# 라우터 Query Literal 과 같은 값이어야 한다 (#F3).
type ReportView = Literal["customer", "engineer"]

# trigger key -> 자원 부족 원인 라벨. OS 중립 축 이름이라 Windows paging/run queue 포화가
# Linux swap/load 로 오라벨되지 않는다. dict 삽입순이 곧 표시·집계 순서다.
_CAUSE_LABEL_BY_TRIGGER: dict[str, str] = {
    "cpu_util": "CPU 이용률",
    "cpu_saturation": "CPU 포화",
    "mem_util": "메모리 이용률",
    "mem_saturation": "메모리 포화",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
}

# USE Method 도넛 카탈로그 — (분류, 색, 조치 설명). 한국어 분류명은 도메인 어휘라
# `right_sizing.RECOMMENDATION_LABEL_KO` 단일 진실이고 여기 두지 않는다.
# 원소 순서가 곧 도넛 세그먼트 순서이자 서버 목록 드롭다운 option 순서다.
_DONUT_SEGMENT_DEFS: list[tuple[right_sizing.Recommendation, str, str]] = [
    ("under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "var(--color-title)", "자원 여유 — 사양 축소 검토"),
    ("idle", "#64748b", "미사용 — 종료·통합 검토"),
    ("optimal", "#22c55e", "적정"),
    ("insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# 분류 -> 배지 CSS 클래스. 도메인 모듈이 CSS 를 알 이유가 없어 표시 계층에 둔다.
BADGE_CLASS: dict[right_sizing.Recommendation, str] = {
    "idle": "rec-idle",
    "over_provisioned": "rec-over_provisioned",
    "under_provisioned": "rec-under_provisioned",
    "optimal": "rec-optimal",
    "insufficient_data": "rec-insufficient_data",
}

# 테마색1 CSS 변수 (base.html :root --color-title) — 테마가 바뀌면 따라간다.
# SVG stroke 는 inline style 로 넣어야 한다 — presentation attribute 는 CSS 변수를 받지 않는다.
UTIL_GAUGE_COLOR = "var(--color-title)"

# list 페이지 dropdown — value 는 템플릿 data-classification 과 매칭되는 영어 enum 이어야 한다.
PROVISIONING_CLASS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, right_sizing.RECOMMENDATION_LABEL_KO[key]) for key, _, _ in _DONUT_SEGMENT_DEFS
)

OS_FAMILY_LABEL_KO: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}

# risk_level 정렬 우선순위 — 낮을수록 먼저 (위험 우선).
RISK_LEVEL_ORDER: dict[str, int] = {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}

# 표시 라벨만 여기 — TimeRange·윈도우 상수는 db/repositories/query/types.py 단일 진실 (#F10).
DIAGNOSTIC_RANGE_LABEL_KR: dict[str, str] = {
    "15m": "15분",
    "1h": "1시간",
    "6h": "6시간",
    "24h": "1일",
    "7d": "7일",
    "14d": "14일",
    "30d": "30일",
}
