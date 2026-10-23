"""Two strategies. Each returns an entry proposal (or None) and manages exits."""

def trend_entry(f, p):
    if p.get("trend_entry_mode") == "regime":   # low-turnover: join an established uptrend on a pullback
        if f["ema20"] > f["ema50"] > f["ema200"] and f["ema50_slope"] > p.get("trend_min_slope", 0.002) \
                and f["close"] > f["ema50"] and f["rsi"] < p.get("trend_max_rsi", 65):
            return {"strategy": "trend", "stop": f["close"] - p["trend_stop_atr"] * f["atr"],
                    "reason": f"established uptrend EMA20>50>200, EMA50 slope {f['ema50_slope']:.2%}, RSI {f['rsi']:.0f} not stretched"}
        return None
    if f["close"] > f["don_hi"] and f["ema20"] > f["ema50"] and f["close"] > f["ema200"] and f["ema50_slope"] > 0 \
            and f["vol_ratio"] >= p["trend_min_vol_ratio"]:
        return {"strategy": "trend", "stop": f["close"] - p["trend_stop_atr"] * f["atr"],
                "reason": f"Donchian{p['donchian']} breakout, EMA20>EMA50, above EMA200, vol x{f['vol_ratio']:.1f}"}

def tmn_trend_entry(f, p):
    """Toman-weakening capture: hold a hard asset vs TMN while its long TMN-denominated trend is persistent."""
    if f["ema50"] > f["ema200"] and f["close"] > f["ema50"] and f["er_long"] >= p["tt_min_er"] and f["htf_up"]:
        return {"strategy": "tmn_trend", "stop": f["close"] - p["tt_stop_atr"] * f["atr"],
                "reason": f"TMN trend: EMA50>EMA200, efficiency(60) {f['er_long']:.2f}, HTF up"}

def meanrev_entry(f, p):
    calm = f["atr_pct"] <= p["mr_max_vol_mult"] * max(f["atr_pct_avg"], 1e-9)
    if f["rsi"] < p["mr_rsi"] and f["close"] < f["bb_lo"] and calm and f["ema50_slope"] > -p["mr_max_down_slope"]:
        return {"strategy": "meanrev", "stop": f["close"] - p["mr_stop_atr"] * f["atr"], "target": f["sma20"],
                "reason": f"RSI {f['rsi']:.0f} below {p['mr_rsi']}, close under lower BB, vol calm ({f['atr_pct']:.3%})"}

def manage_exit(pos, bar, f, p):
    """Called on each closed bar while in position. Returns (exit_price, reason) or (None, '')."""
    if bar["l"] <= pos["stop"]:
        return min(pos["stop"], bar["o"]), "stop" if pos["stop"] < pos["entry"] else "trail_stop"
    pos["bars"] += 1
    if pos["strategy"] == "tmn_trend":
        pos["peak"] = max(pos["peak"], bar["h"])
        pos["stop"] = max(pos["stop"], pos["peak"] - p["tt_trail_atr"] * f["atr"])
        if f["close"] < f["ema100"]: return None, "tmn_trend_broken"
    elif pos["strategy"] == "trend":
        pos["peak"] = max(pos["peak"], bar["h"])
        pos["stop"] = max(pos["stop"], pos["peak"] - p["trend_trail_atr"] * f["atr"])
    else:
        if bar["h"] >= f["sma20"] or f["rsi"] > p["mr_exit_rsi"]:
            return None, "mr_target"   # exit at next open (signal on close)
        if pos["bars"] >= p["mr_max_bars"]:
            return None, "time_stop"
    return None, ""
