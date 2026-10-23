"""ICT backtest: python -m bot.multi.run_ict [--tf 15|1] [--bias 240|60]. Same IS/OOS split as v2."""
import argparse, json, os
from .params import defaults, MARKETS
from .engine import load, usdt_rate_series
from .run_backtest import windows
from .ict import backtest
from ..wallex import resample

ICT_P = {"ict_k": 2, "ict_htf_k": 2, "ict_sweep_window": 12, "ict_stop_buf": 0.0005, "ict_min_risk": 0.002,
         "ict_rr": 2.0, "ict_limit_bars": 8, "ict_max_bars": 96, "ict_killzone": False, "ict_use_ob": False}
MAP = {"BTCUSDT": "BTCUSDT", "BTCTMN": "BTCUSDT", "ETHUSDT": "ETHUSDT", "ETHTMN": "ETHUSDT",
       "PAXGUSDT": "PAXGUSDT", "XAUTTMN": "PAXGUSDT"}

def gload(sym, tf):
    path = f"data/candles/BINANCE_{sym}_{'1m' if tf == '1' else tf}.json"
    return json.load(open(path)) if os.path.exists(path) else None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tf", default="15"); ap.add_argument("--bias", default="240")
    ap.add_argument("--killzone", action="store_true"); ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--markets", default=",".join(MAP)); ap.add_argument("--wallex-signals", action="store_true")
    a = ap.parse_args()
    p = defaults(); p.update(ICT_P); p["ict_killzone"] = a.killzone; p["ict_rr"] = a.rr
    if a.tf == "1": p.update(ict_sweep_window=30, ict_limit_bars=15, ict_max_bars=240, ict_min_risk=0.0015)
    d60 = load(MARKETS, "60"); IS, OOS = windows(d60, p, 0.6); rate = usdt_rate_series(d60, "60")
    rows = []
    for m in a.markets.split(","):
        exe = json.load(open(f"data/candles/{m}_{'1m' if a.tf == '1' else a.tf}.json"))
        g = None if a.wallex_signals else gload(MAP[m], a.tf)
        src = "binance" if g else "wallex"; g = g or exe
        htf = resample([dict(x) for x in g], int(a.bias) * 60)
        fees = p["fees"].get(m) or p["fee_default"]["USDT" if m.endswith("USDT") else "TMN"]
        for name, w in (("in_sample", IS), ("out_of_sample", OOS), ("full", (IS[0], OOS[1]))):
            r = backtest(g, htf, exe, p, fees, *w, rate=rate, quote="USDT" if m.endswith("USDT") else "TMN")
            rows.append({"window": name, "market": m, "signals": src, **{k: v for k, v in r.items() if k != "trade_list"}})
            if name == "full":
                json.dump(r["trade_list"], open(f"data/ict_trades_{m}_{a.tf}.json", "w"), indent=1)
    hdr = ["window", "market", "return_pct", "max_dd_pct", "trades", "win_rate", "avg_gross_pct", "avg_abs_move_pct", "roundtrip_cost_pct"]
    print(" | ".join(hdr))
    for x in sorted(rows, key=lambda x: x["window"]): print(" | ".join(str(x[h]) for h in hdr))
    json.dump(rows, open(f"data/ict_results_{a.tf}_{a.bias}{'_kz' if a.killzone else ''}.json", "w"), indent=1)

if __name__ == "__main__":
    main()
