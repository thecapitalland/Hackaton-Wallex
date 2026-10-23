"""Technical signal + rule-based regime. Long-only (Wallex spot)."""
from .indicators import ema, rsi, atr, sma

def compute_features(c):
    closes = [b["c"] for b in c]
    vols = [b["v"] for b in c]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    r, a, vavg = rsi(closes), atr(c), sma(vols, 20)
    i = len(c) - 1
    hi20 = max(b["h"] for b in c[max(0, i - 20):i]) if i > 0 else c[i]["h"]
    lo20 = min(b["l"] for b in c[max(0, i - 20):i]) if i > 0 else c[i]["l"]
    return {
        "close": closes[i], "ema20": e20[i], "ema50": e50[i], "ema200": e200[i],
        "ema20_slope": (e20[i] - e20[i - 5]) / e20[i - 5] if i >= 5 else 0.0,
        "rsi": r[i], "rsi_prev": r[i - 1] if i else r[i], "atr": a[i], "atr_pct": a[i] / closes[i],
        "atr_pct_avg": sum(a[j] / closes[j] for j in range(max(0, i - 50), i + 1)) / min(i + 1, 51),
        "vol_ratio": vols[i] / vavg[i] if vavg[i] else 1.0, "hi20": hi20, "lo20": lo20,
    }

def rule_regime(f):
    if f["atr_pct"] > 2.0 * f["atr_pct_avg"]:
        return "volatile"
    if f["ema20"] > f["ema50"] and f["ema20_slope"] > 0.002:
        return "trend_up"
    if f["ema20"] < f["ema50"] and f["ema20_slope"] < -0.002:
        return "trend_down"
    return "range"

def technical_signal(f):
    """Return dict(action=BUY|HOLD, strategy, strength, reasons)."""
    reasons = []
    # Strategy 0: trend following (ride established uptrend, wide TP, exit mainly via trailing stop)
    if f["close"] > f["ema20"] > f["ema50"] > f["ema200"] and f["ema20_slope"] > 0.003 and 52 < f["rsi"] < 72:
        return {"action": "BUY", "strategy": "trend_follow", "strength": 0.7, "tp_mult": 2.5,
                "reasons": ["close>EMA20>EMA50>EMA200", f"EMA20 slope {f['ema20_slope']:.2%}", f"RSI {f['rsi']:.1f}"]}
    # Strategy A: trend breakout
    if f["close"] > f["hi20"] and f["ema20"] > f["ema50"] and f["vol_ratio"] > 1.1 and f["rsi"] < 78:
        reasons = [f"close {f['close']:.2f} > 20-bar high {f['hi20']:.2f}", "EMA20>EMA50",
                   f"vol x{f['vol_ratio']:.2f}", f"RSI {f['rsi']:.1f}"]
        return {"action": "BUY", "strategy": "breakout", "strength": min(1.0, f["vol_ratio"] / 2), "reasons": reasons}
    # Strategy B: trend pullback (dip to EMA20 inside uptrend, RSI turning up)
    if f["ema20"] > f["ema50"] > f["ema200"] and abs(f["close"] - f["ema20"]) < 0.5 * f["atr"] \
            and f["rsi_prev"] < 50 <= f["rsi"] + 5 and f["rsi"] > f["rsi_prev"]:
        return {"action": "BUY", "strategy": "pullback", "strength": 0.6,
                "reasons": ["uptrend EMA20>50>200", "price near EMA20", f"RSI turning up {f['rsi_prev']:.1f}->{f['rsi']:.1f}"]}
    # Strategy C: mean reversion in range
    if f["rsi"] < 30 and f["rsi"] > f["rsi_prev"] and f["close"] < f["ema20"] - 1.5 * f["atr"]:
        return {"action": "BUY", "strategy": "mean_reversion", "strength": 0.5,
                "reasons": [f"RSI oversold & rising {f['rsi']:.1f}", "close < EMA20-1.5ATR"]}
    return {"action": "HOLD", "strategy": None, "strength": 0.0, "reasons": ["no setup"]}

# Strategy whitelist per regime. Breakout was net-negative on recent Wallex 1h/4h data, so it is
# disabled by default (add it back here to experiment).
ALLOWED = {"trend_up": {"trend_follow", "pullback"}, "range": {"mean_reversion"},
           "volatile": set(), "trend_down": {"mean_reversion"}}

def rule_gate(regime, sig):
    if sig["action"] != "BUY":
        return False, "no signal"
    ok = sig["strategy"] in ALLOWED.get(regime, set())
    return ok, f"{sig['strategy']} {'allowed' if ok else 'vetoed'} in {regime}"
