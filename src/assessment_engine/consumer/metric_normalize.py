"""메트릭 canonical 정규화 경계 — 수집 진입(consumer)에서 OS 무관 불변식 강제 (defense in depth).

변환 책임은 agent(OS API raw -> canonical Linux /proc 단위)에 있지만, 엔진은 agent 를 신뢰하지 않고
저장 전 canonical 불변식을 한 번 더 게이트한다 (특히 Windows pagefile->swap·메모리 매핑이 어긋날 때).
위반 시 ceiling 으로 클램프 + warning 1회 — bad agent 데이터가 음수 used / 왜곡된 % 로 다운스트림에 새지 않게.

canonical 불변식 (used = total - free/available 가 음수가 되는 것을 원천 차단):
- swap_free_kb    ≤ swap_total_kb
- mem_available_kb ≤ mem_total_kb

per-field 음수(<0)는 consumer schema `Field(ge=0)` 가 1차 차단 — 본 모듈은 cross-field 불변식만 담당.
단일 진실: 모든 시계열 메트릭은 본 경계를 통과한 값만 저장된다 (real-time·report·chart 모두 clean 데이터 전제).
"""

from loguru import logger


def clamp_ceiling(
    value: int | None,
    ceiling: int | None,
    *,
    field: str,
    composite_id: str | None,
) -> int | None:
    """`value` 가 `ceiling`(total) 을 초과하면 ceiling 으로 클램프 — used 음수 방지.

    둘 중 하나라도 None 이면 그대로 통과 (해당 OS 미수집 = NULL 보존, 0 으로 날조하지 않음).
    클램프 발생 시 운영자 인지용 warning (#F8 — 식별자·필드명·값만, payload raw dump 금지).
    """
    if value is None or ceiling is None or value <= ceiling:
        return value
    logger.warning(
        "metric canonical clamp field={} value={} exceeds ceiling={} -> clamped composite_id={}",
        field,
        value,
        ceiling,
        composite_id,
    )
    return ceiling
