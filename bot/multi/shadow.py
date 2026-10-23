"""Shadow mode: live Wallex data -> scanner -> AI selector -> risk manager -> decision log. NEVER places orders.
Virtual portfolio state in logs/shadow_state.json, decisions in logs/decisions.jsonl."""
import json, os, time
from typing import TypedDict
from langgraph.graph import StateGraph, END
from ..wallex import WallexClient
from .features import precompute, snapshot
from .strategies import manage_exit
from .selector import LLMSelector, RuleSelector
from .allocator import Allocator, LLMAllocator
from .risk import RiskManager
from .engine import tehran_day

class S(TypedDict, total=False):
    ts: int; candles: dict; spreads: dict; scan: dict; decisions: list; actions: list; state: dict; allocation: dict; approved: list; feats: dict

def log(rec, path="logs/decisions.jsonl"):
    os.makedirs("logs", exist_ok=True)
    with open(path, "a") as f: f.write(json.dumps({"ts": int(time.time()), **rec}, default=str) + "\n")

def build(p, markets, client=None, state_path="logs/shadow_state.json"):
    c = client or WallexClient(); use_llm = p.get("use_llm", False)
    sel = LLMSelector(p) if use_llm else RuleSelector(p); alloc = (LLMAllocator if use_llm else Allocator)(p); risk = RiskManager(p)
    def load_state():
        if os.path.exists(state_path): return json.load(open(state_path))
        return {"cash": p["capital_tmn"], "pos": {}, "peak": p["capital_tmn"], "day": None, "day_start": p["capital_tmn"], "halted": None}
    def rate():
        b, a = c.best_bid_ask("USDTTMN"); return (b + a) / 2
    def cost(m):  # taker fee from data/fees.json (Wallex /v1/account/fee) + USDTTMN conversion for USDT pairs
        f = (p.get("fees", {}).get(m) or p["fee_default"]["USDT" if m.endswith("USDT") else "TMN"])["taker"]
        conv = 0 if load_state().get("usdt_funded") else (p.get("fees", {}).get("USDTTMN") or {"taker": 0.003})["taker"]
        return f + (conv if m.endswith("USDT") else 0)

    def data(s):
        candles, spreads = {}, {}
        for m in markets:
            try:
                cs = c.candles(m, p["timeframe"], 400)[:-1]      # drop forming bar
                b, a = c.best_bid_ask(m)
                if len(cs) > 220 and b and a: candles[m] = cs; spreads[m] = (a - b) / ((a + b) / 2)
            except Exception as e: log({"event": "data_error", "market": m, "err": repr(e)})
        return {"ts": int(time.time()), "candles": candles, "spreads": spreads, "state": load_state()}

    def manage(s):
        st, acts, r = s["state"], [], rate()
        for m, q in list(st["pos"].items()):
            cs = s["candles"].get(m)
            if not cs: continue
            f = snapshot(cs, precompute(cs), len(cs) - 1, p["donchian"])
            px, why = manage_exit(q, cs[-1], f, p)
            if px is None and why: px = cs[-1]["c"]
            if px is not None:
                proceeds = q["qty"] * px * (r if m.endswith("USDT") else 1) * (1 - cost(m)); st["cash"] += proceeds
                st["pos"].pop(m); rec = {"event": "shadow_exit", "market": m, "price": px, "reason": why,
                                         "pnl_tmn": round(proceeds - q["cost_tmn"]), "entry_decision": q["decision"]}
                log(rec); acts.append(rec)
        eq = st["cash"] + sum(q["qty"] * s["candles"].get(m, [{"c": q["entry"]}])[-1]["c"] * (r if m.endswith("USDT") else 1)
                              for m, q in st["pos"].items())
        d = tehran_day(s["ts"])
        if st["day"] != d: st["day"], st["day_start"] = d, eq; st["halted"] = None if st["halted"] == "daily_loss_cap" else st["halted"]
        st["peak"] = max(st["peak"], eq); st["equity"] = eq; st["rate"] = r
        h = risk.check_portfolio(eq, st["day_start"], st["peak"])
        if h and not st["halted"]: st["halted"] = h; log({"event": "halt", "reason": h, "equity": eq})
        return {"state": st, "actions": acts}

    def analyst(s):   # market analyst node: features for every market (closed bars only)
        feats = {m: snapshot(cs, precompute(cs), len(cs) - 1, p["donchian"], s["spreads"][m]) for m, cs in s["candles"].items()}
        scan = {m: f for m, f in feats.items() if m not in s["state"]["pos"] and s["spreads"][m] * 1e4 <= p["max_spread_bps"]}
        return {"feats": feats, "scan": scan}

    def regime(s):    # regime/strategy node (AI): structured JSON per market
        return {"decisions": sel.decide(s["scan"]) if s["scan"] else []}

    def allocator(s): # allocator node (AI): TMN/USDT/gold split + ranking
        a = alloc.decide(s["feats"].get("USDTTMN"), s["feats"].get("XAUTTMN"), s["decisions"])
        log({"event": "allocation", **a})
        order = {m: i for i, m in enumerate(a["ranked"])}
        dec = sorted(s["decisions"], key=lambda d: order.get(d["market"], 99))
        return {"allocation": a, "decisions": dec}

    def risk_node(s): # deterministic risk manager: can veto anything
        st, appr = s["state"], []
        held = dict(st["pos"])
        for d in s["decisions"]:
            dec = {k: v for k, v in d.items() if not k.startswith("_")}
            if d["strategy"] == "none":
                log({"event": "decision", **dec, "approved": False, "risk": "no setup"}); continue
            f = s["scan"][d["market"]]; prop = d["_prop"]
            notional, why = risk.check_entry(d, f["close"], prop["stop"], st["equity"], st["cash"], held, st["halted"])
            log({"event": "decision", **dec, "features": {k: round(v, 6) for k, v in f.items()}, "approved": notional > 0, "risk": why})
            if notional > 0: appr.append((d, notional)); held[d["market"]] = 1
        return {"approved": appr}

    def funding(s):   # USDT funding node: shadow = virtual one-time TMN->USDT conversion before first USDT-pair trade
        st = s["state"]
        if st.get("usdt_funded") or not any(d["market"].endswith("USDT") for d, _ in s["approved"]): return {}
        fee = (p.get("fees", {}).get("USDTTMN") or {"maker": 0.0025})["maker"]
        amt = max(0.0, st["cash"] - p.get("tmn_buffer", 100_000)); st["cash"] -= amt * fee; st["usdt_funded"] = True
        log({"event": "decision", "node": "usdt_funding", "market": "USDTTMN", "action": "BUY_USDT_VIRTUAL",
             "amount_tmn": round(amt), "fee_tmn": round(amt * fee), "reason": "fund USDT base for USDT-pair strategy; "
             "lower fees 0.10-0.13% vs 0.25-0.30% on TMN pairs (shadow: simulated, no order)"})
        return {"state": st}

    def execution(s): # shadow execution: virtual fills + decision log, NO ORDERS
        st, acts, r = s["state"], list(s["actions"]), s["state"]["rate"]
        for d, notional in s["approved"]:
            m = d["market"]; f = s["scan"][m]; prop = d["_prop"]; px = f["close"]; rr = r if m.endswith("USDT") else 1
            dec = {k: v for k, v in d.items() if not k.startswith("_")}
            st["cash"] -= notional * (1 + cost(m))
            st["pos"][m] = {"qty": notional / rr / px, "entry": px, "stop": prop["stop"], "peak": px, "bars": 0,
                            "strategy": prop["strategy"], "cost_tmn": notional * (1 + cost(m)), "decision": dec, "t": s["ts"]}
            rec = {"event": "shadow_entry", "market": m, "price": px, "notional_tmn": round(notional), "note": "NO ORDER PLACED"}
            log(rec); acts.append(rec)
        st["allocation"] = s.get("allocation")
        os.makedirs("logs", exist_ok=True); json.dump(st, open(state_path, "w"), indent=1, default=str)
        return {"state": st, "actions": acts}

    g = StateGraph(S)
    nodes = (("data", data), ("manage", manage), ("analyst", analyst), ("regime", regime), ("allocator", allocator),
             ("risk", risk_node), ("funding", funding), ("execution", execution))
    for n, fn in nodes: g.add_node(n, fn)
    g.set_entry_point("data")
    for (a, _), (b, _) in zip(nodes, nodes[1:]): g.add_edge(a, b)
    g.add_edge("execution", END)
    return g.compile()
