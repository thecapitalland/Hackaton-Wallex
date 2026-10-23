"""Replay recorded Wallex XAUTUSDT book vs Binance-mirror XAUTUSDT (1s). Inventory gap strategy: base 50% XAUT.
SELL half when (wallex_bid/global-1) > th  -> state 0; BUY back when executable premium (ask/global-1) <= 0 -> state 0.5.
BUY half when (wallex_ask/global-1) < -th -> state 1; SELL back when bid/global-1 >= 0.
Signal at t, fill at book at t+latency (marketable LIMIT = taker 0.13%). Net per round trip = excess vs holding 50%."""
import json, glob, sys, numpy as np, pandas as pd
SYM=sys.argv[1] if len(sys.argv)>1 else "XAUTUSDT"
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
m=pd.merge_asof(w,b,on="ts",tolerance=5,direction="backward").dropna().reset_index(drop=True)
hrs=(m.ts.iloc[-1]-m.ts.iloc[0])/3600
print(f"samples {len(m)} over {hrs:.1f}h  ({pd.to_datetime(m.ts.iloc[0],unit='s').tz_localize('UTC').tz_convert('Asia/Tehran')} -> {pd.to_datetime(m.ts.iloc[-1],unit='s').tz_localize('UTC').tz_convert('Asia/Tehran')})")
mid=(m.bid+m.ask)/2; prem=mid/m.g-1; sp=(m.ask/m.bid-1)
print(f"premium mid: median {prem.median()*100:.3f}%  std {prem.std()*100:.3f}%  |>0.2| {(prem.abs()>.002).mean()*100:.1f}%  |>0.3| {(prem.abs()>.003).mean()*100:.1f}%  spread median {sp.median()*1e4:.1f}bps")
F=0.0013; ts=m.ts.values; bid=m.bid.values; ask=m.ask.values; g=m.g.values
def fill(i,lat):
    j=np.searchsorted(ts,ts[i]+lat); return min(j,len(ts)-1)

pr=pd.Series(m.bid.values/2+m.ask.values/2,index=pd.to_datetime(m.ts,unit="s"))/m.g.values-1
bias2h=pr.rolling("2h",min_periods=20).median().shift(1).fillna(prem.iloc[:200].median()).values
rows=[]
for mode,bias in (("raw (vs 0)",np.zeros(n:=len(ts))),("bias-adj (vs 2h median)",bias2h)):
  for th in (0.002,0.003,0.004):
    for lat in (0,5,10):
        st=0; out=[]; i=0
        while i<n:
            pb=bid[i]/g[i]-1-bias[i]; pa=ask[i]/g[i]-1-bias[i]
            if st==0 and pb>th: j=fill(i,lat); sp_=bid[j]; st=-1; i=j
            elif st==0 and pa<-th: j=fill(i,lat); bp_=ask[j]; st=1; i=j
            elif st==-1 and pa<=0: j=fill(i,lat); out.append(sp_*(1-F)/(ask[j]*(1+F))-1); st=0; i=j
            elif st==1 and pb>=0: j=fill(i,lat); out.append(bid[j]*(1-F)/(bp_*(1+F))-1); st=0; i=j
            i+=1
        r=np.array(out)
        rows.append({"premium":mode,"th_%":th*100,"lat_s":lat,"round_trips":len(out),"net/trip_%":round(r.mean()*100,3) if len(r) else None,
          "win_%":round((r>0).mean()*100) if len(r) else None,"portfolio_excess_%":round(r.sum()*50,3),"open_at_end":st!=0})
t=pd.DataFrame(rows); print(t.to_string(index=False)); t.insert(0,"symbol",SYM); t.to_csv("data/replay_gap.csv" if SYM=="XAUTUSDT" else f"data/replay_gap_{SYM}.csv",index=False)
