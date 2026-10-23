"""User's Gold-Trader setup, long-only on Wallex XAUTUSDT. Signals: Dukascopy XAUUSD D1/H1 (H4 resampled) + Binance
XAUTUSDT 1m->M15. Execution: Wallex XAUTUSDT 15m, levels in % (global->Wallex by ratio at entry).
Bias score -10..+10 = 0.40*D1 + 0.35*H4 + 0.25*H1.
 D1: +DI vs -DI (+-3, halved if ADX<20), close vs EMA20/50/200 (+-1 each), discount/premium in 20d range (+-2), ATR expanding w/ trend (+-1) -> scaled to 10.
 H4/H1: last structure break BOS/MSS (+-4), unfilled FVG balance (+-2), swing HH/HL vs LH/LL (+-2), RSI14 vs 50 (+-2).
Trade only if D1, H4, H1 all > 0; 'strong' if total >= 5.8 else half size; H1 score < 3 -> half size.
Entry (sessions Tehran 10:30-13:30, 14:30-17:30 = 07:00-10:00, 11:00-14:00 UTC): M15 low tags the latest unfilled H1 bullish FVG,
then M15 rejection (close in upper 40% of range & bullish) or MSS (close > prior 3-bar high); skip if H1 RSI > 70.
Stop: below FVG bottom, clamped to 0.20-0.50% ($8-20 at ~$4000). Invalidation: H1 close below FVG bottom.
Target: highest H1 high of last 48h (next liquidity) if >= 1R else skip. Management: at +0.05% ($2) close 50%, SL->BE,
then halve remainder every +0.125% ($5); TP fixed. Risk 1.5% (option 0.6%); ATR(H1) > 110% of avg20 -> half size;
stop for the day after 3 losses or -1% day. Fees: taker 0.13% entry/stop/invalidation, maker 0.10% for limit partials/TP;
half-spread `hs` on taker fills. Min notional 1 USDT (=~50k TMN is ~0.18 USDT, never binding here)."""
import json, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
exec(open('/workspace/gold/hr.py').read().split("h=load('y_h.json')")[0])   # ema, rsi, adx helpers

def df_from(path):
    b = json.load(open(path)); d = pd.DataFrame(b); d.index = pd.to_datetime(d.t, unit="s", utc=True); return d[["o", "h", "l", "c"]]

def tf_score(x):
    """H4/H1 score series (-10..10) using only closed bars."""
    c, h, l = x.c, x.h, x.l; n = len(x); out = np.zeros(n)
    sh = (h.shift(2) > h.shift(1)) & (h.shift(2) > h.shift(3)) & (h.shift(2) > h) & (h.shift(2) > h.shift(4))
    sl = (l.shift(2) < l.shift(1)) & (l.shift(2) < l.shift(3)) & (l.shift(2) < l) & (l.shift(2) < l.shift(4))
    r = rsi(c).values; last_sh = last_sl = None; prev_sh = prev_sl = None; struct = 0; fvgs = []
    for i in range(5, n):
        if sh.iloc[i]: prev_sh, last_sh = last_sh, h.iloc[i-2]
        if sl.iloc[i]: prev_sl, last_sl = last_sl, l.iloc[i-2]
        if last_sh is not None and c.iloc[i] > last_sh: struct = 1
        if last_sl is not None and c.iloc[i] < last_sl: struct = -1
        if l.iloc[i] > h.iloc[i-2]: fvgs.append((1, h.iloc[i-2], l.iloc[i]))
        if h.iloc[i] < l.iloc[i-2]: fvgs.append((-1, h.iloc[i], l.iloc[i-2]))
        fvgs = [f for f in fvgs[-30:] if not ((f[0] == 1 and l.iloc[i] < f[1]) or (f[0] == -1 and h.iloc[i] > f[2]))]
        fb = sum(f[0] for f in fvgs); s = 4 * struct + 2 * np.sign(fb)
        if prev_sh is not None and prev_sl is not None:
            s += 2 if (last_sh > prev_sh and last_sl > prev_sl) else -2 if (last_sh < prev_sh and last_sl < prev_sl) else 0
        s += 2 * np.sign(r[i] - 50) if not np.isnan(r[i]) else 0
        out[i] = s
    return pd.Series(out, index=x.index)

def d1_score(d):
    a, p, n, at = adx(d); s = 3 * np.sign(p - n) * np.where(a < 20, 0.5, 1)
    for k in (20, 50, 200): s = s + np.sign(d.c - ema(d.c, k))
    hi, lo = d.h.rolling(20).max(), d.l.rolling(20).min(); pos = (d.c - lo) / (hi - lo)
    s = s + np.where(pos < 0.5, 2, -2) * np.sign(d.c - ema(d.c, 50))   # discount in uptrend bullish, premium in downtrend bearish
    s = s + np.sign(at - at.rolling(20).mean()) * np.sign(p - n)
    return (s / 10 * 10).clip(-10, 10)

def h1_fvgs(h):
    return [(h.index[i], h.h.iloc[i-2], h.l.iloc[i]) for i in range(2, len(h)) if h.l.iloc[i] > h.h.iloc[i-2]]

def run(risk=0.015, hs=0.0005, t_split=None):
    D = pd.read_pickle('/workspace/gold/dukd_1022_1716.pkl'); D.columns = ['o', 'h', 'l', 'c', 'v'][:len(D.columns)]; D = D[D.index.dayofweek < 5]
    H = pd.read_pickle('/workspace/gold/dukh_1022_1716.pkl'); H.columns = ['o', 'h', 'l', 'c', 'v'][:len(H.columns)]
    H4 = H.resample('4h', origin='epoch').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    G = df_from("data/candles/BINANCE_XAUTUSDT_1m.json").resample('15min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    X = df_from("data/candles/XAUTUSDT_1m.json").resample('15min').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    sd, s4, s1 = d1_score(D), tf_score(H4) * 10 / 10, tf_score(H)
    r1 = rsi(H.c); at1 = adx(H)[3]; atr_hot = at1 > 1.1 * at1.rolling(20).mean()
    fv = h1_fvgs(H)
    def asof(s, t, lag):            # last CLOSED bar value
        s2 = s[s.index <= t - lag]; return s2.iloc[-1] if len(s2) else np.nan
    eq, trades, day, day_l, day_pnl = 1.0, [], None, 0, 0.0
    Xi = {t: i for i, t in enumerate(X.index)}; busy_until = None
    for t, row in G.iterrows():
        if busy_until is not None and t <= busy_until: continue
        hm = t.hour * 60 + t.minute
        if not (420 <= hm < 600 or 660 <= hm < 840): continue
        if day != t.date(): day, day_l, day_pnl = t.date(), 0, 0.0
        if day_l >= 3 or day_pnl <= -0.01: continue
        d_, h4_, h1_ = asof(sd, t, pd.Timedelta('1D')), asof(s4, t, pd.Timedelta('4h')), asof(s1, t, pd.Timedelta('1h'))
        if not (d_ > 0 and h4_ > 0 and h1_ > 0): continue
        if asof(r1, t, pd.Timedelta('1h')) > 70: continue
        tot = 0.4 * d_ + 0.35 * h4_ + 0.25 * h1_
        zs = [z for z in fv if z[0] <= t - pd.Timedelta('1h') and z[0] >= t - pd.Timedelta('48h')]
        zs = [z for z in zs if H.l[(H.index > z[0]) & (H.index <= t - pd.Timedelta('1h'))].min() >= z[1]] if zs else []
        if not zs: continue
        z = zs[-1]; top, bot = z[2], z[1]
        gi = G.index.get_loc(t)
        if gi < 3: continue
        tagged = row.l <= top and row.c >= bot
        rej = row.c > row.o and (row.c - row.l) >= 0.6 * (row.h - row.l + 1e-9)
        mss = row.c > G.h.iloc[gi-3:gi].max()
        if not (tagged and (rej or mss)): continue
        nxt = t + pd.Timedelta('15min')
        if nxt not in Xi: continue
        k = Xi[nxt]; ratio = X.c.iloc[k-1] / row.c
        entry = X.o.iloc[k] * (1 + hs)
        stop_pct = min(0.005, max(0.002, (row.c - bot * 0.9995) / row.c))
        hh = H.h[(H.index <= t - pd.Timedelta('1h')) & (H.index >= t - pd.Timedelta('48h'))].max()
        tgt_pct = hh / row.c - 1
        if tgt_pct < stop_pct: continue
        size = risk / stop_pct * (1 if tot >= 5.8 else 0.5) * (0.5 if h1_ < 3 else 1) * (0.5 if asof(atr_hot, t, pd.Timedelta('1h')) else 1)
        size = min(size, 1.0)
        stop = entry * (1 - stop_pct); tp = entry * (1 + tgt_pct); inv = bot * ratio
        rem, pnl, lvl, be = 1.0, 0.0, entry * 1.0005, False; fee_in = 0.0013
        why = "time"; j = k
        for j in range(k, min(len(X), k + 96)):
            b = X.iloc[j]
            if b.l <= stop:
                px = min(stop, b.o) * (1 - hs); pnl += rem * (px * (1 - 0.0013) / entry - 1); rem = 0; why = "stop_be" if be else "stop"; break
            while b.h > lvl and rem > 0.01:
                part = rem * 0.5 if rem > 0.1 else rem
                pnl += part * (lvl * (1 - 0.001) / entry - 1); rem -= part
                if not be: stop = entry; be = True
                lvl += entry * 0.00125
            if rem > 0 and b.h > tp:
                pnl += rem * (tp * (1 - 0.001) / entry - 1); rem = 0; why = "tp"; break
            tj = X.index[j]
            if tj.minute == 45:   # H1 close check (invalidation)
                if b.c < inv: px = b.c * (1 - hs); pnl += rem * (px * (1 - 0.0013) / entry - 1); rem = 0; why = "invalidation"; break
            if rem <= 0.01: why = "scaled_out"; break
        if rem > 0: pnl += rem * (X.c.iloc[j] * (1 - hs) * (1 - 0.0013) / entry - 1)
        pnl -= fee_in
        trades.append({"t": t, "exit_t": X.index[j], "ret": pnl, "size": size, "pnl_eq": size * pnl, "why": why,
                       "score": round(tot, 2), "d1": d_, "h4": h4_, "h1": h1_, "stop_pct": stop_pct, "tgt_pct": tgt_pct})
        eq *= 1 + size * pnl; day_pnl += size * pnl; day_l += pnl < 0; busy_until = X.index[j]
    return pd.DataFrame(trades)

def report(T, t0, t1, label):
    x = T[(T.t >= t0) & (T.t < t1)]; days = (t1 - t0).total_seconds() / 86400
    if not len(x): print(label, "no trades"); return
    w, s = [], t0
    while s + pd.Timedelta('1D') <= t1:
        y = x[(x.t >= s) & (x.t < s + pd.Timedelta('1D'))]; w.append(np.prod(1 + y.pnl_eq) - 1 if len(y) else 0); s += pd.Timedelta('12h')
    print(f"{label}: trades {len(x)} ({len(x)/days:.2f}/day) | net/trade {x.ret.mean()*100:.3f}% (unsized) | win {100*(x.ret>0).mean():.0f}% | "
          f"total {100*(np.prod(1+x.pnl_eq)-1):.2f}% | 24h windows profitable {100*np.mean([v>0 for v in w]):.0f}% median {100*np.median(w):.2f}% worst {100*min(w):.2f}% | exits {x.why.value_counts().to_dict()}")

if __name__ == "__main__":
    for risk, hs in ((0.015, 0.0005), (0.006, 0.0005), (0.015, 0.001)):
        T = run(risk, hs)
        if not len(T): print("no trades"); continue
        t0 = pd.Timestamp("2026-08-24", tz="UTC"); split = t0 + pd.Timedelta("40D"); t1 = pd.Timestamp("2026-10-22T14:00", tz="UTC")
        print(f"== risk {risk*100:.1f}%  half-spread {hs*1e4:.0f}bp")
        report(T, t0, split, "  first 40d"); report(T, split, t1, "  last ~19d")
    T.to_csv("data/gold_setup_trades.csv", index=False)
