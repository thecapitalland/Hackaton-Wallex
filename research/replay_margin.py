"""Replay margin gap rule (bot/multi/live_margin.decide) over recorded Wallex book vs Binance mirror (data/live/*.jsonl).
Fill at signal time + 5s latency at Wallex executable price; stop-loss 1.5%; PnL via margin.trip_pnl on 100 USDT loan."""
import json, glob, sys, numpy as np, pandas as pd
sys.path.insert(0, ".")
from bot.multi.live_margin import decide, P
from bot.multi.margin import trip_pnl
def load(SYM):
    W=[];B=[]
    for f in sorted(glob.glob("data/live/wallex_*.jsonl")):
        for ln in open(f):
            if '"'+SYM+'"' not in ln[:80]: continue
            r=json.loads(ln)
            if r.get("bid") and r.get("ask"): W.append((r["ts"],max(x[0] for x in r["bid"]),min(x[0] for x in r["ask"])))
    for f in sorted(glob.glob("data/live/binance_*.jsonl")):
        for ln in open(f):
            r=json.loads(ln); p=r["p"].get(SYM)
            if p: B.append((r["ts"],p))
    w=pd.DataFrame(W,columns=["ts","bid","ask"]).sort_values("ts"); b=pd.DataFrame(B,columns=["ts","g"]).sort_values("ts")
    return pd.merge_asof(w,b,on="ts",tolerance=5,direction="backward").dropna().reset_index(drop=True)
rows=[]
for SYM in ("XAUTUSDT","BTCUSDT","ETHUSDT"):
    m=load(SYM); ts,bid,ask,g=m.ts.values,m.bid.values,m.ask.values,m.g.values; n=len(ts)
    hrs=(ts[-1]-ts[0])/3600; pb_all=bid/g-1; pa_all=ask/g-1
    for th in (0.004,0.005,0.006,0.008):
        pos=None; out=[]; i=0
        while i<n:
            j=min(np.searchsorted(ts,ts[i]+5),n-1)
            if pos:
                cur=ask[i] if pos["side"]=="short" else bid[i]; adv=(cur/pos["entry"]-1)*(1 if pos["side"]=="short" else -1)
                act,_,_=decide(pos,pb_all[i],pa_all[i],th=th,now=ts[i])
                if adv>=P["sl"] or act=="close":
                    ex=ask[j] if pos["side"]=="short" else bid[j]; out.append(trip_pnl(pos["side"],pos["entry"],ex,100,ts[j]-pos["t0"])); pos=None; i=j+1; continue
            else:
                act,side,_=decide(None,pb_all[i],pa_all[i],th=th,now=ts[i])
                if act=="open": pos={"side":side,"entry":bid[j] if side=="short" else ask[j],"t0":ts[j]}; i=j+1; continue
            i+=1
        r=np.array(out)
        rows.append({"symbol":SYM,"hours":round(hrs,1),"max|prem|_%":round(max(pb_all.max(),-pa_all.min())*100,3),"th_%":th*100,"trips":len(r),
                     "net/trip_usdt(100 loan)":round(r.mean(),3) if len(r) else None,"net/trip_%loan":round(r.mean(),3) if len(r) else None,
                     "total_usdt":round(r.sum(),3),"open_at_end":pos is not None})
t=pd.DataFrame(rows); print(t.to_string(index=False)); t.to_csv("data/replay_margin.csv",index=False)
