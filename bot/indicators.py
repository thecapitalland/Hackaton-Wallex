"""Pure-python indicators (no numpy) so they are trivially backtestable."""

def ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out

def rsi(closes, n=14):
    out = [50.0] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0), max(-ch, 0)
        if i <= n:
            gain += g / n; loss += l / n
        else:
            gain = (gain * (n - 1) + g) / n; loss = (loss * (n - 1) + l) / n
        if i >= n:
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out

def atr(c, n=14):
    trs, out, a = [], [], None
    for i, b in enumerate(c):
        tr = b["h"] - b["l"] if i == 0 else max(b["h"] - b["l"], abs(b["h"] - c[i-1]["c"]), abs(b["l"] - c[i-1]["c"]))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
    return out

def sma(vals, n):
    out, s = [], 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n: s -= vals[i - n]
        out.append(s / min(i + 1, n))
    return out

def adx(c, n=14):
    out, pdm_s, ndm_s, tr_s, a = [0.0] * len(c), 0.0, 0.0, 0.0, None
    for i in range(1, len(c)):
        up, dn = c[i]["h"] - c[i-1]["h"], c[i-1]["l"] - c[i]["l"]
        pdm, ndm = (up if up > dn and up > 0 else 0), (dn if dn > up and dn > 0 else 0)
        tr = max(c[i]["h"] - c[i]["l"], abs(c[i]["h"] - c[i-1]["c"]), abs(c[i]["l"] - c[i-1]["c"]))
        pdm_s += (pdm - pdm_s) / n; ndm_s += (ndm - ndm_s) / n; tr_s += (tr - tr_s) / n
        if tr_s <= 0: out[i] = out[i-1]; continue
        pdi, ndi = 100 * pdm_s / tr_s, 100 * ndm_s / tr_s
        dx = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0
        a = dx if a is None else a + (dx - a) / n
        out[i] = a
    return out

def efficiency_ratio(vals, n=20):
    out = [0.0] * len(vals)
    for i in range(n, len(vals)):
        path = sum(abs(vals[j] - vals[j-1]) for j in range(i - n + 1, i + 1))
        out[i] = abs(vals[i] - vals[i-n]) / path if path else 0.0
    return out
