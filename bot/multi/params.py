from .fees import load_fees
DEFAULTS = {
    "timeframe": "60", "warmup": 210, "capital_tmn": 5_000_000,
    # fees: per-market from data/fees.json (Wallex /v1/account/fee); these are fallbacks only
    "fees": {}, "fee_default": {"TMN": {"maker": 0.0025, "taker": 0.003}, "USDT": {"maker": 0.001, "taker": 0.0013}},
    "slippage_bps": 5,
    "spread_bps": {"BTCUSDT": 2, "ETHUSDT": 7, "BTCTMN": 5, "ETHTMN": 16, "XAUTTMN": 31, "PAXGUSDT": 65, "USOONUSDT": 60, "USDTTMN": 1},
    # funding: fraction of capital converted TMN->USDT once at start (USDTTMN taker), back at end
    "usdt_alloc": 0.0, "allocator": False, "rebalance_band": 0.1,
    # maker entries: limit buy at signal close*(1-offset); fills only if next bar LOW trades strictly below limit
    "maker_entries": True, "maker_offset_bps": 10,
    "risk_per_trade": 0.015, "max_positions": 3, "max_position_frac": 0.45, "min_notional_tmn": 50_000,
    "daily_loss_cap": 0.06, "max_drawdown_cap": 0.12, "min_confidence": 0.5, "max_spread_bps": 40,
    "donchian": 40, "trend_stop_atr": 2.5, "trend_trail_atr": 5.0, "trend_min_vol_ratio": 1.0,
    "trend_entry_mode": "breakout", "trend_min_slope": 0.002, "trend_max_rsi": 65,
    "mr_enabled": True, "mr_rsi": 25, "mr_exit_rsi": 55, "mr_stop_atr": 2.0, "mr_max_bars": 24, "mr_max_vol_mult": 1.5,
    "mr_max_down_slope": 0.01, "mr_in_downtrend": False,
    "chop_er": 0.3, "chop_adx": 20, "chop_bbw": 0.01, "chop_votes": 2,
    "tmn_trend_markets": ["USDTTMN", "XAUTTMN", "BTCTMN"], "tt_min_er": 0.15, "tt_stop_atr": 4.0, "tt_trail_atr": 6.0,
}
MARKETS = ["BTCUSDT", "ETHUSDT", "BTCTMN", "ETHTMN", "XAUTTMN", "PAXGUSDT", "USOONUSDT", "USDTTMN"]
TMN_ONLY = ["BTCTMN", "ETHTMN", "XAUTTMN", "USDTTMN"]

def defaults():
    p = dict(DEFAULTS); p["fees"] = load_fees(); return p
