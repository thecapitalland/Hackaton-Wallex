"""Entry point. Modes: backtest | paper (default) | live (needs LIVE=1 and --i-understand-live-risk)."""
import argparse, json, os, sys, time
from bot.config import load_config
from bot.wallex import WallexClient, TF_SECONDS

def cmd_backtest(cfg, args):
    from bot.backtest import run_backtest
    c = WallexClient()
    data = {}
    end = int(time.time())
    from bot.wallex import history
    for s in cfg.symbols:
        bars = history(c, s, cfg.timeframe, args.bars)
        data[s] = bars[:-1]  # drop forming bar
        print(f"{s}: {len(data[s])} bars {time.strftime('%Y-%m-%d %H:%M', time.gmtime(data[s][0]['t']))} -> "
              f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(data[s][-1]['t']))} UTC")
    res = run_backtest(cfg, data, {s: c.market(s) for s in cfg.symbols})
    os.makedirs("data", exist_ok=True)
    json.dump(res, open("data/backtest_result.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != "trade_list"}, indent=1))

def cmd_loop(cfg, args):
    from bot.broker import PaperBroker, LiveBroker
    from bot.graph import build_graph
    c = WallexClient()
    if cfg.mode == "live":
        if os.getenv("LIVE") != "1" or not args.i_understand_live_risk:
            sys.exit("Refusing live mode: set LIVE=1 AND pass --i-understand-live-risk")
        if not c.api_key:
            sys.exit("WALLEX_API_KEY missing")
        broker = LiveBroker(cfg, c); broker.sync_cash()
    else:
        broker = PaperBroker(cfg, c)
    app = build_graph(cfg, c, broker)
    i = 0
    while args.iterations == 0 or i < args.iterations:
        try:
            out = app.invoke({})
            print(time.strftime("%H:%M:%S"), f"equity={out['equity']:.2f} killed={out.get('killed')}",
                  [(d['symbol'], d['decision']) for d in out['decisions']])
        except Exception as e:
            print("iteration error:", repr(e))
        i += 1
        if args.iterations == 0 or i < args.iterations:
            time.sleep(args.sleep if args.sleep is not None else cfg.loop_seconds)

def cmd_shadow(cfg, args):
    """Live market data, AI selector + risk manager, decisions -> logs/decisions.jsonl. Never places orders."""
    from bot.multi.params import defaults, MARKETS
    from bot.multi.shadow import build
    p = defaults()
    if os.path.exists("data/best_params.json"): p.update(json.load(open("data/best_params.json")))
    app = build(p, MARKETS)
    i = 0
    while args.iterations == 0 or i < args.iterations:
        try:
            out = app.invoke({})
            st = out["state"]
            print(time.strftime("%H:%M:%S"), f"equity={st['equity']:,.0f} TMN open={list(st['pos'])} halted={st['halted']}",
                  [(d["market"], d["strategy"], d["confidence"]) for d in out["decisions"] if d["strategy"] != "none"])
        except Exception as e:
            print("iteration error:", repr(e))
        i += 1
        if args.iterations == 0 or i < args.iterations:
            time.sleep(args.sleep if args.sleep is not None else 300)

def cmd_shadow_usdt(cfg, args):
    """USDT-base virtual portfolio (18 USDT) on Wallex USDT pairs. Never places orders."""
    from bot.multi.shadow_usdt import build
    app = build(); i = 0
    while args.iterations == 0 or i < args.iterations:
        try:
            st = app.invoke({})["st"]
            print(time.strftime("%H:%M:%S"), f"equity={st['equity']:.4f} USDT open={list(st['pos'])} halted={st['halted']}", flush=True)
        except Exception as e:
            print("iteration error:", repr(e), flush=True)
        i += 1
        if args.iterations == 0 or i < args.iterations: time.sleep(args.sleep if args.sleep is not None else 60)

def cmd_liquidate(cfg, args):
    """Convert all non-TMN assets to TMN. DRY-RUN unless --execute AND LIVE=1."""
    from bot.multi.liquidate import run
    print(json.dumps(run(WallexClient(), execute=args.execute), indent=1, default=str))

def cmd_multi_backtest(cfg, args):
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "bot.multi.run_backtest"]))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", choices=["backtest", "paper", "live", "shadow", "shadow-usdt", "liquidate", "multi-backtest", "live-xaut", "margin-gap", "setup-trade"])
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever")
    ap.add_argument("--sleep", type=int, default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--i-understand-live-risk", action="store_true")
    ap.add_argument("--liquidate-now", action="store_true", help="live-xaut: run end-of-day liquidation immediately")
    ap.add_argument("--execute", action="store_true", help="liquidate: actually place orders (also needs LIVE=1)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.mode == "shadow": sys.exit(cmd_shadow(cfg, a))
    if a.mode == "shadow-usdt": sys.exit(cmd_shadow_usdt(cfg, a))
    if a.mode == "liquidate": sys.exit(cmd_liquidate(cfg, a))
    if a.mode == "live-xaut":
        from bot.multi.live_xaut import run as _lx
        sys.exit(_lx(execute=a.execute, iterations=a.iterations, sleep=a.sleep or 15, liq_now=a.liquidate_now))
    if a.mode == "setup-trade":
        from bot.multi.setup_trade import main as _st
        sys.exit(_st())
    if a.mode == "margin-gap":
        from bot.multi.live_margin import run as _lm
        sys.exit(_lm(iterations=a.iterations, sleep=a.sleep or 15))
    if a.mode == "multi-backtest": sys.exit(cmd_multi_backtest(cfg, a))
    if a.mode: cfg.mode = a.mode
    if cfg.mode == "live": cfg.state_path = cfg.state_path.replace(".json", "_live.json")
    (cmd_backtest if cfg.mode == "backtest" else cmd_loop)(cfg, a)
