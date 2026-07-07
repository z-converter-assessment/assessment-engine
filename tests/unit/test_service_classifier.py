"""service_classifier.py — 서비스 카테고리 분류·포트 매핑."""

import pytest

from assessment_engine.service_classifier import (
    BADGE_CLASS_BY_CATEGORY,
    SERVICE_CATALOG,
    SERVICE_CATEGORIES,
    classify,
    matched_ports,
    well_known_ports,
)

# ─── classify: name 신호 (하위호환) ──────────────────────────────────────────


@pytest.mark.parametrize(
    "unit, expected",
    [
        ("nginx.service", "web"),
        ("nginx", "web"),  # .service suffix 없어도
        ("apache2.service", "web"),
        ("postgresql.service", "db"),
        ("postgresql@14-main.service", "db"),  # Debian/Ubuntu 변형 unit name (substring 매칭)
        ("mariadb.service", "db"),
        ("redis-server.service", "cache"),
        ("rabbitmq-server.service", "mq"),
        ("docker.service", "container"),
        ("prometheus.service", "monitor"),
        ("ssh.service", "remote"),
        ("foobar.service", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify(unit, expected):
    assert classify(unit) == expected


def test_classify_case_insensitive():
    assert classify("NGINX.service") == "web"
    assert classify("PostgreSQL.service") == "db"


@pytest.mark.parametrize(
    "unit, expected",
    [
        # 커버리지 보강 워크로드 (이름 신호)
        ("tomcat.service", "web"),
        ("php-fpm.service", "web"),
        ("openresty.service", "web"),
        ("oracle", "db"),
        ("elasticsearch.service", "db"),
        ("clickhouse-server.service", "db"),
        ("valkey-server.service", "cache"),
        ("keydb.service", "cache"),
        ("pulsar", "mq"),
        ("emqx.service", "mq"),
        ("redpanda", "mq"),
        ("podman.service", "container"),
        ("k3s.service", "container"),
        ("crio.service", "container"),
        ("telegraf.service", "monitor"),
        ("loki.service", "monitor"),
        ("fluent-bit.service", "monitor"),
    ],
)
def test_classify_extended_catalog(unit, expected):
    assert classify(unit) == expected


@pytest.mark.parametrize(
    "port, expected",
    [
        (1521, "db"),  # oracle
        (9200, "db"),  # elasticsearch/opensearch
        (8123, "db"),  # clickhouse http
        (6650, "mq"),  # pulsar
        (8428, "monitor"),  # victoriametrics
        (3100, "monitor"),  # loki
    ],
)
def test_detect_listen_categories_extended_ports(port, expected):
    # 호스트 레벨 port 신호 — comm 부재(Windows 권한 부족 등)에도 포트로 분류.
    from assessment_engine.service_classifier import detect_listen_categories

    result = detect_listen_categories([{"proto": "tcp", "port": port, "comm": None}])
    assert list(result.keys()) == [expected]


# ─── classify: Windows SCM 이름 (이름 신호로 흡수, ADR 0032) ──────────────────


@pytest.mark.parametrize(
    "unit, expected",
    [
        ("W3SVC", "web"),  # IIS
        ("MSSQLSERVER", "db"),  # SQL Server default instance
        ("MSSQL$PROD", "db"),  # named instance 변형 (mssql substring)
    ],
)
def test_classify_windows_scm_name(unit, expected):
    assert classify(unit) == expected


# ─── classify: comm 신호 (이름 미스매치 흡수) ────────────────────────────────


def test_classify_comm_signal_name_variant():
    """name 이 comm 의 substring 이면 귀속 후 comm 키워드로 분류 (mongo <- mongod)."""
    # name "mongo" 는 키워드 미매치(키워드는 mongod/mongodb), comm "mongod" 가 name 을 포함 -> 귀속 -> db
    listen = [{"proto": "tcp", "port": 27017, "comm": "mongod"}]
    assert classify("mongo", listen) == "db"


def test_classify_comm_unattributable_stays_unknown():
    """이름이 comm 과 완전 무관하면 귀속 불가 -> unknown (T15 한계)."""
    listen = [{"proto": "tcp", "port": 1433, "comm": "sqlservr.exe"}]
    assert classify("MyCompanyDB", listen) == "unknown"


# ─── classify: port 신호 ────────────────────────────────────────────────────


def test_classify_port_signal_via_wellknown_name():
    """comm 부재(비루트 agent) + name 이 well-known 포트 보유 -> port 귀속 후 분류."""
    # name "redis" 는 이미 이름 신호로 cache. 포트 경로 검증을 위해 이름 신호가 약한 케이스 사용.
    # "valkey"(redis fork) 가정 — 이름 미매치지만 comm 이 name 포함.
    listen = [{"proto": "tcp", "port": 6379, "comm": "valkey-server"}]
    # name "valkey" vs comm "valkey-server": comm in name? no. name in comm? "valkey" in "valkey-server" yes -> 귀속
    # comm "valkey-server" 키워드 매치 없음 -> port 6379 -> cache
    assert classify("valkey", listen) == "cache"


def test_classify_priority_name_over_port():
    """name 신호가 port 와 충돌하면 name 우선 (haproxy 가 5432 프록시해도 web)."""
    listen = [{"proto": "tcp", "port": 5432, "comm": "haproxy"}]
    assert classify("haproxy.service", listen) == "web"


def test_classify_listen_ports_none_name_only():
    """listen_ports 미제공 시 name 신호만 — 현행 동작 보존."""
    assert classify("nginx.service") == "web"
    assert classify("MyCompanyDB") == "unknown"


# ─── matched_ports ────────────────────────────────────────────────────────


def test_matched_ports_via_comm_match():
    """comm이 unit name을 포함하면 매칭."""
    listen_ports = [
        {"proto": "tcp", "port": 80, "uid": 0, "comm": "nginx"},
        {"proto": "tcp", "port": 443, "uid": 0, "comm": "nginx"},
        {"proto": "tcp", "port": 22, "uid": 0, "comm": "sshd"},
    ]
    result = matched_ports("nginx.service", listen_ports)
    pairs = {(r.proto, r.port) for r in result}
    assert ("tcp", 80) in pairs
    assert ("tcp", 443) in pairs
    assert ("tcp", 22) not in pairs


def test_matched_ports_via_well_known_fallback():
    """comm 없거나 매칭 안 되면 well-known 포트 테이블로 폴백."""
    listen_ports = [
        {"proto": "tcp", "port": 5432, "uid": 999, "comm": None},
        {"proto": "tcp", "port": 22, "uid": 0, "comm": "sshd"},
    ]
    result = matched_ports("postgresql.service", listen_ports)
    pairs = {(r.proto, r.port) for r in result}
    assert ("tcp", 5432) in pairs
    assert ("tcp", 22) not in pairs


def test_matched_ports_dedupe_proto_port_pair():
    """같은 (proto, port) 중복 제거."""
    listen_ports = [
        {"proto": "tcp", "port": 80, "uid": 0, "comm": "nginx"},
        {"proto": "tcp", "port": 80, "uid": 33, "comm": "nginx"},  # 같은 (tcp, 80)
        {"proto": "tcp6", "port": 80, "uid": 0, "comm": "nginx"},  # proto 다르면 별개
    ]
    result = matched_ports("nginx.service", listen_ports)
    pairs = {(r.proto, r.port) for r in result}
    assert ("tcp", 80) in pairs
    assert ("tcp6", 80) in pairs
    assert len(result) == 2


def test_matched_ports_unknown_service_returns_empty():
    listen_ports = [{"proto": "tcp", "port": 22, "uid": 0, "comm": "sshd"}]
    assert matched_ports("foobar.service", listen_ports) == []


def test_matched_ports_empty_listen():
    assert matched_ports("nginx.service", []) == []


# ─── well_known_ports (export 폴백) ──────────────────────────────────────────


def test_well_known_ports_name_substring():
    assert well_known_ports("postgresql@14-main.service") == (5432,)
    assert well_known_ports("nginx.service") == (80, 443)
    assert well_known_ports("foobar.service") == ()


# ─── 카탈로그 파생 일관성 (drift 회귀, ADR 0032) ─────────────────────────────


def test_catalog_categories_match_derived():
    """SERVICE_CATEGORIES 는 카탈로그 key 순서와 동일."""
    assert SERVICE_CATEGORIES == tuple(d.key for d in SERVICE_CATALOG)


def test_catalog_badge_class_covers_all_categories_plus_unknown():
    """모든 카테고리 + unknown 이 뱃지 CSS 매핑을 가진다 (미스 시 unstyled 뱃지)."""
    for d in SERVICE_CATALOG:
        assert BADGE_CLASS_BY_CATEGORY[d.key] == d.badge_class
    assert BADGE_CLASS_BY_CATEGORY["unknown"] == "badge-cat-unknown"


def test_catalog_ports_no_cross_category_collision():
    """well-known 포트가 두 카테고리에 중복 배정되지 않는다 (port 신호 결정성)."""
    seen: dict[int, str] = {}
    for d in SERVICE_CATALOG:
        for ports in d.port_names.values():
            for port in ports:
                if port in seen:
                    assert seen[port] == d.key, f"port {port} 충돌: {seen[port]} vs {d.key}"
                seen[port] = d.key


# ─── compute_service_categories: ingest 사전계산 (목록·상세·리포트·필터 단일 진실) ──────────


def test_compute_categories_port_only_workload():
    """이름·comm 미상이라도 listen 포트(6379)로 cache 식별 — 목록 뱃지 버그 케이스."""
    from assessment_engine.service_classifier import compute_service_categories

    cats = compute_service_categories(None, [{"proto": "tcp", "port": 6379, "comm": "opaquebin"}])
    assert cats == ["cache"]


def test_compute_categories_name_listen_union_sorted_dedup():
    """services 이름 분류 ∪ listen 탐지, 정렬·dedup·unknown 제외."""
    from assessment_engine.service_classifier import compute_service_categories

    cats = compute_service_categories(
        [{"unit": "nginx.service"}, {"unit": "sshd.service"}],  # web + sshd(remote, baseline 제외)
        [{"proto": "tcp", "port": 6379, "comm": "redis-server"}],  # cache
    )
    assert cats == ["cache", "web"]


def test_compute_categories_empty():
    from assessment_engine.service_classifier import compute_service_categories

    assert compute_service_categories(None, []) == []
    assert compute_service_categories([], None) == []


def test_compute_categories_matches_workload_counter_keyset():
    """저장 집합 키셋 == workload_category_counter 키셋 (분류 로직 단일 진실 — 화면 간 일치 보장)."""
    from assessment_engine.service_classifier import compute_service_categories
    from assessment_engine.web.services.mappers.server import workload_category_counter

    services = [{"unit": "nginx.service"}, {"unit": "docker.service"}, {"unit": "containerd.service"}]
    listen = [{"proto": "tcp", "port": 6379, "comm": "redis-server"}, {"proto": "tcp", "port": 9090, "comm": "x"}]
    computed = sorted(compute_service_categories(services, listen))
    counter_keys = sorted(workload_category_counter(services, listen).keys())
    assert computed == counter_keys


def test_compute_categories_excludes_baseline_keeps_workload():
    """baseline(OS 기본·관리 — SSH·NTP·RPC) 제외, 특징 워크로드(cache·DNS 서버)만 저장 카테고리."""
    from assessment_engine.service_classifier import compute_service_categories

    cats = compute_service_categories(
        [{"unit": "sshd.service"}, {"unit": "chronyd.service"}, {"unit": "rpcbind.service"},
         {"unit": "redis.service"}, {"unit": "named.service"}],
        [{"proto": "tcp", "port": 22, "comm": "sshd"}, {"proto": "udp", "port": 123, "comm": "chronyd"},
         {"proto": "tcp", "port": 6379, "comm": "redis-server"}, {"proto": "tcp", "port": 53, "comm": "named"}],
    )
    assert cats == ["cache", "infra"]  # redis(cache)·named(DNS 서버=infra) — ssh/chronyd/rpcbind 제외
