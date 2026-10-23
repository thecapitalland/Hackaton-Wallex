"""Wallex margin-trade (تعهدی) client. SAFE BY DEFAULT: only GET endpoints and the public dry-run / loan calculators are used.
open/close/edit raise unless env MARGIN_LIVE=1 AND execute=True (the margin-gap mode never passes execute=True)."""
import os, requests
BASE = os.getenv("WALLEX_BASE_URL", "https://api.wallex.ir") + "/margin-trade/v1"
# fee model on loan notional (from public/dry-run + docs)
TRADE_FEE = 0.0025      # per side
OPEN_FEE = 0.00015      # once (dry-run: 0.00000366 XAUT on 0.0244 loan = 0.015%)
EXTEND_FEE = 0.00015    # per 4h extension
EXTEND_S = 14400

class LiveMarginDisabled(RuntimeError): pass

class MarginClient:
    def __init__(self, api_key=None, timeout=10, execute=False):
        self.k = api_key or os.getenv("WALLEX_API_KEY", ""); self.t = timeout; self.s = requests.Session()
        self.execute = bool(execute) and os.getenv("MARGIN_LIVE") == "1"
    def _h(self): return {"x-api-key": self.k, "content-type": "application/json"}
    def _get(self, path, params=None):
        r = self.s.get(BASE + path, params=params, headers=self._h(), timeout=self.t); r.raise_for_status(); return r.json()["result"]
    def _post_public(self, path, body):
        assert path in ("/public/dry-run", "/public/loan"), "only public calculators allowed"
        r = self.s.post(BASE + path, json=body, headers=self._h(), timeout=self.t); r.raise_for_status(); return r.json()["result"]
    def markets(self): return {m["symbol"]: m for m in self._get("/public/markets")}
    def positions(self): return self._get("/positions")
    @staticmethod
    def _body(market, side, collateral, risk_coef, open_price):
        return {"market": market, "side": side, "collateral": str(collateral), "risk_coef": str(risk_coef), "open_price": str(open_price)}
    def dry_run(self, market, side, collateral, risk_coef, open_price): return self._post_public("/public/dry-run", self._body(market, side, collateral, risk_coef, open_price))
    def loan(self, market, side, collateral, risk_coef, open_price): return self._post_public("/public/loan", self._body(market, side, collateral, risk_coef, open_price))
    def _guard(self):
        if not self.execute: raise LiveMarginDisabled("margin open/close disabled (shadow only; needs MARGIN_LIVE=1 and --execute)")
    def open_position(self, *a, **k): self._guard(); raise LiveMarginDisabled("not implemented in hackathon build")
    def close_position(self, *a, **k): self._guard(); raise LiveMarginDisabled("not implemented in hackathon build")
    def edit_position(self, *a, **k): self._guard(); raise LiveMarginDisabled("not implemented in hackathon build")

def choose_coef(collateral, min_loan=100.0, max_coef=10.0, step=0.5):
    """Smallest risk_coef (step 0.5) such that collateral*coef >= min_loan."""
    c = 1.0
    while collateral * c < min_loan - 1e-9 and c < max_coef: c += step
    return c

def trip_pnl(side, entry, exit_, loan_usdt, hold_s):
    """Net PnL (USDT) of a margin round trip on loan notional incl. trade fees both sides, open fee and 4h extend fees."""
    gross = (entry - exit_) / entry if side == "short" else (exit_ - entry) / entry
    n_ext = int(hold_s // EXTEND_S)
    return loan_usdt * (gross - 2 * TRADE_FEE - OPEN_FEE - n_ext * EXTEND_FEE)
