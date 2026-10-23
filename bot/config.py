import os, yaml
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    symbols: list = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    timeframe: str = "240"           # Wallex udf resolution (minutes or 1D)
    lookback_bars: int = 300
    mode: str = "paper"              # backtest | paper | live
    loop_seconds: int = 300
    start_equity_usdt: float = 1000.0   # paper/backtest starting equity
    quote: str = "USDT"
    risk_per_trade: float = 0.025    # fraction of equity risked to stop
    max_position_frac: float = 0.6   # cap notional per position (of equity)
    max_open_positions: int = 2
    max_daily_drawdown: float = 0.15 # kill switch
    stop_atr: float = 3.0
    tp_atr: float = 4.5
    trail_atr: float = 3.0
    trail_activate_atr: float = 2.0
    fee_rate: float = 0.002          # per side, conservative estimate
    limit_offset_bps: float = 5.0    # LIMIT price improvement vs mid toward aggressive fill
    use_llm: bool = True
    log_path: str = "data/decisions.jsonl"
    state_path: str = "data/state.json"

def _tf(v):
    return str(v)

def load_config(path: str = None) -> Config:
    path = path or os.getenv("BOT_CONFIG", "config.yaml")
    data = {}
    if os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    cfg = Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    env_map = {"SYMBOLS": "symbols", "TIMEFRAME": "timeframe", "MODE": "mode", "RISK_PER_TRADE": "risk_per_trade",
               "MAX_DAILY_DRAWDOWN": "max_daily_drawdown", "MAX_OPEN_POSITIONS": "max_open_positions",
               "LOOP_SECONDS": "loop_seconds", "START_EQUITY_USDT": "start_equity_usdt"}
    for env, attr in env_map.items():
        v = os.getenv(env)
        if v:
            cur = getattr(cfg, attr)
            if attr == "symbols":
                v = [s.strip().upper() for s in v.split(",") if s.strip()]
            elif isinstance(cur, bool):
                v = v.lower() in ("1", "true", "yes")
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            setattr(cfg, attr, v)
    cfg.timeframe = _tf(cfg.timeframe)
    return cfg
