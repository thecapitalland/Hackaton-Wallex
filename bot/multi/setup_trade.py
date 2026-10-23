"""Single discretionary-setup trade executed algorithmically (user-approved): BUY at ENTRY (limit at best ask), native exchange-side
STOP_MARKET SELL at SL (Wallex spot supports STOP_LIMIT/STOP_MARKET via stop_price, see developers.wallex.ir/docs/spot-create-order;
no OCO). TP: try a resting LIMIT SELL too; if the exchange rejects it (qty already locked by the stop) the bot monitors and, at bid>=TP,
cancels the stop and sells. When one leg fills the other is cancelled. Reserves the lot in logs/setup_lock.json so live-xaut ignores it.
Log: logs/setup_trades.jsonl."""
import json, os, time, uuid, datetime as dt
from zoneinfo import ZoneInfo
from ..risk import round_step
TZ = ZoneInfo("Asia/Tehran"); SYM = "XAUTUSDT"; LOG = "logs/setup_trades.jsonl"; LOCK = "logs/setup_lock.json"; FEE_T = 0.0013; FEE_M = 0.001
REASON = ("ستاپ گرفتن نقدینگی سقف (liquidity grab): خرید طلا با حد ضرر ۴۰۱۰ و حد سود ۴۰۳۲؛ حجم طوری تنظیم شد که با خوردن حد ضرر حداکثر ۱۰٪ حساب از دست برود"
          " | Liquidity-grab setup: buy gold, SL 4010, TP 4032; size chosen so an SL hit loses at most 10% of the account")

def log(rec):
    os.makedirs("logs", exist_ok=True)
    rec = {"ts": int(time.time()), "time_tehran": dt.datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), "setup": os.getenv("SETUP_NAME", "liq_grab_xaut"), **rec}
    with open(LOG, "a") as f: f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)
def cid(t): return f"XS_{t}_{int(time.time())}_{uuid.uuid4().hex[:6]}".upper()
def write_lock(d): json.dump(d, open(LOCK, "w"))
def release_lock():
    if os.path.exists(LOCK): os.remove(LOCK)

def plan(usdt_free, total_eq, entry, sl, tp, step, buffer=0.03):
    """Size from free USDT minus buffer; check SL loss <= 10% equity."""
    qty = round_step((usdt_free - buffer) / (entry * (1 + FEE_T)), step)
    loss = qty * entry * (1 + FEE_T) - qty * sl * (1 - FEE_T)
    gain_tp = qty * tp * (1 - FEE_M) - qty * entry * (1 + FEE_T)
    return {"qty": qty, "cost": round(qty * entry, 6), "loss_at_sl": round(loss, 6), "loss_pct_eq": round(loss / total_eq, 5),
            "net_at_tp": round(gain_tp, 6), "ok": loss <= 0.10 * total_eq and qty > 0}

def stop_order(c, qty, sl, c_id):
    errs = []
    # STOP_MARKET is rejected on Wallex ("market orders not allowed"); use STOP_LIMIT with limit 0.15% below the stop
    for k in ("stop_price", "stopPrice", "stop_Price"):
        body = {"symbol": SYM, "side": "SELL", "type": "STOP_LIMIT", "quantity": str(qty), "price": str(round(sl * 0.9985, 2)), k: str(sl), "client_id": c_id}
        try: return c._auth_req("POST", "/v1/account/orders", json=body)["result"], k
        except Exception as e: errs.append(f"{k}:{getattr(getattr(e,'response',None),'text','')[:150] or repr(e)[:150]}")
    raise RuntimeError(errs)

def run(c, entry, sl, tp, poll=3):
    m = c.markets()[SYM]; step, tick = m.get("stepSize") or 5, m.get("tickSize") or 2
    b = c.balances(); g = lambda a, k="value": float(b.get(a, {}).get(k) or 0)
    bid, ask = c.best_bid_ask(SYM); usdt_free = g("USDT") - g("USDT", "locked")
    tmn_usdt = g("TMN") / c.best_bid_ask("USDTTMN")[1]
    total_eq = g("USDT") + g("XAUT") * bid + tmn_usdt
    px = round_step(min(ask, entry * 1.001), tick); p = plan(usdt_free, total_eq, px, sl, tp, step)
    risk = {**p, "entry_px": px, "sl": sl, "tp": tp, "total_eq_usdt": round(total_eq, 4), "bid": bid, "ask": ask,
            "note": "TP +$5 (~0.12%) is below round-trip fees (~0.23%): even at TP the net is slightly negative; chosen by the user"}
    if os.getenv("RESUME_QTY"):   # entry already filled (crash after entry): go straight to exits
        fx = float(os.getenv("RESUME_QTY")); avg = float(os.getenv("RESUME_AVG", px)); return exits(c, fx, avg, sl, tp, step, tick, poll)
    if not p["ok"]: log({"event": "setup_veto", "reason": REASON, "risk": risk}); return 1
    write_lock({"xaut": 0.0, "usdt": p["cost"] * (1 + FEE_T) + 0.01, "since": int(time.time())})
    c_buy = cid("BUY"); c.place_order(SYM, "BUY", "LIMIT", str(p["qty"]), str(px), client_id=c_buy); time.sleep(8)
    o = c.get_order(c_buy)
    if o.get("active"):
        try: c.cancel_order(c_buy)
        except Exception: pass
        time.sleep(1); o = c.get_order(c_buy)
    fq = float(o.get("executedQty") or 0); avg = float(o.get("executedSum") or 0) / fq if fq else None
    log({"event": "setup_entry", "reason": REASON, "risk": risk, "order": {"client_id": c_buy, "status": o.get("status"), "fill_qty": fq, "avg": avg, "fee": o.get("fee")}})
    if not fq: release_lock(); log({"event": "setup_end", "why": "entry not filled"}); return 1
    b = c.balances(); fx = min(round_step(float(b["XAUT"]["value"]) - float(b["XAUT"].get("locked") or 0), step), round_step(fq, step))
    return exits(c, fx, avg, sl, tp, step, tick, poll)

STATE = "logs/setup_state.json"; DONE = "logs/setup_trade.DONE"; CANCEL_TP_AT = float(os.getenv("CANCEL_TP_AT", "4012")); REARM_TP_AT = float(os.getenv("REARM_TP_AT", "4018"))
def _ex(o): return float(o.get("executedQty") or 0)
def _save(st): json.dump(st, open(STATE, "w"), indent=1)

def exits(c, fx, avg, sl, tp, step, tick, poll, st=None):
    """Exit manager (restart-safe via logs/setup_state.json). Only ever sells the setup lot (fx minus what's already sold)."""
    write_lock({"xaut": fx, "usdt": 0.0, "since": int(time.time()), "entry": avg})
    st = st or (json.load(open(STATE)) if os.path.exists(STATE) else {})
    st.setdefault("fx", fx); st.setdefault("sold", 0.0); st.setdefault("sl_cid", os.getenv("RESUME_SL_CID")); st.setdefault("tp_cid", os.getenv("RESUME_TP_CID"))
    st.setdefault("tp_on", bool(st["tp_cid"])); st.setdefault("trig_t", None)
    rem = lambda: round_step(st["fx"] - st["sold"], step)
    if not st["sl_cid"]:
        st["sl_cid"] = cid("SL"); so, key = stop_order(c, rem(), sl, st["sl_cid"]); log({"event": "setup_sl_native", "client_id": st["sl_cid"], "qty": rem(), "stop": sl, "resp": so})
    if not st["tp_cid"]:
        st["tp_cid"] = cid("TP"); c.place_order(SYM, "SELL", "LIMIT", str(rem()), str(tp), client_id=st["tp_cid"]); st["tp_on"] = True
        log({"event": "setup_tp_resting", "client_id": st["tp_cid"], "qty": rem(), "price": tp})
    _save(st); log({"event": "setup_monitor_start", "state": st})
    while rem() * sl >= 1.0:
        time.sleep(poll)
        try:
            s = c.get_order(st["sl_cid"]); t = c.get_order(st["tp_cid"]) if st["tp_on"] else {}
            sold = st.get("sold_base", 0.0) + _ex(s) + (_ex(t) if st["tp_on"] else 0); st["sold"] = sold
            if st["tp_on"] and not t.get("active") and _ex(t) > 0:
                try: c.cancel_order(st["sl_cid"])
                except Exception: pass
                log({"event": "setup_exit", "leg": "TP", "order": t}); break
            if not s.get("active") and _ex(s) > 0 and rem() * sl < 1.0:
                if st["tp_on"]:
                    try: c.cancel_order(st["tp_cid"])
                    except Exception: pass
                log({"event": "setup_exit", "leg": "SL", "order": s}); break
            bid, _ = c.best_bid_ask(SYM); triggered = _ex(s) > 0 or (bid is not None and bid <= sl)
            if st["tp_on"] and bid is not None and bid <= CANCEL_TP_AT:
                c.cancel_order(st["tp_cid"]); time.sleep(1); t = c.get_order(st["tp_cid"]); st["sold_base"] = st.get("sold_base", 0.0) + _ex(t); st["tp_on"] = False
                log({"event": "setup_tp_cancelled", "bid": bid, "tp_filled": _ex(t), "reason": f"bid {bid} <= {CANCEL_TP_AT}: free the lot so the exchange STOP_LIMIT @{sl} can sell (لغو حد سود تا حد ضرر صرافی موجودی آزاد داشته باشد)"})
            elif not st["tp_on"] and bid is not None and bid >= REARM_TP_AT and _ex(s) == 0 and s.get("active"):
                st["tp_cid"] = cid("TP"); c.place_order(SYM, "SELL", "LIMIT", str(rem()), str(tp), client_id=st["tp_cid"]); st["tp_on"] = True; st["trig_t"] = None
                log({"event": "setup_tp_rearmed", "bid": bid, "client_id": st["tp_cid"], "qty": rem(), "reason": f"bid {bid} >= {REARM_TP_AT} and SL not triggered: re-place TP @{tp} (بازگشت قیمت، ثبت دوباره حد سود)"})
            if triggered and not st["tp_on"]:
                st["trig_t"] = st["trig_t"] or time.time(); lim = float(s.get("price") or sl * 0.9985)
                if time.time() - st["trig_t"] > 20 and bid is not None and bid < lim and rem() * bid >= 1.0:
                    try: c.cancel_order(st["sl_cid"])
                    except Exception: pass
                    time.sleep(1); s = c.get_order(st["sl_cid"]); st["sold_base"] = st.get("sold_base", 0.0) + _ex(s); st["sold"] = st["sold_base"]
                    q = rem(); x_cid = cid("SLX"); c.place_order(SYM, "SELL", "LIMIT", str(q), str(round_step(bid, tick)), client_id=x_cid); time.sleep(8)
                    x = c.get_order(x_cid)
                    if x.get("active"):
                        try: c.cancel_order(x_cid)
                        except Exception: pass
                        time.sleep(1); x = c.get_order(x_cid)
                    st["sold_base"] += _ex(x); st["sold"] = st["sold_base"]
                    log({"event": "setup_sl_fallback", "bid": bid, "stop_limit": lim, "qty": q, "filled": _ex(x), "client_id": x_cid,
                         "reason": "SL triggered but unfilled >20s with bid below its limit: aggressive LIMIT at best bid for the remaining setup lot only"})
                    if rem() * bid >= 1.0:   # re-arm protection for remainder, keep looping
                        st["sl_cid"] = cid("SL"); stop_order(c, rem(), sl, st["sl_cid"]); st["trig_t"] = None
                    else: log({"event": "setup_exit", "leg": "SL_fallback"}); break
            _save(st)
        except Exception as e: log({"event": "setup_monitor_error", "err": repr(e)[:200]})
    release_lock(); _save(st); open(DONE, "w").write(str(int(time.time()))); log({"event": "setup_end", "lock_released": True, "state": st}); return 0

def main():
    from ..wallex import WallexClient
    if os.getenv("LIVE") != "1": print("setup-trade needs LIVE=1"); return 1
    return run(WallexClient(), float(os.getenv("ENTRY", "4026.9")), float(os.getenv("SL", "4010")), float(os.getenv("TP", "4032")))
