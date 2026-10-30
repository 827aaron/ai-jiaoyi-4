"""Thin client for the local Freqtrade 2026.9 REST API (freqtrade/rpc/api_server/)."""

import json
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8080/api/v1/"
DEFAULT_SECRETS = "/Volumes/jiaoyi/freqtrade/secrets/api.json"


class FtApiError(Exception):
    pass


class FtClient:
    """Every call carries `timeout`; `http` is anything with requests.Session.request's signature."""

    def __init__(self, base_url: str, auth: tuple[str, str], timeout: float = 10.0, http=None):
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.auth = auth
        self.timeout = timeout
        self.http = http or requests.Session()

    @classmethod
    def from_secrets(cls, path: str = DEFAULT_SECRETS, base_url: str = DEFAULT_BASE_URL,
                     timeout: float = 10.0) -> "FtClient":
        srv = json.loads(Path(path).read_text())["api_server"]
        return cls(base_url, (srv["username"], srv["password"]), timeout)

    def _call(self, method: str, path: str, *, params: dict | None = None, body=None):
        try:
            resp = self.http.request(method, self.base_url + path, params=params, json=body,
                                     auth=self.auth, timeout=self.timeout)
        except requests.RequestException as e:
            raise FtApiError(f"{method} {path}: {type(e).__name__}: {e}") from e
        if resp.status_code != 200:
            raise FtApiError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def health(self) -> dict:
        return self._call("GET", "health")

    def balance(self) -> dict:
        return self._call("GET", "balance")

    def status(self) -> list:
        return self._call("GET", "status")

    def locks(self) -> dict:
        return self._call("GET", "locks")

    def add_lock(self, pair: str, until: datetime, reason: str, side: str = "*") -> dict:
        body = [{"pair": pair, "side": side, "until": until.isoformat(), "reason": reason}]
        return self._call("POST", "locks", body=body)

    def closed_trades_since(self, since_ms: int, page: int = 500) -> list[dict]:
        """Closed trades with close_timestamp >= since_ms, newest first, paging until older ones."""
        out: list[dict] = []
        offset = 0
        while True:
            data = self._call("GET", "trades",
                              params={"limit": page, "offset": offset, "order_by_id": "false"})
            batch = data.get("trades", [])
            for t in batch:
                ts = t.get("close_timestamp")
                if ts is not None and ts >= since_ms:
                    out.append(t)
            if len(batch) < page or any((t.get("close_timestamp") or 0) < since_ms for t in batch):
                return out
            offset += page

    def reload_config(self) -> dict:
        return self._call("POST", "reload_config")
