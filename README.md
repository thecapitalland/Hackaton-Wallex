# wallex-agent-bot

This bot was built for the Wallex challenge of the Starcoach Agentic AI hackathon.

It is an agentic LangGraph trading bot for Wallex that runs **Wallex vs global (Binance) gap arbitrage** with a two-sided inventory:
- XAUTUSDT trades live.
- BTCUSDT and ETHUSDT run in shadow.
- A gpt-5-mini regime node sets the inventory.
- A deterministic risk veto approves or blocks each trade.
- Every decision is logged as JSON.

- **Full submission document:** [STRATEGY.md](STRATEGY.md). It covers the logic, AI use, risk, re-running, backtests/shadow, log format and limitations.
- **Persian summary:** [SUBMISSION_FA.md](SUBMISSION_FA.md)

## Quick start
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pandas numpy pytest
cp .env.example .env   # set WALLEX_API_KEY (+ BOXAPI_API_KEY); .env is git-ignored
.venv/bin/pytest -q tests
.venv/bin/python main.py live-xaut --iterations 4      # dry-run (default)
LIVE=1 ./run_live_xaut.sh --execute                     # real orders: needs --execute AND LIVE=1
.venv/bin/python tools/report_live.py                   # performance report (markdown)
```

## Layout
| Path | Contents |
|---|---|
| `bot/multi/live_xaut.py` | final gap/inventory agent (LangGraph) |
| `bot/multi/decision_model.py` | typed LLM / deterministic decision models |
| `bot/multi/liquidate.py` | asset → TMN liquidation (dry-run by default) |
| `bot/multi/funding.py` | TMN → USDT funding step |
| `research/` | replay, gold walk-forward, transfer study, GitHub strategy study |
| `data/research_*.json`, `data/replay_gap*.csv` | research results |
| `logs/*.jsonl` | decision logs (deliverable; not committed by default) |

Other modes (`backtest`, `paper`, `shadow`, `shadow-usdt`, `multi-backtest`, `liquidate`) are from earlier research iterations; see `python main.py -h`.
