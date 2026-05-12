#!/usr/bin/env bash
# OpenStack staging 환경 제거. deploy.sh down의 alias.
set -euo pipefail
cd "$(dirname "$0")"
exec ./deploy.sh down
