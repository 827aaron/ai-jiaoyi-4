#!/bin/bash
# Stops the full 4.0 brain, brings 2.0 back, and leaves 4.0 mirroring it. Without --execute it
# only runs the checks. 2.0 resumes with its own data/paused.json: a pause stays until the panel
# lifts it.
set -euo pipefail
source "$(dirname "$0")/switch_common.sh"

if ban_active "$ROOT_4/brain/data/rest_ban.json"; then
  die "a Binance ban is active: starting 2.0 now would hit it"
fi
for f in "${JOBS_2_START[@]}"; do [ -f "$AGENTS/$f.plist" ] || die "missing $f.plist"; done
if [ "${1:-}" != "--execute" ]; then echo "checks passed; nothing changed"; exit 0; fi

stop_job_4
wait_gone "$PROCESS_4" "4.0"
set_mode mirror
for job in "${JOBS_2_START[@]}"; do start_job "$job"; done
wait_up "$PROCESS_2" "2.0"
touch "$ROOT_4/data/planned_restart"
start_job "$JOB_4"
wait_up "follow/run_brain.py" "4.0"
echo "rolled back: 2.0 running, 4.0 mirroring"
