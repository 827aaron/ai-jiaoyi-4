"""Run Freqtrade dry-run, the bridge, the guard and the daily report as one process.

The guard is read every second and the bridge every five. If this process dies Freqtrade keeps
running unguarded, so Freqtrade is put in its own process group, terminated on any exit path, and
a survivor of a hard kill is terminated at the next start.

launchd restarts this on a non-zero exit only: a refusal or a halt exits 0 and stays down.
"""

import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from bridge import Bridge
from ftclient import FtApiError, FtClient
from guard import Guard
from report import BANGKOK, DATA, POSITIONS, ROOT, write_report

SAMPLER_DONE = DATA / "sampler_done.json"
SAMPLER_MAX_PEAK = 2000
API_FILE = DATA / "api.json"
PID_FILE = DATA / "freqtrade.pid"
EVENTS = DATA / "events.jsonl"
BAN_FILE = POSITIONS.with_name("rest_ban.json")
BRIDGE_EVERY_S = 5
REPORT_AFTER_MIN = 15  # past midnight, so the shadow lines of the day's last closes have landed

log = logging.getLogger("follow")


def event(kind: str, **fields) -> None:
    with EVENTS.open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "kind": kind, **fields}) + "\n")
    log.info("%s %s", kind, fields)


def ensure_api_file() -> None:
    if API_FILE.exists():
        return
    fd = os.open(API_FILE, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"api_server": {
            "username": "follow",
            "password": secrets.token_urlsafe(24),
            "jwt_secret_key": secrets.token_urlsafe(32),
            "ws_token": secrets.token_urlsafe(24),
        }}, fh)


def stop_orphan() -> None:
    if not PID_FILE.exists():
        return
    pid = int(PID_FILE.read_text())
    seen = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    if "ft_counted.py" not in seen.stdout:  # the pid was reused or Freqtrade is already gone
        return
    os.killpg(pid, signal.SIGTERM)
    for _ in range(20):
        if subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode:
            break
        time.sleep(1)
    event("orphan_stopped", pid=pid)


def start_freqtrade() -> subprocess.Popen:
    log_file = (DATA / "freqtrade.log").open("a")
    proc = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"), str(ROOT / "follow/ft_counted.py"), "trade",
            "--config", str(ROOT / "config/freqtrade.json"), "--config", str(API_FILE),
            "--userdir", str(ROOT / "user_data"), "--db-url", f"sqlite:///{DATA / 'follow.sqlite'}",
        ],
        cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    return proc


def stop_freqtrade(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def sampler_passed() -> bool:
    if not SAMPLER_DONE.exists():
        return False
    peak = json.loads(SAMPLER_DONE.read_text())["max"]
    return peak is not None and peak < SAMPLER_MAX_PEAK


def main() -> int:
    logging.basicConfig(
        filename=DATA / "run.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not sampler_passed():
        print("refused: the 24-hour weight sample has not passed", file=sys.stderr)
        return 0
    ensure_api_file()
    client = FtClient.from_secrets(str(API_FILE))
    bridge = Bridge(client, POSITIONS, DATA / "bridge_state.json")
    guard = Guard(
        BAN_FILE, DATA / "ip_weight_now.json", DATA / "ft_limited.json", DATA / "restarts.json",
    )
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    stop_orphan()
    proc: subprocess.Popen | None = None
    reported = datetime.now(BANGKOK).date() - timedelta(days=1)
    last_bridge = 0.0
    try:
        while True:
            now = time.time()
            reason = guard.stop_reason(now)
            running = proc is not None and proc.poll() is None
            if reason and running:
                stop_freqtrade(proc)
                event("freqtrade_stopped", reason=reason)
                running = False
            elif not reason and not running:
                if proc is not None:
                    event("freqtrade_gone", code=proc.returncode)
                if guard.allow_restart(now):
                    proc = start_freqtrade()
                    event("freqtrade_started", pid=proc.pid)
                    last_bridge = now + 30  # the API needs a while after startup
                else:
                    event("halted", reason=guard.halted)
                    return 0
            if guard.halted:
                event("halted", reason=guard.halted)
                return 0
            if running and now - last_bridge >= BRIDGE_EVERY_S:
                last_bridge = now
                try:
                    client.health()
                    bridge.tick()
                except FtApiError as e:
                    log.warning("freqtrade api not answering: %s", e)
            local = datetime.now(BANGKOK)
            yesterday = local.date() - timedelta(days=1)
            if reported < yesterday and local.hour * 60 + local.minute >= REPORT_AFTER_MIN:
                event("report", path=str(write_report(yesterday)))
                reported = yesterday
            time.sleep(1)
    finally:
        stop_freqtrade(proc)


if __name__ == "__main__":
    sys.exit(main())
