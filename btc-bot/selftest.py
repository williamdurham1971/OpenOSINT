"""Offline tests: no network, no keys. `python btc_bot.py selftest`"""
import json, math, random, tempfile, types, urllib.parse
from pathlib import Path

import bot as B, engine as E, mexc, patterns as PT, strategies as S


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


def shape_path(points, wiggle=0.15):
    c=[]
    for (i0,p0),(i1,p1) in zip(points,points[1:]):
        for i in range(i0,i1): c.append(p0+(p1-p0)*(i-i0)/(i1-i0))
    c.append(points[-1][1]); o=[c[0]]+c[:-1]
    return o,[max(a,b)+wiggle for a,b in zip(o,c)],[min(a,b)-wiggle for a,b in zip(o,c)],c
SHAPES={
 "falling_wedge":([(0,100),(10,110),(20,100),(32,106),(44,98),(56,102),(68,97.5),(78,101),(86,106)],1),
 "rising_wedge": ([(0,100),(10,90),(20,100),(32,94),(44,102),(56,97),(68,103),(78,99),(86,92)],-1),
 "asc_triangle": ([(0,100),(10,110),(20,100),(32,110),(44,104),(56,110),(66,106),(76,111),(84,116)],1),
 "desc_triangle":([(0,110),(10,100),(20,110),(32,100),(44,106),(56,100),(66,103),(76,99),(84,94)],-1),
 "sym_triangle": ([(0,100),(10,112),(20,100),(32,108),(44,102),(56,106),(66,103),(76,107),(86,112)],1),
 "double_bottom":([(0,110),(14,100),(30,110),(46,100.2),(60,110),(70,116)],1),
 "double_top":   ([(0,90),(14,100),(30,90),(46,99.8),(60,90),(70,84)],-1),
 "inv_head_shoulders":([(0,110),(12,100),(24,108),(36,95),(48,108),(60,100),(72,110),(82,117)],1),
 "head_shoulders":    ([(0,90),(12,100),(24,92),(36,105),(48,92),(60,100),(72,90),(82,83)],-1),
 "bull_flag":([(0,100),(12,125),(24,115),(36,122),(46,114),(58,128),(66,134)],1),
 "bear_flag":([(0,125),(12,100),(24,110),(36,103),(46,111),(58,97),(66,91)],-1),
}


def t_patterns_fire_on_their_shapes():
    for name, (pts, d) in SHAPES.items():
        o, h, l, c = shape_path(pts)
        sg = PT.detect(o, h, l, c)
        assert any(x.name == name and x.dir == d for x in sg), f"{name} not detected: {[(x.name, x.dir, x.t) for x in sg]}"
        assert len({(x.name, x.dir, x.t) for x in sg}) == len(sg), f"{name}: duplicate signals for one breakout"
        mine = next(x for x in sg if x.name == name and x.dir == d)
        assert (mine.stop < mine.entry < mine.target) if d == 1 else (mine.target < mine.entry < mine.stop), f"{name}: stop/target on the wrong side"


def t_patterns_quiet_on_a_straight_line():
    o, h, l, c = shape_path([(0, 100), (200, 150)])
    assert PT.detect(o, h, l, c) == []


def t_patterns_causal():
    rows = walk(2500, seed=11, vol=0.012)
    o, h, l, c = cols(rows)
    full = PT.detect(o, h, l, c)
    assert len(full) > 15, "random-walk data should produce some pattern signals"
    key = lambda x: (x.name, x.dir, x.t, round(x.entry, 9), round(x.stop, 9), round(x.target, 9))
    # cut the history exactly at each signal's bar: the signal must still be there, and must not exist one bar earlier
    for sg in full[:40]:
        k = sg.t + 1
        with_bar = {key(x) for x in PT.detect(o[:k], h[:k], l[:k], c[:k])}
        assert key(sg) in with_bar, f"{sg.name} at bar {sg.t} depends on bars after it (look-ahead)"
        before = {(x.name, x.dir, x.t) for x in PT.detect(o[:sg.t], h[:sg.t], l[:sg.t], c[:sg.t])}
        assert (sg.name, sg.dir, sg.t) not in before
    for k in (900, 1700):                                      # and an arbitrary cut changes nothing earlier
        want = [key(x) for x in full if x.t < k - 1]
        got = [key(x) for x in PT.detect(o[:k], h[:k], l[:k], c[:k]) if x.t < k - 1]
        assert got == want


def t_pattern_strategies_causal():
    rows = walk(2200, seed=13, vol=0.012)
    o, h, l, c = cols(rows)
    for fam in ("pattern_all", "pattern_fw"):
        full = S.positions(fam, dict(mult10=30, hold=72), o, h, l, c)
        assert sum(full) > 0, f"{fam} never entered on random-walk data"
        for k in (700, 1300, 2100):
            assert S.positions(fam, dict(mult10=30, hold=72), o[:k], h[:k], l[:k], c[:k]) == full[:k], f"{fam}: look-ahead"


def t_manual_control_and_alerts():
    up = walk(1700, seed=3, drift=0.003, vol=0.002)
    with tempfile.TemporaryDirectory() as d:
        bt = B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(0, 0), name="c", capital=1000.0, state_dir=d, log=lambda *_: None)
        json.dump({"mode": "pause"}, open(Path(d) / "c_control.json", "w"))
        assert bt.on_bar(up)["position"] == 0, "pause must not open a new position"
        json.dump({"mode": "auto"}, open(Path(d) / "c_control.json", "w"))
        nxt = up + [(up[-1][0] + 14_400_000, up[-1][4], up[-1][4] * 1.001, up[-1][4], up[-1][4] * 1.001, 1.0)]
        assert bt.on_bar(nxt)["position"] == 1
        json.dump({"mode": "flat"}, open(Path(d) / "c_control.json", "w"))
        jump = nxt + [(nxt[-1][0] + 14_400_000, nxt[-1][4], nxt[-1][4] * 1.3, nxt[-1][4], nxt[-1][4] * 1.3, 1.0)]
        assert bt.on_bar(jump)["position"] == 0, "flat must close the position even in an uptrend"
        alerts = (Path(d) / "c_alerts.csv").read_text()
        assert "ABNORMAL MOVE" in alerts and "+30.00" in alerts, "a +30% bar must raise an abnormal-move alert"


def t_lab_runs_several_bots():
    rows = walk(1600, seed=4)
    with tempfile.TemporaryDirectory() as d:
        bots = [B.Bot(("tsmom", dict(lookback=24)), "4h", B.PaperBroker(), name="x", state_dir=d, log=lambda *_: None),
                B.Bot(("donchian", dict(n=24)), "60m", B.PaperBroker(), name="y", state_dir=d, log=lambda *_: None)]
        calls = []
        B.run_lab(bots, once=True, fetch=lambda iv: calls.append(iv) or rows)
        assert sorted(calls) == ["4h", "60m"] and all(b.state["last_bar"] == rows[-1][0] for b in bots)


def run():
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("t_")):
        fn()
        print(f"  ok  {name}")
    print("selftest OK")
