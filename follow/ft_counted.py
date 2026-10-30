"""Launch freqtrade with every ccxt REST call counted.

Patches the sync and async ccxt base `fetch` (ccxt.pro inherits the async one) before freqtrade
builds its exchange, so the startup burst is counted too. Once a minute it appends one JSON line
to COUNT_FILE: calls and estimated Binance weight per endpoint in that minute, plus the highest
X-MBX-USED-WEIGHT-1M header seen per host. The header is per IP, so it also includes any other
process on this machine that calls the same host; the estimate covers this process only.
"""

import json
import os
import sys
import threading
import time
from collections import Counter
from urllib.parse import parse_qs, urlparse

import ccxt
import ccxt.async_support

COUNT_FILE = os.environ.get(
    "PHASE0_REQCOUNT_FILE", "/Volumes/jiaoyi/freqtrade/user_data/phase0_reqcount.jsonl"
)

_lock = threading.Lock()
_calls: Counter = Counter()
_weight: Counter = Counter()
_hdr_max: dict = {}


def _klines_weight(limit: int) -> int:
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


def _depth_weight(limit: int) -> int:
    if limit <= 50:
        return 2
    return {100: 5, 500: 10}.get(limit, 20)


# Binance USD-M futures REST weights (developers.binance.com, 2026). Unknown paths count 1.
def estimate_weight(path: str, query: dict) -> int:
    has_symbol = "symbol" in query
    limit = int(query.get("limit", ["500"])[0])
    if path.endswith(("/klines", "/markPriceKlines", "/indexPriceKlines", "/premiumIndexKlines")):
        return _klines_weight(limit)
    if path.endswith("/depth"):
        return _depth_weight(int(query.get("limit", ["500"])[0]))
    if path.endswith("/ticker/24hr"):
        return 1 if has_symbol else 40
    if path.endswith("/ticker/bookTicker"):
        return 2 if has_symbol else 5
    if path.endswith("/ticker/price"):
        return 1 if has_symbol else 2
    if path.endswith("/premiumIndex"):
        return 1 if has_symbol else 10
    if path == "/api/v3/exchangeInfo":
        return 20
    return 1


def _record(self, url: str, method: str) -> None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    key = f"{method} {parsed.netloc}{parsed.path}"
    w = estimate_weight(parsed.path, query)
    headers = getattr(self, "last_response_headers", None) or {}
    used = None
    for k, v in headers.items():
        if k.lower() == "x-mbx-used-weight-1m":
            used = int(v)
    with _lock:
        _calls[key] += 1
        _weight[key] += w
        if used is not None:
            _hdr_max[parsed.netloc] = max(_hdr_max.get(parsed.netloc, 0), used)


_sync_fetch = ccxt.Exchange.fetch
_async_fetch = ccxt.async_support.Exchange.fetch


def _counted_sync_fetch(self, url, method="GET", headers=None, body=None):
    try:
        return _sync_fetch(self, url, method, headers, body)
    finally:
        _record(self, url, method)


async def _counted_async_fetch(self, url, method="GET", headers=None, body=None):
    try:
        return await _async_fetch(self, url, method, headers, body)
    finally:
        _record(self, url, method)


ccxt.Exchange.fetch = _counted_sync_fetch
ccxt.async_support.Exchange.fetch = _counted_async_fetch


def _flush_forever() -> None:
    started = time.time()
    while True:
        time.sleep(60 - (time.time() % 60))
        with _lock:
            row = {
                "ts": int(time.time()),
                "uptime_s": int(time.time() - started),
                "calls": dict(_calls),
                "weight": dict(_weight),
                "weight_total": sum(_weight.values()),
                "header_used_weight_1m_max": dict(_hdr_max),
            }
            _calls.clear()
            _weight.clear()
            _hdr_max.clear()
        with open(COUNT_FILE, "a") as fh:
            fh.write(json.dumps(row) + "\n")


threading.Thread(target=_flush_forever, name="phase0-reqcount", daemon=True).start()

if __name__ == "__main__":
    from freqtrade.main import main

    sys.argv = ["freqtrade", *sys.argv[1:]]
    main()
