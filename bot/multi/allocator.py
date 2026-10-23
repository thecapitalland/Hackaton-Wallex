"""Allocator node: decides the TMN / USDT / gold split from the Toman trend and re-ranks opportunities.
Output JSON: {"usdt_share", "gold_share", "tmn_share", "ranked": [...markets], "reason"}.
Deterministic stand-in (default, replayable) + LLM path (gpt-5-mini via BoxAPI) with prompt-hash cache."""
import hashlib, json, os

def _trend_score(f):
    if not f: return 0
    s = 0
    if f["ema50"] > f["ema200"]: s += 1
    if f["close"] > f["ema50"]: s += 1
    if f.get("htf_up"): s += 1
    return s  # 0..3

class Allocator:
    name = "rules"
    def __init__(self, p): self.p = p
    def decide(self, usdt_f, gold_f, decisions):
        su, sg = _trend_score(usdt_f), _trend_score(gold_f)
        usdt = {3: 0.6, 2: 0.35, 1: 0.15, 0: 0.05}[su]
        gold = {3: 0.2, 2: 0.1}.get(sg, 0.0)
        ranked = [d["market"] for d in sorted(decisions, key=lambda d: -d.get("confidence", 0)) if d.get("strategy") != "none"]
        return {"usdt_share": usdt, "gold_share": gold, "tmn_share": round(1 - usdt - gold, 3), "ranked": ranked,
                "reason": f"USDT/TMN trend score {su}/3, XAUT/TMN trend score {sg}/3", "source": "rules"}

PROMPT = """You allocate a 5M Toman long-only spot portfolio on Wallex between TMN cash, USDT and gold (XAUT).
Toman tends to weaken in trends; protect value but avoid churn (conversion fee 0.3%/side).
Rule baseline: {base}. USDTTMN features: {u}. XAUTTMN features: {g}. Candidate trades: {c}.
Reply ONLY JSON {{"usdt_share":0-0.9,"gold_share":0-0.4,"ranked":[markets subset of candidates],"reason":"short"}}"""

class LLMAllocator(Allocator):
    name = "llm"
    def __init__(self, p, cache_path="data/llm_cache_alloc.jsonl", model=None):
        super().__init__(p); self.cache_path = cache_path; self.model = model or os.getenv("LLM_MODEL", "gpt-5-mini")
        self.cache = {}; self.spend = 0.0
        if os.path.exists(cache_path):
            for l in open(cache_path):
                r = json.loads(l); self.cache[r["key"]] = r["resp"]
    def decide(self, usdt_f, gold_f, decisions):
        base = super().decide(usdt_f, gold_f, decisions)
        rnd = lambda f: {k: round(v, 4) for k, v in (f or {}).items() if k in ("close", "ema50", "ema200", "er_long", "adx", "htf_up", "ret_24")}
        c = [{k: d[k] for k in ("market", "strategy", "confidence")} for d in decisions if d.get("strategy") != "none"]
        prompt = PROMPT.format(base=json.dumps({k: base[k] for k in ("usdt_share", "gold_share")}), u=json.dumps(rnd(usdt_f)),
                               g=json.dumps(rnd(gold_f)), c=json.dumps(c))
        key = hashlib.sha256((self.model + prompt).encode()).hexdigest()[:20]
        resp = self.cache.get(key)
        if resp is None:
            from .selector import llm_client
            cli = llm_client()
            if cli is None: return base
            try:
                r = cli.chat.completions.create(model=self.model, messages=[{"role": "user", "content": prompt}],
                                                response_format={"type": "json_object"})
                resp = json.loads(r.choices[0].message.content); self.spend += float(getattr(r.usage, "cost", 0) or 0)
                with open(self.cache_path, "a") as fh: fh.write(json.dumps({"key": key, "resp": resp}) + "\n")
                self.cache[key] = resp
            except Exception as e:
                return {**base, "reason": base["reason"] + f" | llm_error:{type(e).__name__}"}
        u = min(0.9, max(0.0, float(resp.get("usdt_share", base["usdt_share"]))))
        g = min(0.4, max(0.0, float(resp.get("gold_share", base["gold_share"]))))
        if u + g > 0.95: g = 0.95 - u
        cand = set(base["ranked"]); ranked = [m for m in resp.get("ranked", []) if m in cand] + [m for m in base["ranked"] if m not in resp.get("ranked", [])]
        return {"usdt_share": u, "gold_share": g, "tmn_share": round(1 - u - g, 3), "ranked": ranked,
                "reason": "LLM: " + str(resp.get("reason", "")), "source": "llm"}
