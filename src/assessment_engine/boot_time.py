"""boot_time 동일 부팅 판정 — 측정 지터 흡수 단일 진실 (web·consumer 공용 도메인 모듈).

에이전트가 boot_time 을 /proc/stat btime(정적) 대신 now - uptime(동적)으로 산출해 매 수집마다 ±1초
흔들린다(실측 확인). 정확 비교(!=)는 이 지터를 재부팅/변경으로 오판 -> CPU/IO delta 간헐 NULL(부하 상위·
차트 출렁임) + inventory_history 가짜 행 적재. 두 시점 boot_time 차이가 허용치 이내면 동일 부팅으로 본다.

읽기 시점 정규화(P1 raw-first): DB 에는 agent raw boot_time 을 그대로 저장하고, "동일 부팅인가" 판정만
본 모듈 단일 헬퍼/상수 경유. SQL(db.repositories.query.metric·report 가 types.BOOT_JITTER_SEC 공유)도
BOOT_TIME_JITTER_TOLERANCE 에서 파생한 초 단위 값을 bound parameter 로 사용해 단일 진실 유지.
"""

from datetime import datetime, timedelta

# 측정 지터 허용치 — 이 이내 차이는 동일 부팅. 실측 지터는 ±1초, 실제 재부팅은 분 단위 점프라 안전하게 구분.
BOOT_TIME_JITTER_TOLERANCE = timedelta(seconds=5)


def is_counter_reset(cur_boot: datetime | None, prev_boot: datetime | None) -> bool:
    """재부팅(counter reset) 확정 판정 — 보수적.

    두 시점 boot_time 차이가 허용치를 넘으면 재부팅 -> /proc 누적 카운터 0 리셋 -> delta 무의미(None).
    한쪽이라도 NULL(옛 데이터·미발행)이면 단정 못 해 False -> 호출자는 d<0 휴리스틱 fallback.
    """
    if cur_boot is None or prev_boot is None:
        return False
    return abs(cur_boot - prev_boot) > BOOT_TIME_JITTER_TOLERANCE


def boot_time_changed(prev_boot: datetime | None, new_boot: datetime | None) -> bool:
    """boot_time 변경 감지 — 적극적 (inventory_history append 트리거).

    허용치를 넘는 차이만 변경으로 본다(지터 흡수). 값<->부재(NULL) 전환은 의미있는 변경(True).
    둘 다 NULL 이면 동일(False).
    """
    if prev_boot is None and new_boot is None:
        return False
    if prev_boot is None or new_boot is None:
        return True
    return abs(prev_boot - new_boot) > BOOT_TIME_JITTER_TOLERANCE
