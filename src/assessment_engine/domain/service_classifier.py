"""서비스 카테고리 분류·포트 매핑 — 단일 카탈로그 (E7).

consumer(ingest 사전계산)와 web(표시·필터)이 같은 카탈로그를 쓰고, web 역의존이 0 이라야 한다.

`SERVICE_CATALOG` 가 카테고리별 분류 규약의 단일 진실. 분류·포트·드롭다운·뱃지 CSS 가
모두 본 카탈로그에서 파생 (import 시점 1회). 서비스 추가 = 카탈로그 1곳만 수정.

분류는 다중 신호 — unit 이름 / 프로세스 comm / listen 포트. 우선순위는 정밀도 순
(name -> comm -> port): 소프트웨어 정체성(name/comm)이 프로토콜(port)보다 카테고리
정밀도가 높다 (예: haproxy 가 5432 를 프록시해도 web 이지 db 가 아님). port 는
name/comm 이 무정보일 때(특히 Windows SCM 이름 변형) 의 fallback.

Windows SCM 이름은 정규화 없이 들어와(MSSQLSERVER / MSSQL$INSTANCE / W3SVC 등) 이름
변형이 크다 -> name_keywords 에 SCM 이름·exe basename substring 을 함께 등록해 흡수.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assessment_engine.json_types import JsonObject


@dataclass
class MatchedPort:
    """서비스 유닛에 매핑된 listen 포트 1개. `matched_ports`/`detect_listen_categories` 결과 단위.

    분류 도메인 개념이라 본 모듈(domain)에 정의 — web view_model(`ServiceItem.ports`)이 re-export 소비.
    consumer(ingest)·web 양쪽이 web 역의존 없이 분류 단일 진실을 import (right_sizing.py 도메인 패턴 정합).
    """

    proto: str
    port: int


@dataclass(frozen=True)
class CategoryDef:
    """서비스 카테고리 1개의 분류 규약.

    name_keywords: unit 이름·프로세스 comm 양쪽에 적용하는 substring 키워드.
                   (Linux unit + Windows SCM 이름 + exe basename 통합)
    port_names: normalized 서비스명 -> well-known 포트. comm 부재(비루트 agent) 시
                포트 표시(`matched_ports`)·port 신호 분류의 폴백.
    badge_class: 표시 CSS 클래스 (templating filter 가 파생 import).
    """

    key: str
    name_keywords: tuple[str, ...]
    port_names: dict[str, tuple[int, ...]]
    badge_class: str
    # 참고자료 표시용 한국어 — 카테고리 뜻(label_ko) + 대표 제품·범위(desc_ko). 분류 로직과 무관, 표시 전용.
    # desc_ko 의 제품명은 큐레이션된 대표 예시(읽기용)일 뿐 매칭 진실은 name_keywords (exhaustive) 단일.
    label_ko: str
    desc_ko: str
    # 런타임 스택 카테고리 — 구성 요소가 여러 서비스로 떠도(docker+containerd, kubelet+containerd)
    # 1개 워크로드다. 카운트를 인스턴스 합이 아니라 호스트당 1로 집계 (뱃지·role·환경분포 일관).
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
            "openresty",  # nginx 기반 LuaJIT 플랫폼
            "tomcat",  # Java 서블릿 컨테이너
            "jetty",
            "php-fpm",  # PHP FastCGI Process Manager
            "gunicorn",  # Python WSGI
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
            "postgres",  # comm basename (PostgreSQL 프로세스명)
            "mariadb",
            "mysqld",
            "mysql",
            "mongod",
            "mongodb",
            "cassandra",
            "influxdb",
            "sqlservr",  # SQL Server exe basename (Windows)
            "mssql",  # SQL Server SCM 서비스명 (MSSQLSERVER / MSSQL$INSTANCE)
            "oracle",  # Oracle Database (tnslsnr/ora_*)
            "elasticsearch",  # 검색·분석 데이터 저장소
            "opensearch",  # Elasticsearch fork
            "clickhouse",  # 컬럼 지향 OLAP
            "db2",  # IBM Db2
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
            "valkey",  # Redis fork (Linux Foundation)
            "keydb",  # 멀티스레드 Redis fork
            "dragonfly",  # Redis 호환 in-memory store
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
            "mosquitto",  # MQTT broker (가장 가벼운 mq — EPEL 표준 패키지)
            "pulsar",  # Apache Pulsar
            "redpanda",  # Kafka 호환 (9092 — kafka 포트 공유)
            "emqx",  # MQTT broker (1883 — mosquitto 포트 공유)
            "artemis",  # ActiveMQ Artemis (61616 공유)
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
            "podman",  # rootless OCI 런타임
            "crio",  # CRI-O (Kubernetes 런타임)
            "k3s",  # 경량 Kubernetes
            "k0s",
        ),
        port_names={},  # 외부 listen 포트 표준 없음 — 이름 신호로만 식별
        badge_class="badge-cat-container",
        label_ko="컨테이너 런타임",
        desc_ko="Docker·containerd·Kubernetes(kubelet/k3s)·Podman 등 컨테이너 런타임 스택 (호스트당 1로 집계)",
        single_instance=True,  # docker+containerd 등은 1 런타임 스택 — 호스트당 1로 카운트
    ),
    CategoryDef(
        key="monitor",
        name_keywords=(
            "prometheus",
            "grafana",
            "datadog",
            "node_exporter",
            "zabbix",
            "telegraf",  # InfluxData 메트릭 agent
            "collectd",
            "fluentd",  # 로그 수집
            "fluent-bit",
            "filebeat",  # Elastic Beats
            "victoriametrics",
            "loki",  # Grafana 로그 집계
            "netdata",
            "alertmanager",  # Prometheus Alertmanager
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
            "rdp",  # Windows 원격 데스크톱
            "termservice",  # Windows Terminal Services (RDP SCM 서비스명)
            "umrdpservice",
            "winrm",  # Windows 원격 관리 (WS-Management)
            "wsmprovhost",
            "tigervnc",
            "x11vnc",
            "vino",  # GNOME VNC
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
            "minio",  # 오브젝트 스토리지
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

# normalized 서비스명 -> well-known 포트.
_NAME_PORTS: dict[str, tuple[int, ...]] = {name: ports for d in SERVICE_CATALOG for name, ports in d.port_names.items()}


# port -> category (port 신호 분류). cross-category 충돌 시 카탈로그 순서 우선.
def _build_port_index() -> dict[int, str]:
    index: dict[int, str] = {}
    for d in SERVICE_CATALOG:
        for ports in d.port_names.values():
            for port in ports:
                index.setdefault(port, d.key)
    return index


_PORT_INDEX: dict[int, str] = _build_port_index()

# list 페이지 dropdown option.
SERVICE_CATEGORIES: tuple[str, ...] = tuple(d.key for d in SERVICE_CATALOG)

# 시그니처 워크로드 — 환경 성격(아키텍처)을 규정하는 티어. file·mail·infra(chronyd/sssd)·remote(sshd)는
# 어디에나 있는 유틸/관리 서비스라 "이 환경이 어떤 느낌인가" 신호가 약해 제외. 환경 개요 "주요 워크로드"
# 표시 필터 전용 — 목록·상세·필터·보고서는 전체 워크로드(비 baseline) 유지.
SIGNATURE_CATEGORIES: tuple[str, ...] = ("web", "db", "cache", "mq", "container", "monitor")

# 런타임 스택 카테고리 — 카운트를 호스트당 1로 (인스턴스 합 금지). 현재 container 만.
SINGLE_INSTANCE_CATEGORIES: frozenset[str] = frozenset(d.key for d in SERVICE_CATALOG if d.single_instance)

# 뱃지 CSS — templating.filters 가 파생 import (unknown 포함).
BADGE_CLASS_BY_CATEGORY: dict[str, str] = {d.key: d.badge_class for d in SERVICE_CATALOG}
BADGE_CLASS_BY_CATEGORY["unknown"] = "badge-cat-unknown"


# --- 분류 ------------------------------------------------------------------


def _match_keyword(text: str) -> str | None:
    """name/comm substring 을 카탈로그 키워드와 매칭 — 첫 매칭 카테고리 또는 None."""
    if not text:
        return None
    for keyword, category in _NAME_INDEX:
        if keyword in text:
            return category
    return None


def _attributed_ports(unit: str, listen_ports: list[JsonObject], pid: int | None = None) -> list[JsonObject]:
    """unit 에 귀속된 listen_port dict 목록.

    pid 제공 시(agent 가 services 에 pid 발행) 동일 pid 소켓을 정확 join — services<->listen_ports 확정 귀속.
    pid 부재(비-systemd EL6·Windows NT5·소켓 액티베이션)면 comm~name substring -> name well-known 포트 순 fallback.
    동일 (proto, port) 중복 제거. comm/port 신호 분류·`matched_ports` 공용 진입점.

    comm=="systemd" 제외: 소켓 액티베이션 리스너(pid null)의 보유자는 systemd 매니저라 comm="systemd" 인데,
    generic "systemd" 가 양방향 substring 으로 모든 systemd-*.socket 유닛명에 오매칭돼 매니저가 든 22(ssh) 등
    타 소켓을 흡입 -> 최저 well-known 포트로 오분류(예: 전 systemd 소켓 remote)되는 누출을 차단. 매니저 placeholder
    는 특정 유닛 소유 증거가 아니므로 귀속 대상 아님. systemd-resolved 등 자기 comm 데몬은 정상 매칭(영향 0).
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
        # comm=="systemd"(소켓 액티베이션 매니저 placeholder)는 귀속 제외 — substring 오매칭 누출 차단(docstring).
        comm_match = bool(comm) and comm != "systemd" and (name in comm or comm in name)
        if comm_match or port in well_known:
            result.append(p)
            seen.add(key)

    return result


def classify_service(unit: str, listen_ports: list[JsonObject] | None = None, pid: int | None = None) -> str:
    """서비스 unit -> 카테고리. 다중 신호 (name -> comm -> port), 미매칭 시 "unknown".

    listen_ports 미제공(목록 화면 등 경량 SELECT) 시 name 신호만 사용. pid 제공 시 services<->listen_ports
    정확 join 으로 comm/port 신호 귀속 (pid 부재면 comm~name 휴리스틱).
    """
    name = unit.lower().removesuffix(".service")
    # 1. unit 이름 키워드 (최고 정밀 — 소프트웨어 정체성)
    cat = _match_keyword(name)
    if cat is not None:
        return cat

    if listen_ports:
        attributed = _attributed_ports(unit, listen_ports, pid)
        # 2. comm 키워드 (이름 미스매치 흡수 — Windows SCM 이름과 exe basename 불일치 등)
        for p in attributed:
            cat = _match_keyword((p.get("comm") or "").lower())
            if cat is not None:
                return cat
        # 3. port (프로토콜 사실관계 — 1433 -> db)
        for p in attributed:
            cat = _PORT_INDEX.get(p.get("port", 0))
            if cat is not None:
                return cat

    return "unknown"


def matched_ports(unit: str, listen_ports: list[JsonObject], pid: int | None = None) -> list[MatchedPort]:
    """서비스 유닛에 연관된 listen 포트 — 단일 규칙(전 화면 공유).

    포트 귀속 규칙:
    - 포트에 소유 pid 가 있으면 그 pid 유닛에만 귀속(정확). 다른 유닛엔 안 붙임 — .service/.socket·동일 comm 유닛
      간 이중 귀속 차단(예: 22 는 ssh.service(pid) 만, sshd-unix-local.socket/sshd-vsock.socket 엔 미귀속).
    - 포트에 pid 가 없으면(소켓 액티베이션 pid null·비-systemd) 소유 프로세스 부재라, 유닛 카테고리(classify)로
      매핑되는 포트(_PORT_INDEX 동일 카테고리)만 fallback 귀속 — ssh.socket(remote)->22, systemd-resolved(infra)->53.
      카테고리 없는(unknown) 유닛엔 미귀속(68 DHCP 등 OS 내부 포트 노이즈 제거).
    동일 포트라도 proto(tcp/tcp6/udp) 다르면 별도 항목.
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
            # 소유 프로세스 있는 포트 — 그 pid 유닛에만
            if pid is not None and p_pid == pid:
                result.append(MatchedPort(proto=proto, port=port))
                seen.add(key)
            continue
        # pid 없는 포트(소유 프로세스 부재) — 카테고리-일관 fallback
        if cat is None:
            cat = classify_service(unit, listen_ports)
        if cat != "unknown" and _PORT_INDEX.get(port) == cat:
            result.append(MatchedPort(proto=proto, port=port))
            seen.add(key)
    return result


def detect_listen_categories(listen_ports: list[JsonObject]) -> dict[str, list[MatchedPort]]:
    """listen 소켓을 카테고리로 직접 분류 — services unit 과 무관 (T15 보완).

    per-unit 분류(`classify_service`)가 pid join 으로 정확해진 뒤에도, 어떤 service unit 에도 속하지 않는
    listen 소켓(비-service 프로세스)은 여전히 이 경로가 comm(exe basename)·port 로 직접 잡는다 —
    "이 호스트가 무슨 워크로드를 listen 하나"의 host union 보완.

    소켓별 우선순위: comm 키워드 -> port 인덱스. 카테고리 -> 그 근거가 된 포트 목록 반환.
    동일 (proto, port) 중복 제거. 워크로드 카테고리(카탈로그 등재 comm·port)만 잡힘 — ssh 등 제외.
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
# 원격 접속(전 호스트 관리 표면) + OS 기본 인프라 클라이언트(NTP·DNS resolver·RPC·auth 클라이언트)는 거의 모든
# 호스트에 있어 구별력이 0 — 인식(상세 live classify)은 유지하되 "이 서버의 특징" 신호로는 안 친다.
# compute_service_categories(저장값 = 목록·환경분포·필터 소스)·workload_category_counter 에서 제외.
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
        # systemd 자체 유닛(journald·coredump·udevd·networkd 등)은 OS 제공물 — 특징 워크로드 아님. 포트 문맥
        # classify 가 이들을 remote/file/infra 로 오귀속하는 노이즈 차단(워크로드 유닛엔 "systemd-" 접두 없음).
        "systemd-",
    }
)
_BASELINE_PORTS: frozenset[int] = frozenset({22, 23, 3389, 5985, 5986, 5900, 5901, 123, 111})


def is_baseline_service(name: str | None) -> bool:
    """OS 기본·관리 서비스(원격 접속·NTP·RPC·auth 클라이언트) 여부 — 특징 워크로드 필터. 상세는 미적용(전부 인식)."""
    t = (name or "").lower()
    return any(kw in t for kw in _BASELINE_KEYWORDS)


def is_baseline_socket(p: JsonObject) -> bool:
    """listen 소켓이 baseline(관리·OS 기본) 인가 — comm 또는 port 기준. 특징 워크로드 필터."""
    return is_baseline_service(p.get("comm")) or p.get("port", 0) in _BASELINE_PORTS


def compute_service_categories(services: list[JsonObject] | None, listen_ports: list[JsonObject] | None) -> list[str]:
    """ingest 사전계산 — 호스트 "특징 워크로드" 카테고리 키 집합 (정렬·dedup, "unknown"·baseline 제외).

    services unit 이름 분류(`classify_service`: name->comm->port)와 listen 소켓 직접 분류(`detect_listen_categories`).
    baseline(OS 기본·관리 — SSH·NTP·RPC 등)은 제외 — 거의 전 호스트에 있어 특징 신호 아님(상세 live classify 는 유지).
    inventory upsert 시 1회 계산해 `server_inventory.service_categories` 저장 -> 목록·환경분포·필터 소비(화면 간 일치).
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
