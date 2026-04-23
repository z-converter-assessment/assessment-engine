/* agent.c — ZConverter metric agent (C99 + librabbitmq) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <ifaddrs.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <time.h>
#include <amqp.h>
#include <amqp_tcp_socket.h>

#define ENV_OR(k, d)  (getenv(k) ? getenv(k) : (d))
#define MAX_PAYLOAD   8192

/* ── metric collectors ───────────────────────────────────────────────────── */

static int collect_nproc(void)
{
    return (int)sysconf(_SC_NPROCESSORS_ONLN);
}

static long collect_mem_total_mb(void)
{
    FILE *f = fopen("/proc/meminfo", "r");
    if (!f) return 0;
    long kb = 0;
    char line[128];
    while (fgets(line, sizeof line, f))
        if (sscanf(line, "MemTotal: %ld kB", &kb) == 1) break;
    fclose(f);
    return kb / 1024;
}

static void collect_disks_json(char *buf, size_t cap)
{
    DIR *d = opendir("/sys/block");
    if (!d) { snprintf(buf, cap, "[]"); return; }

    size_t pos = 0;
    buf[pos++] = '[';
    int first = 1;

    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        const char *name = ent->d_name;
        if (name[0] == '.')                  continue;
        if (strncmp(name, "loop", 4) == 0)   continue;
        if (strncmp(name, "ram",  3) == 0)   continue;
        if (strncmp(name, "dm-",  3) == 0)   continue;

        char path[256];
        snprintf(path, sizeof path, "/sys/block/%s/size", name);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        unsigned long long sectors = 0;
        fscanf(f, "%llu", &sectors);
        fclose(f);

        unsigned long long bytes = sectors * 512ULL;
        char size_str[32];
        if      (bytes >= (1ULL << 30)) snprintf(size_str, sizeof size_str, "%lluG", bytes >> 30);
        else if (bytes >= (1ULL << 20)) snprintf(size_str, sizeof size_str, "%lluM", bytes >> 20);
        else                            snprintf(size_str, sizeof size_str, "%lluB", bytes);

        char entry[256];
        int n = snprintf(entry, sizeof entry,
                         "%s{\"name\":\"%s\",\"size\":\"%s\"}",
                         first ? "" : ",", name, size_str);
        if (n < 0 || pos + (size_t)n + 2 >= cap) break;
        memcpy(buf + pos, entry, (size_t)n);
        pos += (size_t)n;
        first = 0;
    }
    closedir(d);
    buf[pos++] = ']';
    buf[pos]   = '\0';
}

static void collect_ips_json(char *buf, size_t cap)
{
    struct ifaddrs *ifaddr;
    if (getifaddrs(&ifaddr) == -1) { snprintf(buf, cap, "[]"); return; }

    size_t pos = 0;
    buf[pos++] = '[';
    int first = 1;

    for (struct ifaddrs *ifa = ifaddr; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET) continue;
        struct sockaddr_in *sin = (struct sockaddr_in *)ifa->ifa_addr;
        char ip[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof ip);
        if (strcmp(ip, "127.0.0.1") == 0) continue;

        char entry[64];
        int n = snprintf(entry, sizeof entry, "%s\"%s\"", first ? "" : ",", ip);
        if (n < 0 || pos + (size_t)n + 2 >= cap) break;
        memcpy(buf + pos, entry, (size_t)n);
        pos += (size_t)n;
        first = 0;
    }
    freeifaddrs(ifaddr);
    buf[pos++] = ']';
    buf[pos]   = '\0';
}

static int build_payload(char *buf, size_t cap,
                         const char *hostname, const char *external_ip)
{
    char disks[4096], ips[1024];
    collect_disks_json(disks, sizeof disks);
    collect_ips_json(ips, sizeof ips);

    char external_json[64];
    if (external_ip && *external_ip)
        snprintf(external_json, sizeof external_json, "[\"%s\"]", external_ip);
    else
        snprintf(external_json, sizeof external_json, "[]");

    int n = snprintf(buf, cap,
        "{\"hostname\":\"%s\",\"nproc\":%d,\"mem_total_mb\":%ld,"
        "\"disks\":%s,\"ip\":{\"internal\":%s,\"external\":%s}}",
        hostname, collect_nproc(), collect_mem_total_mb(), disks, ips, external_json);
    return (n > 0 && (size_t)n < cap) ? 0 : -1;
}

/* ── AMQP helpers ────────────────────────────────────────────────────────── */

static amqp_connection_state_t mq_connect(
    const char *host, int port,
    const char *user, const char *pass, const char *vhost)
{
    amqp_connection_state_t conn = amqp_new_connection();
    amqp_socket_t *sock = amqp_tcp_socket_new(conn);
    if (!sock) goto fail;
    if (amqp_socket_open(sock, host, port) != AMQP_STATUS_OK) goto fail;

    {
        amqp_rpc_reply_t r = amqp_login(conn, vhost, 0, 131072, 0,
                                         AMQP_SASL_METHOD_PLAIN, user, pass);
        if (r.reply_type != AMQP_RESPONSE_NORMAL) goto fail;
    }
    amqp_channel_open(conn, 1);
    {
        amqp_rpc_reply_t r = amqp_get_rpc_reply(conn);
        if (r.reply_type != AMQP_RESPONSE_NORMAL) goto fail;
    }
    return conn;

fail:
    amqp_destroy_connection(conn);
    return NULL;
}

/* ── main ────────────────────────────────────────────────────────────────── */

int main(void)
{
    const char *host        = ENV_OR("RABBITMQ_HOST",        "localhost");
    int         port        = atoi(ENV_OR("RABBITMQ_PORT",   "5672"));
    const char *user        = ENV_OR("RABBITMQ_USER",        "guest");
    const char *pass        = ENV_OR("RABBITMQ_PASS",        "guest");
    const char *vhost       = ENV_OR("RABBITMQ_VHOST",       "/");
    const char *exchange    = ENV_OR("RABBITMQ_EXCHANGE",    "");
    const char *routing_key = ENV_OR("RABBITMQ_ROUTING_KEY", "metric");
    int         interval    = atoi(ENV_OR("PUBLISH_INTERVAL_SEC", "60"));
    const char *external_ip = ENV_OR("EXTERNAL_IP",          "");
    const char *dry_run_env = getenv("DRY_RUN");
    int         dry_run     = dry_run_env && strcmp(dry_run_env, "1") == 0 ? 1 : 0;

    char hostname[256];
    const char *hn = getenv("HOSTNAME_OVERRIDE");
    if (hn && *hn) {
        strncpy(hostname, hn, sizeof hostname - 1);
        hostname[sizeof hostname - 1] = '\0';
    } else {
        gethostname(hostname, sizeof hostname);
    }

    fprintf(stderr, "[info] agent started hostname=%s routing_key=%s interval=%ds%s\n",
            hostname, routing_key, interval, dry_run ? " (DRY_RUN)" : "");

    amqp_connection_state_t conn = NULL;

    for (;;) {
        char payload[MAX_PAYLOAD];
        if (build_payload(payload, sizeof payload, hostname, external_ip) != 0) {
            fprintf(stderr, "[error] payload build failed\n");
            sleep((unsigned)interval);
            continue;
        }

        if (dry_run) {
            time_t now = time(NULL);
            char ts[32];
            strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", gmtime(&now));
            fprintf(stdout, "[%s] DRY_RUN hostname=%s payload=%s\n", ts, hostname, payload);
            fflush(stdout);
            sleep((unsigned)interval);
            continue;
        }

        if (!conn) {
            conn = mq_connect(host, port, user, pass, vhost);
            if (!conn) {
                fprintf(stderr, "[warn] MQ connection failed, retry in %ds\n", interval);
                sleep((unsigned)interval);
                continue;
            }
            fprintf(stderr, "[info] connected %s:%d vhost=%s\n", host, port, vhost);
        }

        amqp_basic_properties_t props;
        props._flags = AMQP_BASIC_CONTENT_TYPE_FLAG | AMQP_BASIC_DELIVERY_MODE_FLAG;
        props.content_type  = amqp_cstring_bytes("application/json");
        props.delivery_mode = 2; /* persistent */

        int rc = amqp_basic_publish(conn, 1,
                     amqp_cstring_bytes(exchange),
                     amqp_cstring_bytes(routing_key),
                     0, 0, &props,
                     amqp_cstring_bytes(payload));

        if (rc != AMQP_STATUS_OK) {
            fprintf(stderr, "[warn] publish failed rc=%d, reconnecting\n", rc);
            amqp_destroy_connection(conn);
            conn = NULL;
        } else {
            time_t now = time(NULL);
            char ts[32];
            strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", gmtime(&now));
            fprintf(stdout, "[%s] published hostname=%s\n", ts, hostname);
            fflush(stdout);
        }

        sleep((unsigned)interval);
    }

    if (conn) amqp_destroy_connection(conn);
    return 0;
}