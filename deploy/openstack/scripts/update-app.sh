#!/usr/bin/env bash
# 앱 VM만 코드 재배포 (DB·MW 영향 없음). deploy.sh update-app의 alias.
set -euo pipefail
cd "$(dirname "$0")"
exec ./deploy.sh update-app
