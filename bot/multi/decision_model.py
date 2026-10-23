"""'System One' provider-agnostic decision model.
Input: structured market state (features dict). Output: typed decision with calibrated probabilities.
Downstream (selector/risk) uses probabilities: size_multiplier = 0.5 + 0.5 * p(take_trade); trade only if p >= min_confidence.
Backends: DeterministicModel (default, replayable), OpenAICompatModel (BoxAPI gpt-5-mini, JSON schema, cached),
TypeSafeJevModel (placeholder, not callable yet)."""
import hashlib, json, math, os
from dataclasses import dataclass, asdict, field
from enum import Enum

class Regime(str, Enum): trend_up = "trend_up"; range = "range"; trend_down = "trend_down"
class Bucket(str, Enum): tmn_heavy = "tmn_heavy"; balanced = "balanced"; usdt_heavy = "usdt_heavy"
BUCKET_USDT = {Bucket.tmn_heavy: 0.1, Bucket.balanced: 0.35, Bucket.usdt_heavy: 0.6}

@dataclass
class Decision:
    market: str
    regime: Regime
    regime_probs: dict
    take_trade_prob: float
    allocation_bucket: Bucket
    allocation_probs: dict
    reason: str = ""
    backend: str = "deterministic"
    def to_json(self): d = asdict(self); d["regime"] = self.regime.value; d["allocation_bucket"] = self.allocation_bucket.value; return d

SCHEMA = {"name": "decision", "strict": True, "schema": {"type": "object", "additionalProperties": False,
    "required": ["regime_probs", "take_trade_prob", "allocation_probs", "reason"], "properties": {
    "regime_probs": {"type": "object", "additionalProperties": False, "required": [r.value for r in Regime],
                     "properties": {r.value: {"type": "number"} for r in Regime}},
    "take_trade_prob": {"type": "number"},
    "allocation_probs": {"type": "object", "additionalProperties": False, "required": [b.value for b in Bucket],
                         "properties": {b.value: {"type": "number"} for b in Bucket}},
    "reason": {"type": "string"}}}}

def _norm(d):
    d = {k: max(0.0, float(v)) for k, v in d.items()}; s = sum(d.values()) or 1.0
    return {k: round(v / s, 4) for k, v in d.items()}

def _softmax(scores, temp=1.0):
    m = max(scores.values()); e = {k: math.exp((v - m) / temp) for k, v in scores.items()}; return _norm(e)

class DecisionModel:
    name = "base"
    def decide(self, market, state, proposal=None) -> Decision: raise NotImplementedError

class DeterministicModel(DecisionModel):
    """Calibrated-ish probabilities from features via softmax over hand-built scores (replayable)."""
    name = "deterministic"
    def decide(self, market, f, proposal=None, rule_conf=None):
        trend = (f["ema20"] > f["ema50"]) + (f["close"] > f["ema200"]) + (f["ema50_slope"] > 0) + f.get("htf_up", 0)
        chop = (f["er"] < 0.3) + (f["adx"] < 20) + (f.get("bbw", 1) < 0.01)
        rp = _softmax({"trend_up": trend - chop * 0.5, "range": chop, "trend_down": (4 - trend) - chop * 0.5}, 1.0)
        tp = 0.0 if proposal is None else (rule_conf if rule_conf is not None else rp["trend_up"])
        ap = _softmax({"usdt_heavy": trend - 2, "balanced": 0.5, "tmn_heavy": 2 - trend}, 1.0)
        return Decision(market, Regime(max(rp, key=rp.get)), rp, round(tp, 4), Bucket(max(ap, key=ap.get)), ap,
                        "deterministic stand-in", self.name)

class OpenAICompatModel(DecisionModel):
    name = "openai_compat"
    def __init__(self, model=None, cache_path="data/llm_cache_dm.jsonl"):
        self.model = model or os.getenv("LLM_MODEL", "gpt-5-mini"); self.cache_path = cache_path; self.cache = {}
        self.fallback = DeterministicModel(); self.spend = 0.0
        if os.path.exists(cache_path):
            for l in open(cache_path): r = json.loads(l); self.cache[r["key"]] = r["resp"]
    def decide(self, market, f, proposal=None, rule_conf=None):
        state = {"market": market, "features": {k: round(v, 5) for k, v in f.items()},
                 "proposal": {k: v for k, v in (proposal or {}).items() if k in ("strategy", "reason")}}
        prompt = ("Long-only spot trading decision. Given market state JSON, output calibrated probabilities "
                  "(each group sums to 1). take_trade_prob=0 if no proposal. State: " + json.dumps(state, sort_keys=True))
        key = hashlib.sha256((self.model + prompt).encode()).hexdigest()[:20]
        resp = self.cache.get(key)
        if resp is None:
            from .selector import llm_client
            cli = llm_client()
            if cli is None: return self.fallback.decide(market, f, proposal, rule_conf)
            try:
                r = cli.chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}],
                                                response_format={"type": "json_schema", "json_schema": SCHEMA})
                resp = json.loads(r.choices[0].message.content); self.spend += float(getattr(r.usage, "cost", 0) or 0)
                with open(self.cache_path, "a") as fh: fh.write(json.dumps({"key": key, "resp": resp}) + "\n")
                self.cache[key] = resp
            except Exception as e:
                d = self.fallback.decide(market, f, proposal, rule_conf); d.reason += f" | llm_error:{type(e).__name__}"; return d
        rp, ap = _norm(resp["regime_probs"]), _norm(resp["allocation_probs"])
        tp = 0.0 if proposal is None else min(1.0, max(0.0, float(resp["take_trade_prob"])))
        return Decision(market, Regime(max(rp, key=rp.get)), rp, tp, Bucket(max(ap, key=ap.get)), ap, resp.get("reason", ""), self.name)

class TypeSafeJevModel(DecisionModel):
    """Placeholder for the TypeSafe Jev backend (typed decision service). Not callable yet."""
    name = "typesafe_jev"
    def decide(self, market, f, proposal=None, rule_conf=None):
        raise NotImplementedError("TypeSafe Jev backend not available yet; use DeterministicModel or OpenAICompatModel")

def get_model(name=None):
    name = name or os.getenv("DECISION_BACKEND", "deterministic")
    return {"deterministic": DeterministicModel, "openai_compat": OpenAICompatModel, "typesafe_jev": TypeSafeJevModel}[name]()
