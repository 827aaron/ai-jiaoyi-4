"""Run 4.0: guarded Freqtrade dry-run plus the brain and the panel, as one launchd job.

The guard governs Freqtrade only: in mirror mode nothing else talks to Binance, and the full
brain obeys its own REST budget and the ban file this job watches. launchd restarts any non-zero
exit, a stray SIGTERM included; only a guard halt exits 0, stays down and raises a desktop
notification, because nobody restarts it. `launchctl bootout` stops the job for good.

Two full brains on one IP would double the market load, so the brain is not started in full mode
while 2.0's job is loaded or its process is alive. Freqtrade keeps running either way.
"""

import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from guard import Guard
from report import DATA, POSITIONS, ROOT

BRAIN = ROOT / "brain"
API_FILE = BRAIN / "data/api.json"
PIDS = BRAIN / "data/run_brain.pids"
PLANNED = DATA / "planned_restart"  # written by the switch scripts, spends no crash budget
RUN_20_LABEL = "com.jiaoyi.run"
RUN_20_PROCESS = "ai-jiaoyi-2.0/.venv/bin/jiaoyi run"
EVENTS = DATA / "events.jsonl"
CHILD_RETRY_S = 30
HEARTBEAT_EVERY_S = 60
READY_URL = "http://127.0.0.1:8083/api/v1/ping"

log = logging.getLogger("run_brain")


def event(kind: str, **fields) -> None:
    with EVENTS.open("a") as fh:
        fh.write(json.dumps({"ts": time.time(), "kind": kind, **fields}) + "\n")
    log.info("%s %s", kind, fields)


def full_brain() -> bool:
    with (BRAIN / "config/app.toml").open("rb") as fh:
        return tomllib.load(fh).get("input_mode", "brain") == "brain"


def brain_20_alive() -> bool:
    loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{RUN_20_LABEL}"],
                            capture_output=True).returncode == 0
    return loaded or subprocess.run(["pgrep", "-f", RUN_20_PROCESS], capture_output=True).returncode == 0


def credentials() -> dict:
    if not API_FILE.exists():
        API_FILE.parent.mkdir(exist_ok=True)
        fd = os.open(API_FILE, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"api_server": {
                "username": "brain",
                "password": secrets.token_urlsafe(24),
                "jwt_secret_key": secrets.token_urlsafe(32),
                "ws_token": secrets.token_urlsafe(24),
            }}, fh)
    return json.loads(API_FILE.read_text())["api_server"]


def commands() -> dict[str, list[str]]:
    python = str(BRAIN / ".venv/bin/python")
    return {
        "freqtrade": [
            str(ROOT / ".venv/bin/python"), str(ROOT / "follow/ft_counted.py"), "trade",
            "--config", str(BRAIN / "config/freqtrade.rules20.json"), "--config", str(API_FILE),
            "--userdir", str(ROOT / "user_data"),
        ],
        "brain": [python, "-m", "jiaoyi.cli", "run"],
        "panel": [python, "-m", "jiaoyi.cli", "panel", "--host", "127.0.0.1", "--port", "8091"],
    }


def halted(reason: str | None) -> int:
    event("halted", reason=reason)
    (DATA / "halted.json").write_text(json.dumps({"ts": time.time(), "reason": reason}))
    text = f"4.0 stopped: {reason}"[:200].replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{text}" with title "jiaoyi 4.0"'],
                   capture_output=True)
    return 0


def freqtrade_ready() -> bool:
    try:
        urllib.request.urlopen(READY_URL, timeout=2).read()
    except (urllib.error.URLError, OSError):
        return False
    return True


def start(name: str, cmd: list[str], env: dict) -> subprocess.Popen:
    out = (BRAIN / f"data/{name}.log").open("a")
    return subprocess.Popen(cmd, cwd=BRAIN, env=env, stdout=out, stderr=subprocess.STDOUT,
                            start_new_session=True)


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def stop_orphans() -> None:
    if not PIDS.exists():
        return
    for pid in json.loads(PIDS.read_text()).values():
        seen = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        if "ft_counted.py" in seen.stdout or "jiaoyi.cli" in seen.stdout:
            os.killpg(pid, signal.SIGTERM)
            event("orphan_stopped", pid=pid)
    time.sleep(5)


def main() -> int:
    logging.basicConfig(filename=DATA / "run_brain.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    api = credentials()
    env = {**os.environ, "FREQTRADE_API_USER": api["username"],
           "FREQTRADE_API_PASSWORD": api["password"], "PYTHONPATH": str(BRAIN / "src")}
    full = full_brain()
    ban_file = BRAIN / "data/rest_ban.json" if full else POSITIONS.with_name("rest_ban.json")
    guard = Guard(ban_file, DATA / "ip_weight_now.json", DATA / "ft_limited.json",
                  DATA / "restarts_brain.json")
    planned = PLANNED.exists()
    PLANNED.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    stop_orphans()
    cmds = commands()
    procs: dict[str, subprocess.Popen] = {}
    retry_at: dict[str, float] = {}
    heartbeat: subprocess.Popen | None = None
    next_heartbeat = time.time() + HEARTBEAT_EVERY_S
    try:
        while True:
            now = time.time()
            reason = guard.stop_reason(now)
            ft = procs.get("freqtrade")
            if reason and ft is not None and ft.poll() is None:
                stop(ft)
                event("freqtrade_stopped", reason=reason)
            elif not reason and (ft is None or ft.poll() is not None):
                if not planned and not guard.allow_restart(now):
                    return halted(guard.halted)
                planned = False
                procs["freqtrade"] = start("freqtrade", cmds["freqtrade"], env)
                event("freqtrade_started", pid=procs["freqtrade"].pid)
            if guard.halted:
                return halted(guard.halted)
            for name in ("brain", "panel"):
                p = procs.get(name)
                down = p is None or p.poll() is not None
                if down and now >= retry_at.get(name, 0) and freqtrade_ready():
                    if name == "brain" and full and brain_20_alive():
                        retry_at[name] = now + CHILD_RETRY_S
                        event("brain_refused", reason="2.0 is still running")
                        continue
                    if p is not None:
                        event(f"{name}_gone", code=p.returncode)
                    procs[name] = start(name, cmds[name], env)
                    retry_at[name] = now + CHILD_RETRY_S
                    event(f"{name}_started", pid=procs[name].pid)
            # 2.0's watchdog goes away with 2.0: this one checks the brain's heartbeat for Telegram
            if full and now >= next_heartbeat and (heartbeat is None or heartbeat.poll() is not None):
                next_heartbeat = now + HEARTBEAT_EVERY_S
                heartbeat = start("watchdog", [cmds["brain"][0], "-m", "jiaoyi.cli", "watchdog"], env)
            PIDS.write_text(json.dumps({n: p.pid for n, p in procs.items()}))
            time.sleep(1)
    finally:
        for p in procs.values():
            stop(p)


if __name__ == "__main__":
    sys.exit(main())
