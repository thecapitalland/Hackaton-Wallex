"""LLM regime classifier with rule-based fallback. The LLM can only VETO or approve; it never sizes."""
import os, json
from .signals import rule_regime, rule_gate

_client = None
def _llm():
    global _client
    if _client is None and os.getenv("BOXAPI_BASE_URL") and os.getenv("BOXAPI_API_KEY"):
        from openai import OpenAI
        _client = OpenAI(base_url=os.getenv("BOXAPI_BASE_URL"), api_key=os.getenv("BOXAPI_API_KEY"), timeout=30)
    return _client

PROMPT = """You are a crypto market regime analyst for an aggressive but risk-controlled spot (long-only) bot.
Given features for {sym} on {tf} candles, classify regime as one of trend_up, trend_down, range, volatile,
and decide whether to APPROVE or VETO the proposed technical entry. Reply ONLY JSON:
{{"regime": "...", "approve": true/false, "confidence": 0-1, "reason": "short"}}
Features: {feat}
Recent closes (oldest->newest): {closes}
Proposed signal: {sig}
Rule-based regime: {rule}"""

def classify(sym, tf, feats, sig, candles, use_llm=True):
    rule = rule_regime(feats)
    gate_ok, gate_reason = rule_gate(rule, sig)
    base = {"regime": rule, "approve": gate_ok, "source": "rules", "reason": gate_reason, "confidence": 0.5}
    if sig["action"] != "BUY" or not use_llm or _llm() is None:
        return base
    try:
        msg = PROMPT.format(sym=sym, tf=tf, feat=json.dumps({k: round(v, 6) for k, v in feats.items()}),
                            closes=[round(b["c"], 4) for b in candles[-24:]], sig=json.dumps(sig), rule=rule)
        r = _llm().chat.completions.create(model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
                                           messages=[{"role": "user", "content": msg}], temperature=0)
        txt = r.choices[0].message.content.strip().strip("`")
        txt = txt[txt.find("{"): txt.rfind("}") + 1]
        out = json.loads(txt)
        regime = out.get("regime", rule)
        # LLM can only VETO: final approval requires both rules gate and LLM approval
        approve = bool(out.get("approve")) and gate_ok
        return {"regime": regime, "approve": approve, "source": "llm", "confidence": out.get("confidence"),
                "reason": out.get("reason", ""), "rule_regime": rule}
    except Exception as e:
        base["reason"] += f" (llm error: {type(e).__name__})"
        return base
