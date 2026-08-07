"""boot_time 동일 부팅 판정 — 측정 jitter 흡수 단일 진실 (web·consumer 공용 도메인 모듈).

에이전트가 boot_time 을 /proc/stat btime(정적) 대신 now - uptime(동적)으로 산출해 매 수집마다 +/-1초
흔들린다(실측 확인). 정확 비교(!=)는 이 jitter 를 재부팅으로 오판해 CPU/IO delta 를 간헐 NULL 로 만들고
inventory_history 에 가짜 행을 쌓는다. DB 에는 agent raw boot_time 을 그대로 저장하고 흡수는 읽기 시점
판정에서만 한다 — SQL(`db.repositories.query.types.BOOT_JITTER_SEC`)도 여기서 파생한 초 단위 값을 bound
parameter 로 써 단일 진실을 유지한다.
"""

from datetime import datetime, timedelta

# 실측 jitter(+/-1초)와 실제 재부팅(분 단위 점프) 사이에 여유 있게 둔 값.
BOOT_TIME_JITTER_TOLERANCE = timedelta(seconds=5)


def is_counter_reset(cur_boot: datetime | None, prev_boot: datetime | None) -> bool:
    """재부팅(counter reset) 확정 판정 — 보수적.

    재부팅이면 /proc 누적 카운터가 0 으로 리셋돼 그 구간 delta 가 무의미해진다(호출자는 None 처리).
    한쪽이라도 NULL(옛 데이터·미발행)이면 단정 못 해 False — 호출자는 d<0 휴리스틱으로 폴백한다.
    """
    if cur_boot is None or prev_boot is None:
        return False
    return abs(cur_boot - prev_boot) > BOOT_TIME_JITTER_TOLERANCE


def boot_time_changed(prev_boot: datetime | None, new_boot: datetime | None) -> bool:
    """boot_time 변경 감지 — 적극적 (inventory_history append 트리거).

    값<->NULL 전환은 의미있는 변경(True), 둘 다 NULL 이면 동일(False).
    """
    if prev_boot is None and new_boot is None:
        return False
    if prev_boot is None or new_boot is None:
        return True
    return abs(prev_boot - new_boot) > BOOT_TIME_JITTER_TOLERANCE
