"""서비스 카테고리 분류·포트 매핑 — `SERVICE_CATALOG` 단일 카탈로그.

consumer(ingest 사전계산)와 web(표시·필터)이 같은 카탈로그를 쓰므로 web 역의존이 0 이라야 한다.
서비스 추가는 카탈로그 1곳만 고친다 — 분류·포트·드롭다운·뱃지 CSS 가 전부 여기서 파생한다.
다중 신호 우선순위·카테고리 경계 규약은 docs/reference/web/services.md.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


@dataclass
class MatchedPort:
    """서비스 유닛에 매핑된 listen 포트 1개."""

    proto: str
    port: int


@dataclass(frozen=True)
class CategoryDef:
    """서비스 카테고리 1개의 분류 규약.

    name_keywords 는 unit 이름과 프로세스 comm 양쪽에 적용한다 (Linux unit·Windows SCM 이름·exe basename 통합).
    Windows SCM 이름은 정규화 없이 들어와(MSSQLSERVER / MSSQL$INSTANCE / W3SVC) 변형이 커서, SCM 이름과 exe
    basename 을 함께 등록해 흡수한다.
    port_names 는 comm 이 없는(비루트 agent) 구간의 폴백 — 키는 normalized 서비스명(소문자·`.service` 제거)이라
    형태가 다른 키는 조회에 걸리지 않는다.
    """

    key: str
    name_keywords: tuple[str, ...]
    port_names: dict[str, tuple[int, ...]]
    badge_class: str
    # 표시 전용(분류 무관). desc_ko 제품명은 큐레이션된 예시일 뿐 매칭 진실은 name_keywords 단독.
    label_ko: str
    desc_ko: str
    # 런타임 스택(docker+containerd 등)은 여러 서비스로 떠도 1 워크로드 — 인스턴스 합이 아니라 호스트당 1로 집계.
    single_instance: bool = False


# 카테고리 경계·tie-break 규약과 순서(= 분류 우선순위)는 docs/reference/web/services.md "카테고리 경계".
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
            "w3svc",  # IIS (Windows SCM 서비스명)
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
            "tomcat": (8080,),  # 나머지 app server 는 포트 가변 — 이름/comm 신호로만 식별
        },
        badge_class="badge-cat-web",
        label_ko="웹 / 애플리케이션",
        desc_ko="Nginx·Apache·HAProxy·Caddy·IIS·Tomcat 등 웹 서버·리버스 프록시·애플리케이션 서버(WAS)",
    ),
    CategoryDef(
        key="db",
        name_keywords=(
            "postgresql",
            "postgres",  # comm basename
            "mariadb",
            "mysqld",
            "mysql",
            "mongod",
            "mongodb",
            "cassandra",
            "influxdb",
            "sqlservr",  # SQL Server exe basename (Windows)
            "mssql",  # SQL Server SCM 서비스명 (MSSQLSERVER / MSSQL$INSTANCE)
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
            "clickhouse": (8123, 9000),  # HTTP + native TCP
            "db2": (50000,),
        },
        badge_class="badge-cat-db",
        label_ko="데이터베이스",
        desc_ko="PostgreSQL·MySQL/MariaDB·MongoDB·SQL Server·Oracle·Elasticsearch 등 관계형·NoSQL·검색 데이터 저장소",
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
        label_ko="캐시 / 인메모리",
        desc_ko="Redis·Memcached·Valkey·Varnish 등 인메모리 캐시·콘텐츠 가속",
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
            "redpanda",  # kafka 와 9092 공유
            "emqx",
            "artemis",  # activemq 와 61616 공유
        ),
        port_names={
            "rabbitmq-server": (5672, 15672),
            "rabbitmq": (5672, 15672),
            "kafka": (9092,),
            "nats-server": (4222, 8222),
            "activemq": (61616, 8161),
            "mosquitto": (1883, 8883),  # MQTT default + TLS
            "pulsar": (6650,),
            "emqx": (1883,),
        },
        badge_class="badge-cat-mq",
        label_ko="메시지 큐",
        desc_ko="RabbitMQ·Kafka·NATS·ActiveMQ·MQTT(Mosquitto/EMQX) 등 메시지 브로커",
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
        port_names={},  # 외부 listen 포트 표준 없음 — 이름 신호로만 식별
        badge_class="badge-cat-container",
        label_ko="컨테이너 런타임",
        desc_ko="Docker·containerd·Kubernetes(kubelet/k3s)·Podman 등 컨테이너 런타임 스택 (호스트당 1로 집계)",
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
            "zabbix": (10050, 10051),  # agent / server
            "alertmanager": (9093,),
            "victoriametrics": (8428,),
            "loki": (3100,),
            "netdata": (19999,),
        },  # grafana 3000 은 일반 Node 앱과 충돌 위험이라 제외 — 이름/comm 신호로만 식별
        badge_class="badge-cat-monitor",
        label_ko="모니터링 / 관측",
        desc_ko="Prometheus·Grafana·Zabbix·node_exporter·Telegraf·Loki 등 메트릭·로그 수집 에이전트",
    ),
    CategoryDef(
        key="remote",
        name_keywords=(
            "sshd",
            "ssh",
            "openssh",
            "rdp",
            "termservice",  # RDP SCM 서비스명 (Windows Terminal Services)
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
        label_ko="원격 접속 / 관리",
        desc_ko="SSH·RDP·WinRM·VNC 등 원격 접속·관리 서비스 (관리 표면 — 대부분 호스트에 존재)",
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
            "tgtd",  # iSCSI 타깃
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
        label_ko="파일 / 스토리지 공유",
        desc_ko="NFS·SMB(Samba)·FTP·iSCSI·MinIO 등 파일 공유·네트워크 스토리지",
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
            "named",  # BIND
            "dnsmasq",
            "unbound",
            "pdns",
            "coredns",
            "knot",
            "dhcpd",
            "dhcp",
            "kea",  # ISC Kea DHCP
            "chronyd",
            "ntpd",
            "slapd",  # OpenLDAP
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
        label_ko="네트워크 인프라",
        desc_ko="DNS(BIND·dnsmasq)·DHCP·NTP·LDAP·SNMP·프록시 등 네트워크 기반 서비스",
    ),
)

# --- 파생 인덱스 (import 시점 1회, 재계산 0) ---------------------------------

# (keyword, category) 순서 = 카탈로그 등장 순 = 첫 매칭 우선.
_NAME_INDEX: tuple[tuple[str, str], ...] = tuple((kw, d.key) for d in SERVICE_CATALOG for kw in d.name_keywords)

_NAME_PORTS: dict[str, tuple[int, ...]] = {name: ports for d in SERVICE_CATALOG for name, ports in d.port_names.items()}


# cross-category 포트 충돌은 카탈로그 순서가 이긴다.
def _build_port_index() -> dict[int, str]:
    index: dict[int, str] = {}
    for d in SERVICE_CATALOG:
        for ports in d.port_names.values():
            for port in ports:
                index.setdefault(port, d.key)
    return index


_PORT_INDEX: dict[int, str] = _build_port_index()

SERVICE_CATEGORIES: tuple[str, ...] = tuple(d.key for d in SERVICE_CATALOG)

# 시그니처 워크로드 — 환경 성격을 규정하는 티어만. 어디에나 있는 유틸/관리(file·mail·infra·remote)는 구별력이 0.
SIGNATURE_CATEGORIES: tuple[str, ...] = ("web", "db", "cache", "mq", "container", "monitor")

SINGLE_INSTANCE_CATEGORIES: frozenset[str] = frozenset(d.key for d in SERVICE_CATALOG if d.single_instance)

BADGE_CLASS_BY_CATEGORY: dict[str, str] = {d.key: d.badge_class for d in SERVICE_CATALOG}
BADGE_CLASS_BY_CATEGORY["unknown"] = "badge-cat-unknown"


# --- 분류 ------------------------------------------------------------------


def _match_keyword(text: str) -> str | None:
    if not text:
        return None
    for keyword, category in _NAME_INDEX:
        if keyword in text:
            return category
    return None


def _attributed_ports(unit: str, listen_ports: list[JsonObject], pid: int | None = None) -> list[JsonObject]:
    """unit 에 귀속된 listen_port dict 목록 — pid 로 정확 join, pid 부재 시 comm~name -> well-known 포트 폴백.

    comm=="systemd" 는 귀속에서 뺀다. 소켓 액티베이션 리스너(pid null)의 보유자는 systemd 매니저라 comm 이
    "systemd" 인데, 이 generic 이름이 양방향 substring 으로 모든 systemd-*.socket 유닛명에 오매칭돼 매니저가 든
    22(ssh) 등 타 소켓까지 흡입한다(최저 well-known 포트로 오분류). 매니저 placeholder 는 특정 유닛 소유 증거가
    아니다. systemd-resolved 등 자기 comm 을 가진 데몬은 정상 매칭된다.
    """
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
    """서비스 unit -> 카테고리. 다중 신호(name -> comm -> port), 미매칭 시 "unknown".

    순서는 정밀도 순이다 — 소프트웨어 정체성(name/comm)이 프로토콜(port)보다 정밀해서, haproxy 가 5432 를
    프록시해도 db 가 아니라 web 이다.

    listen_ports 미제공(목록 화면 등 경량 SELECT) 시 name 신호만 쓴다.
    """
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
    """서비스 유닛에 연관된 listen 포트 — 전 화면 공유 단일 규칙.

    포트에 소유 pid 가 있으면 그 pid 유닛에만 귀속한다 — .service/.socket·동일 comm 유닛 간 이중 귀속 차단
    (22 는 ssh.service 에만, sshd-unix-local.socket 엔 안 붙는다). pid 가 없는 포트(소켓 액티베이션·비-systemd)는
    소유 프로세스가 없으므로 유닛 카테고리(classify)와 같은 카테고리의 포트만 폴백 귀속하고, 카테고리 없는
    (unknown) 유닛엔 안 붙인다 — 68 DHCP 등 OS 내부 포트 노이즈 제거. 동일 포트라도 proto 가 다르면 별도 항목.
    """
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
    """listen 소켓을 services unit 과 무관하게 직접 분류 — 카테고리 -> 근거가 된 포트 목록.

    어떤 service unit 에도 속하지 않는 소켓(비-service 프로세스)을 comm(exe basename) -> port 순으로 잡아
    호스트 워크로드 union 을 보완한다 (tradeoffs T15). 카탈로그에 등재된 comm·port 만 잡힌다.
    """
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


# --- baseline(OS 기본·관리) 서비스 — 특징 워크로드 아님 (목록·환경분포 제외) --------------
# 원격 접속·OS 기본 인프라 클라이언트는 거의 모든 호스트에 있어 구별력이 0. 인식(상세 live classify)은 유지하고
# "이 서버의 특징" 집계에서만 뺀다.
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
        # systemd 자체 유닛 — 포트 문맥 classify 가 remote/file/infra 로 오귀속하는 노이즈 차단
        # (워크로드 유닛엔 "systemd-" 접두가 없다).
        "systemd-",
    }
)
_BASELINE_PORTS: frozenset[int] = frozenset({22, 23, 3389, 5985, 5986, 5900, 5901, 123, 111})


def is_baseline_service(name: str | None) -> bool:
    """OS 기본·관리 서비스(원격 접속·NTP·RPC·auth 클라이언트) 여부 — 특징 워크로드 필터."""
    t = (name or "").lower()
    return any(kw in t for kw in _BASELINE_KEYWORDS)


def is_baseline_socket(p: JsonObject) -> bool:
    """listen 소켓이 baseline 인가 — comm 또는 port 기준."""
    return is_baseline_service(p.get("comm")) or p.get("port", 0) in _BASELINE_PORTS


def compute_service_categories(services: list[JsonObject] | None, listen_ports: list[JsonObject] | None) -> list[str]:
    """ingest 사전계산 — 호스트 특징 워크로드 카테고리 키 집합 (정렬·dedup, "unknown"·baseline 제외).

    inventory upsert 시 1회 계산해 `server_inventory.service_categories` 에 저장한다 — 목록·환경분포·필터가
    같은 저장값을 읽어야 화면 간 카테고리 집합이 어긋나지 않는다.
    """
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
