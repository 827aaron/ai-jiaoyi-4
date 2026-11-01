#!/bin/bash
# Stops 2.0 and starts the full 4.0 brain. Needs the user's own go-ahead and a flat 2.0.
# Without --execute it only runs the checks. The plists stay; rollback_to_2.sh undoes this.
set -euo pipefail
source "$(dirname "$0")/switch_common.sh"

# State the full brain continues from. Not positions, risk_state or paused: those belong to
# 2.0's account, and 4.0 keeps its own book in Freqtrade.
HANDOVER_FILES=(battle_card.json blocked_symbols.json symbol_pool.json market_view.json
  market_desk_state.json market_trail.json slow_budget.json llm_credits.json majors.json
  mid_tier.json held_events.json prompts_active.json pool_overrides.json card_flip.json)
HANDOVER_DIRS=(market_desk)

"$PYTHON" -I - "$ROOT_2/data/positions.json" <<'PY' || die "2.0 is not flat"
import json, sys
positions = json.load(open(sys.argv[1]))["positions"]
live = [k for k, v in positions.items() if v["record"]["status"] not in ("closed", "failed")]
if live:
    sys.exit(f"open in 2.0: {live}")
PY
for ban in "$ROOT_2/data/rest_ban.json" "$ROOT_4/brain/data/rest_ban.json"; do
  if ban_active "$ban"; then die "a Binance ban is active: $ban"; fi
done
[ ! -e "$ROOT_4/data/halted.json" ] || die "4.0 is halted: data/halted.json"
for f in "${JOBS_2_START[@]}"; do [ -f "$AGENTS/$f.plist" ] || die "missing $f.plist, rollback would fail"; done
[ -f "$AGENTS/$JOB_4.plist" ] || die "missing $JOB_4.plist"
[ -f "$ROOT_2/data/battle_card.json" ] || die "2.0 has no battle card to hand over"
grep -q '^input_mode = "mirror"' "$APP_TOML" || die "4.0 is not in mirror mode"
if [ "${1:-}" != "--execute" ]; then echo "checks passed; nothing changed"; exit 0; fi

for job in "${JOBS_2_STOP[@]}"; do stop_job "$job"; done
wait_gone "$PROCESS_2" "2.0"
stop_job_4
wait_gone "$PROCESS_4" "4.0"

backup="$ROOT_4/brain/data/switch-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup"
cp -p "$APP_TOML" "$backup/"
for f in "${HANDOVER_FILES[@]}"; do
  [ ! -e "$ROOT_4/brain/data/$f" ] || cp -p "$ROOT_4/brain/data/$f" "$backup/"
  [ ! -e "$ROOT_2/data/$f" ] || cp -p "$ROOT_2/data/$f" "$ROOT_4/brain/data/$f"
done
for d in "${HANDOVER_DIRS[@]}"; do
  [ ! -e "$ROOT_2/data/$d" ] || cp -Rp "$ROOT_2/data/$d" "$ROOT_4/brain/data/"
done
set_mode brain
touch "$ROOT_4/data/planned_restart"
start_job "$JOB_4"
wait_up "follow/run_brain.py" "4.0"
echo "switched: 2.0 stopped, 4.0 full brain starting; backup in $backup"
