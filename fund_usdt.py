"""User-approved one-time funding step: convert available TMN (minus buffer) to USDT on USDTTMN only.
Marketable LIMIT orders at best ask (chunked so price stays within max_slip of initial ask). Logs to
logs/decisions.jsonl and logs/live_funding.jsonl. Usage: python fund_usdt.py [--execute]  (default dry-run)."""
import argparse, json, os, sys, time, uuid
from bot.wallex import WallexClient
from bot.risk import round_step

REASON = ("trading decision: convert the whole account to USDT first so subsequent trades execute faster and more easily "
          "(USDT-base pairs: lower fees 0.10/0.13% vs 0.25/0.30%, deeper books) | "
          "تصمیم معاملاتی: ابتدا کل حساب به تتر تبدیل می‌شود تا معاملات بعدی سریع‌تر و راحت‌تر انجام شوند")
def log(rec):
    os.makedirs("logs", exist_ok=True); rec = {"ts": int(time.time()), "time_tehran": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    for f in ("logs/decisions.jsonl", "logs/live_funding.jsonl"):
        with open(f, "a") as fh: fh.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str))

def bal(c):
    b = c.balances(); g = lambda a: (float(b.get(a, {}).get("value") or 0), float(b.get(a, {}).get("locked") or 0))
    return g("TMN"), g("USDT")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true"); ap.add_argument("--buffer", type=float, default=0.01)
    ap.add_argument("--max-slip", type=float, default=0.003); a = ap.parse_args()
    c = WallexClient(); mk = c.market("USDTTMN"); step, tick = mk.get("stepSize") or 2, mk.get("tickSize") or 0
    min_notional = float(mk.get("minNotional") or 50000)
    (tmn, tmn_locked), (usdt, _) = bal(c); avail = tmn - tmn_locked; budget = avail * (1 - a.buffer)
    log({"event": "decision", "node": "usdt_funding", "market": "USDTTMN", "action": "BUY_USDT", "dry_run": not a.execute,
         "tmn_available": avail, "budget_tmn": round(budget), "usdt_before": usdt, "reason": REASON})
    first_ask = None; spent = 0.0; orders = []
    for rnd in range(10):
        rem = budget - spent
        if rem < min_notional: break
        d = c.depth("USDTTMN"); asks = sorted(((float(x["price"]), float(x["quantity"])) for x in d["ask"]))
        first_ask = first_ask or asks[0][0]; cap = first_ask * (1 + a.max_slip)
        levels = [(p, q) for p, q in asks if p <= cap]
        if not levels: log({"event": "funding_wait", "reason": "best ask beyond slippage cap", "best_ask": asks[0][0], "cap": cap}); time.sleep(10); continue
        px = levels[-1][0]; book_qty = sum(q for _, q in levels)
        qty = round_step(min(rem / px / 1.0035, book_qty), step)        # room for 0.30% taker fee
        if qty * px < min_notional: break
        cid = f"FUND_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}".upper()
        payload = {"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "price": str(round_step(px, tick)), "quantity": str(qty), "client_id": cid}
        log({"event": "order_payload", "dry_run": not a.execute, **payload, "levels_used": len(levels), "first_ask": first_ask})
        if not a.execute: break
        try:
            r = c.place_order("USDTTMN", "BUY", "LIMIT", qty, round_step(px, tick), client_id=cid)
        except Exception as e:
            body = getattr(getattr(e, "response", None), "text", "")[:300]; log({"event": "order_error", "client_id": cid, "err": repr(e)[:200], "body": body}); break
        st = None
        for _ in range(12):
            time.sleep(2)
            try: st = c.get_order(cid)
            except Exception as e: log({"event": "status_error", "err": repr(e)[:200]}); continue
            if st.get("status") in ("FILLED", "CANCELED") or not st.get("active", True): break
        if st and st.get("status") not in ("FILLED",) and st.get("active"):
            try: c.cancel_order(cid)
            except Exception as e: log({"event": "cancel_error", "err": repr(e)[:200]})
            time.sleep(1); st = c.get_order(cid)
        ex_qty = float(st.get("executedQty") or 0); ex_sum = float(st.get("executedSum") or 0)
        orders.append({"client_id": cid, "status": st.get("status"), "price": px, "executedQty": ex_qty, "executedSum": ex_sum,
                       "executedPrice": st.get("executedPrice"), "fee": st.get("fee"), "raw_keys": sorted(st.keys())})
        log({"event": "order_result", **orders[-1]}); spent += ex_sum
        if ex_qty <= 0: break
        time.sleep(1)
    if a.execute:
        time.sleep(2); (tmn2, l2), (usdt2, _) = bal(c)
        tq = sum(o["executedQty"] for o in orders); ts_ = sum(o["executedSum"] for o in orders)
        log({"event": "funding_done", "orders": len(orders), "usdt_bought": tq, "tmn_spent": ts_, "avg_price": ts_ / tq if tq else None,
             "final_tmn": tmn2, "final_tmn_locked": l2, "final_usdt": usdt2})

if __name__ == "__main__":
    main()
