"""Multi-year XAUUSD (Dukascopy 15m, 2021->now) walk-forward test of 12 published gold ideas, costs for Wallex XAUTUSDT.
Each strategy -> target position series in [0,1] (long/flat) on 15m bars, decided at bar close, applied next bar.
Stops/targets evaluated on bar CLOSE (conservative-ish approximation; stated). Cost per unit turnover = taker 0.13% + half-spread hs.
Modes: LONG = position in {0,1}; INVENTORY = 0.5 base, bullish ->1, bearish (mirror rule) ->0; measured as excess vs constant 0.5.
Walk-forward: train 12 months, test next 6 months, rolling; per window pick best of <=4 variants on train (counted)."""
import sys, itertools, json
import numpy as np, pandas as pd

def load():
    d = pd.read_pickle("data/candles/DUKA_XAUUSD_15m.pkl"); d = d[d.index.dayofweek < 5]; d = d[(d.h > d.l) | (d.v > 0)]
    return d

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(s, n):
    dd = s.diff(); u = dd.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); dn = (-dd.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + u / dn)
def atr(d, n=14):
    tr = pd.concat([d.h - d.l, (d.h - d.c.shift()).abs(), (d.l - d.c.shift()).abs()], axis=1).max(axis=1); return tr.ewm(alpha=1/n, adjust=False).mean()

H = lambda d: np.asarray(d.index.hour + d.index.minute / 60.0)

def trade_loop(d, entry, exit_fn, stop=None, tgt=None, mirror=None):
    """Generic state machine. entry/mirror: bool arrays; exit_fn(i, entry_px) -> bool; stop/tgt arrays of % distances."""
    c = d.c.values; n = len(c); pos = np.zeros(n); p = 0; ep = 0
    for i in range(n):
        if p == 1:
            r = c[i] / ep - 1
            if (stop is not None and r <= -stop[ei]) or (tgt is not None and r >= tgt[ei]) or exit_fn(i, ep): p = 0
        if p == 0 and entry[i]: p, ep, ei = 1, c[i], i
        pos[i] = p
    return pos

# ---------------- strategies: f(d, v) -> (long_pos, bearish_flag_series_or_None)
def s_london_orb(d, v):
    h = H(d); day = d.index.normalize()
    asia = (h < 7); hi = d.h.where(asia).groupby(day).transform("max"); lo = d.l.where(asia).groupby(day).transform("min")
    rng = (hi - lo) / d.c; win = (h >= 7) & (h < 10)
    first = (win & (d.c > hi)).groupby(day).cumsum() == 1
    ent = (win & (d.c > hi) & first).values
    pos = trade_loop(d, ent, lambda i, ep: h[i] >= 16, stop=(rng * v["k"]).values, tgt=(rng * v["k"]).values)
    bear = (win & (d.c < lo)).values
    return pos, trade_loop(d, bear, lambda i, ep: h[i] >= 16, stop=(rng * v["k"]).values, tgt=(rng * v["k"]).values)

def s_ny_momentum(d, v):
    h = H(d); day = d.index.normalize(); o = d.c.where((h >= 13.25) & (h < 13.5)).groupby(day).transform("first")
    at = (h >= 14.5) & (h < 14.75); up = at & (d.c > o * (1 + v["th"])); dn = at & (d.c < o * (1 - v["th"]))
    ex = lambda i, ep: h[i] >= 20
    return trade_loop(d, up.values, ex), trade_loop(d, dn.values, ex)

def s_overnight(d, v):
    h = H(d); pos = ((h >= v["start"]) | (h < 13)).astype(float)   # hold through Asia+London, flat NY day
    pos = np.roll(pos, 0); return pos, None

def s_turtle_h4(d, v):
    k = 16; hi = d.h.rolling(20 * k).max().shift(1); lo = d.l.rolling(10 * k).min().shift(1); e = ema(d.c, 200 * k); a = atr(d, 14 * k) / d.c
    ent = ((d.c > hi) & (d.c > e)).values; lo_v = lo.values
    pos = trade_loop(d, ent, lambda i, ep: d.c.values[i] < lo_v[i], stop=(v["sa"] * a).values, tgt=(v["sa"] * 2 * a).values)
    bear = ((d.c < d.l.rolling(20 * k).min().shift(1)) & (d.c < e)).values; hi2 = d.h.rolling(10 * k).max().shift(1).values
    return pos, trade_loop(d, bear, lambda i, ep: d.c.values[i] > hi2[i], stop=(v["sa"] * a).values)

def s_donchian_h1(d, v):
    k = 4; hi = d.h.rolling(v["n"] * k).max().shift(1); lo = d.l.rolling(20 * k).min().shift(1).values
    pos = trade_loop(d, (d.c > hi).values, lambda i, ep: d.c.values[i] < lo[i])
    lo2 = d.l.rolling(v["n"] * k).min().shift(1); hi2 = d.h.rolling(20 * k).max().shift(1).values
    return pos, trade_loop(d, (d.c < lo2).values, lambda i, ep: d.c.values[i] > hi2[i])

def s_ema_pullback(d, v):
    k = 4; e20, e50, e200 = ema(d.c, 20 * k), ema(d.c, 50 * k), ema(d.c, 200 * k); a = atr(d, 14 * k) / d.c
    up = (e20 > e50) & (e50 > e200); touch = (d.l <= e20) & (d.c > e20) & up
    pos = trade_loop(d, touch.values, lambda i, ep: False, stop=(v["sa"] * a).values, tgt=(v["sa"] * 2 * a).values)
    dn = (e20 < e50) & (e50 < e200); bt = (d.h >= e20) & (d.c < e20) & dn
    return pos, trade_loop(d, bt.values, lambda i, ep: False, stop=(v["sa"] * a).values, tgt=(v["sa"] * 2 * a).values)

def s_rsi2(d, v):
    c1 = d.c.resample("1h").last().dropna(); r = rsi(c1, 2).reindex(d.index, method="ffill"); e = ema(c1, 200).reindex(d.index, method="ffill")
    rv = r.values
    pos = trade_loop(d, ((r < v["lv"]) & (d.c > e)).values, lambda i, ep: rv[i] > 70)
    return pos, trade_loop(d, ((r > 100 - v["lv"]) & (d.c < e)).values, lambda i, ep: rv[i] < 30)

def s_vwap(d, v):
    day = d.index.normalize(); tp = (d.h + d.l + d.c) / 3; w = d.v.replace(0, 1e-9)
    vw = (tp * w).groupby(day).cumsum() / w.groupby(day).cumsum(); sd = (d.c - vw).groupby(day).transform(lambda x: x.expanding().std())
    h = H(d); ok = (h >= 7) & (h < 18); vwv = vw.values
    pos = trade_loop(d, (ok & (d.c < vw - v["k"] * sd)).values, lambda i, ep: d.c.values[i] >= vwv[i] or h[i] >= 20, stop=np.full(len(d), 0.01))
    return pos, trade_loop(d, (ok & (d.c > vw + v["k"] * sd)).values, lambda i, ep: d.c.values[i] <= vwv[i] or h[i] >= 20, stop=np.full(len(d), 0.01))

def s_vol_breakout(d, v):
    day = d.index.normalize(); dd = d.resample("1D").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    rng = (dd.h - dd.l).shift(1); lvl = (dd.o + v["k"] * rng).reindex(day).values; lvl2 = (dd.o - v["k"] * rng).reindex(day).values
    h = H(d); ex = lambda i, ep: h[i] >= 20.5
    return trade_loop(d, (d.c.values > lvl) & (h < 18), ex), trade_loop(d, (d.c.values < lvl2) & (h < 18), ex)

def s_bb_squeeze(d, v):
    k = 4; m = d.c.rolling(20 * k).mean(); sd = d.c.rolling(20 * k).std(); bw = 4 * sd / m; sq = bw < bw.rolling(100 * k).quantile(v["q"])
    up = sq.shift(1).fillna(False).astype(bool) & (d.c > m + 2 * sd); dn = sq.shift(1).fillna(False).astype(bool) & (d.c < m - 2 * sd); mv = m.values
    return trade_loop(d, up.values, lambda i, ep: d.c.values[i] < mv[i]), trade_loop(d, dn.values, lambda i, ep: d.c.values[i] > mv[i])

def s_tsmom(d, v):
    dc = d.c.resample("1D").last().dropna(); sig = (dc / dc.shift(v["n"]) - 1).reindex(d.index, method="ffill").shift(1)
    return (sig > 0).astype(float).values, (sig < 0).astype(float).values

def s_hour_seasonal(d, v, train=None):
    # long during the K hours (UTC) with best mean 1h return in the TRAIN data only (pass via v["hours"])
    h = d.index.hour; return np.isin(h, v.get("hours", [])).astype(float), None

STRATS = {
 "01_london_orb": (s_london_orb, [{"k": 0.5}, {"k": 1.0}]),
 "02_ny_open_momentum": (s_ny_momentum, [{"th": 0.0}, {"th": 0.001}]),
 "03_overnight_hold": (s_overnight, [{"start": 20}, {"start": 22}]),
 "04_turtle_h4_ema200": (s_turtle_h4, [{"sa": 2.0}, {"sa": 3.0}]),
 "05_donchian_h1": (s_donchian_h1, [{"n": 55}, {"n": 100}]),
 "06_ema20_pullback_h1": (s_ema_pullback, [{"sa": 1.5}, {"sa": 2.5}]),
 "07_rsi2_h1": (s_rsi2, [{"lv": 5}, {"lv": 10}]),
 "08_vwap_reversion_15m": (s_vwap, [{"k": 2.0}, {"k": 2.5}]),
 "09_vol_breakout_daily": (s_vol_breakout, [{"k": 0.5}, {"k": 0.7}]),
 "10_bb_squeeze_h1": (s_bb_squeeze, [{"q": 0.1}, {"q": 0.2}]),
 "11_tsmom_daily": (s_tsmom, [{"n": 20}, {"n": 60}]),
 "12_hour_seasonality": (s_hour_seasonal, [{"K": 3}, {"K": 6}]),
}

def pnl(d, pos, cost):
    r = d.c.pct_change().fillna(0).values; p = np.r_[0, pos[:-1]]     # position decided at close i, earns bar i+1
    turn = np.abs(np.diff(np.r_[0, pos])); turn = np.r_[0, turn[:-1]]
    return p * r - turn * cost

def trades_from(pos, net, idx):
    out = []; inpos = False
    p = np.r_[0, pos[:-1]]
    for i in range(len(p)):
        if p[i] > 0 and not inpos: inpos, s, acc = True, i, 0.0
        if inpos:
            acc += net[i]
            if p[i] == 0 or i == len(p) - 1: out.append((idx[s], acc)); inpos = False
    return out

def stats(net, pos, idx):
    eq = np.cumsum(net); dd = (np.maximum.accumulate(eq) - eq).max()
    tr = trades_from(pos, net, idx); rets = np.array([x[1] for x in tr]) if tr else np.array([0.0])
    s = pd.Series(net, index=idx); day = s.groupby(idx.normalize()).sum(); day = day[day != 0]
    yrs = s.groupby(idx.year).sum()
    gp, gl = rets[rets > 0].sum(), -rets[rets < 0].sum()
    return {"trades": len(tr), "net_per_trade_%": round(rets.mean() * 100, 3), "win_%": round((rets > 0).mean() * 100, 1),
            "PF": round(gp / gl, 2) if gl > 0 else None, "total_%": round(eq[-1] * 100, 1) if len(eq) else 0, "maxDD_%": round(dd * 100, 1),
            "days_profitable_%": round((day > 0).mean() * 100, 1) if len(day) else None, "per_year_%": {int(k): round(v * 100, 1) for k, v in yrs.items()}}

def run(d, name, mode, hs):
    fn, variants = STRATS[name]; cost = 0.0013 + hs
    starts = pd.date_range(d.index[0].normalize() + pd.DateOffset(months=12), d.index[-1], freq="6MS")
    net_all = np.zeros(len(d)); pos_all = np.zeros(len(d)); chosen = []
    for ts in starts:
        tr_m = (d.index >= ts - pd.DateOffset(months=12)) & (d.index < ts); te_m = (d.index >= ts) & (d.index < ts + pd.DateOffset(months=6))
        if te_m.sum() == 0: continue
        best = None
        for v in variants:
            v = dict(v)
            if name == "12_hour_seasonal" or name == "12_hour_seasonality":
                hr = d.c[tr_m].resample("1h").last().pct_change().dropna(); v["hours"] = list(hr.groupby(hr.index.hour).mean().nlargest(v["K"]).index)
            seg = d[tr_m | te_m]; lp, bp = fn(seg, v)
            pos = lp if mode == "long" else (0.5 + 0.5 * lp - 0.5 * (bp if bp is not None else 0)).clip(0, 1)
            base = 0 if mode == "long" else 0.5
            net = pnl(seg, pos, cost) - pnl(seg, np.full(len(seg), base), 0)
            trm = tr_m[tr_m | te_m]; sc = net[trm].sum()
            if best is None or sc > best[0]: best = (sc, v, pos, net)
        _, v, pos, net = best; tem = te_m[tr_m | te_m]
        net_all[te_m] = net[tem]; pos_all[te_m] = pos[tem]; chosen.append({k: v[k] for k in v if k != "hours"} | ({"hours": v["hours"]} if "hours" in v else {}))
    m = d.index >= starts[0]
    return stats(net_all[m], pos_all[m] if mode == "long" else (pos_all[m] > 0.5).astype(float), d.index[m]), chosen

if __name__ == "__main__":
    d = load(); print("data", d.index[0], d.index[-1], len(d), flush=True)
    res = {}
    for mode in ("long", "inventory"):
        for name in STRATS:
            row = {}
            for hs in (0.0005, 0.001, 0.0015):
                st, ch = run(d, name, mode, hs); row[f"hs{int(hs*1e4)}"] = st
            res[f"{mode}|{name}"] = {**row, "variants_tried": len(STRATS[name][1]), "chosen_last": ch[-1] if ch else None}
            s = row["hs10"]
            print(f"{mode:9s} {name:24s} n={s['trades']:5d} net/tr {s['net_per_trade_%']:7.3f}% win {s['win_%']:5.1f}% PF {s['PF']} tot {s['total_%']:7.1f}% "
                  f"DD {s['maxDD_%']:5.1f}% days+ {s['days_profitable_%']}% | hs5 tot {row['hs5']['total_%']} hs15 tot {row['hs15']['total_%']} | yrs {s['per_year_%']}", flush=True)
    json.dump(res, open("data/research_gold_wf.json", "w"), indent=1, default=str)
