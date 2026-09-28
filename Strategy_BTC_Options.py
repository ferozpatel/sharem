"""
Strategy_BTC_Options.py — BTC daily-options credit-spread strategy (Requirement 9).

Parallel to the Fyers bot's Strategy_Sensex_May_2026.py. Mirrors the Sensex
options credit-spread structure (PCR bias, S/R band, no-trade zone,
consecutive-confirm monitoring, trailing) adapted to BTC daily options, and runs
in observation mode by default.

NOTE: This is a scaffolding stub. The startup/signal/entry/monitoring/expiry
logic is implemented across Task 14. Task 14.1 adds the startup routine,
config-constant initialization, and the observation-mode logging surface.
"""

import time
from datetime import datetime, timedelta

from delta_config import REFERENCE_TIMEZONE, REFERENCE_TIMEZONE_NAME

import helper_delta as helper
from delta_client import CredentialLoadError, require_delta

# ============================================================
# MODULE-LEVEL CONFIGURATION CONSTANTS (parallel to the Sensex constants)
# ============================================================
# Default to observation mode: log signals and WOULD_HAVE_ENTERED decisions,
# place no live orders, until the operator validates the logs (Req 9.3).
OBSERVATION_MODE = True

# Index-price anchor for the S/R band, no-trade zone, and bias (Req 9.11);
# the perpetual price is logged only (Req 9.12).
INDEX_ANCHOR_SYMBOL = ".DEXBTUSD"
PERP_SYMBOL = "BTCUSD"

# Instrument under management: BTC daily options (Req 9.1). The underlying
# asset symbol Delta uses on the option chain / in built symbols is "BTC".
INSTRUMENT_LABEL = "BTC daily options"
UNDERLYING_ASSET = "BTC"

# Signal/analysis candle timeframe in minutes (parallel to the Sensex
# `timeFrame`). Kept as an initialized-config value; the signal cycle (14.2)
# drives the actual candle reads.
TIMEFRAME_MINUTES = 3

# Support/Resistance band width and strike grid (Req 9.1).
SR_BAND_WIDTH = 1000
STRIKE_STEP = 200

# No-trade dead zone: +/- 50 points around the band midpoint (Req 9.14).
NO_TRADE_ZONE_BUFFER = 50

# Consecutive-poll confirmation counts for SL/target (Req 9.19, 9.20).
SL_CONFIRM_TICKS = 2
TGT_CONFIRM_TICKS = 2

# Number of CONSECUTIVE signal cycles the entry conditions must hold before a
# live entry is permitted (Req 9.16), mirroring the SL/target confirm style
# (SL_CONFIRM_TICKS / TGT_CONFIRM_TICKS). A short streak filters single-cycle
# noise; default 2 consecutive cycles. Entry gating (Task 14.3) tracks the
# streak in the carried cycle state and only permits entry once it is reached.
ENTRY_CONFIRM_CYCLES = 2

# Monitoring poll interval in seconds (Req 9.18).
LTP_POLL_INTERVAL = 2

# Signal-cycle cadence in seconds — how often the observation loop reads the
# anchor, rebuilds the chain snapshot, and logs the signal (Task 14.2). Kept
# separate from the SL/target monitoring poll (LTP_POLL_INTERVAL) so the
# analysis cadence can align with TIMEFRAME_MINUTES without over-polling the
# chain. Defaults to the timeframe (in seconds); adjust for a faster/slower
# observation cadence.
SIGNAL_INTERVAL_SEC = TIMEFRAME_MINUTES * 60

# Trail-to-breakeven trigger as a fraction of target (Req 9.22).
TRAIL_TRIGGER_TARGET_FRACTION = 0.60

# Whether trailing-to-breakeven is enabled (Req 9.22). When True and the open
# position's unrealized profit reaches TRAIL_TRIGGER_TARGET_FRACTION of the
# configured TARGET_POINTS, the stop-loss is moved to the breakeven price (the
# entry credit) so a winner cannot turn into a loser. Defaults to True to match
# the Sensex strategy's trailing behavior; set False to disable trailing and
# keep the fixed STOP_LOSS_POINTS stop for the life of the trade.
TRAILING_ENABLED = True

# Credit-spread + protective hedge structure (Req 9.16).
ALWAYS_CREDIT = True

# Distance (in strike steps) of the protective hedge leg from the main leg for
# a credit spread (Req 9.16). The main (credit) leg is sold near the ATM; the
# protective hedge leg is bought this many 200-pt (``STRIKE_STEP``) strikes
# FURTHER out-of-the-money (lower strike for a put spread / higher strike for a
# call spread). Default 2 strikes = 400 points of defined-risk width.
HEDGE_OFFSET_STRIKES = 2

# Pre-expiry lead time (minutes) during which no new entries open, ending at the
# 12:00 UTC daily expiry (Req 9.24).
PRE_EXPIRY_LEAD_MINUTES = 15

# The BTC daily option expiry boundary, expressed as the UTC hour of day: Delta
# BTC daily options expire at 12:00 UTC each day (17:30 IST). Every expiry-window
# / rollover computation is anchored to this boundary in the Reference_Timezone
# (UTC), consistent with helper_delta.getDailyExpiry which rolls at >= 12:00 UTC
# (Req 9.23-9.26).
DAILY_EXPIRY_UTC_HOUR = 12

# ---- Concrete live-entry numeric thresholds: [NEEDS INPUT] (Req 9.27) --------
# Finalized after the observation period once logged signals are validated.
PCR_BULL_THRESHOLD = None   # [NEEDS INPUT]
PCR_BEAR_THRESHOLD = None   # [NEEDS INPUT]
STOP_LOSS_POINTS = None     # [NEEDS INPUT]
TARGET_POINTS = None        # [NEEDS INPUT]
POSITION_SIZE = None        # [NEEDS INPUT]
MAX_TRADES_PER_DAY = None   # [NEEDS INPUT]


# ============================================================
# LOG TIMESTAMP HELPER (Reference_Timezone = UTC)
# ============================================================
def _now_ts():
    """
    Return the current timestamp as an ISO-ish string in the Reference_Timezone
    (UTC), used to prefix log lines. Every Delta API timestamp is UTC, so all
    strategy logging is anchored to UTC as well (design section 8, Req 10.1).
    """
    return datetime.now(REFERENCE_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


# IST offset from UTC (India Standard Time = UTC+5:30). DISPLAY-ONLY.
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _now_ist():
    """
    Return the current timestamp in IST (India Standard Time, UTC+5:30) as
    "YYYY-MM-DD HH:MM:SS IST", used to prefix human-facing log lines.

    DISPLAY ONLY. This does NOT change any scheduling/expiry math: Delta's
    daily expiry is 12:00 UTC and every internal time computation (getDailyExpiry,
    future expiry handling, all API timestamps) stays anchored to the
    Reference_Timezone (UTC). This helper merely shifts the current UTC wall
    clock by +5:30 for readability and appends an explicit "IST" label. Computed
    from ``datetime.now(REFERENCE_TIMEZONE)`` (UTC) so no extra tz dependency is
    required.
    """
    ist = datetime.now(REFERENCE_TIMEZONE) + _IST_OFFSET
    return ist.strftime("%Y-%m-%d %H:%M:%S") + " IST"


# ============================================================
# SUPPORT / RESISTANCE + NO-TRADE ZONE (Req 9.13, 9.14)
# ============================================================
def compute_support_resistance(index_price):
    """
    Derive the S/R band and no-trade dead zone from the ``.DEXBTUSD`` index
    anchor (Req 9.13, 9.14).

    The 1000-pt (``SR_BAND_WIDTH``) band is centered on the index price:
      * midpoint    = index_price
      * support     = midpoint - SR_BAND_WIDTH / 2   (band lower bound)
      * resistance  = midpoint + SR_BAND_WIDTH / 2   (band upper bound)
    The no-trade dead zone straddles the midpoint by ``NO_TRADE_ZONE_BUFFER``
    (±50 pt) — no directional entry is allowed while the index sits inside it
    (Req 9.14; enforced by the entry gating in 14.3):
      * no_trade_low  = midpoint - NO_TRADE_ZONE_BUFFER
      * no_trade_high = midpoint + NO_TRADE_ZONE_BUFFER

    Returns ``(support, resistance, no_trade_low, no_trade_high, midpoint)`` as
    floats. Raises ``ValueError`` when the index price is missing/non-positive
    so a bad anchor is loud rather than silently producing a bogus band.
    """
    if index_price is None:
        raise ValueError(
            "compute_support_resistance: index_price is required (got None).")
    try:
        midpoint = float(index_price)
    except (TypeError, ValueError):
        raise ValueError(
            "compute_support_resistance: index_price must be numeric, got "
            "{!r}.".format(index_price))
    if midpoint <= 0:
        raise ValueError(
            "compute_support_resistance: index_price must be positive, got "
            "{!r}.".format(index_price))

    half_band = SR_BAND_WIDTH / 2.0
    support = midpoint - half_band
    resistance = midpoint + half_band
    no_trade_low = midpoint - NO_TRADE_ZONE_BUFFER
    no_trade_high = midpoint + NO_TRADE_ZONE_BUFFER
    return support, resistance, no_trade_low, no_trade_high, midpoint


# ============================================================
# OBSERVATION-MODE LOGGING SURFACE (Req 9.4)
# ============================================================
# These print-based logging functions are the observation-mode logging surface
# (parallel to the Sensex strategy's print logging). The signal cycle (Task
# 14.2) calls them each evaluated cycle with real values; they are defined here
# so the logging contract is established with startup. Signatures are kept
# flexible/simple so 14.2 can populate the actual values. NONE of these place
# orders — observation mode logs only (Req 9.4).
def _fmt(value, nd=2):
    """Format a numeric value to `nd` decimals, tolerating None/non-numeric."""
    if value is None:
        return "N/A"
    try:
        return "{:.{nd}f}".format(float(value), nd=nd)
    except (TypeError, ValueError):
        return str(value)


def log_signal(index_price, perp_price=None, pcr=None, bias=None,
               atm_strike=None, **extra):
    """
    Log the derived signal for an evaluated cycle (Req 9.4).

    The ``.DEXBTUSD`` index price is the anchor (Req 9.11); the ``BTCUSD``
    perpetual price is logged alongside for reference only (Req 9.12). `pcr`,
    `bias`, and `atm_strike` are the cycle's computed signal values (populated
    by 14.2); any extra keyword values are appended verbatim for observability.
    """
    line = ("SIGNAL [{ts}] index({idx})={ip} perp({perp})={pp} "
            "atm_strike={atm} pcr={pcr} bias={bias}").format(
                ts=_now_ist(),
                idx=INDEX_ANCHOR_SYMBOL, ip=_fmt(index_price),
                perp=PERP_SYMBOL, pp=_fmt(perp_price),
                atm=atm_strike if atm_strike is not None else "N/A",
                pcr=_fmt(pcr, 4),
                bias=bias if bias is not None else "N/A")
    if extra:
        line += " " + " ".join("{}={}".format(k, v) for k, v in extra.items())
    print(line)


def _fmt_delta(value):
    """
    Format an own same-strike OI delta for the consolidated chain table.

    Returns a 1-decimal string for a real value; returns "" (blank) when the
    delta is missing — e.g. the first cycle, where there is no previous snapshot
    to diff against (Req 9.8). Kept blank rather than "N/A" so the ``call_oiChg``
    / ``put_oiChg`` columns read cleanly on the first cycle.
    """
    if value is None:
        return ""
    return _fmt(value, 1)


def log_option_chain_table(snapshot, oi_deltas, pcr):
    """
    Log ONE consolidated Sensex-style option-chain table per cycle (Req 9.4,
    9.6, 9.7, 9.8, 9.9).

    This single table replaces the three former per-strike tables
    (CHAIN_SNAPSHOT + PCR_TABLE + the inline OI_DELTA block) with one aligned,
    ASCII-safe grid. Columns:

        #, strike, call_oi, call_px, put_px, put_oi,
        call_oiChg, put_oiChg, oi_chg_6h*

      * ``#`` numbers the 17 strikes 1..17 ascending; the middle row (#9 of 17)
        is the ATM strike (``snapshot["atm_strike"]``) and is marked with a
        trailing " <== ATM".
      * ``call_oiChg`` / ``put_oiChg`` are the bot's OWN same-strike
        consecutive-snapshot OI deltas (Req 9.8), looked up per strike from the
        ``oi_deltas`` dict (``helper.computeSameStrikeOIDelta``); blank on the
        first cycle when there is no previous snapshot.
      * ``oi_chg_6h`` is the exchange-provided 6-hour OI-change field, logged for
        observation ONLY and never used for signals (Req 9.9); marked with a
        trailing footnote.

    The aggregate PCR over the central 9 strikes (Req 9.7) is printed in the
    header line (``aggregatePCR=...``) so the separate PCR table is no longer
    needed. Timestamps are displayed in IST (display-only; logic stays UTC).
    """
    if not isinstance(snapshot, dict):
        print("OPTION_CHAIN [{ts}] (no snapshot available)".format(
            ts=_now_ist()))
        return

    atm_strike = snapshot.get("atm_strike")
    print("OPTION_CHAIN [{ts}] ATM={atm} aggregatePCR={pcr} "
          "(central 9 strikes)".format(
              ts=_now_ist(), atm=atm_strike, pcr=_fmt(pcr, 4)))

    rows = snapshot.get("rows") or {}
    oi_deltas = oi_deltas or {}
    row_fmt = ("  {:>2} {:>10} {:>12} {:>12} {:>12} {:>12} "
               "{:>12} {:>12} {:>14}")
    print(row_fmt.format(
        "#", "strike", "call_oi", "call_px", "put_px", "put_oi",
        "call_oiChg", "put_oiChg", "oi_chg_6h*"))

    for i, strike in enumerate(snapshot.get("strikes", []), start=1):
        pair = rows.get(strike) or {}
        call_row = pair.get("call") if isinstance(pair, dict) else None
        put_row = pair.get("put") if isinstance(pair, dict) else None
        call_row = call_row if isinstance(call_row, dict) else {}
        put_row = put_row if isinstance(put_row, dict) else {}

        # 6-hour OI change is log-only (Req 9.9); prefer the call row, fall
        # back to the put row so the observation field is captured either way.
        oi_chg_6h = call_row.get("oi_change_6h")
        if oi_chg_6h is None:
            oi_chg_6h = put_row.get("oi_change_6h")

        # Own same-strike consecutive-snapshot OI deltas (Req 9.8), keyed by
        # strike; tolerate int/str key variants and first-cycle-empty deltas.
        delta_row = (oi_deltas.get(strike)
                     or oi_deltas.get(int(strike)) or {}) \
            if isinstance(oi_deltas, dict) else {}
        call_delta = delta_row.get("call_oi_delta")
        put_delta = delta_row.get("put_oi_delta")

        line = row_fmt.format(
            i, strike,
            _fmt(call_row.get("oi"), 1), _fmt(call_row.get("price")),
            _fmt(put_row.get("price")), _fmt(put_row.get("oi"), 1),
            _fmt_delta(call_delta), _fmt_delta(put_delta),
            _fmt(oi_chg_6h, 1))
        if atm_strike is not None and strike == atm_strike:
            line += " <== ATM"
        print(line)

    print("  * oi_chg_6h is exchange-provided, logged for observation only "
          "(never used for signals).")


def log_support_resistance(index_price, support, resistance,
                           no_trade_low, no_trade_high, midpoint=None):
    """
    Log the support/resistance band and the no-trade dead zone derived from the
    index anchor (Req 9.4, 9.13, 9.14).
    """
    print("SUPPORT_RESISTANCE [{ts}] index={ip} midpoint={mid} "
          "support={sup} resistance={res} "
          "no_trade_zone=[{ntl}, {nth}] (band_width={bw}, buffer={buf})".format(
              ts=_now_ist(), ip=_fmt(index_price),
              mid=_fmt(midpoint if midpoint is not None else index_price),
              sup=_fmt(support), res=_fmt(resistance),
              ntl=_fmt(no_trade_low), nth=_fmt(no_trade_high),
              bw=SR_BAND_WIDTH, buf=NO_TRADE_ZONE_BUFFER))


def log_would_have_entered(decision, reason=None, **details):
    """
    Log the observation-mode ``WOULD_HAVE_ENTERED`` decision for an evaluated
    cycle (Req 9.4).

    In observation mode NO live order is placed — this log line records what the
    strategy WOULD have done had observation mode been disabled. `decision` is a
    truthy would-enter flag (or a short decision string); `reason` explains the
    gate outcome; any extra `details` (proposed legs, strikes, side, size) are
    appended for observability. The live-order path is a separate hook (see
    ``_place_live_entry`` / Task 14.3) and is NEVER exercised here.
    """
    if isinstance(decision, str):
        verdict = decision
    else:
        verdict = "WOULD_ENTER" if decision else "NO_ENTRY"
    line = "WOULD_HAVE_ENTERED [{ts}] decision={verdict}".format(
        ts=_now_ist(), verdict=verdict)
    if reason:
        line += " reason={}".format(reason)
    if details:
        line += " " + " ".join(
            "{}={}".format(k, v) for k, v in details.items())
    line += " | OBSERVATION_MODE={} (no live order placed)".format(
        OBSERVATION_MODE)
    print(line)


# ============================================================
# SIGNAL CYCLE (Req 9.6-9.13) — Task 14.2
# ============================================================
# The signal cycle is the observation-mode analogue of the Sensex strategy's
# `checkCriteriaAndTakeTrade`: each cycle reads the `.DEXBTUSD` index anchor
# (Req 9.11), logs the `BTCUSD` perpetual (Req 9.12), builds + logs the
# 17-strike snapshot (Req 9.6), computes + logs the PCR over the 9 central
# strikes (Req 9.7), diffs same-strike OI vs the previous snapshot (Req 9.8)
# while logging the exchange 6-hour field for observation only (Req 9.9),
# derives the S/R band + no-trade zone from the anchor (Req 9.13/9.14), tracks
# the consecutive same-strike PCR change across cycles (Req 9.10), and derives
# a directional bias from PCR + index position relative to the 1000-pt band
# (Req 9.13). It places NO orders — observation only (Req 9.4).


def _pcr_is_finite(pcr):
    """True when `pcr` is a real, finite number (guards inf/None from computePCR)."""
    if pcr is None:
        return False
    try:
        value = float(pcr)
    except (TypeError, ValueError):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def update_pcr_trend(prev_pcr, curr_pcr, prev_trend=None, eps=1e-9):
    """
    Track the consecutive same-strike PCR increment/decrement across cycles
    (Req 9.10).

    "Same-strike PCR change" here compares the PCR value of the SAME central
    9-strike window cycle-over-cycle — the window is re-centered on the ATM each
    cycle but no strike is shifted within the comparison, so the trend reflects
    the aggregate central-strike PCR moving up/down/flat between consecutive
    cycles (mirroring the Sensex consecutive-PCR check). `prev_trend` is the
    trend dict returned by the previous cycle (or None on the first cycle).

    Returns a dict::

        {"direction": "up"|"down"|"flat"|"n/a",
         "streak": <int>,        # consecutive cycles in `direction`
         "prev_pcr": <float|None>,
         "curr_pcr": <float|None>}

    `direction` is "n/a" when either PCR is missing/non-finite (e.g. computePCR
    returned inf for an all-put window) so a bad value never corrupts the streak.
    On the first cycle (no prev_pcr) direction is "flat" with streak 1.
    """
    prev_dir = (prev_trend or {}).get("direction")
    prev_streak = (prev_trend or {}).get("streak", 0) or 0

    if not _pcr_is_finite(curr_pcr):
        # Current value unusable: report n/a without disturbing the streak base.
        return {"direction": "n/a", "streak": 0,
                "prev_pcr": prev_pcr, "curr_pcr": curr_pcr}

    if not _pcr_is_finite(prev_pcr):
        # First usable cycle: nothing to diff against yet.
        return {"direction": "flat", "streak": 1,
                "prev_pcr": prev_pcr, "curr_pcr": curr_pcr}

    delta = float(curr_pcr) - float(prev_pcr)
    if delta > eps:
        direction = "up"
    elif delta < -eps:
        direction = "down"
    else:
        direction = "flat"

    if direction == prev_dir:
        streak = prev_streak + 1
    else:
        streak = 1

    return {"direction": direction, "streak": streak,
            "prev_pcr": prev_pcr, "curr_pcr": curr_pcr}


def _provisional_pcr_label(pcr):
    """
    Provisional, threshold-free PCR label for observation logging (Req 9.13).

    The concrete ``PCR_BULL_THRESHOLD`` / ``PCR_BEAR_THRESHOLD`` are
    ``[NEEDS INPUT]`` (None) until the observation period is validated, so this
    does NOT gate on them. It emits a provisional lean using 1.0 as a neutral
    pivot purely for readability — PCR > 1 is put-heavy (leans bullish), PCR < 1
    is call-heavy (leans bearish) — and the caller always annotates that the
    real thresholds remain [NEEDS INPUT].
    """
    if not _pcr_is_finite(pcr):
        if pcr == float("inf"):
            return "PCR_HIGH(all-put/undefined-call)"
        return "PCR_NA"
    value = float(pcr)
    if value > 1.0:
        return "PCR_HIGH(put-heavy,provisional-bullish)"
    if value < 1.0:
        return "PCR_LOW(call-heavy,provisional-bearish)"
    return "PCR_NEUTRAL(provisional)"


def derive_directional_bias(index_price, pcr, no_trade_low, no_trade_high):
    """
    Derive the directional bias from the PCR together with the index position
    relative to the 1000-pt S/R band / no-trade zone (Req 9.13).

    Position rule (matches the entry-side policy that Task 14.3 will enforce,
    Req 9.14/9.15 — here we only DERIVE + LOG, never gate an order):
      * index inside the no-trade zone (ntl <= index <= nth) -> NEUTRAL/no-trade
      * index below the no-trade zone (support side)         -> BULL permitted
      * index above the no-trade zone (resistance side)      -> BEAR permitted

    The PCR is combined provisionally via ``_provisional_pcr_label`` (1.0 pivot)
    WITHOUT hard-gating on the ``[NEEDS INPUT]`` PCR thresholds. The returned
    `decision`/`reason` feed ``log_would_have_entered`` (observation only).

    Returns ``(bias, position, decision, reason, pcr_label)`` where `bias` is a
    short human label, `position` is one of
    ``no_trade_zone``/``support_side``/``resistance_side``, `decision` is a
    provisional WOULD-HAVE verdict string, and `reason` explains it.
    """
    pcr_label = _provisional_pcr_label(pcr)
    thresholds_note = "thresholds=[NEEDS INPUT]"

    # Position of the index relative to the no-trade zone / band.
    if index_price is None:
        return ("UNKNOWN", "unknown", "NO_ENTRY",
                "index price unavailable ({})".format(thresholds_note),
                pcr_label)

    try:
        idx = float(index_price)
    except (TypeError, ValueError):
        return ("UNKNOWN", "unknown", "NO_ENTRY",
                "index price non-numeric ({})".format(thresholds_note),
                pcr_label)

    if no_trade_low <= idx <= no_trade_high:
        return ("NEUTRAL", "no_trade_zone", "NO_ENTRY",
                "index inside no-trade zone [{}, {}] ({})".format(
                    _fmt(no_trade_low), _fmt(no_trade_high), thresholds_note),
                pcr_label)

    if idx < no_trade_low:
        # Support side: only a bull bias is permitted by position.
        provisional_pcr_bull = _pcr_is_finite(pcr) and float(pcr) > 1.0
        provisional_pcr_bull = provisional_pcr_bull or pcr == float("inf")
        if provisional_pcr_bull:
            decision = "WOULD_ENTER(bull, provisional)"
            reason = ("support side AND {} agrees (bullish); {} — "
                      "provisional only".format(pcr_label, thresholds_note))
        else:
            decision = "NO_ENTRY(bull-side, PCR not bullish)"
            reason = ("support side but {} does not lean bullish; {}".format(
                pcr_label, thresholds_note))
        return ("BULL", "support_side", decision, reason, pcr_label)

    # idx > no_trade_high -> resistance side: only a bear bias is permitted.
    provisional_pcr_bear = _pcr_is_finite(pcr) and float(pcr) < 1.0
    if provisional_pcr_bear:
        decision = "WOULD_ENTER(bear, provisional)"
        reason = ("resistance side AND {} agrees (bearish); {} — "
                  "provisional only".format(pcr_label, thresholds_note))
    else:
        decision = "NO_ENTRY(bear-side, PCR not bearish)"
        reason = ("resistance side but {} does not lean bearish; {}".format(
            pcr_label, thresholds_note))
    return ("BEAR", "resistance_side", decision, reason, pcr_label)


# ============================================================
# ENTRY GATING (Req 9.14-9.17) — Task 14.3
# ============================================================
def market_hours_permit():
    """
    Market-hours entry policy hook (Req 9.16).

    BTC daily options trade ~24/7 on Delta, so this permissively returns True
    by default. The pre-expiry lead-window gate that suppresses NEW entries in
    the final minutes before the 12:00 UTC daily expiry (Req 9.24) is a
    separate concern implemented in Task 14.8; it is intentionally NOT applied
    here. Kept as a hook so 14.8 can layer the expiry-window / session policy on
    top without touching the entry-gating core.
    """
    return True


def _provisional_side_agreement(position, pcr):
    """
    Provisional (threshold-free) check that the PCR lean AGREES with the
    band-side direction, mirroring ``derive_directional_bias`` (Req 9.13, 9.15).

    Support side permits bull entries only and wants a put-heavy lean
    (PCR > 1.0, or the all-put ``inf`` case); resistance side permits bear
    entries only and wants a call-heavy lean (PCR < 1.0). The concrete
    ``PCR_BULL_THRESHOLD`` / ``PCR_BEAR_THRESHOLD`` remain ``[NEEDS INPUT]``, so
    1.0 is used only as a provisional pivot to drive the consecutive-cycle
    confirmation counter during observation — it never sizes or fires a live
    order on its own (the live path additionally requires the configured
    thresholds; see ``evaluate_entry``).
    """
    if position == "support_side":
        return (_pcr_is_finite(pcr) and float(pcr) > 1.0) or pcr == float("inf")
    if position == "resistance_side":
        return _pcr_is_finite(pcr) and float(pcr) < 1.0
    return False


def evaluate_entry(bias, position, pcr, pcr_trend, index_price, snapshot,
                   state):
    """
    Evaluate the entry gates for one signal cycle WITHOUT placing any order
    (Req 9.14-9.16). Returns a structured decision and updates the carried
    consecutive-confirm counter in ``state``.

    Gates (evaluated in order; ALL must pass for a live entry):
      1. No-trade zone (Req 9.14): no entry while the index sits inside the
         no-trade zone (``position == "no_trade_zone"``).
      2. Directional side (Req 9.15): a valid band side must be established —
         support side permits bull only, resistance side permits bear only
         (``bias``/``position`` already encode this from
         ``derive_directional_bias``); the provisional PCR lean must agree.
      3. No open position (Req 9.16): ``state["position_open"]`` must be false.
      4. Daily trade limit (Req 9.16): ``state["trades_today"]`` must be below
         ``MAX_TRADES_PER_DAY``. That constant is ``[NEEDS INPUT]`` (None); when
         unset the cap cannot be enforced, so a LIVE entry is blocked (logged)
         rather than opening an uncapped position.
      5. Confirmation (Req 9.16): the directional condition must hold for
         ``ENTRY_CONFIRM_CYCLES`` CONSECUTIVE cycles. The streak counter lives
         in ``state["entry_confirm_count"]`` and is reset whenever the
         condition breaks (mirrors the SL/target confirm style).
      6. Market-hours policy (Req 9.16): ``market_hours_permit()`` must permit.
      7. Live params (Req 9.27): ``PCR_BULL_THRESHOLD`` / ``PCR_BEAR_THRESHOLD``
         / ``POSITION_SIZE`` are ``[NEEDS INPUT]`` — while any is None a live
         order cannot be thresholded/sized, so live entry stays blocked. This
         is tracked as a ``[NEEDS INPUT]`` blocker separate from the pure gates
         so observation logging can still show a would-have-entered verdict.

    Returns a dict::

        {"gated_enter": bool,   # pure gates 1-6 all pass (observation verdict)
         "live_ready": bool,    # gated_enter AND params configured + cap set
         "confirmed": bool,     # streak >= ENTRY_CONFIRM_CYCLES
         "conditions_met": bool,# directional condition held THIS cycle
         "confirm_count": int, "confirm_required": int,
         "blockers": [str], "unset_params": [str],
         "reason": str, "bias": bias, "position": position}

    Places NO orders and performs NO network calls — the caller decides whether
    to route to ``_place_live_entry`` (only when live_ready AND not
    OBSERVATION_MODE).
    """
    state = state if isinstance(state, dict) else {}
    blockers = []

    # ----- Directional entry condition (side + provisional PCR lean) -----
    valid_side = position in ("support_side", "resistance_side")
    conditions_met = valid_side and _provisional_side_agreement(position, pcr)

    # Consecutive-cycle confirmation streak (mirrors SL/TGT confirm counters):
    # increment while the condition holds, reset to 0 the moment it breaks.
    prev_count = state.get("entry_confirm_count", 0) or 0
    confirm_count = prev_count + 1 if conditions_met else 0
    state["entry_confirm_count"] = confirm_count
    confirmed = confirm_count >= ENTRY_CONFIRM_CYCLES

    # ----- Gate 1: no-trade zone (Req 9.14) / Gate 2: valid side (9.15) --
    if position == "no_trade_zone":
        blockers.append("no_trade_zone(9.14)")
    elif not valid_side:
        blockers.append("no_directional_side(9.15)")
    elif not conditions_met:
        # Valid side but the provisional PCR lean disagrees with it (9.15).
        blockers.append("pcr_lean_disagrees(9.15)")

    # ----- Gate 3: no position already open (Req 9.16) -------------------
    if state.get("position_open"):
        blockers.append("position_already_open(9.16)")

    # ----- Gate 6: market-hours policy permits new entries (Req 9.16) ----
    if not market_hours_permit():
        blockers.append("market_hours_closed(9.16)")

    # ----- Gate 4: daily trade limit not reached (Req 9.16) --------------
    trades_today = state.get("trades_today", 0) or 0
    if MAX_TRADES_PER_DAY is None:
        # [NEEDS INPUT]: cannot enforce the cap -> block LIVE entry (Req 9.27).
        blockers.append("max_trades_per_day_unset[NEEDS INPUT]")
    elif trades_today >= MAX_TRADES_PER_DAY:
        blockers.append("daily_trade_limit_reached(9.16)")

    # ----- Gate 5: confirmed for the configured consecutive cycles (9.16)-
    if not confirmed:
        blockers.append("awaiting_confirm({}/{})".format(
            confirm_count, ENTRY_CONFIRM_CYCLES))

    # ----- Gate 7: live sizing/threshold params configured (Req 9.27) ----
    live_params = {
        "PCR_BULL_THRESHOLD": PCR_BULL_THRESHOLD,
        "PCR_BEAR_THRESHOLD": PCR_BEAR_THRESHOLD,
        "POSITION_SIZE": POSITION_SIZE,
    }
    unset_params = [name for name, value in live_params.items()
                    if value is None]
    if unset_params:
        blockers.append(
            "live_params_unset[NEEDS INPUT]:{}".format(",".join(unset_params)))

    # Pure entry gates (1-6): exclude the [NEEDS INPUT] blockers so observation
    # mode can still report a would-have-entered verdict once the observation
    # period supplies the concrete params. ``live_ready`` requires EVERYTHING.
    gating_blockers = [b for b in blockers if "NEEDS INPUT" not in b]
    gated_enter = not gating_blockers
    live_ready = not blockers

    if gated_enter:
        reason = ("all entry gates satisfied (bias={}, {}, confirmed "
                  "{}/{})".format(bias, position, confirm_count,
                                  ENTRY_CONFIRM_CYCLES))
    else:
        reason = "entry blocked: " + "; ".join(blockers)

    return {
        "gated_enter": gated_enter,
        "live_ready": live_ready,
        "confirmed": confirmed,
        "conditions_met": conditions_met,
        "confirm_count": confirm_count,
        "confirm_required": ENTRY_CONFIRM_CYCLES,
        "blockers": blockers,
        "unset_params": unset_params,
        "reason": reason,
        "bias": bias,
        "position": position,
    }


def run_signal_cycle(client, prev_snapshot=None, prev_pcr=None,
                     prev_pcr_trend=None, state=None):
    """
    Run ONE observation-mode signal cycle (Req 9.6-9.13). Places no orders.

    Steps (design section 7):
      1. Read the ``.DEXBTUSD`` index anchor (Req 9.11).
      2. Read + log the ``BTCUSD`` perpetual, log-only (Req 9.12).
      3. Resolve the daily expiry (DDMMYY -> DD-MM-YYYY for the chain query).
      4. Compute the ATM strike from the anchor.
      5. Fetch the chain + build the 17-strike snapshot (Req 9.6).
      6. Compute the PCR over the 9 central strikes (Req 9.7).
      7. Diff same-strike OI vs the previous snapshot (Req 9.8); the exchange
         6-hour field is logged via ``log_option_chain_table`` only (Req 9.9).
      8. Derive the S/R band + no-trade zone from the anchor (Req 9.13/9.14).
      9. Track the consecutive same-strike PCR change across cycles (Req 9.10).
     10. Derive the directional bias from PCR + index position (Req 9.13).
     11. Emit the full logging surface (signal, chain, PCR, S/R, would-enter).
     12. Evaluate the entry gates (Req 9.14-9.16) via ``evaluate_entry``,
         carrying the open-position/confirm-streak/trade-count state.
     13. In OBSERVATION_MODE log WOULD_HAVE_ENTERED only (Req 9.4); in live mode
         (OBSERVATION_MODE False) place the credit spread via
         ``_place_live_entry`` when the gates are satisfied, recording the open
         position on success or leaving it not-open on failure (Req 9.16/9.17).

    ``state`` (optional) carries the entry-gating fields from the previous
    cycle: ``position_open``, ``entry_confirm_count``, ``trades_today``,
    ``open_position``. Returns a state dict — ``{"snapshot", "pcr",
    "pcr_trend", "oi_deltas", "bias", "position", "decision", "index_price",
    "perp_price", "entry", "position_open", "entry_confirm_count",
    "trades_today", "open_position"}`` — so the caller can carry both the
    diff inputs (Req 9.8, 9.10) and the entry-gating state into the next cycle.
    ``_place_live_entry`` is called ONLY when OBSERVATION_MODE is False.

    Open-position monitoring (Task 14.6, Req 9.18-9.22): when the carried state
    reports a position already open, this cycle MONITORS that position for exit
    instead of evaluating a NEW entry — a position that is open should be watched
    for its target/SL/trailing exit, not re-entered. It delegates to
    ``monitor_open_position`` (once per cycle; finer per-``LTP_POLL_INTERVAL``
    polling can be layered on later without blocking the loop) and returns early,
    carrying the previous cycle's snapshot/PCR/trend forward for continuity.
    """
    carried = state if isinstance(state, dict) else {}

    # ---- Open position? Monitor for exit instead of evaluating entry ----
    # (Task 14.6, Req 9.18-9.22). Monitoring mutates the carried state's
    # confirmation counters / trail flag and, on a completed exit, flips
    # position_open back to False so the next cycle resumes entry evaluation.
    if carried.get("position_open") and isinstance(
            carried.get("open_position"), dict):
        monitor_result = monitor_open_position(client, carried)
        return {
            # Carry the diff inputs forward so the next entry-evaluation cycle
            # still diffs OI (9.8) / PCR trend (9.10) against the prior snapshot.
            "snapshot": carried.get("snapshot"),
            "pcr": carried.get("pcr"),
            "pcr_trend": carried.get("pcr_trend"),
            "oi_deltas": carried.get("oi_deltas"),
            "bias": carried.get("bias"),
            "position": carried.get("position"),
            "decision": carried.get("decision"),
            "index_price": carried.get("index_price"),
            "perp_price": carried.get("perp_price"),
            "entry": carried.get("entry"),
            "monitor": monitor_result,
            # Entry-gating + monitoring state (possibly updated by the monitor).
            "position_open": carried.get("position_open", False),
            "entry_confirm_count": carried.get("entry_confirm_count", 0),
            "trades_today": carried.get("trades_today", 0),
            "open_position": carried.get("open_position"),
            "tgt_confirm_count": carried.get("tgt_confirm_count", 0),
            "sl_confirm_count": carried.get("sl_confirm_count", 0),
            "sl_at_breakeven": carried.get("sl_at_breakeven", False),
            "last_exit": carried.get("last_exit"),
        }

    # 1. Index anchor (Req 9.11).
    index_price = helper.getIndexPrice(client, INDEX_ANCHOR_SYMBOL)

    # 2. Perpetual price — log-only reference, never the anchor (Req 9.12).
    try:
        perp_price = helper.manualLTP(PERP_SYMBOL, client)
    except Exception as exc:  # noqa: BLE001 - perp is log-only; never fatal
        perp_price = None
        print("SIGNAL_WARN [{ts}] perpetual {sym} price unavailable "
              "(log-only): {typ}: {msg}".format(
                  ts=_now_ist(), sym=PERP_SYMBOL,
                  typ=type(exc).__name__, msg=exc))

    # 3. Daily expiry -> chain-query date format.
    ddmmyy = helper.getDailyExpiry()
    expiry_ddmmyyyy = helper.expiry_ddmmyy_to_ddmmyyyy(ddmmyy)

    # 4. ATM strike from the anchor.
    atm = helper._nearest_atm_strike(index_price, STRIKE_STEP)

    # 5. Fetch chain + build the 17-strike snapshot (Req 9.6).
    chain = helper.fetchOptionChain(UNDERLYING_ASSET, expiry_ddmmyyyy, client)
    snapshot = helper.buildChainSnapshot(chain, atm, step=STRIKE_STEP, n=8)

    # 6. PCR over the 9 central strikes (Req 9.7).
    pcr = helper.computePCR(snapshot, central=9)

    # 7. Same-strike OI deltas vs the previous cycle's snapshot (Req 9.8). The
    #    exchange 6-hour field is log-only and surfaced via
    #    log_option_chain_table (Req 9.9); it is intentionally NOT consulted for
    #    any signal here.
    oi_deltas = helper.computeSameStrikeOIDelta(prev_snapshot, snapshot)

    # 8. S/R band + no-trade zone from the anchor (Req 9.13/9.14).
    support, resistance, ntl, nth, midpoint = compute_support_resistance(
        index_price)

    # 9. Consecutive same-strike PCR change across cycles (Req 9.10).
    pcr_trend = update_pcr_trend(prev_pcr, pcr, prev_pcr_trend)

    # 10. Directional bias from PCR + index position relative to the band
    #     (Req 9.13). Does NOT hard-gate on the [NEEDS INPUT] PCR thresholds.
    bias, position, decision, reason, pcr_label = derive_directional_bias(
        index_price, pcr, ntl, nth)

    # 11. Emit the observation-mode logging surface (Req 9.4). No orders.
    log_signal(
        index_price, perp_price=perp_price, pcr=pcr, bias=bias,
        atm_strike=snapshot.get("atm_strike"),
        position=position, pcr_label=pcr_label,
        pcr_trend="{}x{}".format(pcr_trend.get("direction"),
                                 pcr_trend.get("streak")),
        expiry=expiry_ddmmyyyy)
    # ONE consolidated Sensex-style option-chain table per cycle (Req 9.6,
    # 9.7, 9.8, 9.9): per-strike call/put price+OI, the bot's OWN same-strike
    # OI deltas, and the log-only exchange 6-hour field, with the aggregate PCR
    # in its header. Replaces the former CHAIN_SNAPSHOT + PCR_TABLE + inline
    # OI_DELTA blocks (now a single table per cycle).
    log_option_chain_table(snapshot, oi_deltas, pcr)

    log_support_resistance(index_price, support, resistance, ntl, nth,
                           midpoint=midpoint)

    # Consecutive same-strike PCR-trend line (Req 9.10).
    print("PCR_TREND [{ts}] direction={dir} consecutive_cycles={streak} "
          "prev_pcr={prev} curr_pcr={curr}".format(
              ts=_now_ist(), dir=pcr_trend.get("direction"),
              streak=pcr_trend.get("streak"),
              prev=_fmt(prev_pcr, 4), curr=_fmt(pcr, 4)))

    # 12. Entry gating (Req 9.14-9.17) — Task 14.3. Carry the entry-gating
    #     state (open-position flag, consecutive-confirm streak, per-day trade
    #     count, open-position details) from the previous cycle so the
    #     confirmation counter and limits persist across cycles. ``carried`` is
    #     the input state dict already resolved at the top of the cycle.
    entry_state = {
        "position_open": bool(carried.get("position_open", False)),
        "entry_confirm_count": carried.get("entry_confirm_count", 0) or 0,
        "trades_today": carried.get("trades_today", 0) or 0,
        "open_position": carried.get("open_position"),
    }
    entry = evaluate_entry(
        bias, position, pcr, pcr_trend, index_price, snapshot, entry_state)

    # 13. Act on the gated decision.
    #     * OBSERVATION_MODE (default True): LOG WOULD_HAVE_ENTERED only; never
    #       place an order and never call _place_live_entry (Req 9.4).
    #     * Live mode (OBSERVATION_MODE False) AND live_ready: place the credit
    #       spread; record the open position on success, or on failure log and
    #       leave the position not-open, continuing to evaluate (Req 9.16/9.17).
    confirm_str = "{}/{}".format(entry["confirm_count"],
                                 entry["confirm_required"])
    blockers_str = ";".join(entry["blockers"]) if entry["blockers"] else "none"
    if OBSERVATION_MODE:
        log_would_have_entered(
            "WOULD_ENTER" if entry["gated_enter"] else "NO_ENTRY",
            reason=entry["reason"], bias=bias, position=position,
            pcr=_fmt(pcr, 4), pcr_label=pcr_label, confirm=confirm_str,
            blockers=blockers_str, signal_decision=decision,
            pcr_trend="{}x{}".format(pcr_trend.get("direction"),
                                     pcr_trend.get("streak")))
    elif entry["live_ready"]:
        # Live credit-spread placement (Req 9.16). Any placement failure is
        # logged inside _place_live_entry, which leaves the position not-open
        # (Req 9.17); the loop continues regardless.
        try:
            result = _place_live_entry(
                client, bias, position, snapshot, index_price, entry_state)
        except Exception as exc:  # noqa: BLE001 - never crash the loop (9.17)
            result = {"ok": False, "opened": False,
                      "error": "{}: {}".format(type(exc).__name__, exc)}
            print("LIVE_ENTRY_FAIL [{}] unexpected error placing entry: {} — "
                  "leaving position not-open (9.17).".format(
                      _now_ist(), result["error"]))
        if result.get("ok") and result.get("opened"):
            entry_state["position_open"] = True
            entry_state["open_position"] = result.get("open_position")
            entry_state["trades_today"] = entry_state.get("trades_today", 0) + 1
            # Reset the confirm streak after opening so the next entry must
            # re-confirm from scratch.
            entry_state["entry_confirm_count"] = 0
            print("ENTRY_OPENED [{}] position recorded open; trades_today={}."
                  .format(_now_ist(), entry_state["trades_today"]))
        else:
            # Not-open per Req 9.17; continue evaluating next cycles.
            print("ENTRY_NOT_OPENED [{}] live entry did not open "
                  "(reason={}); continuing.".format(
                      _now_ist(),
                      result.get("error") if isinstance(result, dict)
                      else result))
    else:
        # Live mode but gates not fully satisfied — do not place any order.
        print("ENTRY_SKIPPED [{}] live entry gates not satisfied: {}".format(
            _now_ist(), entry["reason"]))

    return {
        "snapshot": snapshot,
        "pcr": pcr,
        "pcr_trend": pcr_trend,
        "oi_deltas": oi_deltas,
        "bias": bias,
        "position": position,
        "decision": decision,
        "index_price": index_price,
        "perp_price": perp_price,
        # Entry-gating state carried into the next cycle (Task 14.3).
        "entry": entry,
        "position_open": entry_state.get("position_open", False),
        "entry_confirm_count": entry_state.get("entry_confirm_count", 0),
        "trades_today": entry_state.get("trades_today", 0),
        "open_position": entry_state.get("open_position"),
        # Monitoring state (Task 14.6) carried forward. A freshly opened
        # position starts its target/SL confirmation streaks and trail flag at
        # zero/False; if no entry opened this cycle the prior values persist.
        "tgt_confirm_count": (0 if entry_state.get("position_open")
                              else carried.get("tgt_confirm_count", 0)),
        "sl_confirm_count": (0 if entry_state.get("position_open")
                             else carried.get("sl_confirm_count", 0)),
        "sl_at_breakeven": (False if entry_state.get("position_open")
                            else carried.get("sl_at_breakeven", False)),
        "last_exit": carried.get("last_exit"),
    }


def checkCriteriaAndTakeTrade(client, state=None):
    """
    Sensex-style per-cycle entry point (mirrors ``checkCriteriaAndTakeTrade``).

    Thin wrapper over ``run_signal_cycle`` that threads the carried observation
    state — the previous cycle's ``snapshot``/``pcr``/``pcr_trend`` — so the
    same-strike OI delta (Req 9.8) and consecutive PCR trend (Req 9.10) diff
    against the prior cycle, along with the entry-gating state
    (``position_open``/``entry_confirm_count``/``trades_today``/
    ``open_position``) so the confirmation streak and limits persist. Returns
    the updated state dict for the next call. In OBSERVATION_MODE (default)
    this evaluates and LOGS only and places no orders (Req 9.4); the live
    credit-spread path (``_place_live_entry``) runs only when OBSERVATION_MODE
    is False and the entry gates are satisfied (Req 9.16).
    """
    state = state or {}
    return run_signal_cycle(
        client,
        prev_snapshot=state.get("snapshot"),
        prev_pcr=state.get("pcr"),
        prev_pcr_trend=state.get("pcr_trend"),
        state=state)


# ============================================================
# LIVE-ORDER HOOK (Req 9.5, 9.16, 9.17) — Task 14.3
# ============================================================
def _place_live_entry(client, bias, position, snapshot, index_price,
                      state=None):
    """
    Place a live credit-spread entry: SELL the main leg + BUY an OTM protective
    hedge leg, and report the result (Req 9.5, 9.16, 9.17).

    Leg construction by direction (``ALWAYS_CREDIT`` credit spread + protective
    hedge):
      * BULL / support side  -> credit PUT spread: SELL a near-ATM put (main,
        collects premium) and BUY a further-OTM (LOWER-strike) put (protective
        hedge, defines risk).
      * BEAR / resistance side -> credit CALL spread: SELL a near-ATM call
        (main) and BUY a further-OTM (HIGHER-strike) call (protective hedge).
    The main strike is the ATM (``snapshot["atm_strike"]``, falling back to the
    nearest grid strike to ``index_price``); the hedge strike is
    ``HEDGE_OFFSET_STRIKES`` × ``STRIKE_STEP`` further OTM. Symbols are built via
    ``helper.buildSymbol`` for the current daily expiry
    (``helper.getDailyExpiry()``); orders route through ``helper.placeOrder``
    (market orders). No order logic is duplicated here.

    Safety:
      * HARD guard: refuses to act while ``OBSERVATION_MODE`` is True (Req 9.4)
        — the caller only invokes this in live mode, this is defense in depth.
      * ``[NEEDS INPUT]`` guard: if ``POSITION_SIZE`` is None there is no size to
        place, so it logs and returns a failure (no order sent) (Req 9.27).

    Failure handling (Req 9.17): on any leg/symbol/sizing failure it logs the
    failure, leaves the position not-open, and returns ``{"ok": False,
    "opened": False, ...}``. If the main SELL fills but the protective hedge BUY
    fails (NAKED SHORT risk), it logs a critical warning and makes a best-effort
    attempt to close the main leg, then returns not-open.

    On full success returns ``{"ok": True, "opened": True, "open_position":
    {...leg symbols/strikes/ids/size...}, "main_leg": <res>, "hedge_leg":
    <res>}``.
    """
    state = state if isinstance(state, dict) else {}

    # HARD SAFETY: never transmit a live order in observation mode (Req 9.4).
    if OBSERVATION_MODE:
        print("LIVE_ENTRY_BLOCKED [{}] OBSERVATION_MODE is ON; refusing to "
              "place any live order.".format(_now_ist()))
        return {"ok": False, "opened": False, "error": "observation_mode"}

    # [NEEDS INPUT] guard: no configured size => cannot place (Req 9.27, 9.17).
    if POSITION_SIZE is None:
        print("LIVE_ENTRY_BLOCKED [{}] POSITION_SIZE is [NEEDS INPUT] (None); "
              "cannot size a live order — leaving position not-open "
              "(9.17).".format(_now_ist()))
        return {"ok": False, "opened": False, "error": "position_size_unset"}

    # Resolve the ATM/main strike (prefer the snapshot ATM; fall back to the
    # nearest grid strike to the index anchor).
    atm_strike = snapshot.get("atm_strike") if isinstance(snapshot, dict) \
        else None
    if atm_strike is None:
        atm_strike = helper._nearest_atm_strike(index_price, STRIKE_STEP)
    try:
        main_strike = int(atm_strike)
    except (TypeError, ValueError):
        print("LIVE_ENTRY_FAIL [{}] could not resolve ATM/main strike "
              "(atm={!r}); leaving position not-open (9.17).".format(
                  _now_ist(), atm_strike))
        return {"ok": False, "opened": False, "error": "atm_strike_unresolved"}

    hedge_shift = HEDGE_OFFSET_STRIKES * STRIKE_STEP

    # Direction -> credit spread + OTM protective hedge (Req 9.16).
    if position == "support_side" or bias == "BULL":
        option_type = "P"                       # credit PUT spread (bullish)
        hedge_strike = main_strike - hedge_shift  # further-OTM = LOWER put
        spread_kind = "credit_put_spread(bull)"
    elif position == "resistance_side" or bias == "BEAR":
        option_type = "C"                       # credit CALL spread (bearish)
        hedge_strike = main_strike + hedge_shift  # further-OTM = HIGHER call
        spread_kind = "credit_call_spread(bear)"
    else:
        print("LIVE_ENTRY_FAIL [{}] indeterminate direction (bias={!r}, "
              "position={!r}); leaving position not-open (9.17).".format(
                  _now_ist(), bias, position))
        return {"ok": False, "opened": False,
                "error": "indeterminate_direction"}

    if hedge_strike <= 0:
        print("LIVE_ENTRY_FAIL [{}] computed hedge strike {} is non-positive; "
              "leaving position not-open (9.17).".format(
                  _now_ist(), hedge_strike))
        return {"ok": False, "opened": False, "error": "invalid_hedge_strike"}

    # Build both option symbols for the current daily expiry.
    expiry_ddmmyy = helper.getDailyExpiry()
    try:
        main_symbol = helper.buildSymbol(
            product_type="option", underlying=UNDERLYING_ASSET,
            option_type=option_type, strike=main_strike,
            expiry_ddmmyy=expiry_ddmmyy)
        hedge_symbol = helper.buildSymbol(
            product_type="option", underlying=UNDERLYING_ASSET,
            option_type=option_type, strike=hedge_strike,
            expiry_ddmmyy=expiry_ddmmyy)
    except Exception as exc:  # noqa: BLE001 - bad symbol => not-open (9.17)
        print("LIVE_ENTRY_FAIL [{}] could not build option symbols ({}): "
              "{}: {} — leaving position not-open (9.17).".format(
                  _now_ist(), spread_kind, type(exc).__name__, exc))
        return {"ok": False, "opened": False, "error": "symbol_build_failed"}

    size = POSITION_SIZE
    print("LIVE_ENTRY [{}] {} size={} SELL main={} / BUY hedge={} "
          "(atm={}, hedge_offset={} strikes={} pts, expiry={}).".format(
              _now_ist(), spread_kind, size, main_symbol, hedge_symbol,
              main_strike, HEDGE_OFFSET_STRIKES, hedge_shift, expiry_ddmmyy))

    # --- Leg 1: SELL the main (credit) leg (Req 9.16). -------------------
    main_res = helper.placeOrder(
        main_symbol, "sell", size, order_type="market_order",
        client=client, papertrading=0)
    if not (isinstance(main_res, dict) and main_res.get("ok")):
        detail = main_res.get("error") if isinstance(main_res, dict) \
            else main_res
        print("LIVE_ENTRY_FAIL [{}] main SELL leg {} failed: {} — leaving "
              "position not-open (9.17).".format(
                  _now_ist(), main_symbol, detail))
        return {"ok": False, "opened": False, "error": "main_leg_failed",
                "detail": main_res}

    # --- Leg 2: BUY the OTM protective hedge leg (Req 9.16). -------------
    hedge_res = helper.placeOrder(
        hedge_symbol, "buy", size, order_type="market_order",
        client=client, papertrading=0)
    if not (isinstance(hedge_res, dict) and hedge_res.get("ok")):
        detail = hedge_res.get("error") if isinstance(hedge_res, dict) \
            else hedge_res
        # CRITICAL: main short is on but the protective hedge failed -> naked
        # short risk. Best-effort unwind of the main leg, then report not-open.
        print("LIVE_ENTRY_CRITICAL [{}] main SELL {} FILLED but protective "
              "hedge BUY {} FAILED ({}) — NAKED SHORT RISK; attempting to "
              "close the main leg.".format(
                  _now_ist(), main_symbol, hedge_symbol, detail))
        unwind = helper.placeOrder(
            main_symbol, "buy", size, order_type="market_order",
            client=client, papertrading=0)
        if isinstance(unwind, dict) and unwind.get("ok"):
            print("LIVE_ENTRY_RECOVER [{}] main leg {} closed after hedge "
                  "failure; position left not-open (9.17).".format(
                      _now_ist(), main_symbol))
        else:
            unwind_detail = unwind.get("error") if isinstance(unwind, dict) \
                else unwind
            print("LIVE_ENTRY_CRITICAL [{}] FAILED to close main leg {} after "
                  "hedge failure ({}) — MANUAL INTERVENTION REQUIRED; position "
                  "left not-open (9.17).".format(
                      _now_ist(), main_symbol, unwind_detail))
        return {"ok": False, "opened": False, "error": "hedge_leg_failed",
                "detail": hedge_res, "main_leg": main_res, "unwind": unwind}

    # --- Capture the entry credit for monitoring P&L (Req 9.18-9.22). ----
    # The spread's NET CREDIT (premium received per unit) is the P&L baseline
    # the monitor diffs the live spread cost against. placeOrder returns only an
    # order id (not a fill price), so the credit is APPROXIMATED from the leg
    # LTPs at entry time: entry_credit = main_leg_LTP - hedge_leg_LTP (main is
    # short/sold, hedge is long/bought further OTM, so the difference is the net
    # premium collected). This is a best-effort baseline — real fills may differ
    # by slippage; if either leg LTP is unavailable the credit is left None and
    # the monitor re-baselines from the first poll (and logs that limitation).
    main_entry_px = None
    hedge_entry_px = None
    try:
        main_entry_px = helper.manualLTP(main_symbol, client)
    except Exception as exc:  # noqa: BLE001 - baseline is best-effort
        print("LIVE_ENTRY_WARN [{}] could not read main leg {} entry LTP for "
              "credit baseline: {}: {}".format(
                  _now_ist(), main_symbol, type(exc).__name__, exc))
    try:
        hedge_entry_px = helper.manualLTP(hedge_symbol, client)
    except Exception as exc:  # noqa: BLE001 - baseline is best-effort
        print("LIVE_ENTRY_WARN [{}] could not read hedge leg {} entry LTP for "
              "credit baseline: {}: {}".format(
                  _now_ist(), hedge_symbol, type(exc).__name__, exc))
    entry_credit = None
    if main_entry_px is not None and hedge_entry_px is not None:
        entry_credit = float(main_entry_px) - float(hedge_entry_px)

    # --- Both legs placed: record the open position (Req 9.16). ----------
    open_position = {
        "spread_kind": spread_kind,
        "option_type": option_type,
        "main_symbol": main_symbol,
        "main_strike": main_strike,
        "main_side": "sell",
        "main_order_id": main_res.get("id"),
        "main_entry_price": main_entry_px,
        "hedge_symbol": hedge_symbol,
        "hedge_strike": hedge_strike,
        "hedge_side": "buy",
        "hedge_order_id": hedge_res.get("id"),
        "hedge_entry_price": hedge_entry_px,
        "entry_credit": entry_credit,
        "size": size,
        "bias": bias,
        "position": position,
        "expiry": expiry_ddmmyy,
        "opened_ts": _now_ist(),
    }
    print("LIVE_ENTRY_OK [{}] {} opened: SELL {} (id={}) / BUY {} (id={}) "
          "size={}.".format(
              _now_ist(), spread_kind, main_symbol, main_res.get("id"),
              hedge_symbol, hedge_res.get("id"), size))
    return {"ok": True, "opened": True, "open_position": open_position,
            "main_leg": main_res, "hedge_leg": hedge_res}


# ============================================================
# POSITION MONITORING + TRAILING (Req 9.18-9.22) — Task 14.6
# ============================================================
# While a credit spread is open, each poll fetches the two leg LTPs (9.18) and
# evaluates the target / stop-loss conditions on the spread's unrealized P&L,
# using a consecutive-poll confirmation counter for each (9.19, 9.20). On a
# confirmed target/SL the spread is closed (live) or a WOULD_HAVE_EXITED line is
# logged (observation); a failed live exit is logged and the position is RETAINED
# open pending a retry on the next poll (9.21). When trailing is enabled and the
# unrealized profit reaches TRAIL_TRIGGER_TARGET_FRACTION of the target, the stop
# is moved to breakeven (9.22).
#
# P&L / points interpretation (documented mapping)
# ------------------------------------------------
# The position is a CREDIT spread: the main leg is SOLD (short) and the hedge leg
# is BOUGHT (long, further OTM). Entry collects a net credit per unit:
#     entry_credit      = main_entry_price - hedge_entry_price      (premium in)
# To CLOSE the spread you buy back the main leg (pay its LTP) and sell the hedge
# leg (receive its LTP), so the current cost to close per unit is:
#     current_spread_cost = main_ltp - hedge_ltp
# Unrealized profit, expressed in option-PREMIUM POINTS per unit, is therefore:
#     unrealized_points = entry_credit - current_spread_cost
# and the currency P&L is that scaled by the contract size:
#     unrealized_pnl    = unrealized_points * size
# TARGET_POINTS and STOP_LOSS_POINTS are read as PREMIUM POINTS per unit (the
# same units as unrealized_points), so:
#     target hit  <=>  unrealized_points >= TARGET_POINTS      (profit target)
#     SL hit      <=>  unrealized_points <= -STOP_LOSS_POINTS  (loss stop)
# When the stop has been trailed to breakeven the effective SL level becomes the
# entry credit (0 points), i.e. SL hit <=> unrealized_points <= 0.


def _reset_monitor_state(state):
    """Clear the per-position monitoring counters/flags in ``state`` (9.19-9.22).

    Called when a position closes (or when there is nothing to monitor) so the
    next position starts its target/SL confirmation streaks and trail flag from
    scratch, mirroring the entry-confirm reset after an entry opens.
    """
    if not isinstance(state, dict):
        return
    state["tgt_confirm_count"] = 0
    state["sl_confirm_count"] = 0
    state["sl_at_breakeven"] = False


def _safe_leg_ltp(symbol, client):
    """Read one leg's LTP, returning None (never raising) on any failure (9.18).

    A transient price hiccup must not crash the monitor loop; a None leg price is
    logged by the caller and simply skips the SL/target evaluation for that poll
    so the position is re-checked on the next poll.
    """
    try:
        return helper.manualLTP(symbol, client)
    except Exception as exc:  # noqa: BLE001 - monitor must survive price errors
        print("MONITOR_WARN [{}] leg LTP unavailable for {}: {}: {}".format(
            _now_ist(), symbol, type(exc).__name__, exc))
        return None


def _exit_open_position(client, open_position, reason):
    """Close the open credit spread leg-by-leg through Delta_Helper (9.19/9.20).

    Closing a credit spread reverses both legs: BUY back the short main leg and
    SELL the long hedge leg (market orders via ``helper.placeOrder``). Returns a
    structured dict so the caller can branch on success without string parsing:

      * success: ``{"ok": True, "main_close": <res>, "hedge_close": <res>}``
      * failure: ``{"ok": False, "error": "<reason>", ...}`` — on a missing size
        or when EITHER leg's close order fails; the caller then RETAINS the
        position open for a retry (9.21). Any leg that DID close is reported so
        the retry does not double-close it blindly.
    """
    main_symbol = open_position.get("main_symbol")
    hedge_symbol = open_position.get("hedge_symbol")
    size = open_position.get("size") or POSITION_SIZE
    if size is None:
        return {"ok": False, "error": "position_size_unset"}

    # Reverse the main (short) leg: BUY to close.
    main_close = helper.placeOrder(
        main_symbol, "buy", size, order_type="market_order",
        client=client, papertrading=0)
    main_ok = isinstance(main_close, dict) and main_close.get("ok")

    # Reverse the hedge (long) leg: SELL to close.
    hedge_close = helper.placeOrder(
        hedge_symbol, "sell", size, order_type="market_order",
        client=client, papertrading=0)
    hedge_ok = isinstance(hedge_close, dict) and hedge_close.get("ok")

    if main_ok and hedge_ok:
        return {"ok": True, "main_close": main_close, "hedge_close": hedge_close}

    return {
        "ok": False,
        "error": "exit_leg_failed",
        "main_ok": bool(main_ok),
        "hedge_ok": bool(hedge_ok),
        "main_close": main_close,
        "hedge_close": hedge_close,
    }


def monitor_open_position(client, state):
    """Run ONE monitoring poll for the open credit spread (Req 9.18-9.22).

    Designed to be called once per cycle (the simple, robust choice for this
    strategy's cycle model) OR inside a tighter poll loop; each call is a single
    self-contained poll. Finer per-``LTP_POLL_INTERVAL`` polling can be layered
    on later by calling this repeatedly within a cycle — documented as a future
    refinement so the main loop is never blocked for long.

    Steps:
      1. Poll both leg LTPs via ``helper.manualLTP`` (9.18).
      2. Compute the spread's unrealized profit in premium points and currency
         (see the module P&L/points mapping above). The entry credit is read
         from ``open_position["entry_credit"]``; if it is missing (e.g. the
         entry-time LTP read failed) it is RE-BASELINED to the current spread
         cost on this first poll and the limitation is logged.
      3. Trailing (9.22): when ``TRAILING_ENABLED`` and ``TARGET_POINTS`` is set,
         if unrealized profit >= ``TRAIL_TRIGGER_TARGET_FRACTION * TARGET_POINTS``
         move the stop to breakeven (``state["sl_at_breakeven"] = True``); the
         effective SL level used below then becomes breakeven (0 points).
      4. Confirmation counters (9.19, 9.20): increment ``tgt_confirm_count`` when
         the target condition holds this poll (reset to 0 otherwise) and
         ``sl_confirm_count`` when the SL condition holds (reset otherwise).
      5. Exit (9.19, 9.20): when ``tgt_confirm_count >= TGT_CONFIRM_TICKS`` (or
         ``sl_confirm_count >= SL_CONFIRM_TICKS``) close the spread. In
         OBSERVATION_MODE log a WOULD_HAVE_EXITED decision and place NO orders;
         in live mode close via ``_exit_open_position`` — on success record the
         outcome and mark the position closed, on failure log and RETAIN the
         position open for the next poll (9.21).

    ``[NEEDS INPUT]`` handling: when ``TARGET_POINTS`` or ``STOP_LOSS_POINTS`` is
    None the concrete target/SL cannot be evaluated, so the poll LOGS the
    unrealized P&L and that target/SL (and trailing, which needs the target)
    cannot be evaluated, and takes NO action — matching observation-period
    behavior until the risk params are finalized (Req 9.27).

    Mutates ``state`` in place (counters, ``sl_at_breakeven``, and on a
    completed exit ``position_open``/``open_position``/``last_exit``) and returns
    a summary dict describing the poll.
    """
    state = state if isinstance(state, dict) else {}
    open_position = state.get("open_position")
    if not isinstance(open_position, dict):
        # Nothing to monitor — keep the counters clean and report idle.
        _reset_monitor_state(state)
        return {"monitored": False, "reason": "no_open_position"}

    main_symbol = open_position.get("main_symbol")
    hedge_symbol = open_position.get("hedge_symbol")
    size = open_position.get("size") or POSITION_SIZE

    # 1. Poll both leg LTPs (9.18).
    main_ltp = _safe_leg_ltp(main_symbol, client)
    hedge_ltp = _safe_leg_ltp(hedge_symbol, client)

    if main_ltp is None or hedge_ltp is None:
        # Cannot value the spread this poll; log and re-check next poll. Do NOT
        # disturb the confirmation streaks on a pure data gap.
        print("MONITOR [{ts}] {kind} main={ms}@{mp} hedge={hs}@{hp} "
              "unrealized=N/A (leg price unavailable; skipping SL/target this "
              "poll) tgt_confirm={tc}/{tt} sl_confirm={sc}/{st}".format(
                  ts=_now_ist(), kind=open_position.get("spread_kind"),
                  ms=main_symbol, mp=_fmt(main_ltp),
                  hs=hedge_symbol, hp=_fmt(hedge_ltp),
                  tc=state.get("tgt_confirm_count", 0), tt=TGT_CONFIRM_TICKS,
                  sc=state.get("sl_confirm_count", 0), st=SL_CONFIRM_TICKS))
        return {"monitored": True, "priced": False,
                "main_ltp": main_ltp, "hedge_ltp": hedge_ltp}

    # 2. Unrealized P&L in premium points (per unit) and currency.
    current_spread_cost = float(main_ltp) - float(hedge_ltp)
    entry_credit = open_position.get("entry_credit")
    rebaselined = False
    if entry_credit is None:
        # Entry-time credit was unavailable: re-baseline to the current spread
        # cost on this first poll (approximation) and note the limitation.
        entry_credit = current_spread_cost
        open_position["entry_credit"] = entry_credit
        rebaselined = True
        print("MONITOR_NOTE [{}] entry_credit was unavailable; re-baselining to "
              "current spread cost {} (approximation — P&L is measured relative "
              "to this poll, not the true fill).".format(
                  _now_ist(), _fmt(entry_credit)))

    unrealized_points = float(entry_credit) - current_spread_cost
    unrealized_pnl = (unrealized_points * size) if size is not None else None

    # 3. Trailing to breakeven (9.22) — needs a configured target.
    trail_action = "none"
    already_breakeven = bool(state.get("sl_at_breakeven"))
    if (TRAILING_ENABLED and TARGET_POINTS is not None and not already_breakeven
            and unrealized_points >= TRAIL_TRIGGER_TARGET_FRACTION
            * float(TARGET_POINTS)):
        if OBSERVATION_MODE:
            trail_action = "would_trail_to_breakeven"
            print("MONITOR_TRAIL [{}] WOULD_TRAIL_TO_BREAKEVEN: unrealized "
                  "{} pts >= {} * target {} = {} pts (OBSERVATION_MODE; SL not "
                  "actually moved).".format(
                      _now_ist(), _fmt(unrealized_points),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION),
                      _fmt(TARGET_POINTS),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION * float(TARGET_POINTS))))
        else:
            state["sl_at_breakeven"] = True
            already_breakeven = True
            trail_action = "trailed_to_breakeven"
            print("MONITOR_TRAIL [{}] SL moved to BREAKEVEN: unrealized {} pts "
                  ">= {} * target {} = {} pts (effective SL now breakeven, "
                  "0 pts).".format(
                      _now_ist(), _fmt(unrealized_points),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION),
                      _fmt(TARGET_POINTS),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION * float(TARGET_POINTS))))

    # ----- [NEEDS INPUT] guard: no concrete target/SL to evaluate --------
    if TARGET_POINTS is None or STOP_LOSS_POINTS is None:
        unset = [n for n, v in (("TARGET_POINTS", TARGET_POINTS),
                                ("STOP_LOSS_POINTS", STOP_LOSS_POINTS))
                 if v is None]
        print("MONITOR [{ts}] {kind} main={ms}@{mp} hedge={hs}@{hp} "
              "spread_cost={cost} entry_credit={ec}{rb} unrealized={up} pts "
              "(pnl={pnl}) — target/SL NOT evaluated: {unset} [NEEDS INPUT] "
              "(Req 9.27); no action.".format(
                  ts=_now_ist(), kind=open_position.get("spread_kind"),
                  ms=main_symbol, mp=_fmt(main_ltp),
                  hs=hedge_symbol, hp=_fmt(hedge_ltp),
                  cost=_fmt(current_spread_cost), ec=_fmt(entry_credit),
                  rb=" (rebaselined)" if rebaselined else "",
                  up=_fmt(unrealized_points), pnl=_fmt(unrealized_pnl),
                  unset=",".join(unset)))
        return {"monitored": True, "priced": True, "actionable": False,
                "unrealized_points": unrealized_points,
                "unrealized_pnl": unrealized_pnl,
                "trail_action": trail_action, "unset_params": unset}

    # 4. Target / SL conditions + consecutive-poll confirmation (9.19, 9.20).
    target_hit = unrealized_points >= float(TARGET_POINTS)
    if already_breakeven:
        # Trailed stop: breakeven is the effective SL (0 points).
        sl_hit = unrealized_points <= 0.0
        sl_level_desc = "breakeven(0)"
    else:
        sl_hit = unrealized_points <= -float(STOP_LOSS_POINTS)
        sl_level_desc = "-{}".format(_fmt(STOP_LOSS_POINTS))

    tgt_count = (state.get("tgt_confirm_count", 0) or 0) + 1 if target_hit else 0
    sl_count = (state.get("sl_confirm_count", 0) or 0) + 1 if sl_hit else 0
    state["tgt_confirm_count"] = tgt_count
    state["sl_confirm_count"] = sl_count

    print("MONITOR [{ts}] {kind} main={ms}@{mp} hedge={hs}@{hp} "
          "spread_cost={cost} entry_credit={ec}{rb} unrealized={up} pts "
          "(pnl={pnl}) target={tp} sl={sl} tgt_confirm={tc}/{tt} "
          "sl_confirm={sc}/{st} trail={trail}".format(
              ts=_now_ist(), kind=open_position.get("spread_kind"),
              ms=main_symbol, mp=_fmt(main_ltp),
              hs=hedge_symbol, hp=_fmt(hedge_ltp),
              cost=_fmt(current_spread_cost), ec=_fmt(entry_credit),
              rb=" (rebaselined)" if rebaselined else "",
              up=_fmt(unrealized_points), pnl=_fmt(unrealized_pnl),
              tp=_fmt(TARGET_POINTS), sl=sl_level_desc,
              tc=tgt_count, tt=TGT_CONFIRM_TICKS,
              sc=sl_count, st=SL_CONFIRM_TICKS,
              trail="breakeven" if already_breakeven else trail_action))

    # 5. Exit on a confirmed target / SL (9.19, 9.20). Target takes precedence
    #    when both somehow confirm on the same poll (a realized profit exit is
    #    preferred over a stop exit).
    exit_reason = None
    if tgt_count >= TGT_CONFIRM_TICKS:
        exit_reason = "target"
    elif sl_count >= SL_CONFIRM_TICKS:
        exit_reason = "stop_loss"

    if exit_reason is None:
        return {"monitored": True, "priced": True, "actionable": True,
                "exit": False, "unrealized_points": unrealized_points,
                "unrealized_pnl": unrealized_pnl,
                "tgt_confirm_count": tgt_count, "sl_confirm_count": sl_count,
                "trail_action": trail_action}

    # ----- OBSERVATION mode: log the would-have-exit; place NO orders -----
    if OBSERVATION_MODE:
        print("MONITOR_EXIT [{}] WOULD_HAVE_EXITED reason={} unrealized={} pts "
              "(pnl={}) confirmed tgt={}/{} sl={}/{} | OBSERVATION_MODE "
              "(no exit order placed).".format(
                  _now_ist(), exit_reason, _fmt(unrealized_points),
                  _fmt(unrealized_pnl), tgt_count, TGT_CONFIRM_TICKS,
                  sl_count, SL_CONFIRM_TICKS))
        return {"monitored": True, "priced": True, "actionable": True,
                "exit": "would_have_exited", "reason": exit_reason,
                "unrealized_points": unrealized_points,
                "unrealized_pnl": unrealized_pnl}

    # ----- LIVE mode: close the spread; retain on failure (9.19-9.21) -----
    try:
        result = _exit_open_position(client, open_position, exit_reason)
    except Exception as exc:  # noqa: BLE001 - never crash the monitor loop
        result = {"ok": False, "error": "{}: {}".format(
            type(exc).__name__, exc)}
        print("MONITOR_EXIT_FAIL [{}] unexpected error exiting position "
              "(reason={}): {} — RETAINING position open for retry "
              "(9.21).".format(_now_ist(), exit_reason, result["error"]))

    if result.get("ok"):
        # Record the outcome and mark the position closed (9.19, 9.20).
        last_exit = {
            "reason": exit_reason,
            "unrealized_points": unrealized_points,
            "unrealized_pnl": unrealized_pnl,
            "main_symbol": main_symbol,
            "hedge_symbol": hedge_symbol,
            "size": size,
            "closed_ts": _now_ist(),
        }
        state["last_exit"] = last_exit
        state["position_open"] = False
        state["open_position"] = None
        _reset_monitor_state(state)
        print("MONITOR_EXIT_OK [{}] EXITED reason={} unrealized={} pts "
              "(pnl={}); position recorded closed.".format(
                  _now_ist(), exit_reason, _fmt(unrealized_points),
                  _fmt(unrealized_pnl)))
        return {"monitored": True, "priced": True, "actionable": True,
                "exit": "closed", "reason": exit_reason,
                "unrealized_points": unrealized_points,
                "unrealized_pnl": unrealized_pnl, "detail": result}

    # Exit failed: log and RETAIN the position open for a retry (9.21). Do NOT
    # touch position_open/open_position or reset the confirm streak, so the next
    # poll immediately re-attempts the exit while the condition still holds.
    print("MONITOR_EXIT_FAIL [{}] exit (reason={}) FAILED: {} — RETAINING "
          "position open pending retry (9.21).".format(
              _now_ist(), exit_reason,
              result.get("error") if isinstance(result, dict) else result))
    return {"monitored": True, "priced": True, "actionable": True,
            "exit": "failed_retained", "reason": exit_reason,
            "unrealized_points": unrealized_points,
            "unrealized_pnl": unrealized_pnl, "detail": result}


# ============================================================
# STARTUP (Req 9.1, 9.2) + ENTRYPOINT
# ============================================================
def startup():
    """
    Establish the Delta client and log the initialized configuration BEFORE any
    entry conditions are evaluated (Req 9.1), stopping cleanly if the client
    cannot be connected (Req 9.2).

    The client is obtained via ``require_delta()`` which surfaces the named
    ``CredentialLoadError`` (naming the exact credential file to supply) when no
    validated credential is available, and any other error is treated as a
    connection failure. On ANY failure this logs an error identifying the
    connection failure and returns ``None`` — the caller (``run``) then STOPS
    without evaluating entries and places NO orders (Req 9.2). On success the
    initialized configuration is logged and the connected client is returned.
    """
    print("=" * 60)
    print("STARTUP [{ts}] Strategy_BTC_Options starting...".format(
        ts=_now_ist()))

    # --- Establish the client connection FIRST (Req 9.1, 9.2) -------------
    try:
        client = require_delta()
    except CredentialLoadError as exc:
        # Named credential-load failure: the message identifies the exact
        # credential file the operator must supply.
        print("STARTUP_ERROR [{ts}] Delta client connection could NOT be "
              "established (credential load failure): {msg}".format(
                  ts=_now_ist(), msg=exc))
        if getattr(exc, "expected_file", None):
            print("STARTUP_ERROR expected credential file: {}".format(
                exc.expected_file))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None
    except Exception as exc:  # noqa: BLE001 - any connection failure aborts
        print("STARTUP_ERROR [{ts}] Delta client connection could NOT be "
              "established: {typ}: {msg}".format(
                  ts=_now_ist(), typ=type(exc).__name__, msg=exc))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None

    if client is None:
        # Defensive: require_delta returned no client without raising.
        print("STARTUP_ERROR [{ts}] Delta client is unavailable (no client "
              "constructed).".format(ts=_now_ist()))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None

    # --- Initialize + log the configuration (Req 9.1, 9.27) ---------------
    print("STARTUP [{ts}] Delta client connection established.".format(
        ts=_now_ist()))
    print("STARTUP config:")
    print("  instrument           = {}".format(INSTRUMENT_LABEL))
    print("  underlying_asset     = {}".format(UNDERLYING_ASSET))
    print("  index_anchor         = {}".format(INDEX_ANCHOR_SYMBOL))
    print("  perpetual (log-only) = {}".format(PERP_SYMBOL))
    print("  timeframe_minutes    = {}".format(TIMEFRAME_MINUTES))
    print("  strike_step          = {}".format(STRIKE_STEP))
    print("  sr_band_width        = {}".format(SR_BAND_WIDTH))
    print("  no_trade_buffer      = {}".format(NO_TRADE_ZONE_BUFFER))
    print("  sl_confirm_ticks     = {}".format(SL_CONFIRM_TICKS))
    print("  tgt_confirm_ticks    = {}".format(TGT_CONFIRM_TICKS))
    print("  ltp_poll_interval    = {}s".format(LTP_POLL_INTERVAL))
    print("  trail_trigger_frac   = {}".format(TRAIL_TRIGGER_TARGET_FRACTION))
    print("  always_credit        = {}".format(ALWAYS_CREDIT))
    print("  pre_expiry_lead_min  = {}".format(PRE_EXPIRY_LEAD_MINUTES))
    print("  reference_timezone   = {}".format(REFERENCE_TIMEZONE_NAME))
    print("  OBSERVATION_MODE     = {}".format(OBSERVATION_MODE))

    # Risk params are [NEEDS INPUT] module constants finalized after the
    # observation period; explicitly log which remain unset (Req 9.27).
    risk_params = {
        "PCR_BULL_THRESHOLD": PCR_BULL_THRESHOLD,
        "PCR_BEAR_THRESHOLD": PCR_BEAR_THRESHOLD,
        "STOP_LOSS_POINTS": STOP_LOSS_POINTS,
        "TARGET_POINTS": TARGET_POINTS,
        "POSITION_SIZE": POSITION_SIZE,
        "MAX_TRADES_PER_DAY": MAX_TRADES_PER_DAY,
    }
    print("STARTUP risk params (concrete live values are [NEEDS INPUT], "
          "Req 9.27):")
    for name, value in risk_params.items():
        state = "[NEEDS INPUT]" if value is None else ""
        print("  {:<20} = {} {}".format(name, value, state).rstrip())

    return client


def run():
    """
    Strategy entrypoint (Task 14.1 scope: startup + observation banner).

    Establishes the client and initializes config via ``startup`` (Req 9.1); if
    no client is returned the run STOPS immediately without evaluating any entry
    conditions and without placing orders (Req 9.2). On success it prints the
    OBSERVATION_MODE banner making clear no live orders will be placed (Req 9.3,
    9.4). The signal-cycle evaluation loop (which calls the logging surface
    above each cycle) is driven by Task 14.2 — this function deliberately does
    not implement that loop, and NO orders are placed anywhere here.
    """
    client = startup()
    if client is None:
        # Startup already logged the connection failure and abort reason
        # (Req 9.2). Stop without evaluating entries / placing orders.
        return

    print("=" * 60)
    if OBSERVATION_MODE:
        print("OBSERVATION_MODE is ON: the strategy will LOG signals, the PCR "
              "table, the option-chain snapshot, support/resistance levels, "
              "and WOULD_HAVE_ENTERED decisions, and will place NO live orders "
              "(Req 9.3, 9.4).")
    else:
        print("OBSERVATION_MODE is OFF: live orders would route through "
              "helper_delta once entry conditions are confirmed (Req 9.5). "
              "The live-entry path is implemented in Task 14.3.")
    print("=" * 60)

    # ---- Observation-mode signal-cycle loop (Task 14.2) ------------------
    # Run the per-cycle signal evaluation every SIGNAL_INTERVAL_SEC, carrying
    # the previous cycle's snapshot/PCR/trend so the same-strike OI delta
    # (Req 9.8) and consecutive PCR trend (Req 9.10) diff against the prior
    # cycle. Each cycle is wrapped so a transient error (e.g. a chain fetch
    # hiccup) is logged and the loop continues rather than crashing; a
    # KeyboardInterrupt stops the loop cleanly. Observation-only: no orders.
    print("SIGNAL_LOOP [{ts}] starting observation cycles every {n}s "
          "(no orders will be placed).".format(
              ts=_now_ist(), n=SIGNAL_INTERVAL_SEC))
    state = {}
    try:
        while True:
            try:
                state = checkCriteriaAndTakeTrade(client, state)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print("SIGNAL_ERROR [{ts}] signal cycle failed, continuing: "
                      "{typ}: {msg}".format(
                          ts=_now_ist(), typ=type(exc).__name__, msg=exc))
            time.sleep(SIGNAL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("SIGNAL_LOOP [{ts}] stopped by operator (KeyboardInterrupt); "
              "no orders were placed.".format(ts=_now_ist()))

    return client


if __name__ == "__main__":
    run()
