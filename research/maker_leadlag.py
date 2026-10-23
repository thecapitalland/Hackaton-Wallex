"""Maker lead-lag: Binance 1m move > threshold and Wallex lagging by > gap  ->  rest LIMIT buy at Wallex close*(1-off)
for `wait` bars. Conservative fill: a later bar's LOW must trade THROUGH limit by `tt` (low < lim*(1-tt)).
Exit: maker limit target (fill only if HIGH > tgt*(1+tt)), taker stop-loss, taker time stop, optional taker
exit-on-reverse-gap (Wallex overshoots Binance since entry by > rev). PnL in quote currency, real Wallex fees.
Train first 40 days, test rest."""
import json, itertools, sys
import numpy as np
sys.path.insert(0, "research"); sys.path.insert(0, ".")
from models import summarize, FEES, SPREAD_BPS
SL = 2e-4
PAIRS = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT", "PAXGUSDT": "PAXGUSDT", "XAUTTMN": "PAXGUSDT"}

def data(m):
    W = json.load(open(f"data/candles/{m}_1m.json")); B = {b["t"]: b["c"] for b in json.load(open(f"data/candles/BINANCE_{PAIRS[m]}_1m.json"))}
    d = {k: np.array([b[k] for b in W], float) for k in "tohlc"}
    d["g"] = np.array([B.get(int(t), np.nan) for t in d["t"]]); return d

def sim(d, m, p, tt=0.0):
    o, h, l, c, g, t = d["o"], d["h"], d["l"], d["c"], d["g"], d["t"]; n = len(c); L = p["L"]
    f = FEES[m]; half = SPREAD_BPS.get(m, 30) / 2e4
    rb = g / np.roll(g, L) - 1; rw = c / np.roll(c, L) - 1
    sig = np.where((rb > p["thr"]) & (rb - rw > p["gap"]))[0]; sig = sig[sig > L]
    trades, busy = [], -1
    for i in sig:
        if i <= busy or i + 2 >= n: continue
        lim = c[i] * (1 - p["off"])
        k = next((j for j in range(i + 1, min(n, i + 1 + p["wait"])) if l[j] < lim * (1 - tt)), None)
        if k is None: continue
        px = min(lim, o[k]); stop = px * (1 - p["stop"]); tgt = px * (1 + p["tgt"]); ex = None; g0, c0 = g[k], px
        for j in range(k, min(n, k + p["hold"] + 1)):
            if l[j] <= stop: ex, fo, why = min(stop, o[j]) * (1 - SL - half), f["taker"], "stop"; break
            if j > k and h[j] > tgt * (1 + tt): ex, fo, why = tgt, f["maker"], "target"; break
            if p.get("rev") and j > k and not np.isnan(g[j]) and (c[j] / c0 - 1) - (g[j] / g0 - 1) > p["rev"]:
                if j + 1 < n: ex, fo, why = o[j + 1] * (1 - SL - half), f["taker"], "reverse_gap"; j += 1
                break
        if ex is None: j = min(n - 1, k + p["hold"]); ex, fo, why = c[j] * (1 - SL - half), f["taker"], "time"
        r = ex * (1 - fo) / (px * (1 + f["maker"])) - 1
        trades.append({"t_in": t[k], "t_out": t[j], "ret": r, "gross": ex / px - 1, "why": why,
                       "cost": f["maker"] + fo + (0 if why == "target" else SL + half)})
        busy = j
    return trades

GRID = {"L": [2, 3, 5], "thr": [0.001, 0.002], "gap": [0.002, 0.003, 0.005], "off": [0.0, 0.0005, 0.001],
        "tgt": [0.002, 0.003, 0.005], "hold": [15, 60], "wait": [3], "stop": [0.01], "rev": [0, 0.002]}

if __name__ == "__main__":
    res = {}
    for m in (sys.argv[1:] or PAIRS):
        d = data(m); t0 = d["t"][0] + 86400; split = d["t"][0] + 40 * 86400; t1 = d["t"][-1]
        best = None
        for combo in itertools.product(*GRID.values()):
            p = dict(zip(GRID, combo)); tr = sim(d, m, p)
            trn = [x for x in tr if t0 <= x["t_in"] < split]
            if len(trn) < 15: continue
            sc = sum(x["ret"] for x in trn)
            if best is None or sc > best[0]: best = (sc, p)
        if not best: print(m, "no config with >=15 train trades"); continue
        p = best[1]; row = {"params": p}
        for tt in (0.0, 0.0002, 0.0005):
            tr = sim(d, m, p, tt)
            row[f"tt{tt}"] = {"train": summarize(tr, t0, split), "test": summarize(tr, split, t1),
                              "exits": {w: sum(1 for x in tr if x["why"] == w and x["t_in"] >= split) for w in ("target", "stop", "time", "reverse_gap")}}
        p0 = dict(p, rev=0); tr = sim(d, m, p0); row["no_rev_test"] = summarize(tr, split, t1)
        res[m] = row
        for tt in (0.0, 0.0002, 0.0005):
            a, b = row[f"tt{tt}"]["train"], row[f"tt{tt}"]["test"]
            print(f"{m:8s} tt={tt*1e4:.0f}bp TRAIN n={a['trades']} mean {a['mean_trade_%']}% | TEST n={b['trades']} ({b['trades_day']}/d) "
                  f"mean {b['mean_trade_%']}% win {b['win_%']}% tot {b['total_%']}% 24h-win {b.get('24h_win_windows_%')}% "
                  f"med {b.get('24h_median_%')} worst {b.get('24h_worst_%')} exits {row[f'tt{tt}']['exits']}", flush=True)
        print("   params", p, "| without reverse-gap exit TEST mean", row["no_rev_test"]["mean_trade_%"], "n", row["no_rev_test"]["trades"])
    json.dump(res, open("data/research_maker_leadlag.json", "w"), indent=1, default=float)
