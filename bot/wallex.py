"""Minimal Wallex REST client (https://api.wallex.ir)."""
import os, time, requests

BASE = os.getenv("WALLEX_BASE_URL", "https://api.wallex.ir")
TF_SECONDS = {"1": 60, "15": 900, "60": 3600, "180": 10800, "240": 14400, "1D": 86400}

class WallexClient:
    def __init__(self, api_key: str = None, timeout: int = 15):
        self.api_key = api_key or os.getenv("WALLEX_API_KEY", "")
        self.s = requests.Session()
        self.timeout = timeout
        self._markets = None

    def _get(self, path, params=None, auth=False):
        h = {"x-api-key": self.api_key} if auth else {}
        for attempt in range(3):
            try:
                r = self.s.get(BASE + path, params=params, headers=h, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))

    # ---- public
    def markets(self, refresh=False):
        if self._markets is None or refresh:
            self._markets = self._get("/v1/markets")["result"]["symbols"]
        return self._markets

    def market(self, symbol):
        return self.markets()[symbol]

    def depth(self, symbol):
        return self._get("/v1/depth", {"symbol": symbol})["result"]

    NATIVE = {"1", "15", "60", "1D"}

    def candles(self, symbol, resolution="60", bars=300, end=None):
        """OHLCV. Wallex's 240 resolution actually returns 1h bars, so non-native resolutions
        (e.g. 120/240) are built locally by resampling 1h candles aligned to UTC."""
        resolution = str(resolution)
        if resolution not in self.NATIVE and resolution.isdigit() and int(resolution) % 60 == 0:
            k = int(resolution) // 60
            raw = self.candles(symbol, "60", bars * k + k, end)
            return resample(raw, int(resolution) * 60)
        end = end or int(time.time())
        step = TF_SECONDS.get(str(resolution), 3600)
        d = self._get("/v1/udf/history", {"symbol": symbol, "resolution": resolution,
                                         "from": end - step * bars, "to": end})
        if d.get("s") != "ok":
            return []
        out = []
        for t, o, h, l, c, v in zip(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]):
            out.append({"t": int(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)})
        return out

    def best_bid_ask(self, symbol):
        d = self.depth(symbol)
        def top(side, pick):
            book = d.get(side) or []
            prices = [float(x["price"]) for x in book]
            return pick(prices) if prices else None
        return top("bid", max), top("ask", min)

    # ---- private
    def _auth_req(self, method, path, json=None):
        if not self.api_key:
            raise RuntimeError("WALLEX_API_KEY not set")
        r = self.s.request(method, BASE + path, json=json, headers={"x-api-key": self.api_key}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def balances(self):
        return self._auth_req("GET", "/v1/account/balances")["result"]["balances"]

    def fee(self):
        return self._auth_req("GET", "/v1/account/fee")["result"]

    def place_order(self, symbol, side, type_, quantity, price=None, client_id=None):
        body = {"symbol": symbol, "side": side, "type": type_, "quantity": str(quantity)}
        if price is not None:
            body["price"] = str(price)
        if client_id:
            body["client_id"] = client_id
        return self._auth_req("POST", "/v1/account/orders", json=body)["result"]

    def cancel_order(self, client_order_id):
        return self._auth_req("DELETE", f"/v1/account/orders/{client_order_id}")

    def get_order(self, client_order_id):
        return self._auth_req("GET", f"/v1/account/orders/{client_order_id}")["result"]

    def open_orders(self, symbol=None):
        return self._auth_req("GET", "/v1/account/openOrders" + (f"?symbol={symbol}" if symbol else ""))["result"]


def resample(c, secs):
    out = []
    for b in c:
        bucket = b["t"] - b["t"] % secs
        if out and out[-1]["t"] == bucket:
            o = out[-1]; o["h"] = max(o["h"], b["h"]); o["l"] = min(o["l"], b["l"]); o["c"] = b["c"]; o["v"] += b["v"]
        else:
            out.append({**b, "t": bucket})
    return out


def history(client, symbol, resolution, bars):
    """Paged long history (for backtests). Non-native resolutions are resampled from 1h."""
    resolution = str(resolution)
    k, base = 1, resolution
    if resolution not in WallexClient.NATIVE and resolution.isdigit():
        k, base = int(resolution) // 60, "60"
    need, out, end = bars * k + k, [], int(time.time())
    while len(out) < need:
        ch = [b for b in client.candles(symbol, base, min(1000, need - len(out) + 5), end=end)
              if not out or b["t"] < out[0]["t"]]
        if not ch: break
        out = ch + out; end = ch[0]["t"] - 1
    return resample(out, int(resolution) * 60) if k > 1 else out
