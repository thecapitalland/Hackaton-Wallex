"""Hard risk rules. Nothing upstream (incl. the LLM) can override these."""
import math

def round_step(x, step, up=False):
    """Wallex returns stepSize/tickSize as number of DECIMALS (e.g. 8 -> 1e-8, 0 -> integer).
    Values < 1 are treated as an explicit increment."""
    step = float(step if step is not None else 8)
    if step >= 1 or step == 0:
        step = 10 ** (-int(step))
    n = (math.ceil if up else math.floor)(x / step + 1e-9)
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, dec)

def size_position(cfg, equity, cash, price, atr_v, n_open, killed, market=None, tp_mult=1.0):
    """Return (ok, qty, stop, tp, reason)."""
    if killed:
        return False, 0, 0, 0, "kill switch active (daily drawdown)"
    if n_open >= cfg.max_open_positions:
        return False, 0, 0, 0, f"max open positions {cfg.max_open_positions}"
    stop = price - cfg.stop_atr * atr_v
    tp = price + cfg.tp_atr * tp_mult * atr_v
    per_unit = price - stop
    if per_unit <= 0:
        return False, 0, 0, 0, "invalid stop"
    qty = (equity * cfg.risk_per_trade) / per_unit
    qty = min(qty, equity * cfg.max_position_frac / price, cash * 0.995 / (price * (1 + cfg.fee_rate)))
    min_notional = 0.0
    if market:
        qty = round_step(qty, market.get("stepSize") or 0)
        min_notional = float(market.get("minNotional") or 0)
        if qty < float(market.get("minQty") or 0):
            return False, 0, 0, 0, "qty < minQty"
    if qty <= 0 or qty * price < max(min_notional, 1e-9):
        return False, 0, 0, 0, f"notional {qty*price:.2f} < minNotional {min_notional}"
    return True, qty, stop, tp, f"risk {cfg.risk_per_trade:.1%} of equity, stop {cfg.stop_atr}ATR, tp {cfg.tp_atr}ATR"

def update_exit(cfg, pos, bar_high, bar_low, atr_v):
    """Trail stop and check exits on a bar. Returns (exit_price or None, reason)."""
    if bar_low <= pos["stop"]:
        return pos["stop"], "stop" if pos["stop"] < pos["entry"] else "trail_stop"
    if bar_high >= pos["tp"]:
        return pos["tp"], "take_profit"
    pos["peak"] = max(pos.get("peak", pos["entry"]), bar_high)
    new_stop = pos["peak"] - cfg.trail_atr * atr_v
    if pos["peak"] > pos["entry"] + cfg.trail_activate_atr * atr_v:  # trail only once trade has room
        pos["stop"] = max(pos["stop"], new_stop)
    return None, ""

def daily_killed(cfg, day_start_equity, equity):
    return day_start_equity > 0 and (day_start_equity - equity) / day_start_equity >= cfg.max_daily_drawdown
