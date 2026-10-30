"""Mirror 2.0's positions.json into Freqtrade dry-run. Reads the file only; 2.0 is never written.

Positions already known when the bridge first runs are baselined and never followed, because a
late entry would be compared against a price from hours earlier.
"""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ftclient import FtApiError

log = logging.getLogger(__name__)

MAX_ENTER_ATTEMPTS = 3
TERMINAL = {"closed", "refused", "missed", "baseline", "failed"}


def pair_of(symbol: str) -> str:
    base = symbol.removesuffix("USDT")
    return f"{base}/USDT:USDT"


class Bridge:
    def __init__(self, client: Any, positions_path: Path, state_path: Path):
        self.client = client
        self.positions_path = positions_path
        self.state_path = state_path
        self.state: dict[str, dict] | None = None
        if state_path.exists():
            self.state = json.loads(state_path.read_text())

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=0))
        os.replace(tmp, self.state_path)

    def _read_positions(self) -> dict[str, dict] | None:
        try:
            return json.loads(self.positions_path.read_text())["positions"]
        except (OSError, ValueError, KeyError) as e:
            log.warning("positions.json unreadable this tick: %s", e)
            return None

    def _enter(self, pid: str, rec: dict, row: dict) -> None:
        qty, price, lev = rec["quantity"], rec["entry_price"], rec["leverage"]
        side = rec["side"]
        row["attempts"] = row.get("attempts", 0) + 1
        try:
            res = self.client.force_enter(
                pair_of(rec["symbol"]), side, qty * price / lev, lev, pid
            )
        except FtApiError as e:
            row["error"] = str(e)[:200]
            # A 502 is Freqtrade refusing (same pair already open, say); anything else may pass.
            if "HTTP 502" in str(e) or row["attempts"] >= MAX_ENTER_ATTEMPTS:
                row["status"] = "refused"
            return
        if res.get("trade_id") is None:
            row["error"] = str(res.get("status"))[:200]
            if row["attempts"] >= MAX_ENTER_ATTEMPTS:
                row["status"] = "refused"
            return
        row.update(status="open", trade_id=res["trade_id"], entered_at=_now())
        row.pop("error", None)

    def _exit(self, row: dict) -> None:
        trade_id = row["trade_id"]
        try:
            if not self.client.trade(trade_id).get("is_open", True):
                row.update(status="closed", closed_at=_now())
                return
            self.client.force_exit(trade_id)
        except FtApiError as e:
            row["error"] = str(e)[:200]
            return
        row["exit_sent_at"] = row.get("exit_sent_at") or _now()

    def tick(self) -> None:
        positions = self._read_positions()
        if positions is None:
            return
        if self.state is None:
            self.state = {pid: {"status": "baseline"} for pid in positions}
            self._save()
            return
        changed = False
        for pid, wrapped in positions.items():
            rec = wrapped["record"]
            row = self.state.get(pid)
            if row is None:
                row = self.state[pid] = {"status": "pending", "symbol": rec["symbol"]}
                changed = True
            if row["status"] in TERMINAL:
                continue
            status = rec["status"]
            before = dict(row)
            if row["status"] == "pending":
                if status == "failed":
                    row["status"] = "failed"
                elif status == "closed":
                    row["status"] = "missed"
                elif status == "open" and rec.get("entry_price"):
                    self._enter(pid, rec, row)
            elif row["status"] == "open" and status != "open":
                self._exit(row)
            changed = changed or row != before
        if changed:
            self._save()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
