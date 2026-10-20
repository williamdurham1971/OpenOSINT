"""Offline tests: no network, no keys. `python btc_bot.py selftest`"""
import json, math, random, tempfile, types, urllib.parse
from pathlib import Path

import bot as B, engine as E, mexc, strategies as S


def walk(n, seed=1, drift=0.0002, vol=0.01):
    r = random.Random(seed)
    px, rows = 100.0, []
    for i in range(n):
        o = px
        px *= 1 + r.gauss(drift, vol)
        rows.append((i * 14_400_000, o, max(o, px) * 1.002, min(o, px) * 0.998, px, 1.0))
    return rows


def cols(rows):
    return [[r[i] for r in rows] for i in (1, 2, 3, 4)]


def t_signing():
    q = "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=11&recvWindow=5000&timestamp=1644489390087"
    key = "45d0b3c26f2644f19bfb98b07741b2f5"
    # MEXC's docs list two different signatures for this very input (Example 1 and 2); ours must equal Example 2.
    assert mexc.sign(key, q) == "fd3e4e8543c5188531eb7279d68ae7d26a573d0fc5ab0d18eb692451654d837a"
    # Example 3: query string and body are concatenated WITHOUT a separator
    assert mexc.sign(key, "symbol=BTCUSDT&side=BUY&type=LIMITquantity=1&price=11&recvWindow=5000&timestamp=1644489390087") \
        == "d1a676610ceb39174c8039b3f548357994b2a34139a8addd33baadba65684592"


class FakeResp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.body).encode()


def t_signed_requests():
    seen = []

    def opener(req):
        seen.append(req); return FakeResp({"ok": True})
    c = mexc.Signed("KEY", "SECRET", opener=opener, clock=lambda: 1700000000.0)
    c.market_buy_quote(123.456)
    r = seen[-1]
    assert r.get_method() == "POST" and r.headers["X-mexc-apikey"] == "KEY"
    path, query = r.full_url.split("?")
    assert path.endswith("/api/v3/order")
    base, sig = query.rsplit("&signature=", 1)
    assert sig == mexc.sign("SECRET", base)                            # signature covers everything before it
    p = dict(urllib.parse.parse_qsl(base))
    assert p["side"] == "BUY" and p["type"] == "MARKET" and p["quoteOrderQty"] == "123.46" and p["timestamp"] == "1700000000000"
    c.test_order("BUY", quoteOrderQty="5")
    assert "/api/v3/order/test" in seen[-1].full_url

    def boom(req):
        seen.append(req); raise TimeoutError("slow")
    c2 = mexc.Signed("K", "S", opener=boom)
    n0 = len(seen)
    try:
        c2.market_sell_qty(0.001)
    except RuntimeError:
        pass
    assert len(seen) - n0 == 1, "an order request must never be retried automatically"


def t_no_lookahead():
    rows = walk(2500)
    o, h, l, c = cols(rows)
    for fam, params in S.GRID:
        full = S.positions(fam, params, o, h, l, c)
        for k in (900, 1700, 2499):
            part = S.positions(fam, params, o[:k], h[:k], l[:k], c[:k])
            assert part == full[:k], f"{S.name_of(fam, params)}: signal changed when later bars were appended (look-ahead)"


def t_bot_matches_backtest():
    rows = walk(4200, seed=5)
    o, h, l, c = cols(rows)
    ret = E.returns(c)
    for strat in (("donchian", dict(n=48)), ("ema_trend", dict(fast=24, slow=200))):
        pos = S.positions(strat[0], strat[1], o, h, l, c)
        held = E.held_from_pos(pos, 0)
        held = [0] * (S.WARMUP + 1) + held[S.WARMUP + 1:]        # flat until the bot's first bar (it was not running before)
        net, trades = E.simulate(held, ret, 0.0007)
        eq_bt = 1.0
        for r in net[S.WARMUP + 1:]:
            eq_bt *= 1 + r
        with tempfile.TemporaryDirectory() as d:
            bt = B.Bot(strat, "4h", B.PaperBroker(0.0005, 0.0002), name="t", capital=1000.0, state_dir=d, log=lambda *_: None)
            bot_pos, mism = [], 0
            for t in range(S.WARMUP, len(rows)):
                res = bt.on_bar(rows[t + 1 - S.WARMUP:t + 1])
                bot_pos.append(res["position"])
                mism += res["want"] != pos[t]
            # the bot holds after bar t what the backtest holds on bar t+1
            eq_bot = bt.equity(c[-1]) / 1000.0
        assert mism <= len(rows) * 0.002, f"{strat[0]}: bot signal differs from the backtest on {mism} bars"
        assert abs(eq_bot / eq_bt - 1) < 0.002, f"{strat[0]}: bot equity {eq_bot:.4f} vs backtest {eq_bt:.4f}"


def t_paper_accounting_and_restart():
    rows = walk(1600, seed=2, drift=0.001)
    with tempfile.TemporaryDirectory() as d:
        bt = B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(0.0005, 0.0002), name="a", capital=1000.0, state_dir=d, log=lambda *_: None)
        px = rows[-1][4]
        st = dict(cash=1000.0, btc=0.0)
        f = B.PaperBroker(0.0005, 0.0002).buy(st, 100.0)
        assert abs(f["price"] - 100.02) < 1e-9 and abs(st["btc"] - 1000 * 0.9995 / 100.02) < 1e-9 and st["cash"] == 0
        B.PaperBroker(0.0005, 0.0002).sell(st, 100.0)
        assert abs(st["cash"] - 1000 * (1 - 0.0005) / 100.02 * 99.98 * (1 - 0.0005)) < 1e-9
        r1 = bt.on_bar(rows)
        assert r1 is not None and bt.on_bar(rows) is None                  # the same bar twice is a no-op
        n_trades = sum(1 for _ in open(Path(d) / "a_trades.csv")) if (Path(d) / "a_trades.csv").exists() else 1
        bt2 = B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(), name="a", capital=999.0, state_dir=d, log=lambda *_: None)
        assert bt2.state["start_equity"] == 1000.0 and bt2.on_bar(rows) is None   # restart keeps state, ignores a stale bar
        assert sum(1 for _ in open(Path(d) / "a_trades.csv")) == n_trades
        del px


def t_kill_switch_and_wait_for_signal():
    up = walk(1700, seed=3, drift=0.003, vol=0.002)
    with tempfile.TemporaryDirectory() as d:
        bt = B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(0, 0), name="k", capital=1000.0, max_drawdown=0.2, state_dir=d, log=lambda *_: None)
        r = bt.on_bar(up); assert r["position"] == 1
        crash = list(up) + [(up[-1][0] + 14_400_000, up[-1][4], up[-1][4], up[-1][4] * 0.5, up[-1][4] * 0.5, 1.0)]
        r = bt.on_bar(crash)
        assert bt.state["halted"] and r["position"] == 0 and bt.state["btc"] == 0, "kill switch must flatten and halt"
        nxt = crash + [(crash[-1][0] + 14_400_000, 1, 10 ** 6, 1, 10 ** 6, 1.0)]       # a roaring uptrend must NOT re-enter
        assert bt.on_bar(nxt)["position"] == 0
    with tempfile.TemporaryDirectory() as d:
        bt = B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(0, 0), name="w", capital=1000.0, enter_on_start=False, state_dir=d, log=lambda *_: None)
        assert bt.on_bar(up)["position"] == 0, "must sit out a trend that is already running"
        down = up + [(up[-1][0] + 14_400_000 * (i + 1), 1, 1, 1, 1.0, 1.0) for i in range(30)]
        assert bt.on_bar(down)["position"] == 0 and bt.state["armed"], "flat signal must arm the bot"
        back = down + [(down[-1][0] + 14_400_000 * (i + 1), 50, 50, 50, 50 + i, 1.0) for i in range(30)]
        assert bt.on_bar(back)["position"] == 1, "once armed, a new long signal is traded"


def t_live_broker():
    calls = []

    class Fake:
        def __init__(self): self.bal = {"USDT": 500.0, "BTC": 0.5}
        def balances(self): return dict(self.bal)
        def market_buy_quote(self, usdt):
            calls.append(("buy", usdt)); self.bal["USDT"] -= usdt; self.bal["BTC"] += usdt / 100000
        def market_sell_qty(self, q):
            calls.append(("sell", q)); self.bal["BTC"] -= q; self.bal["USDT"] += q * 100000
    fk = Fake()
    lb = B.LiveBroker(fk, max_usdt=200.0)
    B.time.sleep = lambda *_: None                                           # no real waiting in tests
    st = dict(cash=1000.0, btc=0.0)
    f = lb.buy(st, 100000.0)
    assert calls[-1] == ("buy", 200.0), "must respect --max-usdt"
    assert abs(st["btc"] - 0.002) < 1e-12 and abs(st["cash"] - 800.0) < 1e-9 and abs(f["qty"] - 0.002) < 1e-12
    lb.sell(st, 100000.0)
    assert calls[-1] == ("sell", 0.002), "must sell only what the bot bought, not the 0.5 BTC already in the account"
    assert fk.bal["BTC"] > 0.49
    try:
        B.LiveBroker(Fake(), max_usdt=1.0).buy(dict(cash=1000.0, btc=0.0), 1.0)
        raise AssertionError("buying below the minimum must fail")
    except RuntimeError:
        pass


def t_resample():
    H = 3_600_000
    rows = [(i * H, 1 + i, 2 + i, 0 + i, 1.5 + i, 1.0) for i in range(10)]
    r = S.resample(rows, 4)
    assert len(r) == 2 and r[0] == (0, 1, 5, 0, 4.5, 4.0)


def run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("t_")):
        fn()
        print(f"  ok  {name}")
    print("selftest OK")
