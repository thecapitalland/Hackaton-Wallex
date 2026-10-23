"""FastAPI dashboard: PnL, positions, recent decisions. Run: uvicorn dashboard:app --port 8000"""
import json, os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from bot.config import load_config

app = FastAPI(title="Wallex Agent Bot")
cfg = load_config()

def _state():
    p = cfg.state_path
    for path in (p.replace(".json", "_live.json"), p):
        if os.path.exists(path): return json.load(open(path)), path
    return {}, None

def _log(n=200):
    if not os.path.exists(cfg.log_path): return []
    return [json.loads(l) for l in open(cfg.log_path).readlines()[-n:]]

@app.get("/api/state")
def api_state():
    st, path = _state(); return {"source": path, **st}

@app.get("/api/decisions")
def api_decisions(n: int = 100): return _log(n)

@app.get("/health")
def health(): return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def index():
    st, _ = _state(); log = _log(400)
    eqs = [d for d in log if d.get("type") == "equity"]
    eq = eqs[-1]["equity"] if eqs else st.get("cash", 0)
    start = st.get("start_equity", cfg.start_equity_usdt)
    pnl = (eq / start - 1) * 100 if start else 0
    pos = "".join(f"<tr><td>{s}</td><td>{p['qty']}</td><td>{p['entry']:.4f}</td><td>{p['stop']:.4f}</td>"
                  f"<td>{p['tp']:.4f}</td><td>{p.get('strategy')}</td></tr>" for s, p in st.get("positions", {}).items())
    closed = "".join(f"<tr><td>{c['symbol']}</td><td>{c['entry']:.4f}</td><td>{c['exit']:.4f}</td><td>{c['pnl']:.2f}</td>"
                     f"<td>{c['reason']}</td></tr>" for c in st.get("closed", [])[-20:][::-1])
    decs = "".join(f"<tr><td>{d.get('ts')}</td><td>{d.get('symbol')}</td><td>{d.get('decision')}</td>"
                   f"<td>{d.get('regime',{}).get('regime','')} ({d.get('regime',{}).get('source','')})</td>"
                   f"<td>{'; '.join(d.get('signal',{}).get('reasons',[]))} | {d.get('regime',{}).get('reason','')} | {d.get('risk','') or d.get('reason','')}</td></tr>"
                   for d in log[::-1] if d.get("type") != "equity")
    spark = ",".join(f"{e['equity']:.2f}" for e in eqs[-200:])
    return f"""<html><head><meta http-equiv=refresh content=30><title>Wallex Agent Bot</title>
<style>body{{font-family:sans-serif;margin:20px}}table{{border-collapse:collapse;font-size:13px}}td,th{{border:1px solid #ccc;padding:3px 6px}}</style></head>
<body><h2>Wallex Agent Bot</h2><p><b>Equity:</b> {eq:.2f} {cfg.quote} &nbsp; <b>PnL:</b> {pnl:+.2f}% &nbsp;
<b>Cash:</b> {st.get('cash',0):.2f} &nbsp; <b>Kill switch:</b> {st.get('killed')}</p>
<p>Equity history: {spark[-600:]}</p>
<h3>Open positions</h3><table><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Stop</th><th>TP</th><th>Strategy</th></tr>{pos}</table>
<h3>Closed trades</h3><table><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Reason</th></tr>{closed}</table>
<h3>Decisions</h3><table><tr><th>ts</th><th>Symbol</th><th>Decision</th><th>Regime</th><th>Reasons</th></tr>{decs}</table></body></html>"""
