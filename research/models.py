"""Side-by-side short-horizon strategy research (no currency drift: PnL in quote ccy of each pair).
Train = first 40 days, Test = last ~20 days. Rolling 24h/48h windows (step 12h) on test.
Fill model: TAKER entry = next bar open * (1 + slip + half spread), taker fee.
            MAKER entry = limit price, filled only if a later bar's LOW < limit (strict) within `wait` bars, maker fee.
            Exits: stop = taker (min(stop, open)) ; target = maker limit filled if HIGH > target ; time/signal = taker at close.
Run: python research/models.py"""
import json, math, itertools, sys
import numpy as np
sys.path.insert(0, ".")
from bot.wallex import resample

FEES = json.load(open("data/fees.json"))
SPREAD_BPS = {"BTCUSDT": 2, "ETHUSDT": 7, "BTCTMN": 5, "ETHTMN": 16}
SLIP_BPS = 2

def arr(c):
    return {k: np.array([b[k] for b in c], float) for k in "tohlcv"}

def load(m, tf_min):
    c = json.load(open(f"data/candles/{m}_1m.json"))
    return arr(resample([dict(x) for x in c], tf_min * 60) if tf_min > 1 else c)

def ema(x, n):
    a = 2 / (n + 1); out = np.empty_like(x); e = x[0]
    for i, v in enumerate(x): e = a * v + (1 - a) * e; out[i] = e
    return out
def roll(x, n, f):
    out = np.full_like(x, np.nan)
    from numpy.lib.stride_tricks import sliding_window_view as sw
    if len(x) >= n: out[n-1:] = f(sw(x, n), axis=1)
    return out
def atr(d, n=14):
    pc = np.roll(d["c"], 1); pc[0] = d["c"][0]
    tr = np.maximum(d["h"] - d["l"], np.maximum(abs(d["h"] - pc), abs(d["l"] - pc))); return ema(tr, 2 * n - 1)
def er(c, n=20):
    ch = np.abs(c - np.roll(c, n)); path = roll(np.abs(np.diff(c, prepend=c[0])), n, np.sum)
    out = np.where(path > 0, ch / path, 0); out[:n] = 0; return out

# ---------------- models: return list of (i, mode, limit, stop, target, max_bars) signals at close of bar i
def m_momentum(d, p):
    c = d["c"]; a = atr(d); hi = roll(d["h"], p["n"], np.max); e = ema(c, p["ema"]); s = []
    for i in range(max(p["n"], p["ema"]) + 1, len(c)):
        if c[i] > hi[i-1] and c[i] > e[i] and e[i] > e[i-5]:
            s.append((i, "taker", None, c[i] - p["stop"] * a[i], c[i] + p["tgt"] * a[i], p["hold"]))
    return s

def m_range_mr(d, p):
    c = d["c"]; a = atr(d); m = roll(c, 20, np.mean); sd = roll(c, 20, np.std); e = er(c, 20); s = []
    for i in range(30, len(c)):
        if e[i] < p["er_max"] and not np.isnan(sd[i]):
            lim = m[i] - p["k"] * sd[i]
            s.append((i, "maker", lim, lim - p["stop"] * a[i], m[i], p["hold"]))
    return s

def m_vwap(d, p):
    c, v = d["c"], d["v"] + 1e-12; tp = (d["h"] + d["l"] + d["c"]) / 3
    vw = roll(tp * v, p["n"], np.sum) / roll(v, p["n"], np.sum); sd = roll(c - np.nan_to_num(vw), p["n"], np.std); s = []
    for i in range(p["n"] + 1, len(c)):
        if np.isnan(vw[i]) or sd[i] <= 0: continue
        lim = vw[i] - p["k"] * sd[i]
        s.append((i, "maker", lim, lim - p["stop"] * sd[i], vw[i], p["hold"]))
    return s

def m_leadlag(d, p, g):
    """Buy Wallex when Binance moved up over last L bars more than Wallex did (Wallex lags global)."""
    c = d["c"]; L = p["L"]; s = []
    gc = g
    for i in range(L + 1, len(c)):
        if np.isnan(gc[i]) or np.isnan(gc[i-L]): continue
        rb = gc[i] / gc[i-L] - 1; rw = c[i] / c[i-L] - 1
        if rb > p["thr"] and rb - rw > p["gap"]:
            s.append((i, "taker", None, c[i] * (1 - p["stop"]), c[i] * (1 + p["tgt"]), p["hold"]))
    return s

# ---------------- simulator
def simulate(d, sigs, m, wait=4, maker_exit=True):
    f = FEES[m]; half = SPREAD_BPS[m] / 2e4; slip = SLIP_BPS / 1e4
    o, h, l, c, t = d["o"], d["h"], d["l"], d["c"], d["t"]; n = len(c)
    trades, busy = [], -1
    for (i, mode, lim, stop, tgt, hold) in sigs:
        if i <= busy or i + 1 >= n: continue
        if mode == "taker":
            k = i + 1; px = o[k] * (1 + slip + half); fin = f["taker"]
            stop, tgt = stop * px / c[i], tgt * px / c[i]
        else:
            k = next((j for j in range(i + 1, min(n, i + 1 + wait)) if l[j] < lim), None)
            if k is None: continue
            px = min(lim, o[k]); fin = f["maker"]
        if px <= stop or tgt <= px: continue
        ex = None
        for j in range(k, min(n, k + hold + 1)):
            if l[j] <= stop: ex, fout, why = min(stop, o[j]) * (1 - slip - half), f["taker"], "stop"; break
            if j > k and h[j] > tgt:
                if maker_exit: ex, fout, why = tgt, f["maker"], "target"
                else: ex, fout, why = max(tgt, o[j]) * (1 - slip - half), f["taker"], "target"
                break
        if ex is None:
            j = min(n - 1, k + hold); ex, fout, why = c[j] * (1 - slip - half), f["taker"], "time"
        r = ex * (1 - fout) / (px * (1 + fin)) - 1
        trades.append({"t_in": t[k], "t_out": t[j], "ret": r, "gross": ex / px - 1, "why": why, "cost": fin + fout + 2 * (slip + half) * (mode == "taker")})
        busy = j
    return trades

def summarize(trades, t0, t1, size=1.0):
    tr = [x for x in trades if t0 <= x["t_in"] < t1]
    days = (t1 - t0) / 86400; n = len(tr)
    res = {"trades": n, "trades_day": round(n / days, 1) if days else 0,
           "mean_trade_%": round(np.mean([x["ret"] for x in tr]) * 100, 3) if n else 0,
           "win_%": round(100 * np.mean([x["ret"] > 0 for x in tr]), 1) if n else 0,
           "avg_abs_move_%": round(np.mean([abs(x["gross"]) for x in tr]) * 100, 3) if n else 0,
           "rt_cost_%": round(np.mean([x["cost"] for x in tr]) * 100, 3) if n else 0,
           "total_%": round((np.prod([1 + size * x["ret"] for x in tr]) - 1) * 100, 2) if n else 0}
    for W in (86400, 172800):
        wr = []
        s = t0
        while s + W <= t1:
            w = [x for x in tr if s <= x["t_in"] < s + W]
            wr.append(np.prod([1 + size * x["ret"] for x in w]) - 1 if w else 0.0); s += 43200
        key = "24h" if W == 86400 else "48h"
        if wr:
            res[f"{key}_win_windows_%"] = round(100 * np.mean([x > 0 for x in wr]), 0)
            res[f"{key}_median_%"] = round(float(np.median(wr)) * 100, 2)
            res[f"{key}_mean_%"] = round(float(np.mean(wr)) * 100, 2)
            res[f"{key}_worst_%"] = round(float(np.min(wr)) * 100, 2)
    return res

GRIDS = {
 "1_momentum_15m": (15, m_momentum, {"n": [20, 48], "ema": [50], "stop": [1.5, 2.5], "tgt": [2, 4], "hold": [16, 48]}),
 "1_momentum_60m": (60, m_momentum, {"n": [12, 24], "ema": [50], "stop": [1.5, 2.5], "tgt": [2, 4], "hold": [12, 24]}),
 "2_range_mr_maker_15m": (15, m_range_mr, {"er_max": [0.2, 0.35], "k": [1.5, 2.0, 2.5], "stop": [1.5, 3], "hold": [16, 48]}),
 "3_vwap_bb_5m": (5, m_vwap, {"n": [48, 144], "k": [2.0, 3.0], "stop": [1.5, 3.0], "hold": [24, 72]}),
 "5_leadlag_1m": (1, "leadlag", {"L": [2, 3, 5], "thr": [0.0015, 0.0025, 0.004], "gap": [0.001, 0.002], "stop": [0.003, 0.006], "tgt": [0.002, 0.004], "hold": [5, 15]}),
}
GMAP = {"BTCUSDT": "BTCUSDT", "BTCTMN": "BTCUSDT", "ETHUSDT": "ETHUSDT", "ETHTMN": "ETHUSDT"}

def run_model(name, m, d, pr, glob=None):
    tf, fn, _ = GRIDS[name]
    sigs = m_leadlag(d, pr, glob) if fn == "leadlag" else fn(d, pr)
    return simulate(d, sigs, m, wait=4 if tf >= 5 else 3)

def main(markets=("BTCUSDT", "ETHUSDT", "BTCTMN", "ETHTMN"), only=None):
    out = {}
    for name, (tf, fn, grid) in GRIDS.items():
        if only and name not in only: continue
        for m in markets:
            d = load(m, tf); t_start, t_end = d["t"][0] + 3 * 86400, d["t"][-1]
            split = d["t"][0] + 40 * 86400
            glob = None
            if fn == "leadlag":
                g = {b["t"]: b["c"] for b in json.load(open(f"data/candles/BINANCE_{GMAP[m]}_1m.json"))}
                glob = np.array([g.get(int(t), np.nan) for t in d["t"]])
            best = None
            for combo in itertools.product(*grid.values()):
                pr = dict(zip(grid, combo)); tr = run_model(name, m, d, pr, glob)
                trn = [x for x in tr if t_start <= x["t_in"] < split]
                if len(trn) < 10: continue
                score = sum(x["ret"] for x in trn)                       # train total return (additive)
                if best is None or score > best[0]: best = (score, pr, tr)
            if best is None: out[(name, m)] = None; continue
            _, pr, tr = best
            out[(name, m)] = {"params": pr, "train": summarize(tr, t_start, split), "test": summarize(tr, split, t_end), "trades": tr}
            r = out[(name, m)]
            print(f"{name:22s} {m:8s} train mean/trade {r['train']['mean_trade_%']:7.3f}% n={r['train']['trades']:4d} | "
                  f"TEST n={r['test']['trades']:4d} mean {r['test']['mean_trade_%']:7.3f}% win {r['test']['win_%']:5.1f}% "
                  f"tot {r['test']['total_%']:7.2f}% 24h-win {r['test'].get('24h_win_windows_%')}% med {r['test'].get('24h_median_%')}% "
                  f"worst {r['test'].get('24h_worst_%')}% | move {r['test']['avg_abs_move_%']} vs cost {r['test']['rt_cost_%']} | {pr}", flush=True)
    json.dump({f"{k[0]}|{k[1]}": ({kk: vv for kk, vv in v.items() if kk != "trades"} if v else None) for k, v in out.items()},
              open("data/research_results.json", "w"), indent=1, default=float)
    return out

if __name__ == "__main__":
    main(only=sys.argv[1:] or None)
