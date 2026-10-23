"""Execution: PaperBroker (simulated fills on live book) and LiveBroker (real Wallex LIMIT orders)."""
import json, os, time, uuid
from .risk import round_step

class PaperBroker:
    live = False
    def __init__(self, cfg, client):
        self.cfg, self.client = cfg, client
        self.state = self._load()

    def _load(self):
        if os.path.exists(self.cfg.state_path):
            return json.load(open(self.cfg.state_path))
        return {"cash": self.cfg.start_equity_usdt, "positions": {}, "closed": [], "day": "", "day_start_equity": 0,
                "start_equity": self.cfg.start_equity_usdt}

    def save(self):
        os.makedirs(os.path.dirname(self.cfg.state_path) or ".", exist_ok=True)
        json.dump(self.state, open(self.cfg.state_path, "w"), indent=1)

    def limit_price(self, symbol, side, ref):
        m = self.client.market(symbol)
        try:
            bid, ask = self.client.best_bid_ask(symbol)
        except Exception:
            bid = ask = None
        off = self.cfg.limit_offset_bps / 1e4
        if side == "BUY":
            p = (ask or ref) * (1 + off)   # marketable limit: crosses spread a little, capped slippage
        else:
            p = (bid or ref) * (1 - off)
        return round_step(p, m.get("tickSize") or 0, up=(side == "BUY"))

    def buy(self, symbol, qty, price, stop, tp, meta):
        cost = qty * price * (1 + self.cfg.fee_rate)
        self.state["cash"] -= cost
        self.state["positions"][symbol] = {"qty": qty, "entry": price, "stop": stop, "tp": tp, "peak": price,
                                           "opened": int(time.time()), **meta}
        return {"status": "FILLED(sim)", "price": price, "qty": qty}

    def sell(self, symbol, price, reason):
        pos = self.state["positions"].pop(symbol)
        proceeds = pos["qty"] * price * (1 - self.cfg.fee_rate)
        self.state["cash"] += proceeds
        pnl = proceeds - pos["qty"] * pos["entry"] * (1 + self.cfg.fee_rate)
        self.state["closed"].append({"symbol": symbol, "entry": pos["entry"], "exit": price, "qty": pos["qty"],
                                     "pnl": pnl, "reason": reason, "closed": int(time.time())})
        return {"status": "FILLED(sim)", "price": price, "pnl": pnl}

    def equity(self, prices):
        return self.state["cash"] + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in self.state["positions"].items())


class LiveBroker(PaperBroker):
    """Real orders. Positions/stops are managed client-side (bot checks stops every loop) and
    entries/exits use marketable LIMIT orders. Unfilled orders are cancelled after `fill_wait` seconds."""
    live = True
    fill_wait = 20

    def _place_and_wait(self, symbol, side, qty, price):
        m = self.client.market(symbol)
        qty = round_step(qty, m.get("stepSize") or 0)
        cid = f"agent-{uuid.uuid4().hex[:16]}"
        self.client.place_order(symbol, side, "LIMIT", qty, price, client_id=cid)
        deadline = time.time() + self.fill_wait
        o = {}
        while time.time() < deadline:
            time.sleep(2)
            o = self.client.get_order(cid)
            if str(o.get("status", "")).upper() in ("FILLED", "DONE"):
                break
        executed = float(o.get("executedQty") or 0)
        if str(o.get("status", "")).upper() not in ("FILLED", "DONE"):
            try: self.client.cancel_order(cid)
            except Exception: pass
        return cid, executed, o

    def buy(self, symbol, qty, price, stop, tp, meta):
        cid, filled, o = self._place_and_wait(symbol, "BUY", qty, price)
        if filled <= 0:
            return {"status": "NOT_FILLED", "client_id": cid}
        r = super().buy(symbol, filled, price, stop, tp, {**meta, "client_id": cid})
        r["status"] = "FILLED"
        return r

    def sell(self, symbol, price, reason):
        pos = self.state["positions"][symbol]
        cid, filled, o = self._place_and_wait(symbol, "SELL", pos["qty"], price)
        if filled <= 0:
            return {"status": "NOT_FILLED", "client_id": cid}
        if filled < pos["qty"] * 0.999:
            pos["qty"] -= filled
            return {"status": "PARTIAL", "filled": filled}
        r = super().sell(symbol, price, reason)
        r["status"] = "FILLED"
        return r

    def sync_cash(self):
        try:
            b = self.client.balances()
            q = b.get(self.cfg.quote) or {}
            self.state["cash"] = float(q.get("value", 0)) - float(q.get("locked", 0))
        except Exception:
            pass
