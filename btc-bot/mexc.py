"""MEXC spot REST client: public market data (no key) and the few signed calls the bot needs.

Public: klines (paged with startTime+endTime, 500 per request), ticker price, exchange info.
Signed: account, test order, market order. Signature = HMAC-SHA256(secret, query string + body), lowercase hex,
header X-MEXC-APIKEY (https://mexcdevelop.github.io/apidocs/spot_v3_en/).
"""
import hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request

BASE = "https://api.mexc.com"
SYMBOL = "BTCUSDT"
TF_SECONDS = {"15m": 900, "60m": 3600, "4h": 14400, "1d": 86400}
PAGE = 500                      # MEXC returns at most 500 klines per request, whatever `limit` says


def sign(secret, query):
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _urlopen(req, timeout=20):
    return urllib.request.urlopen(req, timeout=timeout)


def http_json(method, url, headers=None, tries=5, opener=None):
    """GET/POST returning parsed JSON, retrying transient failures (TLS timeouts happen) with backoff."""
    opener = opener or _urlopen
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
            with opener(req) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504):
                last = f"HTTP {e.code}"; time.sleep(min(30, 2 ** i))
                continue
            raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = repr(e); time.sleep(min(30, 2 ** i))
    raise RuntimeError(f"gave up after {tries} tries: {last}")


def klines(start_ms, end_ms, interval="60m", symbol=SYMBOL, opener=None):
    """Closed or open candles with open time in [start_ms, end_ms]: [(open_ms, o, h, l, c, volume), ...]."""
    q = urllib.parse.urlencode(dict(symbol=symbol, interval=interval, startTime=int(start_ms), endTime=int(end_ms), limit=PAGE))
    rows = http_json("GET", f"{BASE}/api/v3/klines?{q}", opener=opener)
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def klines_range(start_ms, end_ms, interval="60m", symbol=SYMBOL, pause=0.12, progress=None, opener=None):
    """Page forward over [start_ms, end_ms] in windows of 500 bars (startTime alone is ignored by MEXC)."""
    step = TF_SECONDS[interval] * 1000
    out, t = [], int(start_ms)
    while t <= end_ms:
        rows = klines(t, min(end_ms, t + PAGE * step - 1), interval, symbol, opener=opener)
        out += rows
        if progress:
            progress(len(out), t)
        t += PAGE * step
        time.sleep(pause)
    seen, dedup = set(), []
    for r in sorted(out):
        if r[0] not in seen:
            seen.add(r[0]); dedup.append(r)
    return dedup


def ticker_price(symbol=SYMBOL, opener=None):
    return float(http_json("GET", f"{BASE}/api/v3/ticker/price?symbol={symbol}", opener=opener)["price"])


def exchange_info(symbol=SYMBOL, opener=None):
    return http_json("GET", f"{BASE}/api/v3/exchangeInfo?symbol={symbol}", opener=opener)["symbols"][0]


class Signed:
    """Signed endpoints. `opener` and `clock` are injectable so the request building can be tested without a network."""
    def __init__(self, key, secret, opener=None, clock=time.time):
        self.key, self.secret, self.opener, self.clock = key, secret, opener, clock

    def _query(self, params):
        p = dict(params)
        p["recvWindow"] = 5000
        p["timestamp"] = int(self.clock() * 1000)
        q = urllib.parse.urlencode(p)
        return q + "&signature=" + sign(self.secret, q)

    def call(self, method, path, params=None):
        url = f"{BASE}{path}?{self._query(params or {})}"
        return http_json(method, url, headers={"X-MEXC-APIKEY": self.key, "Content-Type": "application/json"},
                         tries=1 if method == "POST" else 3, opener=self.opener)     # never auto-retry an order

    def account(self):
        return self.call("GET", "/api/v3/account")

    def balances(self):
        return {b["asset"]: float(b["free"]) for b in self.account().get("balances", [])}

    def test_order(self, side, **kw):
        return self.call("POST", "/api/v3/order/test", dict(symbol=SYMBOL, side=side, type="MARKET", **kw))

    def market_buy_quote(self, usdt):
        return self.call("POST", "/api/v3/order", dict(symbol=SYMBOL, side="BUY", type="MARKET", quoteOrderQty=f"{usdt:.2f}"))

    def market_sell_qty(self, btc):
        return self.call("POST", "/api/v3/order", dict(symbol=SYMBOL, side="SELL", type="MARKET", quantity=f"{btc:.6f}"))
