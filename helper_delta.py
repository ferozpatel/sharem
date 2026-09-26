"""
helper_delta.py — Delta Exchange broker-abstraction module (Requirements 4-8).

Parallel to the Fyers bot's helper_fyers.py. Exposes price retrieval, order
management, historical OHLC, positions/margin/exits, symbol build/parse/expiry,
and the option-chain module used by Strategy_BTC_Options.py.

NOTE: This is a scaffolding stub. Function bodies are implemented across
Tasks 6, 8, 9, 10, 11, and 13.
"""

import re
import time
from datetime import datetime, timedelta
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
# Per-session symbol -> integer product_id cache. Populated in full on the
# first resolveProductId call so the (large) GET /v2/products response is
# fetched at most once per process/session (Reqs 5.1, 8.4).
_PRODUCT_ID_CACHE = {}


def resolveProductId(symbol, client):
    """Resolve a symbol to its integer product_id via GET /v2/products (cached).
    (Tasks 8.1/9.1, Requirements 5.1, 8.4)

    On a cache miss the entire product list is fetched once via
    ``client.request("GET", "/v2/products")`` (response shape
    ``{"result": [{"id": <int>, "symbol": "..."}, ...]}``) and the whole
    symbol->id map is cached in the module-level ``_PRODUCT_ID_CACHE`` so
    repeated lookups — and lookups of other symbols — never re-fetch. Returns
    the integer `product_id`. If the symbol is absent from Delta's product list,
    a ``DeltaAPIError`` naming the `symbol` is raised.
    """
    key = str(symbol)
    if key in _PRODUCT_ID_CACHE:
        return _PRODUCT_ID_CACHE[key]

    response = client.request("GET", "/v2/products")
    products = response.get("result") if isinstance(response, dict) else None
    if not isinstance(products, list):
        products = []

    for product in products:
        if not isinstance(product, dict):
            continue
        sym = product.get("symbol")
        pid = product.get("id")
        if sym is None or pid is None:
            continue
        try:
            _PRODUCT_ID_CACHE[str(sym)] = int(pid)
        except (TypeError, ValueError):
            # Skip malformed rows rather than caching a non-integer id.
            continue

    if key in _PRODUCT_ID_CACHE:
        return _PRODUCT_ID_CACHE[key]

    raise DeltaAPIError(
        "resolveProductId could not find a product_id for symbol {!r} in the "
        "Delta product list (GET /v2/products).".format(symbol),
        payload=symbol,
    )


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
class SymbolError(ValueError):
    """
    Raised by ``buildSymbol``/``parseSymbol`` when a symbol cannot be
    constructed or parsed (Requirements 8.5, 8.7).

    The message NAMES the offending `parameter` (for a build request) or states
    that the `symbol` is malformed (for a parse request), so the failure is
    actionable and no symbol / no product-parameter set is returned. Being a
    ``ValueError`` subclass, callers may catch it as either type.
    """

    def __init__(self, message, parameter=None, symbol=None):
        super().__init__(message)
        self.parameter = parameter
        self.symbol = symbol


# Canonical product_type -> handler, with a few sensible aliases accepted.
_PERPETUAL_TYPES = {"perpetual", "perp", "future", "futures"}
_OPTION_TYPES = {"option", "options"}
_SPOT_TYPES = {"spot"}

# Delta option symbol shape: OptionType-Underlying-Strike-ExpiryDDMMYY.
_OPTION_SYMBOL_RE = re.compile(r"^([CP])-([A-Z0-9]+)-(\d+)-(\d{6})$")
_EXPIRY_DDMMYY_RE = re.compile(r"^\d{6}$")


def buildSymbol(product_type, underlying, quoting=None,
                option_type=None, strike=None, expiry_ddmmyy=None):
    """
    Build a Delta symbol: perpetual (``BTCUSD``), option
    (``C-BTC-83600-240926``), or spot (``BTC_INR``); reject and NAME invalid
    params. (Task 8.1, Requirements 8.1-8.3, 8.5)

    `product_type` (case-insensitive) selects the format and accepts a few
    aliases: perpetual/perp/future/futures -> perpetual, option/options ->
    option, spot -> spot. Any missing/invalid required parameter raises a
    ``SymbolError`` whose message and `parameter` attribute name the offender,
    and NO symbol is returned (Req 8.5):

      * perpetual: requires `underlying` + `quoting` -> f"{U}{Q}" (e.g. BTCUSD)
      * option: requires `option_type` in {C,P} (lower-case accepted and
        upper-cased), `underlying`, `strike` (a positive integer aligned to the
        ``STRIKE_STEP`` 200-point grid), and `expiry_ddmmyy` (6-digit DDMMYY
        string) -> f"{OPT}-{U}-{STRIKE}-{EXPIRY}" (e.g. C-BTC-83600-240926)
      * spot: requires `underlying` + `quoting` -> f"{U}_{Q}" (e.g. BTC_INR)
    """
    if product_type is None:
        raise SymbolError(
            "buildSymbol: 'product_type' is required (one of perpetual, "
            "option, spot).", parameter="product_type")
    kind = str(product_type).strip().lower()

    # `underlying` is required for every product type.
    if underlying is None or str(underlying).strip() == "":
        raise SymbolError(
            "buildSymbol: 'underlying' is required and must be non-empty.",
            parameter="underlying")
    under = str(underlying).strip().upper()

    if kind in _PERPETUAL_TYPES:
        if quoting is None or str(quoting).strip() == "":
            raise SymbolError(
                "buildSymbol: 'quoting' is required for a perpetual symbol.",
                parameter="quoting")
        quote_asset = str(quoting).strip().upper()
        return "{}{}".format(under, quote_asset)

    if kind in _SPOT_TYPES:
        if quoting is None or str(quoting).strip() == "":
            raise SymbolError(
                "buildSymbol: 'quoting' is required for a spot symbol.",
                parameter="quoting")
        quote_asset = str(quoting).strip().upper()
        return "{}_{}".format(under, quote_asset)

    if kind in _OPTION_TYPES:
        # option_type in {C, P} (accept lower case and map to upper).
        if option_type is None or str(option_type).strip() == "":
            raise SymbolError(
                "buildSymbol: 'option_type' is required for an option symbol "
                "(expected 'C' or 'P').", parameter="option_type")
        opt = str(option_type).strip().upper()
        if opt not in ("C", "P"):
            raise SymbolError(
                "buildSymbol: 'option_type' must be 'C' or 'P', got "
                "{!r}.".format(option_type), parameter="option_type")

        # strike: a positive integer aligned to the 200-point grid.
        if strike is None:
            raise SymbolError(
                "buildSymbol: 'strike' is required for an option symbol.",
                parameter="strike")
        try:
            strike_int = int(strike)
        except (TypeError, ValueError):
            raise SymbolError(
                "buildSymbol: 'strike' must be an integer, got "
                "{!r}.".format(strike), parameter="strike")
        # Reject if the float/str carried a fractional part (e.g. 83600.5).
        if isinstance(strike, float) and not float(strike).is_integer():
            raise SymbolError(
                "buildSymbol: 'strike' must be a whole number, got "
                "{!r}.".format(strike), parameter="strike")
        if strike_int <= 0:
            raise SymbolError(
                "buildSymbol: 'strike' must be positive, got "
                "{!r}.".format(strike), parameter="strike")
        if strike_int % STRIKE_STEP != 0:
            raise SymbolError(
                "buildSymbol: 'strike' {} is not aligned to the {}-point "
                "strike step.".format(strike_int, STRIKE_STEP),
                parameter="strike")

        # expiry_ddmmyy: a 6-digit DDMMYY string.
        if expiry_ddmmyy is None:
            raise SymbolError(
                "buildSymbol: 'expiry_ddmmyy' is required for an option "
                "symbol (DDMMYY).", parameter="expiry_ddmmyy")
        expiry = str(expiry_ddmmyy).strip()
        if not _EXPIRY_DDMMYY_RE.match(expiry):
            raise SymbolError(
                "buildSymbol: 'expiry_ddmmyy' must be a 6-digit DDMMYY "
                "string, got {!r}.".format(expiry_ddmmyy),
                parameter="expiry_ddmmyy")

        return "{}-{}-{}-{}".format(opt, under, strike_int, expiry)

    raise SymbolError(
        "buildSymbol: unknown 'product_type' {!r} (expected one of perpetual, "
        "option, spot).".format(product_type), parameter="product_type")


def parseSymbol(symbol):
    """
    Parse a conforming OPTION symbol into
    ``{option_type, underlying, strike, expiry}``; reject malformed symbols.
    (Task 8.1, Requirements 8.6, 8.7)

    The symbol must match ``OptionType-Underlying-Strike-ExpiryDDMMYY`` exactly:
    four dash-separated parts, the first ``C`` or ``P``, the third an integer
    strike, the fourth a 6-digit DDMMYY expiry. On success it returns
    ``{"option_type": "C"/"P", "underlying": "BTC", "strike": <int>,
    "expiry": "240926"}`` with `strike` as an ``int`` so the build->parse round
    trip (Req 8.8) yields the original parameter set. Any non-conforming input
    raises a ``SymbolError`` naming the symbol as malformed, and NO product
    parameters are returned (Req 8.7).
    """
    if symbol is None:
        raise SymbolError(
            "parseSymbol: symbol is required and must be a non-empty string.",
            symbol=symbol)
    text = str(symbol).strip()
    match = _OPTION_SYMBOL_RE.match(text)
    if not match:
        raise SymbolError(
            "parseSymbol: malformed option symbol {!r}; expected "
            "'OptionType-Underlying-Strike-ExpiryDDMMYY' (e.g. "
            "'C-BTC-83600-240926').".format(symbol), symbol=symbol)

    option_type, underlying, strike, expiry = match.groups()
    return {
        "option_type": option_type,
        "underlying": underlying,
        "strike": int(strike),
        "expiry": expiry,
    }


def getDailyExpiry(now_utc=None):
    """
    Return the current daily expiry in ``DDMMYY``, rolling to the NEXT day
    at/after 12:00 UTC. (Task 8.1, Requirements 8.3, 9.23)

    Delta BTC daily options expire at 12:00 UTC. Before 12:00 UTC the current
    contract expires TODAY, so today's date is returned; at/after 12:00 UTC the
    contract has rolled, so the next day's date is returned. `now_utc` may be a
    timezone-aware datetime (converted to UTC) or a naive datetime (assumed to
    already be UTC); when omitted, ``datetime.now(REFERENCE_TIMEZONE)`` (UTC) is
    used. The result is formatted ``DDMMYY`` (e.g. 26 Sep 2024 -> "260924").
    """
    if now_utc is None:
        now = datetime.now(REFERENCE_TIMEZONE)
    elif now_utc.tzinfo is not None:
        # Aware datetime: normalize to the UTC reference timezone.
        now = now_utc.astimezone(REFERENCE_TIMEZONE)
    else:
        # Naive datetime: assume it is already expressed in UTC.
        now = now_utc

    # At/after 12:00 UTC the daily contract has rolled to the next day.
    if now.hour >= 12:
        now = now + timedelta(days=1)

    return now.strftime("%d%m%y")


# ============================================================
# OPTION-CHAIN MODULE (Requirements 9.6-9.9, 9.11, 9.12) — Task 13
# ============================================================
# NOTE: getIndexPrice (Req 9.11) is implemented below. The remaining Task 13
# option-chain functions — fetchOptionChain, buildChainSnapshot, computePCR,
# and computeSameStrikeOIDelta — are still separate/pending.
# Field names (documented) that carry the exchange 6-hour OI-change value.
# Delta's ticker `oi` array maps its second element to `oi_change_usd_6h`
# ("Change in open interest value in settling symbol"); the REST tickers row
# flattens fields, so accept the documented name first and then defensively
# scan for any field whose name signals a 6-hour OI change. This value is
# captured for LOGGING ONLY (Req 9.9) and must never feed signal logic.
_OI_CHANGE_6H_KEYS = ("oi_change_usd_6h", "oi_value_change_6h", "oi_change_6h")


def _extract_oi_change_6h(row):
    """
    Pull the exchange-provided 6-hour OI-change value from a raw ticker row,
    for logging only (Req 9.9).

    Prefers the documented ``oi_change_usd_6h`` field, then a couple of known
    variants, then defensively scans for any key whose name contains both "6h"
    and "oi" (e.g. a future rename). Returns the coerced float, or ``None`` when
    no such field is present or it cannot be parsed as a number.
    """
    if not isinstance(row, dict):
        return None
    for key in _OI_CHANGE_6H_KEYS:
        if key in row:
            return _coerce_price(row.get(key))
    for key, value in row.items():
        lowered = str(key).lower()
        if "6h" in lowered and "oi" in lowered:
            return _coerce_price(value)
    return None


def _normalize_chain_row(row):
    """
    Normalize one raw Delta ticker row into a clean option-chain dict.

    Returns ``{"symbol", "contract_type", "strike", "oi", "best_bid",
    "best_ask", "price", "oi_change_6h"}`` where `strike` and `oi` are coerced
    to numbers (absolute open interest; `oi` defaults to ``0.0`` when absent so
    downstream PCR/OI-delta sums never crash), `best_bid`/`best_ask` come from
    `quotes`, `price` prefers the last traded price (`close`/`last`) and falls
    back to `mark_price`, and `oi_change_6h` is the log-only exchange 6-hour
    OI-change field (Req 9.9). Returns ``None`` for a row with no usable strike.
    """
    if not isinstance(row, dict):
        return None
    strike = _coerce_price(row.get("strike_price"))
    if strike is None:
        return None

    quotes = row.get("quotes")
    if not isinstance(quotes, dict):
        quotes = {}
    best_bid = _coerce_price(quotes.get("best_bid"))
    best_ask = _coerce_price(quotes.get("best_ask"))

    # Price: last traded (`close`/`last`/`last_price`), else mark price.
    price = None
    for key in ("close", "last", "last_price"):
        price = _coerce_price(row.get(key))
        if price is not None:
            break
    if price is None:
        price = _coerce_price(row.get("mark_price"))

    oi = _coerce_price(row.get("oi"))

    return {
        "symbol": row.get("symbol"),
        "contract_type": row.get("contract_type"),
        "strike": strike,
        "oi": oi if oi is not None else 0.0,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "price": price,
        "oi_change_6h": _extract_oi_change_6h(row),
    }


def _nearest_atm_strike(price, step=STRIKE_STEP):
    """
    Return the strike nearest to `price` aligned to the `step` grid (default
    200), i.e. the at-the-money strike. Returns ``None`` for an unusable price.

    Provided as a small convenience so callers (and the strategy) can derive the
    ATM strike from the ``.DEXBTUSD`` index price; ``buildChainSnapshot`` also
    aligns its `atm_strike` through this so an off-grid input still yields a
    grid-aligned 17-strike window.
    """
    coerced = _coerce_price(price)
    if coerced is None:
        return None
    return int(round(coerced / step) * step)


def expiry_ddmmyy_to_ddmmyyyy(expiry_ddmmyy):
    """
    Convert a 6-digit ``DDMMYY`` daily-expiry string (the symbol format from
    ``getDailyExpiry``) to the ``DD-MM-YYYY`` format the ``/v2/tickers``
    ``expiry_date`` query parameter expects (Req 9.6).

    E.g. ``"260924" -> "26-09-2024"``. The two-digit year is expanded to
    ``20YY``. Raises ``ValueError`` for a non-6-digit input.
    """
    text = str(expiry_ddmmyy).strip()
    if not _EXPIRY_DDMMYY_RE.match(text):
        raise ValueError(
            "expiry_ddmmyy_to_ddmmyyyy: expected a 6-digit DDMMYY string, got "
            "{!r}.".format(expiry_ddmmyy))
    return "{}-{}-20{}".format(text[0:2], text[2:4], text[4:6])


def fetchOptionChain(underlying, expiry_ddmmyyyy, client):
    """
    Fetch the BTC option chain for one expiry via GET /v2/tickers. (Task 13.1,
    Requirements 9.6, 9.9)

    Issues ``GET /v2/tickers`` with query params
    ``contract_types=call_options,put_options``,
    ``underlying_asset_symbols=<underlying>`` (e.g. "BTC"), and
    ``expiry_date=<expiry_ddmmyyyy>`` in ``DD-MM-YYYY`` format (note: this is
    the dashed four-digit-year form, distinct from the ``DDMMYY`` symbol form —
    see ``expiry_ddmmyy_to_ddmmyyyy``). The DeltaSigner builds the query string
    from ``params``.

    Returns the list of ticker rows normalized to
    ``{"symbol", "contract_type", "strike", "oi", "best_bid", "best_ask",
    "price", "oi_change_6h"}`` (see ``_normalize_chain_row``); the log-only
    exchange 6-hour OI-change field is retained under ``oi_change_6h`` (Req 9.9)
    and must never feed signal logic.

    Mirrors ``manualLTP``'s transient-retry behavior: a ``requests`` transport
    error or a retryable ``DeltaAPIError`` (429/5xx/network) is retried up to
    ``MANUAL_LTP_MAX_RETRIES`` additional attempts with exponential backoff
    (0.3 * 2**n seconds). Returns an empty list only when Delta genuinely
    returns no rows; a non-transient upstream error, or a transient one that
    survives all retries, is raised.
    """
    params = {
        "contract_types": "call_options,put_options",
        "underlying_asset_symbols": str(underlying),
        "expiry_date": str(expiry_ddmmyyyy),
    }
    total_attempts = MANUAL_LTP_MAX_RETRIES + 1

    for attempt in range(total_attempts):
        retry_remaining = attempt < MANUAL_LTP_MAX_RETRIES

        try:
            response = client.request("GET", "/v2/tickers", params=params)
        except requests.exceptions.RequestException:
            # Network timeout / connection error: always transient (Req 4.5).
            if retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            raise
        except DeltaAPIError as exc:
            if _is_transient_delta_error(exc) and retry_remaining:
                time.sleep(MANUAL_LTP_BACKOFF_BASE_SEC * (2 ** attempt))
                continue
            # Non-transient upstream error: surface it (hard failure).
            raise

        rows = response.get("result") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            rows = []

        normalized = []
        for row in rows:
            clean = _normalize_chain_row(row)
            if clean is not None:
                normalized.append(clean)
        return normalized

    # Defensive: the loop returns on success and re-raises on the final failed
    # attempt, so this is unreachable in practice.
    raise DeltaAPIError(
        "fetchOptionChain exhausted retries without a result for underlying "
        "{!r} expiry {!r}.".format(underlying, expiry_ddmmyyyy))


def buildChainSnapshot(chain, atm_strike, step=STRIKE_STEP, n=8):
    """
    Build the 17-strike option-chain snapshot anchored at the ATM strike.
    (Task 13.1, Requirement 9.6)

    `atm_strike` is the at-the-money strike (nearest `step`=200 multiple to the
    ``.DEXBTUSD`` index price; the strategy computes it via ``getIndexPrice`` +
    ``_nearest_atm_strike``). It is aligned to the `step` grid defensively so an
    off-grid input still yields a grid-aligned window. The window spans ATM ± n
    strikes at `step` spacing → ``2*n + 1`` strikes (17 when n=8), returned in
    ascending order.

    For each strike the matching call and put rows are pulled from `chain`
    (matched by rounded strike and normalized `contract_type`). A strike with no
    matching row is represented with ``None`` rather than crashing.

    Returns::

        {
          "atm_strike": <int>,          # grid-aligned ATM
          "step": <int>,                # strike spacing
          "n": <int>,                   # strikes each side of ATM
          "strikes": [<int>, ...],      # 2n+1 strikes ascending
          "rows": { <strike>: {"call": <row|None>, "put": <row|None>}, ... },
        }

    consumable by ``computePCR`` (sums put/call OI over the 9 central strikes)
    and by the strategy's logging. Each row retains its log-only `oi_change_6h`
    (Req 9.9), which must NOT be used for signal logic.
    """
    atm = _nearest_atm_strike(atm_strike, step)
    if atm is None:
        # Fall back to a best-effort integer cast so a bad anchor is loud but
        # does not crash the whole cycle.
        atm = int(atm_strike)

    strikes = [atm + i * step for i in range(-n, n + 1)]

    # Index chain rows by rounded strike, split into calls and puts.
    calls = {}
    puts = {}
    for row in (chain or []):
        if not isinstance(row, dict):
            continue
        raw_strike = row.get("strike")
        strike_val = _coerce_price(raw_strike)
        if strike_val is None:
            continue
        strike_key = int(round(strike_val))
        ctype = str(row.get("contract_type") or "").strip().lower()
        if "call" in ctype or ctype == "c":
            calls[strike_key] = row
        elif "put" in ctype or ctype == "p":
            puts[strike_key] = row

    rows = {}
    for strike in strikes:
        key = int(strike)
        rows[key] = {
            "call": calls.get(key),
            "put": puts.get(key),
        }

    return {
        "atm_strike": atm,
        "step": step,
        "n": n,
        "strikes": strikes,
        "rows": rows,
    }


def _row_oi(row):
    """
    Return the absolute open interest of a normalized chain row as a float.

    Rows come from ``_normalize_chain_row`` where `oi` already defaults to
    ``0.0`` when absent, but this helper is defensive: a ``None`` row (no
    matching contract at a strike), a non-dict, or a missing/garbled `oi`
    coerces to ``0.0`` so PCR/OI-delta sums never crash. The log-only
    ``oi_change_6h`` field is intentionally ignored here (Reqs 9.7, 9.8, 9.9).
    """
    if not isinstance(row, dict):
        return 0.0
    oi = _coerce_price(row.get("oi"))
    return oi if oi is not None else 0.0


def _central_strikes(snapshot, central):
    """
    Return the middle `central` strikes of a snapshot, centered on the ATM.

    The snapshot's ``strikes`` list is ascending and (for a full 17-strike
    window) has the ATM in the middle, so the central slice is symmetric about
    it — for `central`=9 over 17 strikes this is indices 4..12 (ATM ± 4). The
    slice is computed generally: when there are fewer strikes than `central`
    (or `central` is non-positive), all available strikes are returned. When
    possible the window is centered on ``atm_strike`` so the central band tracks
    the ATM even if the strike list is asymmetric.
    """
    strikes = snapshot.get("strikes") if isinstance(snapshot, dict) else None
    if not isinstance(strikes, list) or not strikes:
        return []

    total = len(strikes)
    if central is None or central <= 0 or central >= total:
        return list(strikes)

    # Prefer centering the window on the ATM strike when it is present in the
    # list; otherwise fall back to the geometric middle of the list.
    atm = snapshot.get("atm_strike")
    center_idx = total // 2
    if atm is not None:
        try:
            center_idx = strikes.index(int(atm))
        except (ValueError, TypeError):
            center_idx = total // 2

    half = central // 2
    start = center_idx - half
    # Clamp the window inside the list while keeping exactly `central` strikes.
    if start < 0:
        start = 0
    if start + central > total:
        start = total - central
    return list(strikes[start:start + central])


def computePCR(snapshot, central=9):
    """
    Compute the Put-Call Ratio over the 9 central strikes. (Task 13.3, Req 9.7)

    PCR = (Σ put `oi`) / (Σ call `oi`) summed over ONLY the ``central`` (default
    9) central strikes of the 17-strike snapshot — the strikes centered on the
    ATM (indices 4..12 for a full window, i.e. ATM ± 4). Selection is delegated
    to ``_central_strikes`` which centers on ``atm_strike`` and guards against
    snapshots with fewer strikes. Absolute open interest is read from
    ``snapshot["rows"][strike]["put"]["oi"]`` / ``["call"]["oi"]`` via
    ``_row_oi`` (a ``None`` row or missing `oi` contributes ``0.0``). The
    log-only exchange 6-hour field (`oi_change_6h`) is never consulted (Req 9.9).

    Divide-by-zero handling (documented sentinels): when the call-OI sum is 0,
    return ``float("inf")`` if the put-OI sum is > 0 (all interest is on the put
    side — an unbounded ratio), or ``0.0`` when both sums are 0 (no interest at
    all, so the ratio is treated as 0.0). Always returns a float.
    """
    if not isinstance(snapshot, dict):
        return 0.0

    rows = snapshot.get("rows")
    if not isinstance(rows, dict):
        rows = {}

    sum_put_oi = 0.0
    sum_call_oi = 0.0
    for strike in _central_strikes(snapshot, central):
        pair = rows.get(strike)
        if pair is None:
            # Tolerate a strike-key type mismatch (e.g. float vs int).
            try:
                pair = rows.get(int(strike))
            except (TypeError, ValueError):
                pair = None
        if not isinstance(pair, dict):
            continue
        sum_call_oi += _row_oi(pair.get("call"))
        sum_put_oi += _row_oi(pair.get("put"))

    if sum_call_oi == 0.0:
        # No call-side OI: unbounded ratio when puts exist, else undefined -> 0.
        return float("inf") if sum_put_oi > 0.0 else 0.0

    return sum_put_oi / sum_call_oi


def computeSameStrikeOIDelta(prev_snapshot, curr_snapshot):
    """
    Compute the bot's OWN same-strike consecutive-snapshot OI change. (Req 9.8)

    For each strike in the CURRENT snapshot, the change in absolute open interest
    is measured against the PREVIOUS snapshot AT THE SAME STRIKE (no strike
    shifting), separately for calls and puts: ``delta = curr_oi - prev_oi``.
    This is fully independent of the exchange 6-hour field (`oi_change_6h`),
    which is log-only (Req 9.9).

    Returns a dict keyed by strike (int) ->::

        {"call_oi_delta": <float>, "put_oi_delta": <float>,
         "call_oi": <curr float>, "put_oi": <curr float>}

    Current OI is included for convenient logging. Missing/first-cycle handling
    (documented):
      * When a current strike is absent in the previous snapshot, the previous
        OI is treated as ``0.0`` so the delta is well-defined (equals the
        current OI).
      * When `prev_snapshot` is None/empty (the first cycle, nothing to diff
        against), every current strike gets ``0.0`` deltas with its current OI
        populated, so first-cycle logging works without spurious jumps.
    """
    if not isinstance(curr_snapshot, dict):
        return {}
    curr_rows = curr_snapshot.get("rows")
    if not isinstance(curr_rows, dict):
        return {}

    prev_rows = prev_snapshot.get("rows") if isinstance(prev_snapshot, dict) else None
    first_cycle = not isinstance(prev_rows, dict) or not prev_rows
    if not isinstance(prev_rows, dict):
        prev_rows = {}

    deltas = {}
    for strike, curr_pair in curr_rows.items():
        if not isinstance(curr_pair, dict):
            continue
        curr_call_oi = _row_oi(curr_pair.get("call"))
        curr_put_oi = _row_oi(curr_pair.get("put"))

        if first_cycle:
            # Nothing to diff against on the first cycle: zero deltas.
            call_delta = 0.0
            put_delta = 0.0
        else:
            prev_pair = prev_rows.get(strike)
            if prev_pair is None:
                try:
                    prev_pair = prev_rows.get(int(strike))
                except (TypeError, ValueError):
                    prev_pair = None
            # Absent-in-previous strike -> prev OI treated as 0.0.
            if isinstance(prev_pair, dict):
                prev_call_oi = _row_oi(prev_pair.get("call"))
                prev_put_oi = _row_oi(prev_pair.get("put"))
            else:
                prev_call_oi = 0.0
                prev_put_oi = 0.0
            call_delta = curr_call_oi - prev_call_oi
            put_delta = curr_put_oi - prev_put_oi

        deltas[strike] = {
            "call_oi_delta": call_delta,
            "put_oi_delta": put_delta,
            "call_oi": curr_call_oi,
            "put_oi": curr_put_oi,
        }

    return deltas


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
