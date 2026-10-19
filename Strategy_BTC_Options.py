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

import os
import sys
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

# S/R line granularity: the single support/resistance line is the nearest
# multiple of this step to the spot (Sensex-style nearest-level model). Matches
# the Sensex 500-point S/R grid (get_support_resistance step=500). e.g. spot
# 81800 -> line 82000; spot 81400 -> line 81500. NOTE: BTC option strikes are on
# a 200-point grid (STRIKE_STEP), and 500 is not a multiple of 200, so the x500
# lines (81500, 82500, ...) are never tradeable strikes. OI plays NO part in
# choosing WHICH 500 level is the S/R line. But when the chosen line carries no
# OI — unlisted, or listed with zero OI — ``resolve_sr_strike`` substitutes the
# listed strike NEAREST THE SPOT that does carry OI, and role / no-trade / bias
# are then measured against that strike instead.
SR_LINE_STEP = 500

# Consecutive-trend window for the OI-PCR lists (avgOiPcrList2 / avgOiPcr9List2).
# Mirrors the Sensex strategy: a PCR increase/decrease is only confirmed over
# this many consecutive values (3). The window resets to the newest value on an
# ATM-strike shift, slides forward (dropping the oldest) when the trend is not
# monotonic, and is capped at this length so a long 24/7 session can't grow it.
PCR_TREND_WINDOW = 3

# Rolling window for the FULL-CHAIN (total) PCR series — kept at 3 to match the
# 9/17-strike PCRs: a strictly rising/falling run over the latest 3 values sets
# isTotalPcrInc / isTotalPcrDecr. The total PCR has no ATM dependency (it sums
# every listed strike), so its window never needs resetting. Observation-only.
TOTAL_PCR_WINDOW = 3

# Minimum per-step move (in %) for a PCR inc/dec run to be considered
# ECONOMICALLY meaningful rather than numerical drift. The base isPcrInc /
# isPcrDecr flags use a STRICT comparison, so a 4th-decimal wiggle (e.g.
# 0.8643 -> 0.8641, a -0.02% step) technically qualifies as "falling". The
# parallel `..._MinMove` flags additionally require EVERY step to exceed this
# threshold, filtering that noise. Observation-only for now — tune once the
# logs show which magnitude actually precedes a real move.
PCR_MIN_STEP_PCT = 0.1

# No-trade dead zone around the S/R level (Req 9.14). Set to 0 = DISABLED: there
# is no dead band near the S/R line any more, so price is always classified as
# either side of the level (spot below => RESISTANCE / bear side, spot above =>
# SUPPORT / bull side). Only an exact spot == level tick reports AT_LINE.
#
# Why 0: the level can be substituted by the listed strike nearest spot when the
# 500 line carries no OI (``resolve_sr_strike``), and that strike is always
# within one strike step (200) of spot — so any non-trivial buffer around it
# would veto a large share of cycles purely as an artefact of the strike grid.
# Raise this above 0 to re-enable the dead band.
NO_TRADE_ZONE_BUFFER = 0

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

# Nominal signal-cycle cadence in seconds (one TIMEFRAME_MINUTES candle). The
# observation loop no longer sleeps a fixed interval from an arbitrary start —
# it aligns each cycle to the UTC candle close (:00, :03, :06, ...) via
# `_seconds_until_next_candle_close`, so a cycle fires as each candle closes
# (mirrors the Sensex `minute % timeFrame == 0` trigger). This constant is kept
# as the nominal period for reference/logging and as the natural fallback cadence.
SIGNAL_INTERVAL_SEC = TIMEFRAME_MINUTES * 60

# Per-contract dated logging (Option C): tee all stdout to a dated log file that
# ROLLS at the 12:00 UTC daily-expiry boundary (17:30 IST), so each BTC contract
# day gets its own file — logs/delta_btc_<expiry YYYY-MM-DD>.log — instead of a
# calendar-date file that would split one contract across two files. Set
# LOG_TO_FILE = False to log to console only.
LOG_TO_FILE = True
LOG_DIR = "logs"

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
# ---- Sensex-equivalent entry criteria (ported 1:1 from Strategy_Sensex) ------
# Percentage thresholds are kept EXACTLY as the Sensex strategy uses them. The
# whole strategy is still OBSERVATION_MODE, so these are tuning knobs for later.
CHOI_MORNING_PCT = 15       # LOGIC1: cumulative-CHOI gap must be >= this (%)
CHOI_CONVERGE_PCT = 10      # LOGIC2: cumulative-CHOI gap must be >= this (%)
OI_CONVERGE_PCT = 2         # LOGIC2: total-OI gap must be >= this (%)
CHOI_TRAP_PCT = 15          # LOGIC3: cumulative-CHOI gap must be >= this (%)

# LOGIC3 is ALWAYS observation-only and never places an order, exactly as in the
# Sensex strategy (there it logs LOGIC3_OBSERVE). Only LOGIC1/LOGIC2 are
# live-tradeable modes once OBSERVATION_MODE is disabled.
LOGIC3_OBSERVATION_ONLY = True

# Fresh-flow (per-cycle CHOI) dominance confirmation at the S/R strike.
# BULL needs PE freshly dominant AND not unwinding; BEAR mirrors with CE.
USE_CHOI_DOMINANCE_GATE = True

# The directional PCR gate uses the 17-strike INVARIANT-WINDOW track (every
# value re-measured on the CURRENT strike set, so an ATM shift cannot distort
# the comparison) and requires the rising/falling run to have held for at least
# this many consecutive cycles.
PCR_ENTRY_STREAK_MIN = 2

# "Morning window" equivalent (Sensex: 9:15-9:48 IST, the first ~11 candles after
# the open, when change-in-OI is still noisy/near-zero). For BTC the contract
# starts at 12:00 UTC / 17:30 IST and the cumulative-CHOI baseline resets exactly
# then, so the same near-zero problem applies for the first ~33 minutes:
# 17:30-18:03 IST == 12:00-12:33 UTC.
MORNING_WINDOW_START_UTC_MIN = 0    # minutes past 12:00 UTC
MORNING_WINDOW_END_UTC_MIN = 33

# No NEW entry inside the last ``ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY`` minutes
# before the contract expires (Sensex equivalent: dt1.hour <= 15, i.e. nothing
# near the close). BTC expires at 12:00 UTC / 17:30 IST, so 60 minutes means
# entries stop at 16:30 IST and the session keeps a one-hour runway.
#
# MUST be measured as MINUTES REMAINING UNTIL EXPIRY, never as a wall-clock
# comparison: the BTC contract session runs 17:30 IST -> 17:30 IST and therefore
# SPANS MIDNIGHT. A naive ``ist >= 16:30`` test wrongly flags the whole evening
# (17:30-23:59, the START of the session, ~23h from expiry) as "past the
# cutoff" — that bug blocked 130 of 476 cycles on 2026-10-18, including every
# LOGIC2 signal of the session.
ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY = 60

# Derived, for log labels only: 16:30 IST when the lead time is 60 min.
# (expiry 12:00 UTC = 720 min) - 60 lead + 330 (IST offset) = 990 min = 16:30.
_CUTOFF_IST_MIN = (DAILY_EXPIRY_UTC_HOUR * 60
                   - ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY + 330) % (24 * 60)
ENTRY_CUTOFF_IST_HOUR = _CUTOFF_IST_MIN // 60
ENTRY_CUTOFF_IST_MINUTE = _CUTOFF_IST_MIN % 60

# Daily (per-CONTRACT-session) untrailed-stop-loss halt, Sensex `slCount != 2`.
# Only an UNTRAILED SL counts; a trailed exit sitting at breakeven is a scratch.
# Resets at the 12:00 UTC / 17:30 IST contract rollover with the rest of the
# per-contract state.
MAX_SL_PER_DAY = 2

# PCR_BULL/BEAR_THRESHOLD are vestigial — the Sensex port gates direction on the
# 17-strike invariant streak, not a PCR cutoff. Kept as None so the startup
# banner still reports them; nothing reads them for a decision.
PCR_BULL_THRESHOLD = None
PCR_BEAR_THRESHOLD = None

# STOP_LOSS_POINTS / TARGET_POINTS / POSITION_SIZE are now computed PER TRADE
# (Sensex-style: SL/target from the short-leg candle-range median × multiplier,
# size from risk-based lots). They stay None as module-level defaults — the live
# monitor reads the per-trade values off the open_position dict, and the sizing
# comes from calc_lots_by_risk. MAX_TRADES_PER_DAY is likewise superseded by the
# session rule (win => stop; loss => re-enter up to MAX_SL_PER_DAY).
STOP_LOSS_POINTS = None
TARGET_POINTS = None
POSITION_SIZE = None
MAX_TRADES_PER_DAY = None

# ---- Sensex-style risk sizing + SL/target (ported; concrete live values) -----
# Per the user: risk ~2000 INR (~$20) per trade, SL multiplier 2.5, target 4.0.
FIXED_RISK_PER_TRADE = 20.0       # USD risked if the SL is hit (net of hedge)
SL_MULTIPLIER = 2.5               # effective_sl  = median_range * this
TARGET_MULTIPLIER = 4.0           # effective_tgt = median_range * this

# Delta BTC-option contract spec (from the product catalog): each contract is
# contract_value BTC, vanilla (quoted/settled in USD). The USD P&L for a move of
# N premium-points on L contracts is  N * CONTRACT_VALUE * L  — this is the
# Sensex LOT_SIZE (Rs 20/pt) equivalent. ASSUMPTION pending a live-fill check.
CONTRACT_VALUE = 0.001            # BTC per contract (Delta spec)

# Lot bounds + rounding (Sensex-equivalent). MIN keeps a trade placeable; MAX is
# a safety ceiling; the fractional-lot round-up recovers size lost to flooring.
MIN_LOTS = 1
MAX_LOTS = 10000
LOT_ROUNDUP_FRACTION = 0.75

# Hedge-offset ratio bounds: the discount applied to the short-leg SL for the
# hedge's share of the loss. Clamped so a noisy ratio can't collapse net SL to
# ~0 and over-size the position (mirrors Sensex HEDGE_OFFSET_RATIO_MIN/MAX).
HEDGE_OFFSET_RATIO_MIN = 0.10
HEDGE_OFFSET_RATIO_MAX = 0.75

# Delta-based hedge selection: target hedge |delta| ~= main|delta| * this, on
# the OTM side, snapped to the 200 strike grid (Sensex uses /2 on a 500 grid).
HEDGE_TARGET_DELTA_FRACTION = 0.5

# Option candle-range median (drives SL/target). Last N 3-min HIGH-LOW ranges;
# low-sample guard halves a thin-window median that is implausibly large (early
# in a fresh contract only). Mirrors Sensex get_option_candle_range.
OPT_RANGE_CANDLES = 10
OPT_RANGE_MIN_MEDIAN_CANDLES = 4
OPT_RANGE_LOW_SAMPLE_FACTOR = 0.5

# Fallback when a freshly-listed option has no usable candle history yet: size
# SL/target as a fraction of the entry premium instead of the candle median.
SL_PREMIUM_FALLBACK_FRACTION = 0.40   # effective_sl  = entry_premium * this
TGT_PREMIUM_FALLBACK_FRACTION = 0.64  # effective_tgt = entry_premium * this (keeps 2.5:4.0)


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


def _seconds_until_next_candle_close(interval_min):
    """
    Seconds to wait until the next ``interval_min``-minute candle boundary on
    the UTC wall clock.

    BTC/crypto candles are UTC-aligned, so a 3-minute candle closes at :00, :03,
    :06, ... of every hour. Sleeping until the boundary makes each signal cycle
    fire right as a candle closes and evaluate the just-closed candle — mirroring
    the Sensex bot's ``dt1.minute % timeFrame == 0`` candle-close trigger instead
    of drifting from an arbitrary start time.

    Alignment is computed from ``datetime.now(REFERENCE_TIMEZONE)`` (UTC); the
    returned wait is always strictly positive (when called exactly on a boundary
    it rolls forward to the NEXT boundary so the same candle is never processed
    twice). For a 3-minute timeframe the UTC and IST boundaries coincide (the
    +5:30 IST offset is a whole multiple of 3 minutes), so the displayed IST
    timestamps still land on clean :00/:03/:06 marks.
    """
    now = datetime.now(REFERENCE_TIMEZONE)
    period = interval_min * 60
    secs_into_period = ((now.minute % interval_min) * 60
                        + now.second
                        + now.microsecond / 1_000_000.0)
    remaining = period - secs_into_period
    if remaining <= 0.05:
        remaining += period
    return remaining


# ============================================================
# PER-CONTRACT DATED LOGGING (Option C) — rolls at 12:00 UTC / 17:30 IST
# ============================================================
# All strategy output is print()-based; rather than touch every log call we tee
# sys.stdout to a per-contract log file. The file is labelled by the CURRENT
# daily-expiry date (``helper.getDailyExpiry`` rolls at >= 12:00 UTC), so a fresh
# file opens exactly when a new BTC contract begins (17:30 IST) and each file
# holds one full 24h contract session. ``roll_contract_log`` is called once per
# cycle (cheap) to switch files at the boundary; ``_Tee.write`` just mirrors each
# line to whatever file is currently open (no per-write date math). All of this
# is fully guarded — any file error degrades to console-only, never crashing the
# strategy.
_log_fh = None
_log_label = None


def _current_contract_label():
    """Current BTC contract's expiry date as ``YYYY-MM-DD`` (rolls at 12:00 UTC
    via ``helper.getDailyExpiry``'s DDMMYY), used as the log-file label."""
    ddmmyy = helper.getDailyExpiry()
    return "20{}-{}-{}".format(ddmmyy[4:6], ddmmyy[2:4], ddmmyy[0:2])


def roll_contract_log():
    """
    Open or rotate the per-contract log file. The file rolls at the 12:00 UTC
    (17:30 IST) contract boundary — when the daily expiry date changes a fresh
    ``logs/delta_btc_<expiry>.log`` is opened and the previous one closed. Safe
    no-op when ``LOG_TO_FILE`` is off; degrades to console-only on any error.
    """
    global _log_fh, _log_label
    if not LOG_TO_FILE:
        return
    label = _current_contract_label()
    if label == _log_label and _log_fh is not None:
        return
    if _log_fh is not None:
        try:
            _log_fh.flush()
            _log_fh.close()
        except Exception:  # noqa: BLE001 - logging must never crash the bot
            pass
        _log_fh = None
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, "delta_btc_{}.log".format(label))
        _log_fh = open(path, "a", encoding="utf-8")
        _log_label = label
        _log_fh.write("==== CONTRACT LOG {} opened {} (rolls at 12:00 UTC / "
                      "17:30 IST) ====\n".format(label, _now_ist()))
        _log_fh.flush()
    except Exception as exc:  # noqa: BLE001
        _log_fh = None
        try:
            sys.__stdout__.write(
                "LOG_FILE_ERROR: could not open log for {}: {}\n".format(
                    label, exc))
        except Exception:  # noqa: BLE001
            pass


class _Tee(object):
    """Mirror writes to the original stream AND the current contract log file.
    Reads the module-level ``_log_fh`` at write time so file rotation is picked
    up automatically. Every operation is guarded so logging never crashes the
    strategy."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        try:
            self._stream.write(data)
        except Exception:  # noqa: BLE001
            pass
        fh = _log_fh
        if fh is not None:
            try:
                fh.write(data)
            except Exception:  # noqa: BLE001
                pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass
        fh = _log_fh
        if fh is not None:
            try:
                fh.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()


def install_contract_logging():
    """Open the first contract log file and tee ``sys.stdout`` to it so every
    print goes to BOTH the console and the dated file. Idempotent + guarded."""
    if not LOG_TO_FILE:
        return
    roll_contract_log()
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)


# ============================================================
# SUPPORT / RESISTANCE + NO-TRADE ZONE (Req 9.13, 9.14)
# ============================================================
def compute_support_resistance(index_price):
    """
    Derive the single nearest-500 S/R line and the spot's role against it
    (Req 9.13, 9.14) — the Sensex nearest-level model, on a 500-point grid.

    The S/R line is the nearest ``SR_LINE_STEP`` (500) level to the spot:

        sr_line = round(spot / 500) * 500

    The spot's position relative to that line sets the role and the tradable
    side. ``NO_TRADE_ZONE_BUFFER`` is 0 (dead band DISABLED), so every cycle
    lands on one side or the other:
      * spot BELOW the line  -> line acts as RESISTANCE -> resistance_side (bear)
      * spot ABOVE the line  -> line acts as SUPPORT    -> support_side   (bull)
      * spot EXACTLY on it   -> AT_LINE / no_trade_zone (degenerate tick only;
        widens to a real dead band if the buffer is raised above 0)

    e.g. spot 81800 -> sr_line 82000; spot is below -> 82000 is RESISTANCE and
    the bot looks for a BEAR trade. (spot 81400 -> sr_line 81500.)

    Returns ``(sr_line, role, position, no_trade_low, no_trade_high)`` where
    ``role`` is ``RESISTANCE``/``SUPPORT``/``AT_LINE`` and ``position`` is
    ``resistance_side``/``support_side``/``no_trade_zone`` (consumed by
    ``derive_directional_bias`` and the entry gating). Raises ``ValueError``
    when the index price is missing/non-positive so a bad anchor is loud rather
    than silently producing a bogus line.
    """
    if index_price is None:
        raise ValueError(
            "compute_support_resistance: index_price is required (got None).")
    try:
        spot = float(index_price)
    except (TypeError, ValueError):
        raise ValueError(
            "compute_support_resistance: index_price must be numeric, got "
            "{!r}.".format(index_price))
    if spot <= 0:
        raise ValueError(
            "compute_support_resistance: index_price must be positive, got "
            "{!r}.".format(index_price))

    sr_line = float(round(spot / SR_LINE_STEP) * SR_LINE_STEP)
    role, position, no_trade_low, no_trade_high = _classify_spot_vs_line(
        spot, sr_line)
    return sr_line, role, position, no_trade_low, no_trade_high


def _role_vs_level(spot, level):
    """
    Role of a level relative to spot, with NO dead band (Req 9.13).

    Returns ``(role, position)``:
      * spot BELOW the level -> ("RESISTANCE", "resistance_side")
      * spot ABOVE the level -> ("SUPPORT",    "support_side")
      * spot exactly ON it   -> ("AT_LINE",    "no_trade_zone")

    Used when the 500 line has been substituted by a nearest-to-spot strike
    (``resolve_sr_strike``): that strike sits within one strike step of spot, so
    the no-trade buffer must NOT be re-measured around it (see the caller). This
    helper answers only "which side of the level is price on".
    """
    if spot < level:
        return "RESISTANCE", "resistance_side"
    if spot > level:
        return "SUPPORT", "support_side"
    return "AT_LINE", "no_trade_zone"


def _classify_spot_vs_line(spot, sr_line):
    """
    Classify the spot's role against an S/R line (Req 9.13, 9.14).

    Returns ``(role, position, no_trade_low, no_trade_high)``:
      * spot within +/- ``NO_TRADE_ZONE_BUFFER`` of the line -> AT_LINE /
        no_trade_zone. The buffer is 0 (dead band DISABLED), so this now only
        fires on an exact spot == line tick;
      * spot BELOW the line -> the line is overhead RESISTANCE / resistance_side;
      * spot ABOVE the line -> the line is underlying SUPPORT / support_side.

    Shared by ``compute_support_resistance`` (on the raw 500 line) and the OI
    refinement (on the chosen strike) so both classify consistently.
    """
    no_trade_low = sr_line - NO_TRADE_ZONE_BUFFER
    no_trade_high = sr_line + NO_TRADE_ZONE_BUFFER
    if no_trade_low <= spot <= no_trade_high:
        return "AT_LINE", "no_trade_zone", no_trade_low, no_trade_high
    if spot < sr_line:
        return "RESISTANCE", "resistance_side", no_trade_low, no_trade_high
    return "SUPPORT", "support_side", no_trade_low, no_trade_high


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


def _ratio2(numer, denom):
    """
    Two-decimal ratio string for the Sensex-style oipcr / choipcr columns.

    Returns ``"nan"`` when the denominator is zero/None or either side is
    non-numeric (mirrors the Sensex log, where a zero CALL-side value yields a
    ``nan`` choipcr). Otherwise ``round(numer / denom, 2)`` as a 2-dp string.
    """
    try:
        n = float(numer)
        d = float(denom)
    except (TypeError, ValueError):
        return "nan"
    if d == 0.0:
        return "nan"
    return "{:.2f}".format(round(n / d, 2))


def _fmt_oi_usd(value, signed=False):
    """
    Format a USD-notional OI figure the way the Delta UI does: ``$1.17M`` for
    millions, ``$835.68K`` for thousands, and ``$3.52`` (two decimals, no
    suffix) for small values so tiny OI stays precise instead of collapsing to
    ``0``. With ``signed=True`` a leading ``+``/``-`` is shown (for OI changes).
    Returns ``None`` when the value is not numeric.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    sign = "-" if f < 0 else ("+" if signed else "")
    a = abs(f)
    if a >= 1_000_000:
        body = "${:.2f}M".format(a / 1_000_000.0)
    elif a >= 1_000:
        body = "${:.2f}K".format(a / 1_000.0)
    else:
        body = "${:.2f}".format(a)
    return sign + body


def _contracts_to_usd(contracts, oi_contracts, oi_usd):
    """
    Scale a contract quantity to USD notional using the per-row conversion
    factor ``oi_usd / oi_contracts`` (which equals ``contract_value * spot`` and
    is identical for the CE and PE side at a strike). Used so an OI *change*
    prints on the same USD basis as the OI it sits next to. Returns ``None`` when
    the factor can't be derived (missing USD value, or zero contract OI).
    """
    try:
        c = float(contracts)
        oc = float(oi_contracts)
        ou = float(oi_usd)
    except (TypeError, ValueError):
        return None
    if oc == 0:
        return None
    return c * (ou / oc)


def _fmt_oi_abs(oi_contracts, oi_usd):
    """
    Display absolute OI as USD notional (``oi_value_usd``, matching the Delta
    UI). Falls back to a higher-precision contract count (3 decimals for tiny
    sub-1 values, else 1 decimal) when the exchange USD value is unavailable, so
    the column is still readable. Pure display — ratio math uses raw contracts.
    """
    usd = _fmt_oi_usd(oi_usd)
    if usd is not None:
        return usd
    try:
        c = float(oi_contracts)
    except (TypeError, ValueError):
        c = 0.0
    return "{:.3f}".format(c) if 0 < abs(c) < 1 else "{:.1f}".format(c)


def _fmt_oi_change_disp(delta, oi_contracts, oi_usd):
    """
    Return a ``(tag, value)`` pair for a same-strike OI change, with ``value``
    in USD notional when derivable (so it matches the USD OI columns) and a
    higher-precision signed contract count otherwise. ``tag`` is ``added`` for a
    positive change, ``unwind`` for <= 0.
    """
    try:
        d = float(delta) if delta is not None else 0.0
    except (TypeError, ValueError):
        d = 0.0
    tag = "added" if d > 0 else "unwind"
    usd = _contracts_to_usd(d, oi_contracts, oi_usd)
    if usd is not None:
        return tag, _fmt_oi_usd(usd, signed=True)
    return tag, _fmt(d, 3 if 0 < abs(d) < 1 else 1)


def log_option_chain_table(snapshot, oi_deltas, pcr,
                           index_price=None, perp_price=None):
    """
    Log ONE Sensex-style option-chain block per cycle (Req 9.4, 9.6-9.9).

    Mirrors the Sensex ``====after 3 min ochain====`` layout: a header line, a
    column hint, then one ``pcrN`` row per strike (17 strikes printed
    DESCENDING, high strikes first, so the ATM lands on row #9). Per row:

        pcrN =  BTC-<strike>   <oipcr>   <choipcr> CALL <added|unwind> <ceChg>
                PUT <added|unwind> <peChg>  CE_OI=<ce_oi> PE_OI=<pe_oi>

      * ``oipcr`` = same-strike OI PCR = PE_OI / CE_OI (Req 9.7).
      * ``choipcr`` = same-strike change-OI PCR = PUT_oiChg / CALL_oiChg, from
        the bot's OWN consecutive-snapshot deltas (Req 9.8); ``nan`` when the
        CALL-side change is zero (first cycle / no CALL activity).
      * ``CALL/PUT added|unwind`` are those same OI deltas with a direction tag.
      * ``CE_OI`` / ``PE_OI`` are the current same-strike open interest (Req 9.7).

    Below the table the aggregate PCR over the central 9 strikes (Req 9.7), the
    sum of the per-strike oipcr, and the BTC spot (``.DEXBTUSD`` index anchor,
    Req 9.11) + perpetual/future (``BTCUSD``, log-only, Req 9.12) prices are
    printed. The CE_OI/PE_OI columns display USD notional (``oi_value_usd``,
    matching the Delta UI's ``$M``/``$K`` figures); every ratio (oipcr, choipcr,
    PCR) is still computed on the raw contract OI, which is unit-invariant so the
    numbers are identical either way. Prices are USD. Timestamps display in IST
    (logic stays UTC).
    """
    if not isinstance(snapshot, dict):
        print("====after 3 min ochain==== {} (no snapshot available)".format(
            _now_ist()))
        return

    atm_strike = snapshot.get("atm_strike")
    rows = snapshot.get("rows") or {}
    oi_deltas = oi_deltas or {}

    print("====after 3 min ochain==== {}".format(_now_ist()))
    print("                                    oipcr   choipcr   "
          "CE_OI($) / PE_OI($) (same strike, USD notional ~ Delta UI)")

    # Print strikes DESCENDING (high -> low) so the ATM sits on row #9 of 17,
    # matching the Sensex option-chain view.
    strikes_desc = list(reversed(snapshot.get("strikes", [])))
    oipcr_sum = 0.0

    for i, strike in enumerate(strikes_desc, start=1):
        pair = rows.get(strike) or {}
        call_row = pair.get("call") if isinstance(pair, dict) else None
        put_row = pair.get("put") if isinstance(pair, dict) else None
        call_row = call_row if isinstance(call_row, dict) else {}
        put_row = put_row if isinstance(put_row, dict) else {}

        # Raw contract OI drives ALL ratio math (unit-invariant); USD notional
        # is display-only (matches the Delta UI).
        ce_oi = call_row.get("oi") or 0.0
        pe_oi = put_row.get("oi") or 0.0
        ce_oi_usd = call_row.get("oi_usd")
        pe_oi_usd = put_row.get("oi_usd")

        # Own same-strike consecutive-snapshot OI deltas (Req 9.8), keyed by
        # strike; tolerate int/str key variants and first-cycle zero deltas.
        delta_row = (oi_deltas.get(strike)
                     or oi_deltas.get(int(strike)) or {}) \
            if isinstance(oi_deltas, dict) else {}
        ce_choi = delta_row.get("call_oi_delta")
        pe_choi = delta_row.get("put_oi_delta")

        # Ratios computed on raw contracts (the USD factor cancels in a ratio).
        oipcr = _ratio2(pe_oi, ce_oi)       # PE_OI / CE_OI (same strike)
        choipcr = _ratio2(pe_choi, ce_choi)  # PUT_chg / CALL_chg (same strike)
        try:
            oipcr_sum += float(oipcr)
        except (TypeError, ValueError):
            pass

        # Display values in USD notional (OI change on the same USD basis).
        ce_tag, ce_val = _fmt_oi_change_disp(ce_choi, ce_oi, ce_oi_usd)
        pe_tag, pe_val = _fmt_oi_change_disp(pe_choi, pe_oi, pe_oi_usd)

        line = ("pcr{n} =  BTC-{strike}   {oipcr}   {choipcr} "
                "CALL {ctag} {cval}   PUT {ptag} {pval}  "
                "CE_OI={ceoi} PE_OI={peoi}").format(
                    n=i, strike=int(strike), oipcr=oipcr, choipcr=choipcr,
                    ctag=ce_tag, cval=ce_val, ptag=pe_tag, pval=pe_val,
                    ceoi=_fmt_oi_abs(ce_oi, ce_oi_usd),
                    peoi=_fmt_oi_abs(pe_oi, pe_oi_usd))
        if atm_strike is not None and strike == atm_strike:
            line += " <== ATM"
        print(line)

    print("PCRSUM== {}".format(_fmt(oipcr_sum, 2)))
    print()
    print("AVG_OIPCR_9STRIKE= {}  (central 9 strikes, Req 9.7)".format(
        _fmt(pcr, 2)))

    # Spot (index anchor) + perpetual/future reference, printed after the chain.
    print("BTC SPOT (.DEXBTUSD)   = {}".format(_fmt(index_price)))
    print("BTC PERP/FUT (BTCUSD)  = {}".format(_fmt(perp_price)))


def log_support_resistance(index_price, sr_line, role, position,
                           no_trade_low, no_trade_high,
                           sr_strike=None, fallback=False, fallback_note=""):
    """
    Log the S/R level and the spot's role against it (Req 9.4, 9.13, 9.14),
    Sensex-style ``SUPP_RES===`` line.

    ``role`` is ``RESISTANCE``/``SUPPORT``/``AT_LINE``; the trailing note spells
    out the directional read (spot below the level -> resistance -> bear; spot
    above -> support -> bull; at the level -> no trade).

    When the nearest-500 line carried no OI (unlisted, or listed with zero OI)
    ``fallback`` is True and ``sr_strike`` is the nearest-to-spot listed strike
    that replaced it; the role/no-trade values passed in are then already
    measured against THAT strike. An extra ``SR_FALLBACK`` line explains the
    substitution so the log is never ambiguous about which level is in force.
    """
    level = sr_strike if (fallback and sr_strike is not None) else sr_line
    if position == "resistance_side":
        note = "spot below level => RESISTANCE => look for BEAR trade"
    elif position == "support_side":
        note = "spot above level => SUPPORT => look for BULL trade"
    else:
        # no_trade_zone. With NO_TRADE_ZONE_BUFFER = 0 (dead band disabled) this
        # only happens on an exact spot == line tick; a non-zero buffer widens it
        # back into a real band around the 500 line.
        where = ("spot exactly ON the {} line {}".format(
            SR_LINE_STEP, _fmt(sr_line)) if not NO_TRADE_ZONE_BUFFER
            else "spot within +/-{} of the {} line {}".format(
                NO_TRADE_ZONE_BUFFER, SR_LINE_STEP, _fmt(sr_line)))
        note = ("{where} => NO-TRADE zone".format(where=where)
                if role not in ("SUPPORT", "RESISTANCE") else
                "role={role} vs level {level}, but {where} => NO-TRADE zone"
                .format(role=role, level=_fmt(level), where=where))

    basis = ("nearest listed strike to spot {spot}; {step} line {line} had "
             "no OI".format(spot=_fmt(index_price), step=SR_LINE_STEP,
                            line=_fmt(sr_line))
             if fallback else
             "nearest {step} to spot {spot}".format(
                 step=SR_LINE_STEP, spot=_fmt(index_price)))

    print("SUPP_RES=== {level} role={role} ({basis}) "
          "no_trade=[{ntl}, {nth}] | {note}".format(
              level=_fmt(level), role=role, basis=basis,
              ntl=_fmt(no_trade_low), nth=_fmt(no_trade_high), note=note))
    if fallback and fallback_note:
        print("SR_FALLBACK: {}".format(fallback_note))


def _compare_ce_pe(ce, pe):
    """
    Sensex-style ``CE < PE by X%`` comparison string for a CE/PE pair.

    ``X`` is the absolute difference as a percentage of the larger side
    (``|ce - pe| / max(|ce|, |pe|) * 100``). Returns ``"n/a"`` when either side
    is non-numeric and ``"CE = PE by 0.0%"`` when both are zero.
    """
    try:
        c = float(ce)
        p = float(pe)
    except (TypeError, ValueError):
        return "n/a"
    larger = max(abs(c), abs(p))
    if larger == 0.0:
        return "CE = PE by 0.0%"
    pct = abs(c - p) / larger * 100.0
    if c > p:
        rel = "CE > PE"
    elif c < p:
        rel = "CE < PE"
    else:
        rel = "CE = PE"
    return "{} by {:.1f}%".format(rel, pct)


def log_atm_shift_summary(snapshot, pcr_full, pcr_9, is_atm_shift, map_strike,
                          avg_oipcr_list, avg_oipcr9_list,
                          atm_not_shifted_count):
    """
    Log the Sensex-equivalent ATM-shift + rolling OI-PCR summary lines
    (Req 9.4, 9.7, 9.10).

    Mirrors the Sensex block::

        IS_ATM_STRIKE_SHIFT = False  mapStrike = {85000: 85000}
        avgOiPcrList2  = [1.59, 1.61] atmStrikeNotShiftedCount= 2   (full 17)
        avgOiPcr9List2 = [1.10, 1.12] atmStrikeNotShiftedCount= 2   (central 9)

    ``is_atm_shift`` is True when the ATM strike moved vs the previous cycle;
    ``map_strike`` maps prev->curr ATM. The two lists accumulate the per-cycle
    full-17 and central-9 OI PCRs while the ATM holds steady and reset on a
    shift; ``atm_not_shifted_count`` is the consecutive-stable-cycle count.
    """
    print("IS_ATM_STRIKE_SHIFT = {}  mapStrike = {}".format(
        is_atm_shift, map_strike))
    print("avgOiPcrList2  = {} atmStrikeNotShiftedCount= {}  (full 17 strikes, "
          "curr={})".format(
              [round(x, 4) for x in avg_oipcr_list], atm_not_shifted_count,
              _fmt(pcr_full, 4)))
    print("avgOiPcr9List2 = {} atmStrikeNotShiftedCount= {}  (central 9, "
          "curr={})".format(
              [round(x, 4) for x in avg_oipcr9_list], atm_not_shifted_count,
              _fmt(pcr_9, 4)))
    print("==== PCR trend check {} ====".format(_now_ist()))


def _seq_pct_steps(window):
    """Per-step percentage change across a PCR window (e.g. [a, b, c] ->
    [(b-a)/a*100, (c-b)/b*100]); a 0 denominator yields a 0.0 step. Rounded to
    2 decimals so a sub-0.1% step is visible instead of showing as -0.0%."""
    steps = []
    for i in range(len(window) - 1):
        prev, curr = window[i], window[i + 1]
        steps.append(round((curr - prev) / prev * 100, 2) if prev else 0.0)
    return steps


def _fmt_steps(window):
    """Render a PCR window's per-step % changes as ``step1 +1.17% | step2 -0.02%``."""
    return " | ".join("step{} {:+}%".format(i + 1, v)
                      for i, v in enumerate(_seq_pct_steps(window)))


def _log_min_move(flag_label, window, min_pct=PCR_MIN_STEP_PCT):
    """
    Log whether an already-detected inc/dec run clears the ``min_pct`` per-step
    threshold, i.e. whether it is an economically meaningful move rather than
    4th-decimal drift. Prints ``<flag>_MinMove = True/False`` with the smallest
    step so the verdict is self-explanatory. Returns the boolean.
    """
    steps = [abs(s) for s in _seq_pct_steps(window)]
    smallest = min(steps) if steps else 0.0
    ok = bool(steps) and smallest >= min_pct
    print("{}_MinMove = {}  (smallest step {:.2f}% vs {:.2f}% threshold -> "
          "{})".format(flag_label, ok, smallest, min_pct,
                       "meaningful" if ok else "drift/noise"))
    return ok


def update_pcr_trend_window(avg_oipcr_list, avg_oipcr9_list,
                            atm_not_shifted_count, is_atm_shift,
                            window=PCR_TREND_WINDOW):
    """
    Sensex-equivalent consecutive PCR increase/decrease detection + rolling
    window upkeep (mirrors Strategy_Sensex_May_2026's avgOiPcrList2 /
    avgOiPcr9List2 handling, Req 9.10).

    The two parallel windows — full-17 (`avg_oipcr_list`) and central-9
    (`avg_oipcr9_list`) — are kept in lockstep (every trim applies to both). The
    caller has already appended this cycle's PCRs and, on an ATM-strike shift,
    reset each window to its newest value. Here:

      * A trend is evaluated only when the ATM strike has held steady for
        ``>= window`` cycles AND ``window`` values are banked.
      * Strictly rising across the window -> ``inc=True`` (``PCR_SEQ_INC``);
        strictly falling -> ``dec=True`` (``PCR_SEQ_DEC``). These are the
        Sensex ``IS_CONSECUTIVELY_..._PCR_INCREASED/DECREASED`` flags.
      * Not monotonic -> flags clear and the window slides forward: drop the
        oldest value (keep the recent ones) so a fresh run can form, or — when
        the last two are equal (fully stalled) — keep only the newest value.
      * A safety cap keeps at most ``window`` most-recent values so a long 24/7
        session can't grow the lists without bound.

    The central-9 window is reported read-only (``PCR9_SEQ_*``) and trimmed in
    lockstep with the full-17 window, exactly like the Sensex shadow list.

    Returns ``(avg_oipcr_list, avg_oipcr9_list, inc, dec, inc9, dec9)`` with the
    (possibly trimmed) windows, the full-17 increase/decrease flags (``inc``/
    ``dec`` = isPcrInc/isPcrDecr) and the central-9 flags (``inc9``/``dec9`` =
    is9PcrInc/is9PcrDecr).
    """
    inc = dec = False
    inc9 = dec9 = False

    # Safety cap: never carry more than `window` most-recent values.
    if len(avg_oipcr_list) > window:
        avg_oipcr_list = avg_oipcr_list[-window:]
    if len(avg_oipcr9_list) > window:
        avg_oipcr9_list = avg_oipcr9_list[-window:]

    gate = (not is_atm_shift) and atm_not_shifted_count >= window

    # Central-9 read-only trend report (shadows the shared window).
    if gate and len(avg_oipcr9_list) == window:
        q = avg_oipcr9_list
        if all(q[i] < q[i + 1] for i in range(len(q) - 1)):
            inc9 = True
            print("PCR9_SEQ_INC: {} | {} (central 9 rising)".format(
                " -> ".join(_fmt(x, 4) for x in q), _fmt_steps(q)))
            print("is9PcrInc = True")
            _log_min_move("is9PcrInc", q)
        elif all(q[i] > q[i + 1] for i in range(len(q) - 1)):
            dec9 = True
            print("PCR9_SEQ_DEC: {} | {} (central 9 falling)".format(
                " -> ".join(_fmt(x, 4) for x in q), _fmt_steps(q)))
            print("is9PcrDecr = True")
            _log_min_move("is9PcrDecr", q)
        else:
            print("PCR9_SEQ_FLAT: {} (not monotonic)".format(
                " -> ".join(_fmt(x, 4) for x in q)))

    # Full-17 trend detection + shared window trim.
    if gate and len(avg_oipcr_list) == window:
        p = avg_oipcr_list
        seq = " -> ".join(_fmt(x, 4) for x in p)
        step_str = _fmt_steps(p)
        if all(p[i] < p[i + 1] for i in range(len(p) - 1)):
            inc = True
            print("PCR_SEQ_INC: {} | {}".format(seq, step_str))
            print("isPcrInc = True")
            _log_min_move("isPcrInc", p)
        elif all(p[i] > p[i + 1] for i in range(len(p) - 1)):
            dec = True
            print("PCR_SEQ_DEC: {} | {}".format(seq, step_str))
            print("isPcrDecr = True")
            _log_min_move("isPcrDecr", p)
        else:
            print("PCR_SEQ_FLAT: {} (not monotonic)".format(seq))
            # Slide forward: drop the oldest (keep recent run) unless the last
            # two are equal (fully stalled), in which case keep only the newest.
            if p[-2] != p[-1]:
                avg_oipcr_list = avg_oipcr_list[1:]
                avg_oipcr9_list = avg_oipcr9_list[1:]
            else:
                avg_oipcr_list = avg_oipcr_list[-1:]
                avg_oipcr9_list = avg_oipcr9_list[-1:]
    elif len(avg_oipcr_list) == window:
        # Window full but the ATM has not yet held steady for `window` cycles
        # (recent shift): drop the stale leading values so length tracks the
        # not-shifted count. Both windows trimmed in lockstep.
        remove = max(0, window - atm_not_shifted_count)
        if remove:
            avg_oipcr_list = avg_oipcr_list[remove:]
            avg_oipcr9_list = avg_oipcr9_list[remove:]

    return avg_oipcr_list, avg_oipcr9_list, inc, dec, inc9, dec9


# ============================================================
# INVARIANT-WINDOW PCR TREND (parallel, observation-only)
# ============================================================
# The existing avgOiPcrList2 / avgOiPcr9List2 + isPcrInc/is9PcrInc track the PCR
# AS MEASURED each cycle, so when the ATM shifts the strike set changes and the
# PCR jumps for a reason that is NOT an OI change (one high-OI strike leaves the
# window, another enters). Those series are left exactly as-is.
#
# This parallel track removes that artifact: it keeps the last few raw-chain OI
# snapshots and, every cycle, RE-COMPUTES the whole PCR series over the CURRENT
# strike set. Every value in the series then sits on the same basis, so a rise /
# fall reflects ONLY open-interest change at those strikes. No reset is needed on
# an ATM shift, so a genuine trend can run uninterrupted. Observation-only.
def _pcr_from_oi_map(oi_map, strikes):
    """PCR = (Σ put OI) / (Σ call OI) over `strikes`, read from a slim
    ``{strike: {"call_oi", "put_oi"}}`` map. ``None`` when the call side is
    zero/absent (ratio undefined)."""
    if not isinstance(oi_map, dict):
        return None
    ce = pe = 0.0
    for s in strikes or []:
        try:
            row = oi_map.get(int(s))
        except (TypeError, ValueError):
            row = None
        if isinstance(row, dict):
            ce += row.get("call_oi") or 0.0
            pe += row.get("put_oi") or 0.0
    if ce <= 0:
        return None
    return pe / ce


def invariant_pcr_series(chain_oi_history, strikes):
    """
    Re-compute the PCR for each snapshot in ``chain_oi_history`` (oldest first)
    over the SAME, current ``strikes``. Returns the series oldest->newest with
    unusable values dropped — every element is directly comparable.
    """
    series = []
    for oi_map in (chain_oi_history or []):
        value = _pcr_from_oi_map(oi_map, strikes)
        if _pcr_is_finite(value):
            series.append(value)
    return series


def total_chain_pcr(chain):
    """
    FULL-CHAIN (total) PCR = (Σ put OI) / (Σ call OI) over EVERY listed strike of
    the expiry — the standard "PCR (OI)" definition. Delta exposes no aggregate
    PCR endpoint, so this is summed locally from the already-fetched chain (zero
    extra API calls).

    Unlike the 9/17-strike PCRs this has NO ATM dependency, so its value never
    jumps because a window moved — but it DOES include far-OTM strangle/premium-
    selling OI, which dilutes the near-the-money directional signal. Logged for
    comparison only.

    Returns ``(pcr, ce_contracts, pe_contracts, ce_usd, pe_usd)``; ``pcr`` is
    ``None`` when the call side is zero.
    """
    ce = pe = 0.0
    ce_usd = pe_usd = 0.0
    for pair in _index_chain_by_strike(chain).values():
        for side, acc in (("call", "ce"), ("put", "pe")):
            row = pair.get(side)
            if not isinstance(row, dict):
                continue
            try:
                oi = float(row.get("oi"))
            except (TypeError, ValueError):
                oi = 0.0
            try:
                usd = float(row.get("oi_usd"))
            except (TypeError, ValueError):
                usd = 0.0
            if acc == "ce":
                ce += oi
                ce_usd += usd
            else:
                pe += oi
                pe_usd += usd
    if ce <= 0:
        return None, ce, pe, ce_usd, pe_usd
    return pe / ce, ce, pe, ce_usd, pe_usd


def log_total_pcr_trend(series, prev_inc_streak, prev_dec_streak,
                        window=TOTAL_PCR_WINDOW):
    """
    Detect + log a strictly rising/falling run on the FULL-CHAIN PCR series and
    maintain its consecutive-occurrence streaks. Needs ``window`` (4) values; no
    ATM-shift reset applies because the total PCR has no window dependency.

    Returns ``(inc, dec, inc_streak, dec_streak)``.
    """
    if len(series) < window:
        return False, False, 0, 0
    s = series[-window:]
    seq = " -> ".join(_fmt(x, 4) for x in s)
    inc = all(s[i] < s[i + 1] for i in range(len(s) - 1))
    dec = all(s[i] > s[i + 1] for i in range(len(s) - 1))
    inc_streak = _update_flag_streak(inc, prev_inc_streak)
    dec_streak = _update_flag_streak(dec, prev_dec_streak)
    if inc:
        print("TOTAL_PCR_SEQ_INC: {} | {}".format(seq, _fmt_steps(s)))
        print("isTotalPcrInc = True  (streak={} consecutive cycle(s) "
              "rising)".format(inc_streak))
        _log_min_move("isTotalPcrInc", s)
    elif dec:
        print("TOTAL_PCR_SEQ_DEC: {} | {}".format(seq, _fmt_steps(s)))
        print("isTotalPcrDecr = True  (streak={} consecutive cycle(s) "
              "falling)".format(dec_streak))
        _log_min_move("isTotalPcrDecr", s)
    else:
        print("TOTAL_PCR_SEQ_FLAT: {} (not monotonic)".format(seq))
    return inc, dec, inc_streak, dec_streak


def log_invariant_pcr_trend(label, series, prev_inc_streak, prev_dec_streak,
                            window=PCR_TREND_WINDOW):
    """
    Detect + log a strictly rising/falling run on an invariant PCR series and
    maintain its consecutive-occurrence streaks.

    Prints ``<label>_SEQ_INC_InvariantWindow`` / ``..._DEC_InvariantWindow`` with
    the series and per-step %, the ``is...Inc_InvariantWindow`` flag, and the
    streak count. Needs ``window`` comparable values; no ATM-shift reset applies
    because every value is already on the current strike set.

    Returns ``(inc, dec, inc_streak, dec_streak)``.
    """
    if len(series) < window:
        return False, False, 0, 0
    s = series[-window:]
    seq = " -> ".join(_fmt(x, 4) for x in s)
    inc = all(s[i] < s[i + 1] for i in range(len(s) - 1))
    dec = all(s[i] > s[i + 1] for i in range(len(s) - 1))
    inc_streak = _update_flag_streak(inc, prev_inc_streak)
    dec_streak = _update_flag_streak(dec, prev_dec_streak)

    if inc:
        print("{}_SEQ_INC_InvariantWindow: {} | {}".format(
            label, seq, _fmt_steps(s)))
        print("is{}PcrInc_InvariantWindow = True  (streak={} consecutive "
              "cycle(s) rising)".format(label, inc_streak))
        _log_min_move("is{}PcrInc_InvariantWindow".format(label), s)
    elif dec:
        print("{}_SEQ_DEC_InvariantWindow: {} | {}".format(
            label, seq, _fmt_steps(s)))
        print("is{}PcrDecr_InvariantWindow = True  (streak={} consecutive "
              "cycle(s) falling)".format(label, dec_streak))
        _log_min_move("is{}PcrDecr_InvariantWindow".format(label), s)
    else:
        print("{}_SEQ_FLAT_InvariantWindow: {} (not monotonic)".format(
            label, seq))
    return inc, dec, inc_streak, dec_streak


def _update_flag_streak(flag_true, prev_streak):
    """Increment a consecutive-occurrence counter while `flag_true` holds; reset
    to 0 the moment it stops (the streak is 'disqualified')."""
    return (prev_streak or 0) + 1 if flag_true else 0


def _log_seq_streaks(label, inc_streak, dec_streak):
    """
    Log how many CONSECUTIVE cycles a PCR_SEQ inc/dec verdict has held, so a
    sustained bullish/bearish run is visible at a glance. Prints only while a
    run is alive; nothing on a flat/disqualified cycle. Observation-only.
    """
    if inc_streak >= 1:
        print("{}_INC_STREAK = {} consecutive cycle(s) rising "
              "(bullish trend building)".format(label, inc_streak))
    elif dec_streak >= 1:
        print("{}_DEC_STREAK = {} consecutive cycle(s) falling "
              "(bearish trend building)".format(label, dec_streak))


def _update_dominance_streak(curr, prev, up_streak, down_streak, is_atm_shift,
                             eps=1e-9):
    """
    Update a consecutive PCR rise/fall streak (Sensex-style once/twice/thrice).

    A rising PCR means puts are growing faster than calls -> PE-side dominance
    (bullish); a falling PCR -> CE-side dominance (bearish). The streak counts
    consecutive cycles in the same direction and resets to 0 on an ATM-strike
    shift, a non-finite value, or a flat step. Returns ``(up_streak, down_streak)``
    (only one is ever non-zero).
    """
    if is_atm_shift or not _pcr_is_finite(curr) or not _pcr_is_finite(prev):
        return 0, 0
    delta = float(curr) - float(prev)
    if delta > eps:
        return up_streak + 1, 0
    if delta < -eps:
        return 0, down_streak + 1
    return 0, 0


def _log_pcr_dominance(label, up_streak, down_streak):
    """
    Sensex-style once/twice/thrice dominance print for a PCR series. A rising
    streak logs PE dominance (bullish lean); a falling streak logs CE dominance
    (bearish lean); nothing is printed on a flat/first cycle.
    """
    if up_streak >= 1:
        print("{}: PE_dominant (PCR rising) once={} twice={} thrice={} "
              "(up_streak={})".format(label, up_streak >= 1, up_streak >= 2,
                                      up_streak >= 3, up_streak))
    elif down_streak >= 1:
        print("{}: CE_dominant (PCR falling) once={} twice={} thrice={} "
              "(down_streak={})".format(label, down_streak >= 1,
                                        down_streak >= 2, down_streak >= 3,
                                        down_streak))


def _index_chain_by_strike(chain):
    """
    Index the RAW option chain (every listed strike for the expiry, not just the
    200-grid snapshot) into ``{int(strike): {"call": row|None, "put": row|None}}``.

    Needed because the 17-strike snapshot is built on a fixed 200 grid, so it
    never contains the interim strikes Delta lists near expiry (e.g. 81500). The
    raw chain does, so the S/R-strike picker consults this index.
    """
    idx = {}
    for row in (chain or []):
        if not isinstance(row, dict):
            continue
        try:
            key = int(round(float(row.get("strike"))))
        except (TypeError, ValueError):
            continue
        ctype = str(row.get("contract_type") or "").strip().lower()
        slot = idx.setdefault(key, {"call": None, "put": None})
        if "call" in ctype or ctype == "c":
            slot["call"] = row
        elif "put" in ctype or ctype == "p":
            slot["put"] = row
    return idx


def _chain_oi_map(chain):
    """
    Slim ``{int(strike): {"call_oi": float, "put_oi": float}}`` built from the
    raw chain, carried cycle-to-cycle so the S/R strike's OI CHANGE can be
    diffed at ANY strike — including interim strikes (e.g. 83500) that the
    200-grid snapshot omits. Lighter than carrying full rows.
    """
    out = {}
    for strike, pair in _index_chain_by_strike(chain).items():
        ce = pair.get("call") if isinstance(pair.get("call"), dict) else {}
        pe = pair.get("put") if isinstance(pair.get("put"), dict) else {}
        try:
            ce_oi = float(ce.get("oi"))
        except (TypeError, ValueError):
            ce_oi = 0.0
        try:
            pe_oi = float(pe.get("oi"))
        except (TypeError, ValueError):
            pe_oi = 0.0
        out[strike] = {"call_oi": ce_oi, "put_oi": pe_oi}
    return out


def _strike_total_oi(pair):
    """Total (CE + PE) contract OI at an indexed strike ``pair``; 0.0 when the
    strike is absent or both sides are missing/garbled."""
    if not isinstance(pair, dict):
        return 0.0
    total = 0.0
    for side in ("call", "put"):
        row = pair.get(side)
        if isinstance(row, dict):
            try:
                total += float(row.get("oi") or 0.0)
            except (TypeError, ValueError):
                pass
    return total


def resolve_sr_strike(sr_line, spot, chain):
    """
    Resolve the S/R level to a strike that actually carries OI (user's rule).

    1. The S/R line stays the nearest-500 level to spot (Sensex model) — OI
       never influences WHICH 500 level is chosen.
    2. If that line IS a listed strike carrying OI, it is the S/R strike. Done.
       (BTC strikes are on a 200 grid, so the x000 lines qualify.)
    3. If the line is NOT available — either unlisted (the x500 levels) or
       listed with zero OI — fall back to the listed strike NEAREST THE SPOT
       that carries OI. The role then follows from spot vs that strike::

         spot 82553 -> line 82500 unavailable -> nearest to spot is 82600
                       (47 away vs 153) -> spot BELOW  -> RESISTANCE
         spot 82480 -> line 82500 unavailable -> nearest to spot is 82400
                       (80 away vs 120) -> spot ABOVE  -> SUPPORT

       On BTC's 200 grid this is simply spot rounded to the nearest 200, but it
       is computed from the live chain rather than assuming the grid, so it
       stays correct if Delta ever changes the strike spacing or starts listing
       the x500 levels.

    Ties (spot exactly midway between two strikes) break to the LOWER strike so
    the choice is deterministic and reproducible.

    Returns ``(sr_strike, fallback_used, note)``. On any failure the line is
    returned unchanged with ``fallback_used=False``, so a chain hiccup degrades
    to plain Sensex behaviour rather than raising.
    """
    try:
        line = int(round(float(sr_line)))
        sp = float(spot)
    except (TypeError, ValueError):
        return sr_line, False, "non-numeric line/spot; using the 500 line as-is"

    idx = _index_chain_by_strike(chain) if chain else {}
    if not idx:
        return line, False, "chain unavailable; using the 500 line as-is"

    line_oi = _strike_total_oi(idx.get(line))
    if line_oi > 0:
        return line, False, ""

    # Line unavailable -> nearest-to-SPOT listed strike that carries OI.
    with_oi = [s for s in idx if _strike_total_oi(idx.get(s)) > 0]
    pool = with_oi or list(idx.keys())
    if not pool:
        return line, False, "no listed strikes; using the 500 line as-is"

    chosen = min(pool, key=lambda s: (abs(s - sp), s))
    why = ("not a listed strike" if line not in idx
           else "listed but zero OI")
    note = ("500 line {} is {}; nearest listed strike to spot {} is {} "
            "({:.0f} away)".format(line, why, _fmt(sp), chosen,
                                   abs(chosen - sp)))
    if not with_oi:
        note += " [no strike in the chain carries OI]"
    return chosen, chosen != line, note


def _dominance_hint(dom, role):
    """
    Map fresh-flow dominance at an S/R level to a confirm/warning verdict
    (Sensex ``INCR_CHOI`` hint table):
      * SUPPORT    + PE dominant -> support HOLDING (bullish)
      * SUPPORT    + CE dominant -> support UNDERMINED (bearish warning)
      * RESISTANCE + CE dominant -> resistance HOLDING (bearish)
      * RESISTANCE + PE dominant -> resistance CHALLENGED (bullish warning)
    """
    if dom == "FLAT":
        return "neutral"
    if role == "SUPPORT":
        return ("support HOLDING (bullish)" if dom == "PE"
                else "support UNDERMINED (bearish warning)")
    if role == "RESISTANCE":
        return ("resistance HOLDING (bearish)" if dom == "CE"
                else "resistance CHALLENGED (bullish warning)")
    return "n/a"


def log_supp_res_oi(snapshot, sr_strike, sr_line_orig, snapped=False,
                    chain=None, prev_chain_oi=None, baseline_chain_oi=None,
                    sr_role=None):
    """
    Log the Sensex-equivalent CE/PE OI + OI-change detail AT the chosen S/R
    strike (Req 9.4, 9.7, 9.8).

    Mirrors the Sensex block::

        CEoich val =  +$691.76K  PEoich val =  +$973.64K , CE < PE by 29.0%
            (cumulative since contract start)
        SUPP_RES TOTAL OI (strike 83000): CE= $1.43M  PE= $2.09M , CE < PE by 31.4%
        INCR_CHOI: SUPP_RES=83000 role=SUPPORT fresh_CE=+$376.62K
            fresh_PE=+$575.34K -> PE_dominant by 34.5%
            (support HOLDING (bullish)) | cumulative CE=... PE=...

    Three distinct measures: the CUMULATIVE change-in-OI since CONTRACT START
    (the "stock" — Delta has no since-day-start OI-change column, so it is
    computed locally against a 12:00-UTC baseline that resets each contract),
    the ABSOLUTE OI at the strike, and the FRESH per-cycle flow with a
    role-aware dominance verdict (the "flow", which leads the stock).

    The S/R LEVEL is the nearest-500 line (``sr_line_orig``) — the Sensex model,
    with OI playing NO part in choosing which 500 level. ``sr_strike`` is the
    strike that level resolved to: the line itself when it carries OI, else the
    nearest-to-spot listed strike with OI (``resolve_sr_strike``; ``snapped``
    says whether it differed). ``CEoich``/``PEoich`` are that strike's OI change
    vs the previous cycle's raw chain; the TOTAL OI line is its current CE/PE OI.
    """
    rows = snapshot.get("rows") or {}
    strike_index = _index_chain_by_strike(chain) if chain else {}

    sr_key = sr_strike
    # Prefer the raw-chain row (covers interim strikes); else the snapshot row.
    pair = strike_index.get(sr_key) or rows.get(sr_key) \
        or rows.get(sr_line_orig) or {}
    call_row = pair.get("call") if isinstance(pair, dict) else None
    put_row = pair.get("put") if isinstance(pair, dict) else None
    call_row = call_row if isinstance(call_row, dict) else {}
    put_row = put_row if isinstance(put_row, dict) else {}

    # The substitution itself is already explained by the SUPP_RES / SR_FALLBACK
    # lines above, so nothing extra is printed here; ``snapped`` only affects
    # the strike label below.

    # Raw contracts for the CE/PE comparison (unit-invariant); USD for display.
    ce_oi = call_row.get("oi") or 0.0
    pe_oi = put_row.get("oi") or 0.0
    ce_oi_usd = call_row.get("oi_usd")
    pe_oi_usd = put_row.get("oi_usd")

    # ---- Two DIFFERENT OI-change measures at the chosen S/R strike ----------
    # Delta exposes NO cumulative since-day-start OI-change column (only a
    # rolling `oi_change_usd_6h`, log-only), so BOTH are computed locally from
    # the raw chain:
    #
    #   * CUMULATIVE ("stock", Sensex `CEoich`/`PEoich`): current OI minus the
    #     OI captured at CONTRACT START (12:00 UTC baseline). Shows total
    #     buildup/unwind over this contract day; resets at each rollover.
    #   * FRESH ("flow", Sensex `fresh_CE`/`fresh_PE`): current OI minus the
    #     PREVIOUS CYCLE's OI — who is writing RIGHT NOW at the level. Flow
    #     leads the stock, so this is the higher-quality confirmation.
    base = (baseline_chain_oi or {}).get(sr_key) \
        if isinstance(baseline_chain_oi, dict) else None
    if isinstance(base, dict):
        ce_cum = (ce_oi or 0.0) - (base.get("call_oi") or 0.0)
        pe_cum = (pe_oi or 0.0) - (base.get("put_oi") or 0.0)
    else:
        ce_cum = pe_cum = 0.0

    prev = (prev_chain_oi or {}).get(sr_key) \
        if isinstance(prev_chain_oi, dict) else None
    if isinstance(prev, dict):
        ce_fresh = (ce_oi or 0.0) - (prev.get("call_oi") or 0.0)
        pe_fresh = (pe_oi or 0.0) - (prev.get("put_oi") or 0.0)
        fresh_known = True
    else:
        ce_fresh = pe_fresh = 0.0
        fresh_known = False

    # Display in USD notional (same basis as the OI columns); the % comparisons
    # are computed on raw contracts and are identical in either unit.
    def _chg(val, oi_ref, usd_ref):
        return _fmt_oi_usd(_contracts_to_usd(val, oi_ref, usd_ref),
                           signed=True) or _fmt(val, 1)

    ce_cum_disp = _chg(ce_cum, ce_oi, ce_oi_usd)
    pe_cum_disp = _chg(pe_cum, pe_oi, pe_oi_usd)
    ce_fresh_disp = _chg(ce_fresh, ce_oi, ce_oi_usd)
    pe_fresh_disp = _chg(pe_fresh, pe_oi, pe_oi_usd)

    # Strike label: the chosen strike, noting the raw 500 line when it differs.
    try:
        line_i = int(round(float(sr_line_orig)))
    except (TypeError, ValueError):
        line_i = sr_line_orig
    strike_label = ("{}".format(sr_key) if sr_key == line_i
                    else "{} (S/R line {})".format(sr_key, line_i))

    # 1) CUMULATIVE change in OI since contract start (Sensex line 1).
    print("CEoich val =  {}  PEoich val =  {} , {}  (cumulative since contract "
          "start)".format(ce_cum_disp, pe_cum_disp,
                          _compare_ce_pe(ce_cum, pe_cum)))
    # 2) Absolute OI at the S/R strike (Sensex line 2).
    print("SUPP_RES TOTAL OI (strike {}): CE= {}  PE= {} , {}".format(
        strike_label,
        _fmt_oi_abs(ce_oi, ce_oi_usd), _fmt_oi_abs(pe_oi, pe_oi_usd),
        _compare_ce_pe(ce_oi, pe_oi)))
    # 2b) Per-strike GREEKS + IV at the S/R strike (observation-only). These
    #     feed the future delta-based hedge-offset / lot sizing (Sensex
    #     calc_lots_by_risk DELTA path); logged now to validate Delta's Greeks
    #     look sane before anything depends on them. |CE_delta|/|PE_delta| hints
    #     the hedge-offset ratio for a same-side spread.
    print("SR_GREEKS (strike {}): CE[delta={} gamma={} theta={} vega={} "
          "iv={}] | PE[delta={} gamma={} theta={} vega={} iv={}]".format(
              sr_key,
              _fmt(call_row.get("delta"), 4), _fmt(call_row.get("gamma"), 6),
              _fmt(call_row.get("theta"), 2), _fmt(call_row.get("vega"), 2),
              _fmt(call_row.get("mark_iv"), 2),
              _fmt(put_row.get("delta"), 4), _fmt(put_row.get("gamma"), 6),
              _fmt(put_row.get("theta"), 2), _fmt(put_row.get("vega"), 2),
              _fmt(put_row.get("mark_iv"), 2)))
    # 3) FRESH per-cycle flow + dominance verdict (Sensex line 3 INCR_CHOI).
    if fresh_known:
        dom = ("CE" if ce_fresh > pe_fresh
               else "PE" if pe_fresh > ce_fresh else "FLAT")
        den = max(abs(ce_fresh), abs(pe_fresh)) or 1
        gap = round(abs(ce_fresh - pe_fresh) / den * 100, 1)
        print("INCR_CHOI: SUPP_RES={} role={} fresh_CE={} fresh_PE={} -> "
              "{}_dominant by {}% ({}) | cumulative CE={} PE={}".format(
                  sr_key, sr_role or "n/a", ce_fresh_disp, pe_fresh_disp,
                  dom, gap, _dominance_hint(dom, sr_role),
                  ce_cum_disp, pe_cum_disp))
        dom_side = dom
    else:
        print("INCR_CHOI: SUPP_RES={} role={} first sighting (no prev cycle to "
              "diff) | cumulative CE={} PE={}".format(
                  sr_key, sr_role or "n/a", ce_cum_disp, pe_cum_disp))
        dom_side = "FLAT"

    # Return the raw CONTRACT measures so the Sensex-equivalent entry gate can
    # consume them (percentages are unit-invariant, so contracts vs USD makes no
    # difference to any comparison). Display stays USD-notional above.
    return {
        "sr_strike": sr_key,
        "ce_oi_total": float(ce_oi or 0.0),     # Sensex suppResCeOi_total
        "pe_oi_total": float(pe_oi or 0.0),     # Sensex suppResPeOi_total
        "ce_choi": float(ce_cum),               # Sensex suppResCeChOi (CEoich)
        "pe_choi": float(pe_cum),               # Sensex suppResPeChOi (PEoich)
        "fresh_ce": float(ce_fresh),            # Sensex fresh_CE
        "fresh_pe": float(pe_fresh),            # Sensex fresh_PE
        "fresh_known": bool(fresh_known),
        "fresh_dominant": dom_side,             # "CE" / "PE" / "FLAT"
    }


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


def derive_directional_bias(index_price, pcr, position, sr_line, role):
    """
    Derive the directional bias from the PCR together with the spot's role
    against the nearest-500 S/R line (Req 9.13).

    Position rule (the nearest-level model — spot vs the single S/R line;
    classified upstream by ``compute_support_resistance``):
      * spot at the line (``no_trade_zone``)        -> NEUTRAL / no-trade
      * spot ABOVE the line = SUPPORT (support_side) -> BULL permitted
      * spot BELOW the line = RESISTANCE (resistance_side) -> BEAR permitted

    e.g. spot 84800 < line 85000 -> RESISTANCE -> look for a BEAR trade.

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

    if index_price is None:
        return ("UNKNOWN", "unknown", "NO_ENTRY",
                "index price unavailable ({})".format(thresholds_note),
                pcr_label)
    try:
        float(index_price)
    except (TypeError, ValueError):
        return ("UNKNOWN", "unknown", "NO_ENTRY",
                "index price non-numeric ({})".format(thresholds_note),
                pcr_label)

    if position == "no_trade_zone":
        return ("NEUTRAL", "no_trade_zone", "NO_ENTRY",
                "spot at the {} S/R line {} (within no-trade buffer) ({})".format(
                    role, _fmt(sr_line), thresholds_note),
                pcr_label)

    if position == "support_side":
        # Spot above SUPPORT -> only a bull bias is permitted by position.
        provisional_pcr_bull = _pcr_is_finite(pcr) and float(pcr) > 1.0
        provisional_pcr_bull = provisional_pcr_bull or pcr == float("inf")
        if provisional_pcr_bull:
            decision = "WOULD_ENTER(bull, provisional)"
            reason = ("spot above SUPPORT {} AND {} agrees (bullish); {} - "
                      "provisional only".format(
                          _fmt(sr_line), pcr_label, thresholds_note))
        else:
            decision = "NO_ENTRY(bull-side, PCR not bullish)"
            reason = ("spot above SUPPORT {} but {} does not lean bullish; "
                      "{}".format(_fmt(sr_line), pcr_label, thresholds_note))
        return ("BULL", "support_side", decision, reason, pcr_label)

    # resistance_side: spot below RESISTANCE -> only a bear bias is permitted.
    provisional_pcr_bear = _pcr_is_finite(pcr) and float(pcr) < 1.0
    if provisional_pcr_bear:
        decision = "WOULD_ENTER(bear, provisional)"
        reason = ("spot below RESISTANCE {} AND {} agrees (bearish); {} - "
                  "provisional only".format(
                      _fmt(sr_line), pcr_label, thresholds_note))
    else:
        decision = "NO_ENTRY(bear-side, PCR not bearish)"
        reason = ("spot below RESISTANCE {} but {} does not lean bearish; "
                  "{}".format(_fmt(sr_line), pcr_label, thresholds_note))
    return ("BEAR", "resistance_side", decision, reason, pcr_label)


# ============================================================
# ENTRY GATING (Req 9.14-9.17) — Task 14.3
# ============================================================
def market_hours_permit(now_utc=None):
    """
    Market-hours entry policy hook (Req 9.16, 9.24).

    BTC daily options trade ~24/7 on Delta, so this permissively permits new
    entries at (almost) all times. The ONE exception is the pre-expiry lead
    window (Task 14.8, Req 9.24): during the final ``PRE_EXPIRY_LEAD_MINUTES``
    before the 12:00 UTC daily expiry (default 11:45:00–11:59:59 UTC) this
    returns ``False`` so no NEW entry is opened as the current daily contract
    winds down. ``evaluate_entry`` already calls ``market_hours_permit()`` as a
    gate, so returning ``False`` here surfaces a ``market_hours_closed`` blocker
    and suppresses the entry — the explicit human-facing pre-expiry log line is
    emitted by ``handle_expiry_rollover`` (Req 9.24/9.25).

    ``now_utc`` is optional and defaults to "now" (mirrors
    ``in_pre_expiry_window`` / ``getDailyExpiry`` handling); the default arg
    keeps the existing zero-argument callers backward-compatible. Returns
    ``True`` (entries permitted) outside the pre-expiry window.
    """
    if in_pre_expiry_window(now_utc):
        return False
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


# ============================================================
# SENSEX-EQUIVALENT BULL / BEAR ENTRY CRITERIA (ported 1:1)
# ============================================================
# Port of the Sensex entry gate (Strategy_Sensex_May_2026.py lines 3188-3243 and
# 3390). The Sensex condition is:
#
#   BULL: bull_direction_ok and _dominance_ok_bull and slCount != 2
#         and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0
#         and anchorLTP > SUPP_RES and isPcrInc
#   BEAR: (elif, so BULL wins ties) mirrored with anchorLTP < SUPP_RES / isPcrDecr
#
# Mapping of the Sensex inputs onto this bot:
#   anchorLTP        -> index_price (.DEXBTUSD spot; same anchor the S/R uses)
#   SUPP_RES         -> the resolved S/R level (nearest-500 line, or the
#                       nearest-to-spot listed strike when that line has no OI)
#   NOTRADEZONE      -> position == "no_trade_zone"
#   isPcrInc/Decr    -> the 17-strike INVARIANT-WINDOW flag + its streak
#                       (>= PCR_ENTRY_STREAK_MIN), per the user's choice
#   suppResCeChOi    -> ce_choi  (cumulative since contract start)
#   suppResPeChOi    -> pe_choi
#   suppResCeOi_total-> ce_oi_total (absolute OI at the S/R strike)
#   fresh_CE/PE      -> fresh_ce / fresh_pe (per-cycle flow)
#   IS_MORNING_WINDOW-> 17:30-18:03 IST (12:00-12:33 UTC), the first ~33 min of
#                       the contract while cumulative CHOI is still near zero
#   dt1.hour <= 15   -> no new entry after 16:30 IST (ENTRY_CUTOFF_IST_*)
#   slCount != 2     -> sl_count < MAX_SL_PER_DAY, per contract session
#   st == 0          -> not state["position_open"]


def is_morning_window(now_utc=None):
    """
    True inside the BTC equivalent of the Sensex 9:15-9:48 morning window: the
    first ``MORNING_WINDOW_END_UTC_MIN`` minutes of the contract session, which
    begins at 12:00 UTC / 17:30 IST (17:30-18:03 IST).

    Rationale (same as Sensex): the cumulative change-in-OI baseline is captured
    at the session start, so for the first few cycles CHOI is near zero and its
    percentage gaps are meaningless. LOGIC1 therefore leans on TOTAL OI
    direction (carried from the previous session's positioning) instead.
    """
    now = now_utc or datetime.now(REFERENCE_TIMEZONE)
    if now.hour != DAILY_EXPIRY_UTC_HOUR:
        return False
    return (MORNING_WINDOW_START_UTC_MIN <= now.minute
            < MORNING_WINDOW_END_UTC_MIN)


def minutes_to_expiry(now_utc=None):
    """
    Minutes remaining until this contract's 12:00 UTC expiry (1..1440).

    The BTC session runs 12:00 UTC -> 12:00 UTC, so the countdown WRAPS: at
    11:00 UTC 60 minutes remain, at 12:57 UTC (the evening of a fresh session)
    1383 minutes remain. Exactly ON the boundary a new session has just begun,
    so a full 1440 minutes remain rather than 0.
    """
    now = now_utc or datetime.now(REFERENCE_TIMEZONE)
    expiry_min = DAILY_EXPIRY_UTC_HOUR * 60
    now_min = now.hour * 60 + now.minute
    remaining = (expiry_min - now_min) % (24 * 60)
    return remaining or (24 * 60)


def entry_cutoff_passed(now_utc=None):
    """
    True once NEW entries are no longer authorised — inside the final
    ``ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY`` minutes before expiry (default 60,
    i.e. from 16:30 IST up to the 17:30 IST expiry).

    Measured as MINUTES REMAINING, not as a wall-clock comparison, because the
    session spans midnight. Entries stay authorised while STRICTLY more than the
    lead time remains, so the 16:30 IST cycle itself is still allowed and 16:33
    onward is blocked.

    Mirrors the Sensex ``dt1.hour <= 15`` cut-off.
    """
    return minutes_to_expiry(now_utc) < ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY


def _pct_gap(a, b):
    """Sensex-style percentage gap: |a-b| / max(|a|,|b|) * 100, rounded to 1dp.
    Returns 0.0 when both sides are zero (no information)."""
    den = max(abs(a), abs(b))
    if den <= 0:
        return 0.0
    return round(abs(a - b) / den * 100.0, 1)


def update_sr_dominance_streaks(sr_oi, sr_role, state):
    """
    Maintain the Sensex fresh-flow dominance streaks at the S/R strike and the
    ``isPE_DominantAtSupp_*`` / ``isCE_DominantAtRes_*`` flags they drive.

    PE-dominant-at-SUPPORT is the BULL confirmation (put writers defending the
    floor); CE-dominant-at-RESISTANCE is the BEAR confirmation (call writers
    capping the ceiling). A streak increments only while its own side stays
    dominant AT THE MATCHING ROLE, and resets to 0 otherwise — including when
    the role flips, so a streak never carries across a support/resistance change.

    When this cycle has no comparable previous cycle (``fresh_known`` False,
    e.g. the first cycle of a contract) BOTH streaks reset and the persisted
    fresh values go to 0, so the dominance gate stays conservative and blocks.

    Mutates ``state`` and returns a dict of the flags for logging/gating.
    """
    state = state if isinstance(state, dict) else {}
    sr_oi = sr_oi if isinstance(sr_oi, dict) else {}

    pe_streak = state.get("pe_supp_streak", 0) or 0
    ce_streak = state.get("ce_res_streak", 0) or 0

    if not sr_oi.get("fresh_known"):
        pe_streak = ce_streak = 0
        fresh_ce = fresh_pe = 0.0
    else:
        fresh_ce = sr_oi.get("fresh_ce") or 0.0
        fresh_pe = sr_oi.get("fresh_pe") or 0.0
        dom = sr_oi.get("fresh_dominant")
        if sr_role == "SUPPORT" and dom == "PE":
            pe_streak += 1
            ce_streak = 0
        elif sr_role == "RESISTANCE" and dom == "CE":
            ce_streak += 1
            pe_streak = 0
        else:
            pe_streak = ce_streak = 0

    state["pe_supp_streak"] = pe_streak
    state["ce_res_streak"] = ce_streak
    state["last_fresh_ce"] = fresh_ce
    state["last_fresh_pe"] = fresh_pe

    return {
        "pe_supp_streak": pe_streak,
        "ce_res_streak": ce_streak,
        "isPE_DominantAtSupp_once": pe_streak >= 1,
        "isPE_DominantAtSupp_twice": pe_streak >= 2,
        "isPE_DominantAtSupp_trice": pe_streak >= 3,
        "isCE_DominantAtRes_once": ce_streak >= 1,
        "isCE_DominantAtRes_twice": ce_streak >= 2,
        "isCE_DominantAtRes_trice": ce_streak >= 3,
        "last_fresh_ce": fresh_ce,
        "last_fresh_pe": fresh_pe,
    }


def evaluate_sensex_entry_criteria(index_price, sr_level, position, sr_role,
                                   sr_oi, dominance, pcr_flags, state,
                                   now_utc=None):
    """
    Evaluate the Sensex BULL/BEAR entry criteria for one cycle. Pure decision
    logic: places no orders, performs no network calls, prints nothing.

    ``pcr_flags`` must carry the 17-strike INVARIANT-WINDOW verdicts and streaks:
    ``is_inc``, ``is_dec``, ``inc_streak``, ``dec_streak``.

    Returns a dict with the mandatory legs, each LOGIC mode's verdict, the
    winning mode, the final bull/bear decision and a blocker list.
    """
    state = state if isinstance(state, dict) else {}
    sr_oi = sr_oi if isinstance(sr_oi, dict) else {}
    dominance = dominance if isinstance(dominance, dict) else {}
    pcr_flags = pcr_flags if isinstance(pcr_flags, dict) else {}

    ce_choi = sr_oi.get("ce_choi") or 0.0
    pe_choi = sr_oi.get("pe_choi") or 0.0
    ce_total = sr_oi.get("ce_oi_total") or 0.0
    pe_total = sr_oi.get("pe_oi_total") or 0.0

    # Sensex `_choi_valid`: a NEGATIVE side means writers are UNWINDING, which
    # inflates the gap without carrying trapped-writer conviction. When either
    # side is not building, the gap is forced to 0 — which fails every LOGIC
    # threshold (10/15%), so no trade can fire. This is a hard filter.
    choi_valid = (ce_choi > 0 and pe_choi > 0)
    choi_pct = _pct_gap(ce_choi, pe_choi) if choi_valid else 0.0
    oi_pct = _pct_gap(ce_total, pe_total)

    # ----- Mandatory direction legs (price vs S/R + PCR trend) -----
    # The no-trade guard MUST come first so a dead-band cycle blocks both sides.
    in_trade_zone = (position != "no_trade_zone") and (sr_level is not None)
    pcr_inc_ok = bool(pcr_flags.get("is_inc")) and \
        (pcr_flags.get("inc_streak") or 0) >= PCR_ENTRY_STREAK_MIN
    pcr_dec_ok = bool(pcr_flags.get("is_dec")) and \
        (pcr_flags.get("dec_streak") or 0) >= PCR_ENTRY_STREAK_MIN

    above_level = in_trade_zone and index_price is not None and \
        float(index_price) > float(sr_level)
    below_level = in_trade_zone and index_price is not None and \
        float(index_price) < float(sr_level)

    bull_mandatory = in_trade_zone and above_level and pcr_inc_ok
    bear_mandatory = in_trade_zone and below_level and pcr_dec_ok

    morning = is_morning_window(now_utc)

    # ----- LOGIC1: morning trap (total-OI direction + CHOI trap >= 15%) -----
    logic1_bull = (morning and bull_mandatory and (ce_total > pe_total)
                   and (pe_choi > ce_choi and choi_pct >= CHOI_MORNING_PCT))
    logic1_bear = (morning and bear_mandatory and (pe_total > ce_total)
                   and (ce_choi > pe_choi and choi_pct >= CHOI_MORNING_PCT))

    # ----- LOGIC2: trend convergence (CHOI >= 10% AND total OI >= 2%) -----
    logic2_bull = (bull_mandatory
                   and (pe_choi > ce_choi and choi_pct >= CHOI_CONVERGE_PCT)
                   and (pe_total > ce_total and oi_pct >= OI_CONVERGE_PCT))
    logic2_bear = (bear_mandatory
                   and (ce_choi > pe_choi and choi_pct >= CHOI_CONVERGE_PCT)
                   and (ce_total > pe_total and oi_pct >= OI_CONVERGE_PCT))

    # ----- LOGIC3: CHOI trap >= 15% (ALWAYS observation-only) -----
    # Note the deliberate inversion vs LOGIC2: bull wants the CALL side dominant
    # cumulatively (trapped call writers being squeezed as price holds above
    # support), while the dominance gate separately demands PUTs dominant in the
    # FRESH flow. Contrarian by design, which is why it never trades.
    logic3_bull = (bull_mandatory and (ce_choi > pe_choi)
                   and choi_pct >= CHOI_TRAP_PCT)
    logic3_bear = (bear_mandatory and (ce_choi < pe_choi)
                   and choi_pct >= CHOI_TRAP_PCT)

    # ----- Priority: first logic with a tradeable signal wins -----
    if logic1_bull or logic1_bear:
        bull_dir_ok, bear_dir_ok = logic1_bull, logic1_bear
        mode = "LOGIC1_MORNING_TRAP"
    elif logic2_bull or logic2_bear:
        bull_dir_ok, bear_dir_ok = logic2_bull, logic2_bear
        mode = "LOGIC2_TREND_CONVERGENCE"
    else:
        bull_dir_ok, bear_dir_ok = logic3_bull, logic3_bear
        mode = "LOGIC3_CHOI_15PCT"

    # ----- Fresh-flow dominance confirmation -----
    if USE_CHOI_DOMINANCE_GATE:
        dom_ok_bull = bool(dominance.get("isPE_DominantAtSupp_once")) and \
            (dominance.get("last_fresh_pe") or 0.0) >= 0
        dom_ok_bear = bool(dominance.get("isCE_DominantAtRes_once")) and \
            (dominance.get("last_fresh_ce") or 0.0) >= 0
    else:
        dom_ok_bull = dom_ok_bear = True

    # ----- Shared risk/state gates -----
    sl_count = state.get("untrailed_sl_count", 0) or 0
    sl_halt = sl_count >= MAX_SL_PER_DAY
    mins_left = minutes_to_expiry(now_utc)
    after_cutoff = entry_cutoff_passed(now_utc)
    position_open = bool(state.get("position_open"))

    common_ok = (not sl_halt) and (not after_cutoff) and in_trade_zone \
        and (not position_open)

    bull_enter = bool(common_ok and bull_dir_ok and dom_ok_bull
                      and above_level and pcr_inc_ok)
    # BEAR is an `elif` in Sensex, so BULL wins when both somehow qualify.
    bear_enter = bool(common_ok and (not bull_enter) and bear_dir_ok
                      and dom_ok_bear and below_level and pcr_dec_ok)

    # LOGIC3 can signal but must never route to a live order.
    tradeable_mode = not (mode == "LOGIC3_CHOI_15PCT"
                          and LOGIC3_OBSERVATION_ONLY)

    blockers = []
    if sl_halt:
        blockers.append("sl_halt({}/{})".format(sl_count, MAX_SL_PER_DAY))
    if after_cutoff:
        blockers.append(
            "after_entry_cutoff({:02d}:{:02d}_IST,{}min_to_expiry)".format(
                ENTRY_CUTOFF_IST_HOUR, ENTRY_CUTOFF_IST_MINUTE, mins_left))
    if not in_trade_zone:
        blockers.append("no_trade_zone(9.14)")
    if position_open:
        blockers.append("position_already_open(9.16)")
    if not (bull_enter or bear_enter):
        if not (pcr_inc_ok or pcr_dec_ok):
            blockers.append("pcr17_invariant_streak<{}".format(
                PCR_ENTRY_STREAK_MIN))
        elif not choi_valid:
            blockers.append("choi_not_building(CE>0 and PE>0 required)")
        elif not (bull_dir_ok or bear_dir_ok):
            blockers.append("no_logic_mode_qualified")
        elif pcr_inc_ok and not dom_ok_bull:
            blockers.append("choi_dominance_bull(need PE dominant, fresh_PE>=0)")
        elif pcr_dec_ok and not dom_ok_bear:
            blockers.append("choi_dominance_bear(need CE dominant, fresh_CE>=0)")
    if (bull_enter or bear_enter) and not tradeable_mode:
        blockers.append("logic3_observation_only")

    return {
        "bull_enter": bull_enter,
        "bear_enter": bear_enter,
        "mode": mode,
        "tradeable_mode": tradeable_mode,
        "live_eligible": bool((bull_enter or bear_enter) and tradeable_mode),
        "in_trade_zone": in_trade_zone,
        "above_level": above_level,
        "below_level": below_level,
        "pcr_inc_ok": pcr_inc_ok,
        "pcr_dec_ok": pcr_dec_ok,
        "bull_mandatory": bull_mandatory,
        "bear_mandatory": bear_mandatory,
        "logic1": (logic1_bull, logic1_bear),
        "logic2": (logic2_bull, logic2_bear),
        "logic3": (logic3_bull, logic3_bear),
        "dom_ok_bull": dom_ok_bull,
        "dom_ok_bear": dom_ok_bear,
        "choi_valid": choi_valid,
        "choi_pct": choi_pct,
        "oi_pct": oi_pct,
        "morning_window": morning,
        "sl_count": sl_count,
        "sl_halt": sl_halt,
        "mins_to_expiry": mins_left,
        "after_cutoff": after_cutoff,
        "blockers": blockers,
    }


def log_sensex_entry_criteria(result, sr_level, sr_role, sr_oi, dominance,
                              pcr_flags):
    """Log the Sensex-equivalent entry evaluation (observation only)."""
    r = result
    print()
    print("==== ENTRY CRITERIA (Sensex-equivalent) {} ====".format(_now_ist()))
    print("PCR17_INVARIANT gate: inc={} (streak={}) dec={} (streak={}) "
          "min_streak={} -> bull_pcr_ok={} bear_pcr_ok={}".format(
              bool(pcr_flags.get("is_inc")), pcr_flags.get("inc_streak") or 0,
              bool(pcr_flags.get("is_dec")), pcr_flags.get("dec_streak") or 0,
              PCR_ENTRY_STREAK_MIN, r["pcr_inc_ok"], r["pcr_dec_ok"]))
    print("MANDATORY: in_trade_zone={} spot_vs_SR: above={} below={} "
          "(SUPP_RES={} role={}) -> bull_mandatory={} bear_mandatory={}".format(
              r["in_trade_zone"], r["above_level"], r["below_level"],
              _fmt(sr_level), sr_role or "n/a",
              r["bull_mandatory"], r["bear_mandatory"]))
    print("CHOI/OI at S/R: CEoich={} PEoich={} choi_valid={} choi_pct={}% | "
          "CE_total={} PE_total={} oi_pct={}%".format(
              _fmt(sr_oi.get("ce_choi"), 1), _fmt(sr_oi.get("pe_choi"), 1),
              r["choi_valid"], r["choi_pct"],
              _fmt(sr_oi.get("ce_oi_total"), 1),
              _fmt(sr_oi.get("pe_oi_total"), 1), r["oi_pct"]))
    print("LOGIC1_MORNING_TRAP (window={}): bull={} bear={}  [CHOI>={}%]".format(
        r["morning_window"], r["logic1"][0], r["logic1"][1],
        CHOI_MORNING_PCT))
    print("LOGIC2_TREND_CONVERGENCE: bull={} bear={}  [CHOI>={}%, OI>={}%]"
          .format(r["logic2"][0], r["logic2"][1], CHOI_CONVERGE_PCT,
                  OI_CONVERGE_PCT))
    print("LOGIC3_CHOI_15PCT (observation-only): bull={} bear={}  [CHOI>={}%]"
          .format(r["logic3"][0], r["logic3"][1], CHOI_TRAP_PCT))
    print("ENTRY_MODE = {} (tradeable={})".format(r["mode"],
                                                  r["tradeable_mode"]))
    print("CHOI_DOM_GATE: gate_on={} bull_ok={} bear_ok={} | "
          "isPE_DomSupp_once={} (streak={}) fresh_PE={} | "
          "isCE_DomRes_once={} (streak={}) fresh_CE={}".format(
              USE_CHOI_DOMINANCE_GATE, r["dom_ok_bull"], r["dom_ok_bear"],
              dominance.get("isPE_DominantAtSupp_once"),
              dominance.get("pe_supp_streak"),
              _fmt(dominance.get("last_fresh_pe"), 1),
              dominance.get("isCE_DominantAtRes_once"),
              dominance.get("ce_res_streak"),
              _fmt(dominance.get("last_fresh_ce"), 1)))
    print("RISK_GATES: sl_count={}/{} halt={} | mins_to_expiry={} "
          "cutoff_lead={}min ({:02d}:{:02d} IST) after_cutoff={}".format(
              r["sl_count"], MAX_SL_PER_DAY, r["sl_halt"],
              r["mins_to_expiry"], ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY,
              ENTRY_CUTOFF_IST_HOUR, ENTRY_CUTOFF_IST_MINUTE,
              r["after_cutoff"]))
    verdict = ("BULL" if r["bull_enter"] else
               "BEAR" if r["bear_enter"] else "NONE")
    print("ENTRY_DECISION = {} (live_eligible={}){}".format(
        verdict, r["live_eligible"],
        "  blockers=" + ";".join(r["blockers"]) if r["blockers"] else ""))
    print()


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


# ============================================================
# PER-CONTRACT STATE RESET (daily rollover at 12:00 UTC / 17:30 IST)
# ============================================================
# Open interest is a PER-CONTRACT quantity. When the BTC daily contract rolls at
# 12:00 UTC (17:30 IST) the new expiry's chain starts from near-zero OI and
# builds through its own session, so NOTHING derived from the expired contract's
# OI is comparable to the new one. Every cross-cycle series / flag / streak must
# therefore start fresh at the boundary.
#
# Without this reset the first cycles of a new contract inherit PCR windows, SEQ
# verdicts, dominance streaks, the invariant-window OI history and the fresh-flow
# diff from a DEAD instrument — which fabricates trend signals out of nothing but
# the instrument switch (e.g. the expired chain's OI vanishing reads as a
# multi-million "unwind" at the S/R strike).
#
# ``roll_contract_log`` only rotates the OUTPUT FILE; this resets the STATE.
#
# Position / monitoring keys are deliberately NOT cleared — an open position must
# never be forgotten across a rollover (the pre-expiry square-off at 11:45 UTC
# handles closing it, Req 9.25). ``current_expiry``/``squared_off_for`` are owned
# by ``handle_expiry_rollover`` and are likewise left alone.

# Cross-cycle state that is only meaningful WITHIN one contract -> key: default.
_PER_CONTRACT_STATE_DEFAULTS = {
    # Snapshot + diff inputs (prev-cycle chain for OI deltas / PCR trend).
    "snapshot": None,
    "pcr": None,
    "pcr_full": None,
    "pcr_trend": None,
    "oi_deltas": None,
    "bias": None,
    "position": None,
    "decision": None,
    "entry": None,
    # ATM-shift tracking + the rolling 17 / central-9 OI-PCR windows.
    "prev_atm": None,
    "atm_not_shifted_count": 0,
    "avg_oipcr_list": [],
    "avg_oipcr9_list": [],
    "is_pcr_seq_inc": False,
    "is_pcr_seq_dec": False,
    "is_pcr9_seq_inc": False,
    "is_pcr9_seq_dec": False,
    "pcr_inc_streak": 0,
    "pcr_dec_streak": 0,
    "pcr9_inc_streak": 0,
    "pcr9_dec_streak": 0,
    # Invariant-window track (raw-chain OI snapshots re-measured on current
    # strikes) + its own flags/streaks.
    "chain_oi_history": [],
    "is_pcr9_inc_inv": False,
    "is_pcr9_dec_inv": False,
    "is_pcr_inc_inv": False,
    "is_pcr_dec_inv": False,
    "pcr9_inc_streak_inv": 0,
    "pcr9_dec_streak_inv": 0,
    "pcr_inc_streak_inv": 0,
    "pcr_dec_streak_inv": 0,
    # Full-chain (total) PCR track.
    "total_pcr": None,
    "total_pcr_list": [],
    "is_total_pcr_inc": False,
    "is_total_pcr_dec": False,
    "total_pcr_inc_streak": 0,
    "total_pcr_dec_streak": 0,
    # once/twice/thrice dominance streaks for the 17- and 9-strike PCRs.
    "pcr17_up_streak": 0,
    "pcr17_down_streak": 0,
    "pcr9_up_streak": 0,
    "pcr9_down_streak": 0,
    # Raw-chain OI references: prev cycle (fresh flow) + contract-start
    # (cumulative CHOI). Clearing the baseline label forces a re-capture.
    "prev_chain_oi": None,
    "baseline_chain_oi": None,
    "baseline_contract": None,
    # Entry gating: the confirmation streak and the per-contract trade count
    # (one BTC daily contract == one trading "day" for MAX_TRADES_PER_DAY).
    "entry_confirm_count": 0,
    "trades_today": 0,
    # Sensex-equivalent entry criteria state: fresh-flow dominance streaks at
    # the S/R strike, the persisted per-cycle flow, and the untrailed-SL counter
    # driving the 2-SL halt. All are per-session, so they reset at the rollover.
    "pe_supp_streak": 0,
    "ce_res_streak": 0,
    "last_fresh_ce": 0.0,
    "last_fresh_pe": 0.0,
    # Sensex `slCount`. Named explicitly to avoid confusion with
    # ``sl_confirm_count`` (the per-poll SL confirmation TICK counter).
    "untrailed_sl_count": 0,
    # Session trade-count rule: set True when a trade hits TARGET (a win), which
    # ends the session for new entries (Sensex st=-1). A loss leaves it False so
    # re-entry is allowed up to MAX_SL_PER_DAY. Resets at the contract rollover.
    "session_won": False,
    "sensex_entry": None,
}


def reset_state_on_contract_roll(state, label=None):
    """
    Clear all per-contract cross-cycle state when the BTC daily contract rolls
    (12:00 UTC / 17:30 IST), so no PCR series, SEQ/dominance streak, invariant
    window or OI diff ever spans two different expiries.

    Mutates ``state`` in place and returns True when a reset was performed. On
    the very first call of a process no reset happens (there is no prior state to
    invalidate) — the current contract label is simply recorded.

    Fully defensive: never raises, so a label-resolution hiccup can't kill the
    signal loop (it just skips the reset for this cycle).
    """
    if not isinstance(state, dict):
        return False
    try:
        curr = label or _current_contract_label()
    except Exception as exc:  # noqa: BLE001 - label is best-effort; never fatal
        print("CONTRACT_RESET_WARN [{ts}] could not resolve the contract label; "
              "skipping the per-contract state reset this cycle: {typ}: {msg}"
              .format(ts=_now_ist(), typ=type(exc).__name__, msg=exc))
        return False

    prev = state.get("contract_label")
    if prev == curr:
        return False

    state["contract_label"] = curr
    if prev is None:
        # Process start: nothing carried yet, so there is nothing to invalidate.
        return False

    cleared = [key for key in _PER_CONTRACT_STATE_DEFAULTS if key in state]
    for key, default in _PER_CONTRACT_STATE_DEFAULTS.items():
        state[key] = list(default) if isinstance(default, list) else default

    print("CONTRACT_RESET [{ts}] contract rolled {prev} -> {curr}: cleared {n} "
          "per-contract state key(s). All PCR windows (17/9/invariant/total), "
          "SEQ + dominance streaks, OI diffs and the entry confirm/trade count "
          "restart from zero — OI is per-contract, so nothing from {prev} is "
          "comparable to {curr}.".format(
              ts=_now_ist(), prev=prev, curr=curr, n=len(cleared)))
    if state.get("position_open"):
        print("CONTRACT_RESET_WARN [{ts}] a position is still flagged OPEN "
              "across the rollover; position/monitoring state was PRESERVED "
              "(not reset) so it cannot be forgotten.".format(ts=_now_ist()))
    return True


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

    # ---- Expiry handling + daily rollover (Task 14.8, Req 9.23-9.26) ----
    # Run ONCE per cycle, before anything else: re-resolve the daily expiry and
    # detect/log a rollover to a new contract (9.23/9.26), and — at the start of
    # the 11:45-12:00 UTC pre-expiry window — square off any open position
    # (9.25). In OBSERVATION_MODE this logs WOULD_HAVE_SQUARED_OFF only. It is
    # fully defensive and never raises. When a LIVE square-off closes the
    # position, ``carried["position_open"]`` is flipped False here so the monitor
    # branch below is skipped and the cycle falls through to entry evaluation —
    # which is then suppressed by ``market_hours_permit`` during the window
    # (9.24).
    expiry_result = handle_expiry_rollover(client, carried)

    # ---- Per-contract state reset (12:00 UTC / 17:30 IST rollover) ----
    # OI is per-contract: the new expiry's chain starts near zero and builds on
    # its own. Clear every cross-cycle PCR window / SEQ + dominance streak /
    # invariant-window history / OI diff so none of them ever spans two
    # expiries. Runs BEFORE the monitor branch so the boundary is honoured on
    # every code path. Position/monitoring state is preserved.
    reset_state_on_contract_roll(carried)

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
            # ATM-shift + rolling OI-PCR state carried forward unchanged while
            # a position is being monitored (no new snapshot this branch).
            "prev_atm": carried.get("prev_atm"),
            "avg_oipcr_list": carried.get("avg_oipcr_list"),
            "avg_oipcr9_list": carried.get("avg_oipcr9_list"),
            "atm_not_shifted_count": carried.get("atm_not_shifted_count"),
            "is_pcr_seq_inc": carried.get("is_pcr_seq_inc", False),
            "is_pcr_seq_dec": carried.get("is_pcr_seq_dec", False),
            "is_pcr9_seq_inc": carried.get("is_pcr9_seq_inc", False),
            "is_pcr9_seq_dec": carried.get("is_pcr9_seq_dec", False),
            "pcr_inc_streak": carried.get("pcr_inc_streak", 0),
            "pcr_dec_streak": carried.get("pcr_dec_streak", 0),
            "pcr9_inc_streak": carried.get("pcr9_inc_streak", 0),
            "pcr9_dec_streak": carried.get("pcr9_dec_streak", 0),
            "chain_oi_history": carried.get("chain_oi_history"),
            "is_pcr9_inc_inv": carried.get("is_pcr9_inc_inv", False),
            "is_pcr9_dec_inv": carried.get("is_pcr9_dec_inv", False),
            "is_pcr_inc_inv": carried.get("is_pcr_inc_inv", False),
            "is_pcr_dec_inv": carried.get("is_pcr_dec_inv", False),
            "pcr9_inc_streak_inv": carried.get("pcr9_inc_streak_inv", 0),
            "pcr9_dec_streak_inv": carried.get("pcr9_dec_streak_inv", 0),
            "pcr_inc_streak_inv": carried.get("pcr_inc_streak_inv", 0),
            "pcr_dec_streak_inv": carried.get("pcr_dec_streak_inv", 0),
            "total_pcr": carried.get("total_pcr"),
            "total_pcr_list": carried.get("total_pcr_list"),
            "is_total_pcr_inc": carried.get("is_total_pcr_inc", False),
            "is_total_pcr_dec": carried.get("is_total_pcr_dec", False),
            "total_pcr_inc_streak": carried.get("total_pcr_inc_streak", 0),
            "total_pcr_dec_streak": carried.get("total_pcr_dec_streak", 0),
            "pcr_full": carried.get("pcr_full"),
            "pcr17_up_streak": carried.get("pcr17_up_streak", 0),
            "pcr17_down_streak": carried.get("pcr17_down_streak", 0),
            "pcr9_up_streak": carried.get("pcr9_up_streak", 0),
            "pcr9_down_streak": carried.get("pcr9_down_streak", 0),
            "prev_chain_oi": carried.get("prev_chain_oi"),
            "baseline_chain_oi": carried.get("baseline_chain_oi"),
            "baseline_contract": carried.get("baseline_contract"),
            # Sensex-equivalent entry-criteria state (unchanged while monitoring
            # — no new snapshot is read on this branch).
            "pe_supp_streak": carried.get("pe_supp_streak", 0),
            "ce_res_streak": carried.get("ce_res_streak", 0),
            "last_fresh_ce": carried.get("last_fresh_ce", 0.0),
            "last_fresh_pe": carried.get("last_fresh_pe", 0.0),
            "untrailed_sl_count": carried.get("untrailed_sl_count", 0),
            "session_won": carried.get("session_won", False),
            "sensex_entry": carried.get("sensex_entry"),
            # Current contract label — MUST be carried so a rollover that happens
            # while a position is being monitored is still detected next cycle.
            "contract_label": carried.get("contract_label"),
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
            # Expiry/rollover state (Task 14.8) — persist across cycles so the
            # new-contract detection and per-expiry square-off marker survive.
            "current_expiry": carried.get("current_expiry"),
            "squared_off_for": carried.get("squared_off_for"),
            "expiry": expiry_result,
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

    # 8. Nearest-500 S/R line + role from the anchor (Req 9.13/9.14). This
    #    structural 500 level drives role / no-trade / bias (kept stable).
    sr_line, sr_role, sr_position, ntl, nth = compute_support_resistance(
        index_price)

    # 8b. Make the S/R level a strike that actually carries OI. The 500 line
    #     above is the level; OI never chooses WHICH 500 level. But when that
    #     line is unavailable — unlisted (the x500 levels on BTC's 200 grid) or
    #     listed with zero OI — fall back to the listed strike NEAREST THE SPOT
    #     that has OI, and re-derive role / no-trade / side from spot vs THAT
    #     strike (spot below it => RESISTANCE, spot above it => SUPPORT).
    #     Computed here, LOGGED below next to the SUPP_RES line (step 11).
    sr_strike, sr_fallback, sr_note = resolve_sr_strike(
        sr_line, index_price, chain)
    if sr_fallback:
        # ROLE comes from spot vs the SUBSTITUTED strike (the user's rule):
        #   spot BELOW it -> RESISTANCE;  spot ABOVE it -> SUPPORT.
        # NO_TRADE_ZONE_BUFFER is 0, so the dead band is disabled and this is
        # simply which side of the level price sits on. Should the buffer ever be
        # raised again it stays anchored to the structural 500 LINE (ntl/nth
        # untouched), never to the substituted strike — that strike is always
        # within one strike step of spot, so a band around IT would veto a large
        # share of cycles purely as a strike-grid artefact.
        sr_role, sr_position = _role_vs_level(index_price, sr_strike)
        if NO_TRADE_ZONE_BUFFER and ntl <= index_price <= nth:
            sr_position = "no_trade_zone"

    # 9. Consecutive same-strike PCR change across cycles (Req 9.10).
    pcr_trend = update_pcr_trend(prev_pcr, pcr, prev_pcr_trend)

    # 10. Directional bias from PCR + spot position vs the S/R line (Req 9.13).
    #     Does NOT hard-gate on the [NEEDS INPUT] PCR thresholds.
    bias, position, decision, reason, pcr_label = derive_directional_bias(
        index_price, pcr, sr_position, sr_line, sr_role)

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
    log_option_chain_table(snapshot, oi_deltas, pcr,
                           index_price=index_price, perp_price=perp_price)

    # ATM-shift tracking + rolling OI-PCR accumulation (Sensex-equivalent
    # summary, Req 9.7/9.10). The full-17 and central-9 OI PCRs are collected
    # while the ATM strike holds steady across cycles and reset on a shift.
    pcr_full = helper.computePCR(snapshot, central=17)
    curr_atm = snapshot.get("atm_strike")
    prev_atm = carried.get("prev_atm")
    is_atm_shift = prev_atm is not None and prev_atm != curr_atm

    # Append this cycle's PCRs onto the carried windows (full-17 + central-9),
    # mirroring the Sensex append that happens BEFORE shift handling.
    avg_oipcr_list = list(carried.get("avg_oipcr_list") or [])
    avg_oipcr9_list = list(carried.get("avg_oipcr9_list") or [])
    if _pcr_is_finite(pcr_full):
        avg_oipcr_list.append(pcr_full)
    if _pcr_is_finite(pcr):
        avg_oipcr9_list.append(pcr)

    if is_atm_shift:
        # ATM strike moved: discard the stale run, keep only the newest value
        # (Sensex `avgOiPcrList2[-1:]`), and restart the consecutive counter.
        map_strike = {prev_atm: curr_atm}
        avg_oipcr_list = avg_oipcr_list[-1:]
        avg_oipcr9_list = avg_oipcr9_list[-1:]
        atm_not_shifted_count = 1
    else:
        map_strike = {curr_atm: curr_atm}
        atm_not_shifted_count = (carried.get("atm_not_shifted_count") or 0) + 1

    # Log the summary (pre-trim windows, as Sensex prints), then run the
    # Sensex-equivalent consecutive inc/dec detection + rolling-window trim.
    log_atm_shift_summary(snapshot, pcr_full, pcr, is_atm_shift, map_strike,
                          avg_oipcr_list, avg_oipcr9_list,
                          atm_not_shifted_count)

    (avg_oipcr_list, avg_oipcr9_list,
     is_pcr_seq_inc, is_pcr_seq_dec,
     is_pcr9_seq_inc, is_pcr9_seq_dec) = update_pcr_trend_window(
         avg_oipcr_list, avg_oipcr9_list,
         atm_not_shifted_count, is_atm_shift)

    # Consecutive-occurrence counters for the SEQ verdicts: how many cycles in a
    # row isPcrInc / isPcrDecr / is9PcrInc / is9PcrDecr has stayed True. A longer
    # run = a more sustained bullish/bearish PCR trend; the counter resets to 0
    # the moment the verdict is disqualified. Observation-only logging.
    pcr_inc_streak = _update_flag_streak(
        is_pcr_seq_inc, carried.get("pcr_inc_streak"))
    pcr_dec_streak = _update_flag_streak(
        is_pcr_seq_dec, carried.get("pcr_dec_streak"))
    pcr9_inc_streak = _update_flag_streak(
        is_pcr9_seq_inc, carried.get("pcr9_inc_streak"))
    pcr9_dec_streak = _update_flag_streak(
        is_pcr9_seq_dec, carried.get("pcr9_dec_streak"))
    _log_seq_streaks("PCR9_SEQ", pcr9_inc_streak, pcr9_dec_streak)
    _log_seq_streaks("PCR_SEQ", pcr_inc_streak, pcr_dec_streak)

    # ---- Parallel INVARIANT-WINDOW PCR trend (observation-only) ------------
    # Re-measure the last PCR_TREND_WINDOW cycles' OI over the CURRENT strike
    # set, so an ATM shift cannot fake a PCR move. The existing series/flags
    # above are untouched; these are additional, independently-streaked signals.
    # NOTE: `curr_chain_oi` (slim raw-chain OI map) is built HERE because it is
    # the first consumer; the S/R-OI block and the carried state reuse it below.
    curr_chain_oi = _chain_oi_map(chain)
    chain_oi_history = list(carried.get("chain_oi_history") or [])
    chain_oi_history.append(curr_chain_oi)
    chain_oi_history = chain_oi_history[-PCR_TREND_WINDOW:]

    strikes_17 = snapshot.get("strikes") or []
    strikes_9 = helper._central_strikes(snapshot, 9)
    series_17 = invariant_pcr_series(chain_oi_history, strikes_17)
    series_9 = invariant_pcr_series(chain_oi_history, strikes_9)

    if series_9:
        print("avgOiPcr9List2_InvariantWindow  = {}  (central 9, re-measured on "
              "current strikes)".format([round(x, 4) for x in series_9]))
    if series_17:
        print("avgOiPcrList2_InvariantWindow   = {}  (full 17, re-measured on "
              "current strikes)".format([round(x, 4) for x in series_17]))

    (is_pcr9_inc_inv, is_pcr9_dec_inv,
     pcr9_inc_streak_inv, pcr9_dec_streak_inv) = log_invariant_pcr_trend(
         "9", series_9,
         carried.get("pcr9_inc_streak_inv"), carried.get("pcr9_dec_streak_inv"))
    (is_pcr_inc_inv, is_pcr_dec_inv,
     pcr_inc_streak_inv, pcr_dec_streak_inv) = log_invariant_pcr_trend(
         "17", series_17,
         carried.get("pcr_inc_streak_inv"), carried.get("pcr_dec_streak_inv"))

    # ---- FULL-CHAIN (total) PCR, observation-only --------------------------
    # Σ PE OI / Σ CE OI over EVERY listed strike. No ATM dependency, so no reset
    # and no window artifact — but it includes far-OTM strangle OI, which dilutes
    # the near-the-money directional read. Kept purely for comparison against the
    # 9/17-strike PCRs over the observation period.
    total_pcr, t_ce, t_pe, t_ce_usd, t_pe_usd = total_chain_pcr(chain)
    total_pcr_list = list(carried.get("total_pcr_list") or [])
    if _pcr_is_finite(total_pcr):
        total_pcr_list.append(total_pcr)
    total_pcr_list = total_pcr_list[-TOTAL_PCR_WINDOW:]

    print("TOTAL_PCR (full chain) = {}  [total CE_OI= {}  total PE_OI= {}]"
          .format(_fmt(total_pcr, 4),
                  _fmt_oi_usd(t_ce_usd) or _fmt(t_ce, 1),
                  _fmt_oi_usd(t_pe_usd) or _fmt(t_pe, 1)))
    print("totalPcrList = {}  (full chain, last {} cycles)".format(
        [round(x, 4) for x in total_pcr_list], TOTAL_PCR_WINDOW))

    (is_total_pcr_inc, is_total_pcr_dec,
     total_pcr_inc_streak, total_pcr_dec_streak) = log_total_pcr_trend(
         total_pcr_list,
         carried.get("total_pcr_inc_streak"),
         carried.get("total_pcr_dec_streak"))

    # Consecutive PCR rise/fall dominance streaks (once/twice/thrice), tracked
    # separately for the full-17 PCR and the central-9 PCR (Sensex-style). A
    # rising PCR -> PE dominance (bull lean); falling -> CE dominance (bear
    # lean). Both reset on an ATM-strike shift.
    pcr17_up_streak, pcr17_down_streak = _update_dominance_streak(
        pcr_full, carried.get("pcr_full"),
        carried.get("pcr17_up_streak", 0) or 0,
        carried.get("pcr17_down_streak", 0) or 0, is_atm_shift)
    pcr9_up_streak, pcr9_down_streak = _update_dominance_streak(
        pcr, prev_pcr,
        carried.get("pcr9_up_streak", 0) or 0,
        carried.get("pcr9_down_streak", 0) or 0, is_atm_shift)
    if (pcr17_up_streak or pcr17_down_streak
            or pcr9_up_streak or pcr9_down_streak):
        print()
    _log_pcr_dominance("PCR17_DOMINANCE", pcr17_up_streak, pcr17_down_streak)
    _log_pcr_dominance("PCR9_DOMINANCE", pcr9_up_streak, pcr9_down_streak)

    log_support_resistance(index_price, sr_line, sr_role, sr_position,
                           ntl, nth, sr_strike=sr_strike,
                           fallback=sr_fallback, fallback_note=sr_note)

    # CE/PE OI + OI-change detail AT the S/R line (Sensex-equivalent). The line
    # is the plain nearest-500 level; OI never influences it. The raw chain is
    # passed so the OI can be read at the nearest LISTED strike (the 200-grid
    # snapshot lacks the interim strikes), along with the previous cycle's
    # raw-chain OI so the change is diffed at that same strike.
    # `curr_chain_oi` (built above) is carried forward for the next cycle's diff.

    # Contract-start OI baseline for the CUMULATIVE change-in-OI measure. Delta
    # has no since-day-start OI-change column, so we capture the chain's OI at
    # the FIRST cycle of each contract day and diff against it. When the daily
    # contract rolls (12:00 UTC / 17:30 IST) the label changes and the baseline
    # re-captures, so cumulative CHOI automatically resets per contract.
    contract_label = _current_contract_label()
    if carried.get("baseline_contract") != contract_label:
        baseline_chain_oi = curr_chain_oi
        print("CHOI_BASELINE [{ts}] captured contract-start OI baseline for "
              "{lbl} ({n} strikes); cumulative CHOI resets from here.".format(
                  ts=_now_ist(), lbl=contract_label, n=len(curr_chain_oi)))
    else:
        baseline_chain_oi = carried.get("baseline_chain_oi") or curr_chain_oi

    sr_oi = log_supp_res_oi(
        snapshot, sr_strike, sr_line, snapped=sr_fallback,
        chain=chain, prev_chain_oi=carried.get("prev_chain_oi"),
        baseline_chain_oi=baseline_chain_oi, sr_role=sr_role)

    # ---- Sensex-equivalent BULL/BEAR entry criteria (observation only) ----
    # Fresh-flow dominance streaks at the S/R strike (isPE_DominantAtSupp_* /
    # isCE_DominantAtRes_*), then the full LOGIC1/2/3 entry evaluation. The
    # directional PCR leg uses the 17-strike INVARIANT-WINDOW flag + streak.
    sr_dominance = update_sr_dominance_streaks(sr_oi, sr_role, carried)
    pcr_entry_flags = {
        "is_inc": is_pcr_inc_inv,
        "is_dec": is_pcr_dec_inv,
        "inc_streak": pcr_inc_streak_inv,
        "dec_streak": pcr_dec_streak_inv,
    }
    sensex_entry = evaluate_sensex_entry_criteria(
        index_price, sr_strike, sr_position, sr_role, sr_oi, sr_dominance,
        pcr_entry_flags, carried)
    log_sensex_entry_criteria(sensex_entry, sr_strike, sr_role, sr_oi,
                              sr_dominance, pcr_entry_flags)

    # ---- Session trade-count rule + trade plan (Sensex-equivalent) --------
    # Rule (user): a WINNING trade ends the session; a LOSING trade allows a
    # re-entry, up to MAX_SL_PER_DAY untrailed stops. So a new entry is allowed
    # only while no target has been hit this session AND the untrailed-SL count
    # is below the cap AND no position is already open. Both counters reset at
    # the contract rollover with the rest of the per-contract state.
    session_won = bool(carried.get("session_won"))
    sl_count_now = carried.get("untrailed_sl_count", 0) or 0
    position_open_now = bool(carried.get("position_open"))
    session_allows_entry = (not session_won
                            and sl_count_now < MAX_SL_PER_DAY
                            and not position_open_now)

    trade_plan = None
    decision_dir = None
    if sensex_entry.get("bull_enter") or sensex_entry.get("bear_enter"):
        decision_dir = "BULL" if sensex_entry.get("bull_enter") else "BEAR"
        if not session_allows_entry:
            reason = ("session_won(target already hit)" if session_won
                      else "sl_halt({}/{})".format(sl_count_now, MAX_SL_PER_DAY)
                      if sl_count_now >= MAX_SL_PER_DAY
                      else "position_already_open")
            print("TRADE_PLAN_SKIPPED [{ts}] {dir} signal but session rule "
                  "blocks a new entry: {why}".format(
                      ts=_now_ist(), dir=decision_dir, why=reason))
        elif not sensex_entry.get("live_eligible"):
            print("TRADE_PLAN_SKIPPED [{ts}] {dir} signal is observation-only "
                  "(LOGIC3); no plan built.".format(
                      ts=_now_ist(), dir=decision_dir))
        else:
            # Pass the CONFIRMED Sensex direction (not the provisional bias)
            # so the plan's spread side matches the entry decision exactly.
            trade_plan = build_trade_plan(
                decision_dir, sr_position, index_price, snapshot, chain,
                client, sr_role=sr_role)
            if trade_plan is not None:
                log_trade_plan(trade_plan, decision_dir)

    # Consecutive same-strike PCR-trend line (Req 9.10).
    print("PCR_TREND [{ts}] direction={dir} consecutive_cycles={streak} "
          "prev_pcr={prev} curr_pcr={curr}".format(
              ts=_now_ist(), dir=pcr_trend.get("direction"),
              streak=pcr_trend.get("streak"),
              prev=_fmt(prev_pcr, 4), curr=_fmt(pcr, 4)))
    print()

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
    elif trade_plan is not None and session_allows_entry:
        # LIVE credit-spread placement driven by the Sensex-equivalent trade
        # plan (plan strikes / risk-based lots / per-trade SL+target). The old
        # evaluate_entry gate (``entry``) is retained only for the observation
        # WOULD_HAVE_ENTERED log above; the actual live trigger is the confirmed
        # Sensex signal + its trade plan. Any placement failure is logged inside
        # _place_live_entry, which leaves the position not-open (Req 9.17); the
        # loop continues regardless.
        try:
            result = _place_live_entry(
                client, decision_dir, sr_position, snapshot, index_price,
                entry_state, plan=trade_plan)
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
        # ATM-shift + rolling OI-PCR state carried into the next cycle
        # (Sensex-equivalent summary; Req 9.7/9.10).
        "prev_atm": curr_atm,
        "avg_oipcr_list": avg_oipcr_list,
        "avg_oipcr9_list": avg_oipcr9_list,
        "atm_not_shifted_count": atm_not_shifted_count,
        "is_pcr_seq_inc": is_pcr_seq_inc,
        "is_pcr_seq_dec": is_pcr_seq_dec,
        "is_pcr9_seq_inc": is_pcr9_seq_inc,
        "is_pcr9_seq_dec": is_pcr9_seq_dec,
        # Consecutive-occurrence counters for the SEQ verdicts (how long the
        # rising/falling run has held); reset to 0 when disqualified.
        "pcr_inc_streak": pcr_inc_streak,
        "pcr_dec_streak": pcr_dec_streak,
        "pcr9_inc_streak": pcr9_inc_streak,
        "pcr9_dec_streak": pcr9_dec_streak,
        # Invariant-window track: rolling raw-chain OI snapshots + its own
        # flags/streaks (no ATM-shift reset — every value is re-measured on the
        # current strike set).
        "chain_oi_history": chain_oi_history,
        "is_pcr9_inc_inv": is_pcr9_inc_inv,
        "is_pcr9_dec_inv": is_pcr9_dec_inv,
        "is_pcr_inc_inv": is_pcr_inc_inv,
        "is_pcr_dec_inv": is_pcr_dec_inv,
        "pcr9_inc_streak_inv": pcr9_inc_streak_inv,
        "pcr9_dec_streak_inv": pcr9_dec_streak_inv,
        "pcr_inc_streak_inv": pcr_inc_streak_inv,
        "pcr_dec_streak_inv": pcr_dec_streak_inv,
        # Full-chain (total) PCR track — no ATM dependency, so no reset.
        "total_pcr": total_pcr,
        "total_pcr_list": total_pcr_list,
        "is_total_pcr_inc": is_total_pcr_inc,
        "is_total_pcr_dec": is_total_pcr_dec,
        "total_pcr_inc_streak": total_pcr_inc_streak,
        "total_pcr_dec_streak": total_pcr_dec_streak,
        # Full-17 PCR + consecutive rise/fall dominance streaks (once/twice/
        # thrice) for both the 17- and 9-strike PCRs, carried for the next cycle.
        "pcr_full": pcr_full,
        "pcr17_up_streak": pcr17_up_streak,
        "pcr17_down_streak": pcr17_down_streak,
        "pcr9_up_streak": pcr9_up_streak,
        "pcr9_down_streak": pcr9_down_streak,
        # Previous-cycle raw-chain OI, for diffing the S/R strike's OI change at
        # any strike (incl. interim strikes absent from the 200-grid snapshot).
        "prev_chain_oi": curr_chain_oi,
        # Contract-start OI baseline + its contract label, for the CUMULATIVE
        # change-in-OI measure (re-captured automatically on daily rollover).
        "baseline_chain_oi": baseline_chain_oi,
        "baseline_contract": contract_label,
        # Sensex-equivalent entry-criteria state. The dominance streaks and the
        # persisted fresh flow were updated in place on ``carried`` by
        # ``update_sr_dominance_streaks``; ``untrailed_sl_count`` is bumped by
        # the monitor on an UNTRAILED stop-loss only.
        "pe_supp_streak": carried.get("pe_supp_streak", 0),
        "ce_res_streak": carried.get("ce_res_streak", 0),
        "last_fresh_ce": carried.get("last_fresh_ce", 0.0),
        "last_fresh_pe": carried.get("last_fresh_pe", 0.0),
        "untrailed_sl_count": carried.get("untrailed_sl_count", 0),
        "session_won": carried.get("session_won", False),
        "sensex_entry": sensex_entry,
        # Current contract label, so the next cycle can detect the 12:00 UTC /
        # 17:30 IST rollover and reset all per-contract state.
        "contract_label": carried.get("contract_label", contract_label),
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
        # Expiry/rollover state (Task 14.8) — persist across cycles so the
        # new-contract detection and per-expiry square-off marker survive. These
        # were updated in place on ``carried`` by ``handle_expiry_rollover``.
        "current_expiry": carried.get("current_expiry"),
        "squared_off_for": carried.get("squared_off_for"),
        "expiry": expiry_result,
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
# TRADE PLAN: hedge selection + risk sizing + SL/target (Sensex-equivalent)
# ============================================================
# These pure functions mirror Strategy_Sensex's select_hedge_by_delta,
# get_option_candle_range and calc_lots_by_risk, adapted to Delta BTC options
# (200-point strike grid, per-strike Greeks from the chain, USD risk, 0.001
# contract value). None of them place orders — build_trade_plan() assembles a
# complete planned credit spread (both legs, lots, credit, SL/target, net risk)
# that the observation surface logs and the live hook can later execute verbatim.


def option_candle_median_range(symbol, client, n_candles=OPT_RANGE_CANDLES):
    """
    Median of the option's recent 3-min HIGH-LOW candle ranges (Sensex
    get_option_candle_range). Drives both sizing and SL/target so the two always
    agree. Returns ``(median_range, n_used)`` or ``(None, 0)`` when no usable
    candle history exists yet (freshly-listed strike) — the caller then falls
    back to a premium-fraction SL/target. Never raises.

    Low-sample guard: a thin window (< OPT_RANGE_MIN_MEDIAN_CANDLES valid
    candles) that is implausibly large is halved, since early in a fresh
    contract every surviving candle is opening volatility.
    """
    try:
        # getHistorical returns a list of {time,open,high,low,close,volume}
        # dicts, ascending by time (oldest first). Never raises.
        candles = helper.getHistorical(symbol, "3m", 1, client)
    except Exception as exc:  # noqa: BLE001 - history is best-effort
        print("OPT_RANGE_WARN [{}] getHistorical failed for {}: {}: {}".format(
            _now_ist(), symbol, type(exc).__name__, exc))
        return None, 0
    if not candles:
        return None, 0
    # Skip the first candle (opening-volatility outlier), take the last N.
    body = candles[1:] if len(candles) > 1 else candles
    body = body[-n_candles:]
    ranges = []
    for c in body:
        hi, lo = c.get("high"), c.get("low")
        if hi is not None and lo is not None and (hi - lo) > 0:
            ranges.append(float(hi - lo))
    if not ranges:
        return None, 0
    # Median of ALL available valid ranges — no minimum-candle requirement.
    # Whatever candles exist for this freshly-listed option (even 1) are used;
    # the premium-fraction fallback only applies when there is NO candle at all.
    n_used = len(ranges)
    ranges_sorted = sorted(ranges)
    mid = n_used // 2
    if n_used % 2:
        median = ranges_sorted[mid]
    else:
        median = (ranges_sorted[mid - 1] + ranges_sorted[mid]) / 2.0
    return median, n_used


def select_hedge_by_delta(main_strike, option_type, chain,
                          target_fraction=HEDGE_TARGET_DELTA_FRACTION):
    """
    Pick the protective hedge strike whose |delta| is nearest
    ``|main_delta| * target_fraction`` on the OTM side, then snap to the 200
    grid (Sensex select_hedge_by_delta, adapted to BTC's grid + chain Greeks).

    ``option_type`` is "C"/"P" (the SAME type as the sold main leg). PE hedge is
    BELOW the main strike, CE hedge ABOVE (further OTM). Uses the per-strike
    ``delta`` now captured on every chain row.

    Returns ``(hedge_strike, main_delta, hedge_delta)`` or ``(None, main_delta,
    None)`` when no OTM strike carries usable greeks (caller then falls back to a
    fixed strike offset). Never raises.
    """
    idx = _index_chain_by_strike(chain) if chain else {}
    side = "call" if option_type == "C" else "put"

    def _row_delta(strike):
        pair = idx.get(strike)
        if not isinstance(pair, dict):
            return None
        row = pair.get(side)
        if isinstance(row, dict):
            d = row.get("delta")
            return float(d) if d is not None else None
        return None

    main_delta = _row_delta(int(main_strike))
    if main_delta is None or abs(main_delta) <= 0:
        return None, main_delta, None

    target = abs(main_delta) * float(target_fraction)

    # OTM candidates on the correct side, each with a usable delta.
    candidates = []
    for strike, pair in idx.items():
        if option_type == "P" and strike >= int(main_strike):
            continue
        if option_type == "C" and strike <= int(main_strike):
            continue
        d = _row_delta(strike)
        if d is not None and abs(d) > 0:
            candidates.append((strike, abs(d)))
    if not candidates:
        return None, main_delta, None

    # Strike whose |delta| is nearest the target hedge delta; already on grid.
    hedge_strike, hedge_abs_delta = min(
        candidates, key=lambda sd: abs(sd[1] - target))
    return int(hedge_strike), main_delta, hedge_abs_delta


def _enrich_chain_greeks(chain, main_strike, option_type, expiry, client):
    """
    ENTRY-TIME-ONLY: patch per-strike Greeks onto the chain rows the delta-based
    hedge selection needs, via ONE batched ``/v2/tickers`` call.

    The per-cycle bulk option-chain list does not carry Greeks on the live Delta
    feed, so ``select_hedge_by_delta`` would otherwise always fall back to the
    fixed strike offset. This fetches Greeks for the sold main strike plus the
    nearest OTM hedge candidates (same side) in a single request and merges them
    into the matching chain rows IN PLACE. It is never called on the per-cycle
    signal path, so it adds no hot-loop latency.

    Returns ``-1`` when the main strike already carries a usable delta (nothing
    fetched), the number of rows patched (``>= 0``) otherwise. Best-effort: any
    failure leaves the chain untouched and yields ``0`` so the caller's
    fixed-offset hedge fallback applies. Never raises.
    """
    if not chain:
        return 0
    side = "call" if option_type == "C" else "put"

    # If the sold main strike already has a usable delta, Greeks are present
    # (future-proof: the live feed may start including them) — skip the fetch.
    for row in chain:
        if (isinstance(row, dict)
                and int(round(row.get("strike") or 0)) == int(main_strike)
                and side in str(row.get("contract_type") or "").lower()
                and row.get("delta") is not None):
            return -1

    # Candidate strikes: main + nearest OTM steps on the correct side, bounded
    # to the batched-ticker symbol limit (one request).
    step = STRIKE_STEP
    n_otm = helper.TICKERS_BY_SYMBOL_MAX - 1
    if option_type == "P":
        strikes = [int(main_strike) - i * step for i in range(0, n_otm + 1)]
    else:
        strikes = [int(main_strike) + i * step for i in range(0, n_otm + 1)]

    symbols = []
    for s in strikes:
        if s <= 0:
            continue
        try:
            symbols.append(helper.buildSymbol(
                product_type="option", underlying=UNDERLYING_ASSET,
                option_type=option_type, strike=s, expiry_ddmmyy=expiry))
        except Exception:  # noqa: BLE001 - skip an unbuildable candidate
            continue
    if not symbols:
        return 0

    fetched = helper.fetchTickersBySymbols(symbols, client)
    if not fetched:
        return 0

    patched = 0
    for row in chain:
        if not isinstance(row, dict):
            continue
        g = fetched.get(str(row.get("symbol")))
        if g:
            row.update(g)
            patched += 1
    return patched


def calc_lots_by_risk(effective_sl_points, main_premium=None,
                      hedge_premium=None, main_delta=None, hedge_delta=None):
    """
    Size contracts so an SL-hit loses ~= FIXED_RISK_PER_TRADE USD, NET of the
    hedge (Sensex calc_lots_by_risk). The hedge offsets part of the short-leg
    loss; the offset ratio is picked DELTA-first, then premium, then none:

        delta_ratio   = |hedge_delta| / |main_delta|     (preferred)
        premium_ratio = hedge_premium / main_premium     (fallback)
        net_sl_points = effective_sl * (1 - clamped_ratio)
        lots          = FIXED_RISK / (net_sl_points * CONTRACT_VALUE)

    Returns ``(lots, offset_ratio, net_sl_points, ratio_source)``. ``lots`` is a
    whole contract count clamped to [MIN_LOTS, MAX_LOTS] with the fractional
    round-up. On an unusable SL it returns ``(MIN_LOTS, None, effective_sl,
    "none")``. Never raises.
    """
    if not effective_sl_points or effective_sl_points <= 0:
        return MIN_LOTS, None, effective_sl_points, "none"

    delta_ratio = None
    if (main_delta is not None and hedge_delta is not None
            and abs(main_delta) > 0 and abs(hedge_delta) > 0):
        delta_ratio = abs(hedge_delta) / abs(main_delta)
    premium_ratio = None
    if (main_premium and hedge_premium and main_premium > 0
            and 0 < hedge_premium < main_premium):
        premium_ratio = hedge_premium / main_premium

    if delta_ratio is not None:
        raw_ratio, source = delta_ratio, "DELTA"
    elif premium_ratio is not None:
        raw_ratio, source = premium_ratio, "PREMIUM"
    else:
        raw_ratio, source = None, "NONE"

    offset_ratio = None
    net_sl_points = float(effective_sl_points)
    if raw_ratio is not None:
        offset_ratio = max(HEDGE_OFFSET_RATIO_MIN,
                           min(raw_ratio, HEDGE_OFFSET_RATIO_MAX))
        net_sl_points = effective_sl_points * (1 - offset_ratio)

    if net_sl_points <= 0:
        return MIN_LOTS, offset_ratio, net_sl_points, source

    loss_per_lot = net_sl_points * CONTRACT_VALUE   # USD per contract at SL
    if loss_per_lot <= 0:
        return MIN_LOTS, offset_ratio, net_sl_points, source

    raw_lots = FIXED_RISK_PER_TRADE / loss_per_lot
    frac = raw_lots - int(raw_lots)
    lots = int(raw_lots) + 1 if frac >= LOT_ROUNDUP_FRACTION else int(raw_lots)
    lots = max(MIN_LOTS, min(lots, MAX_LOTS))
    return lots, offset_ratio, net_sl_points, source


def build_trade_plan(bias, position, index_price, snapshot, chain, client,
                     sr_role=None):
    """
    Assemble the complete planned credit spread for a qualifying BULL/BEAR
    signal — Sensex-equivalent, but places NO order. Returns a plan dict (or
    ``None`` when a direction can't be resolved) with: direction, both leg
    symbols/strikes, per-leg entry premium + delta + candle-range median, the
    effective SL/target premium-points (median × multiplier, or the premium-%
    fallback), the risk-based lot count + hedge-offset ratio, the estimated net
    credit, and the modelled max risk/reward in USD.

    Pure/best-effort: any missing datum degrades gracefully and is noted in the
    plan rather than raising.
    """
    # Direction -> sold ATM leg type + which side is OTM for the hedge.
    if position == "support_side" or bias == "BULL":
        option_type, spread_kind = "P", "credit_put_spread(bull)"
    elif position == "resistance_side" or bias == "BEAR":
        option_type, spread_kind = "C", "credit_call_spread(bear)"
    else:
        return None

    atm = snapshot.get("atm_strike") if isinstance(snapshot, dict) else None
    if atm is None:
        atm = helper._nearest_atm_strike(index_price, STRIKE_STEP)
    try:
        main_strike = int(atm)
    except (TypeError, ValueError):
        return None

    expiry = helper.getDailyExpiry()

    def _sym(strike):
        return helper.buildSymbol(product_type="option",
                                  underlying=UNDERLYING_ASSET,
                                  option_type=option_type, strike=int(strike),
                                  expiry_ddmmyy=expiry)

    notes = []

    # ---- Entry-time Greeks enrichment (bulk list omits Greeks on live feed) ----
    # One batched /v2/tickers call for the main + OTM hedge candidates so the
    # delta-based hedge selection can run; silently degrades to the fixed-offset
    # fallback when Greeks are unavailable. Not on the per-cycle path.
    greeks_patched = _enrich_chain_greeks(
        chain, main_strike, option_type, expiry, client)
    if greeks_patched > 0:
        notes.append("greeks_enriched({}rows)".format(greeks_patched))
    elif greeks_patched == 0:
        notes.append("greeks_unavailable(delta_hedge->fallback)")

    # ---- Hedge selection (delta-based, 200-grid) ----
    hedge_strike, main_delta, hedge_delta = select_hedge_by_delta(
        main_strike, option_type, chain)
    if hedge_strike is None:
        # Fallback: fixed strike offset (HEDGE_OFFSET_STRIKES * grid), OTM side.
        shift = HEDGE_OFFSET_STRIKES * STRIKE_STEP
        hedge_strike = (main_strike - shift if option_type == "P"
                        else main_strike + shift)
        notes.append("hedge_fallback_fixed_offset({}grid)".format(
            HEDGE_OFFSET_STRIKES))

    try:
        main_symbol = _sym(main_strike)
        hedge_symbol = _sym(hedge_strike)
    except Exception as exc:  # noqa: BLE001
        print("TRADE_PLAN_WARN [{}] symbol build failed: {}: {}".format(
            _now_ist(), type(exc).__name__, exc))
        return None

    # ---- Per-leg entry premium (LTP) ----
    def _ltp(sym):
        try:
            return helper.manualLTP(sym, client)
        except Exception:  # noqa: BLE001 - best-effort premium
            return None

    main_premium = _ltp(main_symbol)
    hedge_premium = _ltp(hedge_symbol)

    # ---- SL/target basis: short-leg candle-range median × multiplier ----
    median_range, n_used = option_candle_median_range(main_symbol, client)
    if median_range and median_range > 0:
        effective_sl = median_range * SL_MULTIPLIER
        effective_tgt = median_range * TARGET_MULTIPLIER
        sl_basis = "candle_median({}c)={}".format(n_used, _fmt(median_range, 2))
    elif main_premium and main_premium > 0:
        effective_sl = main_premium * SL_PREMIUM_FALLBACK_FRACTION
        effective_tgt = main_premium * TGT_PREMIUM_FALLBACK_FRACTION
        sl_basis = "premium_fallback(prem={})".format(_fmt(main_premium, 2))
        notes.append("sl_from_premium_fallback(no_candle_history)")
    else:
        effective_sl = effective_tgt = None
        sl_basis = "unavailable(no_candle_history,no_premium)"
        notes.append("sl_target_unavailable")

    # ---- Risk-based sizing ----
    if effective_sl:
        lots, offset_ratio, net_sl_points, ratio_source = calc_lots_by_risk(
            effective_sl, main_premium=main_premium,
            hedge_premium=hedge_premium, main_delta=main_delta,
            hedge_delta=hedge_delta)
    else:
        lots, offset_ratio, net_sl_points, ratio_source = (
            MIN_LOTS, None, None, "none")
        notes.append("size_defaulted_min_lots(no_sl)")

    # ---- Estimated credit + modelled risk/reward (USD) ----
    est_credit = None
    if main_premium is not None and hedge_premium is not None:
        est_credit = float(main_premium) - float(hedge_premium)
    # At SL the short-leg loss is net_sl_points; at target it's effective_tgt.
    max_risk_usd = (net_sl_points * CONTRACT_VALUE * lots
                    if net_sl_points else None)
    max_reward_usd = (effective_tgt * CONTRACT_VALUE * lots
                      if effective_tgt else None)

    return {
        "spread_kind": spread_kind,
        "option_type": option_type,
        "expiry": expiry,
        "main_symbol": main_symbol, "main_strike": main_strike,
        "main_premium": main_premium, "main_delta": main_delta,
        "hedge_symbol": hedge_symbol, "hedge_strike": hedge_strike,
        "hedge_premium": hedge_premium, "hedge_delta": hedge_delta,
        "median_range": median_range, "median_candles": n_used,
        "effective_sl": effective_sl, "effective_tgt": effective_tgt,
        "sl_basis": sl_basis,
        "lots": lots, "offset_ratio": offset_ratio,
        "net_sl_points": net_sl_points, "ratio_source": ratio_source,
        "est_credit": est_credit,
        "max_risk_usd": max_risk_usd, "max_reward_usd": max_reward_usd,
        "notes": notes,
    }


def log_trade_plan(plan, decision_dir):
    """Log the planned credit spread (observation surface). No order placed."""
    if not isinstance(plan, dict):
        return
    print("TRADE_PLAN [{ts}] {dir} {kind} expiry={exp}".format(
        ts=_now_ist(), dir=decision_dir, kind=plan["spread_kind"],
        exp=plan["expiry"]))
    print("  MAIN  SELL {sym} strike={k} premium={p} delta={d}".format(
        sym=plan["main_symbol"], k=plan["main_strike"],
        p=_fmt(plan["main_premium"], 2), d=_fmt(plan["main_delta"], 4)))
    print("  HEDGE BUY  {sym} strike={k} premium={p} delta={d}".format(
        sym=plan["hedge_symbol"], k=plan["hedge_strike"],
        p=_fmt(plan["hedge_premium"], 2), d=_fmt(plan["hedge_delta"], 4)))
    print("  SL/TGT basis={basis} -> effective_sl={sl} effective_tgt={tg} "
          "(mult {slm}/{tgm})".format(
              basis=plan["sl_basis"], sl=_fmt(plan["effective_sl"], 2),
              tg=_fmt(plan["effective_tgt"], 2),
              slm=SL_MULTIPLIER, tgm=TARGET_MULTIPLIER))
    print("  SIZE lots={lots} offset_ratio={r}({src}) net_sl_pts={n} "
          "(risk budget ${risk})".format(
              lots=plan["lots"], r=_fmt(plan["offset_ratio"], 3),
              src=plan["ratio_source"], n=_fmt(plan["net_sl_points"], 2),
              risk=_fmt(FIXED_RISK_PER_TRADE, 2)))
    print("  EST credit={c}  modelled max_risk=${mr}  max_reward=${mw}".format(
        c=_fmt(plan["est_credit"], 2), mr=_fmt(plan["max_risk_usd"], 2),
        mw=_fmt(plan["max_reward_usd"], 2)))
    if plan.get("notes"):
        print("  NOTES: {}".format("; ".join(plan["notes"])))


# ============================================================
# LIVE-ORDER HOOK (Req 9.5, 9.16, 9.17) — Task 14.3
# ============================================================
def _place_live_entry(client, bias, position, snapshot, index_price,
                      state=None, plan=None):
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

    plan = plan if isinstance(plan, dict) else None

    # Per-trade SL/target (premium points) stamped on the open position so the
    # monitor evaluates THIS trade's levels, not the module defaults. Populated
    # from the trade plan; stays None in the legacy (no-plan) path.
    effective_sl = effective_tgt = None

    if plan is not None:
        # ---- Plan-driven placement: strikes, risk-based lots and per-trade
        #      SL/target all come from the Sensex-equivalent build_trade_plan. --
        option_type = plan.get("option_type")
        spread_kind = plan.get("spread_kind")
        main_symbol = plan.get("main_symbol")
        hedge_symbol = plan.get("hedge_symbol")
        main_strike = plan.get("main_strike")
        hedge_strike = plan.get("hedge_strike")
        size = plan.get("lots")
        effective_sl = plan.get("effective_sl")
        effective_tgt = plan.get("effective_tgt")
        expiry_ddmmyy = plan.get("expiry") or helper.getDailyExpiry()
        if not (main_symbol and hedge_symbol and option_type):
            print("LIVE_ENTRY_FAIL [{}] trade plan missing leg symbols/type; "
                  "leaving position not-open (9.17).".format(_now_ist()))
            return {"ok": False, "opened": False, "error": "plan_incomplete"}
        if not size or size <= 0:
            print("LIVE_ENTRY_FAIL [{}] trade plan lot count is {!r}; cannot "
                  "size a live order — position not-open (9.17).".format(
                      _now_ist(), size))
            return {"ok": False, "opened": False, "error": "plan_size_invalid"}
        if effective_sl is None or effective_tgt is None:
            print("LIVE_ENTRY_FAIL [{}] trade plan has no SL/target "
                  "(sl={!r} tgt={!r}); an unmonitorable spread is refused — "
                  "position not-open (9.17).".format(
                      _now_ist(), effective_sl, effective_tgt))
            return {"ok": False, "opened": False, "error": "plan_no_sl_target"}
        print("LIVE_ENTRY [{}] {} size={} BUY hedge={} FIRST then SELL main={} "
              "(plan: main_strike={} hedge_strike={} effective_sl={} "
              "effective_tgt={} expiry={}).".format(
                  _now_ist(), spread_kind, size, hedge_symbol, main_symbol,
                  main_strike, hedge_strike, _fmt(effective_sl, 2),
                  _fmt(effective_tgt, 2), expiry_ddmmyy))
    else:
        # ---- Legacy path (no plan): [NEEDS INPUT] POSITION_SIZE guard, ATM
        #      main strike and a fixed-offset hedge. Retained as a fallback. ----
        if POSITION_SIZE is None:
            print("LIVE_ENTRY_BLOCKED [{}] POSITION_SIZE is [NEEDS INPUT] "
                  "(None); cannot size a live order — leaving position not-open "
                  "(9.17).".format(_now_ist()))
            return {"ok": False, "opened": False,
                    "error": "position_size_unset"}

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
            return {"ok": False, "opened": False,
                    "error": "atm_strike_unresolved"}

        hedge_shift = HEDGE_OFFSET_STRIKES * STRIKE_STEP

        # Direction -> credit spread + OTM protective hedge (Req 9.16).
        if position == "support_side" or bias == "BULL":
            option_type = "P"                       # credit PUT spread (bull)
            hedge_strike = main_strike - hedge_shift  # further-OTM = LOWER put
            spread_kind = "credit_put_spread(bull)"
        elif position == "resistance_side" or bias == "BEAR":
            option_type = "C"                       # credit CALL spread (bear)
            hedge_strike = main_strike + hedge_shift  # further-OTM = HIGHER call
            spread_kind = "credit_call_spread(bear)"
        else:
            print("LIVE_ENTRY_FAIL [{}] indeterminate direction (bias={!r}, "
                  "position={!r}); leaving position not-open (9.17).".format(
                      _now_ist(), bias, position))
            return {"ok": False, "opened": False,
                    "error": "indeterminate_direction"}

        if hedge_strike <= 0:
            print("LIVE_ENTRY_FAIL [{}] computed hedge strike {} is "
                  "non-positive; leaving position not-open (9.17).".format(
                      _now_ist(), hedge_strike))
            return {"ok": False, "opened": False,
                    "error": "invalid_hedge_strike"}

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
            return {"ok": False, "opened": False,
                    "error": "symbol_build_failed"}

        size = POSITION_SIZE
        print("LIVE_ENTRY [{}] {} size={} BUY hedge={} FIRST then SELL main={} "
              "(atm={}, hedge_offset={} strikes={} pts, expiry={}).".format(
                  _now_ist(), spread_kind, size, hedge_symbol, main_symbol,
                  main_strike, HEDGE_OFFSET_STRIKES, hedge_shift,
                  expiry_ddmmyy))

    # --- Leg 1: BUY the OTM protective hedge leg FIRST (Req 9.16). -------
    # The long leg is placed before the short deliberately: if the second leg
    # fails we are left holding a LONG option (defined, tiny risk) rather than a
    # NAKED SHORT. It also seats the long leg so the exchange can grant the
    # spread's reduced (defined-risk) margin when the short is then placed, and
    # avoids a `naked_short_restricted` reject on the short.
    hedge_res = helper.placeOrder(
        hedge_symbol, "buy", size, order_type="market_order",
        client=client, papertrading=0)
    if not (isinstance(hedge_res, dict) and hedge_res.get("ok")):
        detail = hedge_res.get("error") if isinstance(hedge_res, dict) \
            else hedge_res
        print("LIVE_ENTRY_FAIL [{}] protective hedge BUY leg {} failed: {} — "
              "nothing opened, no short placed; position not-open (9.17)."
              .format(_now_ist(), hedge_symbol, detail))
        return {"ok": False, "opened": False, "error": "hedge_leg_failed",
                "detail": hedge_res}

    # --- Leg 2: SELL the main (credit) leg (Req 9.16). ------------------
    main_res = helper.placeOrder(
        main_symbol, "sell", size, order_type="market_order",
        client=client, papertrading=0)
    if not (isinstance(main_res, dict) and main_res.get("ok")):
        detail = main_res.get("error") if isinstance(main_res, dict) \
            else main_res
        # The protective hedge (long) filled but the main short failed. There is
        # NO naked-short risk — we only hold a long option. Best-effort sell it
        # back to flatten so no stray long leg lingers, then report not-open.
        print("LIVE_ENTRY_WARN [{}] hedge BUY {} FILLED but main SELL {} FAILED "
              "({}) — no naked-short risk (long only); selling the hedge back "
              "to flatten.".format(
                  _now_ist(), hedge_symbol, main_symbol, detail))
        unwind = helper.placeOrder(
            hedge_symbol, "sell", size, order_type="market_order",
            client=client, papertrading=0)
        if isinstance(unwind, dict) and unwind.get("ok"):
            print("LIVE_ENTRY_RECOVER [{}] hedge leg {} sold back after main "
                  "failure; position left not-open (9.17).".format(
                      _now_ist(), hedge_symbol))
        else:
            unwind_detail = unwind.get("error") if isinstance(unwind, dict) \
                else unwind
            # Targeted sell-back failed — fall back to exitAll (flatten
            # everything) as the last resort so no stray leg lingers, mirroring
            # the Sensex square-off safety. A long-only orphan is defined-risk,
            # so even if this also fails there is no naked-short exposure.
            print("LIVE_ENTRY_WARN [{}] could not sell back hedge leg {} after "
                  "main failure ({}) — attempting exitAll flatten as last "
                  "resort.".format(_now_ist(), hedge_symbol, unwind_detail))
            try:
                flat = helper.exitAll(client=client)
                print("LIVE_ENTRY_RECOVER [{}] exitAll after hedge-unwind "
                      "failure -> {}.".format(_now_ist(), flat))
            except Exception as exc:  # noqa: BLE001 - last resort, never crash
                print("LIVE_ENTRY_WARN [{}] exitAll last-resort also failed "
                      "({}: {}) — a stray LONG {} may remain (defined risk); "
                      "position left not-open (9.17).".format(
                          _now_ist(), type(exc).__name__, exc, hedge_symbol))
        return {"ok": False, "opened": False, "error": "main_leg_failed",
                "detail": main_res, "hedge_leg": hedge_res, "unwind": unwind}

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

    # --- Post-fill safety (Sensex square-off): a spread we cannot VALUE cannot
    #     be monitored for SL/target. If no anchor premium is readable on either
    #     leg (entry_credit unknown), flatten everything immediately rather than
    #     hold an unmonitorable position. ----
    if entry_credit is None:
        print("LIVE_ENTRY_WARN [{}] both legs filled but NO anchor premium is "
              "readable (main_px={!r} hedge_px={!r}) — spread cannot be "
              "monitored; squaring off (exitAll) as post-fill safety.".format(
                  _now_ist(), main_entry_px, hedge_entry_px))
        try:
            flat = helper.exitAll(client=client)
            print("LIVE_ENTRY_RECOVER [{}] exitAll after unmonitorable fill -> "
                  "{}.".format(_now_ist(), flat))
        except Exception as exc:  # noqa: BLE001 - last resort, never crash
            print("LIVE_ENTRY_WARN [{}] exitAll post-fill square-off failed "
                  "({}: {}) — manual intervention may be required.".format(
                      _now_ist(), type(exc).__name__, exc))
        return {"ok": False, "opened": False,
                "error": "unmonitorable_no_anchor_premium"}

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
        # Per-trade SL/target (premium points) from the trade plan. The monitor
        # reads these in preference to the module-level STOP_LOSS_POINTS /
        # TARGET_POINTS (which stay None), so each trade is managed on its own
        # candle-median-derived levels.
        "effective_sl": effective_sl,
        "effective_tgt": effective_tgt,
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

    # Per-trade SL/target (premium points) stamped at entry from the trade plan.
    # These take precedence over the module-level TARGET_POINTS/STOP_LOSS_POINTS
    # (which stay None) so each trade is managed on its own levels; fall back to
    # the module constants only when the position carries none.
    eff_tgt = open_position.get("effective_tgt")
    if eff_tgt is None:
        eff_tgt = TARGET_POINTS
    eff_sl = open_position.get("effective_sl")
    if eff_sl is None:
        eff_sl = STOP_LOSS_POINTS

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
    if (TRAILING_ENABLED and eff_tgt is not None and not already_breakeven
            and unrealized_points >= TRAIL_TRIGGER_TARGET_FRACTION
            * float(eff_tgt)):
        if OBSERVATION_MODE:
            trail_action = "would_trail_to_breakeven"
            print("MONITOR_TRAIL [{}] WOULD_TRAIL_TO_BREAKEVEN: unrealized "
                  "{} pts >= {} * target {} = {} pts (OBSERVATION_MODE; SL not "
                  "actually moved).".format(
                      _now_ist(), _fmt(unrealized_points),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION),
                      _fmt(eff_tgt),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION * float(eff_tgt))))
        else:
            state["sl_at_breakeven"] = True
            already_breakeven = True
            trail_action = "trailed_to_breakeven"
            print("MONITOR_TRAIL [{}] SL moved to BREAKEVEN: unrealized {} pts "
                  ">= {} * target {} = {} pts (effective SL now breakeven, "
                  "0 pts).".format(
                      _now_ist(), _fmt(unrealized_points),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION),
                      _fmt(eff_tgt),
                      _fmt(TRAIL_TRIGGER_TARGET_FRACTION * float(eff_tgt))))

    # ----- [NEEDS INPUT] guard: no concrete target/SL to evaluate --------
    if eff_tgt is None or eff_sl is None:
        unset = [n for n, v in (("TARGET_POINTS", eff_tgt),
                                ("STOP_LOSS_POINTS", eff_sl))
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
    target_hit = unrealized_points >= float(eff_tgt)
    if already_breakeven:
        # Trailed stop: breakeven is the effective SL (0 points).
        sl_hit = unrealized_points <= 0.0
        sl_level_desc = "breakeven(0)"
    else:
        sl_hit = unrealized_points <= -float(eff_sl)
        sl_level_desc = "-{}".format(_fmt(eff_sl))

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
              tp=_fmt(eff_tgt), sl=sl_level_desc,
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

    # Sensex `slCount` rule: only an UNTRAILED stop-loss is a real loss and
    # counts toward the MAX_SL_PER_DAY halt. A trailed exit sitting at breakeven
    # is a scratch, not a loss, so it is NOT counted. Counted in observation mode
    # too, so the halt behaves exactly as it would live and can be studied.
    if exit_reason == "stop_loss" and not already_breakeven:
        state["untrailed_sl_count"] = (
            state.get("untrailed_sl_count", 0) or 0) + 1
        print("SL_COUNT [{}] untrailed stop-loss -> untrailed_sl_count={}/{}"
              "{}".format(_now_ist(), state["untrailed_sl_count"],
                          MAX_SL_PER_DAY,
                          " — NO further entries this contract session"
                          if state["untrailed_sl_count"] >= MAX_SL_PER_DAY
                          else ""))
    elif exit_reason == "stop_loss" and already_breakeven:
        print("SL_COUNT [{}] stop at breakeven (trailed) — scratch, not counted."
              " untrailed_sl_count={}/{}".format(
                  _now_ist(), state.get("untrailed_sl_count", 0) or 0,
                  MAX_SL_PER_DAY))

    # Session trade-count rule: a TARGET hit (a win) ends the session for new
    # entries (Sensex st=-1). A loss leaves session_won False so a re-entry is
    # allowed up to MAX_SL_PER_DAY. Resets at the contract rollover.
    if exit_reason == "target":
        state["session_won"] = True
        print("SESSION_WIN [{}] target hit — no further entries this contract "
              "session (a loss would have allowed re-entry up to {} SLs)."
              .format(_now_ist(), MAX_SL_PER_DAY))

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
# EXPIRY HANDLING + DAILY ROLLOVER (Req 9.23-9.26) — Task 14.8
# ============================================================
# Delta BTC daily options expire at 12:00 UTC (17:30 IST). Every expiry-window /
# rollover computation is anchored to that boundary in the Reference_Timezone
# (UTC), consistent with helper_delta.getDailyExpiry (which rolls at >= 12:00
# UTC). Human-facing log lines are timestamped in IST (_now_ist) for readability
# only; NONE of the scheduling math depends on IST.
def in_pre_expiry_window(now_utc=None):
    """
    Return True when the current UTC time is inside the pre-expiry lead window
    that precedes the 12:00 UTC daily expiry (Req 9.24).

    The window is the ``PRE_EXPIRY_LEAD_MINUTES`` (default 15) immediately before
    the ``DAILY_EXPIRY_UTC_HOUR`` (12:00) UTC boundary, i.e. with the defaults it
    spans::

        [11:45:00.000000 UTC, 12:00:00.000000 UTC)

    — inclusive of 11:45:00 and running through 11:59:59.999999, and EXCLUSIVE of
    12:00:00 (at which point the contract has already rolled and
    ``getDailyExpiry`` returns the next day). In IST that is 17:15:00–17:29:59.

    ``now_utc`` handling mirrors ``getDailyExpiry``: when omitted,
    ``datetime.now(REFERENCE_TIMEZONE)`` (UTC) is used; a timezone-aware datetime
    is normalized to the UTC reference timezone; a naive datetime is assumed to
    already be expressed in UTC. Returns a plain ``bool``.
    """
    if now_utc is None:
        now = datetime.now(REFERENCE_TIMEZONE)
    elif now_utc.tzinfo is not None:
        # Aware datetime: normalize to the UTC reference timezone.
        now = now_utc.astimezone(REFERENCE_TIMEZONE)
    else:
        # Naive datetime: assume it is already expressed in UTC.
        now = now_utc

    # The 12:00 UTC daily-expiry boundary for `now`'s calendar day, and the
    # lead-window start PRE_EXPIRY_LEAD_MINUTES before it (11:45 UTC default).
    expiry_boundary = now.replace(
        hour=DAILY_EXPIRY_UTC_HOUR, minute=0, second=0, microsecond=0)
    window_start = expiry_boundary - timedelta(minutes=PRE_EXPIRY_LEAD_MINUTES)
    return window_start <= now < expiry_boundary


def handle_expiry_rollover(client, state):
    """
    Resolve the daily expiry, roll over to a new contract, and — at the start of
    the pre-expiry window — square off any open position (Req 9.23-9.26).

    Called ONCE per signal cycle near the top of ``run_signal_cycle`` (before the
    open-position monitor branch). Fully defensive: any unexpected error is
    logged and swallowed so it can never crash the observation/live loop.

    Behavior:
      * Resolve ``current_expiry = helper.getDailyExpiry()`` (DDMMYY, rolls to
        the next day at/after 12:00 UTC) (Req 9.23).
      * New-contract detection / resume (Req 9.26): compare to
        ``state["current_expiry"]``. On the first cycle (no prior value) or when
        it changes, store the new value and clear ``state["squared_off_for"]``;
        when it changed from a real prior value, log ``RESUMED_ON_NEW_CONTRACT``.
        Evaluation naturally resumes on the new contract because
        ``run_signal_cycle`` re-queries ``getDailyExpiry`` for the chain each
        cycle.
      * Pre-expiry square-off (Req 9.25): while ``in_pre_expiry_window()`` AND a
        position is open AND it has not already been squared off for this expiry
        (``state["squared_off_for"] != current_expiry``):
          - OBSERVATION_MODE: log ``WOULD_HAVE_SQUARED_OFF`` (naming the open
            legs), set ``squared_off_for`` so it does not repeat every cycle, and
            place NO orders.
          - LIVE mode: force-exit via ``helper.exitAll(client=client)``. On full
            success mark the position closed (``position_open=False``,
            ``open_position=None``, reset monitor counters) and set
            ``squared_off_for``; on partial/failure log the residual, RETAIN the
            position open, and do NOT set ``squared_off_for`` so the next cycle
            retries (Req 9.25, mirrors the 9.21 retain-on-failure policy).

    Mutates ``state`` in place and returns a summary dict::

        {"current_expiry": <DDMMYY|None>, "rolled": bool,
         "squared_off": True|"would"|"partial"|"failed"|False,
         "in_window": bool}
    """
    state = state if isinstance(state, dict) else {}
    result = {"current_expiry": None, "rolled": False,
              "squared_off": False, "in_window": False}

    # ----- Resolve the current daily expiry (Req 9.23) -------------------
    try:
        current_expiry = helper.getDailyExpiry()
    except Exception as exc:  # noqa: BLE001 - never crash the loop
        print("EXPIRY_WARN [{}] getDailyExpiry failed ({}: {}); skipping "
              "rollover handling this cycle.".format(
                  _now_ist(), type(exc).__name__, exc))
        return result
    result["current_expiry"] = current_expiry

    # ----- New-contract detection + resume (Req 9.26) --------------------
    prev_expiry = state.get("current_expiry")
    if prev_expiry != current_expiry:
        state["current_expiry"] = current_expiry
        # A fresh contract clears any prior square-off marker so the new day's
        # pre-expiry square-off can arm again.
        state["squared_off_for"] = None
        result["rolled"] = True
        if prev_expiry is not None:
            print("RESUMED_ON_NEW_CONTRACT [{}] new_expiry={} (prev={}) — "
                  "re-resolved the daily symbol; resuming evaluation on the new "
                  "daily contract (9.26).".format(
                      _now_ist(), current_expiry, prev_expiry))

    # ----- Pre-expiry square-off (Req 9.24/9.25) -------------------------
    in_window = in_pre_expiry_window()
    result["in_window"] = in_window
    if not (in_window and state.get("position_open")
            and state.get("squared_off_for") != current_expiry):
        return result

    open_position = state.get("open_position")
    if isinstance(open_position, dict):
        legs = "main={} hedge={} kind={}".format(
            open_position.get("main_symbol"),
            open_position.get("hedge_symbol"),
            open_position.get("spread_kind"))
    else:
        legs = str(open_position)

    # OBSERVATION mode: log the would-have-squared-off; place NO orders and
    # mark it done for this expiry so it does not repeat every cycle (Req 9.25).
    if OBSERVATION_MODE:
        print("WOULD_HAVE_SQUARED_OFF [{}] expiry={} open_position=({}) — "
              "pre-expiry window (11:45-12:00 UTC); OBSERVATION_MODE (no exit "
              "order placed) (9.25).".format(_now_ist(), current_expiry, legs))
        state["squared_off_for"] = current_expiry
        result["squared_off"] = "would"
        return result

    # LIVE mode: force-exit every open position via exitAll (Req 9.25).
    try:
        exit_result = helper.exitAll(client=client)
    except Exception as exc:  # noqa: BLE001 - never crash the loop
        exit_result = {"ok": False, "closed_all": False,
                       "error": "{}: {}".format(type(exc).__name__, exc),
                       "residual": []}
        print("EXPIRY_SQUAREOFF_FAIL [{}] expiry={} unexpected error during "
              "exitAll: {} — RETAINING position open for retry next cycle "
              "(9.25).".format(_now_ist(), current_expiry, exit_result["error"]))

    if exit_result.get("ok") and exit_result.get("closed_all"):
        # Full flatten confirmed: mark the position closed and record the marker
        # so this expiry is not squared off again.
        state["position_open"] = False
        state["open_position"] = None
        _reset_monitor_state(state)
        state["squared_off_for"] = current_expiry
        result["squared_off"] = True
        print("EXPIRY_SQUARED_OFF [{}] expiry={} open_position=({}) force-"
              "exited via exitAll; position recorded closed (9.25).".format(
                  _now_ist(), current_expiry, legs))
    elif exit_result.get("error"):
        # Request/verification error: retain the position open, do NOT set the
        # squared_off marker so the next cycle retries (9.25).
        result["squared_off"] = "failed"
        print("EXPIRY_SQUAREOFF_FAIL [{}] expiry={} exitAll error: {} "
              "residual={} — RETAINING position open for retry next cycle "
              "(9.25).".format(
                  _now_ist(), current_expiry, exit_result.get("error"),
                  exit_result.get("residual")))
    else:
        # Partial flatten: some positions remain; retain open + retry next cycle.
        result["squared_off"] = "partial"
        print("EXPIRY_SQUAREOFF_PARTIAL [{}] expiry={} exitAll did not fully "
              "flatten; residual={} — RETAINING position open for retry next "
              "cycle (9.25).".format(
                  _now_ist(), current_expiry, exit_result.get("residual")))

    return result


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
    print("STARTUP entry criteria (Sensex-equivalent, observation only):")
    print("  pcr_gate             = 17-strike INVARIANT window, "
          "streak >= {}".format(PCR_ENTRY_STREAK_MIN))
    print("  logic1_morning       = CHOI >= {}%  (window 17:30-18:03 IST)"
          .format(CHOI_MORNING_PCT))
    print("  logic2_convergence   = CHOI >= {}% AND total OI >= {}%".format(
        CHOI_CONVERGE_PCT, OI_CONVERGE_PCT))
    print("  logic3_choi_trap     = CHOI >= {}%  (observation-only={})".format(
        CHOI_TRAP_PCT, LOGIC3_OBSERVATION_ONLY))
    print("  choi_dominance_gate  = {}".format(USE_CHOI_DOMINANCE_GATE))
    print("  entry_cutoff         = no NEW entry inside the last {} min before "
          "expiry (entries allowed up to {:02d}:{:02d} IST)".format(
              ENTRY_CUTOFF_MINUTES_BEFORE_EXPIRY,
              ENTRY_CUTOFF_IST_HOUR, ENTRY_CUTOFF_IST_MINUTE))
    print("  max_sl_per_session   = {} (untrailed only; trailed = scratch)"
          .format(MAX_SL_PER_DAY))
    print("  session_rule         = win ends session; loss re-enters up to the "
          "SL cap")
    print("STARTUP sizing + SL/target (Sensex-equivalent, per-trade):")
    print("  fixed_risk_per_trade = ${} (~2000 INR)".format(
        _fmt(FIXED_RISK_PER_TRADE, 2)))
    print("  sl_multiplier        = {}  target_multiplier = {}".format(
        SL_MULTIPLIER, TARGET_MULTIPLIER))
    print("  contract_value       = {} BTC/contract (P&L multiplier)".format(
        CONTRACT_VALUE))
    print("  hedge                = delta-based (target |delta|*{}), snap {} "
          "grid".format(HEDGE_TARGET_DELTA_FRACTION, STRIKE_STEP))
    print("  lots                 = risk-based [{}..{}], offset ratio "
          "DELTA->PREMIUM->none".format(MIN_LOTS, MAX_LOTS))

    # Sizing, SL and target are now computed PER TRADE by build_trade_plan
    # (risk-based lots + candle-median SL/target), so the old module-level
    # STOP_LOSS_POINTS / TARGET_POINTS / POSITION_SIZE constants are intentionally
    # left None and NO LONGER gate a live entry. They are shown here only to make
    # the superseded state explicit; the live trigger is the confirmed Sensex
    # signal + its trade plan.
    print("STARTUP sizing/SL/target source = PER-TRADE (build_trade_plan); "
          "module STOP_LOSS_POINTS/TARGET_POINTS/POSITION_SIZE are superseded "
          "(left None, not required).")
    # The PCR threshold constants + MAX_TRADES_PER_DAY only feed the LEGACY
    # evaluate_entry gate, which is now observation-only (its WOULD_HAVE_ENTERED
    # line); they do NOT block the plan-driven live entry. Logged for reference.
    legacy_params = {
        "PCR_BULL_THRESHOLD": PCR_BULL_THRESHOLD,
        "PCR_BEAR_THRESHOLD": PCR_BEAR_THRESHOLD,
        "MAX_TRADES_PER_DAY": MAX_TRADES_PER_DAY,
    }
    print("STARTUP legacy observation-gate params (do NOT affect live entry):")
    for name, value in legacy_params.items():
        note = "(unset; legacy gate observation-only)" if value is None else ""
        print("  {:<20} = {} {}".format(name, value, note).rstrip())

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
    # Per-contract dated logging (Option C): tee stdout to logs/delta_btc_
    # <expiry>.log, rolling at the 12:00 UTC / 17:30 IST contract boundary. Do
    # this FIRST so the startup banner is captured in the file too.
    install_contract_logging()

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
    # Fire each per-cycle signal evaluation ALIGNED to the TIMEFRAME_MINUTES
    # candle close (:00, :03, :06, ... UTC) rather than every SIGNAL_INTERVAL_SEC
    # from an arbitrary start — so every log corresponds to a just-closed candle,
    # exactly like the Sensex bot's `minute % timeFrame == 0` trigger. Each cycle
    # carries the previous cycle's snapshot/PCR/trend so the same-strike OI delta
    # (Req 9.8) and consecutive PCR trend (Req 9.10) diff against the prior cycle,
    # and is wrapped so a transient error (e.g. a chain fetch hiccup) is logged
    # and the loop continues rather than crashing; a KeyboardInterrupt stops the
    # loop cleanly. Observation-only: no orders. The next-boundary wait is
    # recomputed from the wall clock every iteration, so a slow cycle never
    # accumulates drift (it simply targets the following candle).
    print("SIGNAL_LOOP [{ts}] starting observation cycles aligned to {n}-min "
          "candle closes (no orders will be placed).".format(
              ts=_now_ist(), n=TIMEFRAME_MINUTES))
    state = {}
    try:
        while True:
            wait_s = _seconds_until_next_candle_close(TIMEFRAME_MINUTES)
            print("SIGNAL_LOOP [{ts}] waiting {w:.1f}s for next {n}-min candle "
                  "close.".format(ts=_now_ist(), w=wait_s, n=TIMEFRAME_MINUTES))
            time.sleep(wait_s)
            # Roll the per-contract log file at the 12:00 UTC / 17:30 IST
            # boundary so this cycle's output lands in the right contract file.
            roll_contract_log()
            try:
                state = checkCriteriaAndTakeTrade(client, state)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print("SIGNAL_ERROR [{ts}] signal cycle failed, continuing: "
                      "{typ}: {msg}".format(
                          ts=_now_ist(), typ=type(exc).__name__, msg=exc))
    except KeyboardInterrupt:
        print("SIGNAL_LOOP [{ts}] stopped by operator (KeyboardInterrupt); "
              "no orders were placed.".format(ts=_now_ist()))

    return client


if __name__ == "__main__":
    run()
