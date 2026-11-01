"""Compare the Replay20 backtest with 2.0's mainnet shadow, trade by trade."""

import json
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "follow"))
from report import SHADOW, read_shadow  # noqa: E402

OUT = ROOT / "data/replay"


def latest_trades() -> list[dict]:
    results = sorted((ROOT / "user_data/backtest_results").glob("*.zip"), key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(results[-1]) as z:
        name = next(n for n in z.namelist() if n.endswith(".json") and "config" not in n
                    and "market" not in n and "meta" not in n)
        return json.loads(z.read(name))["strategy"]["Replay20"]["trades"]


def main() -> None:
    picked = {r["id"]: r for r in json.loads((OUT / "picked.json").read_text())}
    shadow = read_shadow(SHADOW)
    trades = {t["enter_tag"].split("|")[0]: t for t in latest_trades()}
    groups = defaultdict(lambda: [0, 0.0, 0.0, 0])
    rows, skipped = [], [i for i in picked if i not in trades]
    for pid, t in trades.items():
        rec, s = picked[pid], shadow[pid]
        ours, theirs = s["mainnet_net_usdt"], t["profit_abs"]
        rows.append((pid, rec["symbol"], rec["side"], rec["close_reason"], t["exit_reason"],
                     t["open_rate"], s["mainnet_entry"], ours, theirs))
        for key in (("total", ""), ("2.0 close", rec["close_reason"]), ("replay exit", t["exit_reason"])):
            g = groups[key]
            g[0] += 1
            g[1] += ours
            g[2] += theirs
            g[3] += (ours > 0) == (theirs > 0)
    for key in sorted(groups):
        n, a, b, same = groups[key]
        print(f"{key[0]:12} {key[1]:14} n={n:3} shadow={a:+9.1f} replay={b:+9.1f} sameSign={same}")
    gaps = [abs(r[5] - r[6]) / r[6] * 10000 for r in rows]
    print("entry gap bp median", round(statistics.median(gaps), 1), "max", round(max(gaps), 1))
    print("not replayed:", len(skipped), [picked[i]["symbol"] for i in skipped][:20])
    (OUT / "compare.json").write_text(json.dumps(rows))


if __name__ == "__main__":
    main()
