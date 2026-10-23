from bot.multi.setup_trade import plan
def test_plan_risk_cap():
    p = plan(11.37, 18.0, 4026.9, 4010.0, 4032.0, 0.00001)
    assert p["ok"] and p["qty"] > 0 and p["cost"] < 11.37
    assert p["loss_pct_eq"] < 0.10 and p["net_at_tp"] < 0
def test_plan_rejects_oversize():
    assert not plan(100, 18.0, 4026.9, 3000.0, 4032.0, 0.00001)["ok"]

class FakeC:
    def __init__(s, bids): s.bids = bids; s.o = {}; s.log = []
    def best_bid_ask(s, sym): b = s.bids.pop(0) if len(s.bids) > 1 else s.bids[0]; s.cur = b; return b, b + 0.1
    def get_order(s, c): return s.o[c]
    def cancel_order(s, c): s.log.append(("cancel", c)); s.o[c]["active"] = False
    def place_order(s, sym, side, t, q, p, client_id=None): s.log.append(("place", side, q, p)); s.o[client_id] = {"active": True, "executedQty": "0", "price": p}
    def _auth_req(s, m, path, json=None): s.o[json["client_id"]] = {"active": True, "executedQty": "0", "price": json["price"]}; return {"result": {}}

def test_exits_cancel_rearm_and_tp(tmp_path, monkeypatch):
    import bot.multi.setup_trade as T
    monkeypatch.chdir(tmp_path); (tmp_path / "logs").mkdir(); monkeypatch.setattr(T.time, "sleep", lambda x: None)
    c = FakeC([4020, 4011, 4015, 4019, 4025])
    orig = c.get_order; n = {"i": 0}
    def go(cid):
        n["i"] += 1; o = orig(cid)
        if n["i"] > 12 and o is c.o.get(st_tp[0]): o.update(active=False, executedQty="0.0028")
        return o
    st_tp = [None]; c.get_order = go
    orig_place = c.place_order
    def pl(*a, client_id=None): orig_place(*a, client_id=client_id); st_tp[0] = client_id
    c.place_order = pl
    assert T.exits(c, 0.0028, 4026.89, 4010.0, 4032.0, 0.00001, 0.01, 0) == 0
    kinds = [l[0] for l in c.log]
    assert kinds.count("place") == 2 and "cancel" in kinds   # TP placed, cancelled at <=4012, re-armed at >=4018
    assert not (tmp_path / "logs/setup_lock.json").exists() and (tmp_path / "logs/setup_trade.DONE").exists()
