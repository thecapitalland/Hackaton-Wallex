"""Live recorder (no trading): Wallex depth+trades every ~5s, Binance mirror prices every ~1s -> data/live/*.jsonl"""
import json, time, requests, os
W = "https://api.wallex.ir"; B = "https://data-api.binance.vision/api/v3/ticker/price"
WSYM = ["BTCUSDT", "ETHUSDT", "XAUTUSDT", "XAUTTMN", "BTCTMN"]; BSYM = '["BTCUSDT","ETHUSDT","PAXGUSDT","XAUTUSDT"]'
s = requests.Session()
def out(name, rec):
    with open(f"data/live/{name}_{time.strftime('%Y%m%d')}.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
last_w = 0
while True:
    t = time.time()
    try:
        r = s.get(B, params={"symbols": BSYM}, timeout=3).json()
        out("binance", {"ts": t, "p": {x["symbol"]: float(x["price"]) for x in r}})
    except Exception as e: out("errors", {"ts": t, "src": "binance", "err": repr(e)[:200]})
    if t - last_w >= 5:
        last_w = t
        for sym in WSYM:
            try:
                d = s.get(W + "/v1/depth", params={"symbol": sym}, timeout=4).json()["result"]
                tr = s.get(W + "/v1/trades", params={"symbol": sym}, timeout=4).json()["result"]["latestTrades"]
                out("wallex", {"ts": time.time(), "sym": sym,
                               "bid": [[float(x["price"]), float(x["quantity"])] for x in (d.get("bid") or [])[:10]],
                               "ask": [[float(x["price"]), float(x["quantity"])] for x in (d.get("ask") or [])[:10]],
                               "trades": tr[:50]})
            except Exception as e: out("errors", {"ts": time.time(), "src": sym, "err": repr(e)[:200]})
    time.sleep(max(0, 1 - (time.time() - t)))
