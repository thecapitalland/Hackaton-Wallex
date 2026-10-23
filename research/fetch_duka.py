import dukascopy_python as dk, datetime as dt, pandas as pd, sys
from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD as I
start = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc); end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
parts = []; s = start
while s < end:
    e = min(s + dt.timedelta(days=180), end)
    for tries in range(3):
        try: parts.append(dk.fetch(I, dk.INTERVAL_MIN_15, dk.OFFER_SIDE_BID, s, e)); break
        except Exception as ex: print("retry", s, ex, flush=True)
    print(s.date(), len(parts[-1]) if parts else 0, flush=True); s = e
df = pd.concat(parts); df = df[~df.index.duplicated()]; df.columns = ['o', 'h', 'l', 'c', 'v']
df.to_pickle("data/candles/DUKA_XAUUSD_15m.pkl"); print("done", len(df), df.index[0], df.index[-1])
