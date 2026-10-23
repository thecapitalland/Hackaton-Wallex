"""Triangular arb research inside Wallex (NO orders). Modes:
  snapshot | record (-> data/tri_record.csv + logs/tri_shadow.jsonl) | summary | history
Loops: A = TMN->USDT->ASSET->TMN ; B = TMN->ASSET->USDT->TMN. Taker fills, VWAP over depth for SIZE_TMN."""
import sys, os, csv, json, time, requests
W = "https://api.wallex.ir"
ASSETS = ["XAUT", "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "TON", "TRX"]
SIZE_TMN = 5_000_000; MIN_TMN = 50_000
FEES = json.load(open("data/tri_fees.json")) if os.path.exists("data/tri_fees.json") else {}
def tk(sym): return float(FEES.get(sym, {}).get("takerFeeRate", 0.003 if sym.endswith("TMN") else 0.0013))
s = requests.Session()

def depth(sym):
    d = s.get(W + "/v1/depth", params={"symbol": sym}, timeout=5).json()["result"]
    f = lambda L: [(float(x["price"]), float(x["quantity"])) for x in (L or [])]
    return f(d.get("bid")), f(d.get("ask"))

def buy_with_quote(asks, quote):   # spend quote -> base qty (None if insufficient depth)
    q = 0.0
    for p, a in asks:
        c = min(quote, p * a); q += c / p; quote -= c
        if quote <= 1e-12: return q
    return None
def sell_base(bids, base):         # sell base -> quote
    out = 0.0
    for p, a in bids:
        c = min(base, a); out += c * p; base -= c
        if base <= 1e-15: return out
    return None

def evaluate(books, size=SIZE_TMN):
    ub, ua = books["USDTTMN"]; rows = []
    for A in ASSETS:
        if f"{A}TMN" not in books or f"{A}USDT" not in books: continue
        tb, ta = books[f"{A}TMN"]; xb, xa = books[f"{A}USDT"]
        if not (tb and ta and xb and xa and ub and ua): continue
        fT, fX, fU = tk(f"{A}TMN"), tk(f"{A}USDT"), tk("USDTTMN")
        r = {"asset": A}
        for lbl, sz in (("top", None), ("sz", size)):
            if sz is None:   # top-of-book, ignore depth
                a = (1/ua[0][0])*(1-fU) / xa[0][0]*(1-fX) * tb[0][0]*(1-fT) - 1
                b = (1/ta[0][0])*(1-fT) * xb[0][0]*(1-fX) * ub[0][0]*(1-fU) - 1
            else:
                u = buy_with_quote(ua, sz); u = u and u*(1-fU)
                q = u and buy_with_quote(xa, u); q = q and q*(1-fX)
                t = q and sell_base(tb, q); a = t*(1-fT)/sz - 1 if t else None
                q = buy_with_quote(ta, sz); q = q and q*(1-fT)
                u = q and sell_base(xb, q); u = u and u*(1-fX)
                t = u and sell_base(ub, u); b = t*(1-fU)/sz - 1 if t else None
            r[f"A_{lbl}"] = a; r[f"B_{lbl}"] = b
        gross = lambda v, f: None if v is None else (1+v)/((1-fT)*(1-fX)*(1-fU)) - 1
        r["A_gross_top"] = gross(r["A_top"], 0); r["B_gross_top"] = gross(r["B_top"], 0)
        r["fees_total"] = fT + fX + fU
        # max TMN size at which loop stays >0 (scan)
        for L in "AB":
            best = 0
            for sz in (MIN_TMN, 2e5, 5e5, 1e6, 2e6, 5e6, 1e7, 2e7):
                e = evaluate_one(books, A, L, sz)
                if e is not None and e > 0: best = sz
            r[f"{L}_maxsz_pos"] = best
        rows.append(r)
    return rows

def evaluate_one(books, A, L, sz):
    ub, ua = books["USDTTMN"]; tb, ta = books[f"{A}TMN"]; xb, xa = books[f"{A}USDT"]
    fT, fX, fU = tk(f"{A}TMN"), tk(f"{A}USDT"), tk("USDTTMN")
    if L == "A":
        u = buy_with_quote(ua, sz); q = u and buy_with_quote(xa, u*(1-fU)); t = q and sell_base(tb, q*(1-fX))
        return t*(1-fT)/sz - 1 if t else None
    q = buy_with_quote(ta, sz); u = q and sell_base(xb, q*(1-fT)); t = u and sell_base(ub, u*(1-fX))
    return t*(1-fU)/sz - 1 if t else None

def fetch_all():
    books = {}
    for sym in ["USDTTMN"] + [f"{a}{q}" for a in ASSETS for q in ("TMN", "USDT")]:
        try: books[sym] = depth(sym)
        except Exception: pass
        time.sleep(0.15)
    return books

def fmt(v): return "  n/a " if v is None else f"{v*100:+.3f}%"
def snapshot():
    books = fetch_all(); ua = books["USDTTMN"][1][0][0]; ub = books["USDTTMN"][0][0][0]
    print(f"USDTTMN bid {ub:.0f} ask {ua:.0f}; size={SIZE_TMN:,} TMN")
    print("asset  fees   A_top    A_5M     B_top    B_5M   A_gross  B_gross  Amax Bmax")
    for r in evaluate(books):
        print(f"{r['asset']:5} {r['fees_total']*100:.2f}% {fmt(r['A_top'])} {fmt(r['A_sz'])} {fmt(r['B_top'])} {fmt(r['B_sz'])} {fmt(r['A_gross_top'])} {fmt(r['B_gross_top'])} {r['A_maxsz_pos']:.0f} {r['B_maxsz_pos']:.0f}")

COLS = ["ts", "asset", "A_top", "A_sz", "B_top", "B_sz", "A_gross_top", "B_gross_top", "A_maxsz_pos", "B_maxsz_pos", "usdt_bid", "usdt_ask", "tmn_bid", "tmn_ask", "usdtm_bid", "usdtm_ask"]
def record(period=8):
    os.makedirs("logs", exist_ok=True); new = not os.path.exists("data/tri_record.csv")
    with open("data/tri_record.csv", "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(COLS)
        while True:
            t0 = time.time()
            try:
                books = fetch_all(); ub, ua = books["USDTTMN"]
                for r in evaluate(books):
                    A = r["asset"]; tb, ta = books[f"{A}TMN"]; xb, xa = books[f"{A}USDT"]
                    w.writerow([round(t0, 1), A] + [r[k] for k in COLS[2:10]] + [ub[0][0], ua[0][0], tb[0][0], ta[0][0], xb[0][0], xa[0][0]])
                    for L, name in (("A", "TMN->USDT->ASSET->TMN"), ("B", "TMN->ASSET->USDT->TMN")):
                        e = r[f"{L}_sz"]; et = r[f"{L}_top"]
                        if (e is not None and e > 0) or (et is not None and et > 0):
                            with open("logs/tri_shadow.jsonl", "a") as g:
                                g.write(json.dumps({"ts": t0, "asset": A, "loop": name, "net_top": et, "net_5M": e,
                                    "max_pos_size_tmn": r[f"{L}_maxsz_pos"], "gross_top": r[f"{L}_gross_top"],
                                    "reason": f"net edge after 3 taker fees ({r['fees_total']*100:.2f}%) > 0 at " + ("5M TMN depth" if (e or 0) > 0 else "top-of-book only")}) + "\n")
                f.flush()
            except Exception as ex:
                with open("logs/tri_shadow.jsonl", "a") as g: g.write(json.dumps({"ts": t0, "error": repr(ex)[:200]}) + "\n")
            time.sleep(max(1, period - (time.time() - t0)))

def summary():
    import pandas as pd
    d = pd.read_csv("data/tri_record.csv"); n = d.ts.nunique()
    span = (d.ts.max() - d.ts.min()) / 60
    print(f"snapshots={n} span={span:.1f}min")
    g = d.groupby("asset")
    out = pd.DataFrame({
        "A_pos%": g.A_sz.apply(lambda x: (x > 0).mean()*100), "A_med": g.A_sz.median()*100, "A_max": g.A_sz.max()*100,
        "A_top_pos%": g.A_top.apply(lambda x: (x > 0).mean()*100), "A_gross_med": g.A_gross_top.median()*100,
        "B_pos%": g.B_sz.apply(lambda x: (x > 0).mean()*100), "B_med": g.B_sz.median()*100, "B_max": g.B_sz.max()*100,
        "B_top_pos%": g.B_top.apply(lambda x: (x > 0).mean()*100), "B_gross_med": g.B_gross_top.median()*100,
        "Amaxsz_med": g.A_maxsz_pos.median(), "Bmaxsz_med": g.B_maxsz_pos.median()})
    print(out.round(3).to_string())

def history():   # existing data/live recorder: XAUTTMN, BTCTMN vs XAUTUSDT/BTCUSDT (no USDTTMN -> implied rate only)
    snaps = {}
    for fn in sorted(os.listdir("data/live")):
        if not fn.startswith("wallex_"): continue
        for line in open("data/live/" + fn):
            r = json.loads(line); snaps.setdefault(round(r["ts"] / 5), {})[r["sym"]] = r
    import statistics as st
    for A in ("XAUT", "BTC"):
        v = [(x[f"{A}TMN"]["bid"][0][0] / x[f"{A}USDT"]["ask"][0][0], x[f"{A}TMN"]["ask"][0][0] / x[f"{A}USDT"]["bid"][0][0])
             for x in snaps.values() if f"{A}TMN" in x and f"{A}USDT" in x and x[f"{A}TMN"]["bid"] and x[f"{A}USDT"]["ask"]]
        if v: print(A, "implied USDT/TMN (sell-via-TMN, buy-via-TMN) n=", len(v), "median", round(st.median(a for a, _ in v)), round(st.median(b for _, b in v)))

if __name__ == "__main__":
    {"snapshot": snapshot, "record": record, "summary": summary, "history": history}[sys.argv[1] if len(sys.argv) > 1 else "snapshot"]()
