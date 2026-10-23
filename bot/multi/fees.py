"""Per-market fees from Wallex /v1/account/fee (needs WALLEX_API_KEY) cached to data/fees.json."""
import json, os
PATH = "data/fees.json"
def fetch_fees(client, symbols, path=PATH):
    r = client.fee()
    out = {s: {"maker": float(r[s]["makerFeeRate"]), "taker": float(r[s]["takerFeeRate"])} for s in symbols if s in r}
    json.dump(out, open(path, "w"), indent=1); return out
def load_fees(path=PATH):
    return json.load(open(path)) if os.path.exists(path) else {}
