"""AI opportunity selector. Output contract (per market):
{market, regime, strategy, confidence, size_multiplier, reason}
- RuleSelector: deterministic, replayable stand-in used in backtests.
- LLMSelector: OpenAI-compatible (BOXAPI_*), cached by prompt hash in data/llm_cache.jsonl so backtests replay
  identically; falls back to RuleSelector on error / missing creds. Its output is clamped by the risk manager."""
import hashlib, json, os
from .strategies import trend_entry, meanrev_entry
from .selector_core import classify

REGIMES = ("trend_up", "trend_down", "range", "volatile")

def rule_regime(f):
    if f["atr_pct"] > 2.0 * max(f["atr_pct_avg"], 1e-9): return "volatile"
    if f["ema20"] > f["ema50"] and f["close"] > f["ema200"] and f["ema50_slope"] > 0: return "trend_up"
    if f["ema20"] < f["ema50"] and f["ema50_slope"] < -0.002: return "trend_down"
    return "range"

class RuleSelector:
    name = "rules"
    def __init__(self, p, model=None):
        from .decision_model import DeterministicModel
        self.p = p; self.model = model or DeterministicModel()
    def decide(self, scan):
        """scan: {market: features}. Returns ranked list of decisions (only actionable ones have strategy != none)."""
        out = []
        for m, f in scan.items():
            reg, prop, why = classify(m, f, self.p)
            if not prop:
                out.append({"market": m, "regime": reg, "strategy": "none", "confidence": 0.0, "size_multiplier": 0.0,
                            "reason": why}); continue
            # confidence: trend strength / oversold depth, penalised by spread
            if prop["strategy"] in ("trend", "tmn_trend"):
                conf = 0.5 + min(0.25, f["er"] * 0.5) + min(0.25, max(0, f["adx"] - 20) / 80)
            else:
                conf = 0.5 + min(0.4, (self.p["mr_rsi"] - f["rsi"]) / 50)
            conf = max(0.0, min(1.0, conf - f["spread"] * 50))
            dm = self.model.decide(m, f, prop, rule_conf=conf)       # System-One typed decision w/ probabilities
            conf = dm.take_trade_prob
            out.append({"market": m, "regime": reg, "strategy": prop["strategy"], "confidence": round(conf, 3),
                        "size_multiplier": round(0.5 + 0.5 * conf, 3), "reason": why, "_prop": prop,
                        "regime_probs": dm.regime_probs, "allocation_bucket": dm.allocation_bucket.value,
                        "backend": dm.backend})
        return sorted(out, key=lambda d: -d["confidence"])

PROMPT = """You are the portfolio opportunity selector of a long-only SPOT crypto/commodity bot on Wallex (Iran).
Capital in Toman; goal = maximise realised return with low drawdown. For EACH market below, decide regime
(trend_up|trend_down|range|volatile) and strategy (trend|meanrev|tmn_trend|none). Your core job: keep capital in cash in choppy/flat regimes. Candidate setups were pre-computed by
rules; you may downgrade (none), lower size, or reorder, but never invent a setup that is not listed.
Reply ONLY JSON: {{"decisions":[{{"market":..., "regime":..., "strategy":..., "confidence":0-1, "size_multiplier":0-1.5, "reason":"short"}}]}}
Markets: {data}"""

def llm_client():
    """BoxAPI OpenAI-compatible client (default base https://ai.boxapi.ir/ai/v1). None if no key."""
    key = os.getenv("BOXAPI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key: return None
    from openai import OpenAI
    return OpenAI(base_url=os.getenv("BOXAPI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://ai.boxapi.ir/ai/v1",
                  api_key=key, timeout=60)

class LLMSelector(RuleSelector):
    name = "llm"
    def __init__(self, p, cache_path="data/llm_cache.jsonl", model=None, replay_only=False):
        RuleSelector.__init__(self, p); self.cache_path = cache_path; self.replay_only = replay_only
        self.model = model or os.getenv("LLM_MODEL", "gpt-5-mini"); self.cache = {}; self._cli = None
        if os.path.exists(cache_path):
            for line in open(cache_path):
                try: r = json.loads(line); self.cache[r["key"]] = r["resp"]
                except Exception: pass
    def client(self):
        if self._cli is None: self._cli = llm_client()
        return self._cli
    def decide(self, scan):
        base = super().decide(scan)
        cands = [d for d in base if d["strategy"] != "none"]
        if not cands: return base
        data = {d["market"]: {"rule": {k: d[k] for k in ("regime", "strategy", "confidence", "reason")},
                              **{k: round(v, 5) for k, v in scan[d["market"]].items()}} for d in base}
        prompt = PROMPT.format(data=json.dumps(data, sort_keys=True))
        key = hashlib.sha256((self.model + prompt).encode()).hexdigest()[:20]
        resp = self.cache.get(key)
        if resp is None and not self.replay_only and self.client() is not None:
            try:
                r = self.client().chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}],
                                                          response_format={"type": "json_object"})
                resp = json.loads(r.choices[0].message.content)
                with open(self.cache_path, "a") as fh: fh.write(json.dumps({"key": key, "resp": resp}) + "\n")
                self.cache[key] = resp
            except Exception as e:
                for d in base: d["reason"] += f" | llm_error:{type(e).__name__}"
                return base
        if resp is None: return base
        return merge(base, resp)

def merge(base, resp):
    """Apply LLM decisions to rule candidates: LLM can veto, resize (0-1.5) and re-rank; cannot add setups."""
    by = {d.get("market"): d for d in resp.get("decisions", []) if isinstance(d, dict)}
    out = []
    for d in base:
        l = by.get(d["market"])
        if l and d["strategy"] != "none":
            if l.get("strategy") != d["strategy"]:
                d = {**d, "strategy": "none", "size_multiplier": 0.0, "reason": "LLM veto: " + str(l.get("reason", ""))}
            else:
                d = {**d, "regime": l.get("regime", d["regime"]) if l.get("regime") in REGIMES else d["regime"],
                     "confidence": float(min(1, max(0, l.get("confidence", d["confidence"])))),
                     "size_multiplier": float(min(1.5, max(0, l.get("size_multiplier", d["size_multiplier"])))),
                     "reason": "LLM: " + str(l.get("reason", "")) + " | rules: " + d["reason"]}
        out.append({**d, "source": "llm" if l else "rules"})
    return sorted(out, key=lambda d: -d["confidence"])
