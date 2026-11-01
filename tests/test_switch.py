"""switch_to_4.sh and rollback_to_2.sh against a fake launchctl and throwaway trees."""

import json
import os
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FAKE_LAUNCHCTL = """#!/bin/bash
# jobs/<label> means loaded; a job with its running flag answers the fake pgrep
echo "$*" >> "$FAKE/calls"
label="${2##*/}"
case "$1" in
  print) [ -e "$FAKE/jobs/$label" ] ;;
  disable) touch "$FAKE/disabled-$label" ;;
  enable) rm -f "$FAKE/disabled-$label" ;;
  bootout) rm -f "$FAKE/jobs/$label" ;;
  bootstrap)
    label=$(basename "$3" .plist)
    [ ! -e "$FAKE/disabled-$label" ] || exit 5
    touch "$FAKE/jobs/$label" ;;
esac
"""
FAKE_PGREP = """#!/bin/bash
case "$2" in
  *2.0*) [ -e "$FAKE/jobs/com.jiaoyi.run" ] ;;
  *) [ -e "$FAKE/jobs/com.jiaoyi4.brain" ] ;;
esac
"""
JOBS_2 = ["com.jiaoyi.run", "com.jiaoyi.watchdog", "com.jiaoyi.panel"]


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def world(tmp_path: Path) -> dict:
    fake = tmp_path / "fake"
    (fake / "jobs").mkdir(parents=True)
    agents = tmp_path / "agents"
    agents.mkdir()
    for label in [*JOBS_2, "com.jiaoyi4.brain"]:
        (fake / "jobs" / label).touch()
        (agents / f"{label}.plist").touch()
    root2 = tmp_path / "two"
    (root2 / "data/market_desk").mkdir(parents=True)
    (root2 / "data/positions.json").write_text(json.dumps({"positions": {
        "a": {"record": {"status": "closed"}},
    }}))
    (root2 / "data/battle_card.json").write_text('{"card": "from 2.0"}')
    (root2 / "data/paused.json").write_text('{"paused": true}')
    (root2 / "data/risk_state.json").write_text('{"from": "2.0"}')
    (root2 / "data/market_desk/day.jsonl").write_text("line\n")
    root4 = tmp_path / "four"
    (root4 / "data").mkdir(parents=True)
    (root4 / "brain/data").mkdir(parents=True)
    (root4 / "brain/config").mkdir(parents=True)
    (root4 / "brain/config/app.toml").write_text('mode = "paper"\ninput_mode = "mirror"   # note\n')
    (root4 / "brain/data/risk_state.json").write_text('{"from": "4.0"}')
    env = {
        **os.environ, "FAKE": str(fake), "ROOT_2": str(root2), "ROOT_4": str(root4),
        "AGENTS": str(agents), "WAIT_S": "2", "PYTHON": "python3",
        "LAUNCHCTL": str(executable(fake / "launchctl", FAKE_LAUNCHCTL)),
        "PGREP": str(executable(fake / "pgrep", FAKE_PGREP)),
    }
    return {"env": env, "fake": fake, "two": root2, "four": root4}


def run(world: dict, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(REPO / "scripts" / script), *args], env=world["env"],
                          capture_output=True, text=True, timeout=30)


def calls(world: dict) -> list[str]:
    path = world["fake"] / "calls"
    return path.read_text().splitlines() if path.exists() else []


def mode(world: dict) -> str:
    return (world["four"] / "brain/config/app.toml").read_text().splitlines()[1]


def test_switch_ToFour_DryRunChangesNothing(world):
    assert run(world, "switch_to_4.sh").returncode == 0
    assert not [c for c in calls(world) if not c.startswith("print")]
    assert 'input_mode = "mirror"' in mode(world)


def test_switch_ToFour_StopsTwoBeforeFullBrainAndSurvivesReboot(world):
    done = run(world, "switch_to_4.sh", "--execute")
    assert done.returncode == 0, done.stderr
    log = calls(world)
    label = lambda verb, job: next(i for i, c in enumerate(log) if c.startswith(verb) and job in c)
    assert label("bootout", "com.jiaoyi.watchdog") < label("bootout", "com.jiaoyi.run")
    assert label("bootout", "com.jiaoyi.run") < label("bootstrap", "com.jiaoyi4.brain")
    assert 'input_mode = "brain"   # note' in mode(world)
    for job in JOBS_2:
        assert (world["fake"] / f"disabled-{job}").exists()  # a reboot must not bring 2.0 back
    assert (world["four"] / "data/planned_restart").exists()
    assert (world["four"] / "brain/data/battle_card.json").read_text() == '{"card": "from 2.0"}'
    assert (world["four"] / "brain/data/market_desk/day.jsonl").exists()
    assert not (world["four"] / "brain/data/paused.json").exists()
    assert (world["four"] / "brain/data/risk_state.json").read_text() == '{"from": "4.0"}'


def test_switch_ToFour_RefusesOpenPosition(world):
    (world["two"] / "data/positions.json").write_text(json.dumps({"positions": {
        "a": {"record": {"status": "open"}},
    }}))
    done = run(world, "switch_to_4.sh", "--execute")
    assert done.returncode == 1 and "not flat" in done.stderr
    assert not [c for c in calls(world) if not c.startswith("print")]


@pytest.mark.parametrize("content", [None, "broken"])
def test_switch_ToFour_RefusesBan(world, content):
    text = content or json.dumps({"until": time.time() + 600})
    (world["two"] / "data/rest_ban.json").write_text(text)
    done = run(world, "switch_to_4.sh", "--execute")
    assert done.returncode == 1 and "ban" in done.stderr
    assert 'input_mode = "mirror"' in mode(world)


def test_switch_ToFour_ExpiredBanPasses(world):
    (world["two"] / "data/rest_ban.json").write_text(json.dumps({"until": time.time() - 1}))
    assert run(world, "switch_to_4.sh").returncode == 0


def test_switch_ToFour_RefusesSecondRun(world):
    assert run(world, "switch_to_4.sh", "--execute").returncode == 0
    done = run(world, "switch_to_4.sh", "--execute")
    assert done.returncode == 1 and "not in mirror" in done.stderr


def test_rollback_ToTwo_RestoresTwoThenMirrorsIt(world):
    assert run(world, "switch_to_4.sh", "--execute").returncode == 0
    (world["fake"] / "calls").unlink()
    done = run(world, "rollback_to_2.sh", "--execute")
    assert done.returncode == 0, done.stderr
    log = calls(world)
    out = next(i for i, c in enumerate(log) if c.startswith("bootout") and "jiaoyi4" in c)
    two = next(i for i, c in enumerate(log) if c.startswith("bootstrap") and "com.jiaoyi.run" in c)
    four = next(i for i, c in enumerate(log) if c.startswith("bootstrap") and "jiaoyi4" in c)
    assert out < two < four
    assert 'input_mode = "mirror"   # note' in mode(world)
    for job in [*JOBS_2, "com.jiaoyi4.brain"]:
        assert (world["fake"] / "jobs" / job).exists()
        assert not (world["fake"] / f"disabled-{job}").exists()


def test_rollback_ToTwo_RefusesActiveBan(world):
    assert run(world, "switch_to_4.sh", "--execute").returncode == 0
    (world["four"] / "brain/data/rest_ban.json").write_text(json.dumps({"until": time.time() + 600}))
    done = run(world, "rollback_to_2.sh", "--execute")
    assert done.returncode == 1 and "ban" in done.stderr
    assert not (world["fake"] / "jobs/com.jiaoyi.run").exists()


@pytest.fixture
def full(world: dict) -> dict:
    data = world["four"] / "brain/data"
    con = sqlite3.connect(data / "freqtrade_rules20.sqlite")
    con.execute("create table trades (id integer, is_open integer)")
    con.execute("insert into trades values (1, 0)")
    con.commit()
    con.close()
    (data / "ft_meta.json").write_text("{}")
    (data / "meeting_live").mkdir()
    (world["four"] / "brain/config/freqtrade.rules20.json").write_text(
        '{"dry_run": true,\n  "dry_run_wallet": 10000,\n  "max_open_trades": 8}'
    )
    return world


def wallet(world: dict) -> str:
    return (world["four"] / "brain/config/freqtrade.rules20.json").read_text()


def test_reset_ledger_DryRunChangesNothing(full):
    assert run(full, "reset_ledger.sh", "5000").returncode == 0
    assert '"dry_run_wallet": 10000' in wallet(full)
    assert (full["four"] / "brain/data/freqtrade_rules20.sqlite").exists()


def test_reset_ledger_ArchivesAndSetsTheWallet(full):
    done = run(full, "reset_ledger.sh", "5000", "--execute")
    assert done.returncode == 0, done.stderr
    data = full["four"] / "brain/data"
    archives = list(data.glob("archive-*"))
    assert len(archives) == 1
    assert (archives[0] / "freqtrade_rules20.sqlite").exists()
    assert (archives[0] / "risk_state.json").read_text() == '{"from": "4.0"}'
    assert '"dry_run_wallet": 10000' in (archives[0] / "freqtrade.rules20.json").read_text()
    assert not (data / "freqtrade_rules20.sqlite").exists()
    assert not (data / "ft_meta.json").exists()
    assert '"dry_run_wallet": 5000' in wallet(full)
    assert (full["four"] / "data/planned_restart").exists()
    assert (full["fake"] / "jobs/com.jiaoyi4.brain").exists()


def test_reset_ledger_RefusesOpenTrade(full):
    con = sqlite3.connect(full["four"] / "brain/data/freqtrade_rules20.sqlite")
    con.execute("insert into trades values (2, 1)")
    con.commit()
    con.close()
    done = run(full, "reset_ledger.sh", "5000", "--execute")
    assert done.returncode == 1 and "open trades" in done.stderr
    assert not [c for c in calls(full) if c.startswith("bootout")]


def test_reset_ledger_RefusesARunningMeeting(full):
    (full["four"] / "brain/data/meeting_live/BTCUSDT.json").write_text('{"status": "running"}')
    done = run(full, "reset_ledger.sh", "5000", "--execute")
    assert done.returncode == 1 and "meeting" in done.stderr


def test_reset_ledger_RefusesBadWallet(full):
    assert run(full, "reset_ledger.sh", "abc", "--execute").returncode == 1
