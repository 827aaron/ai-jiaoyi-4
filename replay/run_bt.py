"""Run Freqtrade backtesting with exchangeInfo sent at most once and every other request refused.

The first run saves the market list; later runs answer from that file and never reach Binance.
"""

import json
import sys
from pathlib import Path

import ccxt
import ccxt.async_support

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/replay"
CACHE = OUT / "markets.json"
LOG = OUT / "requests.log"
BAN = Path("/Users/aaron/huoke-agi/ai-jiaoyi-2.0/data/rest_ban.json")

if BAN.exists():
    sys.exit("2.0 is banned, not touching Binance")


def guard(url: str) -> None:
    with LOG.open("a") as fh:
        fh.write(url + "\n")
    if CACHE.exists() or not url.endswith("/fapi/v1/exchangeInfo"):
        raise ccxt.ExchangeError(f"replay refuses {url}")


real_async = ccxt.async_support.Exchange.fetch
real_sync = ccxt.Exchange.fetch


async def fetch_async(self, url, *args, **kwargs):
    guard(url)
    answer = await real_async(self, url, *args, **kwargs)
    return answer


def fetch_sync(self, url, *args, **kwargs):
    guard(url)
    return real_sync(self, url, *args, **kwargs)


ccxt.async_support.Exchange.fetch = fetch_async
ccxt.Exchange.fetch = fetch_sync

real_markets = ccxt.async_support.binance.fetch_markets


async def fetch_markets(self, params={}):
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    markets = await real_markets(self, params)
    CACHE.write_text(json.dumps(markets))
    return markets


ccxt.async_support.binance.fetch_markets = fetch_markets

from freqtrade.main import main  # noqa: E402

main(sys.argv[1:])
