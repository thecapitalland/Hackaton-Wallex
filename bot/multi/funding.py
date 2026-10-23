"""USDT funding node + end-of-challenge USDT->TMN conversion.
Live: before the first USDT-pair trade convert TMN (minus buffer) to USDT via USDTTMN: LIMIT at best bid (maker),
wait, then MARKET fallback for the remainder. Shadow: simulated virtually. dry_run=True only builds/prints payloads.
Real submission requires dry_run=False AND env LIVE=1 (never enabled by default)."""
import os, time, uuid
from ..risk import round_step

REASON = "fund USDT base for USDT-pair strategy; lower fees 0.10-0.13% vs 0.25-0.30% on TMN pairs"

def _cid(tag): return f"{tag}_{uuid.uuid4().hex[:16]}".upper()

def plan_fund(client, tmn_balance, buffer_tmn=100_000, frac=1.0):
    mk = client.market("USDTTMN"); bid, ask = client.best_bid_ask("USDTTMN")
    spend = max(0.0, tmn_balance * frac - buffer_tmn)
    price = round_step(bid, mk.get("tickSize") or 0)
    qty = round_step(spend / price / 1.003, mk.get("stepSize") or 2)    # leave room for fee
    ok = qty * price >= float(mk.get("minNotional") or 0)
    return {"ok": ok, "maker": {"symbol": "USDTTMN", "side": "BUY", "type": "LIMIT", "price": str(price),
                                "quantity": str(qty), "client_id": _cid("FUND")},
            "fallback": {"symbol": "USDTTMN", "side": "BUY", "type": "MARKET", "quantity": "<remaining>"},
            "spend_tmn": round(qty * price), "bid": bid, "ask": ask, "reason": REASON}

def plan_defund(client, usdt_balance):
    mk = client.market("USDTTMN"); bid, ask = client.best_bid_ask("USDTTMN")
    qty = round_step(usdt_balance, mk.get("stepSize") or 2)
    return {"maker": {"symbol": "USDTTMN", "side": "SELL", "type": "LIMIT", "price": str(round_step(ask, mk.get("tickSize") or 0)),
                      "quantity": str(qty), "client_id": _cid("DEFUND")},
            "fallback": {"symbol": "USDTTMN", "side": "SELL", "type": "MARKET", "quantity": "<remaining>"},
            "reason": "end-of-challenge: convert USDT back to TMN"}

def execute(client, plan, dry_run=True, wait_s=90, log=print):
    log({"event": "funding_plan", "dry_run": dry_run, **plan})
    if dry_run or os.getenv("LIVE") != "1": return {"status": "DRY_RUN"}
    o = plan["maker"]; client.place_order(o["symbol"], o["side"], "LIMIT", o["quantity"], o["price"], client_id=o["client_id"])
    t = time.time(); st = None
    while time.time() - t < wait_s:
        st = client.get_order(o["client_id"])
        if st.get("status") == "FILLED": return st
        time.sleep(5)
    try: client.cancel_order(o["client_id"])
    except Exception: pass
    st = client.get_order(o["client_id"]); rem = float(o["quantity"]) - float(st.get("executedQty") or 0)
    if rem > 0: client.place_order(o["symbol"], o["side"], "MARKET", round(rem, 2), client_id=_cid("FB"))
    return {"status": "FILLED_WITH_FALLBACK", "maker_filled": st.get("executedQty")}
