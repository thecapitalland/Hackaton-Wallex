"""LangGraph pipeline: data -> signal -> regime -> risk -> execution -> report."""
import json, os, time
from typing import TypedDict, Any
from langgraph.graph import StateGraph, END
from .signals import compute_features, technical_signal
from .regime import classify
from .risk import size_position, update_exit, daily_killed

class S(TypedDict, total=False):
    ts: int
    candles: dict
    prices: dict
    features: dict
    signals: dict
    regimes: dict
    orders: list
    exits: list
    decisions: list
    equity: float
    killed: bool

def build_graph(cfg, client, broker):
    def data(s: S):
        candles, prices = {}, {}
        for sym in cfg.symbols:
            c = client.candles(sym, cfg.timeframe, cfg.lookback_bars)
            if len(c) >= 60:
                candles[sym] = c
                prices[sym] = c[-1]["c"]
        return {"ts": int(time.time()), "candles": candles, "prices": prices, "decisions": [], "orders": [], "exits": []}

    def signal(s: S):
        feats, sigs = {}, {}
        for sym, c in s["candles"].items():
            feats[sym] = compute_features(c[:-1])  # closed bars only; last bar is still forming
            feats[sym]["close"] = c[-1]["c"]
            sigs[sym] = technical_signal(feats[sym])
        return {"features": feats, "signals": sigs}

    def regime(s: S):
        out = {}
        for sym in s["signals"]:
            out[sym] = classify(sym, cfg.timeframe, s["features"][sym], s["signals"][sym], s["candles"][sym], cfg.use_llm)
        return {"regimes": out}

    def risk(s: S):
        st = broker.state
        day = time.strftime("%Y-%m-%d", time.gmtime())
        eq = broker.equity(s["prices"])
        if st.get("day") != day:
            st["day"], st["day_start_equity"], st["killed"] = day, eq, False
        killed = st.get("killed") or daily_killed(cfg, st["day_start_equity"], eq)
        st["killed"] = killed
        orders, exits, dec = [], [], []
        # exits first (stops / tp / trailing) on the latest bar
        for sym, pos in list(st["positions"].items()):
            if sym not in s["candles"]: continue
            b = s["candles"][sym][-1]
            px, why = update_exit(cfg, pos, b["h"], b["l"], s["features"][sym]["atr"])
            if killed and not px:
                px, why = b["c"], "kill_switch"
            if px:
                exits.append({"symbol": sym, "price": px, "reason": why})
        n_open = len(st["positions"]) - len(exits)
        for sym, sig in s["signals"].items():
            rg = s["regimes"][sym]
            d = {"ts": s["ts"], "symbol": sym, "price": s["prices"][sym], "signal": sig, "regime": rg}
            if sym in st["positions"]:
                d["decision"] = "HOLD_POSITION"
            elif sig["action"] != "BUY":
                d["decision"] = "NO_TRADE"
            elif not rg["approve"]:
                d["decision"] = "VETOED"
            else:
                ok, qty, stop, tp, why = size_position(cfg, eq, st["cash"], s["prices"][sym], s["features"][sym]["atr"],
                                                       n_open, killed, client.market(sym), sig.get("tp_mult", 1.0))
                d["risk"] = why
                if ok:
                    orders.append({"symbol": sym, "qty": qty, "stop": stop, "tp": tp, "strategy": sig["strategy"]})
                    n_open += 1
                    d["decision"] = "ENTER"
                else:
                    d["decision"] = "RISK_BLOCKED"
            dec.append(d)
        return {"orders": orders, "exits": exits, "decisions": dec, "equity": eq, "killed": killed}

    def execute(s: S):
        dec = s["decisions"]
        for ex in s["exits"]:
            px = broker.limit_price(ex["symbol"], "SELL", ex["price"]) if broker.live else ex["price"]
            r = broker.sell(ex["symbol"], px, ex["reason"])
            dec.append({"ts": s["ts"], "symbol": ex["symbol"], "decision": "EXIT", "reason": ex["reason"], "fill": r})
        for o in s["orders"]:
            px = broker.limit_price(o["symbol"], "BUY", s["prices"][o["symbol"]])
            r = broker.buy(o["symbol"], o["qty"], px, o["stop"], o["tp"], {"strategy": o["strategy"]})
            for d in dec:
                if d.get("symbol") == o["symbol"] and d.get("decision") == "ENTER":
                    d["fill"] = r; d["order"] = {**o, "limit_price": px}
        broker.save()
        return {"decisions": dec}

    def report(s: S):
        os.makedirs(os.path.dirname(cfg.log_path) or ".", exist_ok=True)
        eq = broker.equity(s["prices"])
        with open(cfg.log_path, "a") as f:
            for d in s["decisions"]:
                f.write(json.dumps({**d, "mode": cfg.mode}, default=str) + "\n")
            f.write(json.dumps({"ts": s["ts"], "type": "equity", "equity": eq, "cash": broker.state["cash"],
                                "killed": s["killed"], "mode": cfg.mode}) + "\n")
        return {"equity": eq}

    g = StateGraph(S)
    for name, fn in [("data", data), ("signal", signal), ("regime", regime), ("risk", risk),
                     ("execute", execute), ("report", report)]:
        g.add_node(name, fn)
    g.set_entry_point("data")
    for a, b in [("data", "signal"), ("signal", "regime"), ("regime", "risk"), ("risk", "execute"),
                 ("execute", "report"), ("report", END)]:
        g.add_edge(a, b)
    return g.compile()
