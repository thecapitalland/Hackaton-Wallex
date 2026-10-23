"""Kijun(103) first-touch: prev bar fully above kijun & this bar low<=kijun -> long at kijun; mirror for short.
TP/SL fixed % (XAUT $ / XAUT price; same % for BTC/ETH), or SL='kb' = exit at close when bar closes beyond kijun.
Both hit same bar -> SL. One position at a time (symmetric run); long-only stats = long subset.
Data: 1m/5m = last 60d Binance 1m; 15m/1h/4h = Binance since 2022."""
import json, numpy as np, pandas as pd
SPREAD={"XAUTUSDT":4e-4,"BTCUSDT":7e-4,"ETHUSDT":19e-4}
FEES={"zero":0,"maker_taker":.0023,"taker_taker":.0026}
def data(sym,tf):
    if tf in("1m","5m"): d=pd.read_pickle(f"data/candles/BINH_{sym}_1m_60d.pkl")
    elif tf=="15m": d=pd.read_pickle(f"data/candles/BINH_{sym}_15m.pkl")
    else: d=pd.read_pickle(f"data/candles/BINH_{sym}_1h.pkl")
    if tf in("5m","4h"): d=d.resample({"5m":"5min","4h":"4h"}[tf]).agg(dict(o="first",h="max",l="min",c="last")).dropna()
    return d
def sim(d,tp,sl):
    o,h,l,c=(d[x].values for x in "ohlc")
    k=((d.h.rolling(103).max()+d.l.rolling(103).min())/2).shift(1).values
    out=[]; i=104; n=len(o)
    while i<n-1:
        s=1 if (l[i-1]>k[i-1] and l[i]<=k[i]) else -1 if (h[i-1]<k[i-1] and h[i]>=k[i]) else 0
        if not s: i+=1; continue
        e=k[i]; r=None; j=i
        while j<n:
            if sl!="kb" and ((s>0 and l[j]<=e*(1-sl)) or (s<0 and h[j]>=e*(1+sl))): r=-sl; break
            if (s>0 and h[j]>=e*(1+tp)) or (s<0 and l[j]<=e*(1-tp)):
                if not (j==i and ((s>0 and c[j]<e) or (s<0 and c[j]>e))): r=tp; break
            if sl=="kb" and ((s>0 and c[j]<k[j]) or (s<0 and c[j]>k[j])): r=s*(c[j]/e-1); break
            j+=1
        if r is None: break
        out.append((s,r)); i=j+1
    return np.array(out).reshape(-1,2)
ref=pd.read_pickle("data/candles/BINH_XAUTUSDT_1m_60d.pkl").c.iloc[-1]; res=[]
for sym in SPREAD:
    for tf in ("1m","5m","15m","1h","4h"):
        d=data(sym,tf); days=max((d.index[-1]-d.index[0]).days,1)
        for tu in (3,5,10,20):
            for su in (3,5,10,"kb"):
                tp=tu/ref; sl=su if su=="kb" else su/ref; T=sim(d,tp,sl)
                for mode in ("long","sym"):
                    R=T[T[:,0]>0,1] if mode=="long" else T[:,1]
                    for fk,f in FEES.items():
                        x=R-(f+(SPREAD[sym] if f else 0)); eq=np.cumsum(x)
                        res.append(dict(sym=sym,tf=tf,days=days,tp_usd=tu,sl=su,tp_pct=round(tp*100,3),mode=mode,fee=fk,n=len(x),
                          tpd=round(len(x)/days,2),win=round((x>0).mean()*100,1) if len(x) else 0,avg_pct=round(x.mean()*100,4) if len(x) else 0,
                          total_pct=round(eq[-1]*100,1) if len(x) else 0,maxdd_pct=round((eq-np.maximum.accumulate(np.r_[0,eq])[1:]).min()*100,1) if len(x) else 0))
        print(sym,tf,len(d),flush=True)
json.dump(res,open("data/research_kijun.json","w"),indent=1)
df=pd.DataFrame(res)
print(df[df.fee=="zero"].groupby(["tf","mode"]).avg_pct.agg(["mean","max"]))
print(df[df.fee=="maker_taker"].sort_values("avg_pct").tail(8).to_string())
print(df[(df.fee=="zero")&(df.tp_usd==3)&(df.mode=="long")].groupby(["sym","tf"])[["win","avg_pct","tpd"]].mean())
