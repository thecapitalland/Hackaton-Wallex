"""Market scanner: per-market features computed only from CLOSED bars (no look-ahead)."""
from ..indicators import ema, rsi, atr, sma, adx, efficiency_ratio

def precompute(c):
    """Vectorised indicator series for a candle list (index-aligned)."""
    cl = [b["c"] for b in c]; v = [b["v"] for b in c]
    s = {"close": cl, "ema20": ema(cl, 20), "ema50": ema(cl, 50), "ema200": ema(cl, 200),
         "rsi": rsi(cl, 14), "atr": atr(c, 14), "sma20": sma(cl, 20), "vsma": sma(v, 20)}
    s["atr_pct"] = [a / x if x else 0 for a, x in zip(s["atr"], cl)]
    s["atr_pct_avg"] = sma(s["atr_pct"], 100)
    # rolling std for Bollinger
    sd = []
    for i in range(len(cl)):
        w = cl[max(0, i - 19):i + 1]; m = sum(w) / len(w)
        sd.append((sum((x - m) ** 2 for x in w) / len(w)) ** 0.5)
    s["sd20"] = sd
    s["adx"] = adx(c, 14); s["er"] = efficiency_ratio(cl, 20); s["er_long"] = efficiency_ratio(cl, 60)
    s["ema100"] = ema(cl, 100)
    s["htf_fast"] = ema(cl, 80); s["htf_slow"] = ema(cl, 200)   # ~4x-timeframe EMA20/EMA50 proxy
    return s

def snapshot(c, s, i, n_don=20, spread=0.0):
    """Feature dict at bar i (bar i is closed)."""
    hi = max(b["h"] for b in c[max(0, i - n_don):i]) if i > 0 else c[i]["h"]
    lo = min(b["l"] for b in c[max(0, i - n_don):i]) if i > 0 else c[i]["l"]
    e50p = s["ema50"][max(0, i - 10)]
    return {"close": s["close"][i], "ema20": s["ema20"][i], "ema50": s["ema50"][i], "ema200": s["ema200"][i],
            "ema50_slope": (s["ema50"][i] - e50p) / e50p if e50p else 0, "rsi": s["rsi"][i], "atr": s["atr"][i],
            "atr_pct": s["atr_pct"][i], "atr_pct_avg": s["atr_pct_avg"][i], "don_hi": hi, "don_lo": lo,
            "sma20": s["sma20"][i], "bb_lo": s["sma20"][i] - 2 * s["sd20"][i], "bb_hi": s["sma20"][i] + 2 * s["sd20"][i],
            "vol_ratio": c[i]["v"] / s["vsma"][i] if s["vsma"][i] else 1.0, "spread": spread,
            "ret_24": c[i]["c"] / c[max(0, i - 24)]["c"] - 1, "adx": s["adx"][i], "er": s["er"][i],
            "er_long": s["er_long"][i], "bbw": 4 * s["sd20"][i] / s["sma20"][i] if s["sma20"][i] else 0,
            "ema100": s["ema100"][i], "htf_up": float(s["htf_fast"][i] > s["htf_slow"][i] and c[i]["c"] > s["htf_slow"][i])}
