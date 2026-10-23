"""Global reference data: Binance public klines via data-api.binance.vision (api.binance.com returns 451 from box)."""
import json, time, requests
BASE = "https://data-api.binance.vision/api/v3/klines"
def fetch(sym, interval="15m", days=62):
    end = int(time.time() * 1000); start = end - days * 86400_000; out = []
    while start < end:
        r = requests.get(BASE, params={"symbol": sym, "interval": interval, "startTime": start, "limit": 1000}, timeout=20).json()
        if not r: break
        out += [{"t": k[0] // 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in r]
        start = r[-1][0] + 1
    return out[:-1]
if __name__ == "__main__":
    from bot.wallex import resample
    for s in ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]:
        b = fetch(s); json.dump(b, open(f"data/candles/BINANCE_{s}_15.json", "w"))
        json.dump(resample([dict(x) for x in b], 14400), open(f"data/candles/BINANCE_{s}_240.json", "w")); print(s, len(b))
    for s in ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]:
        b = fetch(s, "1m", 62); json.dump(b, open(f"data/candles/BINANCE_{s}_1m.json", "w")); print(s, "1m", len(b))
