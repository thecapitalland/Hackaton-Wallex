"""Wallex vs global premium/discount + inventory strategy (spot, both sides via inventory).
dev = Wallex/global - 1 minus its rolling 24h median (removes XAUT-vs-PAXG basis / USDT-vs-USD basis).
Hold base inventory 50% of capital; dev > +thr -> SELL down to (50-step)%; dev < -thr -> BUY up to (50+step)%;
|dev| < exit -> back to 50%. Orders: maker limit at bar close, filled next bar only if price trades THROUGH
(sell: high > px*(1+tt); buy: low < px*(1-tt)), else taker variant. Excess = strategy equity - passive 50% hold
(gold/BTC drift removed), in quote currency. Tune on first 40 days, test the rest."""
import json, sys, itertools
import numpy as np
sys.path.insert(0, "research"); sys.path.insert(0, ".")
from models import FEES
PAIRS = {"XAUTUSDT": "PAXGUSDT", "PAXGUSDT": "PAXGUSDT", "BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}

def data(m):
    W = json.load(open(f"data/candles/{m}_1m.json")); B = {b["t"]: b["c"] for b in json.load(open(f"data/candles/BINANCE_{PAIRS[m]}_1m.json"))}
    t = np.array([b["t"] for b in W]); c = np.array([b["c"] for b in W]); h = np.array([b["h"] for b in W]); l = np.array([b["l"] for b in W]); o = np.array([b["o"] for b in W])
    g = np.array([B.get(int(x), np.nan) for x in t]); ok = ~np.isnan(g)
    t, c, h, l, g, o = t[ok], c[ok], h[ok], l[ok], g[ok], o[ok]
    prem = c / g - 1
    from numpy.lib.stride_tricks import sliding_window_view as sw
    W_ = 1440; med = np.full_like(prem, np.nan); med[W_-1:] = np.median(sw(prem, W_), axis=1)
    return {"o": o, "t": t, "c": c, "h": h, "l": l, "g": g, "prem": prem, "dev": prem - med}

def stats(d, m):
    dev = d["dev"][~np.isnan(d["dev"])]; ac = [np.corrcoef(dev[:-k], dev[k:])[0, 1] for k in (1, 5, 15, 60)]
    hl = [np.log(0.5) / np.log(a) if 0 < a < 1 else None for a in ac[:1]]
    return {"market": m, "median_premium_%": round(float(np.median(d["prem"])) * 100, 3),
            "dev_std_%": round(float(dev.std()) * 100, 3), "dev_p5_p95_%": [round(float(np.percentile(dev, q)) * 100, 3) for q in (5, 95)],
            "|dev|>0.3%_share": round(float((abs(dev) > 0.003).mean()) * 100, 2), "autocorr_1/5/15/60m": [round(float(a), 3) for a in ac],
            "half_life_min": round(hl[0], 1) if hl[0] else None}

def sim(d, m, p, t0, t1, maker=True, tt=0.0):
    f = FEES[m]; fee = f["maker"] if maker else f["taker"]; slip = 0 if maker else 3e-4
    c, h, l, dev, t, o = d["c"], d["h"], d["l"], d["dev"], d["t"], d["o"]
    half = {"XAUTUSDT": 3.5, "PAXGUSDT": 65, "BTCUSDT": 2, "ETHUSDT": 7}.get(m, 10) / 2e4
    idx = np.where((t >= t0) & (t < t1) & ~np.isnan(dev))[0]
    if len(idx) < 10: return None
    i0 = idx[0]; cap = 1.0; base = 0.5
    q = base * cap / c[i0]; cash = cap * (1 - base); q_hold = q; cash_hold = cash
    target = base; trades = 0; pend = None
    for i in idx[:-1]:
        if pend is not None:                                          # try to fill pending order on this bar
            side, px, qty = pend
            if not maker: px = o[i] * (1 + half if side == "buy" else 1 - half)   # taker: next open +/- half spread
            filled = (not maker) or (side == "sell" and h[i] > px * (1 + tt)) or (side == "buy" and l[i] < px * (1 - tt))
            if filled:
                if side == "sell": cash += qty * px * (1 - slip) * (1 - fee); q -= qty
                else: cash -= qty * px * (1 + slip) * (1 + fee); q += qty
                trades += 1
            pend = None
        x = dev[i]
        if x > p["thr"]: target = base - p["step"]
        elif x < -p["thr"]: target = base + p["step"]
        elif abs(x) < p["exit"]: target = base
        eq = cash + q * c[i]; want = target * eq / c[i]; diff = want - q
        if abs(diff) * c[i] > 0.05 * eq:
            pend = ("buy" if diff > 0 else "sell", c[i], abs(diff))
    j = idx[-1]
    eq = cash + q * c[j]; eq_h = cash_hold + q_hold * c[j]
    return {"excess_%": (eq / eq_h - 1) * 100, "trades": trades}

GRID = {"thr": [0.002, 0.003, 0.005], "exit": [0.0005, 0.001], "step": [0.25, 0.5]}

def windows(d, m, p, t0, t1, maker, tt, W=86400):
    out, s = [], t0
    while s + W <= t1:
        r = sim(d, m, p, s, s + W, maker, tt)
        if r: out.append(r["excess_%"])
        s += 43200
    return out

if __name__ == "__main__":
    res = {}
    for m in (sys.argv[1:] or PAIRS):
        d = data(m); st = stats(d, m); print(st, flush=True)
        t0 = d["t"][0] + 86400; split = d["t"][0] + 40 * 86400; t1 = d["t"][-1]
        best = max(((sim(d, m, dict(zip(GRID, c)), t0, split)["excess_%"], dict(zip(GRID, c))) for c in itertools.product(*GRID.values())), key=lambda x: x[0])
        p = best[1]; row = {"stats": st, "params": p, "train_excess_%": round(best[0], 3)}
        for label, mk, tt in (("maker_tt0", True, 0.0), ("maker_tt2bp", True, 2e-4), ("taker", False, 0.0)):
            r = sim(d, m, p, split, t1, mk, tt); w = windows(d, m, p, split, t1, mk, tt)
            row[label] = {"test_excess_%": round(r["excess_%"], 3), "trades": r["trades"], "trades_day": round(r["trades"] / ((t1 - split) / 86400), 1),
                          "24h_win_%": round(100 * np.mean([x > 0 for x in w]), 0) if w else None,
                          "24h_median_%": round(float(np.median(w)), 3) if w else None, "24h_worst_%": round(float(np.min(w)), 3) if w else None}
            print(f"  {m} {label}: train {row['train_excess_%']}% | TEST excess {row[label]}", flush=True)
        print("  params", p); res[m] = row
    json.dump(res, open("data/research_spread_inventory.json", "w"), indent=1, default=float)
