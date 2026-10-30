"""Daily comparison of 2.0's mainnet shadow against Freqtrade dry-run, in data/reports/.

A day is a Bangkok calendar day of the 2.0 close time. Prices are compared per leg, signed so a
positive gap always means Freqtrade got the worse fill.
"""

import argparse
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
POSITIONS = Path("/Users/aaron/huoke-agi/ai-jiaoyi-2.0/data/positions.json")
SHADOW = Path("/Users/aaron/huoke-agi/ai-jiaoyi-2.0/data/mainnet_shadow.jsonl")
BANGKOK = timezone(timedelta(hours=7))


def parse_ts(raw: str) -> datetime:
    at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def local_day(raw: str) -> date:
    return parse_ts(raw).astimezone(BANGKOK).date()


def gap_bp(side: str, leg: str, ours: float, ft: float) -> float:
    """Basis points by which the Freqtrade fill is worse than the shadow's."""
    buying = (side == "long") == (leg == "entry")
    return (ft - ours if buying else ours - ft) / ours * 10000


def read_shadow(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        with path.open() as fh:
            for line in fh:
                row = json.loads(line)
                out[row["position_id"]] = row  # the last line for a position has the best source
    return out


def read_ft(db: Path) -> dict[str, dict]:
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select id, pair, enter_tag, open_rate, close_rate, open_date, close_date, is_open,"
        " exit_reason, close_profit_abs, funding_fees from trades"
    ).fetchall()
    con.close()
    return {r["enter_tag"]: dict(r) for r in rows}


def build(day: date, positions: dict, state: dict, shadow: dict, ft: dict) -> str:
    rows, opened, followed, refused, missed = [], 0, 0, [], []
    shadow_total = ft_total = funding = 0.0
    gaps: list[float] = []
    for pid, wrapped in positions.items():
        rec = wrapped["record"]
        row = state.get(pid, {})
        if row.get("status") == "baseline" or rec["status"] == "failed":
            continue
        if local_day(rec["opened_at"]) == day:
            opened += 1
            status = row.get("status", "pending")
            if status in ("open", "closed"):
                followed += 1
            elif status == "refused":
                refused.append(f"{rec['symbol']}（{row.get('error', '')[:60]}）")
            elif status == "missed":
                missed.append(rec["symbol"])
        if not rec.get("closed_at") or local_day(rec["closed_at"]) != day:
            continue
        s, f = shadow.get(pid), ft.get(pid)
        if not s or not f or f["close_rate"] is None:
            rows.append(f"| {rec['symbol']} | {rec['side']} | 缺数据 | | | | | | | |")
            continue
        side = rec["side"]
        entry_gap = gap_bp(side, "entry", s["mainnet_entry"], f["open_rate"])
        exit_gap = gap_bp(side, "exit", s["mainnet_exit"], f["close_rate"])
        lag = (parse_ts(f["open_date"]) - parse_ts(rec["opened_at"])).total_seconds()
        fund = f["funding_fees"] or 0.0
        pnl = f["close_profit_abs"] or 0.0
        shadow_total += s["mainnet_net_usdt"]
        ft_total += pnl
        funding += fund
        gaps += [entry_gap, exit_gap]
        rows.append(
            f"| {rec['symbol']} | {side} | {s['mainnet_entry']:g} | {f['open_rate']:g} |"
            f" {entry_gap:+.1f} | {s['mainnet_exit']:g} | {f['close_rate']:g} | {exit_gap:+.1f} |"
            f" {lag:.0f}s | {s['mainnet_net_usdt']:+.2f} / {pnl:+.2f} |"
        )
    known = {pid for pid in state}
    extra = sorted(t for t in ft if t and t not in known)
    closed_by_ft = [
        f"{v['pair']}（{v['exit_reason']}）" for v in ft.values()
        if v["exit_reason"] not in (None, "force_exit") and v["close_date"]
        and local_day(v["close_date"]) == day
    ]
    avg = sum(gaps) / len(gaps) if gaps else 0.0
    return "\n".join([
        f"# 跟单对比 {day}（曼谷时间）",
        "",
        f"- 2.0 当天开仓 {opened} 笔，跟上 {followed} 笔，被拒 {len(refused)} 笔，错过 {len(missed)} 笔，多笔 {len(extra)} 笔",
        f"- 当天平仓两本账：主网影子 {shadow_total:+.2f}U，Freqtrade {ft_total:+.2f}U（含资金费 {funding:+.2f}U）",
        f"- 平均每条腿 Freqtrade 比影子差 {avg:+.1f}bp（正数表示 Freqtrade 成交更差）",
        f"- 被拒：{'、'.join(refused) or '无'}",
        f"- 错过：{'、'.join(missed) or '无'}",
        f"- Freqtrade 自己平的：{'、'.join(closed_by_ft) or '无'}",
        "",
        "| 币 | 方向 | 影子开 | FT开 | 开差bp | 影子平 | FT平 | 平差bp | 晚到 | 影子/FT 净盈亏 |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
    ])


def write_report(day: date) -> Path:
    positions = json.loads(POSITIONS.read_text())["positions"]
    state_path = DATA / "bridge_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    text = build(day, positions, state, read_shadow(SHADOW), read_ft(DATA / "follow.sqlite"))
    out = DATA / "reports" / f"{day}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=date.fromisoformat, default=datetime.now(BANGKOK).date())
    print(write_report(parser.parse_args().day))
