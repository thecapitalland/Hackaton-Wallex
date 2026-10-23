"""Cross-correlation of 1m returns: Binance (global) vs Wallex. corr(lag k) = corr(binance_ret[t], wallex_ret[t+k])."""
import json, math
def load(p): return {b["t"]: b["c"] for b in json.load(open(p))}
def rets(d, ts): return [math.log(d[ts[i]] / d[ts[i-1]]) for i in range(1, len(ts))]
def corr(a, b):
    n = len(a); ma, mb = sum(a)/n, sum(b)/n
    sa = math.sqrt(sum((x-ma)**2 for x in a)); sb = math.sqrt(sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb) for x, y in zip(a, b)) / (sa*sb) if sa and sb else 0
for g, ws in [("BTCUSDT", ["BTCUSDT", "BTCTMN"]), ("ETHUSDT", ["ETHUSDT", "ETHTMN"]), ("PAXGUSDT", ["PAXGUSDT", "XAUTTMN"])]:
    B = load(f"data/candles/BINANCE_{g}_1m.json")
    for w in ws:
        W = load(f"data/candles/{w}_1m.json"); ts = sorted(set(B) & set(W))
        ts = [t for i, t in enumerate(ts) if i == 0 or t - ts[i-1] == 60]  # contiguous-ish
        rb, rw = rets(B, ts), rets(W, ts)
        out = {k: round(corr(rb[:len(rb)-k] if k >= 0 else rb[-k:], rw[k:] if k >= 0 else rw[:len(rw)+k]), 3) for k in range(-2, 6)}
        print(g, "->", w, len(ts), out)
