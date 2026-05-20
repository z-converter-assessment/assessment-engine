"""Mapper sub-package 공용 상수·타입 카탈로그 (P2 단일 진실).

여러 sub-module 이 공유하는 임계값·카탈로그·타입 alias 만 본 모듈에 둔다. 단일 sub-module 내부에서만
쓰는 상수는 해당 sub-module 내부에 유지 — 본 모듈은 다중 공유 명목으로 한정.
"""

from typing import Literal

# ─── UI 임계값 — base.html body data-attribute 동기화 (#E1 P3 · ADR 0015) ────
# template_setup.py 가 본 상수를 import 해 Jinja2 globals 로 노출 → body data-attribute 단일 진실.
_USAGE_DANGER_PCT = 90  # 사용률 위험 임계 — disk_warning · server detail badge 공통
_USAGE_WARN_PCT = 75  # 사용률 주의 임계
_SWAP_DANGER_PCT = 0.1  # 스왑 사용 자체가 이슈 — 0.1% 도 빨강 (JS performance.js 동일)

# 보고서 view 분기 — 라우터 Pydantic Literal 정합 (#F3)
# service · mapper 시그니처에도 적용해 typo 차단.
ReportView = Literal["customer", "engineer"]

# ─── USE Method 도넛 카탈로그 — 대시보드 + 환경 보고서 + 서버 리스트 단일 진실 (T13) ────
# USE Method recommendation enum 1:1 매핑. (key, label, hex, description) 튜플 정렬:
#   under(빨강), over(청록), idle(회색), shutdown(보라), optimal(녹색), insufficient_data(옅은회색).
_DONUT_SEGMENT_DEFS: list[tuple[str, str, str, str]] = [
    ("under_provisioned", "under_provisioned", "#ef4444", "자원 부족 — 사양 상향 검토"),
    ("over_provisioned", "over_provisioned", "#06b6d4", "자원 여유 — 사양 축소 검토"),
    ("idle", "idle", "#94a3b8", "사용률 매우 낮음 — 용도 재평가"),
    ("shutdown", "shutdown", "#9333ea", "사실상 미사용 — 종료 검토"),
    ("optimal", "optimal", "#22c55e", "적정"),
    ("insufficient_data", "insufficient_data", "#cbd5e1", "평가 표본 부족"),
]

# USE Method recommendation enum -> donut segment key (식별 매핑, 라벨·색은 _DONUT_SEGMENT_DEFS 단일 진실).
_DONUT_SEGMENT_FROM_REC: dict[str, str] = {
    "under_provisioned": "under_provisioned",
    "over_provisioned": "over_provisioned",
    "idle": "idle",
    "shutdown": "shutdown",
    "optimal": "optimal",
    "insufficient_data": "insufficient_data",
}

# 서버 목록 셀 안 표시용 약어 — 좁은 칸. 도넛 범례(풀네임)와 별도 매핑.
_DONUT_SEGMENT_SHORT_LABEL: dict[str, str] = {
    "under_provisioned": "Under",
    "over_provisioned": "Over",
    "idle": "Idle",
    "shutdown": "Shutdown",
    "optimal": "Optimal",
    "insufficient_data": "No Data",
}

# ─── 보고서·환경 보고서 공용 capacity 임박 임계 ───
# build_report_summary_bullets (report.py) + _extract_capacity_imminent (environment_report.py).
_CAPACITY_IMMINENT_DAYS = 30

# ─── OS EOL 정적 매핑 — 보고서 정성 요약 + attention OS EOL 카드 양쪽 공용 ───
# (os_id, os_version_prefix) -> ISO 날짜 문자열. 확장 시 본 dict 만 갱신.
_OS_EOL: dict[tuple[str, str], str] = {
    ("centos", "7"): "2024-06-30",
    ("rhel", "7"): "2024-06-30",
    ("ubuntu", "18.04"): "2023-05-31",
    ("debian", "10"): "2024-06-30",
    ("debian", "11"): "2024-07-14",  # standard support EOL (LTS는 2026-08까지)
    ("centos", "8"): "2024-05-31",  # CentOS Stream 8 (AlmaLinux/Rocky 8은 2029까지 active)
}
