import math
from bot.multi.params import DEFAULTS
from bot.multi.risk import RiskManager
from bot.multi.selector import merge, RuleSelector
from bot.multi.engine import Engine, stats

P = dict(DEFAULTS)
D = {"market": "BTCTMN", "confidence": 0.8, "size_multiplier": 1.0}

def test_risk_limits():
    r = RiskManager(P)
    n, _ = r.check_entry(D, 100, 98, 5e6, 5e6, {}, None)
    assert 0 < n <= 5e6 * P["max_position_frac"]
    assert r.check_entry(D, 100, 98, 5e6, 5e6, {"BTCUSDT": {}}, None)[0] == 0          # correlation guard
    assert r.check_entry(D, 100, 98, 5e6, 5e6, {"A": 1, "B": 1, "C": 1}, None)[0] == 0  # max 3
    assert r.check_entry(D, 100, 98, 5e6, 5e6, {}, "max_drawdown_cap")[0] == 0
    assert r.check_entry(D, 100, 98, 5e6, 40_000, {}, None)[0] == 0                     # min notional
    assert r.check_portfolio(94, 100, 100) == "daily_loss_cap" and r.check_portfolio(88, 88.1, 100) == "max_drawdown_cap"

def test_llm_merge_cannot_add_setups():
    base = [{"market": "X", "regime": "range", "strategy": "none", "confidence": 0, "size_multiplier": 0, "reason": ""},
            {"market": "Y", "regime": "trend_up", "strategy": "trend", "confidence": .6, "size_multiplier": .8, "reason": "r"}]
    out = merge(base, {"decisions": [{"market": "X", "strategy": "trend", "confidence": 1, "size_multiplier": 9},
                                     {"market": "Y", "strategy": "trend", "confidence": .9, "size_multiplier": 9}]})
    by = {d["market"]: d for d in out}
    assert by["X"]["strategy"] == "none" and by["Y"]["size_multiplier"] == 1.5 and by["Y"]["confidence"] == .9
    out = merge(base, {"decisions": [{"market": "Y", "strategy": "none", "reason": "chop"}]})
    assert [d for d in out if d["market"] == "Y"][0]["strategy"] == "none"

def test_engine_force_closes_and_is_deterministic():
    bars = []
    for i in range(600):
        c = 100 * math.exp(0.002 * i) * (1 + 0.01 * math.sin(i / 3))
        bars.append({"t": 3600 * i, "o": c, "h": c * 1.004, "l": c * 0.996, "c": c, "v": 10 + (i % 7)})
    p = dict(P, min_confidence=0.0)
    runs = [Engine(p, {"BTCTMN": bars}, {}, ["BTCTMN"]).run(0, bars[-1]["t"]) for _ in range(2)]
    assert runs[0]["final_tmn"] == runs[1]["final_tmn"]
    assert all(t["exit_t"] <= bars[-1]["t"] for t in runs[0]["trades"])
    assert stats(runs[0], p["capital_tmn"])["trades"] == len(runs[0]["trades"])

def test_chop_filter_keeps_cash():
    from bot.multi.selector_core import classify
    f = {"atr_pct": .01, "atr_pct_avg": .01, "er": .05, "adx": 12, "bbw": .005, "htf_up": 0.0, "ema20": 1, "ema50": 1,
         "ema200": 1, "close": 1, "ema50_slope": 0, "er_long": 0}
    reg, prop, why = classify("BTCTMN", f, P)
    assert prop is None and "cash" in why

def test_usdt_wallet_roundtrip_costs():
    bars = [{"t": 3600 * i, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1} for i in range(300)]
    p = dict(P, usdt_alloc=0.5, warmup=10)
    r = Engine(p, {"BTCUSDT": bars}, {b["t"]: 1000.0 for b in bars}, ["BTCUSDT"]).run(0, bars[-1]["t"])
    assert r["final_tmn"] < p["capital_tmn"] and r["final_tmn"] > p["capital_tmn"] * 0.99   # only conversion costs

def test_decision_model_backends():
    import pytest
    from bot.multi.decision_model import DeterministicModel, TypeSafeJevModel, get_model
    f = {"ema20": 2, "ema50": 1, "close": 3, "ema200": 1, "ema50_slope": .01, "htf_up": 1.0, "er": .5, "adx": 30, "bbw": .03}
    d = DeterministicModel().decide("BTCTMN", f, {"strategy": "trend"}, rule_conf=.7)
    assert d.regime.value == "trend_up" and abs(sum(d.regime_probs.values()) - 1) < 1e-3 and d.take_trade_prob == .7
    assert DeterministicModel().decide("BTCTMN", f, None).take_trade_prob == 0
    with pytest.raises(NotImplementedError): TypeSafeJevModel().decide("X", f)
    assert get_model("deterministic").name == "deterministic"

def test_funding_plan_dry_run():
    from bot.multi.funding import plan_fund, plan_defund, execute
    class C:
        def market(self, s): return {"stepSize": 2, "tickSize": 0, "minNotional": 50000}
        def best_bid_ask(self, s): return 271000.0, 271010.0
        def place_order(self, *a, **k): raise AssertionError("must not place orders in dry run")
    p = plan_fund(C(), 5_000_000)
    assert p["ok"] and p["maker"]["type"] == "LIMIT" and p["maker"]["side"] == "BUY" and p["spend_tmn"] <= 4_900_000
    assert p["maker"]["client_id"].replace("_", "").isalnum()
    assert execute(C(), p, dry_run=True, log=lambda x: None)["status"] == "DRY_RUN"
    assert plan_defund(C(), 18.123)["maker"]["quantity"] == "18.12"

def test_liquidate_dry_run_never_orders():
    from bot.multi.liquidate import run
    class C:
        def balances(self): return {"TMN": {"value": "1000", "locked": "0"}, "USDT": {"value": "18.01579", "locked": "0"}}
        def markets(self): return {"USDTTMN": {"stepSize": 2, "tickSize": 0, "minNotional": 50000}}
        def best_bid_ask(self, s): return 272000.0, 272010.0
        def place_order(self, *a, **k): raise AssertionError("dry run must not place orders")
    r = run(C(), execute=False, log=lambda x: None)
    assert r["status"] == "DRY_RUN" and r["plans"][0]["payload"]["side"] == "SELL" and r["plans"][0]["payload"]["quantity"] == "18.01"

def test_live_xaut_gap_logic():
    from bot.multi import live_xaut as L
    class C:
        def markets(self): return {"XAUTUSDT": {"stepSize": 5, "tickSize": 2}}
        def balances(self): return {"USDT": {"value": "18", "locked": "0"}}
    r = L.Runner.__new__(L.Runner); r.st = {"mode": "base", "initial_done": True}
    a = {"base": 0.5}
    m = {"ok": True, "prem_bid": 0.0025, "prem_ask": 0.0027}
    assert r.n_strategy({"mkt": m, "alloc": a})["prop"]["mode"] == "sold"
    m = {"ok": True, "prem_bid": -0.0030, "prem_ask": -0.0025}
    p = r.n_strategy({"mkt": m, "alloc": a})["prop"]; assert p["mode"] == "bought" and abs(p["target_frac"] - 0.9) < 1e-9
    r.st["mode"] = "sold"; m = {"ok": True, "prem_bid": -0.0002, "prem_ask": 0.0}
    assert r.n_strategy({"mkt": m, "alloc": a})["prop"]["mode"] == "base"

def test_gold_closed_guard():
    import datetime as dt
    from bot.multi.live_xaut import gold_closed
    U = dt.timezone.utc
    assert gold_closed(dt.datetime(2026, 10, 24, 12, tzinfo=U))      # Saturday
    assert not gold_closed(dt.datetime(2026, 10, 23, 10, tzinfo=U))  # Friday morning
    assert not gold_closed(dt.datetime(2026, 10, 25, 23, tzinfo=U))  # Sunday after reopen
