#!/bin/bash
# Archives the Freqtrade ledger and starts a new one from the wallet in $1 USDT. Needs a flat book
# and no meeting in progress. Without --execute it only runs the checks. Nothing is deleted; the
# restart also loads the panel on disk. A manual pause survives: lift it in the panel afterwards.
set -euo pipefail
source "$(dirname "$0")/switch_common.sh"

WALLET="${1:-}"
[[ "$WALLET" =~ ^[0-9]+$ ]] || die "usage: reset_ledger.sh <wallet USDT> [--execute]"
BRAIN_DATA="$ROOT_4/brain/data"
DB="$BRAIN_DATA/freqtrade_rules20.sqlite"
FT_CONFIG="$ROOT_4/brain/config/freqtrade.rules20.json"
LEDGER_FILES=(freqtrade_rules20.sqlite freqtrade_rules20.sqlite-wal freqtrade_rules20.sqlite-shm
  ft_meta.json risk_state.json)  # risk_state holds the old wallet's day-start equity

open_trades() {
  "$PYTHON" -I - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(con.execute("select count(*) from trades where is_open = 1").fetchone()[0])
PY
}

[ "$(open_trades)" = 0 ] || die "4.0 still has open trades"
"$PYTHON" -I - "$BRAIN_DATA/meeting_live" <<'PY' || die "a meeting is in progress"
import json, pathlib, sys, time
for path in pathlib.Path(sys.argv[1]).glob("*.json"):
    try:
        running = json.loads(path.read_text()).get("status") == "running"
    except ValueError:
        continue
    if running and time.time() - path.stat().st_mtime < 600:
        sys.exit(f"running: {path.name}")
PY
if [ "${2:-}" != "--execute" ]; then echo "checks passed; nothing changed"; exit 0; fi

stop_job_4
wait_gone "$PROCESS_4" "4.0"
if [ "$(open_trades)" != 0 ]; then  # a signal may have opened one between the check and the stop
  touch "$ROOT_4/data/planned_restart"
  start_job "$JOB_4"
  die "a trade opened during the stop; nothing was archived, 4.0 restarted"
fi
archive="$BRAIN_DATA/archive-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$archive"
cp -p "$FT_CONFIG" "$archive/"
for f in "${LEDGER_FILES[@]}"; do
  [ ! -e "$BRAIN_DATA/$f" ] || mv "$BRAIN_DATA/$f" "$archive/"
done
"$PYTHON" -I - "$FT_CONFIG" "$WALLET" <<'PY'
import re, sys
path, wallet = sys.argv[1:]
text = open(path, encoding="utf-8").read()
new, hits = re.subn(r'("dry_run_wallet":\s*)\d+', rf"\g<1>{wallet}", text)
if hits != 1:
    sys.exit("dry_run_wallet not found exactly once")
open(path, "w", encoding="utf-8").write(new)
PY
touch "$ROOT_4/data/planned_restart"
start_job "$JOB_4"
wait_up "follow/run_brain.py" "4.0"
echo "new ledger from $WALLET USDT; old one in $archive"
