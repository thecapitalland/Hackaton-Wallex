"""Small parameter sweep with in-sample/out-of-sample split (honest check, not a guarantee)."""
import itertools, json, sys, time
from bot.config import load_config
from bot.wallex import WallexClient
from bot.backtest import run_backtest
import bot.signals as S

c = WallexClient(); cfg = load_config()
from bot.wallex import history
def fetch(tf, n):
    return {s: history(c, s, tf, n)[:-1] for s in cfg.symbols}
res = []
for tf, n in [("60", 3000), ("240", 1200)]:
    data = fetch(tf, n)
    L = min(len(v) for v in data.values()); cut = int(L * 0.6)
    ins = {s: v[-L:][:cut] for s, v in data.items()}; oos = {s: v[-L:][cut - 210:] for s, v in data.items()}
    for strat, sa, ta, tr in itertools.product(["all", "no_breakout", "trend_only"], [2.0, 3.0], [3.0, 4.5], [2.5, 3.5]):
        S.ALLOWED.clear()
        base = {"trend_up": {"trend_follow", "breakout", "pullback"}, "range": {"mean_reversion", "breakout"},
                "volatile": {"breakout"}, "trend_down": {"mean_reversion"}}
        for k, v in base.items():
            if strat == "no_breakout": v = v - {"breakout"}
            if strat == "trend_only": v = v & {"trend_follow"}
            S.ALLOWED[k] = v
        cfg.timeframe, cfg.stop_atr, cfg.tp_atr, cfg.trail_atr = tf, sa, ta, tr
        a = run_backtest(cfg, ins); b = run_backtest(cfg, oos)
        res.append((tf, strat, sa, ta, tr, a["return_pct"], a["trades"], b["return_pct"], b["trades"], b["max_drawdown_pct"], a["buy_hold_pct"], b["buy_hold_pct"]))
res.sort(key=lambda r: -r[5])
print("tf strat stop tp trail | IS% n | OOS% n dd | BH_IS BH_OOS")
for r in res[:20]: print(r)
