import types
from bot.config import Config
from bot.risk import round_step, size_position, daily_killed, update_exit
from bot.indicators import ema, rsi
import bot.regime as R

def test_round_step_decimals():
    assert round_step(0.123456789, 8) == 0.12345678
    assert round_step(1234.7, 0) == 1234
    assert round_step(83500.121, 2, up=True) == 83500.13

def test_sizing_and_caps():
    cfg = Config()
    ok, qty, stop, tp, _ = size_position(cfg, 1000, 1000, 100.0, 1.0, 0, False, {"stepSize": 4, "minQty": 0.0001, "minNotional": 1})
    assert ok and stop < 100 < tp and qty * 100 <= 600 + 1e-6
    assert not size_position(cfg, 1000, 1000, 100.0, 1.0, cfg.max_open_positions, False)[0]
    assert not size_position(cfg, 1000, 1000, 100.0, 1.0, 0, True)[0]
    assert not size_position(cfg, 1000, 1000, 100.0, 1.0, 0, False, {"stepSize": 0, "minQty": 1, "minNotional": 50000})[0]

def test_kill_switch():
    cfg = Config()
    assert daily_killed(cfg, 1000, 840) and not daily_killed(cfg, 1000, 900)

def test_stop_hit():
    cfg = Config(); pos = {"entry": 100, "stop": 95, "tp": 110, "peak": 100}
    assert update_exit(cfg, pos, 101, 94, 1)[0] == 95

def test_indicators():
    assert abs(ema([1] * 50, 10)[-1] - 1) < 1e-9
    assert rsi(list(range(1, 40)))[-1] == 100.0

def test_llm_can_only_veto(monkeypatch):
    class Fake:
        def __init__(self, approve): self.a = approve
        @property
        def chat(self):
            a = self.a
            msg = types.SimpleNamespace(content='{"regime":"trend_up","approve":%s,"reason":"x"}' % str(a).lower())
            return types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **k: types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])))
    f = {"atr_pct": 0.01, "atr_pct_avg": 0.01, "ema20": 90, "ema50": 100, "ema20_slope": -0.01, "close": 1}
    sig = {"action": "BUY", "strategy": "trend_follow"}
    monkeypatch.setattr(R, "_llm", lambda: Fake(True))
    assert R.classify("X", "240", f, sig, [{"c": 1}])["approve"] is False   # rules block trend_follow in trend_down
    f2 = {**f, "ema20": 110, "ema20_slope": 0.01}
    assert R.classify("X", "240", f2, sig, [{"c": 1}])["approve"] is True
    monkeypatch.setattr(R, "_llm", lambda: Fake(False))
    assert R.classify("X", "240", f2, sig, [{"c": 1}])["approve"] is False  # LLM veto
