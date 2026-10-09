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

# S/R line granularity: the single support/resistance line is the nearest
# multiple of this step to the spot (Sensex-style nearest-level model). Matches
# the Sensex 500-point S/R grid (get_support_resistance step=500). e.g. spot
# 81800 -> line 82000; spot 81400 -> line 81500. NOTE: BTC option strikes are on
# a 200-point grid (STRIKE_STEP), and 500 is not a multiple of 200, so a 500-grid
# S/R line (e.g. 81500) may not be a tradeable strike. The S/R LINE drives the
# role / no-trade / bias logic on the 500 grid; when OI is read AT the line the
# lookup snaps to the nearest 200 strike (see log_supp_res_oi).
SR_LINE_STEP = 500

# Consecutive-trend window for the OI-PCR lists (avgOiPcrList2 / avgOiPcr9List2).
# Mirrors the Sensex strategy: a PCR increase/decrease is only confirmed over
# this many consecutive values (3). The window resets to the newest value on an
# ATM-strike shift, slides forward (dropping the oldest) when the trend is not
# monotonic, and is capped at this length so a long 24/7 session can't grow it.
PCR_TREND_WINDOW = 3

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

# Nominal signal-cycle cadence in seconds (one TIMEFRAME_MINUTES candle). The
# observation loop no longer sleeps a fixed interval from an arbitrary start —
# it aligns each cycle to the UTC candle close (:00, :03, :06, ...) via
# `_seconds_until_next_candle_close`, so a cycle fires as each candle closes
# (mirrors the Sensex `minute % timeFrame == 0` trigger). This constant is kept
# as the nominal period for reference/logging and as the natural fallback cadence.
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
# SUPPORT / RESISTANCE + NO-TRADE ZONE (Req 9.13, 9.14)
# ============================================================
def compute_support_resistance(index_price):
    """
    Derive the single nearest-500 S/R line and the spot's role against it
    (Req 9.13, 9.14) — the Sensex nearest-level model, on a 500-point grid.

    The S/R line is the nearest ``SR_LINE_STEP`` (500) level to the spot:

        sr_line = round(spot / 500) * 500

    The spot's position relative to that line sets the role and the tradable
    side (``NO_TRADE_ZONE_BUFFER`` straddles the line as a dead zone):
      * spot within +/- buffer of the line -> AT_LINE  / no_trade_zone
      * spot BELOW the line  -> line acts as RESISTANCE -> resistance_side (bear)
      * spot ABOVE the line  -> line acts as SUPPORT    -> support_side   (bull)

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
    no_trade_low = sr_line - NO_TRADE_ZONE_BUFFER
    no_trade_high = sr_line + NO_TRADE_ZONE_BUFFER

    if no_trade_low <= spot <= no_trade_high:
        role, position = "AT_LINE", "no_trade_zone"
    elif spot < sr_line:
        # Spot below the nearest line -> the line is overhead resistance.
        role, position = "RESISTANCE", "resistance_side"
    else:
        # Spot above the nearest line -> the line is support underneath.
        role, position = "SUPPORT", "support_side"

    return sr_line, role, position, no_trade_low, no_trade_high


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
    print("AVG_OIPCR_9STRIKE= {}  (central 9 strikes, Req 9.7)".format(
        _fmt(pcr, 2)))

    # Spot (index anchor) + perpetual/future reference, printed after the chain.
    print("BTC SPOT (.DEXBTUSD)   = {}".format(_fmt(index_price)))
    print("BTC PERP/FUT (BTCUSD)  = {}".format(_fmt(perp_price)))


def log_support_resistance(index_price, sr_line, role, position,
                           no_trade_low, no_trade_high):
    """
    Log the single nearest-500 S/R line and the spot's role against it
    (Req 9.4, 9.13, 9.14), Sensex-style ``SUPP_RES===`` line.

    ``role`` is ``RESISTANCE``/``SUPPORT``/``AT_LINE``; the trailing note spells
    out the directional read (spot below the line -> resistance -> bear; spot
    above -> support -> bull; at the line -> no trade).
    """
    if position == "resistance_side":
        note = "spot below line => RESISTANCE => look for BEAR trade"
    elif position == "support_side":
        note = "spot above line => SUPPORT => look for BULL trade"
    else:
        note = ("spot within +/-{} of line => NO-TRADE zone".format(
            NO_TRADE_ZONE_BUFFER))

    print("SUPP_RES=== {line} role={role} (nearest {step} to spot {spot}) "
          "no_trade=[{ntl}, {nth}] | {note}".format(
              line=_fmt(sr_line), role=role, step=SR_LINE_STEP,
              spot=_fmt(index_price), ntl=_fmt(no_trade_low),
              nth=_fmt(no_trade_high), note=note))


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
              [round(x, 2) for x in avg_oipcr_list], atm_not_shifted_count,
              _fmt(pcr_full, 2)))
    print("avgOiPcr9List2 = {} atmStrikeNotShiftedCount= {}  (central 9, "
          "curr={})".format(
              [round(x, 2) for x in avg_oipcr9_list], atm_not_shifted_count,
              _fmt(pcr_9, 2)))


def _seq_pct_steps(window):
    """Per-step percentage change across a PCR window (e.g. [a, b, c] ->
    [(b-a)/a*100, (c-b)/b*100]); a 0 denominator yields a 0.0 step."""
    steps = []
    for i in range(len(window) - 1):
        prev, curr = window[i], window[i + 1]
        steps.append(round((curr - prev) / prev * 100, 1) if prev else 0.0)
    return steps


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

    Returns ``(avg_oipcr_list, avg_oipcr9_list, inc, dec)`` with the (possibly
    trimmed) windows and the full-17 increase/decrease flags.
    """
    inc = dec = False

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
            print("PCR9_SEQ_INC: {} (central 9 rising)".format(
                " -> ".join(_fmt(x, 2) for x in q)))
        elif all(q[i] > q[i + 1] for i in range(len(q) - 1)):
            print("PCR9_SEQ_DEC: {} (central 9 falling)".format(
                " -> ".join(_fmt(x, 2) for x in q)))
        else:
            print("PCR9_SEQ_FLAT: {} (not monotonic)".format(
                " -> ".join(_fmt(x, 2) for x in q)))

    # Full-17 trend detection + shared window trim.
    if gate and len(avg_oipcr_list) == window:
        p = avg_oipcr_list
        steps = _seq_pct_steps(p)
        seq = " -> ".join(_fmt(x, 2) for x in p)
        step_str = " | ".join("step{} {:+}%".format(i + 1, s)
                              for i, s in enumerate(steps))
        if all(p[i] < p[i + 1] for i in range(len(p) - 1)):
            inc = True
            print("PCR_SEQ_INC: {} | {}".format(seq, step_str))
            print("isPcrInc = True")
        elif all(p[i] > p[i + 1] for i in range(len(p) - 1)):
            dec = True
            print("PCR_SEQ_DEC: {} | {}".format(seq, step_str))
            print("isPcrDecr = True")
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

    return avg_oipcr_list, avg_oipcr9_list, inc, dec


def log_supp_res_oi(snapshot, oi_deltas, sr_line):
    """
    Log the Sensex-equivalent CE/PE OI + OI-change detail AT the S/R line
    (Req 9.4, 9.7, 9.8).

    Mirrors the Sensex block::

        CEoich val =  48140.0  PEoich val =  84900.0 , CE < PE by 43.3%
        SUPP_RES TOTAL OI: CE= 894200  PE= 1093900 , CE < PE by 18.3%

    ``CEoich``/``PEoich`` are the bot's OWN same-strike consecutive-snapshot OI
    deltas (Req 9.8) at the S/R strike; the TOTAL OI line is the current CE/PE
    open interest (Req 9.7) at that strike. The ``sr_line`` is on the 500-point
    S/R grid, which does NOT always align to the 200-point option strike grid
    (e.g. 81500), so the OI lookup SNAPS the line to the nearest 200 strike
    before reading the chain. The snapped strike is printed so the OI source is
    unambiguous.
    """
    rows = snapshot.get("rows") or {}
    # Snap the 500-grid S/R line to the nearest 200-point option strike for the
    # chain OI lookup (BTC strikes are on STRIKE_STEP=200; a 500 line like 81500
    # is not a tradeable strike).
    try:
        sr_key = int(round(float(sr_line) / STRIKE_STEP) * STRIKE_STEP)
    except (TypeError, ValueError):
        sr_key = sr_line
    pair = rows.get(sr_key) or rows.get(sr_line) or {}
    call_row = pair.get("call") if isinstance(pair, dict) else None
    put_row = pair.get("put") if isinstance(pair, dict) else None
    call_row = call_row if isinstance(call_row, dict) else {}
    put_row = put_row if isinstance(put_row, dict) else {}

    # Raw contracts for the CE/PE comparison (unit-invariant); USD for display.
    ce_oi = call_row.get("oi") or 0.0
    pe_oi = put_row.get("oi") or 0.0
    ce_oi_usd = call_row.get("oi_usd")
    pe_oi_usd = put_row.get("oi_usd")

    delta_row = {}
    if isinstance(oi_deltas, dict):
        delta_row = oi_deltas.get(sr_key) or oi_deltas.get(sr_line) or {}
    ce_oich = delta_row.get("call_oi_delta") if isinstance(delta_row, dict) \
        else None
    pe_oich = delta_row.get("put_oi_delta") if isinstance(delta_row, dict) \
        else None
    ce_oich = ce_oich if ce_oich is not None else 0.0
    pe_oich = pe_oich if pe_oich is not None else 0.0

    # OI changes in USD notional (same basis as the OI columns); % comparison is
    # computed on raw contracts and is identical in either unit.
    ce_oich_disp = _fmt_oi_usd(_contracts_to_usd(ce_oich, ce_oi, ce_oi_usd),
                               signed=True) or _fmt(ce_oich, 1)
    pe_oich_disp = _fmt_oi_usd(_contracts_to_usd(pe_oich, pe_oi, pe_oi_usd),
                               signed=True) or _fmt(pe_oich, 1)

    # Strike label: show the 200-snapped strike where OI was read; note the raw
    # 500-grid S/R line too when it differs (i.e. the line is not a 200 strike).
    try:
        sr_line_i = int(sr_line)
    except (TypeError, ValueError):
        sr_line_i = sr_line
    strike_label = ("{}".format(sr_key) if sr_key == sr_line_i
                    else "{} (S/R line {})".format(sr_key, sr_line_i))

    print("CEoich val =  {}  PEoich val =  {} , {}".format(
        ce_oich_disp, pe_oich_disp, _compare_ce_pe(ce_oich, pe_oich)))
    print("SUPP_RES TOTAL OI (strike {}): CE= {}  PE= {} , {}".format(
        strike_label,
        _fmt_oi_abs(ce_oi, ce_oi_usd), _fmt_oi_abs(pe_oi, pe_oi_usd),
        _compare_ce_pe(ce_oi, pe_oi)))


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

    # 8. Nearest-500 S/R line + role from the anchor (Req 9.13/9.14).
    sr_line, sr_role, sr_position, ntl, nth = compute_support_resistance(
        index_price)

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
     is_pcr_seq_inc, is_pcr_seq_dec) = update_pcr_trend_window(
         avg_oipcr_list, avg_oipcr9_list,
         atm_not_shifted_count, is_atm_shift)

    log_support_resistance(index_price, sr_line, sr_role, sr_position,
                           ntl, nth)

    # CE/PE OI + OI-change detail AT the S/R line (Sensex-equivalent).
    log_supp_res_oi(snapshot, oi_deltas, sr_line)

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
        # ATM-shift + rolling OI-PCR state carried into the next cycle
        # (Sensex-equivalent summary; Req 9.7/9.10).
        "prev_atm": curr_atm,
        "avg_oipcr_list": avg_oipcr_list,
        "avg_oipcr9_list": avg_oipcr9_list,
        "atm_not_shifted_count": atm_not_shifted_count,
        "is_pcr_seq_inc": is_pcr_seq_inc,
        "is_pcr_seq_dec": is_pcr_seq_dec,
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
