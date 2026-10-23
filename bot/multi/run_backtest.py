"""Walk-forward backtest: python -m bot.multi.run_backtest [--tf 60] [--split 0.6] [--grid]"""
import argparse, itertools, json, os, time
from .params import DEFAULTS, MARKETS, TMN_ONLY, defaults
from .engine import Engine, load, usdt_rate_series, stats, buy_hold
from .selector import LLMSelector

def windows(data, p, split):
    ts = sorted({b["t"] for m in data for b in data[m]})
    t_start = ts[0] + p["warmup"] * int(p["timeframe"]) * 60
    usable = [t for t in ts if t >= t_start]
    cut = usable[int(len(usable) * split)]
    return (usable[0], cut - 1), (cut, usable[-1])

def evaluate(p, data, rates, win, markets=MARKETS, selector=None, log=None):
    e = Engine(p, data, rates, markets, selector=selector, log=log)
    r = e.run(*win); return e, r, stats(r, p["capital_tmn"])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tf", default="60"); ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--grid", action="store_true"); ap.add_argument("--params", default="data/best_params.json")
    ap.add_argument("--universe", choices=["all", "tmn"], default="all"); ap.add_argument("--usdt-alloc", type=float, default=0.4)
    ap.add_argument("--tag", default=""); ap.add_argument("--allocator", action="store_true")
    ap.add_argument("--llm", action="store_true", help="use LLMSelector (cached replay, live calls if BOXAPI_* set)")
    a = ap.parse_args()
    p = dict(defaults(), timeframe=a.tf)
    if not a.grid and os.path.exists(a.params): p.update(json.load(open(a.params)))
    p["timeframe"] = a.tf
    p["warmup"] = 210
    universe = TMN_ONLY if a.universe == "tmn" else MARKETS
    if a.universe == "all": p["usdt_alloc"] = a.usdt_alloc
    if a.allocator: p["allocator"] = True; p["usdt_alloc"] = 0.0
    data = load(MARKETS, a.tf); rates = usdt_rate_series(data, a.tf)
    IS, OOS = windows(data, p, a.split)
    f = lambda w: f"{time.strftime('%m-%d', time.gmtime(w[0]))}->{time.strftime('%m-%d', time.gmtime(w[1]))}"
    print(f"tf={a.tf} in-sample {f(IS)}  out-of-sample {f(OOS)}")
    if a.grid:  # small, coarse grid on IN-SAMPLE only; choose by return - 0.5*maxDD
        grid = {"chop_er": [0.2, 0.3], "chop_votes": [1, 2], "mr_enabled": [False, True],
                "tt_min_er": [0.1, 0.2], "trend_trail_atr": [3.0, 5.0]}
        best = None
        for combo in itertools.product(*grid.values()):
            q = dict(p, **dict(zip(grid, combo))); s = evaluate(q, data, rates, IS, universe)[2]
            score = s["return_pct"] - 0.5 * s["max_dd_pct"]
            print(dict(zip(grid, combo)), s, round(score, 2))
            if best is None or score > best[0]: best = (score, dict(zip(grid, combo)))
        p.update(best[1]); json.dump(best[1], open(a.params, "w"), indent=1); print("chosen:", best[1])
    sel = LLMSelector(p) if a.llm else None
    rows = []
    for name, w in (("in_sample", IS), ("out_of_sample", OOS), ("full", (IS[0], OOS[1]))):
        log = []
        e, r, s = evaluate(p, data, rates, w, universe, selector=sel, log=log)
        bh = [buy_hold(e, m, *w) for m in universe]
        rows.append({"window": name, "market": "PORTFOLIO", **s, "buy_hold_pct": round(sum(bh) / len(bh), 2)})
        for m, b in zip(universe, bh):
            pm = dict(p, usdt_alloc=0.95 if m.endswith('USDT') else 0.0)
            s_m = evaluate(pm, data, rates, w, [m])[2]
            rows.append({"window": name, "market": m, **s_m, "buy_hold_pct": b})
        if name == "full":
            os.makedirs("logs", exist_ok=True)
            with open(f"logs/backtest_decisions_{a.tf}{a.tag}.jsonl", "w") as fh:
                for x in log: fh.write(json.dumps(x, default=str) + "\n")
            json.dump(r["trades"], open(f"data/backtest_trades_{a.tf}{a.tag}.json", "w"), indent=1, default=str)
    hdr = ["window", "market", "return_pct", "max_dd_pct", "trades", "win_rate", "buy_hold_pct"]
    print(" | ".join(hdr))
    for x in rows: print(" | ".join(str(x[h]) for h in hdr))
    json.dump({"params": p, "rows": rows}, open(f"data/backtest_multi_{a.tf}{a.tag}.json", "w"), indent=1)

if __name__ == "__main__":
    main()
