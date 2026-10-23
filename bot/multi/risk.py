"""Central portfolio risk manager (hard limits; selector output is clamped here)."""
GROUPS = {"BTCUSDT": "BTC", "BTCTMN": "BTC", "ETHUSDT": "ETH", "ETHTMN": "ETH",
          "XAUTTMN": "GOLD", "PAXGUSDT": "GOLD", "USOONUSDT": "OIL", "USDTTMN": "USD"}

class RiskManager:
    def __init__(self, p): self.p = p
    def check_entry(self, d, price_tmn, stop_tmn, equity, cash, positions, halted):
        """Returns (qty_in_quote_units_value_tmn, reason). qty value in TMN notional; 0 = reject."""
        p = self.p
        if halted: return 0, f"halted: {halted}"
        if len(positions) >= p["max_positions"]: return 0, f"max {p['max_positions']} open positions"
        if d["market"] in positions: return 0, "already in position"
        g = GROUPS.get(d["market"], d["market"])
        if any(GROUPS.get(m, m) == g for m in positions): return 0, f"correlation guard: already hold {g}"
        if d["confidence"] < p["min_confidence"]: return 0, f"confidence {d['confidence']:.2f} < {p['min_confidence']}"
        risk_frac = (price_tmn - stop_tmn) / price_tmn
        if risk_frac <= 0: return 0, "invalid stop"
        mult = min(1.5, max(0.0, d["size_multiplier"]))
        notional = equity * p["risk_per_trade"] * mult / risk_frac
        notional = min(notional, equity * p["max_position_frac"], cash / 1.01)
        if notional < p["min_notional_tmn"]: return 0, f"notional {notional:,.0f} < min {p['min_notional_tmn']:,}"
        return notional, f"risk {p['risk_per_trade']:.1%}x{mult:.2f}, stop dist {risk_frac:.2%}"
    def check_portfolio(self, equity, day_start, peak):
        if day_start and (day_start - equity) / day_start >= self.p["daily_loss_cap"]: return "daily_loss_cap"
        if peak and (peak - equity) / peak >= self.p["max_drawdown_cap"]: return "max_drawdown_cap"
        return None
