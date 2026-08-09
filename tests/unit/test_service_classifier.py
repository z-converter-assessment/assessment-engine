import pytest

from assessment_engine.domain.service_classifier import (
    BADGE_CLASS_BY_CATEGORY,
    SERVICE_CATALOG,
    SERVICE_CATEGORIES,
    classify_service,
    matched_ports,
)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("nginx.service", "web"),
        ("nginx", "web"),
        ("apache2.service", "web"),
        ("postgresql.service", "db"),
        ("postgresql@14-main.service", "db"),
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
def test_classify(unit: str, expected: str):
    assert classify_service(unit) == expected


def test_classify_case_insensitive():
    assert classify_service("NGINX.service") == "web"
    assert classify_service("PostgreSQL.service") == "db"


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
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
def test_classify_extended_catalog(unit: str, expected: str):
    assert classify_service(unit) == expected


@pytest.mark.parametrize(
    ("port", "expected"),
    [
        (1521, "db"),
        (9200, "db"),
        (8123, "db"),
        (6650, "mq"),
        (8428, "monitor"),
        (3100, "monitor"),
    ],
)
def test_detect_listen_categories_extended_ports(port: int, expected: str):
    from assessment_engine.domain.service_classifier import detect_listen_categories

    result = detect_listen_categories([{"proto": "tcp", "port": port, "comm": None}])
    assert list(result.keys()) == [expected]


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("W3SVC", "web"),
        ("MSSQLSERVER", "db"),
        ("MSSQL$PROD", "db"),
    ],
)
def test_classify_windows_scm_name(unit: str, expected: str):
    assert classify_service(unit) == expected


def test_classify_comm_signal_name_variant():
    listen = [{"proto": "tcp", "port": 27017, "comm": "mongod"}]
    assert classify_service("mongo", listen) == "db"


def test_classify_comm_unattributable_stays_unknown():
    listen = [{"proto": "tcp", "port": 1433, "comm": "sqlservr.exe"}]
    assert classify_service("MyCompanyDB", listen) == "unknown"


def test_classify_port_signal_via_wellknown_name():
    listen = [{"proto": "tcp", "port": 6379, "comm": "valkey-server"}]
    assert classify_service("valkey", listen) == "cache"


def test_classify_priority_name_over_port():
    listen = [{"proto": "tcp", "port": 5432, "comm": "haproxy"}]
    assert classify_service("haproxy.service", listen) == "web"


def test_classify_listen_ports_none_name_only():
    assert classify_service("nginx.service") == "web"
    assert classify_service("MyCompanyDB") == "unknown"


def test_matched_ports_via_comm_match():
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
    listen_ports = [
        {"proto": "tcp", "port": 5432, "uid": 999, "comm": None},
        {"proto": "tcp", "port": 22, "uid": 0, "comm": "sshd"},
    ]
    result = matched_ports("postgresql.service", listen_ports)
    pairs = {(r.proto, r.port) for r in result}
    assert ("tcp", 5432) in pairs
    assert ("tcp", 22) not in pairs


def test_matched_ports_dedupe_proto_port_pair():
    listen_ports = [
        {"proto": "tcp", "port": 80, "uid": 0, "comm": "nginx"},
        {"proto": "tcp", "port": 80, "uid": 33, "comm": "nginx"},
        {"proto": "tcp6", "port": 80, "uid": 0, "comm": "nginx"},
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


def test_matched_ports_pid_join_attributes_unrelated_comm_and_port():
    listen = [{"proto": "tcp", "port": 1433, "pid": 100, "comm": "randombin"}]
    result = matched_ports("app.service", listen, pid=100)
    assert {(r.proto, r.port) for r in result} == {("tcp", 1433)}
    assert matched_ports("app.service", listen) == []


def test_matched_ports_pid_join_excludes_comm_match_with_other_pid():
    listen = [{"proto": "tcp", "port": 80, "pid": 999, "comm": "nginx"}]
    assert matched_ports("nginx.service", listen, pid=100) == []
    assert matched_ports("nginx.service", listen) == []


def test_matched_ports_pid_join_only_matching_pid_sockets():
    listen = [
        {"proto": "tcp", "port": 80, "pid": 100, "comm": "nginx"},
        {"proto": "tcp", "port": 443, "pid": 100, "comm": "nginx"},
        {"proto": "tcp", "port": 8080, "pid": 200, "comm": "nginx"},
    ]
    result = matched_ports("nginx.service", listen, pid=100)
    assert {(r.proto, r.port) for r in result} == {("tcp", 80), ("tcp", 443)}


def test_matched_ports_pid_join_excludes_missing_pid_sockets():
    listen = [
        {"proto": "tcp", "port": 5432, "pid": 100, "comm": "x"},
        {"proto": "tcp", "port": 5433, "comm": "x"},
    ]
    result = matched_ports("postgresql.service", listen, pid=100)
    assert {(r.proto, r.port) for r in result} == {("tcp", 5432)}


def test_matched_ports_pid_join_dedupe_proto_port():
    listen = [
        {"proto": "tcp", "port": 80, "pid": 100, "comm": "a"},
        {"proto": "tcp", "port": 80, "pid": 100, "comm": "b"},
        {"proto": "tcp6", "port": 80, "pid": 100, "comm": "a"},
    ]
    result = matched_ports("app.service", listen, pid=100)
    assert {(r.proto, r.port) for r in result} == {("tcp", 80), ("tcp6", 80)}
    assert len(result) == 2


def test_classify_pid_join_enables_port_signal():
    listen = [{"proto": "tcp", "port": 1433, "pid": 100, "comm": "opaque"}]
    assert classify_service("app.service", listen, pid=100) == "db"
    assert classify_service("app.service", listen) == "unknown"


def test_classify_pid_join_enables_comm_signal():
    listen = [{"proto": "tcp", "port": 9999, "pid": 100, "comm": "sqlservr"}]
    assert classify_service("app.service", listen, pid=100) == "db"
    assert classify_service("app.service", listen) == "unknown"


def test_classify_pid_join_ignores_other_pid_socket():
    listen = [{"proto": "tcp", "port": 1433, "pid": 200, "comm": "sqlservr"}]
    assert classify_service("app.service", listen, pid=100) == "unknown"


def test_catalog_categories_match_derived():
    assert tuple(d.key for d in SERVICE_CATALOG) == SERVICE_CATEGORIES


def test_catalog_badge_class_covers_all_categories_plus_unknown():
    for d in SERVICE_CATALOG:
        assert BADGE_CLASS_BY_CATEGORY[d.key] == d.badge_class
    assert BADGE_CLASS_BY_CATEGORY["unknown"] == "badge-cat-unknown"


def test_catalog_ports_no_cross_category_collision():
    seen: dict[int, str] = {}
    for d in SERVICE_CATALOG:
        for ports in d.port_names.values():
            for port in ports:
                if port in seen:
                    assert seen[port] == d.key, f"port {port} 충돌: {seen[port]} vs {d.key}"
                seen[port] = d.key


def test_compute_categories_port_only_workload():
    from assessment_engine.domain.service_classifier import compute_service_categories

    cats = compute_service_categories(None, [{"proto": "tcp", "port": 6379, "comm": "opaquebin"}])
    assert cats == ["cache"]


def test_compute_categories_name_listen_union_sorted_dedup():
    from assessment_engine.domain.service_classifier import compute_service_categories

    cats = compute_service_categories(
        [{"unit": "nginx.service"}, {"unit": "sshd.service"}],
        [{"proto": "tcp", "port": 6379, "comm": "redis-server"}],
    )
    assert cats == ["cache", "web"]


def test_compute_categories_empty():
    from assessment_engine.domain.service_classifier import compute_service_categories

    assert compute_service_categories(None, []) == []
    assert compute_service_categories([], None) == []


def test_compute_categories_matches_workload_counter_keyset():
    from assessment_engine.domain.service_classifier import compute_service_categories
    from assessment_engine.web.services.mappers.server import workload_category_counter

    services = [{"unit": "nginx.service"}, {"unit": "docker.service"}, {"unit": "containerd.service"}]
    listen = [{"proto": "tcp", "port": 6379, "comm": "redis-server"}, {"proto": "tcp", "port": 9090, "comm": "x"}]
    computed = sorted(compute_service_categories(services, listen))
    counter_keys = sorted(workload_category_counter(services, listen).keys())
    assert computed == counter_keys


def test_compute_categories_excludes_baseline_keeps_workload():
    from assessment_engine.domain.service_classifier import compute_service_categories

    cats = compute_service_categories(
        [
            {"unit": "sshd.service"},
            {"unit": "chronyd.service"},
            {"unit": "rpcbind.service"},
            {"unit": "redis.service"},
            {"unit": "named.service"},
        ],
        [
            {"proto": "tcp", "port": 22, "comm": "sshd"},
            {"proto": "udp", "port": 123, "comm": "chronyd"},
            {"proto": "tcp", "port": 6379, "comm": "redis-server"},
            {"proto": "tcp", "port": 53, "comm": "named"},
        ],
    )
    assert cats == ["cache", "infra"]
