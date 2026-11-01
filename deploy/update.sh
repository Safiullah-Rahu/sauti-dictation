#!/usr/bin/env bash
# Update Sauti in place from the checkout this script lives in (run as root).
# Takes a backup first; clients keep working during the ~5 s restart and resend any
# dictation that was in flight (see docs/protocol.md, "Reliability rules").
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
/usr/local/sbin/sauti-backup
git -C "$REPO_DIR" pull --ff-only
"$REPO_DIR/deploy/install.sh"
systemctl restart sauti.service
sleep 3
curl -fsS http://127.0.0.1:8700/v1/health && echo " - updated"
