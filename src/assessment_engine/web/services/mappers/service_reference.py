"""참고자료 — 서비스 뱃지 카탈로그 표시 행 (SERVICE_CATALOG 파생, P2).

임계값 참고 페이지(`reports/right_sizing_thresholds.html`)의 뱃지 범례를 만든다. 카탈로그 1곳 수정이
본 표에 자동 반영된다 (#F12) — 손으로 유지하는 사본 없음.
"""

from assessment_engine.domain.service_classifier import SERVICE_CATALOG
from assessment_engine.web.view_models.server import ServiceBadgeRef


def build_service_badge_reference() -> list[ServiceBadgeRef]:
    """뱃지 범례 행 — services_label 은 port_names 를 "서비스명(포트/포트)" 인라인으로 (예 "nginx(80/443)").

    포트 없는 카테고리(container)는 desc_ko 를 대신 쓴다.
    """
    refs: list[ServiceBadgeRef] = []
    for d in SERVICE_CATALOG:
        named_ports = "·".join(f"{name}({'/'.join(str(p) for p in ports)})" for name, ports in d.port_names.items())
        refs.append(
            ServiceBadgeRef(
                category=d.key,
                label_ko=d.label_ko,
                desc_ko=d.desc_ko,
                badge_class=d.badge_class,
                services_label=named_ports or d.desc_ko,
            )
        )
    return refs
