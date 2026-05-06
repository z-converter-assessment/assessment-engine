_PATTERNS: tuple[tuple[str, str], ...] = (
    ("nginx",         "web"),
    ("httpd",         "web"),
    ("apache",        "web"),
    ("caddy",         "web"),
    ("lighttpd",      "web"),
    ("traefik",       "web"),
    ("haproxy",       "web"),
    ("postgresql",    "db"),
    ("mariadb",       "db"),
    ("mysqld",        "db"),
    ("mysql",         "db"),
    ("mongod",        "db"),
    ("mongodb",       "db"),
    ("cassandra",     "db"),
    ("influxdb",      "db"),
    ("redis",         "cache"),
    ("memcached",     "cache"),
    ("varnish",       "cache"),
    ("rabbitmq",      "mq"),
    ("kafka",         "mq"),
    ("activemq",      "mq"),
    ("nats",          "mq"),
    ("docker",        "container"),
    ("containerd",    "container"),
    ("kubelet",       "container"),
    ("prometheus",    "monitor"),
    ("grafana",       "monitor"),
    ("datadog",       "monitor"),
    ("node_exporter", "monitor"),
    ("zabbix",        "monitor"),
)

# 서비스 유닛명 → well-known 포트 목록
# comm 읽기 권한이 없을 때(에이전트가 비루트) 폴백으로 사용
_SERVICE_PORTS: dict[str, list[int]] = {
    "nginx":            [80, 443],
    "apache2":          [80, 443],
    "httpd":            [80, 443],
    "caddy":            [80, 443],
    "lighttpd":         [80, 443],
    "haproxy":          [80, 443],
    "traefik":          [80, 443, 8080],
    "redis-server":     [6379],
    "redis":            [6379],
    "memcached":        [11211],
    "varnish":          [6081, 6082],
    "postgresql":       [5432],
    "postgres":         [5432],
    "mysqld":           [3306],
    "mysql":            [3306],
    "mariadb":          [3306],
    "mongod":           [27017],
    "cassandra":        [9042, 9160],
    "influxdb":         [8086],
    "rabbitmq-server":  [5672, 15672],
    "rabbitmq":         [5672, 15672],
    "kafka":            [9092],
    "nats-server":      [4222, 8222],
    "activemq":         [61616, 8161],
}


def classify(unit: str) -> str:
    name = unit.lower().removesuffix(".service")
    for keyword, category in _PATTERNS:
        if keyword in name:
            return category
    return "unknown"


def matched_ports(unit: str, listen_ports: list[dict]) -> list[dict]:
    """서비스 유닛에 해당하는 listen 포트 목록을 반환한다.

    comm 기반 매칭을 우선하고, comm이 없으면 well-known 포트 테이블로 폴백한다.
    동일 포트라도 proto(tcp/tcp6/udp 등)가 다르면 별도 항목으로 반환한다.
    반환 형식: [{"proto": "tcp", "port": 80}, ...]
    """
    name = unit.removesuffix(".service").lower()
    well_known = set(_SERVICE_PORTS.get(name, []))
    result: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for p in sorted(listen_ports, key=lambda x: (x.get("port", 0), x.get("proto", ""))):
        port = p.get("port", 0)
        proto = p.get("proto", "")
        key = (proto, port)
        if key in seen:
            continue
        comm = (p.get("comm") or "").lower()
        if (comm and (name in comm or comm in name)) or port in well_known:
            result.append({"proto": proto, "port": port})
            seen.add(key)

    return result