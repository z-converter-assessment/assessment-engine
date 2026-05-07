"""service_classifier.py — 서비스 카테고리 분류·포트 매핑."""
import pytest

from assessment_engine.web.services.service_classifier import classify, matched_ports


# ─── classify ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit, expected", [
    ("nginx.service", "web"),
    ("nginx", "web"),                        # .service suffix 없어도
    ("apache2.service", "web"),
    ("postgresql.service", "db"),
    ("postgresql@14-main.service", "db"),     # Debian/Ubuntu 변형 unit name (substring 매칭)
    ("mariadb.service", "db"),
    ("redis-server.service", "cache"),
    ("rabbitmq-server.service", "mq"),
    ("docker.service", "container"),
    ("prometheus.service", "monitor"),
    ("ssh.service", "unknown"),
    ("foobar.service", "unknown"),
    ("", "unknown"),
])
def test_classify(unit, expected):
    assert classify(unit) == expected


def test_classify_case_insensitive():
    assert classify("NGINX.service") == "web"
    assert classify("PostgreSQL.service") == "db"


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