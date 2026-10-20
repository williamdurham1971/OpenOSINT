#!/usr/bin/env python3
"""BTC/USDT spot bot for MEXC: research, paper trading (default) and guarded live trading.

  python btc_bot.py fetch                 # download candle history (public data, no key)
  python btc_bot.py research              # does any textbook TA beat random timing after fees? (the honest test)
  python btc_bot.py cross                 # the frozen strategy on other coins it has never seen
  python btc_bot.py paper                 # run the bot with simulated money (default: 4h EMA 24/200 trend filter)
  python btc_bot.py status                # where is the paper bot
  python btc_bot.py live-check            # validate API keys with a TEST order (no real order is placed)
  python btc_bot.py live ...              # real money; needs many explicit flags
"""
import argparse, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as B, data, engine as E, mexc, research, strategies as S

DEFAULT = "ema_trend:fast=24,slow=200"


def parse_strategy(spec):
    fam, _, rest = spec.partition(":")
    if fam not in S.FUNCS:
        sys.exit(f"unknown strategy family {fam!r}; choose from {', '.join(S.FUNCS)}")
    params = {}
    for kv in filter(None, rest.split(",")):
        k, v = kv.split("=")
        params[k] = int(v)
    return fam, params


def build_bot(a, broker, name):
    return B.Bot(parse_strategy(a.strategy), a.interval, broker, name=name, capital=a.capital, max_drawdown=a.max_drawdown,
                 enter_on_start=not a.wait_for_signal)


def need_keys():
    k, s = os.environ.get("MEXC_API_KEY"), os.environ.get("MEXC_API_SECRET")
    if not k or not s:
        sys.exit("set MEXC_API_KEY and MEXC_API_SECRET in the environment (create a key with SPOT trading only, withdrawals OFF, "
                 "restricted to your IP)")
    return mexc.Signed(k, s)


def cmd_live_check(a):
    c = need_keys()
    bal = c.balances()
    print(f"keys accepted. USDT free: {bal.get('USDT', 0):.2f}   BTC free: {bal.get('BTC', 0):.6f}")
    r = c.test_order("BUY", quoteOrderQty="5")
    print("test order (validated by MEXC, NOT executed):", r)
    print("OK. Nothing was traded. Before going live: withdrawals disabled on the key, IP restricted, small --max-usdt.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fetch"); f.add_argument("--interval", default="60m", choices=list(mexc.TF_SECONDS)); f.add_argument("--symbol", default="BTCUSDT")
    r = sp.add_parser("research"); r.add_argument("--delay", type=int, default=0); r.add_argument("--fee", type=float, default=E.FEE)
    r.add_argument("--slip", type=float, default=E.SLIP); r.add_argument("--shifts", type=int, default=400)
    x = sp.add_parser("cross"); x.add_argument("--strategy", default=DEFAULT); x.add_argument("--delay", type=int, default=0)
    x.add_argument("--fee", type=float, default=E.FEE); x.add_argument("--slip", type=float, default=E.SLIP); x.add_argument("--shifts", type=int, default=400)
    x.add_argument("--symbols", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LTCUSDT", "LINKUSDT", "TRXUSDT"])
    for nm in ("paper", "live"):
        p = sp.add_parser(nm)
        p.add_argument("--strategy", default=DEFAULT, help=f"family:param=value,... (default {DEFAULT})")
        p.add_argument("--interval", default="4h", choices=["60m", "4h"])
        p.add_argument("--capital", type=float, default=1000.0, help="starting USDT (paper) / ceiling the bot may think it has (live)")
        p.add_argument("--max-drawdown", type=float, default=None, help="kill switch: flatten and halt if equity falls this fraction below its peak, e.g. 0.35")
        p.add_argument("--wait-for-signal", action="store_true", help="do not join a trend that is already running at start-up")
        p.add_argument("--once", action="store_true", help="process the latest closed bar and exit")
        p.add_argument("--name", default=nm)
    lv = sp.choices["live"]
    lv.add_argument("--max-usdt", type=float, required=True, help="hard cap on the USDT the bot may ever spend on one buy")
    lv.add_argument("--yes-i-risk-real-money", action="store_true")
    st = sp.add_parser("status"); st.add_argument("--name", default="paper")
    sp.add_parser("live-check"); sp.add_parser("selftest")
    a = ap.parse_args()

    if a.cmd == "selftest":
        import selftest; selftest.run()
    elif a.cmd == "fetch":
        rows = data.fetch(a.interval, symbol=a.symbol)
        print(f"{len(rows)} {a.interval} candles for {a.symbol} cached in {data.path(a.interval, a.symbol)}")
    elif a.cmd == "research":
        research.research(a)
    elif a.cmd == "cross":
        fam, params = parse_strategy(a.strategy)
        for s in a.symbols:
            if len(data.load("4h", s)) < 3000:
                print(f"downloading {s} 4h ..."); data.fetch("4h", symbol=s, verbose=False)
        research.cross_asset(a, a.symbols, fam, params)
    elif a.cmd == "paper":
        b = build_bot(a, B.PaperBroker(E.FEE, E.SLIP), a.name)
        B.run_forever(b, once=a.once)
    elif a.cmd == "status":
        b = B.Bot(parse_strategy(DEFAULT), "4h", B.PaperBroker(), name=a.name)
        print(b.status())
    elif a.cmd == "live-check":
        cmd_live_check(a)
    elif a.cmd == "live":
        if not a.yes_i_risk_real_money:
            sys.exit("refusing to trade real money without --yes-i-risk-real-money")
        if a.max_drawdown is None:
            sys.exit("live mode requires --max-drawdown (kill switch), e.g. --max-drawdown 0.35")
        c = need_keys()
        cmd_live_check(a)
        a.capital = min(a.capital, a.max_usdt)
        b = build_bot(a, B.LiveBroker(c, a.max_usdt), a.name)
        B.run_forever(b, once=a.once)


if __name__ == "__main__":
    main()
