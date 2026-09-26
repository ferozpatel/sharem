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
from datetime import datetime

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

# Credit-spread + protective hedge structure (Req 9.16).
ALWAYS_CREDIT = True

# Pre-expiry lead time (minutes) during which no new entries open, ending at the
# 12:00 UTC daily expiry (Req 9.24).
PRE_EXPIRY_LEAD_MINUTES = 15

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
                ts=_now_ts(),
                idx=INDEX_ANCHOR_SYMBOL, ip=_fmt(index_price),
                perp=PERP_SYMBOL, pp=_fmt(perp_price),
                atm=atm_strike if atm_strike is not None else "N/A",
                pcr=_fmt(pcr, 4),
                bias=bias if bias is not None else "N/A")
    if extra:
        line += " " + " ".join("{}={}".format(k, v) for k, v in extra.items())
    print(line)


def log_pcr_table(snapshot, pcr):
    """
    Log the PCR table: the per-strike put/call OI over the central strikes plus
    the aggregate Put-Call Ratio (Req 9.4, 9.7).

    `snapshot` is the 17-strike snapshot from ``helper.buildChainSnapshot``;
    `pcr` is the aggregate value from ``helper.computePCR``. Reads absolute
    open interest only — the log-only 6-hour OI field is never consulted here.
    """
    print("PCR_TABLE [{ts}] aggregate_PCR={pcr} (central 9 strikes)".format(
        ts=_now_ts(), pcr=_fmt(pcr, 4)))
    if not isinstance(snapshot, dict):
        print("  (no snapshot available)")
        return
    rows = snapshot.get("rows") or {}
    print("  {:>10} {:>14} {:>14}".format("strike", "put_oi", "call_oi"))
    for strike in snapshot.get("strikes", []):
        pair = rows.get(strike) or {}
        put_row = pair.get("put") or {}
        call_row = pair.get("call") or {}
        put_oi = put_row.get("oi") if isinstance(put_row, dict) else None
        call_oi = call_row.get("oi") if isinstance(call_row, dict) else None
        print("  {:>10} {:>14} {:>14}".format(
            strike, _fmt(put_oi, 1), _fmt(call_oi, 1)))


def log_chain_snapshot(snapshot):
    """
    Log the Option_Chain_Snapshot table: for each of the 17 strikes the call/put
    price and absolute OI, plus the exchange 6-hour OI change for observation
    only (Req 9.4, 9.6, 9.9).

    The 6-hour OI-change field is logged strictly for observation and never
    feeds signal logic (Req 9.9).
    """
    if not isinstance(snapshot, dict):
        print("CHAIN_SNAPSHOT [{ts}] (no snapshot available)".format(
            ts=_now_ts()))
        return
    print("CHAIN_SNAPSHOT [{ts}] atm_strike={atm} step={step} "
          "strikes={cnt}".format(
              ts=_now_ts(), atm=snapshot.get("atm_strike"),
              step=snapshot.get("step"),
              cnt=len(snapshot.get("strikes", []))))
    rows = snapshot.get("rows") or {}
    print("  {:>10} {:>12} {:>12} {:>12} {:>12} {:>14}".format(
        "strike", "call_px", "call_oi", "put_px", "put_oi", "oi_chg_6h*"))
    for strike in snapshot.get("strikes", []):
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
        print("  {:>10} {:>12} {:>12} {:>12} {:>12} {:>14}".format(
            strike,
            _fmt(call_row.get("price")), _fmt(call_row.get("oi"), 1),
            _fmt(put_row.get("price")), _fmt(put_row.get("oi"), 1),
            _fmt(oi_chg_6h, 1)))
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
              ts=_now_ts(), ip=_fmt(index_price),
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
        ts=_now_ts(), verdict=verdict)
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


def run_signal_cycle(client, prev_snapshot=None, prev_pcr=None,
                     prev_pcr_trend=None):
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
         6-hour field is logged via ``log_chain_snapshot`` only (Req 9.9).
      8. Derive the S/R band + no-trade zone from the anchor (Req 9.13/9.14).
      9. Track the consecutive same-strike PCR change across cycles (Req 9.10).
     10. Derive the directional bias from PCR + index position (Req 9.13).
     11. Emit the full logging surface (signal, chain, PCR, S/R, would-enter).

    Returns a state dict — ``{"snapshot", "pcr", "pcr_trend", "oi_deltas",
    "bias", "decision", "index_price", "perp_price"}`` — so the caller can carry
    ``snapshot``/``pcr``/``pcr_trend`` into the next cycle to diff against
    (Req 9.8, 9.10). NEVER calls ``_place_live_entry``.
    """
    # 1. Index anchor (Req 9.11).
    index_price = helper.getIndexPrice(client, INDEX_ANCHOR_SYMBOL)

    # 2. Perpetual price — log-only reference, never the anchor (Req 9.12).
    try:
        perp_price = helper.manualLTP(PERP_SYMBOL, client)
    except Exception as exc:  # noqa: BLE001 - perp is log-only; never fatal
        perp_price = None
        print("SIGNAL_WARN [{ts}] perpetual {sym} price unavailable "
              "(log-only): {typ}: {msg}".format(
                  ts=_now_ts(), sym=PERP_SYMBOL,
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
    #    exchange 6-hour field is log-only and surfaced via log_chain_snapshot
    #    (Req 9.9); it is intentionally NOT consulted for any signal here.
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
    log_chain_snapshot(snapshot)
    log_pcr_table(snapshot, pcr)
    log_support_resistance(index_price, support, resistance, ntl, nth,
                           midpoint=midpoint)

    # Same-strike OI-delta line (bot's OWN signal-logic OI change, Req 9.8),
    # kept distinct from the log-only exchange 6-hour field (Req 9.9).
    print("OI_DELTA [{ts}] same-strike consecutive-snapshot OI change "
          "(own, signal-logic; exchange 6h field is log-only):".format(
              ts=_now_ts()))
    if oi_deltas:
        print("  {:>10} {:>16} {:>16}".format(
            "strike", "call_oi_delta", "put_oi_delta"))
        for strike in snapshot.get("strikes", []):
            row = oi_deltas.get(strike) or oi_deltas.get(int(strike)) or {}
            print("  {:>10} {:>16} {:>16}".format(
                strike, _fmt(row.get("call_oi_delta"), 1),
                _fmt(row.get("put_oi_delta"), 1)))
    else:
        print("  (first cycle — no previous snapshot to diff against)")

    # Consecutive same-strike PCR-trend line (Req 9.10).
    print("PCR_TREND [{ts}] direction={dir} consecutive_cycles={streak} "
          "prev_pcr={prev} curr_pcr={curr}".format(
              ts=_now_ts(), dir=pcr_trend.get("direction"),
              streak=pcr_trend.get("streak"),
              prev=_fmt(prev_pcr, 4), curr=_fmt(pcr, 4)))

    # Observation-only would-have-entered decision (no live order, Req 9.4).
    log_would_have_entered(
        decision, reason=reason, bias=bias, position=position,
        pcr=_fmt(pcr, 4), pcr_label=pcr_label,
        pcr_trend="{}x{}".format(pcr_trend.get("direction"),
                                 pcr_trend.get("streak")))

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
    }


def checkCriteriaAndTakeTrade(client, state=None):
    """
    Sensex-style per-cycle entry point (mirrors ``checkCriteriaAndTakeTrade``).

    Thin wrapper over ``run_signal_cycle`` that threads the carried observation
    state — the previous cycle's ``snapshot``/``pcr``/``pcr_trend`` — so the
    same-strike OI delta (Req 9.8) and consecutive PCR trend (Req 9.10) diff
    against the prior cycle. Returns the updated state dict for the next call.
    In observation mode this evaluates and LOGS only; it places no orders and
    never calls ``_place_live_entry`` (Req 9.4).
    """
    state = state or {}
    return run_signal_cycle(
        client,
        prev_snapshot=state.get("snapshot"),
        prev_pcr=state.get("pcr"),
        prev_pcr_trend=state.get("pcr_trend"))


# ============================================================
# LIVE-ORDER HOOK (Req 9.5) — implemented in Task 14.3
# ============================================================
def _place_live_entry(*args, **kwargs):
    """
    Live credit-spread entry hook (Req 9.5, 9.16).

    Placeholder for the live-order path taken ONLY when ``OBSERVATION_MODE`` is
    disabled after the operator validates the observation logs. The actual
    order placement (sell the main leg + buy the OTM protective hedge via
    ``helper_delta``) is implemented in Task 14.3. It is intentionally NOT
    implemented here: Task 14.1 is observation-only and places no orders.
    """
    raise NotImplementedError(
        "Live entry placement is implemented in Task 14.3; Strategy_BTC_Options "
        "runs in observation mode and places no orders in Task 14.1.")


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
        ts=_now_ts()))

    # --- Establish the client connection FIRST (Req 9.1, 9.2) -------------
    try:
        client = require_delta()
    except CredentialLoadError as exc:
        # Named credential-load failure: the message identifies the exact
        # credential file the operator must supply.
        print("STARTUP_ERROR [{ts}] Delta client connection could NOT be "
              "established (credential load failure): {msg}".format(
                  ts=_now_ts(), msg=exc))
        if getattr(exc, "expected_file", None):
            print("STARTUP_ERROR expected credential file: {}".format(
                exc.expected_file))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None
    except Exception as exc:  # noqa: BLE001 - any connection failure aborts
        print("STARTUP_ERROR [{ts}] Delta client connection could NOT be "
              "established: {typ}: {msg}".format(
                  ts=_now_ts(), typ=type(exc).__name__, msg=exc))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None

    if client is None:
        # Defensive: require_delta returned no client without raising.
        print("STARTUP_ERROR [{ts}] Delta client is unavailable (no client "
              "constructed).".format(ts=_now_ts()))
        print("STARTUP_ABORT: stopping without evaluating entry conditions; "
              "no orders placed (Req 9.2).")
        return None

    # --- Initialize + log the configuration (Req 9.1, 9.27) ---------------
    print("STARTUP [{ts}] Delta client connection established.".format(
        ts=_now_ts()))
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
              ts=_now_ts(), n=SIGNAL_INTERVAL_SEC))
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
                          ts=_now_ts(), typ=type(exc).__name__, msg=exc))
            time.sleep(SIGNAL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("SIGNAL_LOOP [{ts}] stopped by operator (KeyboardInterrupt); "
              "no orders were placed.".format(ts=_now_ts()))

    return client


if __name__ == "__main__":
    run()
