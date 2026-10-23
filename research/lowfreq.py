"""Low-frequency long-only candidates on Wallex USDT pairs. Signals on Binance-mirror bars (15m/1h); execution on
Wallex bars of the same timestamps: entry taker at next Wallex open * (1+half_spread), stop taker (min(stop, open))
* (1-half_spread), target = maker limit filled only if Wallex HIGH trades through by 2bp, time stop taker at close.
Fees: maker 0.10%, taker 0.13%. half_spread default 10bp (observed ~20bp spreads). Target >= max(k*ATR, 0.5%).
Train = first 40 days, test = rest. PnL in USDT per trade."""
import json, sys, itertools
import numpy as np
sys.path.insert(0, "research"); sys.path.insert(0, ".")
from models import ema, roll, atr, summarize
from bot.wallex import resample
FM, FT = 0.001, 0.0013
GSYM = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT", "XAUTUSDT": "XAUTUSDT"}

def bars(path, tf):
    c = json.load(open(path)); r = resample([dict(x) for x in c], tf * 60)
    return {k: np.array([b[k] for b in r], float) for k in "tohlcv"}

def align(m, tf):
    g = bars(f"data/candles/BINANCE_{GSYM[m]}_1m.json", tf); w = bars(f"data/candles/{m}_1m.json", tf)
    wi = {int(t): i for i, t in enumerate(w["t"])}
    idx = np.array([wi.get(int(t), -1) for t in g["t"]])
    return g, w, idx

def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up = ema(np.clip(d, 0, None), 2 * n - 1); dn = ema(np.clip(-d, 0, None), 2 * n - 1)
    return 100 - 100 / (1 + up / np.maximum(dn, 1e-12))

def htf_up(g, k):   # higher-timeframe uptrend proxy: EMA(k*20) > EMA(k*50) and close above EMA(k*50)
    f, s = ema(g["c"], 20 * k), ema(g["c"], 50 * k); return (f > s) & (g["c"] > s)

def sig_pullback(g, p):
    c = g["c"]; r = rsi(c); a = atr(g); e = ema(c, 50); up = htf_up(g, p["k"]); out = []
    for i in range(300, len(c)):
        if up[i] and c[i] > e[i] and r[i-1] < p["rsi"] <= r[i]: out.append((i, a[i]))
    return out

def sig_breakout(g, p):
    c = g["c"]; a = atr(g); hi = roll(g["h"], p["n"], np.max); ap = a / c; med = roll(ap, 100, np.median); up = htf_up(g, p["k"]); out = []
    for i in range(300, len(c)):
        if up[i] and c[i] > hi[i-1] and ap[i-1] < p["sq"] * med[i-1]: out.append((i, a[i]))
    return out

def sig_mr(g, p):
    c = g["c"]; a = atr(g); r = rsi(c); m = roll(c, 20, np.mean); sd = roll(c, 20, np.std)
    ok = ema(c, 50 * p["k"]) > ema(c, 50 * p["k"])[np.maximum(np.arange(len(c)) - 10 * p["k"], 0)] * (1 - p["slope"]); out = []
    for i in range(300, len(c)):
        if r[i] < p["rsi"] and c[i] < m[i] - p["bb"] * sd[i] and ok[i]: out.append((i, a[i]))
    return out

def simulate(g, w, idx, sigs, p, hs):
    trades, busy = [], -1
    for i, a in sigs:
        if i <= busy or i + 1 >= len(idx) or idx[i + 1] < 0: continue
        k = idx[i + 1]; px = w["o"][k] * (1 + hs); ap = a / g["c"][i]
        stop = px * (1 - p["stop"] * ap); tgt = px * (1 + max(p["tgt"] * ap, 0.005)); ex = None
        for j in range(k, min(len(w["c"]), k + p["hold"] + 1)):
            if w["l"][j] <= stop: ex, f, why = min(stop, w["o"][j]) * (1 - hs), FT, "stop"; break
            if j > k and w["h"][j] > tgt * 1.0002: ex, f, why = tgt, FM, "target"; break
        if ex is None: j = min(len(w["c"]) - 1, k + p["hold"]); ex, f, why = w["c"][j] * (1 - hs), FT, "time"
        r = ex * (1 - f) / (px * (1 + FT)) - 1
        trades.append({"t_in": w["t"][k], "t_out": w["t"][j], "ret": r, "gross": ex / px - 1, "why": why, "cost": FT + f + 2 * hs})
        busy = int(np.searchsorted(g["t"], w["t"][j]))
    return trades

CANDS = {
 "A_trend_pullback_1h": (60, sig_pullback, {"k": [4], "rsi": [40, 45], "stop": [1.5, 2.5], "tgt": [2, 3], "hold": [24, 48]}),
 "B_breakout_volfilter_1h": (60, sig_breakout, {"k": [4], "n": [24, 48], "sq": [0.8, 1.0], "stop": [1.5, 2.5], "tgt": [2, 3], "hold": [24, 48]}),
 "C_meanrev_extreme_15m": (15, sig_mr, {"k": [4], "rsi": [20, 25], "bb": [2.5], "slope": [0.005, 0.02], "stop": [2, 3], "tgt": [1.5, 2.5], "hold": [32, 96]}),
}

if __name__ == "__main__":
    hs = float(sys.argv[1]) if len(sys.argv) > 1 else 0.001
    res = {}
    for name, (tf, fn, grid) in CANDS.items():
        for m in GSYM:
            g, w, idx = align(m, tf); t0 = g["t"][0] + 4 * 86400; split = g["t"][0] + 40 * 86400; t1 = g["t"][-1]
            best = None
            for combo in itertools.product(*grid.values()):
                p = dict(zip(grid, combo)); tr = simulate(g, w, idx, fn(g, p), p, hs)
                trn = [x for x in tr if t0 <= x["t_in"] < split]
                if len(trn) < 5: continue
                sc = np.mean([x["ret"] for x in trn])
                if best is None or sc > best[0]: best = (sc, p, tr)
            if not best: print(f"{name:25s} {m:9s} <5 train trades"); continue
            sc, p, tr = best; a, b = summarize(tr, t0, split), summarize(tr, split, t1)
            res[f"{name}|{m}"] = {"params": p, "train": a, "test": b}
            print(f"{name:25s} {m:9s} TRAIN n={a['trades']:3d} net/tr {a['mean_trade_%']:6.3f}% | TEST n={b['trades']:3d} ({b['trades_day']}/d) net/tr {b['mean_trade_%']:6.3f}% "
                  f"win {b['win_%']:5.1f}% tot {b['total_%']:6.2f}% 24h+ {b.get('24h_win_windows_%')}% med {b.get('24h_median_%')} worst {b.get('24h_worst_%')} | {p}", flush=True)
    json.dump(res, open(f"data/research_lowfreq_hs{int(hs*1e4)}.json", "w"), indent=1, default=float)
