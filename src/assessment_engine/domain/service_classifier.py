"""서비스 카테고리 분류 규칙과 포트 매핑.

서비스 이름 키워드를 먼저 적용하고, 일치하지 않으면 서비스에 귀속된 수신 포트로 판정한다.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


@dataclass
class MatchedPort:
    """서비스에 연결된 수신 포트."""

    proto: str
    port: int


@dataclass(frozen=True)
class CategoryDef:
    """서비스 카테고리 분류 규칙."""

    key: str
    # 문자열 0개 이상 어노테이션
    name_keywords: tuple[str, ...]
    port_names: dict[str, tuple[int, ...]]
    badge_class: str

    label_ko: str
    desc_ko: str

    single_instance: bool = False


SERVICE_CATALOG: tuple[CategoryDef, ...] = (
    CategoryDef(
        key="web",
        name_keywords=(
            "nginx",
            "httpd",
            "apache",
            "caddy",
            "lighttpd",
            "traefik",
            "haproxy",
            "w3svc",
            "iis",
            "openresty",
            "tomcat",
            "jetty",
            "php-fpm",
            "gunicorn",
            "uwsgi",
        ),
        port_names={
            "nginx": (80, 443),
            "apache2": (80, 443),
            "httpd": (80, 443),
            "caddy": (80, 443),
            "lighttpd": (80, 443),
            "haproxy": (80, 443),
            "traefik": (80, 443, 8080),
            "tomcat": (8080,),
        },
        badge_class="badge-cat-web",
        label_ko="웹 계층",
        desc_ko="Nginx, Apache, HAProxy, Caddy, IIS, Tomcat 등 웹 서버, 리버스 프록시, 애플리케이션 서버",
    ),
    CategoryDef(
        key="db",
        name_keywords=(
            "postgresql",
            "postgres",
            "mariadb",
            "mysqld",
            "mysql",
            "mongod",
            "mongodb",
            "cassandra",
            "influxdb",
            "sqlservr",
            "mssql",
            "oracle",
            "elasticsearch",
            "opensearch",
            "clickhouse",
            "db2",
        ),
        port_names={
            "postgresql": (5432,),
            "postgres": (5432,),
            "mysqld": (3306,),
            "mysql": (3306,),
            "mariadb": (3306,),
            "mongod": (27017,),
            "cassandra": (9042, 9160),
            "influxdb": (8086,),
            "oracle": (1521,),
            "sqlservr": (1433,),
            "mssql": (1433,),
            "elasticsearch": (9200, 9300),
            "opensearch": (9200, 9300),
            "clickhouse": (8123, 9000),
            "db2": (50000,),
        },
        badge_class="badge-cat-db",
        label_ko="데이터베이스",
        desc_ko=(
            "PostgreSQL, MySQL/MariaDB, MongoDB, SQL Server, Oracle, Elasticsearch 등 관계형 데이터베이스, "
            "NoSQL, 검색 엔진"
        ),
    ),
    CategoryDef(
        key="cache",
        name_keywords=(
            "redis",
            "memcached",
            "varnish",
            "valkey",
            "keydb",
            "dragonfly",
        ),
        port_names={
            "redis-server": (6379,),
            "redis": (6379,),
            "memcached": (11211,),
            "varnish": (6081, 6082),
            "valkey": (6379,),
            "keydb": (6379,),
            "dragonfly": (6379,),
        },
        badge_class="badge-cat-cache",
        label_ko="캐시",
        desc_ko="Redis, Memcached, Valkey, Varnish 등 캐시와 콘텐츠 가속 서비스",
    ),
    CategoryDef(
        key="mq",
        name_keywords=(
            "rabbitmq",
            "kafka",
            "activemq",
            "nats",
            "mosquitto",
            "pulsar",
            "redpanda",
            "emqx",
            "artemis",
        ),
        port_names={
            "rabbitmq-server": (5672, 15672),
            "rabbitmq": (5672, 15672),
            "kafka": (9092,),
            "nats-server": (4222, 8222),
            "activemq": (61616, 8161),
            "mosquitto": (1883, 8883),
            "pulsar": (6650,),
            "emqx": (1883,),
        },
        badge_class="badge-cat-mq",
        label_ko="메시지 브로커",
        desc_ko="RabbitMQ, Kafka, NATS, ActiveMQ, Mosquitto, EMQX 등 메시지 브로커",
    ),
    CategoryDef(
        key="container",
        name_keywords=(
            "docker",
            "containerd",
            "kubelet",
            "podman",
            "crio",
            "k3s",
            "k0s",
        ),
        port_names={},
        badge_class="badge-cat-container",
        label_ko="컨테이너 플랫폼",
        desc_ko="Docker, containerd, Kubernetes 노드 구성요소, Podman 등 컨테이너 플랫폼",
        single_instance=True,
    ),
    CategoryDef(
        key="monitor",
        name_keywords=(
            "prometheus",
            "grafana",
            "datadog",
            "node_exporter",
            "zabbix",
            "telegraf",
            "collectd",
            "fluentd",
            "fluent-bit",
            "filebeat",
            "victoriametrics",
            "loki",
            "netdata",
            "alertmanager",
        ),
        port_names={
            "prometheus": (9090,),
            "node_exporter": (9100,),
            "zabbix": (10050, 10051),
            "alertmanager": (9093,),
            "victoriametrics": (8428,),
            "loki": (3100,),
            "netdata": (19999,),
        },
        badge_class="badge-cat-monitor",
        label_ko="모니터링 및 관측성",
        desc_ko="Prometheus, Grafana, Zabbix, Loki, Telegraf 등 모니터링, 메트릭, 로그 수집 구성요소",
    ),
    CategoryDef(
        key="remote",
        name_keywords=(
            "sshd",
            "ssh",
            "openssh",
            "rdp",
            "termservice",
            "umrdpservice",
            "winrm",
            "wsmprovhost",
            "tigervnc",
            "x11vnc",
            "vino",
            "vncserver",
            "teamviewer",
            "anydesk",
            "telnetd",
        ),
        port_names={
            "sshd": (22,),
            "rdp": (3389,),
            "termservice": (3389,),
            "winrm": (5985, 5986),
            "vncserver": (5900, 5901),
            "tigervnc": (5900, 5901),
            "telnetd": (23,),
        },
        badge_class="badge-cat-remote",
        label_ko="원격 접속 및 관리",
        desc_ko="SSH, RDP, WinRM, VNC 등 원격 접속 및 관리 서비스",
    ),
    CategoryDef(
        key="file",
        name_keywords=(
            "nfsd",
            "nfs",
            "rpc.mountd",
            "rpcbind",
            "smbd",
            "samba",
            "nmbd",
            "vsftpd",
            "proftpd",
            "pure-ftpd",
            "glusterd",
            "ceph-osd",
            "minio",
            "iscsid",
            "tgtd",
        ),
        port_names={
            "nfsd": (2049,),
            "rpc.mountd": (20048,),
            "rpcbind": (111,),
            "smbd": (445, 139),
            "samba": (445, 139),
            "vsftpd": (21,),
            "proftpd": (21,),
            "pure-ftpd": (21,),
            "minio": (9001,),
            "iscsid": (3260,),
            "tgtd": (3260,),
        },
        badge_class="badge-cat-file",
        label_ko="파일 및 스토리지 서비스",
        desc_ko="NFS, SMB, FTP, iSCSI, MinIO, Ceph 등 파일 전송과 네트워크 스토리지 서비스",
    ),
    CategoryDef(
        key="mail",
        name_keywords=(
            "postfix",
            "sendmail",
            "exim",
            "exim4",
            "dovecot",
            "cyrus",
            "opendkim",
            "rspamd",
            "amavis",
            "zimbra",
        ),
        port_names={
            "postfix": (25, 587, 465),
            "sendmail": (25,),
            "exim": (25,),
            "exim4": (25,),
            "dovecot": (143, 993, 110, 995),
            "cyrus": (143, 993),
        },
        badge_class="badge-cat-mail",
        label_ko="메일",
        desc_ko="Postfix·Sendmail·Exim(SMTP)·Dovecot(IMAP/POP3) 등 메일 서버",
    ),
    CategoryDef(
        key="infra",
        name_keywords=(
            "named",
            "dnsmasq",
            "unbound",
            "pdns",
            "coredns",
            "knot",
            "dhcpd",
            "dhcp",
            "kea",
            "chronyd",
            "ntpd",
            "slapd",
            "sssd",
            "winbind",
            "squid",
            "privoxy",
            "snmpd",
            "keepalived",
        ),
        port_names={
            "named": (53,),
            "dnsmasq": (53,),
            "unbound": (53,),
            "coredns": (53,),
            "dhcpd": (67,),
            "ntpd": (123,),
            "chronyd": (123,),
            "slapd": (389, 636),
            "squid": (3128,),
            "snmpd": (161, 162),
        },
        badge_class="badge-cat-infra",
        label_ko="네트워크 및 기반 서비스",
        desc_ko="DNS, DHCP, NTP, LDAP, SNMP, 프록시 등 네트워크 및 기반 서비스",
    ),
)


_NAME_INDEX: tuple[tuple[str, str], ...] = tuple((kw, d.key) for d in SERVICE_CATALOG for kw in d.name_keywords)

_NAME_PORTS: dict[str, tuple[int, ...]] = {name: ports for d in SERVICE_CATALOG for name, ports in d.port_names.items()}


def _build_port_index() -> dict[int, str]:
    index: dict[int, str] = {}
    for d in SERVICE_CATALOG:
        for ports in d.port_names.values():
            for port in ports:
                index.setdefault(port, d.key)
    return index


_PORT_INDEX: dict[int, str] = _build_port_index()

SERVICE_CATEGORIES: tuple[str, ...] = tuple(d.key for d in SERVICE_CATALOG)


SIGNATURE_CATEGORIES: tuple[str, ...] = ("web", "db", "cache", "mq", "container", "monitor")

SINGLE_INSTANCE_CATEGORIES: frozenset[str] = frozenset(d.key for d in SERVICE_CATALOG if d.single_instance)

BADGE_CLASS_BY_CATEGORY: dict[str, str] = {d.key: d.badge_class for d in SERVICE_CATALOG}
BADGE_CLASS_BY_CATEGORY["unknown"] = "badge-cat-unknown"


def _match_keyword(text: str) -> str | None:
    if not text:
        return None
    for keyword, category in _NAME_INDEX:
        if keyword in text:
            return category
    return None


def _attributed_ports(unit: str, listen_ports: list[JsonObject], pid: int | None = None) -> list[JsonObject]:
    result: list[JsonObject] = []
    seen: set[tuple[str, int]] = set()

    if pid is not None:
        for p in sorted(listen_ports, key=lambda x: (x.get("port", 0), x.get("proto", ""))):
            if p.get("pid") != pid:
                continue
            key = (p.get("proto", ""), p.get("port", 0))
            if key in seen:
                continue
            result.append(p)
            seen.add(key)
        return result

    name = unit.removesuffix(".service").lower()
    well_known = set(_NAME_PORTS.get(name, ()))
    for p in sorted(listen_ports, key=lambda x: (x.get("port", 0), x.get("proto", ""))):
        port = p.get("port", 0)
        proto = p.get("proto", "")
        key = (proto, port)
        if key in seen:
            continue
        comm = (p.get("comm") or "").lower()
        comm_match = bool(comm) and comm != "systemd" and (name in comm or comm in name)
        if comm_match or port in well_known:
            result.append(p)
            seen.add(key)

    return result


def classify_service(unit: str, listen_ports: list[JsonObject] | None = None, pid: int | None = None) -> str:
    """서비스 unit의 카테고리를 반환한다."""
    name = unit.lower().removesuffix(".service")
    cat = _match_keyword(name)
    if cat is not None:
        return cat

    if listen_ports:
        attributed = _attributed_ports(unit, listen_ports, pid)
        for p in attributed:
            cat = _match_keyword((p.get("comm") or "").lower())
            if cat is not None:
                return cat
        for p in attributed:
            cat = _PORT_INDEX.get(p.get("port", 0))
            if cat is not None:
                return cat

    return "unknown"


def matched_ports(unit: str, listen_ports: list[JsonObject], pid: int | None = None) -> list[MatchedPort]:
    """서비스 unit에 귀속된 수신 포트를 반환한다."""
    seen: set[tuple[str, int]] = set()
    result: list[MatchedPort] = []
    cat: str | None = None
    for p in sorted(listen_ports, key=lambda x: (x.get("port", 0), x.get("proto", ""))):
        port, proto, p_pid = p.get("port", 0), p.get("proto", ""), p.get("pid")
        key = (proto, port)
        if key in seen:
            continue
        if p_pid is not None:
            if pid is not None and p_pid == pid:
                result.append(MatchedPort(proto=proto, port=port))
                seen.add(key)
            continue
        if cat is None:
            cat = classify_service(unit, listen_ports)
        if cat != "unknown" and _PORT_INDEX.get(port) == cat:
            result.append(MatchedPort(proto=proto, port=port))
            seen.add(key)
    return result


def detect_listen_categories(listen_ports: list[JsonObject]) -> dict[str, list[MatchedPort]]:
    """수신 포트별 서비스 카테고리를 반환한다."""
    out: dict[str, list[MatchedPort]] = {}
    seen: set[tuple[str, int]] = set()
    for p in sorted(listen_ports or [], key=lambda x: (x.get("port", 0), x.get("proto", ""))):
        proto = p.get("proto", "")
        port = p.get("port", 0)
        if (proto, port) in seen:
            continue
        comm = (p.get("comm") or "").lower()
        cat = _match_keyword(comm) if comm else None
        if cat is None:
            cat = _PORT_INDEX.get(port)
        if cat is not None:
            out.setdefault(cat, []).append(MatchedPort(proto=proto, port=port))
            seen.add((proto, port))
    return out


_BASELINE_KEYWORDS: frozenset[str] = frozenset(
    {
        "sshd",
        "ssh",
        "openssh",
        "rdp",
        "termservice",
        "umrdpservice",
        "winrm",
        "wsmprovhost",
        "tigervnc",
        "x11vnc",
        "vino",
        "vncserver",
        "telnetd",
        "chronyd",
        "ntpd",
        "ntpdate",
        "timesyncd",
        "resolved",
        "rpcbind",
        "gssproxy",
        "sssd",
        "winbind",
        "systemd-",
    }
)
_BASELINE_PORTS: frozenset[int] = frozenset({22, 23, 3389, 5985, 5986, 5900, 5901, 123, 111})


def is_baseline_service(name: str | None) -> bool:
    t = (name or "").lower()
    return any(kw in t for kw in _BASELINE_KEYWORDS)


def is_baseline_socket(p: JsonObject) -> bool:
    return is_baseline_service(p.get("comm")) or p.get("port", 0) in _BASELINE_PORTS


def compute_service_categories(services: list[JsonObject] | None, listen_ports: list[JsonObject] | None) -> list[str]:
    """서비스와 수신 포트의 카테고리를 반환한다."""
    non_baseline_ports = [p for p in (listen_ports or []) if not is_baseline_socket(p)]
    cats: set[str] = set()
    for s in services or []:
        unit = s.get("unit") if isinstance(s, dict) else None
        if not unit or is_baseline_service(unit):
            continue
        cat = classify_service(unit, listen_ports, s.get("pid"))
        if cat != "unknown":
            cats.add(cat)
    cats |= set(detect_listen_categories(non_baseline_ports).keys())
    return sorted(cats)
