import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "follow"))

from bridge import Bridge, pair_of
from ftclient import FtApiError
from guard import MAX_RESTARTS_PER_DAY, Guard
from report import build, gap_bp


class FakeFt:
    def __init__(self):
        self.enters, self.exits = [], []
        self.enter_error = None
        self.closed = set()
        self.next_id = 1

    def force_enter(self, pair, side, stake, leverage, tag):
        if self.enter_error:
            raise self.enter_error
        self.enters.append((pair, side, stake, leverage, tag))
        self.next_id += 1
        return {"trade_id": self.next_id - 1}

    def force_exit(self, trade_id):
        self.exits.append(trade_id)
        self.closed.add(trade_id)
        return {"result": "ok"}

    def trade(self, trade_id):
        return {"is_open": trade_id not in self.closed}


def pos(status="open", symbol="NEARUSDT", side="long", entry=4.0):
    return {"record": {"symbol": symbol, "side": side, "status": status, "quantity": 150.0,
                       "entry_price": entry, "leverage": 15}}


def write(path, positions):
    path.write_text(json.dumps({"positions": positions}))


@pytest.fixture
def world(tmp_path):
    ft = FakeFt()
    path = tmp_path / "positions.json"
    write(path, {"old": pos()})
    bridge = Bridge(ft, path, tmp_path / "state.json")
    bridge.tick()
    return ft, path, bridge


def test_bridge_tick_baselines_positions_open_at_first_run(world):
    ft, _, bridge = world
    bridge.tick()
    assert ft.enters == []
    assert bridge.state["old"]["status"] == "baseline"


def test_bridge_tick_enters_with_margin_not_notional(world):
    ft, path, bridge = world
    write(path, {"old": pos(), "new": pos(side="short")})
    bridge.tick()
    assert ft.enters == [("NEAR/USDT:USDT", "short", 40.0, 15, "new")]


def test_bridge_tick_exits_once_when_2_0_closes(world):
    ft, path, bridge = world
    write(path, {"new": pos()})
    bridge.tick()
    write(path, {"new": pos(status="closed")})
    bridge.tick()
    bridge.tick()
    assert ft.exits == [1]
    assert bridge.state["new"]["status"] == "closed"


def test_bridge_tick_marks_a_refused_same_pair_entry_without_retrying(world):
    ft, path, bridge = world
    ft.enter_error = FtApiError("POST forceenter: HTTP 502: position already open")
    write(path, {"new": pos()})
    bridge.tick()
    ft.enter_error = None
    bridge.tick()
    assert ft.enters == []
    assert bridge.state["new"]["status"] == "refused"


def test_bridge_tick_retries_a_transient_entry_failure(world):
    ft, path, bridge = world
    ft.enter_error = FtApiError("POST forceenter: ReadTimeout")
    write(path, {"new": pos()})
    bridge.tick()
    ft.enter_error = None
    bridge.tick()
    assert len(ft.enters) == 1
    assert bridge.state["new"]["status"] == "open"


def test_bridge_tick_records_a_position_that_closed_between_ticks_as_missed(world):
    ft, path, bridge = world
    write(path, {"new": pos(status="closed")})
    bridge.tick()
    assert ft.enters == []
    assert bridge.state["new"]["status"] == "missed"


def test_bridge_tick_survives_an_unreadable_positions_file(world):
    ft, path, bridge = world
    path.write_text("{half")
    bridge.tick()
    assert ft.enters == []


def test_bridge_state_survives_a_restart(world, tmp_path):
    ft, path, bridge = world
    write(path, {"new": pos()})
    bridge.tick()
    again = Bridge(ft, path, tmp_path / "state.json")
    again.tick()
    assert len(ft.enters) == 1


def test_bridge_pair_of_keeps_the_multiplier_prefix():
    assert pair_of("1000PEPEUSDT") == "1000PEPE/USDT:USDT"


@pytest.fixture
def guard(tmp_path):
    return Guard(tmp_path / "ban.json", tmp_path / "weight.json", tmp_path / "limited.json")


def test_guard_stop_reason_pauses_past_the_end_of_a_ban(guard):
    guard.ban_file.write_text(json.dumps({"status": 418, "until": 1000.0}))
    assert "ban" in guard.stop_reason(900.0)
    guard.ban_file.unlink()
    assert guard.stop_reason(1001.0) is not None
    assert guard.stop_reason(2801.0) is None


def test_guard_stop_reason_treats_an_unreadable_ban_file_as_a_ban(guard):
    guard.ban_file.write_text("{half")
    assert guard.stop_reason(900.0) is not None


def test_guard_stop_reason_ignores_a_weight_from_an_earlier_minute(guard):
    guard.weight_file.write_text(json.dumps({"used": 2100, "ts": 600.0}))
    assert guard.stop_reason(610.0) is not None
    fresh = Guard(guard.ban_file, guard.weight_file, guard.limited_file)
    assert fresh.stop_reason(700.0) is None


def test_guard_stop_reason_halts_for_good_on_our_own_rate_limit(guard):
    guard.limited_file.write_text("{}")
    assert guard.stop_reason(100.0) is not None
    guard.limited_file.unlink()
    assert guard.stop_reason(99999.0) is not None


def test_guard_allow_restart_halts_past_the_daily_cap(guard):
    assert all(guard.allow_restart(1000.0) for _ in range(MAX_RESTARTS_PER_DAY))
    assert guard.allow_restart(1000.0) is False
    assert guard.halted


@pytest.mark.parametrize("side,leg,ft,expected", [
    ("long", "entry", 101.0, 100.0),
    ("long", "exit", 99.0, 100.0),
    ("short", "entry", 99.0, 100.0),
    ("short", "exit", 101.0, 100.0),
])
def test_report_gap_bp_is_positive_when_freqtrade_is_worse(side, leg, ft, expected):
    assert gap_bp(side, leg, 100.0, ft) == pytest.approx(expected)


def test_report_build_compares_a_closed_trade_and_counts_refusals():
    rec = {"symbol": "NEARUSDT", "side": "long", "status": "closed",
           "opened_at": "2026-11-01T02:00:00Z", "closed_at": "2026-11-01T03:00:00Z"}
    refused = {**rec, "symbol": "ZECUSDT", "status": "open", "closed_at": None}
    text = build(
        date(2026, 11, 1),
        {"a": {"record": rec}, "b": {"record": refused}},
        {"a": {"status": "closed"}, "b": {"status": "refused", "error": "already open"}},
        {"a": {"mainnet_entry": 100.0, "mainnet_exit": 110.0, "mainnet_net_usdt": 9.0}},
        {"a": {"pair": "NEAR/USDT:USDT", "open_rate": 100.1, "close_rate": 109.89,
               "open_date": "2026-11-01 02:00:03", "close_date": "2026-11-01 03:00:03",
               "exit_reason": "force_exit", "close_profit_abs": 8.0, "funding_fees": -0.5}},
    )
    assert "开仓 2 笔，跟上 1 笔，被拒 1 笔，错过 0 笔，多笔 0 笔" in text
    assert "主网影子 +9.00U，Freqtrade +8.00U（含资金费 -0.50U）" in text
    assert "+10.0 |" in text
    assert "| 3s |" in text
