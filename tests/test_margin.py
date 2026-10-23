import pytest, os
from bot.multi.margin import MarginClient, LiveMarginDisabled, choose_coef, trip_pnl
from bot.multi.live_margin import decide

def test_open_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MARGIN_LIVE", raising=False)
    c = MarginClient(api_key="x", execute=True)
    with pytest.raises(LiveMarginDisabled): c.open_position()
    with pytest.raises(LiveMarginDisabled): c.close_position()
    with pytest.raises(AssertionError): c._post_public("/positions", {})

def test_coef_and_pnl():
    assert choose_coef(10) == 10 and choose_coef(50) == 2 and choose_coef(100) == 1
    assert trip_pnl("short", 100, 100, 100, 0) == pytest.approx(-0.515)
    assert trip_pnl("short", 100, 99, 100, 5 * 3600) == pytest.approx(1 - 0.515 - 0.015)
    assert trip_pnl("long", 100, 101, 100, 0) == pytest.approx(0.485)

def test_decide():
    assert decide(None, 0.007, 0.0072, th=0.006)[:2] == ("open", "short")
    assert decide(None, -0.008, -0.007, th=0.006)[:2] == ("open", "long")
    assert decide(None, 0.0045, 0.005, th=0.004)[0] == "hold"   # above th but below 0.515% fees
    assert decide(None, 0.007, 0.0072, th=0.006, closed=True)[0] == "skip"
    pos = {"side": "short", "entry": 1, "t0": 0}
    assert decide(pos, 0.0, 0.0003, now=10)[0] == "close"
    assert decide(pos, 0.005, 0.006, now=10)[0] == "hold"
    assert decide(pos, 0.005, 0.006, now=9 * 3600)[0] == "close"
