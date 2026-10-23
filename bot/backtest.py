"""Bar-by-bar backtest on Wallex candles using the SAME signal/regime(rules)/risk code as live."""
import json
from .signals import compute_features, technical_signal, rule_regime, rule_gate
from .risk import size_position, update_exit, daily_killed

def run_backtest(cfg, candles_by_sym, markets=None, warmup=210):
    cash, eq0 = cfg.start_equity_usdt, cfg.start_equity_usdt
    positions, trades, curve = {}, [], []
    times = sorted({b["t"] for c in candles_by_sym.values() for b in c})
    idx = {s: {b["t"]: i for i, b in enumerate(c)} for s, c in candles_by_sym.items()}
    day, day_start, killed = None, eq0, False
    peak_eq, max_dd = eq0, 0.0
    for t in times[warmup:]:
        prices = {}
        for s, c in candles_by_sym.items():
            i = idx[s].get(t)
            if i is None or i < warmup: continue
            bar, hist = c[i], c[max(0, i - 300):i]               # decide on closed bars, fill at this bar's open
            f = compute_features(hist)
            prices[s] = bar["c"]
            if s in positions:
                px, why = update_exit(cfg, positions[s], bar["h"], bar["l"], f["atr"])
                if px:
                    p = positions.pop(s)
                    px = min(px, bar["o"]) if why.endswith("stop") and bar["o"] < px else px  # gap through stop
                    proceeds = p["qty"] * px * (1 - cfg.fee_rate)
                    cash += proceeds
                    trades.append({"symbol": s, "t": t, "entry": p["entry"], "exit": px, "reason": why,
                                   "strategy": p["strategy"], "pnl": proceeds - p["cost"]})
                continue
            sig = technical_signal(f)
            ok_gate, _ = rule_gate(rule_regime(f), sig)
            if not ok_gate or killed: continue
            eq = cash + sum(p["qty"] * prices.get(k, p["entry"]) for k, p in positions.items())
            entry = bar["o"] * (1 + cfg.limit_offset_bps / 1e4)
            ok, qty, stop, tp, _ = size_position(cfg, eq, cash, entry, f["atr"], len(positions), killed,
                                                 (markets or {}).get(s), sig.get("tp_mult", 1.0))
            if ok:
                cost = qty * entry * (1 + cfg.fee_rate)
                cash -= cost
                positions[s] = {"qty": qty, "entry": entry, "stop": stop, "tp": tp, "peak": entry,
                                "cost": cost, "strategy": sig["strategy"]}
        eq = cash + sum(p["qty"] * prices.get(k, p["entry"]) for k, p in positions.items())
        d = t // 86400
        if d != day:
            day, day_start, killed = d, eq, False
        killed = killed or daily_killed(cfg, day_start, eq)
        peak_eq = max(peak_eq, eq); max_dd = max(max_dd, (peak_eq - eq) / peak_eq)
        curve.append({"t": t, "equity": eq})
    final = curve[-1]["equity"] if curve else eq0
    wins = [x for x in trades if x["pnl"] > 0]
    gp, gl = sum(x["pnl"] for x in wins), -sum(x["pnl"] for x in trades if x["pnl"] <= 0)
    # buy & hold equal-weight benchmark over the same window
    bh = []
    for s, c in candles_by_sym.items():
        if len(c) > warmup: bh.append(c[-1]["c"] / c[warmup]["o"] - 1)
    return {"start_equity": eq0, "final_equity": round(final, 2), "return_pct": round((final / eq0 - 1) * 100, 2),
            "max_drawdown_pct": round(max_dd * 100, 2), "trades": len(trades),
            "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
            "profit_factor": round(gp / gl, 2) if gl else None,
            "open_positions_at_end": len(positions),
            "buy_hold_pct": round(100 * sum(bh) / len(bh), 2) if bh else None,
            "bars": len(times) - warmup, "trade_list": trades}
