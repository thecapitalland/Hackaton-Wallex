"""Transfer step: Duka XAUUSD TSMOM signal -> Wallex XAUTUSDT execution (Wallex 1m prices, +lag, Wallex costs)."""
import json, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
def s(p): b=json.load(open(p)); x=pd.Series([r["c"] for r in b], index=pd.to_datetime([r["t"] for r in b],unit="s",utc=True)); return x[~x.index.duplicated()].sort_index()
d=pd.read_pickle("data/candles/DUKA_XAUUSD_15m.pkl"); d.index=d.index.tz_localize("UTC") if d.index.tz is None else d.index
w=s("data/candles/XAUTUSDT_1m.json").resample("15min").last().ffill()
g=d.c[d.index.dayofweek<5]
print("Wallex XAUT 15m span", w.index[0], w.index[-1])
j=pd.concat([w.pct_change(),g.reindex(w.index).ffill().pct_change()],axis=1).dropna()
for tf in ("15min","1h","4h","1D"):
    a=w.resample(tf).last().pct_change(); b=g.resample(tf).last().pct_change(); x=pd.concat([a,b],axis=1).dropna(); print(f"corr Wallex XAUT vs Duka XAUUSD {tf}: {x.iloc[:,0].corr(x.iloc[:,1]):.3f}")
lvl=(w/g.reindex(w.index).ffill()-1).dropna(); print(f"XAUT/XAUUSD basis: median {lvl.median()*100:.3f}%  std {lvl.std()*100:.3f}%")
for n in (20,60):
    dc=g.resample("1D").last().dropna(); sig=(dc/dc.shift(n)-1>0).astype(float)
    for lagh in (0,1,4):
        res={}
        for name,px,cost in (("duka",g,0.0013+0.001),("wallex",w,0.0013+0.001)):
            idx=px.index; pos=sig.reindex(idx,method="ffill").shift(1).fillna(0)
            if name=="wallex": pos=pos.shift(lagh*4).fillna(0)
            r=px.pct_change().fillna(0); net=pos.shift(1).fillna(0)*r-pos.diff().abs().fillna(0)*cost
            m=idx>=w.index[0]; res[name]=(net[m].sum()*100,(pos[m].diff().abs()>0).sum(), (0.5*r[m]).sum()*100)
        print(f"TSMOM n={n} lag={lagh}h  duka: {res['duka'][0]:+.2f}% (switches {res['duka'][1]}) | wallex: {res['wallex'][0]:+.2f}% (switches {res['wallex'][1]}) | 0.5-hold wallex {res['wallex'][2]:+.2f}%")
print("current signals:", {n:bool((g.resample('1D').last().dropna().iloc[-1]/g.resample('1D').last().dropna().iloc[-1-n]-1)>0) for n in (20,60)})
