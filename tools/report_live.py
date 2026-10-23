"""Markdown performance report for the live gap bot (read-only: never places orders).
Usage: python tools/report_live.py [--log logs/live_xaut.jsonl] [--runlog logs/live_xaut_run.log] [--offline] > report.md
PnL is measured vs the challenge capital of 5,000,000 TMN, and vs the USDT equity at the first live start."""
import argparse, json, os, re, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zoneinfo import ZoneInfo
TZ = ZoneInfo("Asia/Tehran"); START_TMN = 5_000_000

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--log", default="logs/live_xaut.jsonl")
    ap.add_argument("--runlog", default="logs/live_xaut_run.log"); ap.add_argument("--offline", action="store_true"); a = ap.parse_args()
    R = [json.loads(l) for l in open(a.log) if l.strip()]
    starts = [r for r in R if r["event"] == "start" and r.get("live")]
    if not starts: print("no live run found"); return
    t0 = starts[0]["ts"]; R = [r for r in R if r["ts"] >= t0]
    orders = [r for r in R if r["event"] == "order" and r.get("dry_run") is False]
    vetoes = [r for r in R if r["event"] == "veto"]; errs = [r for r in R if r["event"] == "cycle_error"]
    ai = [r for r in R if r["event"] == "ai_regime"]
    b0 = starts[0]["balances"]; sym = orders[0]["payload"]["symbol"] if orders else "XAUTUSDT"
    eq0 = b0["usdt"] + b0["xaut"] * (orders[0]["fill"]["avg"] if orders else 0)
    vol = sum(o["fill"]["qty"] * o["fill"]["avg"] for o in orders if o.get("fill"))
    fees = sum(float(o["fill"].get("fee") or 0) * (o["fill"]["avg"] if o["payload"]["side"] == "BUY" else 1) for o in orders if o.get("fill"))
    # equity curve from the run log (lines after the first live start marker)
    eqs, live_seen = [], False
    if os.path.exists(a.runlog):
        for l in open(a.runlog):
            if '"event": "start"' in l and '"live": true' in l: live_seen = True
            m = re.match(r"(\d\d:\d\d:\d\d) eq=([\d.]+)", l)
            if live_seen and m: eqs.append(float(m.group(2)))
    usdt, xaut, tmn, bid, usdt_tmn = b0["usdt"], b0["xaut"], b0["tmn"], None, None
    if not a.offline:
        from bot.wallex import WallexClient
        c = WallexClient(); b = c.balances(); g = lambda k: float(b.get(k, {}).get("value") or 0)
        usdt, xaut, tmn = g("USDT"), g(sym[:-4]), g("TMN"); bid = c.best_bid_ask(sym)[0]; usdt_tmn = c.best_bid_ask("USDTTMN")[0]
    else:
        last = [o for o in orders if o.get("balances_after")]
        if last: usdt, xaut = last[-1]["balances_after"]["usdt"], last[-1]["balances_after"]["xaut"]
        bid = orders[-1]["fill"]["avg"] if orders else 0
    eq = usdt + xaut * (bid or 0); eqs.append(eq)
    peak, mdd = eqs[0], 0.0
    for e in eqs: peak = max(peak, e); mdd = max(mdd, (peak - e) / peak if peak else 0)
    now = dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    print(f"# Live performance report ({now} Tehran)\n")
    print(f"- Live since: {starts[0]['time_tehran']} Tehran; restarts: {len(starts) - 1}; AI backend: {starts[-1]['backend']}; cycle errors: {len(errs)}")
    print(f"- Trades: **{len(orders)}**; volume {vol:.2f} USDT; avg trade {vol / len(orders) if orders else 0:.2f} USDT; fees ~{fees:.4f} USDT; vetoes logged: {len(vetoes)}; AI regime calls: {len(ai)}")
    print(f"- Balances now: {usdt:.6f} USDT, {xaut:.8f} {sym[:-4]}, {tmn:,.0f} TMN (mark {sym} bid {bid})")
    print(f"- Equity (strategy sleeve): start {eq0:.4f} USDT -> now {eq:.4f} USDT = **{(eq / eq0 - 1) * 100 if eq0 else 0:+.3f}%**; max drawdown {mdd * 100:.3f}%")
    if usdt_tmn:
        tot = tmn + eq * usdt_tmn
        print(f"- Account value at USDTTMN bid {usdt_tmn:,.0f}: **{tot:,.0f} TMN** vs {START_TMN:,} = **{(tot / START_TMN - 1) * 100:+.3f}%** (unrealised until liquidation to TMN)")
    print("\n| time (Tehran) | side | qty | avg price | client_id | reason |\n|---|---|---|---|---|---|")
    for o in orders:
        f = o.get("fill") or {}
        print(f"| {o['time_tehran']} | {o['payload']['side']} | {f.get('qty')} | {f.get('avg')} | {o['payload']['client_id']} | {o['reason']} |")

if __name__ == "__main__": main()
