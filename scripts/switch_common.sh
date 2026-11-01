# Shared by switch_to_4.sh and rollback_to_2.sh. The overrides exist for tests/test_switch.py,
# which runs both scripts against a fake launchctl and throwaway trees.
ROOT_4="${ROOT_4:-/Users/aaron/huoke-agi/ai-jiaoyi-4}"
ROOT_2="${ROOT_2:-/Users/aaron/huoke-agi/ai-jiaoyi-2.0}"
AGENTS="${AGENTS:-$HOME/Library/LaunchAgents}"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
PGREP="${PGREP:-pgrep}"
PYTHON="${PYTHON:-$ROOT_4/.venv/bin/python}"
WAIT_S="${WAIT_S:-60}"
DOMAIN="gui/$(id -u)"
JOB_4=com.jiaoyi4.brain
JOBS_2_START=(com.jiaoyi.run com.jiaoyi.watchdog com.jiaoyi.panel)
JOBS_2_STOP=(com.jiaoyi.watchdog com.jiaoyi.panel com.jiaoyi.run)  # no false alarm from the watchdog
PROCESS_2="ai-jiaoyi-2.0/.venv/bin/jiaoyi run"
PROCESS_4="follow/run_brain.py|ai-jiaoyi-4/follow/ft_counted.py|jiaoyi.cli (run|panel)"
APP_TOML="$ROOT_4/brain/config/app.toml"

die() { echo "REFUSED: $*" >&2; exit 1; }

loaded() { "$LAUNCHCTL" print "$DOMAIN/$1" >/dev/null 2>&1; }

stop_job() {
  "$LAUNCHCTL" disable "$DOMAIN/$1"
  if loaded "$1"; then "$LAUNCHCTL" bootout "$DOMAIN/$1" || true; fi
}

stop_job_4() {
  if loaded "$JOB_4"; then "$LAUNCHCTL" bootout "$DOMAIN/$JOB_4" || true; fi
}

start_job() {
  "$LAUNCHCTL" enable "$DOMAIN/$1"
  loaded "$1" || "$LAUNCHCTL" bootstrap "$DOMAIN" "$AGENTS/$1.plist"
}

wait_gone() {
  local i
  for ((i = 0; i < WAIT_S; i++)); do
    "$PGREP" -f "$1" >/dev/null 2>&1 || return 0
    sleep 1
  done
  die "$2 is still running after ${WAIT_S}s"
}

wait_up() {
  local i
  for ((i = 0; i < WAIT_S; i++)); do
    "$PGREP" -f "$1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "$2 did not come up in ${WAIT_S}s"
}

ban_active() {
  "$PYTHON" -I -c '
import json, sys, time
try:
    until = float(json.load(open(sys.argv[1]))["until"])
except FileNotFoundError:
    sys.exit(1)
except Exception:
    sys.exit(0)  # unreadable counts as a ban, as in follow/guard.py
sys.exit(0 if until > time.time() else 1)' "$1"
}

set_mode() {
  "$PYTHON" -I - "$APP_TOML" "$1" <<'PY'
import re, sys
path, mode = sys.argv[1:]
text = open(path, encoding="utf-8").read()
new, hits = re.subn(r'^input_mode = "\w+"', f'input_mode = "{mode}"', text, flags=re.M)
if hits != 1:
    sys.exit("input_mode line not found exactly once")
open(path, "w", encoding="utf-8").write(new)
PY
}
