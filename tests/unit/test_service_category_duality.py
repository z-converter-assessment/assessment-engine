from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from assessment_engine.domain.service_classifier import (
    SERVICE_CATALOG,
    compute_service_categories,
)
from assessment_engine.json_types import JsonObject
from assessment_engine.web.services.mappers.server import workload_category_counter
from tests.hypothesis_scale import examples

_KEYWORDS = sorted({kw for d in SERVICE_CATALOG for kw in d.name_keywords})
_PORTS = sorted({port for d in SERVICE_CATALOG for ports in d.port_names.values() for port in ports})
_BASELINE_UNITS = ["sshd.service", "chronyd.service", "rpcbind.service", "systemd-logind.service"]
_OPAQUE_UNITS = ["app.service", "worker@1.service", "MyCompanyAgent"]

_unit_name = st.one_of(
    st.sampled_from(_KEYWORDS).map(lambda kw: f"{kw}.service"),
    st.sampled_from(_KEYWORDS),
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
    stored = compute_service_categories(services, listen_ports)
    displayed = workload_category_counter(services, listen_ports)

    assert stored == sorted(displayed)


@given(services=_services, listen_ports=_listen_ports)
@settings(max_examples=examples(500))
def test_stored_categories_are_sorted_and_unique(
    services: list[JsonObject] | None,
    listen_ports: list[JsonObject] | None,
):
    stored = compute_service_categories(services, listen_ports)

    assert stored == sorted(set(stored))


def test_generator_actually_reaches_the_interesting_branches():
    hits = compute_service_categories(
        [{"unit": f"{kw}.service", "sub": "running"} for kw in _KEYWORDS[:20]],
        None,
    )

    assert hits, "카탈로그 키워드가 하나도 분류되지 않았다 — 생성기 파생이 깨졌다"
