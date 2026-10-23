"""Liquidate ALL non-TMN assets to TMN on {ASSET}TMN markets. DRY-RUN by default.
Real orders only with --execute AND env LIVE=1. LIMIT at best bid (marketable), MARKET fallback for remainder."""
import json, os, time, uuid
from ..risk import round_step

def plan(client):
    bals = client.balances(); mk = client.markets(); out = []
    for asset, b in bals.items():
        free = float(b.get("value") or 0) - float(b.get("locked") or 0)
        if asset == "TMN" or free <= 0: continue
        sym = f"{asset}TMN"
        if sym not in mk: out.append({"asset": asset, "free": free, "skip": f"no {sym} market"}); continue
        m = mk[sym]; bid, ask = client.best_bid_ask(sym); qty = round_step(free, m.get("stepSize") or 8)
        if qty * (bid or 0) < float(m.get("minNotional") or 0):
            out.append({"asset": asset, "free": free, "skip": f"below minNotional ({qty*bid:,.0f} TMN)"}); continue
        out.append({"asset": asset, "free": free, "est_tmn": round(qty * bid * 0.997),
                    "payload": {"symbol": sym, "side": "SELL", "type": "LIMIT", "price": str(round_step(bid, m.get("tickSize") or 0)),
                                "quantity": str(qty), "client_id": f"LIQ_{asset}_{uuid.uuid4().hex[:10]}".upper()},
                    "fallback": {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": "<remaining>"}})
    return out

def run(client, execute=False, log=print):
    pl = plan(client)
    for p in pl: log({"event": "liquidation_plan", "dry_run": not execute, **p})
    if not execute or os.getenv("LIVE") != "1":
        return {"status": "DRY_RUN", "plans": pl}
    for p in pl:
        if "payload" not in p: continue
        o = p["payload"]; client.place_order(o["symbol"], "SELL", "LIMIT", o["quantity"], o["price"], client_id=o["client_id"])
        time.sleep(20); st = client.get_order(o["client_id"])
        if st.get("active"):
            client.cancel_order(o["client_id"]); time.sleep(1); st = client.get_order(o["client_id"])
            rem = float(o["quantity"]) - float(st.get("executedQty") or 0)
            if rem > 0: client.place_order(o["symbol"], "SELL", "MARKET", rem, client_id=f"LIQFB_{uuid.uuid4().hex[:10]}".upper())
        log({"event": "liquidation_result", "symbol": o["symbol"], "status": st.get("status")})
    return {"status": "EXECUTED", "balances": client.balances().get("TMN")}
