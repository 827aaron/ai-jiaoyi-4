"""Sample the whole-IP Binance futures weight for 24 hours, once a minute, then exit.

Each sample is one GET /fapi/v1/time (weight 1) late in the minute, because the
X-MBX-USED-WEIGHT-1M header is the running total of the current minute. The 2.0 ban file is
checked before every request, and any 418/429 ends the run for good: requests during a ban
extend it. A restart resumes the same 24-hour window from the samples on disk.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://fapi.binance.com/fapi/v1/time"
BAN_FILE = Path("/Users/aaron/huoke-agi/ai-jiaoyi-2.0/data/rest_ban.json")
DATA = Path(__file__).resolve().parent.parent / "data"
SAMPLES = DATA / "weight_samples.jsonl"
STOPPED = DATA / "sampler_stopped.json"
DONE = DATA / "sampler_done.json"
RUN_S = 86400
SAMPLE_AT_S = 55


def _append(row: dict) -> None:
    with SAMPLES.open("a") as fh:
        fh.write(json.dumps(row) + "\n")


def _ban_active(now: float) -> bool:
    try:
        return float(json.loads(BAN_FILE.read_text())["until"]) > now
    except FileNotFoundError:
        return False
    except (OSError, ValueError, KeyError, TypeError):
        return True  # unreadable means unknown: skip the minute rather than risk a ban


def _first_ts() -> float | None:
    if not SAMPLES.exists():
        return None
    with SAMPLES.open() as fh:
        line = fh.readline()
    return json.loads(line)["ts"] if line else None


def _sample() -> dict:
    try:
        with urllib.request.urlopen(URL, timeout=8) as resp:
            used = resp.headers.get("X-MBX-USED-WEIGHT-1M")
            return {"used": int(used) if used and used.isdigit() else None}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "retry_after": e.headers.get("Retry-After")}
    except OSError as e:
        return {"error": type(e).__name__}


def summarize() -> dict:
    with SAMPLES.open() as fh:
        rows = [json.loads(line) for line in fh]
    used = sorted(r["used"] for r in rows if r.get("used") is not None)
    n = len(used)
    return {
        "samples": len(rows),
        "with_weight": n,
        "min": used[0] if n else None,
        "median": used[n // 2] if n else None,
        "p90": used[int(n * 0.9)] if n else None,
        "p99": used[int(n * 0.99)] if n else None,
        "max": used[-1] if n else None,
        "minutes_over_1500": sum(u > 1500 for u in used),
        "minutes_over_2000": sum(u > 2000 for u in used),
        "skipped_for_ban": sum(1 for r in rows if r.get("skipped") == "ban"),
        "errors": sum(1 for r in rows if "error" in r),
        "first_ts": rows[0]["ts"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
    }


def main() -> int:
    if STOPPED.exists() or DONE.exists():
        return 0
    started = _first_ts() or time.time()
    while time.time() - started < RUN_S:
        now = time.time()
        wait = (SAMPLE_AT_S - now % 60) % 60
        time.sleep(wait if wait > 0.5 else wait + 60)
        now = time.time()
        if _ban_active(now):
            _append({"ts": now, "skipped": "ban"})
            continue
        row = {"ts": now, **_sample()}
        _append(row)
        if row.get("http") in (418, 429):
            STOPPED.write_text(json.dumps(row))
            return 0
    DONE.write_text(json.dumps(summarize(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
