import json
import numpy as np
import time
import re
import math
from datetime import datetime, timedelta
from pytz import timezone
import ta  # Python TA Lib
import pandas as pd
import pandas_ta as pta  # Pandas TA Lib
import requests
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fyers_client import fyers
import helper_fyers as helper

entryHour = 9
entryMinute = 24
entrySecond = 0

entryHour1 = 9
entryMinute1 = 17  # 15
entrySecond1 = 0


def importLibrary():
    global fyers_broker
    global zerodha_broker
    global upstox_broker
    global helper
    global api_connect
    global breeze
    global alice
    global fyers
    global kc
    global api

    if fyers_broker == 1:
        from fyers_apiv3 import fyersModel
        import helper_fyers as helper

        app_id = open("fyers_client_id.txt", 'r').read()
        access_token = open("fyers_access_token.txt", 'r').read()
        fyers = fyersModel.FyersModel(token=access_token, is_async=False, client_id=app_id)

    if zerodha_broker == 1:
        from kiteconnect import KiteTicker
        from kiteconnect import KiteConnect
        import helper_zerodha as helper
        apiKey = open("zerodha_api_key.txt", 'r').read()
        accessToken = open("zerodha_access_token.txt", 'r').read()
        kc = KiteConnect(api_key=apiKey)
        kc.set_access_token(accessToken)


fyers_broker = 1
zerodha_broker = 0


# If you dont want to trade option, make below as 0
tradeOption = 1
stock = "BANKNIFTY"  # BANKNIFTY , NIFTY , FINNIFTY

if tradeOption == 1:
    checkInstrument = helper.getIndexSpot(stock);
    bnExpDate = helper.getBankNiftyExpiryDate();
else:
    checkInstrument = stock

print("checkInstrument = ", checkInstrument)
print("bnExpDate = ", bnExpDate)

st = 0

timeFrame = 3  # in minutes
timeFrame2 = 1  # in minutes

qty = 150  # 5 lots x 30 = 150
sl_point = 50
target_point = 60

bullBar = False
bearBar = False

doNotTrade = True
capital = 700000
buyPremium = 500
hedgeBuyPremium = 15
qty2 = 30
otm = 100
itm = 200
atmCE1 = ""
atmPE1 = ""
hedereturnOption = ""
vol = 10000
volPE = 10000
tradeCEoption = ""
tradePEoption = ""
papertrading = 1  # 0 = paper trading, 1 = live trade

# ============================================================
# OBSERVATION_MODE — log signals without placing real orders
# ============================================================
OBSERVATION_MODE = True

sl_perc = 9
target_perc = 6

tradesDF = pd.DataFrame(columns=["Date", "Symbol", "Direction", "Price", "Qty", "PaperTrading"])

x = 1
y = 1
close = []
opens = []
high = []
low = []
volume = []

candle_formed = 0
oneMinCandle_Formed = 0

slHit = 0
targetHit = 0
tradeCount = 0
sl = 0
target = 0
slCount = 0
targetCount = 0


# Dynamic BNFut — auto-generates futures symbol based on contract expiry
def _get_last_tuesday(year, month):
    """Get last Tuesday of given month (BankNifty futures expiry day)."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_day)
    while d.weekday() != 1:  # 1 = Tuesday
        d -= timedelta(days=1)
    return d

_now = datetime.now()
_expiry = _get_last_tuesday(_now.year, _now.month)
if _now.date() > _expiry.date():
    _next = _now.replace(day=28) + timedelta(days=4)
    _yy = _next.strftime("%y")
    _mmm = _next.strftime("%b").upper()
else:
    _yy = _now.strftime("%y")
    _mmm = _now.strftime("%b").upper()
BNFut = f"NSE:BANKNIFTY{_yy}{_mmm}FUT"
print("BNFut (auto) =", BNFut)

indiaVix = "NSE:INDIAVIX-INDEX"

# ============================================================
# SPREAD TYPE SELECTION: CREDIT vs DEBIT
# Based on 3 parameters: DTE, Premium-to-Move Ratio, IV Rank
# Default: CREDIT on conflict (proven edge)
# ============================================================

def get_expiry_date():
    """Get current month's BankNifty expiry date (last Tuesday)."""
    now = datetime.now()
    expiry = _get_last_tuesday(now.year, now.month)
    if now.date() > expiry.date():
        # Already past this month's expiry, get next month
        next_month = now.replace(day=28) + timedelta(days=4)
        expiry = _get_last_tuesday(next_month.year, next_month.month)
    return expiry


def get_dte():
    """Calculate Days To Expiry."""
    expiry = get_expiry_date()
    dte = (expiry.date() - datetime.now().date()).days
    return dte


def get_iv_rank(fyers_client):
    """
    Calculate IV Rank using 30-day VIX history from Fyers API.
    IV Rank = (Current VIX - 30d Low) / (30d High - 30d Low)
    Returns value between 0 and 1. Cached after first call.
    """
    global _iv_rank_cache
    if _iv_rank_cache is not None:
        return _iv_rank_cache

    try:
        # Fetch 30-day daily candle data for India VIX
        vix_data = helper.getHistorical(indiaVix, 1440, 30, fyers_client)  # 1440 min = daily
        vix_closes = vix_data['close'].to_numpy()
        vix_30d_high = float(max(vix_closes))
        vix_30d_low = float(min(vix_closes))
        current_vix = float(vix_closes[-1])

        if (vix_30d_high - vix_30d_low) > 0:
            iv_rank = (current_vix - vix_30d_low) / (vix_30d_high - vix_30d_low)
        else:
            iv_rank = 0.5  # neutral if no range

        print("IV_RANK_DATA: VIX_30d_High=", round(vix_30d_high, 2),
              " VIX_30d_Low=", round(vix_30d_low, 2),
              " Current=", round(current_vix, 2),
              " IV_Rank=", round(iv_rank, 2))
        _iv_rank_cache = (round(iv_rank, 2), vix_30d_high, vix_30d_low)
        return _iv_rank_cache
    except Exception as e:
        print("IV_RANK_ERROR:", str(e), " — defaulting to 0.5 (neutral)")
        return 0.5, 0, 0


def get_premium_to_move_ratio(atm_premium, daily_range):
    """
    Calculate Premium-to-Expected-Move Ratio.
    Ratio = ATM Premium / (Daily Range × 0.45 delta)
    > 2.0 = premium is rich (sell/credit)
    < 1.5 = premium is cheap (buy/debit)
    """
    expected_option_move = daily_range * 0.45
    if expected_option_move > 0:
        ratio = atm_premium / expected_option_move
    else:
        ratio = 2.0  # default to credit-favorable
    return round(ratio, 2)


def choose_spread_type(iv_params, atm_premium, fyers_client):
    """
    Decide CREDIT or DEBIT spread based on 3 parameters:
    1. DTE (Days to Expiry) — ≤7 = credit, >7 = debit
    2. Premium-to-Move Ratio — ≥2.0 = credit, <1.5 = debit
    3. IV Rank (30-day) — >0.50 = credit, <0.30 = debit

    Scoring: +1 for credit signal, -1 for debit signal, 0 for neutral
    Score ≤ -1 → DEBIT, else → CREDIT (default)
    Majority wins: any 2 out of 3 parameters favoring debit triggers DEBIT.
    """
    score = 0
    reasons = []

    # 1. DTE check
    dte = get_dte()
    if dte <= 7:
        score += 1
        reasons.append(f"DTE={dte}(≤7:CREDIT)")
    elif dte > 7:
        score -= 1
        reasons.append(f"DTE={dte}(>7:DEBIT)")

    # 2. Premium-to-Move Ratio
    daily_range = iv_params.get('daily_range', 1000)
    premium_ratio = get_premium_to_move_ratio(atm_premium, daily_range)
    if premium_ratio >= 2.0:
        score += 1
        reasons.append(f"PremRatio={premium_ratio}(≥2.0:CREDIT)")
    elif premium_ratio < 1.5:
        score -= 1
        reasons.append(f"PremRatio={premium_ratio}(<1.5:DEBIT)")
    else:
        reasons.append(f"PremRatio={premium_ratio}(neutral)")

    # 3. IV Rank
    iv_rank, vix_high, vix_low = get_iv_rank(fyers_client)
    if iv_rank > 0.50:
        score += 1
        reasons.append(f"IVRank={iv_rank}(>0.50:CREDIT)")
    elif iv_rank < 0.30:
        score -= 1
        reasons.append(f"IVRank={iv_rank}(<0.30:DEBIT)")
    else:
        reasons.append(f"IVRank={iv_rank}(neutral)")

    # Decision: majority wins, default CREDIT only when tied or positive
    if score <= -1:
        spread_type = "DEBIT"
    else:
        spread_type = "CREDIT"

    print("=" * 50)
    print("SPREAD_SELECTION: Type=", spread_type, "| Score=", score,
          "| DTE=", dte, "| PremRatio=", premium_ratio,
          "| IVRank=", iv_rank)
    print("SPREAD_REASONS:", " | ".join(reasons))
    print("=" * 50)

    return spread_type, {
        "type": spread_type,
        "score": score,
        "dte": dte,
        "premium_ratio": premium_ratio,
        "iv_rank": iv_rank,
        "vix_30d_high": vix_high,
        "vix_30d_low": vix_low,
        "reasons": reasons
    }


# ============================================================
# IV-BASED DYNAMIC PARAMETERS (FULLY ADAPTIVE)
# ============================================================
# Cached 30-day VIX percentiles — fetched once per day
_vix_percentiles_cache = None
_iv_rank_cache = None


def _fetch_vix_percentiles(fyers_client):
    """
    Fetch 30-day VIX history and compute percentile thresholds.
    Called once per day (cached). All regime decisions are relative to recent history.
    """
    global _vix_percentiles_cache
    try:
        vix_data = helper.getHistorical(indiaVix, 1440, 30, fyers_client)  # daily candles, 30 days
        vix_closes = sorted(vix_data['close'].to_numpy())
        n = len(vix_closes)

        # Percentile thresholds (adaptive regime boundaries)
        p25 = float(vix_closes[int(n * 0.25)])  # 25th percentile = LOW/NORMAL boundary
        p50 = float(vix_closes[int(n * 0.50)])  # 50th percentile (median) = NORMAL/ELEVATED boundary
        p75 = float(vix_closes[int(n * 0.75)])  # 75th percentile = ELEVATED/HIGH boundary
        vix_min = float(vix_closes[0])
        vix_max = float(vix_closes[-1])

        _vix_percentiles_cache = {
            "p25": round(p25, 2),
            "p50": round(p50, 2),
            "p75": round(p75, 2),
            "min": round(vix_min, 2),
            "max": round(vix_max, 2),
            "fetched": True
        }
        print("VIX_PERCENTILES_FETCHED: P25=", _vix_percentiles_cache["p25"],
              " P50=", _vix_percentiles_cache["p50"],
              " P75=", _vix_percentiles_cache["p75"],
              " Min=", vix_min, " Max=", vix_max)
    except Exception as e:
        print("VIX_PERCENTILES_ERROR:", str(e), " — using fallback thresholds")
        _vix_percentiles_cache = {
            "p25": 13.0, "p50": 17.0, "p75": 22.0,
            "min": 10.0, "max": 28.0, "fetched": False
        }
    return _vix_percentiles_cache


def getIVRegime(fyers_client):
    """
    FULLY ADAPTIVE IV Regime — uses 30-day VIX percentiles instead of fixed thresholds.
    Regime boundaries shift with market conditions:
    - LOW = below 25th percentile of last 30 days
    - NORMAL = 25th to 50th percentile
    - ELEVATED = 50th to 75th percentile
    - HIGH = above 75th percentile

    All derived parameters (SL, target, buffer, spread, hedge) scale with
    the actual daily range formula — no hardcoded values.
    """
    global _vix_percentiles_cache

    vix_ltp = helper.manualLTP(indiaVix, fyers_client)
    bn_name = helper.getIndexSpot(stock)
    bn_price = helper.manualLTP(bn_name, fyers_client)
    print("India VIX =", vix_ltp, " BN Price =", bn_price)

    # Fetch percentiles once per day (or on first call)
    if _vix_percentiles_cache is None:
        _fetch_vix_percentiles(fyers_client)

    p25 = _vix_percentiles_cache["p25"]
    p50 = _vix_percentiles_cache["p50"]
    p75 = _vix_percentiles_cache["p75"]

    # Step 1: Expected daily range (1σ, 68% probability)
    daily_range = (vix_ltp * bn_price) / 700.0

    # Step 2: Expected 3-min candle range
    # Theoretical: daily_range / √130
    # Real-world correction: × 0.60 (validated against 150+ actual candles from logs)
    # Theoretical overestimates by ~65% due to consolidation/pauses in real markets
    candle_range_theoretical = daily_range / math.sqrt(130)
    candle_range = candle_range_theoretical * 0.60  # real-world corrected

    # Step 3: ATM option move per candle (delta ≈ 0.45)
    atm_option_move = candle_range * 0.45

    # Step 4: SL = survive ~3.5 adverse candles (2-candle close confirmation handles wicks)
    # 3.5x gives breathing room: with corrected candle_range (~65 pts),
    # option moves ~29 pts/candle, so SL = ~102 pts = 3.5 candles of adverse move
    raw_sl = atm_option_move * 3.5
    sl_pts = round(raw_sl)  # no wick buffer — 2-candle confirmation already filters wicks
    target_pts = sl_pts  # 1:1 R:R

    # Percentage-based SL (kicks in near expiry)
    sl_pct_of_premium = 0.10

    # Step 5: Spread width — quarter of daily range, rounded to 100
    spread_width = round(daily_range / 4.0 / 100) * 100
    spread_width = max(200, min(spread_width, 800))

    # Step 6: ADAPTIVE REGIME using percentiles
    if vix_ltp <= p25:
        regime = "LOW"
        # Low vol: tight buffer (less whipsaw)
        support_resistance_buffer = round(candle_range * 0.40)
    elif vix_ltp <= p50:
        regime = "NORMAL"
        support_resistance_buffer = round(candle_range * 0.50)
    elif vix_ltp <= p75:
        regime = "ELEVATED"
        support_resistance_buffer = round(candle_range * 0.65)
    else:
        regime = "HIGH"
        support_resistance_buffer = round(candle_range * 0.80)

    # Clamp buffer to reasonable range
    support_resistance_buffer = max(20, min(support_resistance_buffer, 150))

    params = {
        "vix": vix_ltp,
        "bn_price": bn_price,
        "regime": regime,
        "regime_thresholds": f"P25={p25} P50={p50} P75={p75}",
        "daily_range": round(daily_range, 1),
        "candle_range": round(candle_range, 1),
        "atm_option_move": round(atm_option_move, 1),
        "sl_point": sl_pts,
        "target_point": target_pts,
        "sl_pct_of_premium": sl_pct_of_premium,
        "trail_trigger": round(atm_option_move),
        "spread_width": spread_width,
        "support_resistance_buffer": support_resistance_buffer
    }
    print("IV Regime =", regime, "(ADAPTIVE: P25=", p25, " P50=", p50, " P75=", p75, ")",
          "| DailyRange =", round(daily_range, 1),
          "| 3minCandleRange =", round(candle_range, 1), "(theoretical=", round(candle_range_theoretical, 1), ")",
          "| ATMoptionMove =", round(atm_option_move, 1),
          "| SL =", sl_pts, "| Target =", target_pts,
          "| TrailTrigger =", round(atm_option_move),
          "| Spread =", spread_width,
          "| Buffer =", support_resistance_buffer)
    return params


# Global IV params — refreshed each 3-min candle
iv_params = {}
# Global spread type — determined once at first candle
spread_decision = {}


def getQtyByCapital(capital, entryPrice):
    quantity = int(capital / entryPrice)
    remainder = quantity % 15
    return quantity - remainder


# ============================================================
# CHART PATTERN DETECTION (3-min FUT candles)
# Logs patterns for future analysis/optimization
# ============================================================

def detect_chart_patterns(opens, high, low, close):
    """
    Detect basic chart patterns on 3-min BankNifty FUT candles.
    Returns list of detected patterns with details for logging.
    Requires at least 5 candles of data.
    """
    patterns = []

    if len(close) < 5:
        return patterns

    # --- Pattern 1: Engulfing (Bullish & Bearish) ---
    # Bullish Engulfing: prev bearish candle fully engulfed by current bullish candle
    prev_body = close[-3] - opens[-3]
    curr_body = close[-2] - opens[-2]
    if prev_body < 0 and curr_body > 0:
        if opens[-2] <= close[-3] and close[-2] >= opens[-3]:
            patterns.append("BULLISH_ENGULFING")
    # Bearish Engulfing: prev bullish candle fully engulfed by current bearish candle
    if prev_body > 0 and curr_body < 0:
        if opens[-2] >= close[-3] and close[-2] <= opens[-3]:
            patterns.append("BEARISH_ENGULFING")

    # --- Pattern 2: Three White Soldiers / Three Black Crows ---
    body1 = close[-4] - opens[-4]
    body2 = close[-3] - opens[-3]
    body3 = close[-2] - opens[-2]
    # Three White Soldiers: 3 consecutive bullish candles, each closing higher
    if body1 > 0 and body2 > 0 and body3 > 0:
        if close[-3] > close[-4] and close[-2] > close[-3]:
            patterns.append("THREE_WHITE_SOLDIERS")
    # Three Black Crows: 3 consecutive bearish candles, each closing lower
    if body1 < 0 and body2 < 0 and body3 < 0:
        if close[-3] < close[-4] and close[-2] < close[-3]:
            patterns.append("THREE_BLACK_CROWS")

    # --- Pattern 3: Morning Star / Evening Star (3-candle reversal) ---
    body_prev2 = close[-4] - opens[-4]
    body_prev1 = close[-3] - opens[-3]
    body_curr = close[-2] - opens[-2]
    # Morning Star: big bearish → small body (doji-like) → big bullish
    if body_prev2 < -30 and abs(body_prev1) <= 15 and body_curr > 30:
        patterns.append("MORNING_STAR")
    # Evening Star: big bullish → small body → big bearish
    if body_prev2 > 30 and abs(body_prev1) <= 15 and body_curr < -30:
        patterns.append("EVENING_STAR")

    # --- Pattern 4: Hammer / Inverted Hammer ---
    curr_range = high[-2] - low[-2]
    curr_body_abs = abs(close[-2] - opens[-2])
    if curr_range > 0 and curr_body_abs > 0:
        upper_wick = high[-2] - max(close[-2], opens[-2])
        lower_wick = min(close[-2], opens[-2]) - low[-2]
        # Hammer: small body at top, long lower wick (≥2x body)
        if lower_wick >= 2 * curr_body_abs and upper_wick <= curr_body_abs * 0.5:
            patterns.append("HAMMER")
        # Inverted Hammer / Shooting Star: small body at bottom, long upper wick
        if upper_wick >= 2 * curr_body_abs and lower_wick <= curr_body_abs * 0.5:
            if curr_body < 0:
                patterns.append("SHOOTING_STAR")
            else:
                patterns.append("INVERTED_HAMMER")

    # --- Pattern 5: Inside Bar (consolidation/breakout setup) ---
    # Current candle's high/low is within previous candle's high/low
    if high[-2] <= high[-3] and low[-2] >= low[-3]:
        patterns.append("INSIDE_BAR")

    # --- Pattern 6: Outside Bar / Engulfing Range ---
    if high[-2] > high[-3] and low[-2] < low[-3]:
        patterns.append("OUTSIDE_BAR")

    # --- Pattern 7: Double Top / Double Bottom (last 5 candles) ---
    highs_5 = high[-5:]
    lows_5 = low[-5:]
    # Double Top: two similar highs with a dip in between
    max_high = max(highs_5)
    high_indices = [i for i, h in enumerate(highs_5) if abs(h - max_high) <= 20]
    if len(high_indices) >= 2 and (high_indices[-1] - high_indices[0]) >= 2:
        patterns.append("DOUBLE_TOP_FORMING")
    # Double Bottom: two similar lows with a rise in between
    min_low = min(lows_5)
    low_indices = [i for i, l in enumerate(lows_5) if abs(l - min_low) <= 20]
    if len(low_indices) >= 2 and (low_indices[-1] - low_indices[0]) >= 2:
        patterns.append("DOUBLE_BOTTOM_FORMING")

    # --- Pattern 8: Strong Momentum (candle body > 70% of range) ---
    if curr_range > 0:
        body_pct = curr_body_abs / curr_range
        if body_pct >= 0.70 and curr_range >= 50:
            direction = "BULL" if curr_body > 0 else "BEAR"
            patterns.append(f"STRONG_MOMENTUM_{direction}")

    return patterns


def log_chart_patterns(opens, high, low, close, iv_params):
    """
    Detect and log chart patterns with context for future analysis.
    """
    patterns = detect_chart_patterns(opens, high, low, close)

    if patterns:
        curr_range = round(high[-2] - low[-2], 1)
        curr_body = round(close[-2] - opens[-2], 1)
        expected_candle_range = iv_params.get('candle_range', 0)
        range_vs_expected = round(curr_range / expected_candle_range, 2) if expected_candle_range > 0 else 0

        print("CHART_PATTERN_DETECTED:", " | ".join(patterns))
        print("  PATTERN_CONTEXT: Body=", curr_body,
              " Range=", curr_range,
              " ExpectedRange=", expected_candle_range,
              " RangeRatio=", range_vs_expected,
              " O=", round(opens[-2], 1),
              " H=", round(high[-2], 1),
              " L=", round(low[-2], 1),
              " C=", round(close[-2], 1))
    else:
        print("CHART_PATTERN: None")

    return patterns


# ============================================================
# ATM OPTION CANDLE RANGE — direct measurement
# Used to compute SL/target as 3 × actual option candle range
# Falls back to IV formula if insufficient candle data available
# ============================================================

def get_option_candle_range(option_symbol, fyers_client, n_candles=10, top_k=5):
    """
    Measure actual ATM option 3-min candle range from recent history.

    Method: Take last n_candles from TODAY's session (skipping the very first
    9:15-9:18 candle which is abnormally large due to opening volatility), then
    return MEDIAN of all valid candle ranges.

    - Last 10 candles (post first candle) covers ~30 min of recent activity
    - Median of all = balanced view of typical movement (not biased by outliers)
    - top_k param kept for backward compat but unused (top-K logic removed —
      was over-weighting active candles and inflating SL)

    Falls back to IV formula only if zero candles or fetch fails.

    Returns:
        float: representative 3-min candle range in points, or None if no data
    """
    try:
        # Fetch last 1 day of intraday candles (returns yesterday + today)
        opt_data = helper.getHistorical(option_symbol, 3, 1, fyers_client)

        # Filter to today's session only — yesterday's ATM was a different strike
        today_date = datetime.now(timezone('Asia/Kolkata')).date()
        opt_data = opt_data[opt_data.index.date == today_date]

        # Skip the very first 3-min candle (opening volatility distorts range)
        # AND skip the LAST candle (current in-progress 3-min bin — partial data)
        if len(opt_data) > 2:
            opt_data = opt_data.iloc[1:-1]
        elif len(opt_data) > 1:
            opt_data = opt_data.iloc[1:]

        highs = opt_data['high'].to_numpy()
        lows = opt_data['low'].to_numpy()

        # Use last n_candles (or all available if fewer)
        ranges = (highs - lows)[-n_candles:]
        # Filter out zero-range candles (no trade in that 3-min)
        ranges = ranges[ranges > 0]

        if len(ranges) == 0:
            return None

        # MEDIAN OF ALL valid candle ranges (was top-K median, simplified for stability)
        representative_range = float(np.median(ranges))

        # Diagnostic logging — audit raw data driving SL calculation
        all_ranges_rounded = [round(float(r), 2) for r in ranges]
        print(f"OPT_RANGE_DEBUG: symbol={option_symbol}",
              f"| total_candles_after_skip1st={len(ranges)}",
              f"| ranges={all_ranges_rounded}",
              f"| median_all={round(representative_range, 2)}")

        return round(representative_range, 2)
    except Exception as e:
        print("OPTION_RANGE_ERROR:", str(e))
        return None


# ============================================================
# ENTRY FUNCTIONS: CREDIT SPREAD & DEBIT SPREAD
# ============================================================


def fetch_ochain_safe(strikecount, sname, fyers_client, use_closest1=False, retries=2, retry_delay=1.5):
    """
    Resilient option chain fetch — handles transient Fyers API failures.

    Returns:
        tuple: (dfochain DataFrame or None, ochainresponse or None)
        - On success: both non-None
        - On failure after retries: both None
    Caller should skip current cycle when df is None.

    use_closest1 toggles between helper.getClosestOptions and getClosestOptions1.
    Required cols: 'symbol', 'option_type', 'oi'. ('oich', 'volume' optional but checked.)
    """
    required_cols = {'symbol', 'option_type', 'oi'}
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            ochainresponse = helper.getOptionChain(strikecount, sname, fyers_client)
            if use_closest1:
                ochain = helper.getClosestOptions1(ochainresponse)
            else:
                ochain = helper.getClosestOptions(ochainresponse)
            df = pd.DataFrame(ochain)

            # Validate response shape
            if df.empty:
                last_err = "empty dataframe"
            elif not required_cols.issubset(df.columns):
                missing = required_cols - set(df.columns)
                last_err = f"missing cols={missing}"
            else:
                if attempt > 1:
                    print(f"OCHAIN_FETCH_OK_AFTER_RETRY: attempt={attempt} sname={sname}")
                return df, ochainresponse

            print(f"OCHAIN_FETCH_BAD_RESPONSE attempt={attempt}/{retries} sname={sname} reason={last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"OCHAIN_FETCH_ERROR attempt={attempt}/{retries} sname={sname} err={last_err}")

        if attempt < retries:
            time.sleep(retry_delay)

    print(f"OCHAIN_FETCH_FAILED sname={sname} reason={last_err} — skipping this cycle")
    return None, None


def getChangeInOI(dfochain, index1, index2):
    oich = dfochain['oich'].to_numpy()
    chInOI = round(oich[index1])
    chInOI2 = round(oich[index2])
    option_type = dfochain['option_type'].to_numpy()

    option_type1 = option_type[index1]
    option_type2 = option_type[index2]

    pcr = round(oich[index1] / oich[index2], 2) if option_type[index1] != '' and option_type[index2] != '' and \
                                                   option_type[index1] == 'PE' else round(oich[index2] / oich[index1], 2)

    if option_type1 == 'CE':
        changOICE = str(pcr) + " CALL added " + str(chInOI) if chInOI > 0 else str(pcr) + " CALL unwind " + str(chInOI)
    elif option_type1 == 'PE':
        changOIPE = "   PUT added " + str(chInOI) if chInOI > 0 else "   PUT unwind " + str(chInOI)
    if option_type2 == 'CE':
        changOICE = str(pcr) + " CALL added " + str(chInOI2) if chInOI2 > 0 else str(pcr) + " CALL unwind " + str(chInOI2)
    elif option_type2 == 'PE':
        changOIPE = "   PUT added " + str(chInOI2) if chInOI2 > 0 else "   PUT unwind " + str(chInOI2)

    changOI = changOICE + changOIPE
    return changOI


def takeEntryCredit(isBullish, isBearish, qty, fyers, papertrading):
    """
    CREDIT SPREAD entry: Sell ATM + Buy OTM hedge.
    Same as original strategy — proven edge.
    """
    global hedgeOrderId
    global tradeATMOption
    global tradeHedgeOption
    global mainOrderId
    global iv_params

    dynamic_sl = iv_params.get("sl_point", sl_point)
    dynamic_target = iv_params.get("target_point", target_point)
    dynamic_spread = iv_params.get("spread_width", 300)

    print("CREDIT_ENTRY: qty=", qty)
    print("IV-Dynamic: SL=", dynamic_sl, " Target=", dynamic_target,
          " Spread=", dynamic_spread)

    name = helper.getIndexSpot(stock)
    ltp = helper.manualLTP(name, fyers)
    if stock == 'BANKNIFTY':
        intExpiry = helper.getBankNiftyExpiryDate()
        closest_Strike = int(round((ltp / 100), 0) * 100)
    elif stock == 'NIFTY':
        intExpiry = helper.getNiftyExpiryDate()
        closest_Strike = int(round((ltp / 50), 0) * 50)
    print('closest_Strike = ', closest_Strike)

    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "PE")
    atmCEPremium = helper.manualLTP(atmCE, fyers)
    atmPEPremium = helper.manualLTP(atmPE, fyers)
    syntheticATMStrike = ltp + atmCEPremium - atmPEPremium
    print('syntheticATMStrike = ', syntheticATMStrike)
    syntheticATMStrike = int(round((syntheticATMStrike / 100), 0) * 100)

    Hedge_Strike_CE_OTMBuy = syntheticATMStrike + dynamic_spread
    Hedge_Strike_PE_OTMBuy = syntheticATMStrike - dynamic_spread
    closest_Strike_CE = syntheticATMStrike
    closest_Strike_PE = syntheticATMStrike
    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike_CE, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike_PE, "PE")

    otmCE = helper.getOptionFormat(stock, intExpiry, Hedge_Strike_CE_OTMBuy, "CE")
    otmPE = helper.getOptionFormat(stock, intExpiry, Hedge_Strike_PE_OTMBuy, "PE")

    if isBullish:
        entryPrice = helper.manualLTP(atmPE, fyers)
        max_premium = entryPrice * 0.60
        hedge_strike = (syntheticATMStrike // 500) * 500
        otmPE_found = None
        for _ in range(6):
            candidate = helper.getOptionFormat(stock, intExpiry, hedge_strike, "PE")
            candidate_premium = helper.manualLTP(candidate, fyers)
            print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
            if candidate_premium <= max_premium:
                otmPE_found = candidate
                break
            hedge_strike -= 500
        if otmPE_found:
            otmPE = otmPE_found
        print("=atmPE=", atmPE)
        print("=otmPE==", otmPE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        hedge_entry_price = helper.manualLTP(otmPE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("CREDIT_NET_CREDIT=", round(entryPrice - hedge_entry_price, 2))

        hedgeOrderId = helper.placeTargetOrder(otmPE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmPE
        time.sleep(0.5)

        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmPE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmPE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers, papertrading)
        tradeATMOption = atmPE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)

    if isBearish:
        entryPrice = helper.manualLTP(atmCE, fyers)
        max_premium = entryPrice * 0.60
        hedge_strike = (syntheticATMStrike // 500 + 1) * 500
        otmCE_found = None
        for _ in range(6):
            candidate = helper.getOptionFormat(stock, intExpiry, hedge_strike, "CE")
            candidate_premium = helper.manualLTP(candidate, fyers)
            print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
            if candidate_premium <= max_premium:
                otmCE_found = candidate
                break
            hedge_strike += 500
        if otmCE_found:
            otmCE = otmCE_found
        print("=atmCE=", atmCE)
        print("=otmCE==", otmCE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        hedge_entry_price = helper.manualLTP(otmCE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("CREDIT_NET_CREDIT=", round(entryPrice - hedge_entry_price, 2))

        hedgeOrderId = helper.placeTargetOrder(otmCE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmCE
        time.sleep(0.5)

        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmCE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmCE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers, papertrading)
        tradeATMOption = atmCE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)

    return mainOrderId


def takeEntryDebit(isBullish, isBearish, qty, fyers, papertrading):
    """
    DEBIT SPREAD entry: Buy ATM + Sell OTM hedge.
    Same logic as credit spread (500-pt interval, 60% premium cap) — only BUY/SELL flipped.
    """
    global hedgeOrderId
    global tradeATMOption
    global tradeHedgeOption
    global mainOrderId
    global iv_params

    dynamic_sl = iv_params.get("sl_point", sl_point)
    dynamic_target = iv_params.get("target_point", target_point)
    dynamic_spread = iv_params.get("spread_width", 300)

    print("DEBIT_ENTRY: qty=", qty)
    print("IV-Dynamic: SL=", dynamic_sl, " Target=", dynamic_target,
          " Spread=", dynamic_spread)

    name = helper.getIndexSpot(stock)
    ltp = helper.manualLTP(name, fyers)
    if stock == 'BANKNIFTY':
        intExpiry = helper.getBankNiftyExpiryDate()
        closest_Strike = int(round((ltp / 100), 0) * 100)
    elif stock == 'NIFTY':
        intExpiry = helper.getNiftyExpiryDate()
        closest_Strike = int(round((ltp / 50), 0) * 50)
    print('closest_Strike = ', closest_Strike)

    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike, "PE")
    atmCEPremium = helper.manualLTP(atmCE, fyers)
    atmPEPremium = helper.manualLTP(atmPE, fyers)
    syntheticATMStrike = ltp + atmCEPremium - atmPEPremium
    print('syntheticATMStrike = ', syntheticATMStrike)
    syntheticATMStrike = int(round((syntheticATMStrike / 100), 0) * 100)

    atmCE = helper.getOptionFormat(stock, intExpiry, syntheticATMStrike, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, syntheticATMStrike, "PE")

    if isBullish:
        # Bull Call Debit Spread: Buy ATM CE + Sell OTM CE (500-pt interval, ≤60% of buy premium)
        entryPrice = helper.manualLTP(atmCE, fyers)
        max_premium = entryPrice * 0.60
        hedge_strike = (syntheticATMStrike // 500 + 1) * 500  # snap to next 500-pt multiple above
        otmCE_found = None
        for _ in range(6):
            candidate = helper.getOptionFormat(stock, intExpiry, hedge_strike, "CE")
            candidate_premium = helper.manualLTP(candidate, fyers)
            print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
            if candidate_premium <= max_premium:
                otmCE_found = candidate
                break
            hedge_strike += 500
        otmCE = otmCE_found if otmCE_found else helper.getOptionFormat(stock, intExpiry, syntheticATMStrike + dynamic_spread, "CE")
        print("=atmCE=", atmCE)
        print("=otmCE==", otmCE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        hedge_entry_price = helper.manualLTP(otmCE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("DEBIT_NET_DEBIT=", round(entryPrice - hedge_entry_price, 2))

        # BUY ATM CE first (main leg)
        mainOrderId = helper.placeTargetOrder(atmCE, "BUY", qty, "MARKET", entryPrice, 0, 0, fyers, papertrading)
        tradeATMOption = atmCE
        time.sleep(0.5)

        # SELL OTM CE (hedge leg — gets margin benefit from buy)
        hedgeOrderId = helper.placeTargetOrder(otmCE, "SELL", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmCE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)

    if isBearish:
        # Bear Put Debit Spread: Buy ATM PE + Sell OTM PE (500-pt interval, ≤60% of buy premium)
        entryPrice = helper.manualLTP(atmPE, fyers)
        max_premium = entryPrice * 0.60
        hedge_strike = (syntheticATMStrike // 500) * 500  # snap to nearest 500-pt multiple at or below
        otmPE_found = None
        for _ in range(6):
            candidate = helper.getOptionFormat(stock, intExpiry, hedge_strike, "PE")
            candidate_premium = helper.manualLTP(candidate, fyers)
            print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
            if candidate_premium <= max_premium:
                otmPE_found = candidate
                break
            hedge_strike -= 500
        otmPE = otmPE_found if otmPE_found else helper.getOptionFormat(stock, intExpiry, syntheticATMStrike - dynamic_spread, "PE")
        print("=atmPE=", atmPE)
        print("=otmPE==", otmPE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        hedge_entry_price = helper.manualLTP(otmPE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("DEBIT_NET_DEBIT=", round(entryPrice - hedge_entry_price, 2))

        # BUY ATM PE first (main leg)
        mainOrderId = helper.placeTargetOrder(atmPE, "BUY", qty, "MARKET", entryPrice, 0, 0, fyers, papertrading)
        tradeATMOption = atmPE
        time.sleep(0.5)

        # SELL OTM PE (hedge leg — gets margin benefit from buy)
        hedgeOrderId = helper.placeTargetOrder(otmPE, "SELL", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmPE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)

    return mainOrderId


def takeEntry(isBullish, isBearish, qty, fyers, papertrading):
    """
    Wrapper: routes to credit or debit entry based on spread_decision.
    """
    global spread_decision
    spread_type = spread_decision.get("type", "CREDIT")

    print("ENTRY_ROUTING: spread_type=", spread_type,
          " isBullish=", isBullish, " isBearish=", isBearish)

    if spread_type == "DEBIT":
        return takeEntryDebit(isBullish, isBearish, qty, fyers, papertrading)
    else:
        return takeEntryCredit(isBullish, isBearish, qty, fyers, papertrading)


# ============================================================
# EXIT FUNCTIONS
# ============================================================

def exitPosition(tradeOption):
    oidentry = helper.placeOrder(tradeOption, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
    print("Exit OID: ", oidentry)
    return oidentry


def exitSpreadPosition(mainATMOption, hedgeOption):
    """Exit spread — logic differs for credit vs debit."""
    global spread_decision
    spread_type = spread_decision.get("type", "CREDIT")

    if spread_type == "DEBIT":
        # Debit: close SHORT leg first (buy back OTM), then close LONG leg (sell ATM)
        # This avoids naked short moment and margin issues
        oidentry = helper.placeOrder(hedgeOption, "BUY", qty, "MARKET", 0, "regular", fyers, papertrading)
        print("Exit hedge (BUY back sold leg) OID: ", oidentry)
        time.sleep(0.5)
        mainOidentry = helper.placeOrder(mainATMOption, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
        print("Exit main (SELL bought leg) OID: ", mainOidentry)
    else:
        # Credit: we sold ATM (buy to close) + bought OTM (sell to close)
        mainOidentry = helper.placeOrder(mainATMOption, "BUY", qty, "MARKET", 0, "regular", fyers, papertrading)
        print("Exit main (BUY sold leg) OID: ", mainOidentry)
        time.sleep(0.5)
        oidentry = helper.placeOrder(hedgeOption, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
        print("Exit hedge (SELL bought leg) OID: ", oidentry)

    time.sleep(0.5)
    return mainOidentry


def findStrikePricePremium(optionName, premium, premiumType):
    name = helper.getIndexSpot(stock)
    closest_Strike_PE = ''
    closest_Strike_CE = ''
    strikeList = []
    prev_diff = 10000

    intExpiry = helper.getBankNiftyExpiryDate()
    ltp = helper.manualLTP(name, fyers)
    print("intExpiry==", intExpiry)
    print("closestPrimium fun LTP", ltp)

    if optionName == "CE" and premiumType == "":
        start = -8
        end = 4
    elif optionName == "CE" and premiumType != "":
        start = 1
        end = 14
    if optionName == "PE" and premiumType == "":
        start = -4
        end = 8
    elif optionName == "PE" and premiumType != "":
        start = -14
        end = -1

    for i in range(start, end):
        strike = (int(ltp / 100) + i) * 100
        strikeList.append(strike)

    if optionName == "CE":
        prev_diff = 10000
        for strike in strikeList:
            ceOptionFormat = helper.getOptionFormat(stock, intExpiry, strike, "CE")
            ltp_option = helper.manualLTP(ceOptionFormat, fyers)
            diff = abs(ltp_option - premium)
            if diff < prev_diff:
                closest_Strike_CE = strike
                prev_diff = diff
    if optionName == "PE":
        prev_diff = 10000
        for strike in strikeList:
            peOptionFormat = helper.getOptionFormat(stock, intExpiry, strike, "PE")
            ltp_option = helper.manualLTP(peOptionFormat, fyers)
            diff = abs(ltp_option - premium)
            if diff < prev_diff:
                closest_Strike_PE = strike
                prev_diff = diff

    atmCE = helper.getOptionFormat(stock, intExpiry, closest_Strike_CE, "CE")
    atmPE = helper.getOptionFormat(stock, intExpiry, closest_Strike_PE, "PE")

    if optionName == "CE":
        return atmCE
    elif optionName == "PE":
        return atmPE


def get_support_resistance(futltp, step=500, buffer=None):
    """Calculate support/resistance based on FUT_LTP.
    Buffer/NOTRADEZONE commented out — returning support or resistance directly.
    Uncomment below to re-enable NOTRADEZONE.
    """
    # if buffer is None:
    #     buffer = iv_params.get("support_resistance_buffer", 30) if iv_params else 30

    support = (futltp // step) * step
    resistance = support + step
    middle = (support + resistance) / 2

    # NOTRADEZONE logic — commented out for now
    # no_trade_low = middle - buffer
    # no_trade_high = middle + buffer
    # if futltp <= no_trade_low:
    #     return support
    # elif futltp >= no_trade_high:
    #     return resistance
    # else:
    #     return "NOTRADEZONE"

    # Without buffer: simple middle split
    if futltp < middle:
        return support
    else:
        return resistance


def sum_around_key(my_map, substring, window=4):
    keys = list(my_map.keys())
    values = list(my_map.values())
    index = next((i for i, k in enumerate(keys) if substring in k), None)
    if index is None:
        return None
    start = max(0, index - window)
    end = min(len(values), index + window + 1)
    total = sum(values[start:end])
    return keys[index], total


def sum_with_neighbors(data_map, search_substr):
    items = list(data_map.items())
    match_index = None
    for i, (k, v) in enumerate(items):
        if search_substr in k:
            match_index = i
            break
    if match_index is None:
        return None
    start = max(0, match_index - 4)
    end = min(len(items), match_index + 5)
    selected = items[start:end]
    total_sum = sum(v for k, v in selected)
    included_keys = [k for k, v in selected]
    return total_sum, included_keys


def is_difference_greater_than_25(val1, val2):
    larger = max(val1, val2)
    if larger == 0:
        return False
    difference = abs(val1 - val2)
    percent_diff = (difference / larger) * 100
    return percent_diff > 25


# ============================================================
# CRITERIA CHECK (same as original)
# ============================================================

def checkCriteriaAndTakeTrade():
    global st, tradeCEoption, tradePEoption, sl, target, slCount, targetCount
    name = helper.getIndexSpot(stock)
    prev_diff = 10000
    closest_Strike = 10000
    now = datetime.now()

    intExpiry = helper.getBankNiftyExpiryDate()
    dataFUT = helper.getHistorical(BNFut, timeFrame, 3, fyers)
    opens = dataFUT['open'].to_numpy()
    high = dataFUT['high'].to_numpy()
    low = dataFUT['low'].to_numpy()
    close = dataFUT['close'].to_numpy()

    isCurrCandlHaveWicks = False
    if close[-2] - opens[-2] > 0:
        isCurrCandlHaveWicks = True if high[-2] - close[-2] > 0 and opens[-2] - low[-2] > 0 else False
    elif close[-2] - opens[-2] < 0:
        isCurrCandlHaveWicks = True if high[-2] - opens[-2] > 0 and close[-2] - low[-2] > 0 else False
    else:
        isCurrCandlHaveWicks = True if high[-2] - opens[-2] > 0 and close[-2] - low[-2] > 0 else False

    isCurrDoji = True if abs(opens[-2] - close[-2]) <= 3 and isCurrCandlHaveWicks else False

    closest_Strike22 = int(round((opens[-2] / 100), 0) * 100)
    currCandleStrike = math.floor(opens[-2] / 100) * 100
    prevCandleStrike = math.floor(opens[-3] / 100) * 100

    currCEStrike = helper.getOptionFormat(stock, intExpiry, currCandleStrike, "CE")
    currPEStrike = helper.getOptionFormat(stock, intExpiry, currCandleStrike, "PE")
    prevCEStrike = helper.getOptionFormat(stock, intExpiry, prevCandleStrike, "CE")
    prevPEStrike = helper.getOptionFormat(stock, intExpiry, prevCandleStrike, "PE")

    currOptiondataCE = helper.getHistorical(currCEStrike, timeFrame, 3, fyers)
    currOptiondataPE = helper.getHistorical(currPEStrike, timeFrame, 3, fyers)
    prevOptiondataCE = helper.getHistorical(prevCEStrike, timeFrame, 3, fyers)
    prevOptiondataPE = helper.getHistorical(prevPEStrike, timeFrame, 3, fyers)

    print("curr Doji", isCurrDoji)

    currVolumeCE = currOptiondataCE['volume'].to_numpy()
    currVolumePE = currOptiondataPE['volume'].to_numpy()
    prevVolumeCE = prevOptiondataCE['volume'].to_numpy()
    prevVolumePE = prevOptiondataPE['volume'].to_numpy()

    currPCR = round(currVolumePE[-2] / currVolumeCE[-2], 2)
    prevPCR = round(prevVolumePE[-3] / prevVolumeCE[-3], 2)
    currCPR = round(currVolumeCE[-2] / currVolumePE[-2], 2)
    prevCPR = round(prevVolumeCE[-3] / prevVolumePE[-3], 2)

    print(currPEStrike, " curr candle vol pcr =", currPCR, " PE=", round(currVolumePE[-2], 2), " CE=",
          round(currVolumeCE[-2], 2), "    ", "cpr = ", currCPR)
    print(prevPEStrike, " prev candle vol pcr =", prevPCR, " PE=", round(prevVolumePE[-3], 2), " CE=",
          round(prevVolumeCE[-3], 2), "    ", "cpr = ", prevCPR)

    # === VOLUME PRESSURE ALERT (for future scalping analysis) ===
    if currPCR >= 2 and prevPCR >= 2:
        print("VOL_PRESSURE_ALERT: STRONG_PUT_PRESSURE | currPCR=", currPCR, " prevPCR=", prevPCR,
              " Direction=BULLISH_SIGNAL (heavy PE buying = expecting up move)")
    elif currCPR >= 2 and prevCPR >= 2:
        print("VOL_PRESSURE_ALERT: STRONG_CALL_PRESSURE | currCPR=", currCPR, " prevCPR=", prevCPR,
              " Direction=BEARISH_SIGNAL (heavy CE buying = expecting down move)")
    elif currPCR >= 2 or prevPCR >= 2:
        print("VOL_PRESSURE_WATCH: SINGLE_CANDLE_PUT_PRESSURE | currPCR=", currPCR, " prevPCR=", prevPCR)
    elif currCPR >= 2 or prevCPR >= 2:
        print("VOL_PRESSURE_WATCH: SINGLE_CANDLE_CALL_PRESSURE | currCPR=", currCPR, " prevCPR=", prevCPR)

    # ATM option premiums
    currCEclose = currOptiondataCE['close'].to_numpy()
    currPEclose = currOptiondataPE['close'].to_numpy()
    print("ATM_CE_premium=", round(currCEclose[-2], 2), " ATM_PE_premium=", round(currPEclose[-2], 2))

    ce_premium_change = round(currCEclose[-2] - currCEclose[-3], 2) if len(currCEclose) >= 3 else 0
    pe_premium_change = round(currPEclose[-2] - currPEclose[-3], 2) if len(currPEclose) >= 3 else 0
    print("CE_PremChg=", ce_premium_change, " PE_PremChg=", pe_premium_change)

    candleBody = round(close[-2] - opens[-2], 1)
    prevCandleBody = round(close[-3] - opens[-3], 1)
    candleRange = round(high[-2] - low[-2], 1)
    print("CandleBody=", candleBody, " PrevBody=", prevCandleBody,
          " CandleRange=", candleRange,
          " Bullish" if candleBody > 0 else " Bearish" if candleBody < 0 else " Doji")

    currTotalVol = currVolumeCE[-2] + currVolumePE[-2]
    prevTotalVol = prevVolumeCE[-3] + prevVolumePE[-3]
    volSpike = round(currTotalVol / prevTotalVol, 2) if prevTotalVol > 0 else 0
    print("TotalVol=", round(currTotalVol), " PrevTotalVol=", round(prevTotalVol),
          " VolSpike=", volSpike, "x", " SPIKE!" if volSpike >= 2.0 else "")

    # === SCALPING OPPORTUNITY SCORE ===
    # Combines multiple data points for high-conviction directional signal
    bull_score = 0
    bear_score = 0
    bull_reasons = []
    bear_reasons = []

    # Factor 1: Volume PCR pressure (both candles confirm direction)
    if currPCR >= 2 and prevPCR >= 2:
        bull_score += 2
        bull_reasons.append("VolPCR_2candle_bull")
    elif currPCR >= 1.5:
        bull_score += 1
        bull_reasons.append("VolPCR_curr_bull")
    if currCPR >= 2 and prevCPR >= 2:
        bear_score += 2
        bear_reasons.append("VolCPR_2candle_bear")
    elif currCPR >= 1.5:
        bear_score += 1
        bear_reasons.append("VolCPR_curr_bear")

    # Factor 2: Candle body direction + strength (body > 60% of range = strong)
    if candleRange > 0:
        body_strength = abs(candleBody) / candleRange
        if candleBody > 0 and body_strength >= 0.60:
            bull_score += 1
            bull_reasons.append(f"StrongBullCandle({round(body_strength*100)}%)")
        elif candleBody < 0 and body_strength >= 0.60:
            bear_score += 1
            bear_reasons.append(f"StrongBearCandle({round(body_strength*100)}%)")

    # Factor 3: Consecutive candle direction (curr + prev same direction)
    if candleBody > 0 and prevCandleBody > 0:
        bull_score += 1
        bull_reasons.append("2ConsecBullCandles")
    elif candleBody < 0 and prevCandleBody < 0:
        bear_score += 1
        bear_reasons.append("2ConsecBearCandles")

    # Factor 4: Volume spike
    if volSpike >= 2.0:
        if candleBody > 0:
            bull_score += 1
            bull_reasons.append(f"VolSpike({volSpike}x)_bull")
        elif candleBody < 0:
            bear_score += 1
            bear_reasons.append(f"VolSpike({volSpike}x)_bear")
    elif volSpike >= 1.5:
        if candleBody > 0:
            bull_score += 1
            bull_reasons.append(f"VolRise({volSpike}x)_bull")
        elif candleBody < 0:
            bear_score += 1
            bear_reasons.append(f"VolRise({volSpike}x)_bear")

    # Factor 5: Premium momentum
    if ce_premium_change < -10 and pe_premium_change > 10:
        bull_score += 1
        bull_reasons.append(f"PremMomentum_bull(CE{ce_premium_change},PE+{pe_premium_change})")
    elif pe_premium_change < -10 and ce_premium_change > 10:
        bear_score += 1
        bear_reasons.append(f"PremMomentum_bear(PE{pe_premium_change},CE+{ce_premium_change})")

    # Factor 6: Candle range vs expected range
    expected_candle_range = iv_params.get('candle_range', 100) if iv_params else 100
    if expected_candle_range > 0:
        range_ratio = candleRange / expected_candle_range
        if range_ratio >= 1.2:
            if candleBody > 0:
                bull_score += 1
                bull_reasons.append(f"BreakoutCandle({round(range_ratio, 1)}x)")
            elif candleBody < 0:
                bear_score += 1
                bear_reasons.append(f"BreakoutCandle({round(range_ratio, 1)}x)")

    max_score = max(bull_score, bear_score)
    direction = "BULL" if bull_score > bear_score else "BEAR" if bear_score > bull_score else "NEUTRAL"
    confidence = "HIGH" if max_score >= 5 else "MEDIUM" if max_score >= 3 else "LOW"

    print("SCALP_SCORE:", direction, "| BullScore=", bull_score, " BearScore=", bear_score,
          "| Confidence=", confidence)
    if bull_score >= 3:
        print("  SCALP_BULL_REASONS:", " + ".join(bull_reasons))
    if bear_score >= 3:
        print("  SCALP_BEAR_REASONS:", " + ".join(bear_reasons))
    if max_score >= 5:
        print("  *** SCALP_HIGH_CONVICTION:", direction, "OPPORTUNITY ***")

    # === STRUCTURED DATA LOG ===
    range_ratio_val = round(candleRange / expected_candle_range, 2) if expected_candle_range > 0 else 0
    print(f"DATAPOINT|{datetime.now().strftime('%H:%M')}|FUT={round(close[-2],1)}"
          f"|Body={candleBody}|PrevBody={prevCandleBody}|Range={candleRange}"
          f"|RangeRatio={range_ratio_val}"
          f"|CurrVolPCR={currPCR}|PrevVolPCR={prevPCR}"
          f"|CurrCPR={currCPR}|PrevCPR={prevCPR}"
          f"|VolSpike={volSpike}"
          f"|CE_PremChg={ce_premium_change}|PE_PremChg={pe_premium_change}"
          f"|BullScore={bull_score}|BearScore={bear_score}"
          f"|Dir={direction}|Conf={confidence}")

    # --- Chart Pattern Detection ---
    log_chart_patterns(opens, high, low, close, iv_params)

    sname = "NSE:NIFTYBANK-INDEX"
    strikecount = 3
    dfochain, _ochainresponse = fetch_ochain_safe(strikecount, sname, fyers, use_closest1=False)
    if dfochain is None:
        print("checkCriteriaAndTakeTrade: skipping cycle — option chain fetch failed")
        return None
    symbol = dfochain['symbol'].to_numpy()
    option_type = dfochain['option_type'].to_numpy()
    oi = dfochain['oi'].to_numpy()

    oipcr1 = round(oi[-1] / oi[-2], 2) if option_type[-1] != '' and option_type[-2] != '' and option_type[-1] == 'PE' else round(oi[-2] / oi[-1], 2)
    oipcr2 = round(oi[-3] / oi[-4], 2) if option_type[-3] != '' and option_type[-4] != '' and option_type[-3] == 'PE' else round(oi[-4] / oi[-3], 2)
    oipcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and option_type[-5] == 'PE' else round(oi[-6] / oi[-5], 2)

    print("======================================")
    print("oipcr1 = ", symbol[-2], " ", oipcr1)
    print("oipcr2 = ", symbol[-4], " ", oipcr2)
    print("oipcr3 = ", symbol[-6], " ", oipcr3)

    count = 0
    if oipcr1 >= 1: count += 1
    if oipcr2 >= 1: count += 1
    if oipcr3 >= 1: count += 1

    bearCount = 0
    if oipcr1 < 1: bearCount += 1
    if oipcr2 < 1: bearCount += 1
    if oipcr3 < 1: bearCount += 1

    oipcrBull = "NoEntry"
    oipcrBear = "NoEntry"
    if count > 1:
        oipcrBull = "BullTrade"
    elif bearCount > 1:
        oipcrBear = "BearTrade"

    print("oipcrBull =", oipcrBull, " bulcount=", count, " bearcount=", bearCount, "oipcrBear=", oipcrBear)

    volume = dfochain['volume'].to_numpy()
    pcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] != '' and option_type[-6] != '' and option_type[-5] == 'PE' else round(oi[-6] / oi[-5], 2)
    pcr4 = round(oi[-7] / oi[-8], 2) if option_type[-7] != '' and option_type[-8] != '' and option_type[-7] == 'PE' else round(oi[-8] / oi[-7], 2)
    pcr5 = round(oi[-9] / oi[-10], 2) if option_type[-9] != '' and option_type[-10] != '' and option_type[-9] == 'PE' else round(oi[-10] / oi[-9], 2)

    print("atmpcr5 pcr6=", pcr4, "  ", pcr5)

    if currPCR >= 1 and prevPCR >= 1 and oipcrBull == "BullTrade" and doNotTrade == False:
        return 0
    elif currPCR < 1 and oipcrBear == "BearTrade" and doNotTrade == False and pcr4 < 1 and pcr5 < 1:
        return 0
    else:
        print("No Entry Yet")


# ============================================================
# MAIN LOOP — Global state variables
# ============================================================

avgOiPcrMap = {}
avgOiPcr = {}
SUPP_RES_STRIKE = ''
AVGOI_PCR = 0
TOTAL_PCR = 0
SYNTH_FUT_STRIKE = ''
FUT_LTP = 0
IS_STRIKE_SHIFT = False
IS_CONSECUTIVELY_2TIMES_PCR_INCREASED = False
IS_CONSECUTIVELY_2TIMES_PCR_DECREASED = False
IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
mapFutStrike = {}
avgOiPcrList = []
count = 0
notStrikeShiftCount = 1
IS_CHOI_DIFF_GT_25PERC = False
RSI_VAL = 0
suppResCeChOi = 0
suppResPeChOi = 0
mapStrike = {}
ATM_STRIKE = 0
IS_ATM_STRIKE_SHIFT = False
atmStrikeNotShiftedCount = 1
spotLTP = 0
avgOiPcrList2 = []
isBullTrade = False
isBearTrade = False
hedgeOrderId = ''
mainOrderId = ''
tradeHedgeOption = ''
tradeATMOption = ''
entryPremium = 0  # track entry premium for trailing SL
trailTriggerPts = 0  # effective_sl / 3 — stored at entry time
slTrailed = False
slConfirmCount = 0
spread_type_decided = False  # flag to decide spread type only once per day


# ============================================================
# MAIN WHILE LOOP
# ============================================================

while x == 1:

    dt1 = datetime.now()
    now = datetime.now()
    custom_time = datetime(now.year, now.month, now.day, entryHour, entryMinute)
    custom_time1 = datetime(now.year, now.month, now.day, entryHour1, entryMinute1)

    if now >= custom_time1:

        if dt1.second <= 1 and dt1.minute % timeFrame == 0:
            count += 1
            candle_formed = 1
            optionInstum = BNFut

            # Refresh IV regime parameters every 3-min candle
            iv_params = getIVRegime(fyers)
            print("IV Params refreshed:", iv_params)

            # === SPREAD TYPE DECISION — moved to entry time (see bull/bear entry blocks) ===
            # IV Rank fetched once per day for efficiency (doesn't change intraday)
            if not spread_type_decided:
                get_iv_rank(fyers)  # cache the 30-day VIX data
                spread_type_decided = True

            # === OPTION CHAIN ANALYSIS (resilient: retries on bad response) ===
            sname = "NSE:NIFTYBANK-INDEX"
            strikecount = 8
            pcrList = []
            volPcrList = []
            choipcrList = []
            symbolList = []
            dfochain, ochainresponse = fetch_ochain_safe(strikecount, sname, fyers, use_closest1=True)
            if dfochain is None or ochainresponse is None:
                print("MAIN_LOOP: skipping cycle — option chain fetch failed")
                time.sleep(2)
                continue
            print('===')
            totalOI = helper.getTotalOI(ochainresponse)
            calloi = totalOI.get('callOi')
            putoi = totalOI.get('putOi')
            try:
                totalOIPCR = round((putoi / calloi), 2)
            except (TypeError, ZeroDivisionError):
                print("MAIN_LOOP: totalOI calc failed, skipping cycle")
                time.sleep(2)
                continue

            print("====after 3 min ochain====", now)

            symbol = dfochain['symbol'].to_numpy()
            option_type = dfochain['option_type'].to_numpy()
            oi = dfochain['oi'].to_numpy()
            volume = dfochain['volume'].to_numpy()
            chInOi = dfochain['oich'].to_numpy() if 'oich' in dfochain.columns else None

            name = helper.getIndexSpot(stock)
            intExpiry = helper.getBankNiftyExpiryDate()

            # PCR calculations for all strikes
            pcr1 = round(oi[-1] / oi[-2], 2) if option_type[-1] == 'PE' else round(oi[-2] / oi[-1], 2)
            changOI = getChangeInOI(dfochain, -1, -2)
            pcr2 = round(oi[-3] / oi[-4], 2) if option_type[-3] == 'PE' else round(oi[-4] / oi[-3], 2)
            changOIpcr2 = getChangeInOI(dfochain, -3, -4)
            pcr3 = round(oi[-5] / oi[-6], 2) if option_type[-5] == 'PE' else round(oi[-6] / oi[-5], 2)
            changOIpcr3 = getChangeInOI(dfochain, -5, -6)
            volpcr1 = round(volume[-1] / volume[-2], 2) if option_type[-1] == 'PE' else round(volume[-2] / volume[-1], 2)
            volpcr2 = round(volume[-3] / volume[-4], 2) if option_type[-3] == 'PE' else round(volume[-4] / volume[-3], 2)
            volpcr3 = round(volume[-5] / volume[-6], 2) if option_type[-5] == 'PE' else round(volume[-6] / volume[-5], 2)

            if strikecount >= 2:
                pcr4 = round(oi[-7] / oi[-8], 2) if option_type[-7] == 'PE' else round(oi[-8] / oi[-7], 2)
                changOIpcr4 = getChangeInOI(dfochain, -7, -8)
                pcr5 = round(oi[-9] / oi[-10], 2) if option_type[-9] == 'PE' else round(oi[-10] / oi[-9], 2)
                changOIpcr5 = getChangeInOI(dfochain, -9, -10)
                pcr6 = round(oi[-11] / oi[-12], 2) if option_type[-11] == 'PE' else round(oi[-12] / oi[-11], 2)
                changOIpcr6 = getChangeInOI(dfochain, -11, -12)
                pcr7 = round(oi[-13] / oi[-14], 2) if option_type[-13] == 'PE' else round(oi[-14] / oi[-13], 2)
                changOIpcr7 = getChangeInOI(dfochain, -13, -14)
                pcr8 = round(oi[-15] / oi[-16], 2) if option_type[-15] == 'PE' else round(oi[-16] / oi[-15], 2)
                changOIpcr8 = getChangeInOI(dfochain, -15, -16)
                pcr9 = round(oi[-17] / oi[-18], 2) if option_type[-17] == 'PE' else round(oi[-18] / oi[-17], 2)
                changOIpcr9 = getChangeInOI(dfochain, -17, -18)

                pcr10 = round(oi[-19] / oi[-20], 2) if option_type[-19] == 'PE' else round(oi[-20] / oi[-19], 2)
                changOIpcr10 = getChangeInOI(dfochain, -19, -20)
                pcr11 = round(oi[-21] / oi[-22], 2) if option_type[-21] == 'PE' else round(oi[-22] / oi[-21], 2)
                changOIpcr11 = getChangeInOI(dfochain, -21, -22)
                pcr12 = round(oi[-23] / oi[-24], 2) if option_type[-23] == 'PE' else round(oi[-24] / oi[-23], 2)
                changOIpcr12 = getChangeInOI(dfochain, -23, -24)
                pcr13 = round(oi[-25] / oi[-26], 2) if option_type[-25] == 'PE' else round(oi[-26] / oi[-25], 2)
                changOIpcr13 = getChangeInOI(dfochain, -25, -26)
                pcr14 = round(oi[-27] / oi[-28], 2) if option_type[-27] == 'PE' else round(oi[-28] / oi[-27], 2)
                changOIpcr14 = getChangeInOI(dfochain, -27, -28)
                pcr15 = round(oi[-29] / oi[-30], 2) if option_type[-29] == 'PE' else round(oi[-30] / oi[-29], 2)
                changOIpcr15 = getChangeInOI(dfochain, -29, -30)
                pcr16 = round(oi[-31] / oi[-32], 2) if option_type[-31] == 'PE' else round(oi[-32] / oi[-31], 2)
                changOIpcr16 = getChangeInOI(dfochain, -31, -32)
                pcr17 = round(oi[-33] / oi[-34], 2) if option_type[-33] == 'PE' else round(oi[-34] / oi[-33], 2)
                changOIpcr17 = getChangeInOI(dfochain, -33, -34)

                volpcr4 = round(volume[-7] / volume[-8], 2) if option_type[-7] == 'PE' else round(volume[-8] / volume[-7], 2)
                volpcr5 = round(volume[-9] / volume[-10], 2) if option_type[-9] == 'PE' else round(volume[-10] / volume[-9], 2)
                volpcr6 = round(volume[-11] / volume[-12], 2) if option_type[-11] == 'PE' else round(volume[-12] / volume[-11], 2)
                volpcr7 = round(volume[-13] / volume[-14], 2) if option_type[-13] == 'PE' else round(volume[-14] / volume[-13], 2)
                volpcr8 = round(volume[-15] / volume[-16], 2) if option_type[-15] == 'PE' else round(volume[-16] / volume[-15], 2)
                volpcr9 = round(volume[-17] / volume[-18], 2) if option_type[-17] == 'PE' else round(volume[-18] / volume[-17], 2)

            pcrList = [pcr1, pcr2, pcr3, pcr4, pcr5, pcr6, pcr7, pcr8, pcr9, pcr10, pcr11, pcr12, pcr13, pcr14, pcr15, pcr16, pcr17]
            volPcrList = [volpcr1, volpcr2, volpcr3, volpcr4, volpcr5, volpcr6, volpcr7, volpcr8, volpcr9]

            print("".ljust(35), "oipcr", " ", "choipcr", " ", "totalOI")
            print("pcr1 = ", symbol[-2], " ", pcr1, " ", changOI, " OI=", int(oi[-2]))
            print("pcr2 = ", symbol[-4], " ", pcr2, " ", changOIpcr2, " OI=", int(oi[-4]))
            print("pcr3 = ", symbol[-6], " ", pcr3, " ", changOIpcr3, " OI=", int(oi[-6]))
            if strikecount >= 2:
                print("pcr4 = ", symbol[-8], " ", pcr4, " ", changOIpcr4, " OI=", int(oi[-8]))
                print("pcr5 = ", symbol[-10], " ", pcr5, " ", changOIpcr5, " OI=", int(oi[-10]))
                print("pcr6 = ", symbol[-12], " ", pcr6, "  ", changOIpcr6, " OI=", int(oi[-12]))
                print("pcr7 = ", symbol[-14], " ", pcr7, " ", changOIpcr7, " OI=", int(oi[-14]))
                print("pcr8 = ", symbol[-16], " ", pcr8, " ", changOIpcr8, " OI=", int(oi[-16]))
                print("pcr9 = ", symbol[-18], " ", pcr9, " ", changOIpcr9, " OI=", int(oi[-18]))
                print("pcr10 = ", symbol[-20], " ", pcr10, " ", changOIpcr10, " OI=", int(oi[-20]))
                print("pcr11 = ", symbol[-22], " ", pcr11, " ", changOIpcr11, " OI=", int(oi[-22]))
                print("pcr12 = ", symbol[-24], " ", pcr12, " ", changOIpcr12, " OI=", int(oi[-24]))
                print("pcr13 = ", symbol[-26], " ", pcr13, " ", changOIpcr13, " OI=", int(oi[-26]))
                print("pcr14 = ", symbol[-28], " ", pcr14, " ", changOIpcr14, " OI=", int(oi[-28]))
                print("pcr15 = ", symbol[-30], " ", pcr15, " ", changOIpcr15, " OI=", int(oi[-30]))
                print("pcr16 = ", symbol[-32], " ", pcr16, " ", changOIpcr16, " OI=", int(oi[-32]))
                print("pcr17 = ", symbol[-34], " ", pcr17, " ", changOIpcr17, " OI=", int(oi[-34]))

                symbolPcrMap = {
                    symbol[-2]: pcr1, symbol[-4]: pcr2, symbol[-6]: pcr3, symbol[-8]: pcr4,
                    symbol[-10]: pcr5, symbol[-12]: pcr6, symbol[-14]: pcr7, symbol[-16]: pcr8,
                    symbol[-18]: pcr9, symbol[-20]: pcr10, symbol[-22]: pcr11, symbol[-24]: pcr12,
                    symbol[-26]: pcr13, symbol[-28]: pcr14, symbol[-30]: pcr15, symbol[-32]: pcr16,
                    symbol[-34]: pcr17
                }

                FUT_LTP = helper.manualLTP(BNFut, fyers)
                SYNTH_FUT_STRIKE = round(FUT_LTP / 100) * 100

                pcrSummation, result = sum_with_neighbors(symbolPcrMap, str(SYNTH_FUT_STRIKE))
                print("pcrSummation=", pcrSummation, " result = ", result)

                pcrSum = round(sum(pcrList), 2)
                print("PCRSUM==", pcrSum)
                avgoiPCROld = round(pcrSum / 17, 2)

                pcrSummation = round(pcrSummation, 2)
                print("pcrSummation==", pcrSummation)
                avgoiPCR = round(pcrSummation / 9, 2)

                volpcrsum = sum(volPcrList)
                print("VOLPCRSUM==", volpcrsum)
                avgvolPCR = round(volpcrsum / 9, 2)

                avgOiPcrList.append(avgoiPCR)
                avgOiPcrList2.append(avgoiPCROld)

            # ATM Strike shift detection
            bankNiftyIndex = helper.getIndexSpot(stock)
            spotLTP = helper.manualLTP(bankNiftyIndex, fyers)
            ATM_STRIKE = round(spotLTP / 100) * 100
            print("spotLTP = ", spotLTP, " ATM_STRIKE = ", ATM_STRIKE)

            if mapStrike:
                last_key = list(mapStrike.keys())[-1]
                last_value = mapStrike[last_key]
                prevATMStrike = last_value
                if ATM_STRIKE == prevATMStrike:
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount += 1
                else:
                    mapStrike.clear()
                    mapStrike[ATM_STRIKE] = ATM_STRIKE
                    IS_ATM_STRIKE_SHIFT = True
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = avgOiPcrList2[-1:]
            else:
                mapStrike[ATM_STRIKE] = ATM_STRIKE
            print("IS_ATM_STRIKE_SHIFT =", IS_ATM_STRIKE_SHIFT, " mapStrike =", mapStrike)
            print("avgOiPcrList2 =", avgOiPcrList2, "atmStrikeNotShiftedCount=", atmStrikeNotShiftedCount)
            print(IS_ATM_STRIKE_SHIFT, " ", atmStrikeNotShiftedCount, " ", len(avgOiPcrList2))

            SUPP_RES = get_support_resistance(FUT_LTP)
            if SUPP_RES != "NOTRADEZONE":
                SUPP_RES = round(SUPP_RES)

            print("SUPP_RES===", SUPP_RES, " Buffer=", iv_params.get("support_resistance_buffer", 30))
            suppResCE = helper.getOptionFormat(stock, intExpiry, SUPP_RES, "CE")
            suppResPE = helper.getOptionFormat(stock, intExpiry, SUPP_RES, "PE")

            row1 = dfochain[dfochain['symbol'] == suppResCE]
            row2 = dfochain[dfochain['symbol'] == suppResPE]
            # Total OI at SUPP_RES strike (used for morning-window rule when CHOI is too noisy)
            suppResCeOi_total = 0
            suppResPeOi_total = 0
            if not row1.empty and not row2.empty:
                suppResCeChOi = row1.iloc[0]['oich']
                suppResPeChOi = row2.iloc[0]['oich']
                suppResCeOi_total = int(row1.iloc[0]['oi'])
                suppResPeOi_total = int(row2.iloc[0]['oi'])
                print("CEoich val = ", suppResCeChOi, " PEoich val = ", suppResPeChOi, ",",
                      f"CE > PE by {round(abs(suppResCeChOi - suppResPeChOi) / max(abs(suppResCeChOi), abs(suppResPeChOi)) * 100, 1)}%" if suppResCeChOi > suppResPeChOi
                      else f"CE < PE by {round(abs(suppResCeChOi - suppResPeChOi) / max(abs(suppResCeChOi), abs(suppResPeChOi)) * 100, 1)}%")
                print("SUPP_RES TOTAL OI: CE=", suppResCeOi_total, " PE=", suppResPeOi_total, ",",
                      f"CE > PE by {round(abs(suppResCeOi_total - suppResPeOi_total) / max(suppResCeOi_total, suppResPeOi_total) * 100, 1)}%" if suppResCeOi_total > suppResPeOi_total
                      else f"CE < PE by {round(abs(suppResCeOi_total - suppResPeOi_total) / max(suppResCeOi_total, suppResPeOi_total) * 100, 1)}%")
            else:
                print("not found")

            # Morning rule: 9:15-9:45 IST. CHOI is noisy/zero in first ~10 candles after open.
            # Use TOTAL OI direction (carried from yesterday's positioning) as the trapped-writers signal.
            IS_MORNING_WINDOW = (dt1.hour == 9 and dt1.minute < 45)
            print("IS_MORNING_WINDOW=", IS_MORNING_WINDOW, " (active 9:15-9:45 IST)")

            if (suppResCeChOi > 0 and suppResPeChOi > 0):
                IS_CHOI_DIFF_GT_25PERC = is_difference_greater_than_25(suppResCeChOi, suppResPeChOi)
            else:
                print(" Either or both CE PE at suppRes unwinded i.e negative")
                IS_CHOI_DIFF_GT_25PERC = False
            print("================================================")
            print("==== signal check time ====", datetime.now())
            print("IS_CHOI_DIFF_GT_25PERC==", IS_CHOI_DIFF_GT_25PERC)

            try:
                dataFUT = helper.getHistorical(BNFut, timeFrame, 3, fyers)
            except Exception as e:
                print("MAIN_LOOP_DATA_FETCH_FAILED:", e, "— skipping this candle, will retry next cycle")
                time.sleep(2)
                continue
            opens = dataFUT['open'].to_numpy()
            high = dataFUT['high'].to_numpy()
            low = dataFUT['low'].to_numpy()
            close = dataFUT['close'].to_numpy()

            RSI_VAL1 = round(ta.momentum.RSIIndicator(pd.Series(close), 14, False).rsi().iloc[-1], 2)
            RSI_VAL2 = round(ta.momentum.RSIIndicator(pd.Series(close), 14, False).rsi().iloc[-2], 2)
            RSI_VAL = RSI_VAL2
            print("RSI_VAL1==", RSI_VAL1)
            print("RSI_VAL2==", RSI_VAL2)
            print("FUT_3m_OHLC O=", round(opens[-2], 1), " H=", round(high[-2], 1),
                  " L=", round(low[-2], 1), " C=", round(close[-2], 1),
                  " Range=", round(high[-2] - low[-2], 1))

            # --- Chart Pattern Detection on FUT candles ---
            log_chart_patterns(opens, high, low, close, iv_params)

            # PCR trend detection (3 consecutive values)
            if not IS_ATM_STRIKE_SHIFT and atmStrikeNotShiftedCount >= 3 and len(avgOiPcrList2) == 3:
                if avgOiPcrList2[0] < avgOiPcrList2[1] < avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = True
                    print("isPcrInc =", IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2)
                elif avgOiPcrList2[0] > avgOiPcrList2[1] > avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = True
                    print("isPcrDecr =", IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2)
                else:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    if avgOiPcrList2[1] < avgOiPcrList2[2] or avgOiPcrList2[1] > avgOiPcrList2[2]:
                        avgOiPcrList2 = avgOiPcrList2[1:]
                        print("recent two =", avgOiPcrList2)
                    else:
                        avgOiPcrList2 = avgOiPcrList2[2:]
                        print("neither =", avgOiPcrList2)
            elif len(avgOiPcrList2) == 3:
                remove = 3 - atmStrikeNotShiftedCount
                avgOiPcrList2 = avgOiPcrList2[remove:]
                print("none =", avgOiPcrList2)

            print("newSynthFut = ", SYNTH_FUT_STRIKE)
            print("ATMStrike = ", ATM_STRIKE)
            print("AVG_OIPCR=", avgoiPCR)
            print("avgoiPCROld= ", avgoiPCROld)
            print("SUPP_RES =", SUPP_RES)
            print("FUT LTP =", FUT_LTP)
            print("CEchoi  PechOi =", suppResCeChOi, "  ", suppResPeChOi)
            print("====================================")
            print("totalOIPCR =", totalOIPCR)
            print("AVG_VOLPCR=", avgvolPCR)
            print("atmPCR=", pcr5, " belowATM ", pcr6)
            print("atmVolPCR=", volpcr5, " belowATM ", volpcr6)
            print(IS_CHOI_DIFF_GT_25PERC, " ", FUT_LTP, " ", SUPP_RES, " ",
                  IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2, " ",
                  IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2, " ", RSI_VAL)

            # === Direction signal — different in morning vs rest of day ===
            # Morning (9:15-9:45): use TOTAL OI direction at SUPP_RES (yesterday's positioning,
            #                      since CHOI is too noisy after just one 3-min candle).
            #                      Skip IS_CHOI_DIFF_GT_25PERC (meaningless when CHOI ≈ 0).
            # Rest of day: use existing CHOI direction + 25% diff filter.
            if IS_MORNING_WINDOW:
                bull_direction_ok = (suppResCeOi_total > suppResPeOi_total)
                bear_direction_ok = (suppResPeOi_total > suppResCeOi_total)
                choi_filter_bull = True   # bypass 25% filter in morning
                choi_filter_bear = True
            else:
                bull_direction_ok = (suppResCeChOi > suppResPeChOi)
                bear_direction_ok = (suppResCeChOi < suppResPeChOi)
                choi_filter_bull = IS_CHOI_DIFF_GT_25PERC
                choi_filter_bear = IS_CHOI_DIFF_GT_25PERC

            # === BULL ENTRY ===
            if bull_direction_ok and slCount != 2 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and choi_filter_bull and FUT_LTP > SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2:
                print("In Bull trade, slCount = ", slCount, " (mode=", "MORNING_OI" if IS_MORNING_WINDOW else "NORMAL_CHOI", ")")

                # Decide spread type at entry time (real-time premium)
                intExpiry_tmp = helper.getBankNiftyExpiryDate()
                bn_ltp_tmp = helper.manualLTP(helper.getIndexSpot(stock), fyers)
                atm_strike_tmp = int(round((bn_ltp_tmp / 100), 0) * 100)
                atmPE_tmp = helper.getOptionFormat(stock, intExpiry_tmp, atm_strike_tmp, "PE")
                atmCE_tmp = helper.getOptionFormat(stock, intExpiry_tmp, atm_strike_tmp, "CE")
                atm_pe_prem = helper.manualLTP(atmPE_tmp, fyers)
                atm_ce_prem = helper.manualLTP(atmCE_tmp, fyers)
                avg_atm_premium = (atm_pe_prem + atm_ce_prem) / 2.0
                print("SPREAD_CALC_AT_ENTRY: ATM_PE=", atm_pe_prem, " ATM_CE=", atm_ce_prem,
                      " AvgATM=", round(avg_atm_premium, 2))
                spread_type, spread_decision = choose_spread_type(iv_params, avg_atm_premium, fyers)
                print("SPREAD_DECISION_AT_ENTRY:", spread_decision)

                isBullTrade = True
                if not OBSERVATION_MODE:
                    st = 1
                    takeEntry(isBullTrade, False, qty, fyers, papertrading)
                    print("after entry tradeATMOption =", tradeATMOption)
                else:
                    print(f"WOULD_HAVE_ENTERED: type={spread_decision.get('type','CREDIT')} direction=BULL qty={qty}")
                    print(f"  spread_decision={spread_decision}")
                    print(f"  iv_params(SL/Target/Spread)= SL={iv_params.get('sl_point')} Tgt={iv_params.get('target_point')} Width={iv_params.get('spread_width')}")
                    print("  (OBSERVATION_MODE=True — no real order placed)")
                    print("=" * 60)
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []
                    print("OBSERVATION_MODE: skipping post-entry SL/target setup")
                    continue

                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens = data1minFUT['open'].to_numpy()
                close = data1minFUT['close'].to_numpy()

                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)

                # PRIMARY: Direct ATM option candle range × 3 (most accurate)
                # FALLBACK: IV-formula derived sl_point (when option data insufficient, e.g., before 9:24)
                opt_range = get_option_candle_range(tradeATMOption, fyers, n_candles=10)
                if opt_range is not None and opt_range > 0:
                    measured_sl = round(opt_range * 3.5)
                    effective_sl_target = measured_sl
                    sl_source = f"OPTION_RANGE(median={opt_range},x3.5)"
                else:
                    # Fallback to IV formula when insufficient option data
                    effective_sl_target = dynamic_sl_pt
                    sl_source = f"IV_FORMULA({dynamic_sl_pt})"


                effective_sl = effective_sl_target
                effective_tgt = effective_sl_target

                # For DEBIT spread: SL/target same as credit (1:1 R:R)
                # Only difference: premium rising = profit, premium falling = loss
                if spread_decision.get("type") == "DEBIT":
                    sl = float(close[-1]) - effective_sl  # premium drops = loss for buyer
                    target = float(close[-1]) + effective_tgt  # premium rises = profit for buyer
                else:
                    # Credit: premium rising = loss, premium falling = profit
                    sl = float(close[-1]) + effective_sl
                    target = float(close[-1]) - effective_tgt

                entryPremium = float(close[-1])
                slTrailed = False
                slConfirmCount = 0
                trailTriggerPts = round(effective_sl * 0.66)
                print("ENTRY_SL_TGT: spread=", spread_decision.get("type"),
                      " SL=", sl, " Target=", target,
                      " EntryPrem=", entryPremium,
                      " TrailTrigger=", trailTriggerPts,
                      " SL_Source=", sl_source,
                      " (iv_pts=", dynamic_sl_pt,
                      " effective_sl=", effective_sl, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False

                effective_sl = effective_sl_target
                effective_tgt = effective_sl_target
                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []

            # === BEAR ENTRY ===
            elif bear_direction_ok and slCount != 2 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and choi_filter_bear and FUT_LTP < SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2:
                print("In Bear trade, slCount= ", slCount, " (mode=", "MORNING_OI" if IS_MORNING_WINDOW else "NORMAL_CHOI", ")")

                # Decide spread type at entry time (real-time premium)
                intExpiry_tmp = helper.getBankNiftyExpiryDate()
                bn_ltp_tmp = helper.manualLTP(helper.getIndexSpot(stock), fyers)
                atm_strike_tmp = int(round((bn_ltp_tmp / 100), 0) * 100)
                atmPE_tmp = helper.getOptionFormat(stock, intExpiry_tmp, atm_strike_tmp, "PE")
                atmCE_tmp = helper.getOptionFormat(stock, intExpiry_tmp, atm_strike_tmp, "CE")
                atm_pe_prem = helper.manualLTP(atmPE_tmp, fyers)
                atm_ce_prem = helper.manualLTP(atmCE_tmp, fyers)
                avg_atm_premium = (atm_pe_prem + atm_ce_prem) / 2.0
                print("SPREAD_CALC_AT_ENTRY: ATM_PE=", atm_pe_prem, " ATM_CE=", atm_ce_prem,
                      " AvgATM=", round(avg_atm_premium, 2))
                spread_type, spread_decision = choose_spread_type(iv_params, avg_atm_premium, fyers)
                print("SPREAD_DECISION_AT_ENTRY:", spread_decision)

                isBearTrade = True
                if not OBSERVATION_MODE:
                    st = 2
                    takeEntry(False, isBearTrade, qty, fyers, papertrading)
                else:
                    print(f"WOULD_HAVE_ENTERED: type={spread_decision.get('type','CREDIT')} direction=BEAR qty={qty}")
                    print(f"  spread_decision={spread_decision}")
                    print(f"  iv_params(SL/Target/Spread)= SL={iv_params.get('sl_point')} Tgt={iv_params.get('target_point')} Width={iv_params.get('spread_width')}")
                    print("  (OBSERVATION_MODE=True — no real order placed)")
                    print("=" * 60)
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []
                    print("OBSERVATION_MODE: skipping post-entry SL/target setup")
                    continue

                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens = data1minFUT['open'].to_numpy()
                close = data1minFUT['close'].to_numpy()

                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)

                # PRIMARY: Direct ATM option candle range × 3 (most accurate)
                # FALLBACK: IV-formula derived sl_point (when option data insufficient, e.g., before 9:24)
                opt_range = get_option_candle_range(tradeATMOption, fyers, n_candles=10)
                if opt_range is not None and opt_range > 0:
                    measured_sl = round(opt_range * 3.5)
                    effective_sl_target = measured_sl
                    sl_source = f"OPTION_RANGE(median={opt_range},x3.5)"
                else:
                    # Fallback to IV formula when insufficient option data
                    effective_sl_target = dynamic_sl_pt
                    sl_source = f"IV_FORMULA({dynamic_sl_pt})"

                effective_sl = effective_sl_target
                effective_tgt = effective_sl_target

                # For DEBIT spread: SL/target same as credit (1:1 R:R)
                if spread_decision.get("type") == "DEBIT":
                    sl = float(close[-1]) - effective_sl
                    target = float(close[-1]) + effective_tgt
                else:
                    sl = float(close[-1]) + effective_sl
                    target = float(close[-1]) - effective_tgt

                entryPremium = float(close[-1])
                slTrailed = False
                slConfirmCount = 0
                trailTriggerPts = round(effective_sl * 0.66)
                print("ENTRY_SL_TGT: spread=", spread_decision.get("type"),
                      " SL=", sl, " Target=", target,
                      " EntryPrem=", entryPremium,
                      " TrailTrigger=", trailTriggerPts,
                      " SL_Source=", sl_source,
                      " (iv_pts=", dynamic_sl_pt,
                      " effective_sl=", effective_sl, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []

            elif len(avgOiPcrList2) == 3:
                print("no trade yet =", avgOiPcrList2)
                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                avgOiPcrList2 = avgOiPcrList2[1:]

            # order_id = checkCriteriaAndTakeTrade()

            if tradeCEoption != "":
                optionInstum = tradeCEoption
            elif tradePEoption != "":
                optionInstum = tradePEoption

            time.sleep(1)

        elif candle_formed == 1:
            if st == 1 or st == 2:
                data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                opens2 = data1minFUT['open'].to_numpy()
                high2 = data1minFUT['high'].to_numpy()
                low2 = data1minFUT['low'].to_numpy()
                close2 = data1minFUT['close'].to_numpy()
                while y == 1:
                    dt2 = datetime.now()
                    if dt2.second <= 1 and dt2.minute % timeFrame2 == 0:
                        oneMinCandle_Formed = 1
                        data1minFUT = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                        close2 = data1minFUT['close'].to_numpy()

                        is_debit = spread_decision.get("type") == "DEBIT"

                        # === CREDIT SPREAD EXIT LOGIC ===
                        if not is_debit:
                            # Credit: premium rising = loss (SL), premium falling = profit (target)
                            if st == 1 or st == 2:
                                trail_trigger = trailTriggerPts
                                if not slTrailed and close2[-1] <= (entryPremium - trail_trigger):
                                    sl = entryPremium
                                    slTrailed = True
                                    print("TRAILING SL activated! SL moved to breakeven =", sl)

                                if close2[-1] >= sl:
                                    slConfirmCount += 1
                                    if slConfirmCount >= 2:
                                        print('SL Hit (confirmed 2 candles)')
                                        st = 0
                                        slCount += 1
                                        print('slCount =', slCount)
                                        oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                        break
                                    else:
                                        print("SL breached candle", slConfirmCount, "of 2. close=", close2[-1], " SL=", sl)
                                        time.sleep(1)
                                        break
                                elif close2[-1] <= target:
                                    print('Target hit')
                                    st = -1
                                    targetCount += 1
                                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                    break
                                else:
                                    slConfirmCount = 0
                                    print("In Trade (CREDIT). No Exit. close=", close2[-1], " SL=", sl, " target=", target)
                                    time.sleep(1)
                                    break

                        # === DEBIT SPREAD EXIT LOGIC ===
                        else:
                            # Debit: premium falling = loss (SL), premium rising = profit (target)
                            if st == 1 or st == 2:
                                trail_trigger = trailTriggerPts
                                if not slTrailed and close2[-1] >= (entryPremium + trail_trigger):
                                    sl = entryPremium
                                    slTrailed = True
                                    print("DEBIT TRAILING SL activated! SL moved to breakeven =", sl)

                                if close2[-1] <= sl:
                                    slConfirmCount += 1
                                    if slConfirmCount >= 2:
                                        print('DEBIT SL Hit (confirmed 2 candles)')
                                        st = 0
                                        slCount += 1
                                        print('slCount =', slCount)
                                        oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                        break
                                    else:
                                        print("DEBIT SL breached candle", slConfirmCount, "of 2. close=", close2[-1], " SL=", sl)
                                        time.sleep(1)
                                        break
                                elif close2[-1] >= target:
                                    print('DEBIT Target hit')
                                    st = -1
                                    targetCount += 1
                                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                    break
                                else:
                                    slConfirmCount = 0
                                    print("In Trade (DEBIT). No Exit. close=", close2[-1], " SL=", sl, " target=", target)
                                    time.sleep(1)
                                    break

                    elif oneMinCandle_Formed == 1:
                        time.sleep(1)
                    else:
                        print("Waiting for first 1min candle to form. Current Second == ", dt2.second, "  ",
                              dt2.minute % timeFrame2)
                        time.sleep(1)

            # TIME EXIT 3.24 pm
            if dt1.hour >= 15 and dt1.minute >= 24:
                if st == 1 or st == 2:
                    print("EOD Exit")
                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                print('End Of the Day')
                # Log daily summary
                print("=" * 60)
                print("DAILY_SUMMARY: Date=", datetime.now().date(),
                      " SpreadType=", spread_decision.get("type", "N/A"),
                      " DTE=", spread_decision.get("dte", "N/A"),
                      " IVRank=", spread_decision.get("iv_rank", "N/A"),
                      " PremRatio=", spread_decision.get("premium_ratio", "N/A"),
                      " SLCount=", slCount,
                      " TargetCount=", targetCount)
                print("=" * 60)
                x = 2
                break
        else:
            print("Waiting for first candle to form. Current Second == ", dt1.second, "  ", dt1.minute % timeFrame)
            time.sleep(1)
    else:
        print(" -- not entered waiting for time---", dt1.hour, ":", dt1.minute)
        time.sleep(1)

# Save trades
tradesDF.to_csv("template_indicator.csv")
