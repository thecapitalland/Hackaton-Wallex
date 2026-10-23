"""Replay inventory-spread strategy on RECORDED real books (data/live). Signal from Wallex mid vs Binance mirror
(mirror price at or before snapshot time). Execution at a LATER snapshot >= signal_ts + latency: sell at best bid,
buy at best ask, taker fee. Excess vs passive 50% inventory, in quote ccy. Also reports executable gap stats:
share of snapshots where (bid/global-1) > fee+thr (sell-side) or (1-ask/global) > fee+thr (buy-side)."""
import json, glob, bisect, sys
import numpy as np
FEE = {"BTCUSDT": 0.0013, "ETHUSDT": 0.0013, "XAUTUSDT": 0.0013}
def load():
    B = [json.loads(l) for f in sorted(glob.glob("data/live/binance_*.jsonl")) for l in open(f)]
    W = [json.loads(l) for f in sorted(glob.glob("data/live/wallex_*.jsonl")) for l in open(f)]
    return B, W
def run(m, B, W, p, lat):
    bt = [b["ts"] for b in B if m in b["p"]]; bp = [b["p"][m] for b in B if m in b["p"]]
    S = [w for w in W if w["sym"] == m and w["bid"] and w["ask"]]
    if len(S) < 30 or not bt: return None
    ts = np.array([s["ts"] for s in S]); bid = np.array([max(x[0] for x in s["bid"]) for s in S]); ask = np.array([min(x[0] for x in s["ask"]) for s in S])
    g = np.array([bp[max(0, bisect.bisect_right(bt, t) - 1)] for t in ts]); mid = (bid + ask) / 2
    prem = mid / g - 1; base_med = np.array([np.median(prem[max(0, i - 360):i + 1]) for i in range(len(prem))])  # ~30min rolling
    dev = prem - base_med; f = FEE[m]
    q = 0.5 / mid[0]; cash = 0.5; q0, c0 = q, cash; trades = 0; pend = None
    for i in range(len(S)):
        if pend and ts[i] >= pend[0]:
            side, qty = pend[1], pend[2]
            if side == "sell": cash += qty * bid[i] * (1 - f); q -= qty
            else: cash -= qty * ask[i] * (1 + f); q += qty
            trades += 1; pend = None
        if pend: continue
        tgt = 0.5 - p["step"] if dev[i] > p["thr"] else 0.5 + p["step"] if dev[i] < -p["thr"] else 0.5 if abs(dev[i]) < p["exit"] else None
        if tgt is None: continue
        eq = cash + q * mid[i]; diff = tgt * eq / mid[i] - q
        if abs(diff) * mid[i] > 0.05 * eq: pend = (ts[i] + lat, "buy" if diff > 0 else "sell", abs(diff))
    eq = cash + q * mid[-1]; eqh = c0 + q0 * mid[-1]
    sell_edge = bid / g - 1 - f; buy_edge = 1 - ask / g - f
    return {"snapshots": len(S), "hours": round((ts[-1] - ts[0]) / 3600, 2), "spread_bps_med": round(float(np.median((ask - bid) / mid)) * 1e4, 1),
            "dev_std_%": round(float(dev.std()) * 100, 3), "excess_%": round((eq / eqh - 1) * 100, 3), "trades": trades,
            "exec_sell_gt_0.2%_share": round(float((sell_edge - base_med > 0.002).mean()) * 100, 2),
            "exec_buy_gt_0.2%_share": round(float((buy_edge + base_med > 0.002).mean()) * 100, 2)}
if __name__ == "__main__":
    B, W = load()
    P = {"ETHUSDT": {"thr": 0.005, "exit": 0.0005, "step": 0.5}, "XAUTUSDT": {"thr": 0.004, "exit": 0.0005, "step": 0.5},
         "BTCUSDT": {"thr": 0.005, "exit": 0.001, "step": 0.5}}
    for m, p in P.items():
        for lat in (0, 5, 10):
            print(m, f"latency {lat}s", run(m, B, W, p, lat))
