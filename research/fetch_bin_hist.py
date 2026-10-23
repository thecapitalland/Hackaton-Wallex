import requests, time, pandas as pd, sys
U="https://data-api.binance.vision/api/v3/klines"
def get(sym, iv, start):
    out=[]; t=int(pd.Timestamp(start,tz="UTC").timestamp()*1000)
    while True:
        k=requests.get(U,params={"symbol":sym,"interval":iv,"startTime":t,"limit":1000},timeout=20).json()
        if not k: break
        out+=k; t=k[-1][0]+1
        if len(k)<1000: break
    d=pd.DataFrame([[float(x[i]) for i in (1,2,3,4,5)] for x in out],columns="o h l c v".split(),index=pd.to_datetime([x[0] for x in out],unit="ms",utc=True))
    d=d[~d.index.duplicated()].iloc[:-1]; d.to_pickle(f"data/candles/BINH_{sym}_{iv}.pkl"); print(sym,iv,len(d),d.index[0],flush=True)
for s in ("BTCUSDT","ETHUSDT","XAUTUSDT"):
    for iv in ("15m","1h"): get(s,iv,"2022-01-01")
