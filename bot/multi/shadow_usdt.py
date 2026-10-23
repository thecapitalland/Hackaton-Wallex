"""USDT-base shadow portfolio (virtual 18 USDT, PnL in USDT) on Wallex USDT pairs. NO ORDERS.
LangGraph: data -> manage -> analyst -> regime(AI, DecisionModel) -> allocator(AI) -> strategy -> risk -> execution.
Signals: Binance-mirror 15m closed bars. Virtual fills at REAL Wallex best bid/ask with taker fee 0.13%,
target exits as maker 0.10% when Wallex bid >= target. Strategy: C_meanrev_extreme_15m (only candidate positive OOS;
2 test trades -> weak evidence) enabled on ETHUSDT; BTCUSDT/XAUTUSDT logged as watch-only."""
import json, os, time, requests
from typing import TypedDict
import numpy as np
from langgraph.graph import StateGraph, END
from ..wallex import WallexClient
from .decision_model import get_model
from .allocator import Allocator

BIN = "https://data-api.binance.vision/api/v3/klines"
MARKETS = {"ETHUSDT": True, "BTCUSDT": False, "XAUTUSDT": False}      # True = strategy enabled
P = {"rsi": 20, "bb": 2.5, "slope": 0.005, "k": 4, "stop_atr": 3, "tgt_atr": 2.5, "min_tgt": 0.005, "hold_bars": 96,
     "risk": 0.015, "max_pos": 3, "max_frac": 0.6, "daily_cap": 0.06, "dd_cap": 0.12, "min_notional_usdt": 1.0,
     "fee_t": 0.0013, "fee_m": 0.001, "gold_n": 20, "gold_frac": 0.5}
STATE = "logs/shadow_usdt_state.json"; LOG = "logs/decisions.jsonl"

def log(rec):
    os.makedirs("logs", exist_ok=True)
    with open(LOG, "a") as f: f.write(json.dumps({"ts": int(time.time()), "time_tehran": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                  "portfolio": "usdt_shadow", **rec}, default=str, ensure_ascii=False) + "\n")

def ema(x, n):
    a = 2 / (n + 1); out = np.empty(len(x)); e = x[0]
    for i, v in enumerate(x): e = a * v + (1 - a) * e; out[i] = e
    return out

def feats(k):
    c = np.array([float(x[4]) for x in k]); h = np.array([float(x[2]) for x in k]); l = np.array([float(x[3]) for x in k])
    pc = np.roll(c, 1); pc[0] = c[0]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); atr = ema(tr, 27)
    d = np.diff(c, prepend=c[0]); up = ema(np.clip(d, 0, None), 27); dn = ema(np.clip(-d, 0, None), 27); rsi = 100 - 100 / (1 + up / np.maximum(dn, 1e-12))
    m, sd = c[-20:].mean(), c[-20:].std(); e200 = ema(c, 200); e50 = ema(c, 50)
    path = np.abs(np.diff(c[-21:])).sum(); er = abs(c[-1] - c[-21]) / path if path else 0
    ex = ema(c, 50 * P["k"])
    return {"close": c[-1], "atr": atr[-1], "atr_pct": atr[-1] / c[-1], "rsi": rsi[-1], "bb_lo": m - P["bb"] * sd, "sma20": m,
            "ema20": ema(c, 20)[-1], "ema50": e50[-1], "ema200": e200[-1], "ema50_slope": e50[-1] / e50[-11] - 1,
            "er": er, "adx": 20.0, "bbw": 4 * sd / m, "htf_up": float(ema(c, 80)[-1] > e200[-1]),
            "htf_slope_ok": float(ex[-1] > ex[-1 - 10 * P["k"]] * (1 - P["slope"]))}

class S(TypedDict, total=False):
    st: dict; kl: dict; book: dict; f: dict; reg: dict; alloc: dict; props: list; appr: list

def build(client=None):
    c = client or WallexClient(); model = get_model("deterministic"); alloc = Allocator({})
    def load():
        if os.path.exists(STATE): return json.load(open(STATE))
        return {"usdt": 18.016, "pos": {}, "peak": 18.016, "day": None, "day_start": 18.016, "halted": None, "trades": 0}
    def data(s):
        kl, book = {}, {}
        for m in MARKETS:
            try:
                k = requests.get(BIN, params={"symbol": m, "interval": "15m", "limit": 600}, timeout=10).json()[:-1]  # closed bars
                b, a = c.best_bid_ask(m); kl[m] = k; book[m] = {"bid": b, "ask": a}
            except Exception as e: log({"event": "data_error", "market": m, "err": repr(e)[:200]})
        return {"st": load(), "kl": kl, "book": book}
    def manage(s):
        st = s["st"]
        for m, q in list(st["pos"].items()):
            bk = s["book"].get(m)
            if not bk: continue
            q["bars"] = q.get("bars", 0) + 1 if s["kl"].get(m) and int(s["kl"][m][-1][0]) != q.get("last_bar") else q.get("bars", 0)
            if s["kl"].get(m): q["last_bar"] = int(s["kl"][m][-1][0])
            why = "stop" if bk["bid"] <= q["stop"] else "target" if bk["bid"] >= q["tgt"] else "time" if q["bars"] >= P["hold_bars"] else None
            if why:
                fee = P["fee_m"] if why == "target" else P["fee_t"]; px = q["tgt"] if why == "target" else bk["bid"]
                proceeds = q["qty"] * px * (1 - fee); st["usdt"] += proceeds; pnl = proceeds - q["cost"]; del st["pos"][m]
                log({"event": "shadow_exit", "market": m, "price": px, "reason": why, "pnl_usdt": round(pnl, 5),
                     "ret_%": round(pnl / q["cost"] * 100, 3), "entry_decision": q["decision"], "note": "NO ORDER PLACED"})
        eq = st["usdt"] + sum(q["qty"] * s["book"].get(m, {"bid": q["entry"]})["bid"] for m, q in st["pos"].items())
        gq = st.get("gold"); eq += gq["qty"] * s["book"].get("XAUTUSDT", {"bid": gq["entry"]})["bid"] if gq else 0
        d = time.strftime("%Y-%m-%d")
        if st["day"] != d: st["day"], st["day_start"] = d, eq; st["halted"] = None if st["halted"] == "daily_cap" else st["halted"]
        st["peak"] = max(st["peak"], eq); st["equity"] = eq
        if (st["day_start"] - eq) / st["day_start"] >= P["daily_cap"]: st["halted"] = "daily_cap"
        if (st["peak"] - eq) / st["peak"] >= P["dd_cap"]: st["halted"] = "dd_cap"
        return {"st": st}
    def gold_tsmom(s):
        """XAUTUSDT daily time-series momentum (research/gold_wf.py: only one of 12 gold ideas robust after Wallex costs on
        2021-2026 XAUUSD walk-forward; n=20 chosen). Long while 20d return of last CLOSED daily bar > 0, else flat. Taker fills at Wallex book."""
        st = s["st"]; bk = s["book"].get("XAUTUSDT")
        try: k = requests.get(BIN, params={"symbol": "XAUTUSDT", "interval": "1d", "limit": 30}, timeout=10).json()[:-1]
        except Exception as e: log({"event": "data_error", "market": "XAUTUSDT", "node": "gold_tsmom", "err": repr(e)[:200]}); return {"st": st}
        if not bk or len(k) < P["gold_n"] + 1: return {"st": st}
        c = [float(x[4]) for x in k]; mom = c[-1] / c[-1 - P["gold_n"]] - 1; want = mom > 0 and not st["halted"]; g = st.get("gold"); day = int(k[-1][0])
        if st.get("gold_last_day") == day and bool(g) == want: return {"st": st}
        st["gold_last_day"] = day
        reason = f"TSMOM n={P['gold_n']}: 20d return {mom:+.2%} of last closed daily bar -> {'LONG' if want else 'FLAT'}"
        if want and not g:
            notional = min(st["equity"] * P["gold_frac"], st["usdt"] / (1 + P["fee_t"]))
            if notional >= P["min_notional_usdt"]:
                px = bk["ask"]; cost = notional * (1 + P["fee_t"]); st["usdt"] -= cost; st["trades"] += 1
                st["gold"] = {"qty": notional / px, "entry": px, "cost": cost}
                log({"event": "shadow_entry", "market": "XAUTUSDT", "strategy": "gold_tsmom", "price_ask": px, "notional_usdt": round(notional, 4), "reason": reason, "note": "NO ORDER PLACED"})
        elif g and not want:
            px = bk["bid"]; proceeds = g["qty"] * px * (1 - P["fee_t"]); st["usdt"] += proceeds; pnl = proceeds - g["cost"]; st["gold"] = None
            log({"event": "shadow_exit", "market": "XAUTUSDT", "strategy": "gold_tsmom", "price": px, "pnl_usdt": round(pnl, 5), "ret_%": round(pnl / g["cost"] * 100, 3), "reason": reason, "note": "NO ORDER PLACED"})
        else: log({"event": "decision", "market": "XAUTUSDT", "strategy": "gold_tsmom", "in_position": bool(g), "reason": reason})
        return {"st": st}
    def analyst(s): return {"f": {m: feats(k) for m, k in s["kl"].items() if len(k) > 300}}
    def regime(s):
        out = {}
        for m, f in s["f"].items():
            sig = MARKETS[m] and f["rsi"] < P["rsi"] and f["close"] < f["bb_lo"] and f["htf_slope_ok"] > 0
            d = model.decide(m, f, {"strategy": "meanrev_extreme"} if sig else None, rule_conf=0.6 if sig else None)
            out[m] = {"decision": d.to_json(), "signal": bool(sig)}
        return {"reg": out}
    def allocator(s):
        decs = [{"market": m, "strategy": "meanrev_extreme" if r["signal"] else "none", "confidence": r["decision"]["take_trade_prob"]} for m, r in s["reg"].items()]
        a = alloc.decide(None, None, decs); a = {**a, "usdt_share": 1.0, "gold_share": 0.0, "tmn_share": 0.0, "reason": "USDT base; " + a["reason"]}
        return {"alloc": a}
    def strategy(s):
        props = []
        for m in s["alloc"]["ranked"] + [m for m in s["reg"] if m not in s["alloc"]["ranked"]]:
            r = s["reg"][m]; f = s["f"][m]; dec = r["decision"]
            if not r["signal"]:
                log({"event": "decision", "market": m, "strategy": "none", "regime": dec["regime"], "regime_probs": dec["regime_probs"],
                     "enabled": MARKETS[m], "reason": f"no setup (RSI {f['rsi']:.0f}, close vs BB-low {f['close']/f['bb_lo']-1:+.2%})"}); continue
            ap = f["atr_pct"]; props.append({"market": m, "stop_pct": P["stop_atr"] * ap, "tgt_pct": max(P["tgt_atr"] * ap, P["min_tgt"]), "dec": dec,
                          "reason": f"mean-reversion extreme: RSI {f['rsi']:.0f}<{P['rsi']}, close below BB(20,{P['bb']}), HTF slope ok"})
        return {"props": props}
    def risk(s):
        st, appr = s["st"], []
        for pr in s["props"]:
            m = pr["market"]; eq = st["equity"]; why = None
            if st["halted"]: why = f"halted:{st['halted']}"
            elif m in st["pos"]: why = "already in position"
            elif len(st["pos"]) >= P["max_pos"]: why = "max positions"
            notional = min(eq * P["risk"] / pr["stop_pct"], eq * P["max_frac"], st["usdt"] / (1 + P["fee_t"]))
            if not why and notional < P["min_notional_usdt"]: why = "below min notional"
            log({"event": "decision", "market": m, "strategy": "meanrev_extreme", "regime_probs": pr["dec"]["regime_probs"],
                 "take_trade_prob": pr["dec"]["take_trade_prob"], "approved": why is None, "risk": why or f"notional {notional:.3f} USDT",
                 "reason": pr["reason"]})
            if not why: appr.append({**pr, "notional": notional})
        return {"appr": appr}
    def execution(s):
        st = s["st"]
        for a in s["appr"]:
            m = a["market"]; px = s["book"][m]["ask"]; qty = a["notional"] / px; cost = a["notional"] * (1 + P["fee_t"])
            st["usdt"] -= cost; st["trades"] += 1
            st["pos"][m] = {"qty": qty, "entry": px, "cost": cost, "stop": px * (1 - a["stop_pct"]), "tgt": px * (1 + a["tgt_pct"]),
                            "bars": 0, "decision": {"regime_probs": a["dec"]["regime_probs"], "reason": a["reason"]}}
            log({"event": "shadow_entry", "market": m, "price_ask": px, "qty": qty, "notional_usdt": round(a["notional"], 4),
                 "stop": st["pos"][m]["stop"], "target": st["pos"][m]["tgt"], "note": "NO ORDER PLACED"})
        os.makedirs("logs", exist_ok=True); json.dump(st, open(STATE, "w"), indent=1); return {"st": st}
    g = StateGraph(S); nodes = [("data", data), ("manage", manage), ("gold_tsmom", gold_tsmom), ("analyst", analyst), ("regime", regime), ("allocator", allocator),
                               ("strategy", strategy), ("risk", risk), ("execution", execution)]
    for n, f in nodes: g.add_node(n, f)
    g.set_entry_point("data")
    for (a, _), (b, _) in zip(nodes, nodes[1:]): g.add_edge(a, b)
    g.add_edge("execution", END); return g.compile()
