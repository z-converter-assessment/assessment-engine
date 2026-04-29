#!/usr/bin/env bash
# 에이전트 컨테이너 메인 루프 — 환경변수는 .env 참고

set -uo pipefail

API_URL="${INGEST_API_URL}/ingest/"
INTERVAL="${PUSH_INTERVAL}"

push_once() {
  local hostname_val
  hostname_val=$(hostname)

  local nproc
  nproc=$(nproc)

  local mem_total_mb
  mem_total_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)

  local lsblk_raw
  lsblk_raw=$(lsblk -J -o NAME,SIZE,TYPE 2>/dev/null \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps([{'name': x['name'], 'size': x['size']} for x in d['blockdevices'] if x.get('type') == 'disk']))
" 2>/dev/null) || lsblk_raw="[]"

  local internal_ips
  internal_ips=$(ip -j addr show 2>/dev/null \
    | python3 -c "
import sys, json
addrs = json.load(sys.stdin)
ips = [a['local'] for i in addrs for a in i.get('addr_info', [])
       if a.get('family') == 'inet' and a['local'] != '127.0.0.1']
print(json.dumps(ips))
" 2>/dev/null) || internal_ips="[]"

  local payload
  payload=$(python3 -c "
import json
print(json.dumps({
    'hostname': '${hostname_val}',
    'nproc': '${nproc}',
    'free': {'mem_total_mb': ${mem_total_mb}},
    'lsblk_raw': ${lsblk_raw},
    'ip_raw': {'internal': ${internal_ips}, 'external': []}
}))
")

  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${API_URL}" \
    -H "Content-Type: application/json" \
    -d "${payload}" \
    --max-time 10 \
    --retry 3 \
    --retry-delay 5) || http_code="000"

  echo "[$(date -Iseconds)] hostname=${hostname_val} status=${http_code}"
}

echo "[$(date -Iseconds)] Agent started — target=${API_URL} interval=${INTERVAL}s"

while true; do
  push_once
  sleep "${INTERVAL}"
done
