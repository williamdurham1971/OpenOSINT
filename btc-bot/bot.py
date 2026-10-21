"""The trading bot: paper (default) or live, one decision per closed bar, same strategy code as the backtest.

State machine: on every newly CLOSED bar compute the strategy's desired position (0 flat / 1 long) from the last
WARMUP closed bars, and trade only if it differs from what the bot holds. Idempotent: a restart never repeats a bar.
"""
import csv, json, math, os, time
from pathlib import Path

import data, mexc, strategies as S

STATE_DIR = Path(__file__).parent / "data"


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


# ---------------- brokers ----------------

class PaperBroker:
    """Simulated fills at the bar-close price with slippage against us plus the taker fee. No network."""
    def __init__(self, fee=0.0005, slip=0.0002):
        self.fee, self.slip = fee, slip

    def buy(self, state, price):
        px = price * (1 + self.slip)
        qty = state["cash"] * (1 - self.fee) / px
        spent, state["cash"], state["btc"] = state["cash"], 0.0, state["btc"] + qty
        return dict(side="BUY", price=px, qty=qty, usdt=spent)

    def sell(self, state, price):
        px = price * (1 - self.slip)
        qty = state["btc"]
        got = qty * px * (1 - self.fee)
        state["cash"], state["btc"] = state["cash"] + got, 0.0
        return dict(side="SELL", price=px, qty=qty, usdt=got)


class LiveBroker:
    """Real MEXC spot market orders. Sells ONLY the BTC this bot bought (state['btc']), never the whole account."""
    MIN_USDT = 5.0

    def __init__(self, client, max_usdt, lot=0.000001):
        self.c, self.max_usdt, self.lot = client, max_usdt, lot

    def buy(self, state, price):
        bal = self.c.balances()
        usdt = min(bal.get("USDT", 0.0), self.max_usdt, state["cash"])
        if usdt < self.MIN_USDT:
            raise RuntimeError(f"not enough USDT to buy: {usdt:.2f} (< {self.MIN_USDT})")
        before = bal.get("BTC", 0.0)
        self.c.market_buy_quote(usdt)
        time.sleep(1.5)
        after = self.c.balances()
        qty = max(0.0, after.get("BTC", 0.0) - before)
        spent = max(0.0, bal.get("USDT", 0.0) - after.get("USDT", 0.0))
        state["btc"] += qty
        state["cash"] = max(0.0, state["cash"] - spent)
        return dict(side="BUY", price=(spent / qty if qty else price), qty=qty, usdt=spent)

    def sell(self, state, price):
        free = self.c.balances().get("BTC", 0.0)
        qty = math.floor(min(state["btc"], free) / self.lot) * self.lot
        if qty <= 0:
            raise RuntimeError("no BTC to sell")
        usdt_before = self.c.balances().get("USDT", 0.0)
        self.c.market_sell_qty(qty)
        time.sleep(1.5)
        got = max(0.0, self.c.balances().get("USDT", 0.0) - usdt_before)
        state["btc"] = max(0.0, state["btc"] - qty)
        state["cash"] += got
        return dict(side="SELL", price=(got / qty if qty else price), qty=qty, usdt=got)


# ---------------- the bot ----------------

class Bot:
    def __init__(self, strategy, interval, broker, name="paper", capital=1000.0, max_drawdown=None, enter_on_start=True,
                 state_dir=STATE_DIR, log=print):
        self.fam, self.params = strategy
        self.interval, self.broker, self.name = interval, broker, name
        self.max_dd, self.enter_on_start, self.log = max_drawdown, enter_on_start, log
        self.dir = Path(state_dir); self.dir.mkdir(exist_ok=True, parents=True)
        self.sp = self.dir / f"{name}_state.json"
        self.state = json.load(open(self.sp)) if self.sp.exists() else dict(
            cash=float(capital), btc=0.0, last_bar=None, start_equity=float(capital), start_price=None,
            peak=float(capital), halted=False, armed=bool(enter_on_start), started=now_s())
        self.step = mexc.TF_SECONDS[interval] * 1000

    # -- persistence / logging
    def _save(self):
        tmp = self.sp.with_suffix(".tmp")
        json.dump(self.state, open(tmp, "w"), indent=1)
        os.replace(tmp, self.sp)

    def _append(self, fname, header, row):
        p = self.dir / f"{self.name}_{fname}"
        new = not p.exists()
        with open(p, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(header)
            w.writerow(row)

    def control(self):
        """Manual override written by `btc_bot.py control`: auto (default) | pause | flat."""
        p = self.dir / f"{self.name}_control.json"
        try:
            mode = json.load(open(p)).get("mode", "auto")
        except (OSError, ValueError):
            return "auto"
        return mode if mode in ("auto", "pause", "flat") else "auto"

    def _alert_if_abnormal(self, rows, z_limit=4.0, window=200):
        """A bar move beyond z_limit standard deviations of recent bars is flagged: usually news. Check before trusting TA."""
        c = [r[4] for r in rows[-(window + 2):]]
        if len(c) < 50:
            return None
        rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        last, past = rets[-1], rets[:-1]
        mu = sum(past) / len(past)
        sd = (sum((x - mu) ** 2 for x in past) / len(past)) ** 0.5
        if sd <= 0 or abs(last - mu) / sd < z_limit:
            return None
        z = (last - mu) / sd
        msg = f"ABNORMAL MOVE: last {self.interval} bar {last*100:+.2f}% ({z:+.1f} sigma). Likely news/catalyst: check before trusting the signal."
        self._append("alerts.csv", ["time_utc", "bar_open_utc", "z", "move_pct", "message"],
                     [now_s(), time.strftime("%Y-%m-%d %H:%M", time.gmtime(rows[-1][0] / 1000)), f"{z:+.1f}", f"{last*100:+.2f}", msg])
        self.log(f"[{now_s()}] {msg}")
        return z

    def _log_trade(self, ts, fill, eq):
        self._append("trades.csv", ["time_utc", "bar_open_utc", "side", "price", "qty", "usdt", "equity_after", "halted"],
                     [now_s(), time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts / 1000)), fill["side"], f"{fill['price']:.2f}",
                      f"{fill['qty']:.6f}", f"{fill['usdt']:.2f}", f"{eq:.2f}", self.state["halted"]])
        self.log(f"[{now_s()}] {fill['side']} {fill['qty']:.6f} BTC @ {fill['price']:.2f} (equity {eq:.2f})")

    def equity(self, price):
        return self.state["cash"] + self.state["btc"] * price

    def position(self):
        return 1 if self.state["btc"] > 0 else 0

    def desired(self, rows):
        o, h, l, c = ([r[i] for r in rows] for i in (1, 2, 3, 4))
        return S.positions(self.fam, self.params, o, h, l, c)[-1]

    # -- one closed bar
    def on_bar(self, rows):
        """rows: closed candles, oldest first, the last one is the newest CLOSED bar."""
        st, bar = self.state, rows[-1]
        ts, close = bar[0], bar[4]
        if st["last_bar"] is not None and ts <= st["last_bar"]:
            return None                                     # already handled (restart / duplicate poll)
        if st["start_price"] is None:
            st["start_price"] = close
        want = self.desired(rows)
        mode = self.control()
        self._alert_if_abnormal(rows)
        if mode == "flat":                                   # manual override: get out and stay out
            want = 0
        elif mode == "pause":                                # manual override: keep whatever is held, no new trades
            want = self.position()
        if st["halted"]:
            want = 0
        elif not st["armed"]:                                # --wait-for-signal: do not join a trend that is already running
            if want == 0:
                st["armed"] = True                           # trend is over; from now on trade every signal
            else:
                want = 0
        action = None
        if want == 1 and self.position() == 0:
            action = self.broker.buy(st, close)
        elif want == 0 and self.position() == 1:
            action = self.broker.sell(st, close)
        eq = self.equity(close)
        st["peak"] = max(st["peak"], eq)
        if action:
            self._log_trade(ts, action, eq)
        bh = st["start_equity"] * close / st["start_price"]
        self._append("equity.csv", ["time_utc", "bar_open_utc", "close", "position", "equity", "buy_hold_equity"],
                     [now_s(), time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts / 1000)), f"{close:.2f}", self.position(), f"{eq:.2f}", f"{bh:.2f}"])
        if self.max_dd is not None and not st["halted"] and eq < st["peak"] * (1 - self.max_dd):
            st["halted"] = True
            self.log(f"[{now_s()}] KILL SWITCH: equity {eq:.2f} is {100*(1-eq/st['peak']):.0f}% below its peak {st['peak']:.2f}; flattening and halting")
            if self.position() == 1:
                self._log_trade(ts, self.broker.sell(st, close), self.equity(close))
        st["last_bar"] = ts
        self._save()
        return dict(bar=ts, close=close, want=want, position=self.position(), equity=eq, action=action)

    def status(self):
        st = self.state
        p = st.get("start_price") or 0
        return (f"{self.name}: {'HALTED ' if st['halted'] else ''}position={'LONG' if self.position() else 'FLAT'}  cash={st['cash']:.2f}  "
                f"btc={st['btc']:.6f}  start_equity={st['start_equity']:.2f}  peak={st['peak']:.2f}  last bar="
                f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(st['last_bar']/1000)) if st['last_bar'] else 'none'}  (started {st['started']} UTC)")


def recent_closed(interval, n=S.WARMUP, now_ms=None):
    """The latest n CLOSED candles from MEXC (the still-forming candle is dropped)."""
    step = mexc.TF_SECONDS[interval] * 1000
    now_ms = now_ms or int(time.time() * 1000)
    end = now_ms
    start = now_ms - (n + 3) * step
    rows = mexc.klines_range(start, end, interval, pause=0.1)
    return [r for r in rows if r[0] + step <= now_ms][-n:]


def run_forever(bot, poll_delay=8, once=False, fetch=recent_closed, sleep=time.sleep, clock=time.time):
    """Wake a few seconds after each bar close, process the new closed bar, repeat. Errors never kill the loop."""
    step = bot.step / 1000
    bot.log(f"[{now_s()}] {bot.status()}")
    while True:
        try:
            rows = fetch(bot.interval)
            res = bot.on_bar(rows)
            if res:
                bot.log(f"[{now_s()}] bar {time.strftime('%Y-%m-%d %H:%M', time.gmtime(res['bar']/1000))} close {res['close']:.2f} "
                        f"-> want {'LONG' if res['want'] else 'FLAT'}, hold {'LONG' if res['position'] else 'FLAT'}, equity {res['equity']:.2f}")
        except Exception as e:                                # network hiccup, exchange error: log and retry next cycle
            bot.log(f"[{now_s()}] ERROR {type(e).__name__}: {e}")
        if once:
            return
        nxt = (int(clock() // step) + 1) * step + poll_delay
        sleep(max(5, nxt - clock()))


def run_lab(bots, poll_delay=8, once=False, fetch=recent_closed, sleep=time.sleep, clock=time.time):
    """Several paper bots in one process; candles are fetched once per interval per cycle."""
    for b in bots:
        b.log(f"[{now_s()}] {b.status()}")
    step = min(b.step for b in bots) / 1000
    while True:
        cache = {}
        for b in bots:
            try:
                if b.interval not in cache:
                    cache[b.interval] = fetch(b.interval)
                res = b.on_bar(cache[b.interval])
                if res:
                    b.log(f"[{now_s()}] {b.name}: bar {time.strftime('%Y-%m-%d %H:%M', time.gmtime(res['bar']/1000))} "
                          f"close {res['close']:.2f} -> hold {'LONG' if res['position'] else 'FLAT'}, equity {res['equity']:.2f}")
            except Exception as e:
                b.log(f"[{now_s()}] {b.name}: ERROR {type(e).__name__}: {e}")
        if once:
            return
        nxt = (int(clock() // step) + 1) * step + poll_delay
        sleep(max(5, nxt - clock()))
