#!/usr/bin/env python3
"""Paper-trading harness for Polymarket's 15-minute "Bitcoin Up or Down" markets.

No orders are ever placed and no keys are needed: it only reads public data,
simulates fills (fees, latency, queue position) and logs everything so you can
measure whether an edge survives costs *before* risking money.

  python paper_trader.py measure --minutes 30      # how fast is each feed / how late is Polymarket?
  python paper_trader.py run --mode both           # paper-trade taker and maker side by side
  python paper_trader.py report                    # PnL / hit-rate / fill-rate / calibration
"""
import argparse, asyncio, bisect, collections, csv, datetime, json, math, statistics, sys, time
from pathlib import Path

import aiohttp
import websockets

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_REST = "https://clob.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
RTDS_WS = "wss://ws-live-data.polymarket.com"
WINDOW = 900
STALL_S = 20.0         # reconnect a price feed that delivers nothing for this long (a half-open socket stays silent for minutes)
JOIN_LATE = 20.0       # still join a window that began up to this many seconds ago
OUT = Path(__file__).parent / "data"


# ---------- pure maths (unit-tested in `selftest`) ----------

def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def fee_per_share(p, rate, exp):
    """Taker fee in $/share = rate * (p*(1-p))**exp. Polymarket docs: fee = C * feeRate * p * (1-p),
    feeRate 0.07 for crypto; the market's feeSchedule says rate 0.07, exponent 1, takerOnly.
    That is 1.75c/share (3.5% of notional) at 50c and 1.12c (1.4%) at 80c. Makers pay no fee."""
    return rate * (p * (1 - p)) ** exp


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


def floor_tick(p, tick):
    return round(math.floor(p / tick + 1e-9) * tick, 6)


def pearson(pairs):
    n = len(pairs)
    if n < 20:
        return None
    mx, my = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((a - mx) * (b - my) for a, b in pairs) / math.sqrt(sxx * syy)


def lag_corr(x, y, lags):
    """corr(x[i-L], y[i]) per L (None entries masked). Peak at L>0 means y lags x by L bins."""
    out = {}
    for L in lags:
        lo, hi = max(0, L), min(len(y), len(x) + L)
        out[L] = pearson([(x[i - L], y[i]) for i in range(lo, hi)
                          if x[i - L] is not None and y[i] is not None])
    return out


def peak_lag(corrs):
    valid = {L: c for L, c in corrs.items() if c is not None}
    return max(valid.items(), key=lambda kv: kv[1]) if valid else (None, None)


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


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

    def mid(self):
        b, a = self.best_bid(), self.best_ask()
        return (b + a) / 2 if b is not None and a is not None else None

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


# ---------- price feeds: all yield (source_unix_ts, price) ----------

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
                try:   # exchange timestamp, so staleness can be measured
                    ts = datetime.datetime.fromisoformat(m["time"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    ts = time.time()
                yield ts, float(m["price"])


FEEDS = {"chainlink": feed_chainlink, "binance": feed_binance, "coinbase": feed_coinbase}


class State:
    def __init__(self):
        self.price = None
        self.ts = 0.0
        self.vol = Vol()
        self.hist_ts, self.hist_px = [], []   # for strike lookup + settlement TWAP

    def push(self, ts, px):
        if self.hist_ts and ts <= self.hist_ts[-1]:
            return False
        self.price, self.ts = px, ts
        self.vol.update(ts, px)
        self.hist_ts.append(ts); self.hist_px.append(px)
        if len(self.hist_ts) > 4000:
            del self.hist_ts[:1000]; del self.hist_px[:1000]
        return True

    def at(self, t):
        """First sample at/after t (None if not yet seen)."""
        i = bisect.bisect_left(self.hist_ts, t)
        return self.hist_px[i] if i < len(self.hist_px) else None

    def twap(self, t0, t1):
        i, j = bisect.bisect_left(self.hist_ts, t0), bisect.bisect_right(self.hist_ts, t1)
        seg = self.hist_px[i:j]
        return sum(seg) / len(seg) if seg else None


def boundary_value(state, B, lookback=60.0):
    """Our estimate of Polymarket's official price at boundary B: mean of the feed over [B-lookback, B).
    (cryptoMarketConfig says twapEnabled, twapLookbackSeconds=60; `verify` tests this against Gamma's
    official priceToBeat/finalPrice.) None until the feed covers the whole lookback and has reached B."""
    if not state.hist_ts or state.hist_ts[0] > B - lookback + 5 or state.hist_ts[-1] < B:
        return None
    return state.twap(B - lookback, B - 1e-6)


async def run_feed(name, state, lag_sink=None, log_path=None):
    """Keep `state` fed. `lag_sink` collects (receive time - source timestamp) in seconds,
    skipping the snapshot burst Chainlink sends on connect."""
    backoff = 1
    logf = open(log_path, "a", buffering=1) if log_path else None
    async with aiohttp.ClientSession() as s:
        while True:
            t_conn = time.time()
            it = FEEDS[name](s).__aiter__()
            try:
                while True:
                    ts, px = await asyncio.wait_for(it.__anext__(), STALL_S)    # raises TimeoutError on a silent socket
                    if state.push(ts, px) and logf:
                        logf.write(f"{ts:.3f},{px}\n")
                    backoff = 1
                    if lag_sink is not None and ts > t_conn:
                        lag_sink.append(time.time() - ts)
            except StopAsyncIteration:
                pass
            except Exception as e:
                print(f"[feed:{name}] {type(e).__name__}: {str(e) or 'no data for %gs' % STALL_S}; reconnecting in {backoff}s", file=sys.stderr)
            finally:
                await it.aclose()
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)


# ---------- Polymarket market stream (shared by run + measure) ----------

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


def apply_book_msg(books, by_tok, m):
    """Update books from one CLOB message. Returns ("book", None), ("trade", (outcome, price, size, taker_side)) or (None, None)."""
    et = m.get("event_type")
    if et == "book" and m.get("asset_id") in by_tok:
        books[by_tok[m["asset_id"]]].snapshot(m.get("bids", []), m.get("asks", []))
        return "book", None
    if et == "price_change":
        for c in m.get("price_changes", []):
            if c["asset_id"] in by_tok:
                books[by_tok[c["asset_id"]]].level(c["side"], float(c["price"]), float(c["size"]))
        return "book", None
    if et == "last_trade_price" and m.get("asset_id") in by_tok:
        return "trade", (by_tok[m["asset_id"]], float(m["price"]), float(m.get("size", 0)), m.get("side"))
    return None, None


async def stream_market(tokens, end, handler):
    """Stream the Up/Down books until unix time `end`, reconnecting on errors.
    handler(kind, trade, msg, books, recv_time) is called synchronously per message."""
    by_tok = {v: k for k, v in tokens.items()}
    books = {k: Book() for k in tokens}
    while time.time() < end:
        for b in books.values():
            b.snapshot([], [])
        try:
            async with websockets.connect(CLOB_WS, ping_interval=None, close_timeout=1) as ws:   # default 10s close wait made us miss the next window
                await ws.send(json.dumps({"assets_ids": list(tokens.values()), "type": "market"}))

                async def pinger():
                    while True:
                        await asyncio.sleep(9); await ws.send("PING")

                async def reader():
                    async for raw in ws:
                        if raw == "PONG":
                            continue
                        try:
                            msgs = json.loads(raw)
                        except ValueError:
                            continue
                        now = time.time()
                        for m in (msgs if isinstance(msgs, list) else [msgs]):
                            kind, tr = apply_book_msg(books, by_tok, m)
                            if kind:
                                handler(kind, tr, m, books, now)

                ptask = asyncio.create_task(pinger())
                try:
                    await asyncio.wait_for(reader(), max(0.1, end - time.time()))
                finally:
                    ptask.cancel()
        except asyncio.TimeoutError:
            break                                    # window over
        except Exception as e:
            print(f"[clob-ws] {type(e).__name__}: {e}; reconnecting", file=sys.stderr)
        await asyncio.sleep(0.5)
    return books


# ---------- maker (passive limit order) simulator ----------

class MakerOrder:
    def __init__(self, side, price, size, now, active_at, fair, tau, btc):
        self.side, self.price, self.size = side, price, size
        self.placed_at, self.active_at = now, active_at
        self.fair, self.tau, self.btc = fair, tau, btc
        self.state, self.queue_ahead, self.queue_init = "pending", 0.0, 0.0
        self.filled, self.cancel_at, self.reason = 0.0, None, None


class MakerSim:
    """Conservative queue-aware simulation of one resting BUY limit order at a time.

    Fill rules (we never assume fills the tape doesn't support):
    - order is live `latency` after the decision; it queues behind whatever size already rests at its price
    - a trade at a lower price than ours fills us fully (price priority);
      a trade at our price eats the queue ahead first, only the excess fills us
    - if the opposite best ask drops to <= our bid, a seller would have crossed into us: full fill
    - queue cancellations are NOT credited (pessimistic)
    - cancel when fair - price < cancel_edge (takes `latency` to reach the book; fills can still happen meanwhile)
    - maker fee assumed 0 (check Polymarket docs; rebates are ignored)
    """
    def __init__(self, args, tick):
        self.a, self.tick = args, tick
        self.orders, self.cur = [], None
        self.lat = args.latency_ms / 1000

    def _fill(self, o, qty):
        qty = min(qty, o.size - o.filled)
        if qty > 0:
            o.filled += qty

    def _advance(self, o, now, kind, tr, books, fair):
        b = books[o.side]
        if o.state == "pending" and now >= o.active_at and (b.bids or b.asks):   # empty book = after a reconnect, queue unknown
            o.state = "live"
            o.queue_ahead = o.queue_init = b.bids.get(o.price, 0.0)
        if o.state == "live" and o.filled < o.size:
            ask = b.best_ask()
            if kind == "book" and ask is not None and ask <= o.price + 1e-9:
                self._fill(o, o.size - o.filled)
            elif kind == "trade" and tr[0] == o.side and tr[3] in (None, "SELL"):
                _, p, sz, _ = tr
                if p < o.price - 1e-9:
                    self._fill(o, o.size - o.filled)
                elif abs(p - o.price) < 1e-9:
                    excess = sz - o.queue_ahead
                    o.queue_ahead = max(0.0, o.queue_ahead - sz)
                    if excess > 0:
                        self._fill(o, excess)
        if o.state in ("pending", "live") and o.cancel_at is None \
                and fair[o.side] - o.price < self.a.maker_cancel_edge:
            o.cancel_at = now + self.lat
        if o.cancel_at is not None and now >= o.cancel_at and o.state != "done":
            o.state, o.reason = "done", "cancelled"
        if o.filled >= o.size - 1e-9:
            o.state, o.reason = "done", o.reason or "filled"

    def _place(self, now, tau, books, fair, btc):
        best = None
        for side in ("up", "down"):
            b = books[side]
            ask, bid = b.best_ask(), b.best_bid()
            if ask is None:
                continue
            cands = [fair[side] - self.a.maker_min_edge, ask - self.tick]
            if bid is not None:
                cands.append(bid + self.tick if self.a.maker_improve else bid)
            price = floor_tick(min(cands), self.tick)
            if not 0.02 <= price <= 0.98:
                continue
            edge = fair[side] - price
            if edge >= self.a.maker_min_edge - 1e-9 and (best is None or edge > best[0]):
                best = (edge, side, price)
        if best:
            _, side, price = best
            o = MakerOrder(side, price, self.a.size, now, now + self.lat, fair[side], tau, btc)
            self.orders.append(o); self.cur = o

    def on_event(self, now, tau, kind, tr, books, fair, btc):
        if self.cur is not None:
            self._advance(self.cur, now, kind, tr, books, fair)
            if self.cur.state != "done" and tau <= self.a.stop_before:
                self.cur.state, self.cur.reason = "done", "window_end"
        if (self.cur is None or self.cur.state == "done") and tau > self.a.stop_before + 5:
            positioned = self.cur is not None and self.cur.filled > 0
            if not positioned and len(self.orders) < self.a.maker_max_orders and kind == "book":
                self._place(now, tau, books, fair, btc)

    def records(self, start, strike):
        return [dict(mode="maker", window=start, side=o.side, shares=o.filled, size=o.size, fill=o.price,
                     fee=0.0, fair=o.fair, edge=o.fair - o.price, tau=o.tau, btc=o.btc, strike=strike,
                     t=o.placed_at, queue_init=o.queue_init, reason=o.reason or "window_end")
                for o in self.orders]


# ---------- one 15-minute window ----------

async def trade_window(args, state, session, start, records, tickcsv):
    end = start + WINDOW
    tokens, meta = await fetch_market(session, start)
    if not tokens:
        print(f"[{start}] market not found", file=sys.stderr); return
    strike = None
    for _ in range(60):                       # feed can lag the wall clock by a few seconds
        strike = boundary_value(state, start)
        if strike is not None:
            break
        await asyncio.sleep(0.5)
    if strike is None:
        print(f"[{start}] no feed history covering the window start, skipping")
        await asyncio.sleep(max(0, end - time.time())); return None
    fs = meta.get("feeSchedule") or {}
    fee_rate, fee_exp = float(fs.get("rate", args.fee_rate)), float(fs.get("exponent", args.fee_exp))
    tick = float(meta.get("orderPriceMinTickSize") or 0.01)
    taker_on, maker_on = args.mode in ("taker", "both"), args.mode in ("maker", "both")
    maker = MakerSim(args, tick) if maker_on else None
    entered, tick_at = False, 0.0
    calc = dict(at=0.0, sigma=None, pu=None, fair=None)
    print(f"[{start}] {meta['question']}  strike~{strike:.2f}  tick={tick}  fee={fee_rate}*(p(1-p))^{fee_exp}  mode={args.mode}")

    async def taker_fill(books, side, fair, seen_ask, edge, tau):
        await asyncio.sleep(args.latency_ms / 1000)          # our order reaches the book late
        filled, avg = books[side].fill_buy(args.size)
        if filled < 1:
            print(f"[{start}] taker {side} no fill after latency (ask gone)"); return
        fee = fee_per_share(avg, fee_rate, fee_exp) * filled
        records.append(dict(mode="taker", window=start, side=side, shares=filled, seen_ask=seen_ask, fill=avg,
                            fee=fee, fair=fair, edge=edge, tau=tau, btc=state.price, strike=strike, t=time.time()))
        print(f"[{start}] PAPER TAKER BUY {side} {filled:.0f}@{avg:.3f} fair={fair:.3f} edge={edge:+.3f} tau={tau:.0f}s")

    def handler(kind, tr, m, books, now):
        nonlocal entered, tick_at
        if state.price is None or now > end:
            return
        tau = end - now
        if now - calc["at"] >= 0.1:        # vol/fair value barely move in 100ms; recomputing per message made us a slow consumer
            sigma = state.vol.sigma()
            if sigma is None:
                return
            pu = fair_up(state.price, strike, sigma * args.vol_mult, tau)
            calc.update(at=now, sigma=sigma, pu=pu, fair={"up": pu, "down": 1 - pu})
        sigma, pu, fair = calc["sigma"], calc["pu"], calc["fair"]
        if sigma is None:
            return
        if maker:
            maker.on_event(now, tau, kind, tr, books, fair, state.price)
        if kind != "book" or tau <= args.stop_before:
            return
        if now - tick_at >= 1:
            tick_at = now
            tickcsv.writerow([f"{now:.3f}", start, f"{tau:.1f}", state.price, f"{sigma:.3e}", f"{pu:.4f}",
                              books["up"].best_bid(), books["up"].best_ask(),
                              books["down"].best_bid(), books["down"].best_ask()])
        if taker_on and not entered:
            for side in ("up", "down"):
                ask = books[side].best_ask()
                if ask is None:
                    continue
                edge = fair[side] - ask - fee_per_share(ask, fee_rate, fee_exp)
                if edge >= args.min_edge and args.min_p <= ask <= args.max_p:
                    entered = True
                    asyncio.create_task(taker_fill(books, side, fair[side], ask, edge, tau))
                    break

    await stream_market(tokens, end, handler)
    if maker:
        for o in maker.orders:
            if o.state != "done":
                o.state, o.reason = "done", "window_end"
        recs = maker.records(start, strike)
        records.extend(recs)
        for r in recs:
            print(f"[{start}] MAKER {r['side']} {r['shares']:.0f}/{r['size']:.0f} @ {r['fill']:.3f} "
                  f"fair@place={r['fair']:.3f} queue={r['queue_init']:.0f} ({r['reason']})")
    return strike


UA = {"User-Agent": "Mozilla/5.0"}


async def official_result(session, start):
    """(outcome 'up'/'down' or None, priceToBeat, finalPrice) for window `start` from Gamma.
    The outcome appears ~1-2 min after the close; the two prices can take ~20 min."""
    async with session.get("https://gamma-api.polymarket.com/events", params={"slug": f"btc-updown-15m-{start}"},
                           headers=UA) as r:
        if r.status != 200:
            return None, None, None
        d = await r.json()
    if not d:
        return None, None, None
    e = d[0]
    md = e.get("eventMetadata") or {}
    out, m = None, (e.get("markets") or [{}])[0]
    try:
        prices = [float(x) for x in json.loads(m["outcomePrices"])]
        outs = [o.lower() for o in json.loads(m["outcomes"])]
        if m.get("closed") and max(prices) >= 0.999 and min(prices) <= 0.001:
            out = outs[prices.index(max(prices))]
    except (KeyError, ValueError, TypeError):
        pass
    return out, md.get("priceToBeat"), md.get("finalPrice")


async def settle(state, session, start, records, strike_proxy):
    """Settle with Polymarket's official result; keep our own estimate alongside for `verify`."""
    end = start + WINDOW
    await asyncio.sleep(max(0, end + 2 - time.time()))
    final_proxy = boundary_value(state, end)
    for _ in range(20):                                  # feed delivery lags ~1.5s; give it a few seconds
        if final_proxy is not None:
            break
        await asyncio.sleep(1)
        final_proxy = boundary_value(state, end)
    proxy = None
    if final_proxy is not None and strike_proxy is not None:
        proxy = "up" if final_proxy >= strike_proxy else "down"
    outcome = beat = fin = None
    for _ in range(40):                                  # resolution usually lands 1-2 min after the close
        try:
            outcome, beat, fin = await official_result(session, start)
        except Exception as e:
            print(f"[{start}] gamma lookup failed: {type(e).__name__}", file=sys.stderr)
        if outcome:
            break
        await asyncio.sleep(15)
    result, src = (outcome, "gamma") if outcome else (proxy, "proxy")
    with open(OUT / "windows.jsonl", "a") as f:
        f.write(json.dumps(dict(window=start, strike_proxy=strike_proxy, final_proxy=final_proxy,
                                outcome_proxy=proxy, outcome_official=outcome,
                                price_to_beat=beat, final_price=fin)) + "\n")
    if outcome and proxy and outcome != proxy:
        print(f"[{start}] PROXY MISMATCH: we said {proxy} (final {final_proxy:.2f} vs strike {strike_proxy:.2f}), "
              f"Polymarket resolved {outcome}")
    if result is None:
        print(f"[{start}] could not determine an outcome; trades left unsettled"); return
    with open(OUT / "trades.jsonl", "a") as f:
        for r in records:
            won = (r["side"] == "up") == (result == "up")
            r.update(final=final_proxy, outcome=result, outcome_source=src, outcome_proxy=proxy,
                     pnl=r["shares"] * (1.0 if won else 0.0) - r["shares"] * r["fill"] - r["fee"])
            f.write(json.dumps(r) + "\n")
            if r["shares"] > 0:
                print(f"[{start}] SETTLED ({src}) {r['mode']} {r['side']} -> {result}  pnl=${r['pnl']:+.2f}")


async def run(args):
    OUT.mkdir(exist_ok=True)
    state = State()
    asyncio.create_task(run_feed(args.feed, state, log_path=OUT / f"feed_{args.feed}.csv"))
    tickf = open(OUT / "ticks.csv", "a", newline="")
    tickcsv = csv.writer(tickf)
    pending = []
    async with aiohttp.ClientSession() as session:
        while True:
            now = time.time()
            cand = int(now - now % WINDOW)
            start = cand if now - cand < JOIN_LATE else cand + WINDOW   # a boundary that just passed is still ours
            if state.vol.sigma() is None and start - now < 90:    # first window: need ~60s of ticks to estimate vol
                start += WINDOW
            if args.max_windows is not None and args.max_windows <= 0:
                break
            await asyncio.sleep(max(0, start - now))
            if now - start > 1:
                print(f"[{start}] joining {now - start:.1f}s late")
            records, strike = [], None
            try:
                strike = await trade_window(args, state, session, start, records, tickcsv)
            except Exception as e:
                print(f"[{start}] error {type(e).__name__}: {e}", file=sys.stderr)
            tickf.flush()
            if strike is not None:                           # settle in the background; the next window starts on time
                pending.append(asyncio.create_task(settle(state, session, start, records, strike)))
            if args.max_windows is not None:
                args.max_windows -= 1
        if pending:
            print("waiting for official results ...")
            await asyncio.gather(*pending)


# ---------- latency measurement ----------

async def measure_rtt(session, n=12):
    """HTTPS round-trip to the CLOB (keep-alive after the first request). Lower bound for order latency."""
    out = []
    for i in range(n):
        t0 = time.perf_counter()
        try:
            async with session.get(f"{CLOB_REST}/time") as r:
                await r.read()
            out.append((time.perf_counter() - t0) * 1000)
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return out[1:]                                          # drop the TLS-handshake sample


def build_series(rows, feeds, horizon_bins):
    """Per-bin horizon changes: log-returns per feed and Up-mid deltas (None when unavailable)."""
    k = horizon_bins
    ret = {f: [None] * len(rows) for f in feeds}
    dmid = [None] * len(rows)
    for i in range(k, len(rows)):
        for f in feeds:
            a, b = rows[i - k][f], rows[i][f]
            if a and b:
                ret[f][i] = math.log(b / a)
        a, b = rows[i - k]["mid"], rows[i]["mid"]
        if a is not None and b is not None and rows[i - k]["window"] == rows[i]["window"]:
            dmid[i] = b - a
    return ret, dmid


def fmt_corr(c):
    return "  n/a" if c is None else f"{c:+.3f}"


def analyze_latency(rows, feeds, stale, clob_lag, trades_seen, rtt, bin_s, horizon, max_lag, out=print):
    k = max(1, round(horizon / bin_s))
    ret, dmid = build_series(rows, feeds, k)
    lags = range(-round(6 / bin_s), round(max_lag / bin_s) + 1)
    n_valid = sum(v is not None for v in dmid)
    out(f"\n=== LATENCY REPORT: {len(rows)} bins of {bin_s}s, {n_valid} with a valid Polymarket mid change (horizon {horizon}s) ===")

    out("\n1) Feed delivery delay: my receive time - source timestamp (includes clock skew; keep NTP on)")
    for f in feeds:
        v = [x * 1000 for x in stale[f]]
        out(f"   {f:10s} n={len(v):5d}  median={statistics.median(v):7.0f} ms  p95={pct(v,.95):7.0f} ms" if v else f"   {f:10s} no samples")
    if clob_lag:
        v = [x * 1000 for x in clob_lag]
        out(f"   {'polymarket book':16s} n={len(v)}  median={statistics.median(v):.0f} ms  p95={pct(v,.95):.0f} ms")
    if rtt:
        out(f"   REST round-trip to CLOB: median={statistics.median(rtt):.0f} ms  p95={pct(rtt,.95):.0f} ms  (floor for your --latency-ms; a signed order costs more)")

    out("\n2) Who moves first? corr(feed A change at t-L, feed B change at t); peak L>0 => B lags A")
    base = "chainlink" if "chainlink" in feeds else feeds[0]
    for f in feeds:
        if f == base:
            continue
        L, c = peak_lag(lag_corr(ret[f], ret[base], lags))
        if L is None:
            out(f"   {f} -> {base}: not enough data"); continue
        out(f"   {f:10s} -> {base}: {base} lags by {L*bin_s:+.1f}s (corr {c:+.3f}; at 0s {fmt_corr(lag_corr(ret[f], ret[base], [0])[0])})")

    out("\n3) How late is Polymarket's Up price? corr(feed change at t-L, Up-mid change at t)")
    out("   lag(s): " + " ".join(f"{L*bin_s:+5.1f}" for L in lags if L % max(1, round(1 / bin_s)) == 0))
    for f in feeds:
        corrs = lag_corr(ret[f], dmid, lags)
        L, c = peak_lag(corrs)
        out(f"   {f:9s}: " + " ".join(fmt_corr(corrs[x]) for x in lags if x % max(1, round(1 / bin_s)) == 0))
        if L is None:
            out("   -> not enough data")
        else:
            when = ("at the same time as" if abs(L * bin_s) < horizon / 2 else
                    f"~{L*bin_s:.1f}s AFTER" if L > 0 else f"~{-L*bin_s:.1f}s BEFORE")
            out(f"   -> peak at {L*bin_s:+.1f}s (corr {c:+.3f})  => Polymarket's Up price moves {when} {f}")

    if trades_seen:
        out("\n4) last_trade_price 'side' semantics (is it the taker's side?):")
        for s in sorted({t[0] for t in trades_seen}, key=str):
            below = [t[1] <= t[2] for t in trades_seen if t[0] == s and t[2] is not None]
            if below:
                out(f"   side={s}: {sum(below)/len(below):.0%} of {len(below)} prints at/below the mid "
                    "(SELL-taker prints should sit at the bid => high %, BUY-taker prints => low %)")
    out("\nNote: horizon windows overlap, so correlations are autocorrelated; trust the peak's location, not its p-value. "
        "Run >=1h (ideally across volatile hours) before concluding.")


async def measure(args):
    OUT.mkdir(exist_ok=True)
    feeds = [f.strip() for f in args.feeds.split(",")]
    states = {f: State() for f in feeds}
    stale = {f: [] for f in feeds}
    for f in feeds:
        asyncio.create_task(run_feed(f, states[f], stale[f], log_path=OUT / f"feed_{f}.csv"))
    stop_at = time.time() + args.minutes * 60
    mid = {"v": None, "win": None}
    clob_lag, trades_seen, rows = [], [], []

    async with aiohttp.ClientSession() as session:
        rtt = await measure_rtt(session)

        async def recorder():
            with open(OUT / "latency.csv", "w", newline="") as fh:
                w = csv.writer(fh); w.writerow(["t", "window", "mid"] + feeds)
                nxt = time.time()
                while time.time() < stop_at:
                    nxt += args.bin
                    await asyncio.sleep(max(0, nxt - time.time()))
                    row = {"t": time.time(), "window": mid["win"], "mid": mid["v"], **{f: states[f].price for f in feeds}}
                    rows.append(row)
                    w.writerow([f"{row['t']:.3f}", row["window"], row["mid"]] + [row[f] for f in feeds])

        async def books_loop():
            while time.time() < stop_at:
                now = time.time()
                start = int(now - now % WINDOW)
                tokens, _ = await fetch_market(session, start)
                if not tokens:
                    await asyncio.sleep(5); continue

                def handler(kind, tr, m, books, recv):
                    mid["v"], mid["win"] = books["up"].mid(), start
                    if m.get("timestamp"):
                        try:
                            clob_lag.append(recv - int(m["timestamp"]) / 1000)
                        except ValueError:
                            pass
                    if kind == "trade" and tr[0] == "up":
                        trades_seen.append((tr[3], tr[1], books["up"].mid()))
                await stream_market(tokens, min(start + WINDOW, stop_at), handler)
                mid["v"] = None

        print(f"measuring {args.minutes} min: feeds={feeds}, bin={args.bin}s ... (Ctrl-C to stop early)")
        await asyncio.gather(recorder(), books_loop())
    rows = [r for r in rows if r["window"] is not None] or rows
    analyze_latency(rows, feeds, stale, clob_lag, trades_seen, rtt, args.bin, args.horizon, args.max_lag)


# ---------- reporting ----------

def summarize(label, rows):
    filled = [r for r in rows if r["shares"] > 0]
    if not filled:
        print(f"[{label}] no filled trades yet"); return
    pnl = [r["pnl"] for r in filled]
    cost = sum(r["shares"] * r["fill"] + r["fee"] for r in filled)
    wins = sum(p > 0 for p in pnl)
    print(f"[{label}] filled={len(filled)}  hit={wins/len(filled):.1%}  net=${sum(pnl):+.2f}  ROI={sum(pnl)/cost:+.1%}  "
          f"avg edge claimed={statistics.mean(r['edge'] for r in filled):+.3f}  "
          f"realised/share={sum(pnl)/sum(r['shares'] for r in filled):+.3f}  fees=${sum(r['fee'] for r in filled):.2f}")
    if label == "maker":
        print(f"        orders={len(rows)}  fill-rate={len(filled)/len(rows):.0%}  "
              f"avg filled fraction={statistics.mean(r['shares']/r['size'] for r in filled):.0%}  "
              f"avg queue ahead at post={statistics.mean(r['queue_init'] for r in rows):.0f} sh")


def report(_):
    f = OUT / "trades.jsonl"
    if not f.exists():
        print("no trades yet"); return
    rows = [json.loads(l) for l in f.open()]
    for r in rows:
        r.setdefault("mode", "taker"); r.setdefault("size", r["shares"]); r.setdefault("queue_init", 0)
    for mode in ("taker", "maker"):
        sub = [r for r in rows if r["mode"] == mode]
        if sub:
            summarize(mode, sub)
    filled = [r for r in rows if r["shares"] > 0]
    if filled:
        print("calibration (model fair vs. how often that side really won):")
        buckets = collections.defaultdict(list)
        for r in filled:
            buckets[min(int(r["fair"] * 10), 9)].append((r["side"] == r["outcome"], r["fair"]))
        for b in sorted(buckets):
            v = buckets[b]
            print(f"  fair {b/10:.1f}-{(b+1)/10:.1f}: n={len(v):3d} model={statistics.mean(x[1] for x in v):.2f} actual={statistics.mean(x[0] for x in v):.2f}")
    if len(filled) < 100:
        print(f"\nNOTE: n={len(filled)} filled trades is far too small to conclude anything (aim for several hundred windows).")



# ---------- verify: our boundary price vs Polymarket's official one ----------

class Series:
    def __init__(self, ts, px):
        self.ts, self.px = ts, px

    def mean(self, a, b, min_n=20):
        i, j = bisect.bisect_left(self.ts, a), bisect.bisect_left(self.ts, b)
        return sum(self.px[i:j]) / (j - i) if j - i >= min_n else None

    def last_before(self, B, max_gap=5.0):
        i = bisect.bisect_left(self.ts, B) - 1
        return self.px[i] if i >= 0 and B - self.ts[i] <= max_gap else None

    def first_at(self, B, max_gap=5.0):
        i = bisect.bisect_left(self.ts, B)
        return self.px[i] if i < len(self.ts) and self.ts[i] - B <= max_gap else None


CANDIDATES = {
    "spot just before B":  lambda s, B: s.last_before(B),
    "spot just after B":   lambda s, B: s.first_at(B),
    "mean [B-60, B)":      lambda s, B: s.mean(B - 60, B),
    "mean [B-30, B+30)":   lambda s, B: s.mean(B - 30, B + 30),
    "mean [B, B+60)":      lambda s, B: s.mean(B, B + 60),
    "mean [B-120, B)":     lambda s, B: s.mean(B - 120, B),
}


def load_feed(path):
    pts = {}
    for line in open(path):
        a = line.strip().split(",")
        if len(a) == 2:
            try:
                pts[float(a[0])] = float(a[1])
            except ValueError:
                pass
    ts = sorted(pts)
    return ts, [pts[t] for t in ts]


async def gamma_event(session, start):
    async with session.get("https://gamma-api.polymarket.com/events", params={"slug": f"btc-updown-15m-{start}"},
                           headers=UA) as r:
        d = await r.json() if r.status == 200 else []
    return d[0] if d else None


async def verify(_):
    OUT.mkdir(exist_ok=True)
    wf = OUT / "windows.jsonl"
    if wf.exists():
        rows = [json.loads(l) for l in wf.open()]
        both = [r for r in rows if r["outcome_proxy"] and r["outcome_official"]]
        bad = [r for r in both if r["outcome_proxy"] != r["outcome_official"]]
        print(f"A) live trader: {len(rows)} windows logged, {len(both)} with proxy and official outcome, "
              f"{len(bad)} mismatches ({len(bad)/len(both):.0%})" if both else f"A) live trader: {len(rows)} windows logged, none comparable yet")
        for r in bad:
            print(f"   MISMATCH {r['window']}: proxy {r['outcome_proxy']} (final-strike={r['final_proxy']-r['strike_proxy']:+.2f}) vs official {r['outcome_official']}")
    path = OUT / "feed_chainlink.csv"
    if not path.exists():
        print("B) no data/feed_chainlink.csv yet: run `run` or `measure` for a while first"); return
    ts, px = load_feed(path)
    ser = Series(ts, px)
    B0, B1 = int(math.ceil((ts[0] + 120) / WINDOW)) * WINDOW, int((ts[-1] - 60) // WINDOW) * WINDOW
    Bs = list(range(B0, B1 + 1, WINDOW))
    if not Bs:
        print(f"B) feed log covers only {(ts[-1]-ts[0])/60:.0f} min; needs a full 15m boundary plus 2 min either side"); return
    async with aiohttp.ClientSession() as session:
        ev = {s_: await gamma_event(session, s_) for s_ in range(B0 - WINDOW, B1 + 1, WINDOW)}
    official = {}
    for B in Bs:
        v = ((ev.get(B) or {}).get("eventMetadata") or {}).get("priceToBeat")
        if v is None:
            v = ((ev.get(B - WINDOW) or {}).get("eventMetadata") or {}).get("finalPrice")
        if v is not None:
            official[B] = float(v)
    print(f"\nB) feed log covers {len(Bs)} boundaries; official Polymarket value available for {len(official)} "
          f"(finalPrice/priceToBeat appear ~20 min after the close)")
    if not official:
        return
    print(f"   error = candidate - official, in $ (BTC ~ ${px[-1]:,.0f}); mean = constant basis, std = real definition error")
    print(f"   {'candidate':22s} {'n':>3s} {'mean':>8s} {'std':>8s} {'MAE':>8s} {'max|e|':>8s}")
    best = None
    vals = {}
    for name, f in CANDIDATES.items():
        errs = {B: f(ser, B) - official[B] for B in official if f(ser, B) is not None}
        vals[name] = {B: f(ser, B) for B in errs}
        if len(errs) < 1:
            print(f"   {name:22s}   0   (no coverage)"); continue
        e = list(errs.values())
        sd = statistics.pstdev(e) if len(e) > 1 else float("nan")
        mae = statistics.mean(abs(x) for x in e)
        print(f"   {name:22s} {len(e):3d} {statistics.mean(e):+8.2f} {sd:8.2f} {mae:8.2f} {max(abs(x) for x in e):8.2f}")
        if best is None or mae < best[1]:
            best = (name, mae)
    if best:
        print(f"   best by MAE: {best[0]}   (the model uses `mean [B-60, B)`)")
    # outcome agreement on consecutive official boundaries
    pairs = [(B, B + WINDOW) for B in official if B + WINDOW in official]
    if pairs:
        print(f"\n   outcome agreement over {len(pairs)} windows with both boundaries official:")
        for name in CANDIDATES:
            ok = tot = 0
            for a, b in pairs:
                va, vb = vals[name].get(a), vals[name].get(b)
                if va is None or vb is None:
                    continue
                tot += 1; ok += ((vb >= va) == (official[b] >= official[a]))
            if tot:
                print(f"   {name:22s} {ok}/{tot}")
    if len(official) < 12:
        print("\nNOTE: few boundaries; definitions that differ by less than the price noise cannot be told apart yet. "
              "Keep `run`/`measure` going for hours to separate them.")


# ---------- self-test ----------

def selftest():
    assert abs(norm_cdf(0) - 0.5) < 1e-12
    assert abs(fair_up(100, 100, 1e-4, 300) - 0.5) < 1e-9
    assert fair_up(101, 100, 1e-4, 300) > 0.9 and fair_up(99, 100, 1e-4, 300) < 0.1
    assert fair_up(100.01, 100, 1e-4, 0) == 1.0 and fair_up(99.99, 100, 1e-4, 0) == 0.0
    assert abs(twap_var(1, 60) - twap_var(1, 60 - 1e-9)) < 1e-6          # continuity at 60s
    assert abs(fee_per_share(0.5, 0.07, 1) - 0.0175) < 1e-12 and abs(fee_per_share(0.8, 0.07, 1) - 0.0112) < 1e-12   # docs table: 100 sh = $1.75 / $1.12
    b = Book(); b.snapshot([{"price": "0.4", "size": "10"}], [{"price": "0.5", "size": "10"}, {"price": "0.6", "size": "10"}])
    assert b.best_ask() == 0.5 and b.fill_buy(15) == (15, (10 * 0.5 + 5 * 0.6) / 15) and abs(b.mid() - 0.45) < 1e-12
    b.level("SELL", 0.5, 0); assert b.best_ask() == 0.6
    assert b.fill_buy(100)[0] == 10
    v = Vol()
    for i in range(200):
        v.update(i, 100 * (1 + (1e-4 if i % 2 else -1e-4)))
    assert 1.5e-4 < v.sigma() < 2.5e-4
    assert floor_tick(0.4199, 0.01) == 0.41 and floor_tick(0.58, 0.01) == 0.58

    # lag detection: y is x delayed by 3 bins (+ masked entries) -> peak at L=3
    import random
    rnd = random.Random(1)
    x = [rnd.gauss(0, 1) for _ in range(600)]
    y = [None, None, None] + x[:-3]
    y[50] = None
    L, c = peak_lag(lag_corr(x, y, range(-6, 9)))
    assert L == 3 and c > 0.99, (L, c)

    # maker simulator: queue + price priority + cancel
    import types
    args = types.SimpleNamespace(latency_ms=0, maker_min_edge=0.02, maker_cancel_edge=0.0, maker_max_orders=3,
                                 maker_improve=True, size=10, stop_before=15)
    books = {k: Book() for k in ("up", "down")}
    books["up"].snapshot([{"price": "0.40", "size": "50"}], [{"price": "0.50", "size": "50"}])
    books["down"].snapshot([{"price": "0.50", "size": "50"}], [{"price": "0.60", "size": "50"}])
    fair = {"up": 0.60, "down": 0.40}
    ms = MakerSim(args, 0.01)
    ms.on_event(100.0, 800, "book", None, books, fair, 1.0)
    o = ms.cur
    assert o and o.side == "up" and abs(o.price - 0.41) < 1e-9, (o and (o.side, o.price))   # improves the 0.40 bid by a tick
    ms.on_event(100.1, 800, "trade", ("up", 0.41, 4, "SELL"), books, fair, 1.0)              # we're alone at 0.41: fills 4
    assert abs(o.filled - 4) < 1e-9
    ms.on_event(100.2, 800, "trade", ("up", 0.41, 4, "BUY"), books, fair, 1.0)               # BUY-taker print can't hit our bid
    assert abs(o.filled - 4) < 1e-9
    ms.on_event(100.3, 800, "trade", ("up", 0.40, 99, "SELL"), books, fair, 1.0)             # lower price => we were ahead => full fill
    assert o.state == "done" and abs(o.filled - 10) < 1e-9 and o.reason == "filled"
    ms.on_event(101.0, 700, "book", None, books, fair, 1.0)                                  # position held: no further orders
    assert len(ms.orders) == 1

    args2 = types.SimpleNamespace(**{**vars(args), "maker_improve": False})   # join the bid, queue behind it
    ms2 = MakerSim(args2, 0.01)
    books["up"].snapshot([{"price": "0.40", "size": "50"}, {"price": "0.41", "size": "30"}], [{"price": "0.50", "size": "50"}])
    ms2.on_event(0.0, 800, "book", None, books, fair, 1.0)
    o2 = ms2.cur; assert abs(o2.price - 0.41) < 1e-9
    ms2.on_event(0.1, 800, "trade", ("up", 0.41, 25, "SELL"), books, fair, 1.0)              # 30 ahead of us: 25 doesn't reach us
    assert o2.filled == 0 and abs(o2.queue_ahead - 5) < 1e-9
    ms2.on_event(0.2, 800, "trade", ("up", 0.41, 8, "SELL"), books, fair, 1.0)               # 5 clears queue, 3 fills us
    assert abs(o2.filled - 3) < 1e-9
    ms2.on_event(0.3, 800, "book", None, books, {"up": 0.40, "down": 0.60}, 1.0)             # fair fell below our price => cancel
    assert o2.state == "done" and o2.reason == "cancelled" and abs(o2.filled - 3) < 1e-9
    rec = ms2.records(0, 1.0)[0]
    assert rec["mode"] == "maker" and rec["shares"] == 3 and rec["fee"] == 0
    # verify helpers + boundary_value on a synthetic 1 Hz ramp: price == timestamp
    st = State()
    for i in range(1000, 1300):
        st.push(float(i), float(i))
    assert boundary_value(st, 1200) == statistics.mean(range(1140, 1200))        # mean over [1140, 1200)
    assert boundary_value(st, 1500) is None and boundary_value(st, 1010) is None  # not reached / not covered
    ser = Series([float(i) for i in range(1000, 1300)], [float(i) for i in range(1000, 1300)])
    assert ser.last_before(1200) == 1199 and ser.first_at(1200) == 1200
    assert CANDIDATES["mean [B-60, B)"](ser, 1200) == statistics.mean(range(1140, 1200))
    assert CANDIDATES["mean [B, B+60)"](ser, 1290) is None                       # no coverage after the log ends
    # watchdog: a feed that goes silent after 3 samples must be re-created
    global STALL_S
    calls = []

    async def silent_feed(session):
        calls.append(time.time())
        for i in range(3):
            yield len(calls) * 100.0 + i, 100.0 + i
        await asyncio.sleep(3600)                        # half-open socket: no data, no error

    async def watch():
        FEEDS["fake"] = silent_feed
        st = State()
        t = asyncio.create_task(run_feed("fake", st))
        await asyncio.sleep(2.6)
        t.cancel()
        return st
    old_stall, STALL_S = STALL_S, 0.4
    try:
        st = asyncio.run(watch())
    finally:
        STALL_S = old_stall; FEEDS.pop("fake", None)
    assert len(calls) >= 2, f"feed was not reconnected after going silent (calls={len(calls)})"
    assert st.price is not None and len(st.hist_ts) >= 4        # samples from both connections arrived
    # an order must not go live on an empty book (queue ahead would read 0 after a reconnect)
    ms3 = MakerSim(args, 0.01)
    books3 = {k: Book() for k in ("up", "down")}
    books3["up"].snapshot([{"price": "0.40", "size": "50"}], [{"price": "0.50", "size": "50"}])
    books3["down"].snapshot([{"price": "0.50", "size": "50"}], [{"price": "0.60", "size": "50"}])
    ms3.on_event(0.0, 800, "book", None, books3, fair, 1.0)
    o3 = ms3.cur; assert o3 and o3.side == "up"
    for b_ in books3.values():
        b_.snapshot([], [])                                                   # reconnect wipes the books
    ms3.on_event(1.0, 799, "book", None, books3, fair, 1.0)
    assert o3.state == "pending" and o3.filled == 0
    books3["up"].snapshot([{"price": "0.41", "size": "70"}], [{"price": "0.50", "size": "50"}])
    ms3.on_event(2.0, 798, "book", None, books3, fair, 1.0)
    assert o3.state == "live" and abs(o3.queue_ahead - 70) < 1e-9, (o3.state, o3.queue_ahead)   # queue read from the real book
    print("selftest OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run", help="paper-trade live")
    r.add_argument("--feed", choices=FEEDS, default="chainlink", help="chainlink = actual settlement source (recommended)")
    r.add_argument("--mode", choices=("taker", "maker", "both"), default="both")
    r.add_argument("--size", type=float, default=20, help="shares per paper order")
    r.add_argument("--min-edge", type=float, default=0.04, help="taker: required fair-ask-fee, $/share")
    r.add_argument("--min-p", type=float, default=0.10); r.add_argument("--max-p", type=float, default=0.90)
    r.add_argument("--latency-ms", type=int, default=400, help="simulated order/cancel latency (see `measure` for a real floor)")
    r.add_argument("--stop-before", type=float, default=15, help="no entries / cancel resting orders in last N seconds")
    r.add_argument("--vol-mult", type=float, default=1.0, help="scale model vol (stress test)")
    r.add_argument("--fee-rate", type=float, default=0.07); r.add_argument("--fee-exp", type=float, default=1,
                   help="fallback only: the market's own feeSchedule from Gamma is used when present")
    r.add_argument("--maker-min-edge", type=float, default=0.02, help="maker: required fair - limit price, $/share (no fee)")
    r.add_argument("--maker-cancel-edge", type=float, default=0.0, help="cancel when fair - price falls below this")
    r.add_argument("--maker-max-orders", type=int, default=3, help="max orders per window while unfilled")
    r.add_argument("--maker-improve", action=argparse.BooleanOptionalAction, default=True,
                   help="bid one tick above the best bid (otherwise join it and queue behind)")
    r.add_argument("--max-windows", type=int, default=None)
    m = sp.add_parser("measure", help="measure feed lead/lag and how late Polymarket reacts")
    m.add_argument("--minutes", type=float, default=30)
    m.add_argument("--feeds", default="chainlink,coinbase", help="comma list from: " + ",".join(FEEDS))
    m.add_argument("--bin", type=float, default=0.5, help="sampling bin, seconds")
    m.add_argument("--horizon", type=float, default=1.0, help="price-change horizon, seconds")
    m.add_argument("--max-lag", type=float, default=20, help="largest lag searched, seconds")
    sp.add_parser("report"); sp.add_parser("selftest"); sp.add_parser("verify")
    a = ap.parse_args()
    if a.cmd == "selftest": selftest()
    elif a.cmd == "report": report(a)
    elif a.cmd == "verify": asyncio.run(verify(a))
    else:
        try: asyncio.run(run(a) if a.cmd == "run" else measure(a))
        except KeyboardInterrupt: pass
