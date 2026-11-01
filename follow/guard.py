"""Decide whether Freqtrade may run. Pure logic; run.py owns the process.

A ban on the shared IP or a heavy minute pauses Freqtrade and it comes back by itself. A 418/429 received by
Freqtrade itself is ours to answer for, so that one halts until a person clears it.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

WEIGHT_STOP = 2000
WEIGHT_PAUSE_S = 600
BAN_MARGIN_S = 1800
MAX_RESTARTS_PER_DAY = 3


@dataclass
class Guard:
    ban_file: Path
    weight_file: Path
    limited_file: Path
    restarts_file: Path | None = None
    resume_at: float = 0.0
    halted: str | None = None
    restarts: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.restarts_file and self.restarts_file.exists():
            self.restarts = json.loads(self.restarts_file.read_text())

    def _ban_until(self, now: float) -> float:
        try:
            until = float(json.loads(self.ban_file.read_text())["until"])
        except FileNotFoundError:
            return 0.0
        except (OSError, ValueError, KeyError, TypeError):
            return now + 60  # an unreadable file is treated as a ban, re-read in a minute
        return until if until > now else 0.0

    def _weight_now(self, now: float) -> int:
        try:
            raw = json.loads(self.weight_file.read_text())
        except (OSError, ValueError):
            return 0
        same_minute = int(raw["ts"] // 60) == int(now // 60)
        return int(raw["used"]) if same_minute else 0

    def stop_reason(self, now: float | None = None) -> str | None:
        """Why Freqtrade must not run right now; None means it may."""
        t = time.time() if now is None else now
        if self.halted:
            return self.halted
        if self.limited_file.exists():
            self.halted = f"freqtrade rate-limited: {self.limited_file.read_text()[:200]}"
            return self.halted
        until = self._ban_until(t)
        if until:
            self.resume_at = max(self.resume_at, until + BAN_MARGIN_S)
            return f"ban until {until:.0f}"
        used = self._weight_now(t)
        if used > WEIGHT_STOP:
            self.resume_at = max(self.resume_at, t + WEIGHT_PAUSE_S)
            return f"ip weight {used}"
        if t < self.resume_at:
            return f"waiting until {self.resume_at:.0f}"
        return None

    def allow_restart(self, now: float | None = None) -> bool:
        """Count one restart; past the daily cap the guard halts, so a loop cannot burn weight."""
        t = time.time() if now is None else now
        day = datetime.fromtimestamp(t, UTC).date().isoformat()
        self.restarts[day] = self.restarts.get(day, 0) + 1
        if self.restarts_file:  # launchd restarts this process, the cap must outlive it
            self.restarts_file.write_text(json.dumps(self.restarts))
        if self.restarts[day] > MAX_RESTARTS_PER_DAY:
            self.halted = f"more than {MAX_RESTARTS_PER_DAY} restarts on {day}"
            return False
        return True
