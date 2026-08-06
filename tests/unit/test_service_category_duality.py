"""서비스 카테고리 산식 이중 구현 감시 (hypothesis).

같은 "이 호스트의 특징 워크로드 카테고리는 무엇인가" 를 두 함수가 각자 구현한다 —
`compute_service_categories`(ingest 사전계산, `server_inventory.service_categories` 에 저장)와
`workload_category_counter`(표시 계층, 뱃지·환경 분포). 앞은 정렬된 키 목록, 뒤는 카운터라 반환
형태는 다르지만 키 집합은 같아야 한다. 어긋나면 저장된 필터값과 화면 뱃지가 갈린다.

생성기는 임의 문자열이 아니라 `SERVICE_CATALOG` 에서 파생시킨다. 무작위 문자열은 거의 전부
`unknown` 으로 떨어져 baseline 제외·`SINGLE_INSTANCE_CATEGORIES`·listen 보충 경계가 한 번도
발화하지 않는다 — 통과하는 property 가 아무것도 증명하지 않게 된다.

이 property 가 깨지면 그것은 안전망 실패가 아니라 버그 발견이다. 이번 작업의 조건이 결과물 보존이라
그 경우에도 코드를 고쳐 배지 집합을 바꾸지 않는다 — 반례를 고정하고 보고한다.
"""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from assessment_engine.json_types import JsonObject
from assessment_engine.service_classifier import (
    SERVICE_CATALOG,
    compute_service_categories,
)
from assessment_engine.web.services.mappers.server import workload_category_counter
from tests.hypothesis_scale import examples

# 분류가 실제로 명중하는 이름·포트를 카탈로그에서 뽑는다.
_KEYWORDS = sorted({kw for d in SERVICE_CATALOG for kw in d.name_keywords})
# port_names 값은 포트 튜플이다 — 평탄화해야 실제 well-known 포트가 생성된다.
_PORTS = sorted({port for d in SERVICE_CATALOG for ports in d.port_names.values() for port in ports})
# baseline(SSH·NTP·RPC 등)과 아무 데도 안 걸리는 이름 — 제외 분기와 unknown 분기를 발화시킨다.
_BASELINE_UNITS = ["sshd.service", "chronyd.service", "rpcbind.service", "systemd-logind.service"]
_OPAQUE_UNITS = ["app.service", "worker@1.service", "MyCompanyAgent"]

_unit_name = st.one_of(
    st.sampled_from(_KEYWORDS).map(lambda kw: f"{kw}.service"),
    st.sampled_from(_KEYWORDS),  # Windows SCM 은 접미사가 없다
    st.sampled_from(_BASELINE_UNITS),
    st.sampled_from(_OPAQUE_UNITS),
)
_port = st.one_of(st.sampled_from(_PORTS), st.integers(min_value=1024, max_value=65535))


@st.composite
def _service(draw: st.DrawFn) -> JsonObject:
    unit: str = draw(_unit_name)
    service: dict[str, Any] = {"unit": unit, "sub": "running"}
    if draw(st.booleans()):
        service["pid"] = draw(st.integers(min_value=1, max_value=40000))
    if draw(st.booleans()):
        service["comm"] = draw(_unit_name)
    return service


@st.composite
def _listen_port(draw: st.DrawFn) -> JsonObject:
    entry: dict[str, Any] = {
        "proto": draw(st.sampled_from(["tcp", "udp"])),
        "addr": draw(st.sampled_from(["0.0.0.0", "127.0.0.1", "::"])),
        "port": draw(_port),
    }
    if draw(st.booleans()):
        entry["pid"] = draw(st.integers(min_value=1, max_value=40000))
    if draw(st.booleans()):
        entry["comm"] = draw(_unit_name)
    return entry


_services = st.one_of(st.none(), st.lists(_service(), max_size=8))
_listen_ports = st.one_of(st.none(), st.lists(_listen_port(), max_size=8))


@given(services=_services, listen_ports=_listen_ports)
@settings(max_examples=examples(500))
def test_ingest_and_display_agree_on_category_set(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None,
):
    """저장되는 카테고리 집합 == 화면이 세는 카테고리 집합."""
    stored = compute_service_categories(services, listen_ports)
    displayed = workload_category_counter(services, listen_ports)

    assert stored == sorted(displayed)


@given(services=_services, listen_ports=_listen_ports)
@settings(max_examples=examples(500))
def test_stored_categories_are_sorted_and_unique(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None,
):
    """저장값은 정렬·중복 제거 상태다 — 필터 비교와 화면 순서가 이 형태를 전제한다."""
    stored = compute_service_categories(services, listen_ports)

    assert stored == sorted(set(stored))


def test_generator_actually_reaches_the_interesting_branches():
    """생성기가 unknown 만 뽑고 있지 않은지 확인한다 — property 가 빈 입력만 보면 아무것도 증명 못 한다."""
    hits = compute_service_categories(
        [{"unit": f"{kw}.service", "sub": "running"} for kw in _KEYWORDS[:20]],
        None,
    )

    assert hits, "카탈로그 키워드가 하나도 분류되지 않았다 — 생성기 파생이 깨졌다"
