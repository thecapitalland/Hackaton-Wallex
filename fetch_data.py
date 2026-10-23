"""Fetch & cache Wallex candles: python fetch_data.py [days]"""
import json, sys, time
from bot.wallex import WallexClient
SYMS = ["USDTTMN", "BTCUSDT","ETHUSDT","BTCTMN","ETHTMN","XAUTTMN","PAXGUSDT","USOONUSDT"]
def fetch(c, s, res, days):
    step = {"1": 60, "15": 900, "60": 3600}[res]; end = int(time.time()); start = end - days*86400; out = {}
    t = end
    while t > start:
        f = max(start, t - step*900)
        d = c._get("/v1/udf/history", {"symbol": s, "resolution": res, "from": f, "to": t})
        if d.get("s") == "ok":
            for row in zip(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]):
                out[int(row[0])] = {"t": int(row[0]), **{k: float(v) for k, v in zip("ohlcv", row[1:])}}
        t = f - 1
    return [out[k] for k in sorted(out)]
if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60; c = WallexClient()
    mk = c.markets(); json.dump({s: mk.get(s) for s in SYMS}, open("data/candles/markets.json", "w"), indent=1)
    for s in SYMS:
        for r in ["1"]:
            b = fetch(c, s, r, days)[:-1]
            json.dump(b, open(f"data/candles/{s}_1m.json", "w"))
            print(s, r, len(b), b and time.strftime('%m-%d', time.gmtime(b[0]['t'])), b and time.strftime('%m-%d', time.gmtime(b[-1]['t'])))
    # Wallex udf "15" returns 1m bars and "60" has gaps -> build 15m/1h by resampling continuous 1m data
    from bot.wallex import resample
    for s in SYMS:
        m = json.load(open(f"data/candles/{s}_1m.json"))
        for r in (15, 60, 240):
            json.dump(resample([dict(x) for x in m], r*60), open(f"data/candles/{s}_{r}.json", "w"))
    try:
        from bot.multi.fees import fetch_fees
        if c.api_key: print("fees:", fetch_fees(c, SYMS))
    except Exception as e: print("fee fetch failed (using defaults):", e)
