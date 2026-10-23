"""Event-driven multi-market portfolio backtester with two wallets (TMN, USDT).
- Signal on bar close t -> order active during bar t+1. Maker entries: limit at close*(1-offset), filled only if
  bar t+1 low < limit (strict trade-through), maker fee, no slippage; otherwise cancelled. Taker: next open + slippage
  + half spread, taker fee. Stops/forced exits are taker.
- USDT pairs trade from a USDT wallet funded once at start (TMN->USDT via USDTTMN taker) and converted back at end.
- Equity marked in TMN at USDTTMN close. Everything force-closed + converted to TMN at the last bar."""
import json, os
from .features import precompute, snapshot
from .strategies import manage_exit
from .risk import RiskManager
from .selector import RuleSelector
from .allocator import Allocator

def load(markets, tf="60", root="data/candles"):
    return {m: json.load(open(f"{root}/{m}_{tf}.json")) for m in markets if os.path.exists(f"{root}/{m}_{tf}.json")}

def usdt_rate_series(data, tf="60", root="data/candles"):
    path = f"{root}/USDTTMN_{tf}.json"
    src = data.get("USDTTMN") or (json.load(open(path)) if os.path.exists(path) else [])
    return {x["t"]: x["c"] for x in src}

def tehran_day(ts): return (ts + 12600) // 86400
def quote(m): return "USDT" if m.endswith("USDT") else "TMN"

class Engine:
    def __init__(self, p, data, rates, markets=None, selector=None, log=None):
        self.p, self.data, self.rates = p, data, rates
        self.markets = [m for m in (markets or list(data)) if m in data]
        self.sel = selector or RuleSelector(p); self.risk = RiskManager(p); self.log = log
        self.series = {m: precompute(data[m]) for m in self.markets}
        self.idx = {m: {b["t"]: i for i, b in enumerate(data[m])} for m in self.markets}
        self._rk = sorted(rates)
        self.alloc = Allocator(p) if p.get("allocator") else None
        self.alloc_log = []

    def fee(self, m, kind):
        f = self.p.get("fees", {}).get(m) or self.p["fee_default"][quote(m)]
        return f[kind]
    def slip(self, m): return (self.p["slippage_bps"] + self.p["spread_bps"].get(m, 10) / 2) / 1e4
    def rate(self, t):
        if t in self.rates: return self.rates[t]
        import bisect
        i = bisect.bisect_right(self._rk, t) - 1
        return self.rates[self._rk[max(i, 0)]] if self._rk else 1.0
    def to_tmn(self, ccy, amt, t): return amt * (self.rate(t) if ccy == "USDT" else 1.0)

    def run(self, t0, t1):
        p = self.p; cash = {"TMN": float(p["capital_tmn"]), "USDT": 0.0}
        pos, pending, trades, curve, last = {}, {}, [], [], {}
        ts = sorted({b["t"] for m in self.markets for b in self.data[m] if t0 <= b["t"] <= t1})
        if not ts: return {"final_tmn": cash["TMN"], "trades": [], "curve": []}
        conv_fee = (p.get("fees", {}).get("USDTTMN") or {"taker": 0.003})["taker"]
        conv_slip = (p["slippage_bps"] + p["spread_bps"].get("USDTTMN", 1) / 2) / 1e4
        if p["usdt_alloc"] > 0:
            amt = cash["TMN"] * p["usdt_alloc"]; cash["TMN"] -= amt
            cash["USDT"] = amt * (1 - conv_fee) / (self.rate(ts[0]) * (1 + conv_slip))
            self._log({"t": ts[0], "event": "fund_usdt", "tmn": amt, "usdt": cash["USDT"]})
        peak = p["capital_tmn"]; day, day_start, halted = None, peak, None

        def close(m, px_raw, t, reason, maker=False):
            q = pos.pop(m); px = px_raw if maker else px_raw * (1 - self.slip(m))
            proceeds = q["qty"] * px * (1 - self.fee(m, "maker" if maker else "taker")); cash[q["ccy"]] += proceeds
            pnl_q = proceeds - q["cost"]; pnl = self.to_tmn(q["ccy"], pnl_q, t)
            tr = {"market": m, "strategy": q["strategy"], "entry_t": q["t"], "exit_t": t, "entry": q["entry"], "exit": px,
                  "pnl_tmn": round(pnl), "ret": pnl_q / q["cost"], "reason": reason, "maker_entry": q["maker"],
                  "decision": q["decision"]}
            trades.append(tr); self._log({"t": t, "event": "exit", **tr})

        for t in ts:
            if day != tehran_day(t):
                day = tehran_day(t); day_start = self.equity(cash, pos, last, t)
                if halted == "daily_loss_cap": halted = None
                if self.alloc: self._rebalance(cash, pos, last, t, conv_fee, conv_slip)
            for m in self.markets:
                i = self.idx[m].get(t)
                if i is None or i < p["warmup"]: continue
                bar = self.data[m][i]; act = pending.pop(m, None)
                if act and act["type"] == "exit" and m in pos: close(m, bar["o"], t, act["reason"])
                elif act and act["type"] == "entry" and m not in pos and not halted:
                    ccy = quote(m); filled = False
                    if p["maker_entries"]:
                        lim = act["close"] * (1 - p["maker_offset_bps"] / 1e4)
                        if bar["o"] <= lim: px, filled = bar["o"], True          # gapped through: fill at open (still maker-ish)
                        elif bar["l"] < lim: px, filled = lim, True
                        fee = self.fee(m, "maker")
                    else:
                        px, filled, fee = bar["o"] * (1 + self.slip(m)), True, self.fee(m, "taker")
                    if not filled: self._log({"t": t, "event": "limit_unfilled", "market": m})
                    else:
                        r = self.rate(t) if ccy == "USDT" else 1.0
                        budget_q = min(act["notional"] / r, cash[ccy] / (1 + fee))
                        if budget_q * r >= p["min_notional_tmn"]:
                            cost = budget_q * (1 + fee); cash[ccy] -= cost
                            stop = px - (act["close"] - act["stop"])
                            pos[m] = {"qty": budget_q / px, "entry": px, "stop": stop, "peak": px, "bars": 0, "t": t,
                                      "ccy": ccy, "cost": cost, "strategy": act["strategy"], "maker": p["maker_entries"],
                                      "decision": act["decision"]}
                            self._log({"t": t, "event": "fill", "market": m, "price": px, "notional_tmn": round(budget_q * r),
                                       "maker": p["maker_entries"]})
                last[m] = bar["c"]
                if m in pos:
                    f = snapshot(self.data[m], self.series[m], i, p["donchian"])
                    px, why = manage_exit(pos[m], bar, f, p)
                    if px is not None: close(m, px, t, why)
                    elif why: pending[m] = {"type": "exit", "reason": why}
            eq = self.equity(cash, pos, last, t); peak = max(peak, eq); curve.append((t, eq))
            h = self.risk.check_portfolio(eq, day_start, peak)
            if h and not halted:
                halted = h; self._log({"t": t, "event": "halt", "reason": h, "equity": eq})
                for m in list(pos): close(m, last[m], t, h)
                pending.clear()
            if halted: continue
            scan = {}
            for m in self.markets:
                i = self.idx[m].get(t)
                if i is None or i < p["warmup"] or m in pos or m in pending: continue
                if p["spread_bps"].get(m, 10) > p["max_spread_bps"]: continue
                if quote(m) == "USDT" and self.to_tmn("USDT", cash["USDT"], t) < p["min_notional_tmn"]: continue
                scan[m] = snapshot(self.data[m], self.series[m], i, p["donchian"], p["spread_bps"].get(m, 10) / 1e4)
            if not scan: continue
            for d in self.sel.decide(scan):
                dec = {k: v for k, v in d.items() if not k.startswith("_")}
                if d["strategy"] == "none":
                    self._log({"t": t, "event": "decision", **dec, "approved": False}); continue
                prop = d["_prop"]; f = scan[d["market"]]; ccy = quote(d["market"])
                held = {**pos, **{k: 1 for k, v in pending.items() if v["type"] == "entry"}}
                avail = self.to_tmn(ccy, cash[ccy], t) - sum(v["notional"] for k, v in pending.items()
                                                             if v["type"] == "entry" and quote(k) == ccy)
                notional, why = self.risk.check_entry(d, f["close"], prop["stop"], eq, max(avail, 0), held, halted)
                self._log({"t": t, "event": "decision", **dec, "risk": why, "approved": notional > 0})
                if notional > 0:
                    pending[d["market"]] = {"type": "entry", "notional": notional, "stop": prop["stop"], "close": f["close"],
                                            "strategy": prop["strategy"], "decision": dec}
        tl = ts[-1]
        for m in list(pos): close(m, last[m], tl, "force_close_end")
        if cash["USDT"] > 0:
            tmn = cash["USDT"] * self.rate(tl) * (1 - conv_slip) * (1 - conv_fee)
            self._log({"t": tl, "event": "defund_usdt", "usdt": cash["USDT"], "tmn": tmn}); cash["TMN"] += tmn; cash["USDT"] = 0
        curve.append((tl, cash["TMN"]))
        return {"final_tmn": cash["TMN"], "trades": trades, "curve": curve}

    def _feat(self, m, t):
        if m not in self.idx: return None
        i = self.idx[m].get(t)
        if i is None or i < self.p["warmup"]: return None
        return snapshot(self.data[m], self.series[m], i - 1, self.p["donchian"])   # last CLOSED bar

    def _rebalance(self, cash, pos, last, t, conv_fee, conv_slip):
        a = self.alloc.decide(self._feat("USDTTMN", t), self._feat("XAUTTMN", t), [])
        eq = self.equity(cash, pos, last, t); r = self.rate(t)
        cur = self.to_tmn("USDT", cash["USDT"], t) + sum(self.to_tmn("USDT", q["qty"] * last.get(m, q["entry"]), t)
                                                         for m, q in pos.items() if q["ccy"] == "USDT")
        diff = a["usdt_share"] * eq - cur
        if abs(diff) < self.p.get("rebalance_band", 0.1) * eq: return
        if diff > 0: amt = min(diff, cash["TMN"]); cash["TMN"] -= amt; cash["USDT"] += amt * (1 - conv_fee) / (r * (1 + conv_slip))
        else:
            u = min(-diff / r, cash["USDT"]); cash["USDT"] -= u; cash["TMN"] += u * r * (1 - conv_slip) * (1 - conv_fee); amt = -u * r
        self._log({"t": t, "event": "allocator", **a, "rebalance_tmn": round(amt)}); self.alloc_log.append((t, a["usdt_share"]))

    def equity(self, cash, pos, last, t):
        e = cash["TMN"] + self.to_tmn("USDT", cash["USDT"], t)
        for m, q in pos.items(): e += self.to_tmn(q["ccy"], q["qty"] * last.get(m, q["entry"]) * (1 - self.fee(m, "taker")), t)
        return e

    def _log(self, rec):
        if self.log is not None: self.log.append(rec)

def stats(res, capital):
    eq = [e for _, e in res["curve"]] or [capital]; pk, mdd = capital, 0.0
    for e in eq: pk = max(pk, e); mdd = max(mdd, (pk - e) / pk)
    tr = res["trades"]; w = [x for x in tr if x["pnl_tmn"] > 0]
    return {"return_pct": round((res["final_tmn"] / capital - 1) * 100, 2), "max_dd_pct": round(mdd * 100, 2),
            "trades": len(tr), "win_rate": round(len(w) / len(tr) * 100, 1) if tr else 0.0}

def buy_hold(eng, m, t0, t1):
    """Buy at first open, sell at last close, taker fees both sides; USDT pairs include TMN<->USDT conversion."""
    bars = [b for b in eng.data[m] if t0 <= b["t"] <= t1]
    if not bars: return 0.0
    g = bars[-1]["c"] / bars[0]["o"] * (1 - eng.fee(m, "taker")) ** 2 * (1 - eng.slip(m)) ** 2
    if quote(m) == "USDT": g *= eng.rate(bars[-1]["t"]) / eng.rate(bars[0]["t"]) * (1 - 0.003) ** 2
    return round((g - 1) * 100, 2)
