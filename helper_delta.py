"""
helper_delta.py — Delta Exchange broker-abstraction module (Requirements 4-8).

Parallel to the Fyers bot's helper_fyers.py. Exposes price retrieval, order
management, historical OHLC, positions/margin/exits, symbol build/parse/expiry,
and the option-chain module used by Strategy_BTC_Options.py.

NOTE: This is a scaffolding stub. Function bodies are implemented across
Tasks 6, 8, 9, 10, 11, and 13.
"""

import time
from urllib.parse import quote

import requests

from delta_config import (
    REFERENCE_TIMEZONE,
    DATA_FEED_HOST,
    DATA_FEED_PORT,
)
from delta_client import DeltaAPIError

# ============================================================
# MODULE-LEVEL CONFIGURATION CONSTANTS
# ============================================================
# Local data-feed endpoint polled by getLTP (mirrors helper_fyers.getLTP).
LTP_FEED_URL = "http://{}:{}/ltp".format(DATA_FEED_HOST, DATA_FEED_PORT)

# Small throttle after each quote call (mirrors helper_fyers.QUOTE_THROTTLE_SEC).
QUOTE_THROTTLE_SEC = 0.05

# Strike step for BTC daily options (200-point grid).
STRIKE_STEP = 200

# Delta candle resolutions accepted by GET /v2/history/candles.
CANDLE_RESOLUTIONS = ("1m", "3m", "5m", "15m", "30m",
                      "1h", "2h", "4h", "6h", "1d", "1w")

# Maximum candles Delta returns in a single history response (paging trigger).
MAX_CANDLES_PER_PAGE = 2000

# Settling asset whose available balance is read from the wallet.
SETTLING_ASSET = "USD"

# Index-price anchor and perpetual (log-only) symbols.
INDEX_ANCHOR_SYMBOL = ".DEXBTUSD"
PERP_SYMBOL = "BTCUSD"

# Delta does NOT serve index symbols (dot-prefixed, e.g. ".DEXBTUSD") via
# GET /v2/tickers/{symbol} — that endpoint returns {"success": true,
# "result": null} for an index. Instead each index value is exposed as the
# `spot_price` field on its UNDERLYING PERPETUAL ticker. This mapping resolves
# an index symbol to the perpetual whose ticker carries its `spot_price`;
# unknown index symbols fall back to PERP_SYMBOL ("BTCUSD").
INDEX_TO_PERP = {".DEXBTUSD": "BTCUSD"}


# ============================================================
# 4. PRICE RETRIEVAL (Requirement 4) — Task 6.1
# ============================================================
# Up to 3 ADDITIONAL attempts beyond the first (4 total) on transient failure,
# with exponential backoff of 0.3 * 2**n seconds (0.3s, 0.6s, 1.2s). (Req 4.5)
MANUAL_LTP_MAX_RETRIES = 3
MANUAL_LTP_BACKOFF_BASE_SEC = 0.3


class PriceUnavailableError(DeltaAPIError):
    """
    Raised by ``manualLTP`` when no usable price can be derived from the ticker
    fallback ladder after all retries are exhausted (Requirement 4.6).

    The message names the offending `symbol` and carries the last upstream
    response (ticker payload or transport error) in both `symbol` and the
    inherited `payload` attribute, so the failure is loud and actionable rather
    than silently returning a bad/absent price.
    """

    def __init__(self, message, symbol=None, payload=None):
        super().__init__(message, payload=payload)
        self.symbol = symbol


def _coerce_price(value):
    """
    Coerce a raw ticker field to a float, tolerating string values (Delta sends
    quote fields as strings) and missing/garbled data.

    Returns the float value, or ``None`` when the value is absent or cannot be
    parsed as a number.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ticker_result(ticker):
    """
    Extract the ticker `result` object from a parsed Delta response.

    The single-symbol endpoint returns ``{"success": true, "result": {...}}``;
    fall back to treating the payload itself as the result dict when no nested
    `result` key is present, so a schema tweak does not drop the fields.
    """
    if isinstance(ticker, dict):
        result = ticker.get("result")
        if isinstance(result, dict):
            return result
        return ticker
    return {}


def _extract_ladder_price(ticker):
    """
    Apply the bid/ask-mid fallback ladder to a ticker payload (Reqs 4.1-4.4).

    Each candidate must be strictly > 0; the returned value is rounded to two
    decimal places. Returns ``None`` when none of last/bid/ask is positive so
    the caller can retry or raise (Req 4.6). Ladder order:
      1. last traded price (`close`, or `last`/`last_price` defensively) (4.1)
      2. midpoint of best_bid and best_ask when BOTH are > 0            (4.2)
      3. best_ask alone when > 0                                        (4.3)
      4. best_bid alone when > 0                                        (4.4)
    """
    result = _ticker_result(ticker)

    # 1. Last traded price (Delta's `close`; accept `last`/`last_price` too).
    last = None
    for key in ("close", "last", "last_price"):
        last = _coerce_price(result.get(key))
        if last is not None:
            break
    if last is not None and last > 0:
        return round(last, 2)

    quotes = result.get("quotes")
    if not isinstance(quotes, dict):
        quotes = {}
    bid = _coerce_price(quotes.get("best_bid"))
    ask = _coerce_price(quotes.get("best_ask"))

    # 2. Midpoint only when BOTH bid and ask are positive (Req 4.2).
    if bid is not None and bid > 0 and ask is not None and ask > 0:
        return round((bid + ask) / 2.0, 2)

    # 3. Ask alone (Req 4.3).
    if ask is not None and ask > 0:
        return round(ask, 2)

    # 4. Bid alone (Req 4.4).
    if bid is not None and bid > 0:
        return round(bid, 2)

    return None


def _is_transient_delta_error(exc):
    """
    Classify a ``DeltaAPIError`` as transient (worth retrying) or not (Req 4.5).

    A missing status code (network-level mapping) and 429/5xx responses are
    treated as transient; well-formed client errors (e.g. a 4xx for a bad
    symbol) are not retried.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        return True
    return status == 429 or status >= 500


def getLTP(instrument):
    """Poll the local data feed for the current LTP (mirrors helper_fyers.getLTP)."""
    raise NotImplementedError("Implemented in Task 6/7")


def manualLTP(symbol, client):
    """
    Live price with the bid/ask-mid fallback ladder sourced from
    GET /v2/tickers/{symbol}. (Task 6.1, Requirements 4.1-4.6)

    Sources the ticker from ``GET /v2/tickers/{symbol}`` (symbol path-encoded)
    and applies the fallback ladder in ``_extract_ladder_price``: last traded
    price (4.1), else bid/ask midpoint when both are positive (4.2), else ask
    (4.3), else bid (4.4), each rounded to two decimal places.

    On transient failure — a ``requests`` transport error or a retryable
    ``DeltaAPIError`` (429/5xx/network) — it retries up to
    ``MANUAL_LTP_MAX_RETRIES`` additional attempts with exponential backoff
    (0.3 * 2**n seconds); a well-formed ticker with no usable price is likewise
    re-read on the remaining attempts (4.5). When no usable price remains after
    all attempts, it raises ``PriceUnavailableError`` identifying the `symbol`
    and the last upstream response, and returns no price (4.6).

    Delta INDEX symbols are dot-prefixed (e.g. ".DEXBTUSD") and are NOT served
    by GET /v2/tickers/{symbol} (that returns result:null). Such symbols are
    delegated to ``getIndexPrice``, which resolves the index to its underlying
    perpetual ticker and reads the `spot_price` field. This makes both the
    strategy's index reads and the local data feed work for index symbols with
    no change at the call sites. Non-dot symbols keep the perpetual/option
    ladder below unchanged.
    """
    # Route Delta index symbols (dot-prefixed) to the index resolver.
    if str(symbol).startswith("."):
        return getIndexPrice(client, str(symbol))

    path = "/v2/tickers/{}".format(quote(str(symbol), safe=""))
    last_upstream = None
    total_attempts = MANUAL_LTP_MAX_RETRIES + 1

    for attempt in range(total_attempts):
        retry_remaining = attempt < MANUAL_LTP_MAX_RETRIES

        try:
            ticker = client.request("GET", path)
        except requests.exceptions.RequestException as exc:
            # Network timeout / connection error: always transient (Req 4.5).
            last_upstream = repr(exc)
            if retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            break
        except DeltaAPIError as exc:
            last_upstream = exc.payload if exc.payload is not None else str(exc)
            if _is_transient_delta_error(exc) and retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            # Non-transient upstream error: stop retrying and raise below.
            break

        # A well-formed ticker was returned; remember it as the last response.
        last_upstream = ticker
        price = _extract_ladder_price(ticker)
        if price is not None:
            return price

        # Well-formed ticker but no positive last/bid/ask: re-read on the
        # remaining attempts, then raise the identifying error (Req 4.6).
        if retry_remaining:
            time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
            continue

    raise PriceUnavailableError(
        "manualLTP could not obtain a usable price for symbol {!r} after {} "
        "attempt(s); last upstream response: {!r}".format(
            symbol, total_attempts, last_upstream),
        symbol=symbol,
        payload=last_upstream,
    )


# ============================================================
# 5. ORDER MANAGEMENT (Requirement 5) — Tasks 9.1, 9.3
# ============================================================
def resolveProductId(symbol, client):
    """Resolve a symbol to its integer product_id via GET /v2/products (cached).
    (Tasks 8.1/9.1, Requirements 5.1, 8.4)"""
    raise NotImplementedError("Implemented in Task 8.1/9.1")


def placeOrder(symbol, side, size, order_type="market_order",
               limit_price=None, client=None, papertrading=0):
    """
    Place a market or limit order via POST /v2/orders after product resolution,
    with input validation and paper-trading gating.
    (Task 9.1, Requirements 5.1-5.5, 5.10, 5.11)
    """
    raise NotImplementedError("Implemented in Task 9.1")


def placeBracketOrder(symbol, side, size, stop_loss_price=None,
                      take_profit_price=None, trail_amount=None,
                      stop_trigger_method="last_traded_price",
                      client=None, papertrading=0):
    """
    Place a native/attached bracket order with optional trailing stop.
    (Task 9.3, Requirements 5.6, 5.7)
    """
    raise NotImplementedError("Implemented in Task 9.3")


def cancelOrder(order_id, product_id, client):
    """Cancel an open order via DELETE /v2/orders. (Task 9.3, Requirement 5.8)"""
    raise NotImplementedError("Implemented in Task 9.3")


# ============================================================
# 6. HISTORICAL OHLC (Requirement 6) — Task 10.1
# ============================================================
def getHistorical(symbol, interval, duration, client):
    """
    Retrieve and resample OHLC candles from GET /v2/history/candles, paging
    across duration and converting timestamps to the Reference_Timezone (UTC).
    (Task 10.1, Requirements 6.1-6.8)
    """
    raise NotImplementedError("Implemented in Task 10.1")


# ============================================================
# 7. POSITIONS, MARGIN, EXITS (Requirement 7) — Task 11.1
# ============================================================
def getBalance(settling_asset=SETTLING_ASSET, client=None):
    """Return available_balance for the Settling_Asset from GET /v2/wallet/balances.
    (Task 11.1, Requirements 7.1, 7.6)"""
    raise NotImplementedError("Implemented in Task 11.1")


def getPositions(symbol, client):
    """Return (size, entry_price) from GET /v2/positions.
    (Task 11.1, Requirement 7.2)"""
    raise NotImplementedError("Implemented in Task 11.1")


def getSpreadMargin(legs, client):
    """Compute (required_margin, available_balance) from contract size, price,
    and leverage. (Task 11.1, Requirements 7.3, 7.7)"""
    raise NotImplementedError("Implemented in Task 11.1")


def exitAll(close_scope=None, client=None):
    """Flatten in-scope positions via POST /v2/positions/close_all, signaling
    full vs partial success. (Task 11.1, Requirements 7.4, 7.5)"""
    raise NotImplementedError("Implemented in Task 11.1")


# ============================================================
# 8. SYMBOL FORMATTING / PARSING / EXPIRY (Requirement 8) — Task 8.1
# ============================================================
def buildSymbol(product_type, underlying, quoting=None,
                option_type=None, strike=None, expiry_ddmmyy=None):
    """
    Build a Delta symbol: perpetual (BTCUSD), option
    (C-BTC-83600-240926), or spot (BTC_INR); reject/ name invalid params.
    (Task 8.1, Requirements 8.1-8.3, 8.5)
    """
    raise NotImplementedError("Implemented in Task 8.1")


def parseSymbol(symbol):
    """
    Parse a conforming symbol into {option_type, underlying, strike, expiry};
    reject malformed symbols. (Task 8.1, Requirements 8.6, 8.7)
    """
    raise NotImplementedError("Implemented in Task 8.1")


def getDailyExpiry(now_utc=None):
    """
    Return the current daily expiry in DDMMYY, rolling to the next day after
    12:00 UTC. (Task 8.1, Requirements 8.3, 9.23)
    """
    raise NotImplementedError("Implemented in Task 8.1")


# ============================================================
# OPTION-CHAIN MODULE (Requirements 9.6-9.9, 9.11, 9.12) — Task 13
# ============================================================
# NOTE: getIndexPrice (Req 9.11) is implemented below. The remaining Task 13
# option-chain functions — fetchOptionChain, buildChainSnapshot, computePCR,
# and computeSameStrikeOIDelta — are still separate/pending.
def fetchOptionChain(underlying, expiry_ddmmyyyy, client):
    """Fetch the option chain via GET /v2/tickers. (Task 13.1, Requirement 9.6)"""
    raise NotImplementedError("Implemented in Task 13.1")


def buildChainSnapshot(chain, atm_strike, step=STRIKE_STEP, n=8):
    """Build the 17-strike snapshot (ATM +/- 8 at 200-pt spacing).
    (Task 13.1, Requirement 9.6)"""
    raise NotImplementedError("Implemented in Task 13.1")


def computePCR(snapshot, central=9):
    """Compute PCR over the 9 central strikes. (Task 13.3, Requirement 9.7)"""
    raise NotImplementedError("Implemented in Task 13.3")


def computeSameStrikeOIDelta(prev_snapshot, curr_snapshot):
    """Same-strike consecutive-snapshot OI delta (no strike shift).
    (Task 13.3, Requirement 9.8)"""
    raise NotImplementedError("Implemented in Task 13.3")


def _extract_index_price(ticker):
    """
    Read an index value from an underlying PERPETUAL ticker payload.

    Delta exposes the index (e.g. ".DEXBTUSD") as the `spot_price` field on the
    perpetual ticker (BTCUSD). Preference order, each must be strictly > 0 and
    rounded to two decimal places:
      1. `spot_price` — this IS the index value (sent as a string, coerced).
      2. `mark_price` — mark price fallback (also a string).
      3. `close` / `last` — last traded price as a last resort.
    Returns ``None`` when none is positive so the caller can retry or raise.
    """
    result = _ticker_result(ticker)

    # 1. spot_price is the index value itself (Delta sends it as a string).
    spot = _coerce_price(result.get("spot_price"))
    if spot is not None and spot > 0:
        return round(spot, 2)

    # 2. mark_price fallback (also a string in Delta responses).
    mark = _coerce_price(result.get("mark_price"))
    if mark is not None and mark > 0:
        return round(mark, 2)

    # 3. Last traded price as a last resort.
    last = None
    for key in ("close", "last", "last_price"):
        last = _coerce_price(result.get(key))
        if last is not None:
            break
    if last is not None and last > 0:
        return round(last, 2)

    return None


def getIndexPrice(client, index_symbol=INDEX_ANCHOR_SYMBOL):
    """
    Read a Delta INDEX value (e.g. ".DEXBTUSD" -> ~84010) by resolving it to its
    underlying perpetual ticker. (Task 13.1, Requirement 9.11)

    Delta does NOT serve index symbols via GET /v2/tickers/{index_symbol} — that
    returns ``{"success": true, "result": null}``. Instead the index value is
    published as the `spot_price` field on the UNDERLYING PERPETUAL ticker. This
    function maps `index_symbol` to its perpetual via ``INDEX_TO_PERP`` (default
    ``PERP_SYMBOL`` == "BTCUSD" for unknown index symbols), fetches
    ``GET /v2/tickers/{perp_symbol}``, and reads the value with the preference
    order in ``_extract_index_price`` (`spot_price`, then `mark_price`, then
    `close`/`last`), each > 0 and rounded to two decimal places.

    It mirrors ``manualLTP``'s transient-retry behavior: a ``requests`` transport
    error or a retryable ``DeltaAPIError`` (429/5xx/network), or a well-formed
    ticker with no usable value, is re-read up to ``MANUAL_LTP_MAX_RETRIES``
    additional attempts with exponential backoff (0.3 * 2**n seconds). When no
    usable value remains after all attempts it raises ``PriceUnavailableError``
    naming `index_symbol` and carrying the last upstream response (Req 4.6).

    Returns the index price as a float.
    """
    perp_symbol = INDEX_TO_PERP.get(str(index_symbol), PERP_SYMBOL)
    path = "/v2/tickers/{}".format(quote(str(perp_symbol), safe=""))
    last_upstream = None
    total_attempts = MANUAL_LTP_MAX_RETRIES + 1

    for attempt in range(total_attempts):
        retry_remaining = attempt < MANUAL_LTP_MAX_RETRIES

        try:
            ticker = client.request("GET", path)
        except requests.exceptions.RequestException as exc:
            # Network timeout / connection error: always transient (Req 4.5).
            last_upstream = repr(exc)
            if retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            break
        except DeltaAPIError as exc:
            last_upstream = exc.payload if exc.payload is not None else str(exc)
            if _is_transient_delta_error(exc) and retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            # Non-transient upstream error: stop retrying and raise below.
            break

        # A well-formed ticker was returned; remember it as the last response.
        last_upstream = ticker
        price = _extract_index_price(ticker)
        if price is not None:
            return price

        # Well-formed ticker but no positive spot/mark/last: re-read on the
        # remaining attempts, then raise the identifying error (Req 4.6).
        if retry_remaining:
            time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
            continue

    raise PriceUnavailableError(
        "getIndexPrice could not obtain a usable index value for {!r} (via "
        "perpetual {!r}) after {} attempt(s); last upstream response: "
        "{!r}".format(index_symbol, perp_symbol, total_attempts, last_upstream),
        symbol=index_symbol,
        payload=last_upstream,
    )
