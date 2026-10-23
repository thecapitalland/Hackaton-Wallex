"""margin-gap SHADOW: every ~15s compare Wallex bid/ask vs Binance mid for XAUTUSDT/BTCUSDT/ETHUSDT.
Wallex premium (bid/g-1 > TH) -> virtual SHORT on margin; discount (ask/g-1 < -TH) -> virtual LONG.
Exit: gap closed (|exec premium| < 0.05%), max hold 8h, stop-loss 1.5% (on price), or liquidation price.
Never opens real positions: only GET + public dry-run/loan. Decisions -> logs/margin_shadow.jsonl."""
import json, os, time, datetime as dt, requests
from zoneinfo import ZoneInfo
from ..wallex import WallexClient
from .margin import MarginClient, choose_coef, trip_pnl, TRADE_FEE, OPEN_FEE
from .live_xaut import gold_closed

TZ = ZoneInfo("Asia/Tehran"); BIN = "https://data-api.binance.vision/api/v3"
SYMS = os.getenv("MARGIN_SYMBOLS", "XAUTUSDT,BTCUSDT,ETHUSDT").split(",")
P = {"th": float(os.getenv("MARGIN_GAP_TH", "0.006")), "exit": 0.0005, "max_hold_s": 8 * 3600, "sl": 0.015,
     "collateral": float(os.getenv("MARGIN_COLLATERAL", "10")), "min_loan": 100.0, "ref_stale_s": 300, "max_abs_prem": 0.03}
LOG = "logs/margin_shadow.jsonl"; STATE = "logs/margin_shadow_state.json"
RT_COST = 2 * TRADE_FEE + OPEN_FEE

def log(rec):
    os.makedirs("logs", exist_ok=True)
    rec = {"ts": int(time.time()), "time_tehran": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with open(LOG, "a") as f: f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
    print(json.dumps(rec, default=str, ensure_ascii=False), flush=True)

def decide(pos, pb, pa, th=None, now=None, closed=False):
    """Pure decision rule. pos None or dict(side, entry, t0, liq). Returns (action, side, reason)."""
    th = P["th"] if th is None else th; now = now or time.time()
    if pos is None:
        if closed: return "skip", None, "global ref stale / gold closed: entries paused"
        if pb > th and pb - RT_COST > 0: return "open", "short", f"Wallex bid premium {pb:+.3%} > {th:.2%}, net edge {pb-RT_COST:+.3%} after {RT_COST:.2%} fees: SHORT"
        if pa < -th and -pa - RT_COST > 0: return "open", "long", f"Wallex ask discount {pa:+.3%} < -{th:.2%}, net edge {-pa-RT_COST:+.3%}: LONG"
        return "hold", None, f"no edge (bid {pb:+.3%}, ask {pa:+.3%}, th {th:.2%})"
    exit_px_prem = pa if pos["side"] == "short" else pb
    if abs(exit_px_prem) < P["exit"] or (pos["side"] == "short" and pa <= 0) or (pos["side"] == "long" and pb >= 0):
        return "close", pos["side"], f"gap closed (exit-side premium {exit_px_prem:+.3%})"
    if now - pos["t0"] > P["max_hold_s"]: return "close", pos["side"], "max hold 8h"
    return "hold", pos["side"], f"waiting for gap close (exit-side premium {exit_px_prem:+.3%})"

class Shadow:
    def __init__(self):
        self.w = WallexClient(); self.m = MarginClient(execute=False)
        self.st = json.load(open(STATE)) if os.path.exists(STATE) else {"pos": {}, "trips": [], "pnl": 0.0}
        self.gl = {}
        try: log({"event": "start", "params": P, "margin_markets": {s: v for s, v in self.m.markets().items() if s in SYMS}, "open_real_positions": self.m.positions()})
        except Exception as e: log({"event": "start", "err": repr(e)[:160]})
    def save(self): json.dump(self.st, open(STATE, "w"), indent=1)
    def step(self, sym):
        try:
            bid, ask = self.w.best_bid_ask(sym)
            bt = requests.get(f"{BIN}/ticker/bookTicker", params={"symbol": sym}, timeout=5).json()
            g = (float(bt["bidPrice"]) + float(bt["askPrice"])) / 2
        except Exception as e: return log({"event": "veto", "symbol": sym, "why": "data error", "err": repr(e)[:160]})
        if self.gl.get(sym, (None,))[0] != g: self.gl[sym] = (g, time.time())
        ref_age = time.time() - self.gl[sym][1]; pb, pa = bid / g - 1, ask / g - 1
        pos = self.st["pos"].get(sym); px = {"bid": bid, "ask": ask, "global": g, "prem_bid": round(pb, 5), "prem_ask": round(pa, 5), "ref_age_s": round(ref_age, 1)}
        if abs(pb) > P["max_abs_prem"]: return log({"event": "veto", "symbol": sym, "why": "implausible premium", **px})
        if pos:  # stop-loss / liquidation on Wallex executable price
            cur = ask if pos["side"] == "short" else bid; adverse = (cur / pos["entry"] - 1) * (1 if pos["side"] == "short" else -1)
            if adverse >= P["sl"] or (pos["side"] == "short" and cur >= pos["liq"]) or (pos["side"] == "long" and cur <= pos["liq"]):
                return self.close(sym, pos, cur, f"stop-loss {adverse:+.2%}", px)
        closed = ref_age > P["ref_stale_s"] or (sym.startswith("XAUT") and gold_closed())
        act, side, why = decide(pos, pb, pa, closed=closed)
        if act == "open":
            entry = bid if side == "short" else ask; coef = choose_coef(P["collateral"], P["min_loan"])
            try: dr = self.m.dry_run(sym, side, P["collateral"], coef, entry)
            except Exception as e: return log({"event": "veto", "symbol": sym, "why": "dry-run failed", "err": repr(e)[:200], **px})
            pos = {"side": side, "entry": entry, "t0": time.time(), "coef": coef, "loan_usdt": P["collateral"] * coef,
                   "liq": float(dr.get("liquidity_price") or 0) or entry * (1 + (0.85 if side == "short" else -0.85) / coef), "dry_run": dr}
            self.st["pos"][sym] = pos; self.save()
            return log({"event": "shadow_open", "symbol": sym, "side": side, "reason": why, "coef": coef, **px, "liq": pos["liq"], "dry_run_fees": {k: dr.get(k) for k in ("trade_fee", "open_fee", "extend_fee")}})
        if act == "close": return self.close(sym, pos, ask if side == "short" else bid, why, px)
        log({"event": "decision", "symbol": sym, "action": act, "pos": pos and pos["side"], "reason": why, **px})
    def close(self, sym, pos, px_exit, why, px):
        hold = time.time() - pos["t0"]; pnl = trip_pnl(pos["side"], pos["entry"], px_exit, pos["loan_usdt"], hold)
        t = {"symbol": sym, "side": pos["side"], "entry": pos["entry"], "exit": px_exit, "hold_s": int(hold), "pnl_usdt": round(pnl, 4),
             "pnl_on_collateral_%": round(pnl / P["collateral"] * 100, 2), "reason": why}
        self.st["trips"].append(t); self.st["pnl"] += pnl; self.st["pos"].pop(sym, None); self.save()
        log({"event": "shadow_close", **t, **px, "cum_pnl_usdt": round(self.st["pnl"], 4)})

def run(iterations=0, sleep=15):
    s = Shadow(); i = 0
    while True:
        for sym in SYMS: s.step(sym)
        i += 1
        if iterations and i >= iterations: return 0
        time.sleep(sleep)
