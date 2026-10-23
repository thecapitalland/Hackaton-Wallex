"""Core job of the selector: classify regime and STAY IN CASH when the market is choppy/flat."""
from .strategies import trend_entry, meanrev_entry, tmn_trend_entry

def chop_check(f, p):
    """Returns (is_chop, reason)."""
    why = []
    if f["er"] < p["chop_er"]: why.append(f"efficiency {f['er']:.2f}<{p['chop_er']}")
    if f["adx"] < p["chop_adx"]: why.append(f"ADX {f['adx']:.0f}<{p['chop_adx']}")
    if f["bbw"] < p["chop_bbw"]: why.append(f"BB width {f['bbw']:.2%} squeeze")
    return (len(why) >= p["chop_votes"]), ", ".join(why) or "trending"

def classify(m, f, p):
    if f["atr_pct"] > 2.5 * max(f["atr_pct_avg"], 1e-9): return "volatile", None, "volatility spike >2.5x avg -> cash"
    chop, why = chop_check(f, p)
    if m in p["tmn_trend_markets"]:
        prop = tmn_trend_entry(f, p)
        if prop and not chop: return "trend_up", prop, prop["reason"]
    up = f["ema20"] > f["ema50"] and f["close"] > f["ema200"] and f["ema50_slope"] > 0
    if chop:
        if p["mr_enabled"] and f["htf_up"]:
            prop = meanrev_entry(f, p)
            if prop: return "range", prop, "chop but HTF up -> dip buy: " + prop["reason"]
        return "range", None, "chop filter -> cash (" + why + ")"
    if up and f["htf_up"]:
        prop = trend_entry(f, p)
        return "trend_up", prop, (prop["reason"] if prop else "trend_up, waiting for entry trigger") + f" | {why}"
    if not f["htf_up"]: return "trend_down", None, "higher timeframe not up -> cash"
    return "range", None, "no aligned trend -> cash"
