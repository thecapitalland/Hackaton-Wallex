"""Flip strategy backtest: at each 1m open, prev red->long, prev green->short(hyp)/sell-inventory.
Fixed TP/SL (XAUT in $, BTC/ETH same % of price), max hold N min, one position at a time.
Intrabar: if TP and SL both hit -> SL (conservative). Costs: fee per side + Wallex spread (round trip)."""
import requests, time, json, numpy as np, pandas as pd, os
U="https://data-api.binance.vision/api/v3/klines"; DAYS=60
def load(sym):
    p=f"data/candles/BINH_{sym}_1m_{DAYS}d.pkl"
    if os.path.exists(p): return pd.read_pickle(p)
    t=int((time.time()-DAYS*86400)*1000); out=[]
    while True:
        k=requests.get(U,params={"symbol":sym,"interval":"1m","startTime":t,"limit":1000},timeout=20).json()
        if not k: break
        out+=k; t=k[-1][0]+1
        if len(k)<1000: break
    d=pd.DataFrame([[float(x[i]) for i in (1,2,3,4)] for x in out],columns="o h l c".split(),index=pd.to_datetime([x[0] for x in out],unit="ms",utc=True)).iloc[:-1]
    d.to_pickle(p); return d
SPREAD={"XAUTUSDT":4e-4,"BTCUSDT":7e-4,"ETHUSDT":19e-4}
FEES={"zero":(0,0),"maker_taker":(.001,.0013),"taker_taker":(.0013,.0013)}
def sim(d,tp,sl,N,mode):  # tp,sl as fraction; returns gross per-trade returns
    o,h,l,c=(d[x].values for x in "ohlc"); col=np.sign(c-o); i=1; R=[]; n=len(o)
    while i<n-N:
        s=-col[i-1]
        if s==0 or (mode=="long" and s<0): i+=1; continue
        e=o[i]; r=None
        for j in range(i,i+N):
            if s>0: hs,ht=l[j]<=e*(1-sl),h[j]>=e*(1+tp)
            else:   hs,ht=h[j]>=e*(1+sl),l[j]<=e*(1-tp)
            if hs: r=-sl; break
            if ht: r=tp; break
        if r is None: r=s*(c[j]/e-1)
        R.append(r); i=j+1
    return np.array(R)
def stats(R,cost,days):
    x=R-cost; eq=np.cumsum(x); dd=(eq-np.maximum.accumulate(np.r_[0,eq])[1:]).min() if len(x) else 0
    return dict(n=len(x),tpd=round(len(x)/days,1),win=round((x>0).mean()*100,1) if len(x) else 0,
      avg_pct=round(x.mean()*100,4) if len(x) else 0,total_pct=round(eq[-1]*100,2) if len(x) else 0,maxdd_pct=round(dd*100,2))
res=[]; XP=None
for sym in ("XAUTUSDT","BTCUSDT","ETHUSDT"):
    d=load(sym); days=(d.index[-1]-d.index[0]).days or 1; px=d.c.iloc[-1]
    if sym=="XAUTUSDT": XP=px
    for usd in (1,2,3,5,10,20):
        f=usd/XP
        for N in (1,5,15,60):
            for mode in ("long","sym"):
                R=sim(d,f,f,N,mode); g=R.mean() if len(R) else 0
                for fk,(fe,fx) in FEES.items():
                    cost=fe+fx+(SPREAD[sym] if fk!="zero" else 0)
                    res.append(dict(sym=sym,tp_sl_usd_xaut=usd,tp_sl_pct=round(f*100,4),N=N,mode=mode,fee=fk,
                        breakeven_fee_per_side_pct=round((g-SPREAD[sym])/2*100,4),**stats(R,cost,days)))
    print(sym,len(d),days,flush=True)
json.dump(res,open("data/research_flip.json","w"),indent=1)
df=pd.DataFrame(res)
for fk in FEES: print(fk); print(df[df.fee==fk].sort_values("avg_pct").tail(6).to_string())
