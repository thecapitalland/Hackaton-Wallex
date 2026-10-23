"""ICT (Michael Huddleston) concepts, long-only:
 HTF bias  : market structure on bias TF (fractal swings; bullish after BOS above last swing high, bearish after
             break of last swing low = CHoCH down).
 Entry TF  : sell-side liquidity sweep (wick below last swing low, close back above) -> bullish CHoCH/BOS (close above
             last swing high after the sweep) with displacement leaving a bullish FVG (low[i] > high[i-2]);
             order block = last down candle before displacement. Entry = maker limit at FVG midpoint (or OB top if
             higher), stop below sweep low, target = R multiple (or previous HTF swing high if closer is disabled).
 Killzones : optional (London 07-10 UTC, NY 12-15 UTC).
 Signals are computed on GLOBAL data (Binance) and executed on Wallex via price-ratio mapping."""
import bisect

def swings(c, k=2):
    """Confirmed fractal swing points: list of (confirm_index, price) for highs and lows."""
    hi, lo = [], []
    for i in range(k, len(c) - k):
        if all(c[i]["h"] > c[j]["h"] for j in range(i - k, i + k + 1) if j != i): hi.append((i + k, c[i]["h"]))
        if all(c[i]["l"] < c[j]["l"] for j in range(i - k, i + k + 1) if j != i): lo.append((i + k, c[i]["l"]))
    return hi, lo

def bias_series(c, k=2):
    """Per bar: +1 bullish structure, -1 bearish, 0 unknown. Uses only confirmed swings."""
    hi, lo = swings(c, k); hmap = dict(hi); lmap = dict(lo)
    out, last_h, last_l, b = [], None, None, 0
    for i, x in enumerate(c):
        if i in hmap: last_h = hmap[i]
        if i in lmap: last_l = lmap[i]
        if last_h is not None and x["c"] > last_h: b = 1; last_h = None      # BOS up
        elif last_l is not None and x["c"] < last_l: b = -1; last_l = None   # break down / CHoCH
        out.append(b)
    return out

def in_killzone(t):
    h = (t % 86400) // 3600
    return 7 <= h < 10 or 12 <= h < 15

def signals(c, htf, p):
    """c: entry-TF candles; htf: bias-TF candles. Returns list of setups dicts (signal at close of bar i)."""
    hb = bias_series(htf, p.get("ict_htf_k", 2)); ht = [x["t"] for x in htf]
    htf_sec = (htf[1]["t"] - htf[0]["t"]) if len(htf) > 1 else 14400
    hi, lo = swings(c, p.get("ict_k", 2)); hmap, lmap = dict(hi), dict(lo)
    last_h = last_l = None; sweep = None; out = []
    for i in range(3, len(c)):
        x = c[i]
        if i in hmap: last_h = hmap[i]
        if i in lmap: last_l = lmap[i]
        j = bisect.bisect_right(ht, x["t"] - htf_sec) - 1          # last CLOSED htf bar
        bull = j >= 0 and hb[j] == 1
        if last_l is not None and x["l"] < last_l and x["c"] > last_l:
            sweep = {"i": i, "low": x["l"], "level": last_l}; last_l = None
        if sweep and i - sweep["i"] > p["ict_sweep_window"]: sweep = None
        if sweep and last_h is not None and x["c"] > last_h and i > sweep["i"]:
            fvg = None
            for k in range(i, max(sweep["i"], i - 3), -1):        # FVG formed in the displacement leg
                if c[k]["l"] > c[k - 2]["h"]: fvg = (c[k - 2]["h"], c[k]["l"]); break
            ob = next((c[k] for k in range(i - 1, sweep["i"] - 1, -1) if c[k]["c"] < c[k]["o"]), None)
            ok_kz = (not p["ict_killzone"]) or in_killzone(x["t"])
            if fvg and bull and ok_kz:
                entry = (fvg[0] + fvg[1]) / 2
                if ob: entry = max(entry, min(ob["h"], x["c"]))  if p.get("ict_use_ob") else entry
                stop = sweep["low"] * (1 - p["ict_stop_buf"])
                if entry > stop and (entry - stop) / entry >= p["ict_min_risk"]:
                    out.append({"i": i, "t": x["t"], "close": x["c"], "entry": entry, "stop": stop,
                                "target": entry + p["ict_rr"] * (entry - stop),
                                "reason": f"HTF bullish structure; swept SSL {sweep['level']:.2f} (low {sweep['low']:.2f}); "
                                          f"CHoCH close>{last_h:.2f}; FVG {fvg[0]:.2f}-{fvg[1]:.2f}"
                                          + (f"; OB {ob['l']:.2f}-{ob['h']:.2f}" if ob else "")
                                          + ("; killzone" if in_killzone(x["t"]) else "")})
            sweep = None if fvg else sweep
            last_h = None
    return out

def backtest(sig_c, sig_htf, exe, p, fees, t0, t1, rate=None, capital=5e6, quote="USDT"):
    """Single-market ICT backtest. Signals on global candles sig_c, fills on Wallex candles exe (same TF).
    Maker limit entry (fill if exe low < limit, strict), TP maker limit, stop taker + slippage. Risk p['risk_per_trade'].
    For USDT quote: capital converted to USDT at start / back at end via USDTTMN taker (rate dict t->TMN per USDT)."""
    import json
    rate = rate or {}; rk = sorted(rate)
    def rt(t):
        if not rk: return 1.0
        i = bisect.bisect_right(rk, t) - 1; return rate[rk[max(i, 0)]]
    conv = 0.003 + 1e-4
    sigs = [s for s in signals(sig_c, sig_htf, p) if t0 <= s["t"] <= t1]
    ex_t = [b["t"] for b in exe]; exi = {b["t"]: i for i, b in enumerate(exe)}
    gi = {b["t"]: i for i, b in enumerate(sig_c)}
    eq = capital * ((1 - conv) / rt(t0) if quote == "USDT" else 1)
    start_eq = eq; peak, mdd, trades, busy_until = eq, 0.0, [], 0
    slip = p["slippage_bps"] / 1e4
    for s in sigs:
        if s["t"] < busy_until or s["t"] not in exi: continue
        i0 = exi[s["t"]]; ratio = exe[i0]["c"] / s["close"]        # map global prices to Wallex
        lim, stop, tgt = s["entry"] * ratio, s["stop"] * ratio, s["target"] * ratio
        fill_i = next((k for k in range(i0 + 1, min(len(exe), i0 + 1 + p["ict_limit_bars"])) if exe[k]["l"] < lim), None)
        if fill_i is None: continue
        px = min(lim, exe[fill_i]["o"]); risk_frac = (px - stop) / px
        if risk_frac < p["ict_min_risk"] / 2: continue          # gapped to/below stop: skip (never negative size)
        notional = min(eq * p["risk_per_trade"] / risk_frac, eq * p["max_position_frac"])
        qty = notional / px; cost_in = notional * (1 + fees["maker"])
        ex_px, why, k = None, "", fill_i
        for k in range(fill_i, len(exe)):
            b = exe[k]
            if b["t"] > t1: ex_px, why = b["o"] * (1 - slip), "force_close_end"; break
            if b["l"] <= stop: ex_px, why = min(stop, b["o"]) * (1 - slip), "stop"; break
            if k > fill_i and b["h"] > tgt: ex_px, why = tgt, "target"; break
            if k - fill_i >= p["ict_max_bars"]: ex_px, why = b["c"] * (1 - slip), "time_stop"; break
        if ex_px is None: ex_px, why = exe[k]["c"] * (1 - slip), "force_close_end"
        fee_out = fees["maker"] if why == "target" else fees["taker"]
        pnl = qty * ex_px * (1 - fee_out) - cost_in; eq += pnl
        peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
        trades.append({"t": s["t"], "fill_t": exe[fill_i]["t"], "exit_t": exe[k]["t"], "entry": px, "exit": ex_px,
                       "reason": why, "gross_move": ex_px / px - 1, "ret": pnl / notional, "pnl": pnl, "why": s["reason"]})
        busy_until = exe[k]["t"]
        if (start_eq - eq) / start_eq >= p["max_drawdown_cap"]: break
    final = eq * (rt(t1) * (1 - conv) if quote == "USDT" else 1)
    n = len(trades); w = sum(1 for x in trades if x["pnl"] > 0)
    rtc = fees["maker"] + (fees["maker"] + fees["taker"]) / 2 + slip
    return {"return_pct": round((final / capital - 1) * 100, 2), "max_dd_pct": round(mdd * 100, 2), "trades": n,
            "win_rate": round(w / n * 100, 1) if n else 0.0,
            "avg_abs_move_pct": round(sum(abs(x["gross_move"]) for x in trades) / n * 100, 3) if n else 0,
            "avg_gross_pct": round(sum(x["gross_move"] for x in trades) / n * 100, 3) if n else 0,
            "roundtrip_cost_pct": round(rtc * 100, 3), "trade_list": trades}
