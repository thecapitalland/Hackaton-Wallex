"""XAUTUSDT two-sided inventory GAP strategy (final challenge strategy). DRY-RUN by default; real orders only with
--execute AND env LIVE=1. LangGraph pipeline per cycle (~15s):
  data -> regime(AI) -> allocator -> strategy(gap) -> risk(veto) -> execution -> log
- Inventory: hold base ~50% XAUT / 50% USDT. Base tilt: daily 20d momentum (+/-0.1) and AI regime (+/-0.05).
- Gap: premium = Wallex executable price / global XAUT (Binance mirror bookTicker mid) - 1.
  bid-premium > TH  -> SELL down to base-0.4 (Wallex rich);  ask-premium < -TH -> BUY up to base+0.4 (Wallex cheap);
  unwind back to base when executable premium crosses 0.  TH default 0.20% (overnight replay, research/replay_gap.py).
- Orders: marketable LIMIT at best bid/ask with unique client_id; unfilled remainder cancelled after ~8s. PAXG never traded.
- Risk veto (deterministic): XAUT frac in [0,0.95], daily DD 3% / total DD 5% -> halt (only liquidation allowed),
  min order 1 USDT, <=1 order per 10s, stale data (>30s) or |premium|>2% -> skip.
- At 23:00 Asia/Tehran: sell all XAUT->USDT, then USDT->TMN via liquidate.run, verify balances, write DONE flag, exit."""
import json, os, time, uuid, datetime as dt, requests
from typing import TypedDict
from zoneinfo import ZoneInfo
from langgraph.graph import StateGraph, END
from ..wallex import WallexClient
from ..risk import round_step
from .decision_model import get_model
from .shadow_usdt import feats
from . import liquidate as LQ

TZ = ZoneInfo("Asia/Tehran"); SYM = os.getenv("GAP_SYMBOL", "XAUTUSDT"); ASSET = SYM[:-4]; TAGF = "" if SYM == "XAUTUSDT" else "_" + SYM; BIN = "https://data-api.binance.vision/api/v3"
P = {"th": float(os.getenv("GAP_TH", "0.002")), "swing": 0.4, "mom_n": 20, "mom_tilt": 0.1, "ai_tilt": 0.05, "max_frac": 0.95,
     "daily_dd": 0.03, "total_dd": 0.05, "min_order": 1.0, "min_rebal_frac": 0.05, "order_gap_s": 10, "stale_s": 30,
     "max_abs_prem": 0.02, "fee_t": 0.0013, "ai_every_s": 1800, "liq_hhmm": os.getenv("LIQ_AT", "23:00"), "ref_stale_s": 300}
LOG = f"logs/live_xaut{TAGF}.jsonl"; DONE = f"logs/live_xaut{TAGF}.DONE"

def gold_closed(t=None):
    """Global spot gold (XAUUSD) is closed Fri 21:00 UTC -> Sun 22:00 UTC (+ daily 21:00-22:00 UTC break)."""
    t = t or dt.datetime.now(dt.timezone.utc); wd, h = t.weekday(), t.hour
    return (wd == 4 and h >= 21) or wd == 5 or (wd == 6 and h < 22) or h == 21

def now_teh(): return dt.datetime.now(TZ)
def log(rec):
    os.makedirs("logs", exist_ok=True)
    rec = {"ts": int(time.time()), "time_tehran": now_teh().strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with open(LOG, "a") as f: f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    print(json.dumps(rec, default=str, ensure_ascii=False), flush=True)
def cid(tag): return f"XG_{tag}_{int(time.time())}_{uuid.uuid4().hex[:6]}".upper()

class S(TypedDict, total=False):
    st: dict; mkt: dict; reg: dict; alloc: dict; prop: dict; appr: dict

class Runner:
    def __init__(self, execute=False, client=None):
        self.live = bool(execute) and os.getenv("LIVE") == "1" and SYM == "XAUTUSDT";  # BTC/ETH: shadow/dry-run only (replay negative)
        self.g_last = (None, time.time()); self.c = client or WallexClient()
        self.model = get_model(); self.det = get_model("deterministic")
        self.state_path = f"logs/live_xaut{TAGF}_state_{'live' if self.live else 'dry'}.json"
        self.mk = self.c.markets()[SYM]; self.last_order = 0; self.ai_cache = None; self.mom_cache = (0, None)
        self.st = self._load()

    # ---------- balances (real in live; virtual copy of real balances in dry-run)
    def _load(self):
        if os.path.exists(self.state_path): return json.load(open(self.state_path))
        b = self.c.balances(); g = lambda a: float(b.get(a, {}).get("value") or 0) - float(b.get(a, {}).get("locked") or 0)
        return {"usdt": g("USDT"), "xaut": g(ASSET), "tmn": g("TMN"), "start_eq": None, "peak": None, "day": None, "day_start": None,
                "halted": None, "mode": "base", "trades": 0, "initial_done": False}
    def _save(self): json.dump(self.st, open(self.state_path, "w"), indent=1)
    def _sync(self):
        if not self.live: return
        b = self.c.balances(); g = lambda a: float(b.get(a, {}).get("value") or 0) - float(b.get(a, {}).get("locked") or 0)
        self.st.update(usdt=g("USDT"), xaut=g(ASSET), tmn=g("TMN"))

    # ---------- nodes
    def n_data(self, s):
        t0 = time.time(); m = {"ok": False}
        try:
            bid, ask = self.c.best_bid_ask(SYM)
            bt = requests.get(f"{BIN}/ticker/bookTicker", params={"symbol": SYM}, timeout=5).json()
            g = (float(bt["bidPrice"]) + float(bt["askPrice"])) / 2
            m = {"ok": bool(bid and ask and g), "bid": bid, "ask": ask, "global": g, "age_s": round(time.time() - t0, 2),
                 "prem_bid": bid / g - 1, "prem_ask": ask / g - 1}
            if g != self.g_last[0]: self.g_last = (g, time.time())
            m["ref_age_s"] = round(time.time() - self.g_last[1], 1)
        except Exception as e: m["err"] = repr(e)[:160]
        if time.time() - self.mom_cache[0] > 900:
            try:
                k = requests.get(f"{BIN}/klines", params={"symbol": SYM, "interval": "1d", "limit": 30}, timeout=10).json()[:-1]
                c = [float(x[4]) for x in k]; mom = c[-1] / c[-1 - P["mom_n"]] - 1
                h = requests.get(f"{BIN}/klines", params={"symbol": SYM, "interval": "1h", "limit": 600}, timeout=10).json()[:-1]
                self.mom_cache = (time.time(), {"mom20": mom, "f": feats(h)})
            except Exception as e: log({"event": "data_error", "node": "data", "err": repr(e)[:160]})
        if self.mom_cache[1]: m.update(self.mom_cache[1])
        return {"mkt": m, "st": self.st}
    def n_regime(self, s):
        m = s["mkt"]
        if "f" not in m: return {"reg": {"probs": {"trend_up": 1/3, "range": 1/3, "trend_down": 1/3}, "src": "none"}}
        if self.ai_cache is None or time.time() - self.ai_cache[0] > P["ai_every_s"]:
            f = {**m["f"], "mom20d": m["mom20"]}
            try: d = self.model.decide(SYM, f, {"strategy": "xaut_gap_inventory", "reason": "regime bias for inventory target"}, rule_conf=0.5)
            except Exception as e: d = self.det.decide(SYM, f, None); d.reason += f" | ai_error:{type(e).__name__}"
            self.ai_cache = (time.time(), {"probs": d.regime_probs, "regime": d.regime.value, "src": d.backend, "reason": d.reason[:200]})
            log({"event": "ai_regime", **self.ai_cache[1], "mom20d": round(m["mom20"], 5)})
        return {"reg": self.ai_cache[1]}
    def n_allocator(self, s):
        m, r = s["mkt"], s["reg"]; mom = m.get("mom20", 0)
        base = 0.5 + (P["mom_tilt"] if mom > 0 else -P["mom_tilt"]) + P["ai_tilt"] * (r["probs"].get("trend_up", 0) - r["probs"].get("trend_down", 0))
        return {"alloc": {"base": round(min(max(base, 0.3), 0.7), 4), "why": f"mom20d {mom:+.2%} -> {'+' if mom > 0 else '-'}{P['mom_tilt']}, AI {r['regime']} ({r['src']})"}}
    def n_strategy(self, s):
        m, base, st = s["mkt"], s["alloc"]["base"], self.st
        if not m.get("ok"): return {"prop": None}
        pb, pa = m["prem_bid"], m["prem_ask"]; mode = st["mode"]; reason = None
        if mode == "base" and pb > P["th"]: mode, reason = "sold", f"Wallex bid premium {pb:+.3%} > {P['th']:.2%}: SELL XAUT (rich vs global)"
        elif mode == "base" and pa < -P["th"]: mode, reason = "bought", f"Wallex ask discount {pa:+.3%} < -{P['th']:.2%}: BUY XAUT (cheap vs global)"
        elif mode == "sold" and pa <= 0: mode, reason = "base", f"premium closed (ask {pa:+.3%} <= 0): BUY back to base"
        elif mode == "bought" and pb >= 0: mode, reason = "base", f"discount closed (bid {pb:+.3%} >= 0): SELL back to base"
        tgt = {"base": base, "sold": base - P["swing"], "bought": base + P["swing"]}[mode]
        if not st["initial_done"]: reason = reason or f"initial inventory: buy XAUT to base {base:.0%}"
        return {"prop": {"mode": mode, "target_frac": round(tgt, 4), "reason": reason or f"rebalance to {mode} target"}}
    def n_risk(self, s):
        m, p, st = s["mkt"], s["prop"], self.st
        if not m.get("ok") or p is None: log({"event": "veto", "why": "no market data", "err": m.get("err")}); return {"appr": None}
        eq = st["usdt"] + st["xaut"] * m["bid"]; day = now_teh().strftime("%Y-%m-%d")
        if st["start_eq"] is None: st["start_eq"] = st["peak"] = eq
        if st["day"] != day: st["day"], st["day_start"] = day, eq
        st["peak"] = max(st["peak"], eq); st["equity"] = round(eq, 6); st["xaut_frac"] = round(st["xaut"] * m["bid"] / eq, 4) if eq else 0
        if (st["day_start"] - eq) / st["day_start"] >= P["daily_dd"]: st["halted"] = "daily_dd_3%"
        if (st["start_eq"] - eq) / st["start_eq"] >= P["total_dd"]: st["halted"] = "total_dd_5%"
        tgt = min(max(p["target_frac"], 0), P["max_frac"]); delta = tgt * eq - st["xaut"] * m["bid"]; side = "BUY" if delta > 0 else "SELL"
        why = None
        if st["halted"]: why = f"halted:{st['halted']}"
        elif m["age_s"] > P["stale_s"]: why = "stale data"
        elif p["mode"] != "base" and st["mode"] == "base" and (m.get("ref_age_s", 0) > P["ref_stale_s"] or (ASSET == "XAUT" and gold_closed())):
            why = "global reference stale / gold market closed: gap entries paused"
        elif abs(m["prem_bid"]) > P["max_abs_prem"]: why = f"premium {m['prem_bid']:+.2%} implausible (data guard)"
        elif abs(delta) < max(P["min_order"], P["min_rebal_frac"] * eq): why = "within band" if p["mode"] == st["mode"] or st["initial_done"] else "below min order"
        elif time.time() - self.last_order < P["order_gap_s"]: why = "rate limit"
        if why == "within band":
            st["mode"] = p["mode"]; st["initial_done"] = True; return {"appr": None}
        if why: log({"event": "veto", "why": why, "proposal": p, "equity": st["equity"]}); return {"appr": None}
        price = m["ask"] if side == "BUY" else m["bid"]
        qty = round_step(min(abs(delta), st["usdt"] / (1 + P["fee_t"]) if side == "BUY" else abs(delta)) / price, self.mk.get("stepSize") or 5)
        if side == "SELL": qty = min(qty, round_step(st["xaut"], self.mk.get("stepSize") or 5))
        if qty * price < P["min_order"]: log({"event": "veto", "why": "below min order after rounding", "proposal": p}); return {"appr": None}
        return {"appr": {"side": side, "qty": qty, "price": round_step(price, self.mk.get("tickSize") or 2), "mode": p["mode"], "reason": p["reason"],
                         "target_frac": tgt, "premium_bid": round(m["prem_bid"], 5), "premium_ask": round(m["prem_ask"], 5), "global": m["global"]}}
    def n_execution(self, s):
        a, st = s["appr"], self.st
        if a: self.place(a, s)
        self._save(); return {"st": st}

    def place(self, a, s, tag="GAP"):
        st = self.st; c_id = cid(tag); self.last_order = time.time()
        payload = {"symbol": SYM, "side": a["side"], "type": "LIMIT", "price": str(a["price"]), "quantity": str(a["qty"]), "client_id": c_id}
        rec = {"event": "order", "dry_run": not self.live, "payload": payload, "reason": a["reason"], "mode_to": a.get("mode"),
               "target_frac": a.get("target_frac"), "premium_bid": a.get("premium_bid"), "premium_ask": a.get("premium_ask"),
               "global_xaut": a.get("global"), "regime": (s or {}).get("reg"), "alloc": (s or {}).get("alloc"), "equity_before": st.get("equity")}
        if not self.live:
            q, px = a["qty"], a["price"]
            if a["side"] == "BUY": st["usdt"] -= q * px * (1 + P["fee_t"]); st["xaut"] += q
            else: st["usdt"] += q * px * (1 - P["fee_t"]); st["xaut"] -= q
            rec["fill"] = {"qty": q, "avg": px, "simulated": True}
        else:
            try:
                self.c.place_order(SYM, a["side"], "LIMIT", payload["quantity"], payload["price"], client_id=c_id); time.sleep(8)
                o = self.c.get_order(c_id)
                if o.get("active"):
                    try: self.c.cancel_order(c_id)
                    except Exception: pass
                    time.sleep(1); o = self.c.get_order(c_id)
                eq_ = float(o.get("executedQty") or 0); rec["fill"] = {"qty": eq_, "avg": (float(o.get("executedSum") or 0) / eq_) if eq_ else None,
                                                                          "status": o.get("status"), "fee": o.get("fee")}
            except Exception as e: rec["error"] = repr(e)[:200]
            self._sync()
        if a.get("mode"): st["mode"] = a["mode"]
        st["initial_done"] = True; st["trades"] += 1; rec["balances_after"] = {"usdt": round(st["usdt"], 6), "xaut": st["xaut"]}
        log(rec)

    def liquidate(self):
        log({"event": "liquidation_start", "dry_run": not self.live, "at": P["liq_hhmm"]})
        for _ in range(5):
            self._sync()
            if st_x := round_step(self.st["xaut"], self.mk.get("stepSize") or 5):
                bid, _a = self.c.best_bid_ask(SYM)
                if st_x * bid < P["min_order"]: break
                self.place({"side": "SELL", "qty": st_x, "price": round_step(bid, self.mk.get("tickSize") or 2), "reason": "end-of-challenge liquidation XAUT->USDT"}, None, "LIQX")
                if not self.live: break
                time.sleep(2)
            else: break
        if self.live:
            r = LQ.run(self.c, execute=True, log=log); time.sleep(5); self._sync()
        else:
            r = LQ.run(self.c, execute=False, log=log)   # plan uses REAL balances (dry-run shows what live would do)
            r["virtual_usdt_to_tmn_est"] = round(self.st["usdt"] * self.c.best_bid_ask("USDTTMN")[0] * 0.997)
        log({"event": "liquidation_done", "dry_run": not self.live, "result": r, "balances": {k: self.st[k] for k in ("usdt", "xaut", "tmn")}})
        self._save(); open(DONE + ("" if self.live else ".dry"), "w").write(now_teh().isoformat())

    def graph(self):
        g = StateGraph(S); ns = [("data", self.n_data), ("regime", self.n_regime), ("allocator", self.n_allocator), ("strategy", self.n_strategy),
                                 ("risk", self.n_risk), ("execution", self.n_execution)]
        for n, f in ns: g.add_node(n, f)
        g.set_entry_point("data")
        for (a, _), (b, _) in zip(ns, ns[1:]): g.add_edge(a, b)
        g.add_edge("execution", END); return g.compile()

def run(execute=False, iterations=0, sleep=15, liq_now=False):
    r = Runner(execute); g = r.graph(); hh, mm = map(int, P["liq_hhmm"].split(":"))
    log({"event": "start", "live": r.live, "dry_run": not r.live, "params": P, "backend": r.model.name, "balances": {k: r.st[k] for k in ("usdt", "xaut", "tmn")}})
    i = 0
    while True:
        t = now_teh()
        if liq_now or (t.hour, t.minute) >= (hh, mm): r.liquidate(); return 0
        try:
            out = g.invoke({}); st = r.st
            print(f"{t:%H:%M:%S} eq={st.get('equity')} xaut_frac={st.get('xaut_frac')} mode={st['mode']} prem_bid={out['mkt'].get('prem_bid', 0):+.4%} "
                  f"prem_ask={out['mkt'].get('prem_ask', 0):+.4%} base={out['alloc']['base']} halted={st['halted']}", flush=True)
        except Exception as e: log({"event": "cycle_error", "err": repr(e)[:300]})
        i += 1
        if iterations and i >= iterations: return 0
        time.sleep(sleep)
