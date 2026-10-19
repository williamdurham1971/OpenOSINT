#!/usr/bin/env python3
"""Paper-trading harness for Polymarket's 15-minute "Bitcoin Up or Down" markets.

No orders are ever placed and no keys are needed: it only reads public data,
simulates taker fills (fee + latency included) and logs everything so you can
measure whether an edge survives costs *before* risking money.

  python paper_trader.py run --feed chainlink      # run for as long as you like
  python paper_trader.py report                    # PnL / hit-rate / edge calibration
"""
import argparse, asyncio, bisect, collections, csv, json, math, statistics, sys, time
from pathlib import Path

import aiohttp
import websockets

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS = "wss://ws-live-data.polymarket.com"
WINDOW = 900
OUT = Path(__file__).parent / "data"


# ---------- pure maths (unit-tested in --selftest) ----------

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fee_per_share(p, rate, exp):
    """Taker fee in $/share. rate=0.25, exp=2 gives the ~1.56% of notional peak at 50c
    reported for 15m crypto markets. VERIFY against Polymarket docs before trusting."""
    return p * rate * (p * (1 - p)) ** exp


def twap_var(sigma, tau, twap_len=60.0):
    """Variance of the final settlement value around the current price when the
    market settles on a trailing `twap_len`-second average (Chainlink 60s TWAP)."""
    if tau <= 0:
        return 0.0
    if tau >= twap_len:
        return sigma ** 2 * (tau - twap_len * 2 / 3)
    return sigma ** 2 * tau ** 3 / (3 * twap_len ** 2)


def fair_up(price, strike, sigma, tau):
    """P(settlement >= strike) under zero drift, per-second log-vol `sigma`."""
    v = twap_var(sigma, tau)
    if v <= 0:
        return 1.0 if price >= strike else 0.0
    return norm_cdf(math.log(price / strike) / math.sqrt(v))


class Book:
    def __init__(self):
        self.bids, self.asks = {}, {}

    def snapshot(self, bids, asks):
        self.bids = {float(x["price"]): float(x["size"]) for x in bids}
        self.asks = {float(x["price"]): float(x["size"]) for x in asks}

    def level(self, side, price, size):
        d = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            d.pop(price, None)
        else:
            d[price] = size

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def fill_buy(self, shares):
        """Walk the ask side; returns (filled, avg_price) or (0, None)."""
        left, cost = shares, 0.0
        for p in sorted(self.asks):
            take = min(left, self.asks[p])
            cost += take * p
            left -= take
            if left <= 1e-9:
                break
        filled = shares - left
        return (filled, cost / filled) if filled > 0 else (0.0, None)


class Vol:
    """Per-second log-return std from the last `n` one-second samples."""
    def __init__(self, n=300):
        self.rets, self.last, self.sec = collections.deque(maxlen=n), None, None

    def update(self, ts, px):
        s = int(ts)
        if self.sec is None:
            self.sec, self.last = s, px
        elif s > self.sec:
            gap = s - self.sec  # carry multi-second gaps as one scaled return
            self.rets.append(math.log(px / self.last) / math.sqrt(gap))
            self.sec, self.last = s, px

    def sigma(self):
        return statistics.pstdev(self.rets) if len(self.rets) >= 60 else None


# ---------- price feeds: all yield (unix_ts, price) ----------

async def feed_chainlink(session):
    sub = {"action": "subscribe", "subscriptions": [{
        "topic": "crypto_prices_chainlink", "type": "update", "filters": '{"symbol":"btc/usd"}'}]}   # must be compact: a space silences live updates
    async with websockets.connect(RTDS_WS, ping_interval=None) as ws:
        await ws.send(json.dumps(sub))
        last_ping = time.time()
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), 5)
            except asyncio.TimeoutError:
                raw = None
            if time.time() - last_ping > 5:
                await ws.send("PING"); last_ping = time.time()
            if not raw or raw == "PONG":
                continue
            try:
                pl = json.loads(raw).get("payload") or {}
            except ValueError:
                continue
            rows = pl.get("data") or ([pl] if "value" in pl else [])
            for r in rows:
                yield r["timestamp"] / 1000, float(r["value"])


async def feed_binance(session):
    async with websockets.connect("wss://stream.binance.com:9443/ws/btcusdt@trade") as ws:
        async for raw in ws:
            m = json.loads(raw)
            yield m["T"] / 1000, float(m["p"])


async def feed_coinbase(session):
    sub = {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}
    async with websockets.connect("wss://ws-feed.exchange.coinbase.com") as ws:
        await ws.send(json.dumps(sub))
        async for raw in ws:
            m = json.loads(raw)
            if m.get("type") == "ticker" and "price" in m:
                yield time.time(), float(m["price"])


FEEDS = {"chainlink": feed_chainlink, "binance": feed_binance, "coinbase": feed_coinbase}


class State:
    def __init__(self):
        self.price = None
        self.ts = 0.0
        self.vol = Vol()
        self.hist_ts, self.hist_px = [], []   # for strike lookup + settlement TWAP

    def push(self, ts, px):
        if self.hist_ts and ts <= self.hist_ts[-1]:
            return
        self.price, self.ts = px, ts
        self.vol.update(ts, px)
        self.hist_ts.append(ts); self.hist_px.append(px)
        if len(self.hist_ts) > 4000:
            del self.hist_ts[:1000]; del self.hist_px[:1000]

    def at(self, t):
        """First sample at/after t (None if not yet seen)."""
        i = bisect.bisect_left(self.hist_ts, t)
        return self.hist_px[i] if i < len(self.hist_px) else None

    def twap(self, t0, t1):
        i, j = bisect.bisect_left(self.hist_ts, t0), bisect.bisect_right(self.hist_ts, t1)
        seg = self.hist_px[i:j]
        return sum(seg) / len(seg) if seg else None


async def run_feed(name, state):
    backoff = 1
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async for ts, px in FEEDS[name](s):
                    state.push(ts, px); backoff = 1
            except Exception as e:
                print(f"[feed:{name}] {type(e).__name__}: {e}; retry in {backoff}s", file=sys.stderr)
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)


# ---------- one 15-minute window ----------

async def fetch_market(session, start):
    slug = f"btc-updown-15m-{start}"
    for _ in range(20):
        async with session.get(GAMMA, params={"slug": slug}, headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status == 200:
                data = await r.json()
                if data:
                    m = data[0]
                    outs, toks = json.loads(m["outcomes"]), json.loads(m["clobTokenIds"])
                    return dict(zip([o.lower() for o in outs], toks)), m
        await asyncio.sleep(3)
    return None, None


async def trade_window(args, state, session, start, tradelog, tickcsv):
    end = start + WINDOW
    tokens, meta = await fetch_market(session, start)
    if not tokens:
        print(f"[{start}] market not found", file=sys.stderr); return
    for _ in range(60):                       # feed can lag the wall clock by a few seconds
        strike = state.at(start)
        if strike is not None:
            break
        await asyncio.sleep(0.5)
    if strike is None or not state.hist_ts or state.hist_ts[0] > start + 2:
        print(f"[{start}] no feed history covering the window start, skipping")
        await asyncio.sleep(max(0, end - time.time())); return
    books = {k: Book() for k in tokens}
    by_tok = {v: k for k, v in tokens.items()}
    entered, tick_at = set(), 0.0
    print(f"[{start}] {meta['question']}  strike={strike:.2f}")

    async def on_book(ws):
        nonlocal tick_at
        async for raw in ws:
            if raw == "PONG":
                continue
            msgs = json.loads(raw)
            for m in (msgs if isinstance(msgs, list) else [msgs]):
                et = m.get("event_type")
                if et == "book" and m["asset_id"] in by_tok:
                    books[by_tok[m["asset_id"]]].snapshot(m.get("bids", []), m.get("asks", []))
                elif et == "price_change":
                    for c in m.get("price_changes", []):
                        if c["asset_id"] in by_tok:
                            books[by_tok[c["asset_id"]]].level(c["side"], float(c["price"]), float(c["size"]))
                else:
                    continue
                now = time.time()
                if now > end - args.stop_before or state.price is None:
                    continue
                tau = end - now
                sigma = state.vol.sigma()
                if sigma is None:
                    continue
                pu = fair_up(state.price, strike, sigma * args.vol_mult, tau)
                if now - tick_at >= 1:
                    tick_at = now
                    tickcsv.writerow([f"{now:.3f}", start, f"{tau:.1f}", state.price, f"{sigma:.3e}", f"{pu:.4f}",
                                      books["up"].best_bid(), books["up"].best_ask(),
                                      books["down"].best_bid(), books["down"].best_ask()])
                for side, fair in (("up", pu), ("down", 1 - pu)):
                    ask = books[side].best_ask()
                    if ask is None or side in entered or entered:  # one position per window
                        continue
                    edge = fair - ask - fee_per_share(ask, args.fee_rate, args.fee_exp)
                    if edge >= args.min_edge and args.min_p <= ask <= args.max_p:
                        entered.add(side)
                        asyncio.create_task(fill(side, fair, ask, edge, tau, pu))

    async def fill(side, fair, seen_ask, edge, tau, pu):
        await asyncio.sleep(args.latency_ms / 1000)          # our order reaches the book late
        filled, avg = books[side].fill_buy(args.size)
        if filled < 1:
            print(f"[{start}] {side} no fill after latency (ask gone)"); return
        fee = fee_per_share(avg, args.fee_rate, args.fee_exp) * filled
        rec = dict(window=start, side=side, shares=filled, seen_ask=seen_ask, fill=avg, fee=fee,
                   fair=fair, edge=edge, tau=tau, btc=state.price, strike=strike, t=time.time())
        tradelog.append(rec)
        print(f"[{start}] PAPER BUY {side} {filled:.0f}@{avg:.3f} fair={fair:.3f} edge={edge:+.3f} tau={tau:.0f}s")

    tradelog = tradelog  # list of dicts for this window; flushed at settlement
    async with websockets.connect(CLOB_WS, ping_interval=None) as ws:
        await ws.send(json.dumps({"assets_ids": list(tokens.values()), "type": "market"}))

        async def pinger():
            while True:
                await asyncio.sleep(9); await ws.send("PING")
        tasks = [asyncio.create_task(on_book(ws)), asyncio.create_task(pinger())]
        await asyncio.sleep(max(0, end - time.time()))
        for t in tasks:
            t.cancel()


def settle(state, start, trades, outfile):
    end = start + WINDOW
    strike = state.at(start)
    final = state.twap(end - 60, end)       # proxy for Chainlink 60s TWAP stream
    if final is None or strike is None:
        return
    up = final >= strike
    with open(outfile, "a") as f:
        for r in trades:
            won = (r["side"] == "up") == up
            r.update(final=final, outcome="up" if up else "down",
                     pnl=r["shares"] * (1.0 if won else 0.0) - r["shares"] * r["fill"] - r["fee"])
            f.write(json.dumps(r) + "\n")
            print(f"[{start}] SETTLED {r['side']} -> {r['outcome']}  pnl=${r['pnl']:+.2f}")


async def run(args):
    OUT.mkdir(exist_ok=True)
    state = State()
    asyncio.create_task(run_feed(args.feed, state))
    tickf = open(OUT / "ticks.csv", "a", newline="")
    tickcsv = csv.writer(tickf)
    pending = []
    async with aiohttp.ClientSession() as session:
        while True:
            now = time.time()
            start = int(now - now % WINDOW) + WINDOW        # always begin at the next boundary
            if start - now < 90:                              # need ~60s of ticks to estimate vol
                start += WINDOW
            if args.max_windows is not None and args.max_windows <= 0:
                break
            await asyncio.sleep(max(0, start - now))
            trades = []
            try:
                await trade_window(args, state, session, start, trades, tickcsv)
            except Exception as e:
                print(f"[{start}] error {type(e).__name__}: {e}", file=sys.stderr)
            tickf.flush()
            await asyncio.sleep(max(0, start + WINDOW - time.time()) + 2)
            settle(state, start, trades, OUT / "trades.jsonl")
            if args.max_windows is not None:
                args.max_windows -= 1


def report(_):
    f = OUT / "trades.jsonl"
    if not f.exists():
        print("no trades yet"); return
    rows = [json.loads(l) for l in f.open()]
    pnl = [r["pnl"] for r in rows]
    cost = sum(r["shares"] * r["fill"] + r["fee"] for r in rows)
    wins = sum(p > 0 for p in pnl)
    print(f"trades={len(rows)}  hit={wins/len(rows):.1%}  net=${sum(pnl):+.2f}  ROI={sum(pnl)/cost:+.1%}  "
          f"avg edge claimed={statistics.mean(r['edge'] for r in rows):+.3f}")
    print(f"avg realised/share={sum(pnl)/sum(r['shares'] for r in rows):+.3f}  total fees=${sum(r['fee'] for r in rows):.2f}")
    print("calibration (model fair vs. how often that side really won):")
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[min(int(r["fair"] * 10), 9)].append((r["side"] == r["outcome"], r["fair"]))
    for b in sorted(buckets):
        v = buckets[b]
        print(f"  fair {b/10:.1f}-{(b+1)/10:.1f}: n={len(v):3d} model={statistics.mean(x[1] for x in v):.2f} actual={statistics.mean(x[0] for x in v):.2f}")
    if len(rows) < 100:
        print(f"\nNOTE: n={len(rows)} is far too small to conclude anything (aim for several hundred windows).")


def selftest():
    assert abs(norm_cdf(0) - 0.5) < 1e-12
    assert abs(fair_up(100, 100, 1e-4, 300) - 0.5) < 1e-9
    assert fair_up(101, 100, 1e-4, 300) > 0.9 and fair_up(99, 100, 1e-4, 300) < 0.1
    assert fair_up(100.01, 100, 1e-4, 0) == 1.0 and fair_up(99.99, 100, 1e-4, 0) == 0.0
    assert abs(twap_var(1, 60) - twap_var(1, 60 - 1e-9)) < 1e-6          # continuity at 60s
    assert abs(fee_per_share(0.5, 0.25, 2) / 0.5 - 0.015625) < 1e-9       # 1.56% of notional at 50c
    b = Book(); b.snapshot([{"price": "0.4", "size": "10"}], [{"price": "0.5", "size": "10"}, {"price": "0.6", "size": "10"}])
    assert b.best_ask() == 0.5 and b.fill_buy(15) == (15, (10 * 0.5 + 5 * 0.6) / 15)
    b.level("SELL", 0.5, 0); assert b.best_ask() == 0.6
    assert b.fill_buy(100)[0] == 10
    v = Vol()
    for i in range(200):
        v.update(i, 100 * (1 + (1e-4 if i % 2 else -1e-4)))
    assert 1.5e-4 < v.sigma() < 2.5e-4
    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--feed", choices=FEEDS, default="chainlink", help="chainlink = actual settlement source (recommended)")
    r.add_argument("--size", type=float, default=20, help="shares per paper trade")
    r.add_argument("--min-edge", type=float, default=0.04, help="required fair-ask-fee, $/share")
    r.add_argument("--min-p", type=float, default=0.10); r.add_argument("--max-p", type=float, default=0.90)
    r.add_argument("--latency-ms", type=int, default=400, help="simulated order latency")
    r.add_argument("--stop-before", type=float, default=15, help="no entries in last N seconds")
    r.add_argument("--vol-mult", type=float, default=1.0, help="scale model vol (stress test)")
    r.add_argument("--fee-rate", type=float, default=0.25); r.add_argument("--fee-exp", type=float, default=2)
    r.add_argument("--max-windows", type=int, default=None)
    sp.add_parser("report"); sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest": selftest()
    elif a.cmd == "report": report(a)
    else:
        try: asyncio.run(run(a))
        except KeyboardInterrupt: pass
