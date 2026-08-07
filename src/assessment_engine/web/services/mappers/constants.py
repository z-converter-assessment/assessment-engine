"""Mapper 공용 표시 상수·라벨 카탈로그 (P2 단일 진실).

값만 둔다 — 판정을 표시로 바꾸는 계산은 `assessment_display.py`, 호스트 raw 파생은 `host_display.py` 소관.
여러 sub-module 이 공유하는 것만 여기 두고, 한 모듈 안에서만 쓰는 상수는 그 모듈에 유지한다.
"""

from typing import Literal

from assessment_engine.domain import right_sizing

# --- UI 임계값 — base.html body data-attribute 동기화 (#E1 P3) ----
# templating/setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
# 보고서 표시 전용 임계 — 행 변환(report)과 요약 불릿(report_summary)이 같은 값을 본다.
_VARIANCE_BURST_RATIO = 1.5  # peak/p95 >= 1.5 — variance burst 표시
_REBOOT_UNSTABLE_COUNT = 3  # reboot_count >= 3 — Agent 불안정 신호 (#F10 attention 임계)

_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계

# 보고서 view 분기 — 라우터 Pydantic Literal 정합 (#F3)
type ReportView = Literal["customer", "engineer"]

# 자원 부족 원인 라벨 — trigger key -> os-neutral 축 이름 (단일 진실, P2). attention capacity 카드 active_causes·
# environment_report 원인 집계 순서(_UNDER_CAUSE_ORDER = 본 dict 삽입순) 공유. Windows paging/run queue 포화도
# 이 축 이름으로 잡혀 Linux swap/load 로 오라벨 0. dict 삽입순 = 표시·집계 순서.
_CAUSE_LABEL_BY_TRIGGER: dict[str, str] = {
    "cpu_util": "CPU 이용률",
    "cpu_saturation": "CPU 포화",
    "mem_util": "메모리 이용률",
    "mem_saturation": "메모리 포화",
    "disk_capacity": "디스크 용량",
    "disk_io": "디스크 I/O",
}

# --- USE Method 도넛 카탈로그 — 대시보드 + 환경 보고서 + 서버 리스트 단일 진실 (T13) ----
# 자원 적정성 상태 enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(파랑=주색), idle(회색), optimal(녹색), insufficient_data(옅은회색).
# over 색 = 테마색1(var(--color-title)) 동일 주색 — 활용률 게이지와 같은 파랑, under 빨강과 대비.
# idle = 미사용 상태(수요 거의 0). 종료·통합 조치는 파생 권고 층(상태 아님).
# (분류, 색, 조치 설명). 한국어 분류명은 `right_sizing.RECOMMENDATION_LABEL_KO` 단일 진실이라 여기 두지 않는다.
# 원소 순서가 곧 도넛 세그먼트 순서이자 서버 목록 드롭다운 option 순서다.
_DONUT_SEGMENT_DEFS: list[tuple[right_sizing.Recommendation, str, str]] = [
    ("under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "var(--color-title)", "자원 여유 — 사양 축소 검토"),
    ("idle", "#64748b", "미사용 — 종료·통합 검토"),
    ("optimal", "#22c55e", "적정"),
    ("insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# 분류 -> 배지 CSS 클래스. 값이 템플릿 클래스명이라 표시 계층 소관이다 (#E1 P2) — 도메인 모듈이
# CSS 를 알 이유가 없다. 한국어 라벨(`right_sizing.RECOMMENDATION_LABEL_KO`)은 도메인 어휘라 그대로 둔다.
BADGE_CLASS: dict[right_sizing.Recommendation, str] = {
    "idle": "rec-idle",
    "over_provisioned": "rec-over_provisioned",
    "under_provisioned": "rec-under_provisioned",
    "optimal": "rec-optimal",
    "insufficient_data": "rec-insufficient_data",
}

# 게이지 테마 단색 = 테마색1 CSS 변수 (base.html :root --color-title). 활용률 게이지 + Right-sizing 분류 막대 단일 통일.
# CSS background 는 var 직접, SVG stroke 는 inline style 로 적용(presentation attribute 는 var 미지원). 테마 변경 시 자동 추종.
UTIL_GAUGE_COLOR = "var(--color-title)"

# list 페이지 dropdown (value, 한글 라벨) 쌍 — value=영어 enum(필터 매칭 data-classification),
# 표시=right_sizing.RECOMMENDATION_LABEL_KO 한글.
PROVISIONING_CLASS_OPTIONS: tuple[tuple[str, str], ...] = tuple(
    (key, right_sizing.RECOMMENDATION_LABEL_KO[key]) for key, _, _ in _DONUT_SEGMENT_DEFS
)

# OS family 표시 라벨 — 보고서(report.py)·환경 보고서(environment_report.py) 공유.
OS_FAMILY_LABEL_KO: dict[str, str] = {"linux": "Linux", "windows": "Windows", "unknown": "미상"}

# risk_level 정렬 우선순위 (위험 우선, 낮을수록 먼저) — N대 비교 표(report.py)·환경 분포(environment_report.py) 공유.
# 미지 키는 맨 뒤(99). risk_level 은 4값 고정이라 default 는 방어값.
RISK_LEVEL_ORDER: dict[str, int] = {"high": 0, "attention": 1, "low_usage": 2, "normal": 3}

# 진단 time_range -> 한국어 표시 라벨 (보고서·대시보드·이력 공용). 표시 라벨이라 mapper 소속
# (윈도우 타입·상수 TimeRange/DIAGNOSTIC_RANGE_DAYS 는 db/repositories/query/types.py 단일 진실 #F10).
DIAGNOSTIC_RANGE_LABEL_KR: dict[str, str] = {
    "15m": "15분",
    "1h": "1시간",
    "6h": "6시간",
    "24h": "1일",
    "7d": "7일",
    "14d": "14일",
    "30d": "30일",
}
