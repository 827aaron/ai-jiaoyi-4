"""Pick the 2.0 trades to replay and fetch their 1-minute candles from data.binance.vision.

Only trades with a mainnet shadow line are kept, on days vision has published in full. Freqtrade
requires mark and funding files: mark is resampled from the same candles and funding is zero, so
the replay compares fills and exits before funding, like the shadow ledger does.
"""

import io
import json
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "follow"))
from report import POSITIONS, parse_ts, read_shadow, SHADOW  # noqa: E402

OUT = ROOT / "data/replay"
DATADIR = OUT / "candles/futures"
VISION = "https://data.binance.vision/data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip"
COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def one_day(symbol: str, day: date) -> pd.DataFrame | None:
    url = VISION.format(s=symbol, d=day.isoformat())
    try:
        raw = urllib.request.urlopen(url, timeout=60).read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as z, z.open(z.namelist()[0]) as fh:
        first = fh.readline()
        header = 0 if first.startswith(b"open_time") else None
        fh = z.open(z.namelist()[0])
        df = pd.read_csv(fh, header=header, usecols=range(6))
    df.columns = COLUMNS
    df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
    return df


def save(pair_file: str, df: pd.DataFrame) -> None:
    df.reset_index(drop=True).to_feather(DATADIR / pair_file)


def main() -> None:
    DATADIR.mkdir(parents=True, exist_ok=True)
    positions = json.loads(POSITIONS.read_text())["positions"]
    shadow = read_shadow(SHADOW)
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    picked = []
    for pid, wrapped in positions.items():
        rec = wrapped["record"]
        if rec["status"] != "closed" or pid not in shadow or not rec["stop_loss_price"]:
            continue
        if parse_ts(rec["closed_at"]).date() <= yesterday:
            picked.append({"id": pid, **rec})
    days = {r["symbol"]: set() for r in picked}
    for r in picked:
        d = parse_ts(r["opened_at"]).date()
        days[r["symbol"]] |= {d + timedelta(n) for n in range(-1, 2) if d + timedelta(n) <= yesterday}
    jobs = [(s, d) for s, ds in days.items() for d in sorted(ds)]
    with ThreadPoolExecutor(8) as pool:
        frames = list(pool.map(lambda j: one_day(*j), jobs))
    missing = [f"{s} {d}" for (s, d), f in zip(jobs, frames) if f is None]
    by_symbol: dict[str, list] = {}
    for (s, _), f in zip(jobs, frames):
        if f is not None:
            by_symbol.setdefault(s, []).append(f)
    for s, parts in by_symbol.items():
        name = f"{s.removesuffix('USDT')}_USDT_USDT"
        df = pd.concat(parts).drop_duplicates("date").sort_values("date")
        save(f"{name}-1m-futures.feather", df)
        hourly = df.set_index("date").resample("1h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna().reset_index()
        save(f"{name}-1h-mark.feather", hourly)
        zero = hourly.assign(open=0.0, high=0.0, low=0.0, close=0.0, volume=0.0)
        save(f"{name}-1h-funding_rate.feather", zero)
    (OUT / "picked.json").write_text(json.dumps(picked))
    print(len(picked), "trades,", len(by_symbol), "symbols,", len(jobs), "days, missing:", missing)


if __name__ == "__main__":
    main()
