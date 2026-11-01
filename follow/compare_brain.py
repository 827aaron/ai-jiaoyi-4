"""Compare 4.0's Rules20 ledger with 2.0, in R so the different wallet sizes cancel out.

The two sides use separate position ids, so a pair is the same symbol and side opened within
MATCH_S seconds. Both sides read the same cards: this shows execution and exits, not signal
quality. 2.0 closes by Jev probability and 4.0 does not, which is intended and listed apart.
"""

import json
import sqlite3
import sys
from collections import Counter
from datetime import timedelta

from report import DATA, POSITIONS, ROOT, SHADOW, parse_ts, read_shadow

DB = ROOT / "brain/data/freqtrade_rules20.sqlite"
STARTED = "2026-11-01T03:31:00+00:00"
MATCH_S = 120
OUT = DATA / "compare_brain.md"


def load_ft() -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("select * from trades order by open_date")]
    con.close()
    return rows


def r_of(side: str, entry: float, exit_: float, stop: float) -> float:
    return (exit_ - entry) * (1 if side == "long" else -1) / abs(entry - stop)


def build() -> str:
    start = parse_ts(STARTED)
    positions = [v["record"] | {"id": k} for k, v in json.loads(POSITIONS.read_text())["positions"].items()]
    ours = [p for p in positions if parse_ts(p["opened_at"]) >= start and p["status"] != "failed"]
    shadow = read_shadow(SHADOW)
    trades = load_ft()
    used, rows, missed = set(), [], []
    reasons: Counter = Counter()
    for p in sorted(ours, key=lambda x: x["opened_at"]):
        at = parse_ts(p["opened_at"])
        match = next((t for t in trades if t["id"] not in used
                      and t["pair"].split("/")[0] + "USDT" == p["symbol"]
                      and bool(t["is_short"]) == (p["side"] == "short")
                      and abs(parse_ts(t["open_date"]) - at) <= timedelta(seconds=MATCH_S)), None)
        if match is None:
            missed.append(f"{p['symbol']} {p['opened_at'][:19]}")
            continue
        used.add(match["id"])
        if match["is_open"] or p["status"] != "closed" or p["id"] not in shadow:
            continue
        s = shadow[p["id"]]
        stop = float(match["enter_tag"].split("|")[1])
        theirs = r_of(p["side"], s["mainnet_entry"], s["mainnet_exit"],
                      s["mainnet_entry"] + p["stop_loss_price"] - p["entry_price"])
        mine = r_of(p["side"], match["open_rate"], match["close_rate"], stop)
        reasons[p["close_reason"]] += 1
        rows.append(f"| {p['symbol']} | {p['side']} | {p['close_reason']} | {match['exit_reason']} |"
                    f" {theirs:+.2f} | {mine:+.2f} | {mine - theirs:+.2f} |")
    extra = [t["pair"] for t in trades if t["id"] not in used]
    total_gap = sum(float(r.split("|")[7]) for r in rows)
    return "\n".join([
        f"# 4.0 对 2.0（{STARTED[:16]} UTC 起）",
        "",
        "同一批卡，只比执行和出场，不比信号。盈亏用 R，两边资金不同。2.0 基准是主网账。",
        f"- 2.0 开仓 {len(ours)}，4.0 跟上 {len(ours) - len(missed)}，漏 {len(missed)}，多 {len(extra)}",
        f"- 两边都已平 {len(rows)} 笔，4.0 减 2.0 合计 {total_gap:+.2f}R",
        f"- 2.0 平仓原因 {dict(reasons)}；keeper 含有意差异的 Jev 概率平仓，take_profit 单列",
        f"- 漏：{'、'.join(missed) or '无'}；多：{'、'.join(extra) or '无'}",
        "",
        "| 币 | 方向 | 2.0 平仓 | 4.0 平仓 | 2.0 R | 4.0 R | 差 |",
        "|---|---|---|---|---|---|---|",
        *rows,
        "",
    ])


if __name__ == "__main__":
    OUT.write_text(build())
    print(OUT)
    sys.exit(0)
