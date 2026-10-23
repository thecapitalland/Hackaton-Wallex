"""Faithful re-implementations of popular GitHub freqtrade strategies (default/published params, NO tuning => entire
2022-2026 sample is out-of-sample). Freqtrade semantics: signal on closed candle -> fill next open; minimal_roi table
(minutes->ret) + stoploss checked intrabar (stop first, conservative); exit signal -> next open. Wallex cost per side = 0.13% + half-spread."""
import numpy as np, pandas as pd, json, warnings; warnings.filterwarnings("ignore")
def ema(s,n): return s.ewm(span=n,adjust=False).mean()
def rsi(s,n):
    d=s.diff(); u=d.clip(lower=0).ewm(alpha=1/n,adjust=False).mean(); v=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False).mean(); return 100-100/(1+u/v)
def bb(s,n,k): m=s.rolling(n).mean(); sd=s.rolling(n).std(ddof=0); return m, m-k*sd, m+k*sd
def tp(d): return (d.h+d.l+d.c)/3
def atr(d,n): tr=pd.concat([d.h-d.l,(d.h-d.c.shift()).abs(),(d.l-d.c.shift()).abs()],axis=1).max(axis=1); return tr.ewm(alpha=1/n,adjust=False).mean()
def adx(d,n=14):
    up=d.h.diff(); dn=-d.l.diff(); pdm=np.where((up>dn)&(up>0),up,0.); ndm=np.where((dn>up)&(dn>0),dn,0.); a=atr(d,n)
    pdi=100*pd.Series(pdm,d.index).ewm(alpha=1/n,adjust=False).mean()/a; ndi=100*pd.Series(ndm,d.index).ewm(alpha=1/n,adjust=False).mean()/a
    return (100*(pdi-ndi).abs()/(pdi+ndi)).ewm(alpha=1/n,adjust=False).mean()
def xa(a,b): return (a>b)&(a.shift()<=b.shift())
def supertrend(d,m,p):
    a=atr(d,p).values; hl=((d.h+d.l)/2).values; c=d.c.values; n=len(c); ub=hl+m*a; lb=hl-m*a; fu=ub.copy(); fl=lb.copy(); up=np.ones(n,bool)
    for i in range(1,n):
        fu[i]=ub[i] if (ub[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]; fl[i]=lb[i] if (lb[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        up[i]= True if c[i]>fu[i-1] else False if c[i]<fl[i-1] else up[i-1]
    return pd.Series(up,d.index)
def cci(d,n=14): t=tp(d); m=t.rolling(n).mean(); md=t.rolling(n).apply(lambda x: np.abs(x-x.mean()).mean(),raw=True); return (t-m)/(0.015*md)
def heikin(d):
    hc=(d.o+d.h+d.l+d.c)/4; ho=hc.copy(); ho.iloc[0]=(d.o.iloc[0]+d.c.iloc[0])/2
    v=ho.values.copy(); hcv=hc.values
    for i in range(1,len(v)): v[i]=(v[i-1]+hcv[i-1])/2
    return pd.Series(v,d.index), hc

# each: (repo, file, original_tf, our_tf, roi{min:ret}, stoploss, fn(d)->(enter, exit))
def binhv45(d):
    m,l,_=bb(d.c,40,2); bbd=(m-l).abs(); cd=(d.c-d.c.shift()).abs(); tail=(d.c-d.l).abs()
    e=l.shift().gt(0)&bbd.gt(d.c*30/1000)&cd.gt(d.c*30/1000)&tail.lt(bbd*30/1000)&d.c.lt(l.shift())&d.c.le(d.c.shift()); return e, pd.Series(False,d.index)
def cluc(d):
    m,l,_=bb(tp(d),20,2); e=(d.c<ema(d.c,50))&(d.c<0.985*l)&(d.v<d.v.rolling(30).mean().shift(1)*20); return e,(d.c>m)
def combined(d):
    m,l,_=bb(d.c,40,2); bbd=(m-l).abs(); cd=(d.c-d.c.shift()).abs(); tail=(d.c-d.l).abs()
    a=l.shift().gt(0)&bbd.gt(d.c*0.008)&cd.gt(d.c*0.0175)&tail.lt(bbd*0.25)&d.c.lt(l.shift())&d.c.le(d.c.shift())
    m2,l2,_=bb(tp(d),20,2); b=(d.c<ema(d.c,50))&(d.c<0.985*l2)&(d.v<d.v.rolling(30).mean().shift(1)*20); return a|b,(d.c>m2)
def bbandrsi(d): m,l,_=bb(tp(d),20,2); r=rsi(d.c,14); return (r<30)&(d.c<l),(r>70)
def adxsmas(d): a=adx(d); s,lg=d.c.rolling(3).mean(),d.c.rolling(6).mean(); return (a>25)&xa(s,lg),(a<25)&xa(lg,s)
def s001(d):
    e20,e50,e100=ema(d.c,20),ema(d.c,50),ema(d.c,100); ho,hc=heikin(d); return xa(e20,e50)&(hc>e20)&(ho<hc), xa(e50,e100)&(hc<e20)&(ho>hc)
def macdcci(d):
    m=ema(d.c,12)-ema(d.c,26); s=ema(m,9); c=cci(d); return (m>s)&(c<=-50),(m<s)&(c>=100)
def st3(d): s=supertrend(d,4,14); return s,~s   # 3 identical defaults (m=4,p=14) collapse to one
def smaoffset(d): return d.c<d.c.rolling(30).mean()*0.958, d.c>ema(d.c,30)*1.012
def elliot(d):
    ewo=(ema(d.c,50)-ema(d.c,200))/d.c*100; r=rsi(d.c,14); lo=ema(d.c,17)*0.978
    return ((d.c<lo)&(ewo>3.34)&(r<60))|((d.c<lo)&(ewo<-17.457)&(r<60)), d.c>ema(d.c,39)*1.011
FT="https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/"; DZ="https://github.com/davidzr/freqtrade-strategies/tree/main/strategies/"
S={"BinHV45":(FT+"berlinguyinca/BinHV45.py","1m","15m",{0:.0125},-.05,binhv45),
   "ClucMay72018":(FT+"berlinguyinca/ClucMay72018.py","5m","15m",{0:.01},-.05,cluc),
   "CombinedBinHAndCluc":(FT+"berlinguyinca/CombinedBinHAndCluc.py","5m","15m",{0:.05},-.05,combined),
   "BbandRsi":(FT+"berlinguyinca/BbandRsi.py","1h","1h",{0:.1},-.25,bbandrsi),
   "AdxSmas":(FT+"berlinguyinca/AdxSmas.py","1h","1h",{0:.1},-.25,adxsmas),
   "Strategy001":(FT+"Strategy001.py","5m","15m",{60:.01,30:.03,20:.04,0:.05},-.10,s001),
   "MACDStrategy(CCI)":(FT+"berlinguyinca/MACDStrategy.py","5m","15m",{60:.01,30:.03,20:.04,0:.05},-.30,macdcci),
   "Supertrend":(FT+"Supertrend.py","1h","1h",{2221:0,861:.029,372:.058,0:.087},-.265,st3),
   "SMAOffset":(DZ+"SMAOffset","5m","15m",{0:1},-.5,smaoffset),
   "ElliotV5HOMod2":(DZ+"ElliotV5HOMod2","5m","15m",{0:.05},-.99,elliot)}

def bt(d,roi,sl,ent,ex,cost,mins):
    o,h,l,c=d.o.values,d.h.values,d.l.values,d.c.values; E=ent.fillna(False).values; X=ex.fillna(False).values; n=len(c); keys=sorted(roi)
    tr=[]; i=1; inpos=False
    while i<n:
        if not inpos:
            if E[i-1]: inpos=True; ep=o[i]; ei=i
            else: i+=1; continue
        held=(i-ei)*mins; th=roi[max(k for k in keys if k<=held)]; px=None
        if l[i]<=ep*(1+sl): px=min(o[i],ep*(1+sl))
        elif h[i]>=ep*(1+th): px=max(o[i],ep*(1+th))
        elif i>ei and X[i-1]: px=o[i]
        if px is not None:
            tr.append((d.index[ei],d.index[i],px/ep*(1-cost)/(1+cost)-1)); inpos=False
            if E[i-1] and px==o[i] and not X[i-1]: pass
        i+=1
    return tr
def stats(tr,idx):
    if not tr: return {"trades":0}
    r=np.array([t[2] for t in tr]); eq=np.cumsum(r); dd=(np.maximum.accumulate(np.r_[0,eq])[1:]-eq).max()
    s=pd.Series(r,[t[1] for t in tr]); day=s.groupby(s.index.normalize()).sum(); ndays=(idx[-1]-idx[0]).days
    gl=-r[r<0].sum()
    return {"trades":len(r),"net_tr_%":round(r.mean()*100,3),"win_%":round((r>0).mean()*100,1),"PF":round(r[r>0].sum()/gl,2) if gl else None,
            "total_%":round(eq[-1]*100,1),"maxDD_%":round(dd*100,1),"trades_per_day":round(len(r)/ndays,2),
            "days_with_trade_%":round(len(day)/ndays*100,1),"trade_days_pos_%":round((day>0).mean()*100,1),
            "per_year_%":{int(k):round(v*100,1) for k,v in s.groupby(s.index.year).sum().items()},
            "last60d_%":round(s[s.index>=idx[-1]-pd.Timedelta(days=60)].sum()*100,1)}
if __name__=="__main__":
    res={}
    for name,(url,otf,tf,roi,sl,fn) in S.items():
        for m in ("BTCUSDT","ETHUSDT","XAUTUSDT"):
            d=pd.read_pickle(f"data/candles/BINH_{m}_{tf}.pkl"); d=d[d.v>0] if m!="XAUTUSDT" else d
            en,ex=fn(d); row={}
            for hs in (5,10,15): row[hs]=stats(bt(d,roi,sl,en,ex,0.0013+hs/1e4,15 if tf=="15m" else 60),d.index)
            res[f"{name}|{m}"]={"url":url,"orig_tf":otf,"tf":tf,"since":str(d.index[0].date()),**{f"hs{k}":v for k,v in row.items()}}
            s=row[10]
            if not s["trades"]: print(f"{name:20s} {m:8s} no trades"); continue
            print(f"{name:20s} {m:8s} {tf:3s} n={s['trades']:5d} net/tr {s['net_tr_%']:+.3f}% win {s['win_%']:5.1f} PF {s['PF']} tot {s['total_%']:+7.1f}% DD {s['maxDD_%']:5.1f}% "
                  f"tr/day {s['trades_per_day']} | hs5 {row[5]['total_%']:+.1f} hs15 {row[15]['total_%']:+.1f} | 60d {s['last60d_%']:+.1f} | {s['per_year_%']}",flush=True)
    json.dump(res,open("data/research_github_strats.json","w"),indent=1,default=str)
