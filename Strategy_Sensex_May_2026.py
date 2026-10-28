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
stock = "SENSEX"  # SENSEX (BSE) — weekly Thursday expiry, lot=20

# Sensex weekly expiry helper — computes next Thursday in Fyers format
# Format: YY+MMM for monthly (e.g. 26JUN), YY+M+DD for weekly non-month-end
# Fyers convention for Sensex weekly: YY+M+DD where M is single digit for Jan-Sep, O/N/D for Oct-Nov-Dec
# But verified format from logs is YY+M+DD (e.g. 25311 = 2025-03-11) for weekly
# For weekly Thursday in current month we use: YY + month_code + DD
def _get_next_thursday():
    """Return next Thursday from today (or today if today is Thursday)."""
    today = datetime.now().date()
    days_until_thu = (3 - today.weekday()) % 7  # Thursday = 3
    if days_until_thu == 0 and datetime.now().hour >= 15 and datetime.now().minute >= 30:
        days_until_thu = 7  # if expiry day after market close, use next week
    return today + timedelta(days=days_until_thu)

def _get_last_thursday(year, month):
    """Get last Thursday of given month (Sensex monthly expiry day)."""
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    d = datetime(year, month, last_day)
    while d.weekday() != 3:  # 3 = Thursday
        d -= timedelta(days=1)
    return d.date()

def getSensexWeeklyExpiry():
    """Build Fyers-format expiry string for Sensex weekly options.

    Format rules (Fyers BSE Sensex):
    - Monthly (last Thursday of month): YY + MMM (e.g. 26JUN)
    - Weekly (other Thursdays): YY + M + DD where:
        Jan-Sep -> single digit '1'-'9'
        Oct -> 'O', Nov -> 'N', Dec -> 'D'
    """
    expiry = _get_next_thursday()
    last_thu = _get_last_thursday(expiry.year, expiry.month)
    yy = str(expiry.year)[-2:]
    if expiry == last_thu:
        # Monthly expiry — use 3-letter month code
        return yy + expiry.strftime("%b").upper()
    # Weekly format
    month_code_map = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
                      7: "7", 8: "8", 9: "9", 10: "O", 11: "N", 12: "D"}
    mc = month_code_map[expiry.month]
    dd = f"{expiry.day:02d}"
    return yy + mc + dd

if tradeOption == 1:
    checkInstrument = helper.getIndexSpot(stock)  # BSE:SENSEX-INDEX
    bnExpDate = getSensexWeeklyExpiry()
else:
    checkInstrument = stock

print("checkInstrument = ", checkInstrument)
print("sensexExpDate = ", bnExpDate)

# Sensex options need BSE: prefix — helper.getOptionFormat hardcodes NSE: so we override
def getOptionFormatSensex(intExpiry, strike, ce_pe):
    return "BSE:SENSEX" + str(intExpiry) + str(strike) + str(ce_pe)

st = 0

# ============================================================
# OBSERVATION_MODE — log signals without placing real orders
# ============================================================
# Set True for first 3-4 days to validate Sensex strategy on real data.
# When True:
#   - All detection runs as normal (PCR, CHOI, S/R, candle range, etc.)
#   - When entry conditions are met → log "WOULD_HAVE_ENTERED" with details
#   - takeEntry() returns without placing orders or changing `st`
#   - Strategy keeps observing all day → captures every potential entry
# Set False after validation period to enable real trading.
# ============================================================
OBSERVATION_MODE = False

timeFrame = 3  # in minutes
timeFrame2 = 1  # in minutes

# === REAL-TIME LTP-BASED SL/TARGET MONITORING ===
# Poll the short-leg LTP every LTP_POLL_INTERVAL seconds (instead of waiting for the
# 1-min candle close) so a fast adverse move is caught in seconds, not up to a minute.
# Require SL_CONFIRM_TICKS consecutive breached polls before exiting — filters out a
# single jumpy/stale option quote that instantly reverts (fake-spike whipsaw).
LTP_POLL_INTERVAL = 2   # seconds between live LTP checks
SL_CONFIRM_TICKS = 2    # consecutive breached polls needed to trigger SL exit
TGT_CONFIRM_TICKS = 2   # consecutive polls needed to trigger target exit

# Trailing SL: once the trade is TRAIL_TRIGGER_TARGET_FRACTION * effective_tgt in profit
# (i.e. 60% of the way to target), move the SL to breakeven (one-shot, no incremental
# trailing). Refuses to let a solid winner that has covered most of the distance to target
# turn back into a loss, while still giving the trade room in the first half of the move.
TRAIL_TRIGGER_TARGET_FRACTION = 0.50
# SL/target multipliers on the option's median HIGH-LOW range, per DIRECTION. These feed BOTH the
# pre-entry qty sizing (effective_sl_pre in takeEntryCredit/Debit) and the post-entry SL/target,
# so the two always agree. Any change here MUST be made to the SL and TARGET pair together.
#
# 2026-10-15: widened to 2.4 / 4.0 for BOTH directions (was bull 1.8/2.1, bear 1.6/3.0) to give
# trades room to survive the ~150-spot-point counter-wiggle that was stopping out otherwise-correct
# entries within 2-3 candles. R:R ~1:1.67.
#
# These multipliers are only safe because the median itself is now sanity-checked — see the
# LOW-SAMPLE guard below. On 2026-10-15 a 2-candle median of 67.55 combined with these same
# multipliers produced SL=377 (premium had to rise 75%) and a NEGATIVE target of -54.95. The
# multipliers were not the problem; the unguarded median was.
# 2026-10-22 (user): target 4.0 -> 4.5 and SL 2.4 -> 2.2, BOTH directions.
# 2026-10-end (user): SL 2.2 -> 2.0, BOTH directions. R:R now 4.5/2.0 = 2.25.
# Backed by Aug-Oct log review: across the spot-anchor months 10 of 11 winning
# trades pulled back <= 1.32x the median before working, so a 2.0x stop clears
# the body of real winners while tightening R:R vs 2.2.
#
# NOTE the two legs pull in opposite directions on risk:
#   * A TIGHTER SL means the premium has less room before the stop fires, so
#     expect more stop-outs on a counter-wiggle.
#   * A tighter SL also SHRINKS net_sl_points, and qty = FIXED_RISK /
#     (net_sl * LOT_SIZE), so LOTS GO UP for the same Rs 4,000 budget.
#     Rupee risk per trade is unchanged; position size is larger.
SL_MULT_BULL = 2.0
TARGET_MULT_BULL = 4.5
SL_MULT_BEAR = 2.0
TARGET_MULT_BEAR = 4.5

# ONE TRADE PER DAY (user, 2026-10-end): the day ends after the FIRST completed
# trade, win OR loss. A TARGET win already stops re-entry (st = -1). A LOSS now
# also stops the day: the entry gate requires slCount != 1, so the single
# allowed SL (slCount 0 -> 1) halts all further entries for the session.
# (Previously slCount != 2 allowed a re-entry after the first SL.)
# Trailing SL (move SL to breakeven once TRAIL_TRIGGER_TARGET_FRACTION of target is covered) is
# DISABLED for BOTH directions: trailing kept exiting trades at breakeven on a normal pullback
# instead of letting them reach target. Every trade now keeps its ORIGINAL SL for the whole trade.
TRAIL_SL_FOR_BULL = False
TRAIL_SL_FOR_BEAR = False

# ============================================================
# MEDIAN-RANGE LOW-SAMPLE GUARD (see get_option_candle_range)
# ============================================================
# The median option candle range drives BOTH sizing and SL/target. It needs enough candles
# to be meaningful. An entry before ~9:40 leaves only 2-3 completed candles after the
# first-candle skip, and all of them are opening volatility -> inflated median.
#
# Evidence (98 OPT_RANGE_DEBUG samples, all past sessions): avg median by sample count is
# 26.02 at 2 candles vs 14.27 at 10 candles. Only 3 samples ever reached 40+:
#   2026-10-15 -> 67.55 on 2 candles   (sampling artifact — this guard targets it)
#   2026-06-24 -> 43.43 on 10 candles  (real volatility — left untouched by design)
#   2026-09-30 -> 40.30 on 4 candles   (meets the minimum, so NOT halved)
MIN_MEDIAN_CANDLES = 4            # a median below this many candles is treated as unreliable
LOW_SAMPLE_MEDIAN_THRESHOLD = 40  # only halve a thin-window median if it exceeds this
LOW_SAMPLE_MEDIAN_FACTOR = 0.5    # halve it (67.55 -> 33.78)

# ============================================================
# TARGET-REACHABILITY MEDIAN CAP (CREDIT spreads only)
# ============================================================
# On a CREDIT spread the target level is  premium - (median x TARGET_MULT).  When the
# median is large relative to the sold premium that level goes to zero or NEGATIVE, i.e.
# the option would have to trade below zero to pay out — the trade then has no exit but
# the SL or the 3:24 square-off. That is exactly what happened on 2026-10-15:
#   median 67.55, premium 215.05 -> target = 215.05 - (67.55 x 4.0) = -54.95  (impossible)
#
# RULE: cap the MEDIAN so the target never asks the premium to fall more than
# MAX_TGT_PREMIUM_FRACTION of its entry value. Capping the median (rather than the target
# alone) keeps SL, target and qty sizing all on ONE basis, so R:R stays exactly
# TARGET_MULT/SL_MULT and tradeOptRange remains the single source of truth.
#
#   median_cap = premium * MAX_TGT_PREMIUM_FRACTION / TARGET_MULT
#
# At TARGET_MULT 4.5 and 0.75 this binds when median > premium * 0.1667, and when it binds
# the target level lands at premium * 0.25 and the SL at premium * 0.3667.
#
# SIDE EFFECTS when it fires (by design — the levels share one basis):
#   * the SL also tightens (it is median x SL_MULT on the same capped median)
#   * a tighter SL means MORE lots for the same Rs 4,000 budget (sizing is risk-based)
# Watch for MEDIAN_CAPPED lines in the log to see how often this binds.
#
# NOT applied to DEBIT spreads: there the target is premium + (median x TARGET_MULT), which
# rises and can never be unreachable, so capping would shrink the median for no reason.
MAX_TGT_PREMIUM_FRACTION = 0.75

# Lot sizing rounds UP when the fractional lot is >= this (else floors). 0.75 means a 3.75+
# raw lot count becomes 4. Recovers position size lost to flooring (see calc_lots_by_risk);
# worst-case net SL overshoot above FIXED_RISK_PER_TRADE is ~(1 - 0.75) = 25% of one lot.
LOT_ROUNDUP_FRACTION = 0.75

qty = 40  # 2 lots x 20 = 40 (Sensex lot = 20) — default/fallback; overridden by risk-based sizing
sl_point = 50
target_point = 60

# ============================================================
# RISK-BASED POSITION SIZING (NET-OF-HEDGE)
# ============================================================
# Lots are sized so a SL-hit loses ~FIXED_RISK_PER_TRADE rupees NET of the hedge.
# The SL is on the SHORT leg (effective_sl points), but the hedge offsets part of
# that loss. How much it offsets depends on the hedge's premium relative to the sold
# leg (a proxy for its delta): a closer/costlier hedge offsets more, a far/cheaper
# hedge offsets less. Using both premiums keeps the NET rupee risk consistent across
# days even though the spread width (and hedge premium) varies.
#
#   hedge_offset_ratio = hedge_median_range / main_median_range   (preferred)
#   net_sl_points      = effective_sl * (1 - hedge_offset_ratio)
#   lots               = FIXED_RISK_PER_TRADE / (net_sl_points * LOT_SIZE)
#
# OFFSET RATIO SOURCE (why range, not premium):
# The offset that actually hits P&L is how far the hedge MOVES when the main leg moves
# (i.e. the delta ratio) — not how much the hedge is WORTH. Premium ratio systematically
# understates it and therefore systematically UNDER-sizes the position:
#   2026-07-29: premium ratio 99/318 = 0.311, but realized offset was 36.89/85.0 = 0.434.
#   Result: intended risk 5000 -> actual risk only ~3800, profit 6735 instead of ~8660.
# Both legs share one underlying, so median high-low range over the same candles is an
# empirical delta proxy (no Greeks needed from the API).
# Fallback chain: hedge range -> premium ratio -> short-leg-only (no offset).
# effective_sl (option candle median x multiplier) itself stays untouched — sizing only.
LOT_SIZE = 20                    # Sensex lot size
# Deep-OTM hedge candles can be thin/stale, making the range ratio noisy. Clamp it so a
# bad data point can never collapse net_sl_points toward zero and over-size the position.
HEDGE_OFFSET_RATIO_MIN = 0.10
HEDGE_OFFSET_RATIO_MAX = 0.75

# Max hedge premium as a fraction of the main-leg premium. The hedge search walks the 500-pt
# grid outward until a candidate is at/below this; a cheaper hedge means a wider spread.
# Lowered 0.60 -> 0.50 after 2026-08-04 Trade 2: the hedge (79000CE @ 191.80 vs main
# 78700CE @ 327.15) came in at 58.6% — just under the old 0.60 cap — so it was accepted on
# the FIRST candidate, only 300 pts away. A near hedge tracks the main leg closely
# (offset_ratio 0.653), which shrinks net_sl_points to 13.2 and sized 18 lots (96% of
# deployable margin). Because qty scales as 1/(1-ratio), any error in the estimated ratio is
# amplified ~1/(1-ratio)^2 — so a wrong ratio blows through FIXED_RISK badly at that size.
# Rejecting an expensive near hedge pushes the search 500 further out, which lowers the ratio
# structurally instead of us artificially discounting a hedge we know is tight.
HEDGE_MAX_PREMIUM_FRACTION = 0.50

# Minimum distance (points) between the main leg and the hedge.
# The search used to start at the ADJACENT 500-grid strike, so the actual gap was an accident
# of grid alignment: ATM 78500 -> 500 away, ATM 78700 -> 300, ATM 78800 -> 200, ATM 78900 ->
# 100, and on the PE side an ATM sitting exactly on a 500 multiple produced the SAME strike as
# the main leg. A near hedge tracks the main leg closely, which inflates offset_ratio, shrinks
# net_sl_points and over-sizes the position (2026-08-04 T2: 300 apart -> ratio 0.653 -> 18
# lots -> 96% of deployable margin; 2026-07-30 T1: 200 apart -> assumed 0.578 vs realized
# 0.353 -> lost 7,096 against a 5,000 budget).
# Starting the search at least this far out makes the gap deterministic (400-800) instead of
# 100-500, so the ratio is structurally low rather than us discounting a hedge we know is tight.
# 400 rejects the 100/200/300 cases that caused the damage while still allowing the adjacent
# 500-grid strike when it is 400+ away (e.g. main 78600CE -> hedge 79000CE). A larger minimum
# would push the hedge needlessly far, making it cheaper, offsetting less and cutting lots.
# HEDGE_MAX_PREMIUM_FRACTION stays as a secondary guard for high-IV days where even a distant
# hedge is expensive.
HEDGE_MIN_DISTANCE = 400
# Anchor for S/R level, PCR-window center, and the FUT/spot-vs-SUPP_RES breakout comparisons.
# Weekly-option OI/max-pain cluster near SPOT (not the monthly future, which carries a full
# month of basis), so spot is the correct reference for S/R. True = use spot, False = monthly
# future (the old behaviour). One-line reversible if live behaviour looks off.
USE_SPOT_ANCHOR = True
# Master switch: always trade CREDIT spreads, never DEBIT. Weekly theta favours the seller,
# and DEBIT has repeatedly lost in chop/expiry (needs a directional move that often doesn't
# come). True disables debit entirely; set False to re-enable the IV-Rank/premium debit rule.
ALWAYS_CREDIT = True
# CHOI fresh-flow dominance confirmation gate wired into the ENTRY criteria. When True, a BULL
# entry additionally requires PE writers freshly DEFENDING the support this cycle
# (isPE_DominantAtSupp_once AND fresh_PE >= 0 = flat or added, not unwound); a BEAR entry
# requires CE writers freshly CAPPING the resistance (isCE_DominantAtRes_once AND fresh_CE >= 0).
# The >= 0 check also excludes the "both sides unwinding (both negative)" case. This is on top
# of the existing price-vs-S/R + PCR-trend gates. Set False to revert to prior behaviour.
USE_CHOI_DOMINANCE_GATE = True
# NO-TRADE ZONE: skip the middle of the 500-pt S/R band. Price must be at least
# NO_TRADE_ZONE_BUFFER points away from the band midpoint before a directional trade is allowed
# — bull only in the lower part (near support), bear only in the upper part (near resistance).
# The dead zone is midpoint ± buffer (i.e. 2*buffer wide). Example, band 75500-76000 (mid 75750),
# buffer 20: bull zone = up to 75730 (support 75500 side), bear zone = from 75770 (resistance
# 76000 side), 75730-75770 = NOTRADEZONE. Set USE_NO_TRADE_ZONE=False to revert to a simple
# midpoint split (no dead zone). Can only REMOVE trades, never add — it's risk-reducing.
USE_NO_TRADE_ZONE = True
NO_TRADE_ZONE_BUFFER = 20
# CREDIT-spread main (sell) leg offset from the synthetic ATM.
#   0   = sell AT the synthetic ATM (highest premium, highest gamma)  <-- ACTIVE
#   100 = sell 1-OTM (bull PE at ATM-100, bear CE at ATM+100)
# ATM gives the richest premium (so the SL/target distances are larger in absolute points),
# at the cost of higher gamma. 1-OTM lowers the sold delta (~0.5 -> ~0.4) for a
# higher-probability short leg with less gamma whipsaw. DEBIT is unaffected either way.
# Always deploy by copying this file to sharem/ and pushing; never hand-edit on the server.
ONE_OTM = 0
FIXED_RISK_PER_TRADE = 4000     # ₹ NET risk per trade if SL hits (after hedge offset)
# MAX_LOTS is now just a sanity backstop — the real capital constraint is the live
# margin check (apply_margin_cap) against DEPLOYABLE_CAPITAL_FRACTION of real available funds.
MAX_LOTS = 40                    # hard safety ceiling (backstop only, not capital-derived)
MIN_LOTS = 1                     # minimum position
# PYRAMIDING: once a trade trails to breakeven (original lot is risk-free), scale in by adding
# up to PYRAMID_ADD_MULTIPLIER x the original qty to BOTH legs on the SAME strikes, sized to
# whatever live margin still allows (as many lots as fit). The combined position then rides the
# SAME target with SL at breakeven for all lots -> winners run at ~3x size (R:R boost). Trade-off:
# if price REVERSES from the trail point back to breakeven, the added lots give back ~trail
# points each (a fatter loss than the base risk budget on those rare days). Set False to disable.
# DISABLED 2026-09-30: pyramiding lost on every live session it fired. The added lots enter at
# the trail level but the SL sits at the ORIGINAL entry, so a reversal costs them the full trail
# distance each (09-30: ~-19k across two trades against a 4k/trade budget), and those exits were
# logged as trailWithPyramidSLCount so the 2-SL daily halt never tripped. Do NOT re-enable until
# the SL is moved to the ADD price (so added lots scratch) AND the SL tracks the NET SPREAD
# (main - hedge) instead of the main leg alone — at the main's entry price the spread is already
# negative because the hedge decays independently.
PYRAMIDING_ENABLED = False
PYRAMID_ADD_MULTIPLIER = 2       # add 2x original qty (total becomes ~3x) when trail activates
# Fraction of available funds usable for a trade. Applied to REAL available funds
# (margin_avail from the broker, fetched live) when available; else to FALLBACK_CAPITAL.
# 0.97 leaves only a 3% buffer against a broker margin-shortfall rejection (which previously
# left a naked leg — see 2026-07-16). Thin buffer: margin can tick up between the check and
# the actual fill, so a very tight day could still see a leg rejected.
DEPLOYABLE_CAPITAL_FRACTION = 0.97
# Used ONLY when the broker's live available-funds figure (margin_avail) can't be fetched.
# Keep at/below your real account balance so a fallback never over-sizes.
FALLBACK_CAPITAL = 800000        # ₹ — hardcoded deployable capital fallback

def calc_lots_by_risk(effective_sl_points, main_premium=None, hedge_premium=None,
                      main_range=None, hedge_range=None, main_delta=None, hedge_delta=None):
    """
    Return qty (lots x LOT_SIZE) sized so a SL-hit loses ~= FIXED_RISK_PER_TRADE NET of hedge.

    Args:
        effective_sl_points: SL distance on the SHORT leg (option candle median x multiplier).
        main_premium: sold (short) leg premium at entry.
        hedge_premium: bought (hedge) leg premium at entry.
        main_range: main leg median high-low 3-min range.
        hedge_range: hedge leg median high-low 3-min range.
        main_delta: main leg option delta at entry (from live greeks).
        hedge_delta: hedge leg option delta at entry (from live greeks).

    Offset ratio priority:
      1) DELTA   — |hedge_delta| / |main_delta|  (most principled: delta IS d(optionprice)/
                   d(underlying), so this is the true instantaneous P&L offset. PE deltas are
                   negative, hence abs() — both legs are the same option type so signs match.)
      2) RANGE   — hedge_range / main_range (empirical proxy; fallback if greeks unavailable)
      3) PREMIUM — hedge_premium / main_premium (last-resort fallback)
      4) NONE    — short-leg-only sizing (no hedge credit) if none available

    All three candidate ratios are logged every time (even the ones not used for sizing) so we
    keep comparing DELTA vs RANGE vs the realized ratio (REALIZED_OFFSET at exit) — the old
    range approach stays visible for validation.

    Returns:
        tuple: (qty, used_offset_ratio) — used_offset_ratio is None if sizing fell back to
        short-leg-only (no hedge credit applied). Caller stores this for the realized-vs-
        assumed offset comparison logged at exit (see exitSpreadPosition).
    """
    if effective_sl_points is None or effective_sl_points <= 0:
        return MIN_LOTS * LOT_SIZE, None

    # --- Compute every candidate ratio (for logging + fallback), then pick by priority ---
    delta_ratio = None
    if (main_delta is not None and hedge_delta is not None
            and abs(main_delta) > 0 and abs(hedge_delta) > 0):
        delta_ratio = abs(hedge_delta) / abs(main_delta)

    range_ratio = None
    if (main_range and hedge_range and main_range > 0 and hedge_range > 0):
        range_ratio = hedge_range / main_range

    premium_ratio = None
    if (main_premium and hedge_premium and main_premium > 0
            and 0 < hedge_premium < main_premium):
        premium_ratio = hedge_premium / main_premium

    # Priority: DELTA -> RANGE -> PREMIUM -> NONE
    hedge_offset_ratio = None
    if delta_ratio is not None:
        raw_ratio = delta_ratio
        ratio_source = "DELTA"
    elif range_ratio is not None:
        raw_ratio = range_ratio
        ratio_source = "RANGE"
    elif premium_ratio is not None:
        raw_ratio = premium_ratio
        ratio_source = "PREMIUM"
    else:
        raw_ratio = None
        ratio_source = "NONE"

    if raw_ratio is not None:
        hedge_offset_ratio = max(HEDGE_OFFSET_RATIO_MIN,
                                 min(raw_ratio, HEDGE_OFFSET_RATIO_MAX))

    # Net-of-hedge SL points: discount the short-leg SL by the hedge's offset share.
    net_sl_points = effective_sl_points
    if hedge_offset_ratio is not None:
        net_sl_points = effective_sl_points * (1 - hedge_offset_ratio)

    def _r(x):
        return round(x, 3) if x is not None else None

    print(f"HEDGE_OFFSET: source={ratio_source}"
          f" used_ratio={_r(hedge_offset_ratio)} raw={_r(raw_ratio)}"
          f" | delta_ratio={_r(delta_ratio)} range_ratio={_r(range_ratio)}"
          f" premium_ratio={_r(premium_ratio)}"
          f" | (main_delta={main_delta} hedge_delta={hedge_delta}"
          f" main_range={main_range} hedge_range={hedge_range})"
          f" effective_sl={effective_sl_points} -> net_sl_points={round(net_sl_points, 2)}")

    if net_sl_points <= 0:
        return MIN_LOTS * LOT_SIZE, hedge_offset_ratio

    loss_per_lot = net_sl_points * LOT_SIZE
    # LOT ROUNDING: plain int() floor used to drop e.g. 3.996 lots -> 3, throwing away ~25% of a
    # position for a few rupees of risk headroom (2026-10-16: 3 lots @ net 79.1 pts = Rs 4,746;
    # the 4th lot was 0.004 away and would have made Rs 6,328). We now round UP when the
    # fractional part is >= LOT_ROUNDUP_FRACTION (0.75), else floor. Worst case this risks
    # ~(1 - 0.75) = 0.25 lot of net SL over the budget — i.e. up to ~25% of one lot's net loss
    # above FIXED_RISK_PER_TRADE on an SL day. MAX_LOTS and the live margin cap still bound it.
    raw_lots = FIXED_RISK_PER_TRADE / loss_per_lot
    frac = raw_lots - int(raw_lots)
    lots = int(raw_lots) + 1 if frac >= LOT_ROUNDUP_FRACTION else int(raw_lots)
    lots = max(MIN_LOTS, min(lots, MAX_LOTS))
    print(f"LOT_ROUNDING: raw_lots={round(raw_lots, 3)} frac={round(frac, 3)}"
          f" (roundup>={LOT_ROUNDUP_FRACTION}) -> lots={lots}")
    return lots * LOT_SIZE, hedge_offset_ratio


def log_atm_greeks(atm_ce_symbol, atm_pe_symbol, fyers_client, strikecount=5):
    """
    DIAGNOSTIC ONLY (does not feed any decision yet): log the synthetic-ATM CE and PE greeks
    at spread-decision time. The two numbers of interest for the credit/debit question (#3):
      - avg_atm_iv = (CE_iv + PE_iv)/2  -> a strike-specific 'is premium rich' gauge, more
        local than India VIX (which is a 30-day index-wide number).
      - iv_skew = PE_iv - CE_iv         -> directional fear (put IV richer = downside demand).
    Logged now to build a sample; NOT wired into choose_spread_type. Never raises.
    """
    try:
        resp = helper.getOptionChainWithGreeks(strikecount, "BSE:SENSEX-INDEX", fyers_client)
        g = helper.getDeltaForSymbols(resp, [atm_ce_symbol, atm_pe_symbol])
        ce = g.get(atm_ce_symbol)
        pe = g.get(atm_pe_symbol)
        ce_iv = ce.get("iv") if ce else None
        pe_iv = pe.get("iv") if pe else None
        avg_iv = round((ce_iv + pe_iv) / 2.0, 2) if (ce_iv is not None and pe_iv is not None) else None
        skew = round(pe_iv - ce_iv, 2) if (ce_iv is not None and pe_iv is not None) else None
        print(f"ATM_GREEKS: CE={atm_ce_symbol} -> {ce}")
        print(f"ATM_GREEKS: PE={atm_pe_symbol} -> {pe}")
        print(f"ATM_IV: ce_iv={ce_iv} pe_iv={pe_iv} avg_atm_iv={avg_iv} iv_skew(PE-CE)={skew}")
    except Exception as e:
        print("ATM_GREEKS_LOG_FAILED (non-fatal):", e)


def get_leg_deltas(main_symbol, hedge_symbol, fyers_client, strikecount=20):
    """
    Fetch live delta for the main and hedge leg in ONE greeks-enabled option-chain call.
    Used to feed the DELTA offset ratio in calc_lots_by_risk.

    Returns:
        tuple: (main_delta, hedge_delta) — either can be None if unavailable, in which case
        calc_lots_by_risk falls back to the range/premium ratio. Never raises: any failure
        returns (None, None) so sizing degrades gracefully instead of blocking a live entry.
    Also prints DELTA_CHECK lines (delta/gamma/theta/vega/iv for both legs + delta ratio)
    so the greeks are captured in the log for offline comparison.
    """
    try:
        resp = helper.getOptionChainWithGreeks(strikecount, "BSE:SENSEX-INDEX", fyers_client)
        greeks = helper.getDeltaForSymbols(resp, [main_symbol, hedge_symbol])
        main_g = greeks.get(main_symbol)
        hedge_g = greeks.get(hedge_symbol)
        print(f"DELTA_CHECK: main={main_symbol} -> {main_g}")
        print(f"DELTA_CHECK: hedge={hedge_symbol} -> {hedge_g}")
        main_delta = main_g.get("delta") if main_g else None
        hedge_delta = hedge_g.get("delta") if hedge_g else None
        if main_delta and hedge_delta and abs(main_delta) > 0:
            print(f"DELTA_CHECK: delta_ratio (|hedge|/|main|) = "
                  f"{round(abs(hedge_delta) / abs(main_delta), 3)}")
        return main_delta, hedge_delta
    except Exception as e:
        print("GET_LEG_DELTAS_FAILED (non-fatal, sizing falls back to range/premium):", e)
        return None, None


def _strike_eq(a, b, tol=1.0):
    """Tolerance-based compare for option-chain strike_price (float) vs our int strike."""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def select_hedge_by_delta(main_strike, option_type, intExpiry, fyers_client, strikecount=20):
    """
    DELTA-BASED hedge selection (replaces the premium-fraction + 500-grid walk).

    COVERAGE: this single helper serves ALL FOUR entry paths — the caller just passes the
    main leg's option_type:
      - CREDIT bull  -> main sell ATM PE, hedge PE (OTM below main)
      - CREDIT bear  -> main sell ATM CE, hedge CE (OTM above main)
      - DEBIT  bull  -> main buy  ATM CE, hedge CE (OTM above main)
      - DEBIT  bear  -> main buy  ATM PE, hedge PE (OTM below main)
    All delta maths is SIGN-AGNOSTIC (abs() everywhere) — CE deltas are positive, PE deltas
    negative, but only magnitude matters. The band bounds are DERIVED DYNAMICALLY from the
    nearest delta (NOT hardcoded) — see step 5.

    Logic:
      1. Fetch the greeks-enabled option chain ONCE.
      2. Read the MAIN leg's |delta| (match strike_price + option_type).
      3. target_hedge_delta = |main_delta| / 2   (sign ignored — puts have negative delta).
      4. Scan same-option-type strikes on the OTM side only (PE: strikes BELOW main;
         CE: strikes ABOVE main) and pick the one whose |delta| is NEAREST the target.
      5. Snap that strike to a 500 multiple for liquidity, biased AWAY from ATM so the
         hedge delta never overshoots the target toward ATM:
           - if the nearest-delta strike is already a 500 multiple -> use it;
           - else if a 500-multiple strike has |delta| within the band [nearest, .x6] ->
             use that (slightly toward ATM but same ballpark). The band is COMPUTED from the
             nearest delta, not fixed: nearest 0.24 -> [0.24, 0.26]; nearest 0.31 -> [0.31,
             0.36]. Only a toward-ATM 500 strike within ~2 pips qualifies.
           - EDGE CASE: if the nearest delta is already above the band cap (band empty, e.g.
             nearest 0.27 -> [0.27, 0.26]), only accept a 500-multiple whose |delta| matches
             the nearest/target near-exactly (within 0.01); otherwise go far OTM.
           - else snap FAR OTM to the nearest 500 (floor for PE, ceil for CE) — the hedge
             delta may then be < target, which is the safe (cheaper/wider) direction.
      6. Guard (strictly-OTM, no-op with the above): never let the snapped strike land
         on/through the main strike.
      7. Look up the snapped strike's delta (for the offset ratio / logging).

    This ALSO serves the delta-based lot sizing: it returns both leg deltas, so the caller
    no longer needs a separate get_leg_deltas() call (net API calls stay ~2 greeks fetches).

    Returns:
        (hedge_symbol, main_delta, hedge_delta) on success.
        (None, None, None) on ANY failure — caller then falls back to the premium+distance
        walk. Never raises: a greeks hiccup must never block a live entry.
    """
    try:
        resp = helper.getOptionChainWithGreeks(strikecount, "BSE:SENSEX-INDEX", fyers_client)
        if not resp or resp.get("code") != 200:
            print(f"HEDGE_BY_DELTA: chain fetch failed (code={resp.get('code') if resp else None}) "
                  f"- falling back to premium walk")
            return None, None, None
        chain = resp.get("data", {}).get("optionsChain", [])

        # Build strike -> delta map for THIS option type (only rows with a real greeks block).
        same_type = []
        main_delta = None
        for opt in chain:
            if opt.get("option_type") != option_type:
                continue
            greeks = opt.get("greeks")
            if not greeks or greeks.get("delta") is None:
                continue
            sp = opt.get("strike_price")
            d = greeks.get("delta")
            same_type.append((sp, d))
            if _strike_eq(sp, main_strike):
                main_delta = d

        if main_delta is None or abs(main_delta) <= 0:
            print(f"HEDGE_BY_DELTA: main delta not found for strike={main_strike} {option_type} "
                  f"- falling back to premium walk")
            return None, None, None

        target = abs(main_delta) / 2.0

        # OTM side only: PE hedge is BELOW main strike, CE hedge is ABOVE main strike.
        if option_type == "PE":
            otm = [(sp, d) for (sp, d) in same_type if float(sp) < float(main_strike)]
        else:  # CE
            otm = [(sp, d) for (sp, d) in same_type if float(sp) > float(main_strike)]

        if not otm:
            print(f"HEDGE_BY_DELTA: no OTM {option_type} strikes with greeks vs main={main_strike} "
                  f"- falling back to premium walk")
            return None, None, None

        # Strike whose |delta| is nearest the target hedge delta.
        best_sp, best_d = min(otm, key=lambda x: abs(abs(x[1]) - target))
        best_sp = int(round(float(best_sp)))
        nearest_abs = abs(best_d)

        # === SNAP TO A 500 MULTIPLE (liquidity), biased AWAY from ATM ===
        # Rule (avoids the old round()-toward-ATM delta overshoot):
        #   1. If the nearest-delta strike is already a 500 multiple -> use it.
        #   2. Else look for a 500-multiple strike whose |delta| stays within the band
        #      [nearest, .x6] (dynamic). e.g. nearest 0.24 -> band [0.24, 0.26]. These sit
        #      just toward ATM; taking one keeps the hedge delta in the same ballpark.
        #   2b. EDGE CASE: if the nearest delta is already ABOVE the band cap (band empty,
        #      e.g. nearest 0.27 -> band [0.27, 0.26]), do NOT stretch toward ATM by a whole
        #      band. Only accept a 500-multiple whose |delta| (near-)EXACTLY matches the
        #      nearest/target delta (within 0.01); otherwise fall through to far OTM.
        #   3. If no qualifying 500-multiple -> snap FAR OTM (away from ATM) to the nearest
        #      500: floor (lower strike) for PE, ceil (higher strike) for CE. Safe direction —
        #      hedge delta undershoots the target rather than overshoots.
        if best_sp % 500 == 0:
            snapped = best_sp
            snap_reason = "nearest_is_500mult"
        else:
            band_low = nearest_abs
            # band_high = first-decimal digit of band_low + 0.06 (dynamic, not hardcoded):
            # 0.24 -> 0.26, 0.31 -> 0.36. If band_low already exceeds band_high the band is
            # empty -> handled by the exact-match edge-case branch below.
            band_high = math.floor(band_low * 10) / 10.0 + 0.06
            if band_low <= band_high:
                band_500 = [(int(round(sp)), d) for (sp, d) in otm
                            if int(round(sp)) % 500 == 0 and band_low <= abs(d) <= band_high + 1e-9]
                _band_tag = f"500mult_in_band[{round(band_low, 2)}-{round(band_high, 2)}]"
            else:
                # EDGE CASE: band empty -> only a near-EXACT delta match on a 500 strike.
                band_500 = [(int(round(sp)), d) for (sp, d) in otm
                            if int(round(sp)) % 500 == 0 and abs(abs(d) - band_low) <= 0.01]
                _band_tag = f"500mult_exact~{round(band_low, 2)}"
            if band_500:
                # Most-OTM qualifying 500 strike = the one whose |delta| is nearest the target.
                bsp, _bd = min(band_500, key=lambda x: abs(abs(x[1]) - target))
                snapped = int(bsp)
                snap_reason = _band_tag
            else:
                if option_type == "PE":
                    snapped = int(math.floor(float(best_sp) / 500.0) * 500)
                else:  # CE
                    snapped = int(math.ceil(float(best_sp) / 500.0) * 500)
                snap_reason = "far_otm_snap"

        # Strictly-OTM safety guard (no-op with the above, kept for defense): never let the
        # snapped strike land on/through the main strike (would flip it to the ITM side).
        if option_type == "PE" and snapped >= main_strike:
            snapped = int((math.ceil(main_strike / 500.0) - 1) * 500)
        elif option_type == "CE" and snapped <= main_strike:
            snapped = int((math.floor(main_strike / 500.0) + 1) * 500)

        # Delta of the FINAL snapped strike (nearest match in the chain, if present).
        hedge_delta = None
        for (sp, d) in same_type:
            if _strike_eq(sp, snapped):
                hedge_delta = d
                break

        hedge_symbol = getOptionFormatSensex(intExpiry, snapped, option_type)
        print(f"HEDGE_BY_DELTA: main={main_strike}{option_type} main_delta={main_delta} "
              f"target_hedge_delta={round(target, 4)} nearest_strike={best_sp} "
              f"nearest_delta={best_d} -> snapped500={snapped} ({snap_reason}) "
              f"hedge={hedge_symbol} hedge_delta={hedge_delta}")
        if main_delta and hedge_delta and abs(main_delta) > 0:
            print(f"DELTA_CHECK: main={getOptionFormatSensex(intExpiry, main_strike, option_type)} "
                  f"delta={main_delta}")
            print(f"DELTA_CHECK: hedge={hedge_symbol} delta={hedge_delta}")
            print(f"DELTA_CHECK: delta_ratio (|hedge|/|main|) = "
                  f"{round(abs(hedge_delta) / abs(main_delta), 3)}")
        return hedge_symbol, main_delta, hedge_delta
    except Exception as e:
        print("SELECT_HEDGE_BY_DELTA_FAILED (non-fatal, falling back to premium walk):", e)
        return None, None, None


def _order_ok(resp):
    """
    True if an order response indicates success.
    - papertrading=0 path returns 0 (no real order placed) -> treat as ok so paper mode flows.
    - live path returns Fyers dict: success = s=='ok' or code==1101.
    - anything else (error dict code=-99, None from exception) -> failure.
    """
    if resp == 0:
        return True
    if isinstance(resp, dict):
        return resp.get('s') == 'ok' or resp.get('code') == 1101
    return False


def apply_margin_cap(qty, main_symbol, main_side, hedge_symbol, hedge_side, fyers_client):
    """
    Check REAL broker margin for the (main leg + hedge leg) basket at the given qty and
    step qty down until it fits within DEPLOYABLE_CAPITAL_FRACTION of the account's REAL
    available funds (margin_avail, fetched live — no hardcoded capital number).
    main_side/hedge_side: 1=BUY, -1=SELL (matches Fyers multiorder_margin API).
    - If even MIN_LOTS exceeds the usable balance, keeps MIN_LOTS (never blocks) and warns.
    - If the margin API call fails, fails OPEN (returns qty unchanged) so an API hiccup
      never stalls trading — logs a warning instead.
    """
    candidate_qty = qty
    while candidate_qty >= LOT_SIZE:
        # productType=INTRADAY to match the actual order placement (placeTargetOrder
        # uses INTRADAY since all trades are same-day exit) - MARGIN/positional would
        # overstate the real margin requirement (no intraday discount).
        # NOTE: the BUY (long) leg MUST be listed first. Per Fyers docs the hedge-benefit
        # netting is applied only when the long/protective leg is evaluated before the
        # short leg; short-first computes near-standalone (unhedged) margin.
        # This ordering must key off SIDE, not the main/hedge label: for CREDIT the hedge
        # is the BUY leg, but for DEBIT the MAIN is the BUY leg — so we sort by side so the
        # long leg is always first, correct for both spread types.
        _legs_raw = [
            {"symbol": main_symbol, "qty": candidate_qty, "side": main_side, "productType": "INTRADAY"},
            {"symbol": hedge_symbol, "qty": candidate_qty, "side": hedge_side, "productType": "INTRADAY"},
        ]
        # BUY (side=1) before SELL (side=-1)
        legs = sorted(_legs_raw, key=lambda l: l["side"], reverse=True)
        margin_required, margin_avail = helper.getSpreadMargin(legs, fyers_client)
        if margin_required is None:
            # Whole margin API failed — no required-margin figure exists to size against,
            # so a capital cap can't be applied at all. Safest: MIN_LOTS (1 lot fits any
            # realistic balance) rather than blindly pushing the full risk-based qty.
            print(f"MARGIN_CHECK: margin API fully failed - falling back to MIN_LOTS (cannot verify)")
            return MIN_LOTS * LOT_SIZE
        # Usable capital: prefer the broker's REAL available funds; if that field is
        # missing (partial response), fall back to hardcoded FALLBACK_CAPITAL.
        if margin_avail is not None:
            usable = margin_avail * DEPLOYABLE_CAPITAL_FRACTION
            _cap_src = f"avail={round(margin_avail, 2)}"
        else:
            usable = FALLBACK_CAPITAL * DEPLOYABLE_CAPITAL_FRACTION
            _cap_src = f"FALLBACK_CAPITAL={FALLBACK_CAPITAL}"
        print(f"MARGIN_CHECK: qty={candidate_qty} ({candidate_qty // LOT_SIZE} lots) "
              f"margin_required={round(margin_required, 2)} {_cap_src} "
              f"usable({int(DEPLOYABLE_CAPITAL_FRACTION*100)}%)={round(usable, 2)}")
        if margin_required <= usable:
            return candidate_qty
        candidate_qty -= LOT_SIZE

    print(f"MARGIN_CHECK: even MIN_LOTS margin exceeds usable funds "
          f"- using MIN_LOTS={MIN_LOTS} anyway (add funds to raise lots)")
    return MIN_LOTS * LOT_SIZE

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


# ============================================================
# DATA SOURCE DESIGN: Sensex Futures (auto-generated monthly contract)
# ============================================================
# Switched from spot index to futures because Sensex spot vs futures basis
# can be 300-700 pts. Option premiums move with futures, so support/resistance
# levels and FUT_LTP comparisons must use futures for accurate signals.
#
# Trade-off: Sensex futures may have lower liquidity than spot, so OHLC
# candles could occasionally have gaps. We accept this for correct S/R alignment.
#
# Format: BSE:SENSEX{YY}{MMM}FUT (e.g., BSE:SENSEX26JUNFUT)
# Auto-rolls to next month after last-Thursday expiry.
# ============================================================
_now_sx = datetime.now()
_expiry_sx = _get_last_thursday(_now_sx.year, _now_sx.month)
if _now_sx.date() > _expiry_sx:
    # Past this month's expiry, move to next month
    _next_sx = _now_sx.replace(day=28) + timedelta(days=4)
    _yy_sx = _next_sx.strftime("%y")
    _mmm_sx = _next_sx.strftime("%b").upper()
else:
    _yy_sx = _now_sx.strftime("%y")
    _mmm_sx = _now_sx.strftime("%b").upper()
BNFut = f"BSE:SENSEX{_yy_sx}{_mmm_sx}FUT"
print(f"BNFut (Sensex futures auto) = {BNFut}")

indiaVix = "NSE:INDIAVIX-INDEX"

# ============================================================
# SPREAD TYPE SELECTION: CREDIT vs DEBIT (Sensex weekly variant)
# Based on 2 parameters: Premium-to-Move Ratio + IV Rank
# DTE removed (always ≤7 for weekly = constant, no signal)
# Decision: BOTH must agree on DEBIT, otherwise default CREDIT
# ============================================================

def get_expiry_date():
    """Get Sensex weekly expiry date (next Thursday)."""
    return datetime.combine(_get_next_thursday(), datetime.min.time())


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
    Decide CREDIT or DEBIT spread for Sensex weekly options.

    THETA-DOMINANT WEEKLY LOGIC (Option C):
    For weekly options, theta decay is the dominant edge. CREDIT collects theta
    every day; DEBIT pays theta every day. Default to CREDIT unless we have a
    very strong case to go DEBIT (high IV + cheap premium = expect IV expansion).

    Decision rule:
        - If IV_Rank > 0.70 AND PremRatio < 1.0 → DEBIT (rare; high IV + cheap)
        - Otherwise → CREDIT (default; theta favors seller)

    Why this differs from BankNifty:
        - BankNifty options are monthly (DTE 5-25); the 3-param scoring
          (DTE+PremRatio+IVRank) suits longer-dated theta dynamics.
        - Sensex weekly always has DTE ≤7. PremRatio thresholds calibrated
          for monthly are biased toward DEBIT for weekly's smaller absolute
          premiums. Adjust by making CREDIT the strong default.

    Logging keeps PremRatio + IV_Rank thresholds for reference (NOT used in
    this decision, but printed so we can analyze if tuning is needed).
    """
    reasons = []

    # DTE captured for logging only (always weekly = ≤7)
    dte = get_dte()
    reasons.append(f"DTE={dte}(weekly)")

    # Premium-to-Move Ratio (informational)
    daily_range = iv_params.get('daily_range', 1000)
    premium_ratio = get_premium_to_move_ratio(atm_premium, daily_range)
    reasons.append(f"PremRatio={premium_ratio}")

    # IV Rank (informational)
    iv_rank, vix_high, vix_low = get_iv_rank(fyers_client)
    reasons.append(f"IVRank={iv_rank}")

    # === THETA-DOMINANT DECISION ===
    # EXPIRY-DAY GUARD (DTE=0): NEVER buy (debit) on expiry day. Theta decay is maximal, and
    # the "cheap premium" (low PremRatio) is mostly an artifact of near-zero extrinsic value,
    # not a real edge. Buying options 0DTE is theta-toxic — force CREDIT (theta seller).
    if ALWAYS_CREDIT:
        spread_type = "CREDIT"
        reasons.append("DECISION:CREDIT (ALWAYS_CREDIT=True — debit disabled)")
    elif dte == 0:
        spread_type = "CREDIT"
        reasons.append("DECISION:CREDIT (DTE=0 expiry-day guard — never buy/debit on expiry, theta-toxic)")
    elif iv_rank > 0.70 and premium_ratio < 1.0:
        spread_type = "DEBIT"
        reasons.append("DECISION:DEBIT (IV_Rank>0.70 AND PremRatio<1.0 — high IV, cheap premium)")
    else:
        spread_type = "CREDIT"
        reasons.append("DECISION:CREDIT (default — theta favors seller for weekly)")

    print("=" * 50)
    print("SPREAD_SELECTION (Sensex weekly): Type=", spread_type,
          "| DTE=", dte, "| PremRatio=", premium_ratio,
          "| IVRank=", iv_rank)
    print("SPREAD_REASONS:", " | ".join(reasons))
    print("=" * 50)

    # score kept in dict for backward-compat with logging code; not used in decision
    score = 1 if spread_type == "CREDIT" else -1
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
    print("India VIX =", vix_ltp, " Spot Price =", bn_price)

    # Fetch percentiles once per day (or on first call)
    if _vix_percentiles_cache is None:
        _fetch_vix_percentiles(fyers_client)

    p25 = _vix_percentiles_cache["p25"]
    p50 = _vix_percentiles_cache["p50"]
    p75 = _vix_percentiles_cache["p75"]

    # ============================================================
    # FORMULA CONSTANTS — TUNED FOR BANKNIFTY, MAY NEED RECALIBRATION FOR SENSEX
    # ============================================================
    # 700 = annualization constant for VIX → daily expected range
    #   Strict math: daily_σ = (VIX × Price) / (100 × √252) = / 1587
    #   Our 700 is ~2.27x larger ≈ ~2σ coverage (95% intraday range)
    #   Tuned empirically against BankNifty intraday high-low data
    #   For Sensex: needs validation — Sensex moves less in % than BN
    #   so 700 may overestimate. Compare formula output vs actual range
    #   over 5-10 days and adjust constant if needed.
    #
    # √130 = candles-per-day approximator (≈ 125 actual 3-min candles)
    #
    # 0.60 = real-world correction factor for theoretical candle range
    #   Validated against BankNifty logs — markets pause/consolidate so
    #   actual candle range is ~60% of theoretical √-time scaling.
    #   For Sensex: same dynamic likely applies but factor may differ
    #   slightly. Track formula vs actual candle ranges in early days.
    #
    # 0.45 = ATM option delta approximation (CE+PE combined ≈ 0.5±)
    #   Same for any liquid index option — keep as-is.
    # ============================================================

    # Step 1: Expected daily range (1σ, 68% probability)
    daily_range = (vix_ltp * bn_price) / 700.0  # TODO_SENSEX: validate 700

    # Step 2: Expected 3-min candle range
    # Theoretical: daily_range / √130
    # Real-world correction: × 0.60 (validated against 150+ BankNifty candles)
    # For Sensex: tracking accuracy in DATAPOINT logs — adjust 0.60 if needed
    candle_range_theoretical = daily_range / math.sqrt(130)
    candle_range = candle_range_theoretical * 0.60  # TODO_SENSEX: validate 0.60

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
    Detect basic chart patterns on 3-min Sensex spot candles.
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

    LOW-SAMPLE GUARD: if fewer than MIN_MEDIAN_CANDLES (4) valid candles fed the
    median AND the result exceeds LOW_SAMPLE_MEDIAN_THRESHOLD (40), the median is
    halved (LOW_SAMPLE_MEDIAN_FACTOR). A thin window only exists on early entries,
    where every surviving candle is opening volatility. A full window is never
    shaved — a high median there is real volatility. Logged as
    LOW_SAMPLE_MEDIAN_HALVED / LOW_SAMPLE_MEDIAN_OK.

    The returned value is the single source for BOTH qty sizing (effective_sl_pre)
    and post-entry SL/Target (via tradeOptRange), so the guard cannot make the two
    disagree.

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

        # Use HIGH-LOW range (full intra-candle movement), NOT close-open body.
        # We monitor exits via real-time LTP, which travels the full high-low path each
        # candle (including wicks). Sizing SL off the body understated real movement and
        # caused whipsaw stop-outs on normal wicks (see 2026-07-27 morning trade: body
        # median 3.7 -> 15pt SL -> stopped in 1 min). High-low reflects what LTP actually sees.
        ranges = (opt_data['high'].to_numpy() - opt_data['low'].to_numpy())[-n_candles:]
        # Filter out zero-range candles (no trade in that 3-min)
        ranges = ranges[ranges > 0]

        if len(ranges) == 0:
            return None

        # MEDIAN OF ALL valid candle ranges (was top-K median, simplified for stability)
        representative_range = float(np.median(ranges))
        raw_median = representative_range
        low_sample_halved = False

        # ============================================================
        # LOW-SAMPLE OPENING-VOLATILITY GUARD
        # ============================================================
        # A median needs enough samples to mean anything. Early entries (before ~9:40) leave
        # only 2-3 completed candles after the 1st-candle skip, and those survivors are ALL
        # opening volatility — so the "median" is really just peak-open range.
        #
        # Measured over 98 OPT_RANGE_DEBUG samples from every past session:
        #   2 candles -> avg median 26.02 (max 67.55) | 3 -> 16.89 | 10 -> 14.27
        # Low-sample windows run ~1.8x richer than a full window.
        #
        # Live failure this produced (2026-10-15, 09:24 entry, 2 candles, median 67.55):
        #   SL  = 215.05 + (67.55 x 2.4) = 377.05  -> premium had to RISE 75% to stop out
        #   Tgt = 215.05 - (67.55 x 4.0) = -54.95  -> NEGATIVE, unreachable (premium floors at 0)
        #   qty = 1 lot (risked only 2,182 of the 4,000 budget)
        # The trade had no exit path but SL or the 3:24 EOD square-off.
        #
        # RULE: when the window is thin (< MIN_MEDIAN_CANDLES) AND the median came out
        # implausibly large (> LOW_SAMPLE_MEDIAN_THRESHOLD), halve it. Only 1 of the 12
        # historical sub-4-candle samples would have tripped this, so it fires rarely and
        # only on the pathological case.
        #
        # Deliberately NOT applied when the window is full: 2026-06-24 produced median 43.43
        # off a complete 10-candle window — that was real volatility, not a sampling artifact,
        # and must not be shaved.
        if len(ranges) < MIN_MEDIAN_CANDLES and representative_range > LOW_SAMPLE_MEDIAN_THRESHOLD:
            representative_range = representative_range * LOW_SAMPLE_MEDIAN_FACTOR
            low_sample_halved = True

        # Diagnostic logging — audit raw data driving SL calculation
        all_ranges_rounded = [round(float(r), 2) for r in ranges]
        print(f"OPT_RANGE_DEBUG: symbol={option_symbol}",
              f"| total_candles_after_skip1st={len(ranges)}",
              f"| ranges={all_ranges_rounded}",
              f"| median_all={round(representative_range, 2)}")

        if low_sample_halved:
            print(f"LOW_SAMPLE_MEDIAN_HALVED: symbol={option_symbol}",
                  f"| candles={len(ranges)} (< {MIN_MEDIAN_CANDLES})",
                  f"| raw_median={round(raw_median, 2)} (> {LOW_SAMPLE_MEDIAN_THRESHOLD})",
                  f"-> used_median={round(representative_range, 2)}",
                  f"| reason=thin window dominated by opening volatility")
        elif len(ranges) < MIN_MEDIAN_CANDLES:
            print(f"LOW_SAMPLE_MEDIAN_OK: symbol={option_symbol}",
                  f"| candles={len(ranges)} (< {MIN_MEDIAN_CANDLES})",
                  f"| median={round(representative_range, 2)} (<= {LOW_SAMPLE_MEDIAN_THRESHOLD})",
                  f"-> kept as-is")

        # === MAIN-LEG OPTION CHART OHLC (single line at entry) — kept for future analysis ===
        # Full open/high/low/close of the exact traded (main/ATM) leg at entry time, so we can
        # later reconstruct the median calc and re-tune SL/target sizing logic offline.
        # One consolidated line (not per-candle) covering the candles that fed the median.
        try:
            ohlc_candles = opt_data.iloc[-n_candles:]
            o_list = [round(float(x), 2) for x in ohlc_candles['open'].to_numpy()]
            h_list = [round(float(x), 2) for x in ohlc_candles['high'].to_numpy()]
            l_list = [round(float(x), 2) for x in ohlc_candles['low'].to_numpy()]
            c_list = [round(float(x), 2) for x in ohlc_candles['close'].to_numpy()]
            print(f"OPT_OHLC_ENTRY: symbol={option_symbol}",
                  f"| candles={len(ohlc_candles)}",
                  f"| open={o_list}",
                  f"| high={h_list}",
                  f"| low={l_list}",
                  f"| close={c_list}",
                  f"| median_range={round(representative_range, 2)}")
        except Exception as ohlc_err:
            print("OPT_OHLC_LOG_ERROR:", str(ohlc_err))

        return round(representative_range, 2)
    except Exception as e:
        print("OPTION_RANGE_ERROR:", str(e))
        return None


def apply_target_reachability_cap(opt_range, main_premium, target_mult, sl_mult,
                                  option_symbol=""):
    """
    Cap the option candle median so a CREDIT spread's target stays reachable.

    Only for CREDIT spreads, where target = premium - (median x target_mult). A large
    median against a small premium drives that level to zero/negative, leaving the trade
    with no exit but the SL or the EOD square-off (2026-10-15: median 67.55 on premium
    215.05 -> target -54.95). See MAX_TGT_PREMIUM_FRACTION for the full rationale.

    The median is the SINGLE basis for SL, target and qty sizing, so capping it here (at
    the sizing site, before tradeOptRange is stored) keeps all three in agreement and
    leaves R:R at exactly target_mult/sl_mult. Must be called BEFORE effective_sl_pre /
    tradeOptRange so the post-entry SL/Target block inherits the same value.

    Returns the median to use (capped or unchanged); never raises.
    """
    try:
        if not opt_range or opt_range <= 0:
            return opt_range
        if not main_premium or float(main_premium) <= 0:
            return opt_range
        if not target_mult or target_mult <= 0:
            return opt_range

        median_cap = (float(main_premium) * MAX_TGT_PREMIUM_FRACTION
                      / float(target_mult))
        if opt_range <= median_cap:
            return opt_range

        raw = opt_range
        capped = round(median_cap, 2)
        print(f"MEDIAN_CAPPED: symbol={option_symbol}",
              f"| raw_median={round(raw, 2)}",
              f"| target would need premium to fall {round(raw * target_mult, 2)}",
              f"(> {MAX_TGT_PREMIUM_FRACTION} of {main_premium})",
              f"-> capped_median={capped}",
              f"| new SL={round(capped * sl_mult, 2)}",
              f"target={round(capped * target_mult, 2)}",
              f"| target_level={round(float(main_premium) - capped * target_mult, 2)}",
              f"| R:R unchanged at {round(target_mult / sl_mult, 2)}")
        return capped
    except Exception as e:
        print("MEDIAN_CAP_ERROR:", str(e))
        return opt_range


def get_entry_anchor_premium(option_symbol, fyers_client):
    """
    Resolve the premium used to anchor SL/Target immediately AFTER a spread is filled.

    This runs with a LIVE position open, so it must never raise and must never silently
    leave SL/Target anchored to stale globals from a previous trade (which is what a bare
    try/except would do — sl/target/entryPremium are module-level and would still hold the
    prior trade's values, monitoring a real position against unrelated levels).

    Fallback chain (each source is an independent endpoint):
      1) last completed 1-min candle close  (fyers.history)
      2) live quote                          (fyers.quotes — different endpoint, so it can
                                              succeed when history is broken/rate-limited)
      3) None -> caller MUST square off; we cannot manage what we cannot measure.

    Returns:
        tuple: (premium or None, source_label)
    """
    try:
        d = helper.getHistorical(option_symbol, 1, 3, fyers_client)
        c = d['close'].to_numpy()
        if len(c) > 0 and float(c[-1]) > 0:
            return float(c[-1]), "CANDLE_CLOSE"
        print("ANCHOR_CANDLE_EMPTY: no usable close for", option_symbol)
    except Exception as e:
        print("ANCHOR_CANDLE_FAILED:", e)

    try:
        ltp = helper.manualLTP(option_symbol, fyers_client)
        if ltp and float(ltp) > 0:
            print(f"ANCHOR_LTP_FALLBACK: using live LTP {ltp} for {option_symbol}")
            return float(ltp), "LIVE_LTP"
    except Exception as e:
        print("ANCHOR_LTP_FAILED:", e)

    return None, "NONE"


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


# ============================================================
# INVARIANT-WINDOW PCR (17 + 9 strike) — OBSERVATION / LOG ONLY
# ============================================================
# The existing avgOiPcrList2 / avgOiPcr9List2 series are POSITIONAL: pcr1..pcr17
# read dfochain rows -2,-4,...,-34, so when the ATM shifts the underlying STRIKE
# SET changes and the 3 values in the window are no longer measuring the same
# thing. Today that is handled by throwing the window away on a shift
# (avgOiPcrList2 = avgOiPcrList2[-1:]) and requiring atmStrikeNotShiftedCount>=3,
# which costs ~9 minutes of rebuild every time price trends.
#
# The INVARIANT window instead keeps the last 3 cycles' per-strike OI and
# RE-MEASURES each of them over the CURRENT strike set, so an ATM shift cannot
# fake (or destroy) a PCR trend. Zero extra API calls — it reuses the dfochain
# already fetched each cycle.
#
# STRICTLY LOG-ONLY. Nothing here feeds entry, sizing or exits; the existing
# series/flags are untouched. Mirrors the BTC implementation so the two logs
# read the same way.
INV_PCR_WINDOW = 3        # cycles kept (matches the existing 3-value window)
INV_PCR_MIN_STEP_PCT = 0.1  # a step smaller than this is drift, not a move
chainOiHistory = []      # last INV_PCR_WINDOW maps of {strike: (ce_oi, pe_oi)}

# ============================================================
# WIDE-CHAIN INVARIANT 17-PCR (separate fetch) — OBSERVATION / LOG ONLY
# ============================================================
# PROBLEM the wide fetch solves (measured on 2026-10-23, 86 cycles):
# the main loop fetches strikecount=8 -> EXACTLY 17 strikes (ATM +/- 8), so the
# invariant 17-window has ZERO buffer. Any ATM shift means the older stored
# snapshots never captured the new edge strikes, and their average is taken over
# fewer strikes than the current cycle's. That session shifted the ATM on 41 of
# 86 cycles and only 21 cycles had clean 17/17/17 coverage. Worst case was a
# 5-strike shift:
#   cycle1 73400..71800 | cycle2 73800..72200 | current 73900..72300  -> 12/17
#
# FIX: fetch a WIDER chain just for this measurement. strikecount=11 -> 23
# strikes (ATM +/- 11), and the middle 17 of those 23 is exactly ATM +/- 8 — the
# same set the existing pcr1..pcr17 use. The 3 spare strikes each side mean a
# 1-3 strike shift still leaves every current strike present in the older
# snapshots, so the re-measurement is genuinely apples-to-apples.
#
# The central 9 needs no such help: being ATM +/- 4 inside a 17-wide chain, it
# already tolerates a 4-strike shift with full coverage (confirmed in the same
# log: the 5-strike shift above still gave the 9-set 8/9). This wide path is
# therefore for the 17-STRIKE series only.
#
# COST: one extra option-chain call per cycle. A 2nd ochain call has caused
# API contention before (see the disabled checkCriteriaAndTakeTrade block), so
# this one uses retries=1, is fully try/except wrapped, feeds NOTHING, and
# simply logs a skip line when the fetch fails. The existing strikecount=8
# fetch, every pcr1..pcr17 value, and every entry gate are untouched.
INV_WIDE_STRIKECOUNT = 11   # -> 23 strikes; middle 17 = ATM +/- 8
INV_WIDE_MEASURE_COUNT = 17  # strikes actually measured (the middle slice)
chainOiHistoryWide = []     # last INV_PCR_WINDOW maps from the WIDE chain

# The NARROW-chain 17-strike invariant series (same strikecount=8 chain, zero buffer)
# is SUPERSEDED by the wide one above and is OFF by default. It is not wrong, but on a
# shifted cycle its average is taken over fewer strikes than the current cycle's, so the
# value is unreliable: on 2026-10-23 only 21 of 86 cycles had clean 17/17/17 coverage.
# The 9-strike series is unaffected (ATM +/- 4 inside a 17-wide chain tolerates a
# 4-strike shift at full coverage) and stays ON.
# Set True to bring it back — useful as a FALLBACK if the wide fetch proves flaky, and
# as a cross-check that wide and narrow agree on full-coverage cycles.
INV_LOG_NARROW_17 = False

# Persistent cross-cycle streaks per series label ("9", "17", "17WIDE"):
#   {label: {"inc": n, "dec": n}}
# The 3-cycle window verdict caps a run at 3; these do not, so a long invariant
# trend stays visible. Log-only, same as everything else here.
invPcrStreaks = {}

# CONSECUTIVE-RISING-WINDOWS streak per label (the operator's "streak count").
#   {label: {"inc": n, "dec": n}}
# Counts how many 3-min cycles IN A ROW the invariant 3-value window itself stayed
# monotonic (rising -> inc, falling -> dec). This mirrors the live 17-PCR idea:
#   cycle A window [1.54,1.63,1.74] rising  -> isPcrInc True  (streak 1)
#   cycle B window [1.63,1.74,1.79] rising  -> still rising   (streak 2)
# i.e. "the next printed 3-min window is also increasing". A non-rising (or building)
# window resets the count to 0. LOG-ONLY, no gate.
invPcrWinRun = {}

# The invariant blocks are COMPUTED where dfochain is fetched (so the 3-cycle
# history gets a snapshot on EVERY cycle — a `continue` further down must never
# leave a gap), but PRINTED later in the "signal check time" section next to the
# other signal flags. These buffers carry the lines between the two points.
invPcrLogLines = []       # narrow-chain block (9 + 17)
invWideLogLines = []      # wide-chain block (17WIDE)


def build_strike_oi_map(dfochain):
    """``{int(strike): (ce_oi, pe_oi)}`` for the current cycle. Never raises."""
    out = {}
    try:
        if 'strike_price' not in dfochain.columns:
            return out
        sp = dfochain['strike_price'].to_numpy()
        ot = dfochain['option_type'].to_numpy()
        oiarr = dfochain['oi'].to_numpy()
        for i in range(len(sp)):
            try:
                k = int(float(sp[i]))
            except (TypeError, ValueError):
                continue
            ce, pe = out.get(k, (0.0, 0.0))
            try:
                v = float(oiarr[i] or 0)
            except (TypeError, ValueError):
                v = 0.0
            if ot[i] == 'CE':
                ce = v
            elif ot[i] == 'PE':
                pe = v
            out[k] = (ce, pe)
    except Exception as e:
        print(f"INV_WINDOW_WARN: build_strike_oi_map failed: {type(e).__name__}: {e}")
    return out


def ordered_pcr_strikes(dfochain, count=17):
    """The strikes pcr1..pcr17 use, in that same order (rows -2,-4,...,-34)."""
    try:
        if 'strike_price' not in dfochain.columns:
            return []
        sp = dfochain['strike_price'].to_numpy()
        out = []
        for n in range(1, count + 1):
            idx = -2 * n
            if abs(idx) > len(sp):
                break
            try:
                out.append(int(float(sp[idx])))
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        print(f"INV_WINDOW_WARN: ordered_pcr_strikes failed: {type(e).__name__}: {e}")
        return []


def invariant_avg_pcr(snapshot, strikes):
    """Average same-strike PE/CE PCR over ``strikes`` using ONE historical OI
    snapshot. Returns ``(avg, covered)``; ``avg`` is None when no strike in
    ``strikes`` has usable OI in that snapshot."""
    vals = []
    for k in strikes:
        pair = snapshot.get(k)
        if not pair:
            continue
        ce, pe = pair
        if ce and ce > 0:
            vals.append(pe / ce)
    if not vals:
        return None, 0
    return round(sum(vals) / len(vals), 4), len(vals)


def update_inv_pcr_streak(label, series):
    """
    Cross-cycle CONSECUTIVE streak for an invariant PCR series — LOG-ONLY.

    The window verdict above only sees 3 cycles. This keeps a persistent count of how
    many cycles IN A ROW the invariant average has moved the same way, so a long trend
    is visible instead of being capped at 3 (mirrors the existing _pe_supp_streak /
    pcr17_up_streak style counters).

    Rule, applied ONCE per cycle on the newest step only:
      step > +INV_PCR_MIN_STEP_PCT  -> inc += 1, dec = 0
      step < -INV_PCR_MIN_STEP_PCT  -> dec += 1, inc = 0
      otherwise (sub-threshold drift) -> BOTH reset to 0 (a flat cycle breaks the run)

    Returns (inc, dec). Never raises.
    """
    try:
        st = invPcrStreaks.setdefault(label, {"inc": 0, "dec": 0})
        if len(series) < 2:
            return st["inc"], st["dec"]
        prev, last = series[-2], series[-1]
        step = ((last - prev) / prev * 100) if prev else 0.0
        if step > INV_PCR_MIN_STEP_PCT:
            st["inc"] += 1
            st["dec"] = 0
        elif step < -INV_PCR_MIN_STEP_PCT:
            st["dec"] += 1
            st["inc"] = 0
        else:
            st["inc"] = 0
            st["dec"] = 0
        return st["inc"], st["dec"]
    except Exception as e:
        print(f"INV_WINDOW_WARN: update_inv_pcr_streak({label}) failed: "
              f"{type(e).__name__}: {e}")
        return 0, 0


def log_invariant_pcr_window(label, series, buf=None):
    """Emit one invariant series + its monotonic-run / MinMove verdict + the
    cross-cycle streak.

    ``series`` is oldest-first. The run/MinMove verdict is WITHIN the 3-cycle window;
    the STREAK is persistent across cycles (see update_inv_pcr_streak). Lines go into
    ``buf`` when given so the caller can print them wherever it likes (the compute site
    and the print site are deliberately different); falls back to printing directly."""
    out = buf if buf is not None else None

    def _emit(line):
        if out is None:
            print(line)
        else:
            out.append(line)

    try:
        inc_streak, dec_streak = update_inv_pcr_streak(label, series)
        shown = [round(v, 4) for v in series]
        _emit(f"avgOiPcr{label}List2_InvariantWindow  = {shown}"
              f"  ({label}-strike, re-measured on current strikes)")
        _emit(f"is{label}Pcr_InvariantWindow_STREAK: inc={inc_streak} dec={dec_streak}"
              f"  (consecutive cycles same direction, >{INV_PCR_MIN_STEP_PCT}% steps;"
              f" not capped at the {INV_PCR_WINDOW}-cycle window)")
        wr = invPcrWinRun.setdefault(label, {"inc": 0, "dec": 0})
        if len(series) < INV_PCR_WINDOW:
            # No full window yet -> no rising/falling window this cycle -> reset the run.
            wr["inc"] = 0; wr["dec"] = 0
            _emit(f"is{label}PcrInc_InvariantWindow = building"
                  f" ({len(series)}/{INV_PCR_WINDOW} cycles)")
            return
        steps = []
        for i in range(1, len(series)):
            prev = series[i - 1]
            steps.append(round((series[i] - prev) / prev * 100, 2) if prev else 0.0)
        chain = " -> ".join(str(round(v, 4)) for v in series)
        step_txt = " | ".join(f"step{i+1} {s:+.2f}%" for i, s in enumerate(steps))
        rising = all(series[i] > series[i - 1] for i in range(1, len(series)))
        falling = all(series[i] < series[i - 1] for i in range(1, len(series)))
        smallest = min(abs(s) for s in steps) if steps else 0.0
        minmove = smallest > INV_PCR_MIN_STEP_PCT
        # Update the consecutive-rising/falling-WINDOWS streak (operator's "streak count").
        if rising:
            wr["inc"] += 1; wr["dec"] = 0
        elif falling:
            wr["dec"] += 1; wr["inc"] = 0
        else:
            wr["inc"] = 0; wr["dec"] = 0
        if rising:
            _emit(f"{label}_SEQ_INC_InvariantWindow: {chain} | {step_txt}")
            _emit(f"is{label}PcrInc_InvariantWindow = True  (run={len(series)} rising)")
            _emit(f"is{label}PcrInc_InvariantWindow_MinMove = {minmove}"
                  f"  (smallest step {smallest:.2f}% vs {INV_PCR_MIN_STEP_PCT}% threshold)")
            _emit(f"is{label}Pcr_InvariantWindow_WINSTREAK = {wr['inc']}"
                  f"  (consecutive 3-min cycles the window stayed RISING; "
                  f"operator streak count)")
        elif falling:
            _emit(f"{label}_SEQ_DEC_InvariantWindow: {chain} | {step_txt}")
            _emit(f"is{label}PcrDecr_InvariantWindow = True  (run={len(series)} falling)")
            _emit(f"is{label}PcrDecr_InvariantWindow_MinMove = {minmove}"
                  f"  (smallest step {smallest:.2f}% vs {INV_PCR_MIN_STEP_PCT}% threshold)")
            _emit(f"is{label}Pcr_InvariantWindow_WINSTREAK = {wr['dec']}"
                  f"  (consecutive 3-min cycles the window stayed FALLING; "
                  f"operator streak count)")
        else:
            _emit(f"{label}_SEQ_FLAT_InvariantWindow: {chain} | {step_txt} (not monotonic)")
            _emit(f"is{label}Pcr_InvariantWindow_WINSTREAK = 0  (window not monotonic "
                  f"this cycle; run reset)")
    except Exception as e:
        _emit(f"INV_WINDOW_WARN: log_invariant_pcr_window({label}) failed: "
              f"{type(e).__name__}: {e}")


def log_invariant_pcr_wide(sname, fyers_client):
    """
    WIDE-CHAIN invariant 17-strike PCR — STRICTLY LOG-ONLY, no gate, no global state
    shared with the live path.

    Fetches its OWN wider option chain (INV_WIDE_STRIKECOUNT=11 -> 23 strikes) so the
    3-cycle invariant window has real buffer: the middle 17 of those 23 is ATM +/- 8,
    the same set pcr1..pcr17 measure, but with 3 spare strikes each side. An ATM shift
    of 1-3 strikes therefore still finds every current strike in the older snapshots,
    which the main strikecount=8 chain cannot do (it has zero buffer).

    Does NOT touch dfochain, any pcr variable, any streak counter, or any entry
    condition. Never raises — on any failure it logs and returns.
    """
    del invWideLogLines[:]
    buf = invWideLogLines
    try:
        dfwide, _resp = fetch_ochain_safe(INV_WIDE_STRIKECOUNT, sname, fyers_client,
                                         use_closest1=True, retries=1)
        if dfwide is None:
            buf.append("INV_WIDE_SKIPPED: wide option-chain fetch failed this cycle "
                       "(log-only feature; nothing else affected).")
            return

        wide_strikes = ordered_pcr_strikes(dfwide, 2 * INV_WIDE_STRIKECOUNT + 1)
        if not wide_strikes:
            buf.append("INV_WIDE_SKIPPED: no strike_price column in the wide chain.")
            return

        # Middle INV_WIDE_MEASURE_COUNT of the wide list = ATM +/- 8. The list is
        # ordered highest strike first, so trimming equally from both ends centres
        # the slice on the ATM regardless of how many strikes came back.
        trim = (len(wide_strikes) - INV_WIDE_MEASURE_COUNT) // 2
        if trim < 0:
            buf.append(f"INV_WIDE_SKIPPED: wide chain returned only "
                       f"{len(wide_strikes)} strikes, need at least "
                       f"{INV_WIDE_MEASURE_COUNT}.")
            return
        mid17 = wide_strikes[trim:trim + INV_WIDE_MEASURE_COUNT]

        chainOiHistoryWide.append(build_strike_oi_map(dfwide))
        del chainOiHistoryWide[:-INV_PCR_WINDOW]

        series, cov = [], []
        for snap in chainOiHistoryWide:
            avg, covered = invariant_avg_pcr(snap, mid17)
            if avg is not None:
                series.append(avg)
                cov.append(covered)

        buf.append("==== WIDE-CHAIN INVARIANT 17-PCR (log-only, separate fetch) ====")
        buf.append(f"inv_wide_fetched   = {len(wide_strikes)} strikes "
                   f"(strikecount={INV_WIDE_STRIKECOUNT}), buffer={trim} each side")
        buf.append(f"inv_wide_measured  = {mid17}")
        buf.append(f"inv_wide_coverage_per_cycle = "
                   f"{[f'{c}/{len(mid17)}' for c in cov]}")
        if cov and min(cov) == len(mid17):
            buf.append("INV_WIDE_FULL: every stored snapshot covers all 17 measured "
                       "strikes — the series below is a true same-strike comparison.")
        elif cov:
            buf.append(f"INV_WIDE_DEGRADED: worst coverage {min(cov)}/{len(mid17)} — the "
                       "ATM moved further than the 3-strike buffer absorbs; treat the "
                       "verdict below as indicative only.")
        log_invariant_pcr_window("17WIDE", series, buf=buf)
    except Exception as e:
        buf.append(f"INV_WIDE_WARN: wide invariant block failed: "
                   f"{type(e).__name__}: {e}")


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


def takeEntryCredit(isBullish, isBearish, syntheticATMStrike, intExpiry, fyers, papertrading):
    """
    CREDIT SPREAD entry: Sell ATM + Buy OTM hedge.
    Same as original strategy — proven edge.

    syntheticATMStrike/intExpiry are passed in from the caller (computed ONCE, right
    before this call) instead of being recomputed here. This guarantees the strike
    used for qty/SL sizing is the exact same strike actually traded — no separate live
    API round-trip that could tick the price/premium between calc and entry.
    """
    global hedgeOrderId
    global tradeATMOption
    global tradeHedgeOption
    global mainOrderId
    global tradeOptRange
    global iv_params
    global entry_ok
    global qty  # exitPosition/exitSpreadPosition read module-level qty on exit — must
                # update it here so exits close the ACTUAL traded quantity, not stale default.
    global hedgeEntryPremium   # hedge leg entry price — for realized-vs-assumed offset logging
    global assumedOffsetRatio  # the ratio calc_lots_by_risk actually sized with

    dynamic_sl = iv_params.get("sl_point", sl_point)
    dynamic_target = iv_params.get("target_point", target_point)
    dynamic_spread = iv_params.get("spread_width", 300)

    print("IV-Dynamic: SL=", dynamic_sl, " Target=", dynamic_target,
          " Spread=", dynamic_spread)

    # CREDIT main (sell) leg is 1-OTM from the synthetic ATM (see ONE_OTM): bull sells the PE
    # ONE_OTM BELOW ATM, bear sells the CE ONE_OTM ABOVE ATM. All downstream calc (delta hedge,
    # sizing, SL/target, exit symbol) keys off these shifted strikes. atmCE/atmPE keep their
    # names but now hold the 1-OTM main leg.
    creditBullStrike = syntheticATMStrike - ONE_OTM   # bull-credit sells PE here (1-OTM)
    creditBearStrike = syntheticATMStrike + ONE_OTM   # bear-credit sells CE here (1-OTM)
    Hedge_Strike_CE_OTMBuy = creditBearStrike + dynamic_spread
    Hedge_Strike_PE_OTMBuy = creditBullStrike - dynamic_spread
    atmCE = getOptionFormatSensex(intExpiry, creditBearStrike, "CE")
    atmPE = getOptionFormatSensex(intExpiry, creditBullStrike, "PE")

    otmCE = getOptionFormatSensex(intExpiry, Hedge_Strike_CE_OTMBuy, "CE")
    otmPE = getOptionFormatSensex(intExpiry, Hedge_Strike_PE_OTMBuy, "PE")

    if isBullish:
        print("CREDIT_MAIN_1OTM: bull sell PE strike=", creditBullStrike, "(synthATM", syntheticATMStrike, "- ONE_OTM", ONE_OTM, ")")
        entryPrice = helper.manualLTP(atmPE, fyers)
        max_premium = entryPrice * HEDGE_MAX_PREMIUM_FRACTION
        # DELTA-BASED hedge selection (primary): hedge |delta| ~= main |delta|/2, snapped to a
        # 500 multiple for liquidity. Also returns both leg deltas (feeds the offset ratio in
        # calc_lots_by_risk), so no separate get_leg_deltas() call is needed.
        hedge_sym, main_delta_val, hedge_delta_val = select_hedge_by_delta(
            creditBullStrike, "PE", intExpiry, fyers)
        if hedge_sym:
            otmPE = hedge_sym
        else:
            # FALLBACK: greeks unavailable -> original premium-fraction + 500-grid walk.
            main_delta_val, hedge_delta_val = None, None
            # First 500-grid strike at least HEDGE_MIN_DISTANCE BELOW the main leg (PE hedge).
            hedge_strike = math.floor((creditBullStrike - HEDGE_MIN_DISTANCE) / 500) * 500
            otmPE_found = None
            for _ in range(6):
                candidate = getOptionFormatSensex(intExpiry, hedge_strike, "PE")
                # Skip (don't crash on) a strike with no usable price — deep-OTM strikes that have
                # not traded return lp=0, and manualLTP now raises rather than reporting 0.
                try:
                    candidate_premium = helper.manualLTP(candidate, fyers)
                except Exception as _hedge_err:
                    print(f"  HEDGE_SKIP {candidate}: no usable price ({_hedge_err})")
                    hedge_strike -= 500
                    continue
                print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
                # Require a POSITIVE premium — 0 would satisfy '<= max_premium' and select an
                # untraded, illiquid strike as the hedge.
                if 0 < candidate_premium <= max_premium:
                    otmPE_found = candidate
                    break
                hedge_strike -= 500
            if otmPE_found:
                otmPE = otmPE_found
        print("=atmPE=", atmPE)
        print("=otmPE==", otmPE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        # Fetch hedge premium BEFORE sizing so net-of-hedge lot calc can use it.
        hedge_entry_price = helper.manualLTP(otmPE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("CREDIT_NET_CREDIT=", round(entryPrice - hedge_entry_price, 2))

        # === RISK-BASED LOT SIZING (net-of-hedge, on the exact strikes being traded) ===
        opt_range = get_option_candle_range(atmPE, fyers, n_candles=10)
        # CREDIT spread: target = premium - median x TARGET_MULT, so an oversized median
        # makes that level zero/negative and unreachable. Cap BEFORE tradeOptRange is
        # stored so sizing and the post-entry SL/Target use the identical median.
        opt_range = apply_target_reachability_cap(
            opt_range, entryPrice, TARGET_MULT_BULL, SL_MULT_BULL, atmPE)
        tradeOptRange = opt_range  # store for post-entry SL/Target reuse — no re-fetch, no drift
        # Hedge leg range too -> empirical delta-offset ratio for risk sizing. Returns None on
        # any failure (thin/illiquid strike); sizing then falls back to the premium ratio.
        hedge_opt_range = get_option_candle_range(otmPE, fyers, n_candles=10)
        # Deltas come from select_hedge_by_delta above (None in the premium-walk fallback);
        # calc_lots_by_risk then falls back to range/premium when they are None.
        effective_sl_pre = round(opt_range * SL_MULT_BULL) if (opt_range and opt_range > 0) else iv_params.get("sl_point", sl_point)
        qty, assumedOffsetRatio = calc_lots_by_risk(effective_sl_pre, main_premium=entryPrice, hedge_premium=hedge_entry_price,
                                main_range=opt_range, hedge_range=hedge_opt_range,
                                main_delta=main_delta_val, hedge_delta=hedge_delta_val)
        hedgeEntryPremium = hedge_entry_price
        print(f"RISK_SIZING: effective_sl_pre={effective_sl_pre} main={entryPrice} hedge={hedge_entry_price} "
              f"main_range={opt_range} hedge_range={hedge_opt_range} "
              f"FIXED_RISK={FIXED_RISK_PER_TRADE} -> qty={qty} ({qty//LOT_SIZE} lots)")
        print("CREDIT_ENTRY: qty=", qty)

        # === MARGIN CAP CHECK (real broker margin, may reduce qty) ===
        qty = apply_margin_cap(qty, atmPE, -1, otmPE, 1, fyers)

        hedgeOrderId = helper.placeTargetOrder(otmPE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmPE
        if not _order_ok(hedgeOrderId):
            print("ENTRY_ABORT: CREDIT bull hedge BUY leg failed — no position taken. resp=", hedgeOrderId)
            entry_ok = False
            return None
        time.sleep(0.5)

        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmPE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmPE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers, papertrading)
        tradeATMOption = atmPE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)
        if not _order_ok(mainOrderId):
            print("ENTRY_ABORT: CREDIT bull main SELL leg failed — squaring off orphaned hedge BUY. resp=", mainOrderId)
            helper.placeOrder(otmPE, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
            entry_ok = False
            return None

    if isBearish:
        print("CREDIT_MAIN_1OTM: bear sell CE strike=", creditBearStrike, "(synthATM", syntheticATMStrike, "+ ONE_OTM", ONE_OTM, ")")
        entryPrice = helper.manualLTP(atmCE, fyers)
        max_premium = entryPrice * HEDGE_MAX_PREMIUM_FRACTION
        # DELTA-BASED hedge selection (primary): hedge |delta| ~= main |delta|/2, snapped to a
        # 500 multiple for liquidity. Also returns both leg deltas (feeds the offset ratio in
        # calc_lots_by_risk), so no separate get_leg_deltas() call is needed.
        hedge_sym, main_delta_val, hedge_delta_val = select_hedge_by_delta(
            creditBearStrike, "CE", intExpiry, fyers)
        if hedge_sym:
            otmCE = hedge_sym
        else:
            # FALLBACK: greeks unavailable -> original premium-fraction + 500-grid walk.
            main_delta_val, hedge_delta_val = None, None
            # First 500-grid strike at least HEDGE_MIN_DISTANCE ABOVE the main leg (CE hedge).
            hedge_strike = math.ceil((creditBearStrike + HEDGE_MIN_DISTANCE) / 500) * 500
            otmCE_found = None
            for _ in range(6):
                candidate = getOptionFormatSensex(intExpiry, hedge_strike, "CE")
                # Skip (don't crash on) a strike with no usable price — deep-OTM strikes that have
                # not traded return lp=0, and manualLTP now raises rather than reporting 0.
                try:
                    candidate_premium = helper.manualLTP(candidate, fyers)
                except Exception as _hedge_err:
                    print(f"  HEDGE_SKIP {candidate}: no usable price ({_hedge_err})")
                    hedge_strike += 500
                    continue
                print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
                # Require a POSITIVE premium — 0 would satisfy '<= max_premium' and select an
                # untraded, illiquid strike as the hedge.
                if 0 < candidate_premium <= max_premium:
                    otmCE_found = candidate
                    break
                hedge_strike += 500
            if otmCE_found:
                otmCE = otmCE_found
        print("=atmCE=", atmCE)
        print("=otmCE==", otmCE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        # Fetch hedge premium BEFORE sizing so net-of-hedge lot calc can use it.
        hedge_entry_price = helper.manualLTP(otmCE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("CREDIT_NET_CREDIT=", round(entryPrice - hedge_entry_price, 2))

        # === RISK-BASED LOT SIZING (net-of-hedge, on the exact strikes being traded) ===
        opt_range = get_option_candle_range(atmCE, fyers, n_candles=10)
        # CREDIT spread: target = premium - median x TARGET_MULT, so an oversized median
        # makes that level zero/negative and unreachable. Cap BEFORE tradeOptRange is
        # stored so sizing and the post-entry SL/Target use the identical median.
        opt_range = apply_target_reachability_cap(
            opt_range, entryPrice, TARGET_MULT_BEAR, SL_MULT_BEAR, atmCE)
        tradeOptRange = opt_range  # store for post-entry SL/Target reuse — no re-fetch, no drift
        # Hedge leg range too -> empirical delta-offset ratio for risk sizing. Returns None on
        # any failure (thin/illiquid strike); sizing then falls back to the premium ratio.
        hedge_opt_range = get_option_candle_range(otmCE, fyers, n_candles=10)
        # Deltas come from select_hedge_by_delta above (None in the premium-walk fallback);
        # calc_lots_by_risk then falls back to range/premium when they are None.
        effective_sl_pre = round(opt_range * SL_MULT_BEAR) if (opt_range and opt_range > 0) else iv_params.get("sl_point", sl_point)
        qty, assumedOffsetRatio = calc_lots_by_risk(effective_sl_pre, main_premium=entryPrice, hedge_premium=hedge_entry_price,
                                main_range=opt_range, hedge_range=hedge_opt_range,
                                main_delta=main_delta_val, hedge_delta=hedge_delta_val)
        hedgeEntryPremium = hedge_entry_price
        print(f"RISK_SIZING: effective_sl_pre={effective_sl_pre} main={entryPrice} hedge={hedge_entry_price} "
              f"main_range={opt_range} hedge_range={hedge_opt_range} "
              f"FIXED_RISK={FIXED_RISK_PER_TRADE} -> qty={qty} ({qty//LOT_SIZE} lots)")
        print("CREDIT_ENTRY: qty=", qty)

        # === MARGIN CAP CHECK (real broker margin, may reduce qty) ===
        qty = apply_margin_cap(qty, atmCE, -1, otmCE, 1, fyers)

        hedgeOrderId = helper.placeTargetOrder(otmCE, "BUY", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmCE
        if not _order_ok(hedgeOrderId):
            print("ENTRY_ABORT: CREDIT bear hedge BUY leg failed — no position taken. resp=", hedgeOrderId)
            entry_ok = False
            return None
        time.sleep(0.5)

        ceTarget = round(entryPrice - dynamic_target)
        ceSL = round(entryPrice + dynamic_sl)
        print("entryPrice==", entryPrice, "Target =", ceTarget, "", atmCE, " SL =", ceSL)
        mainOrderId = helper.placeTargetOrder(atmCE, "SELL", qty, "MARKET", entryPrice, ceSL, ceTarget, fyers, papertrading)
        tradeATMOption = atmCE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)
        if not _order_ok(mainOrderId):
            print("ENTRY_ABORT: CREDIT bear main SELL leg failed — squaring off orphaned hedge BUY. resp=", mainOrderId)
            helper.placeOrder(otmCE, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
            entry_ok = False
            return None

    return mainOrderId


def takeEntryDebit(isBullish, isBearish, syntheticATMStrike, intExpiry, fyers, papertrading):
    """
    DEBIT SPREAD entry: Buy ATM + Sell OTM hedge.
    Same logic as credit spread (500-pt interval, 60% premium cap) — only BUY/SELL flipped.

    syntheticATMStrike/intExpiry are passed in from the caller (computed ONCE, right
    before this call) instead of being recomputed here — same fix as takeEntryCredit,
    guarantees qty/SL sizing uses the exact strike actually traded.
    """
    global hedgeOrderId
    global tradeATMOption
    global tradeHedgeOption
    global mainOrderId
    global tradeOptRange
    global iv_params
    global entry_ok
    global qty  # exitPosition/exitSpreadPosition read module-level qty on exit — must
                # update it here so exits close the ACTUAL traded quantity, not stale default.
    global hedgeEntryPremium   # hedge leg entry price — for realized-vs-assumed offset logging
    global assumedOffsetRatio  # the ratio calc_lots_by_risk actually sized with

    dynamic_sl = iv_params.get("sl_point", sl_point)
    dynamic_target = iv_params.get("target_point", target_point)
    dynamic_spread = iv_params.get("spread_width", 300)

    print("IV-Dynamic: SL=", dynamic_sl, " Target=", dynamic_target,
          " Spread=", dynamic_spread)

    atmCE = getOptionFormatSensex(intExpiry, syntheticATMStrike, "CE")
    atmPE = getOptionFormatSensex(intExpiry, syntheticATMStrike, "PE")

    if isBullish:
        # Bull Call Debit Spread: Buy ATM CE + Sell OTM CE
        # (500-pt interval, hedge <= HEDGE_MAX_PREMIUM_FRACTION of buy premium)
        entryPrice = helper.manualLTP(atmCE, fyers)
        max_premium = entryPrice * HEDGE_MAX_PREMIUM_FRACTION
        # DELTA-BASED hedge selection (primary): hedge |delta| ~= main |delta|/2, snapped to a
        # 500 multiple for liquidity. Also returns both leg deltas (feeds the offset ratio in
        # calc_lots_by_risk), so no separate get_leg_deltas() call is needed.
        hedge_sym, main_delta_val, hedge_delta_val = select_hedge_by_delta(
            syntheticATMStrike, "CE", intExpiry, fyers)
        if hedge_sym:
            otmCE = hedge_sym
        else:
            # FALLBACK: greeks unavailable -> original premium-fraction + 500-grid walk.
            main_delta_val, hedge_delta_val = None, None
            # First 500-grid strike at least HEDGE_MIN_DISTANCE ABOVE the main leg (CE hedge).
            hedge_strike = math.ceil((syntheticATMStrike + HEDGE_MIN_DISTANCE) / 500) * 500
            otmCE_found = None
            for _ in range(6):
                candidate = getOptionFormatSensex(intExpiry, hedge_strike, "CE")
                # Skip (don't crash on) a strike with no usable price — deep-OTM strikes that have
                # not traded return lp=0, and manualLTP now raises rather than reporting 0.
                try:
                    candidate_premium = helper.manualLTP(candidate, fyers)
                except Exception as _hedge_err:
                    print(f"  HEDGE_SKIP {candidate}: no usable price ({_hedge_err})")
                    hedge_strike += 500
                    continue
                print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
                # Require a POSITIVE premium — 0 would satisfy '<= max_premium' and select an
                # untraded, illiquid strike as the hedge.
                if 0 < candidate_premium <= max_premium:
                    otmCE_found = candidate
                    break
                hedge_strike += 500
            otmCE = otmCE_found if otmCE_found else getOptionFormatSensex(intExpiry, syntheticATMStrike + dynamic_spread, "CE")
        print("=atmCE=", atmCE)
        print("=otmCE==", otmCE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        # Fetch hedge premium BEFORE sizing so net-of-hedge lot calc can use it.
        hedge_entry_price = helper.manualLTP(otmCE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("DEBIT_NET_DEBIT=", round(entryPrice - hedge_entry_price, 2))

        # === RISK-BASED LOT SIZING (net-of-hedge, on the exact strikes being traded) ===
        opt_range = get_option_candle_range(atmCE, fyers, n_candles=10)
        tradeOptRange = opt_range  # store for post-entry SL/Target reuse — no re-fetch, no drift
        # Hedge leg range too -> empirical delta-offset ratio for risk sizing. Returns None on
        # any failure (thin/illiquid strike); sizing then falls back to the premium ratio.
        hedge_opt_range = get_option_candle_range(otmCE, fyers, n_candles=10)
        # Deltas come from select_hedge_by_delta above (None in the premium-walk fallback);
        # calc_lots_by_risk then falls back to range/premium when they are None.
        effective_sl_pre = round(opt_range * SL_MULT_BULL) if (opt_range and opt_range > 0) else iv_params.get("sl_point", sl_point)
        qty, assumedOffsetRatio = calc_lots_by_risk(effective_sl_pre, main_premium=entryPrice, hedge_premium=hedge_entry_price,
                                main_range=opt_range, hedge_range=hedge_opt_range,
                                main_delta=main_delta_val, hedge_delta=hedge_delta_val)
        hedgeEntryPremium = hedge_entry_price
        print(f"RISK_SIZING: effective_sl_pre={effective_sl_pre} main={entryPrice} hedge={hedge_entry_price} "
              f"main_range={opt_range} hedge_range={hedge_opt_range} "
              f"FIXED_RISK={FIXED_RISK_PER_TRADE} -> qty={qty} ({qty//LOT_SIZE} lots)")
        print("DEBIT_ENTRY: qty=", qty)

        # === MARGIN CAP CHECK (real broker margin, may reduce qty) ===
        qty = apply_margin_cap(qty, atmCE, 1, otmCE, -1, fyers)

        # BUY ATM CE first (main leg)
        mainOrderId = helper.placeTargetOrder(atmCE, "BUY", qty, "MARKET", entryPrice, 0, 0, fyers, papertrading)
        tradeATMOption = atmCE
        if not _order_ok(mainOrderId):
            print("ENTRY_ABORT: DEBIT bull main BUY leg failed — no position taken. resp=", mainOrderId)
            entry_ok = False
            return None
        time.sleep(0.5)

        # SELL OTM CE (hedge leg — gets margin benefit from buy)
        hedgeOrderId = helper.placeTargetOrder(otmCE, "SELL", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmCE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)
        if not _order_ok(hedgeOrderId):
            print("ENTRY_ABORT: DEBIT bull hedge SELL leg failed — squaring off orphaned main BUY. resp=", hedgeOrderId)
            helper.placeOrder(atmCE, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
            entry_ok = False
            return None

    if isBearish:
        # Bear Put Debit Spread: Buy ATM PE + Sell OTM PE
        # (500-pt interval, hedge <= HEDGE_MAX_PREMIUM_FRACTION of buy premium)
        entryPrice = helper.manualLTP(atmPE, fyers)
        max_premium = entryPrice * HEDGE_MAX_PREMIUM_FRACTION
        # DELTA-BASED hedge selection (primary): hedge |delta| ~= main |delta|/2, snapped to a
        # 500 multiple for liquidity. Also returns both leg deltas (feeds the offset ratio in
        # calc_lots_by_risk), so no separate get_leg_deltas() call is needed.
        hedge_sym, main_delta_val, hedge_delta_val = select_hedge_by_delta(
            syntheticATMStrike, "PE", intExpiry, fyers)
        if hedge_sym:
            otmPE = hedge_sym
        else:
            # FALLBACK: greeks unavailable -> original premium-fraction + 500-grid walk.
            main_delta_val, hedge_delta_val = None, None
            # First 500-grid strike at least HEDGE_MIN_DISTANCE BELOW the main leg (PE hedge).
            hedge_strike = math.floor((syntheticATMStrike - HEDGE_MIN_DISTANCE) / 500) * 500
            otmPE_found = None
            for _ in range(6):
                candidate = getOptionFormatSensex(intExpiry, hedge_strike, "PE")
                # Skip (don't crash on) a strike with no usable price — deep-OTM strikes that have
                # not traded return lp=0, and manualLTP now raises rather than reporting 0.
                try:
                    candidate_premium = helper.manualLTP(candidate, fyers)
                except Exception as _hedge_err:
                    print(f"  HEDGE_SKIP {candidate}: no usable price ({_hedge_err})")
                    hedge_strike -= 500
                    continue
                print(f"  Checking hedge {candidate}: premium={candidate_premium} (max={max_premium})")
                # Require a POSITIVE premium — 0 would satisfy '<= max_premium' and select an
                # untraded, illiquid strike as the hedge.
                if 0 < candidate_premium <= max_premium:
                    otmPE_found = candidate
                    break
                hedge_strike -= 500
            otmPE = otmPE_found if otmPE_found else getOptionFormatSensex(intExpiry, syntheticATMStrike - dynamic_spread, "PE")
        print("=atmPE=", atmPE)
        print("=otmPE==", otmPE, " entryPrice=", entryPrice, " maxHedgePremium=", max_premium)

        # Fetch hedge premium BEFORE sizing so net-of-hedge lot calc can use it.
        hedge_entry_price = helper.manualLTP(otmPE, fyers)
        print("hedge_entry_price =", hedge_entry_price)
        print("DEBIT_NET_DEBIT=", round(entryPrice - hedge_entry_price, 2))

        # === RISK-BASED LOT SIZING (net-of-hedge, on the exact strikes being traded) ===
        opt_range = get_option_candle_range(atmPE, fyers, n_candles=10)
        tradeOptRange = opt_range  # store for post-entry SL/Target reuse — no re-fetch, no drift
        # Hedge leg range too -> empirical delta-offset ratio for risk sizing. Returns None on
        # any failure (thin/illiquid strike); sizing then falls back to the premium ratio.
        hedge_opt_range = get_option_candle_range(otmPE, fyers, n_candles=10)
        # Deltas come from select_hedge_by_delta above (None in the premium-walk fallback);
        # calc_lots_by_risk then falls back to range/premium when they are None.
        effective_sl_pre = round(opt_range * SL_MULT_BEAR) if (opt_range and opt_range > 0) else iv_params.get("sl_point", sl_point)
        qty, assumedOffsetRatio = calc_lots_by_risk(effective_sl_pre, main_premium=entryPrice, hedge_premium=hedge_entry_price,
                                main_range=opt_range, hedge_range=hedge_opt_range,
                                main_delta=main_delta_val, hedge_delta=hedge_delta_val)
        hedgeEntryPremium = hedge_entry_price
        print(f"RISK_SIZING: effective_sl_pre={effective_sl_pre} main={entryPrice} hedge={hedge_entry_price} "
              f"main_range={opt_range} hedge_range={hedge_opt_range} "
              f"FIXED_RISK={FIXED_RISK_PER_TRADE} -> qty={qty} ({qty//LOT_SIZE} lots)")
        print("DEBIT_ENTRY: qty=", qty)

        # === MARGIN CAP CHECK (real broker margin, may reduce qty) ===
        qty = apply_margin_cap(qty, atmPE, 1, otmPE, -1, fyers)

        # BUY ATM PE first (main leg)
        mainOrderId = helper.placeTargetOrder(atmPE, "BUY", qty, "MARKET", entryPrice, 0, 0, fyers, papertrading)
        tradeATMOption = atmPE
        if not _order_ok(mainOrderId):
            print("ENTRY_ABORT: DEBIT bear main BUY leg failed — no position taken. resp=", mainOrderId)
            entry_ok = False
            return None
        time.sleep(0.5)

        # SELL OTM PE (hedge leg — gets margin benefit from buy)
        hedgeOrderId = helper.placeTargetOrder(otmPE, "SELL", qty, "MARKET", hedge_entry_price, 0, 0, fyers, papertrading)
        tradeHedgeOption = otmPE
        print("Exit OID: ", mainOrderId, " ", hedgeOrderId)
        if not _order_ok(hedgeOrderId):
            print("ENTRY_ABORT: DEBIT bear hedge SELL leg failed — squaring off orphaned main BUY. resp=", hedgeOrderId)
            helper.placeOrder(atmPE, "SELL", qty, "MARKET", 0, "regular", fyers, papertrading)
            entry_ok = False
            return None

    return mainOrderId


def takeEntry(isBullish, isBearish, syntheticATMStrike, intExpiry, fyers, papertrading):
    """
    Wrapper: routes to credit or debit entry based on spread_decision.
    OBSERVATION_MODE: when True, just log what WOULD have been taken — no real entry.

    qty is no longer a parameter — takeEntryCredit/takeEntryDebit compute it internally
    right after the strike is locked in, so sizing always matches the exact traded strike.
    """
    global spread_decision
    global entry_ok
    entry_ok = True  # reset each entry; credit/debit set False on a leg rejection
    if OBSERVATION_MODE:
        spread_type = spread_decision.get("type", "CREDIT")
        direction = "BULL" if isBullish else "BEAR"
        print("=" * 60)
        print(f"WOULD_HAVE_ENTERED: type={spread_type} direction={direction} strike={syntheticATMStrike}")
        print(f"  spread_decision={spread_decision}")
        print(f"  iv_params(SL/Target/Spread)= SL={iv_params.get('sl_point')} "
              f"Tgt={iv_params.get('target_point')} Width={iv_params.get('spread_width')}")
        print("  (OBSERVATION_MODE=True — no real order placed)")
        print("=" * 60)
        return  # skip actual entry — st remains unchanged so strategy keeps observing

    spread_type = spread_decision.get("type", "CREDIT")

    print("ENTRY_ROUTING: spread_type=", spread_type,
          " isBullish=", isBullish, " isBearish=", isBearish)

    if spread_type == "DEBIT":
        return takeEntryDebit(isBullish, isBearish, syntheticATMStrike, intExpiry, fyers, papertrading)
    else:
        return takeEntryCredit(isBullish, isBearish, syntheticATMStrike, intExpiry, fyers, papertrading)


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

    # === REALIZED-VS-ASSUMED OFFSET LOGGING (auto-log, does not affect exit or sizing) ===
    # Snapshot LTP on both legs right before firing exit orders (order responses don't carry
    # fill price on a MARKET order, so this is the closest available approximation — same
    # approach already used for the entry anchor). Wrapped so a quote hiccup can never delay
    # or block the actual exit. Compares against entryPremium/hedgeEntryPremium and the ratio
    # assumedOffsetRatio was sized with, so we build a real sample of how often/how far the
    # pre-trade estimate misses (2026-07-30 and 2026-08-10 both showed the assumed ratio
    # overstating the realized one on the SL side).
    try:
        main_exit_ltp = helper.manualLTP(mainATMOption, fyers)
        hedge_exit_ltp = helper.manualLTP(hedgeOption, fyers)
        main_move = abs(main_exit_ltp - entryPremium)
        hedge_move = abs(hedge_exit_ltp - hedgeEntryPremium)
        realized_ratio = round(hedge_move / main_move, 3) if main_move > 0 else None
        print(f"REALIZED_OFFSET: main {entryPremium}->{main_exit_ltp} (moved {round(main_move,2)}) "
              f"hedge {hedgeEntryPremium}->{hedge_exit_ltp} (moved {round(hedge_move,2)}) "
              f"assumed_ratio={assumedOffsetRatio} realized_ratio={realized_ratio}")
    except Exception as _roi_err:
        print("REALIZED_OFFSET_LOG_FAILED (non-fatal):", _roi_err)

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


def pyramid_margin_cap(qty, main_symbol, main_side, hedge_symbol, hedge_side, fyers_client):
    """Margin sizing for the SEQUENTIAL pyramid add.

    The add legs are placed one-by-one (BUY hedge first, then SELL main), so the plain netted
    spread check is too optimistic — on 2026-09-28 it approved 240 (netted req ₹594k <= avail
    ₹618k) but the SELL still rejected for margin shortfall.

    Fix (per design): RESERVE the hedge (BUY) leg's own margin out of available first (it fills
    first and consumes funds), then require the NETTED spread margin to fit in what's LEFT:
        fits if  spread_req(qty)  <=  available - hedge_leg_margin(qty)
    Steps qty down by one lot until it fits. Returns the fitting qty, or 0 if even MIN_LOTS
    doesn't fit (skip the add — original position stays safe at breakeven). Never raises.
    """
    candidate = qty
    while candidate >= LOT_SIZE:
        try:
            # Netted spread requirement + live available for the full pair at this qty.
            # CRITICAL: the BUY (long) leg MUST be listed FIRST — the Fyers multiorder/margin API
            # only applies the hedge-netting benefit when the protective long leg is evaluated
            # before the short. Passing SELL-first returns ~standalone short margin (~3.5x too
            # high) — that bug on 2026-09-29 inflated spread_req and shrank the add to 2 lots.
            _legs = [
                {"symbol": main_symbol, "qty": candidate, "side": main_side, "productType": "INTRADAY"},
                {"symbol": hedge_symbol, "qty": candidate, "side": hedge_side, "productType": "INTRADAY"},
            ]
            _legs = sorted(_legs, key=lambda l: l["side"], reverse=True)  # BUY(+1) before SELL(-1)
            spread_req, avail = helper.getSpreadMargin(_legs, fyers_client)
            # Hedge/BUY leg margin ALONE — reserve it (this leg is placed first).
            hedge_req, _ = helper.getSpreadMargin([
                {"symbol": hedge_symbol, "qty": candidate, "side": hedge_side, "productType": "INTRADAY"},
            ], fyers_client)
        except Exception as _e:
            print("PYRAMID_MARGIN: margin API error — skipping add (safe). err=", _e)
            return 0
        if spread_req is None or hedge_req is None or avail is None:
            print("PYRAMID_MARGIN: margin API returned None — skipping add (safe).")
            return 0
        actual_available = avail - hedge_req      # available AFTER reserving the BUY hedge leg
        print(f"PYRAMID_MARGIN: qty={candidate} ({candidate // LOT_SIZE} lots) "
              f"spread_req={round(spread_req)} hedge_req={round(hedge_req)} avail={round(avail)} "
              f"actual_avail_after_hedge={round(actual_available)}")
        if spread_req <= actual_available:
            return candidate
        candidate -= LOT_SIZE
    print("PYRAMID_MARGIN: even MIN_LOTS does not fit after reserving hedge margin — no add.")
    return 0


def pyramid_add_position(main_symbol, hedge_symbol, is_debit, base_qty):
    """PYRAMIDING: after the trade trails to breakeven (original lot risk-free), scale in by
    adding up to PYRAMID_ADD_MULTIPLIER x the original qty to BOTH legs on the SAME strikes,
    sized to whatever live margin still allows (adds as many lots as fit; never blocks).
    Sides MIRROR the original entry so we ADD to the position, not offset it:
      credit -> main SELL, hedge BUY   |   debit -> main BUY, hedge SELL
    The BUY (long/protective) leg is placed FIRST (avoids a naked-short moment + gets the
    hedge-margin benefit). If a leg fails, the add is aborted/unwound and the original position
    is untouched (still safe at breakeven). Returns the qty actually added (0 if none)."""
    want_qty = PYRAMID_ADD_MULTIPLIER * base_qty
    if is_debit:
        main_side, hedge_side = 1, -1
        main_action, hedge_action = "BUY", "SELL"
    else:
        main_side, hedge_side = -1, 1
        main_action, hedge_action = "SELL", "BUY"

    # 1b: add as many lots as fit AFTER reserving the hedge(BUY) leg's margin out of available,
    # then requiring the netted spread margin to fit in what's left (legs placed sequentially) —
    # see pyramid_margin_cap.
    add_qty = pyramid_margin_cap(want_qty, main_symbol, main_side, hedge_symbol, hedge_side, fyers)
    if add_qty is None or add_qty < LOT_SIZE:
        print(f"PYRAMID_SKIP: no margin room to add (wanted {want_qty}) — keeping original at breakeven")
        return 0

    # BUY (long) leg first, then SELL leg.
    if not is_debit:
        buy_sym, buy_act, sell_sym, sell_act = hedge_symbol, hedge_action, main_symbol, main_action
    else:
        buy_sym, buy_act, sell_sym, sell_act = main_symbol, main_action, hedge_symbol, hedge_action

    buy_oid = helper.placeOrder(buy_sym, buy_act, add_qty, "MARKET", 0, "regular", fyers, papertrading)
    print(f"PYRAMID {buy_act} {buy_sym} qty={add_qty} OID:", buy_oid)
    if not _order_ok(buy_oid):
        print("PYRAMID_ABORT: long add leg failed — no add taken, original stays at breakeven. resp=", buy_oid)
        return 0
    time.sleep(0.5)
    sell_oid = helper.placeOrder(sell_sym, sell_act, add_qty, "MARKET", 0, "regular", fyers, papertrading)
    print(f"PYRAMID {sell_act} {sell_sym} qty={add_qty} OID:", sell_oid)
    if not _order_ok(sell_oid):
        # SELL leg failed after the long filled -> unwind the orphan long to stay balanced.
        _unwind = "SELL" if buy_act == "BUY" else "BUY"
        print("PYRAMID_ABORT: short add leg failed — unwinding orphan long add. resp=", sell_oid)
        helper.placeOrder(buy_sym, _unwind, add_qty, "MARKET", 0, "regular", fyers, papertrading)
        return 0

    print(f"PYRAMID_ADD: base_qty={base_qty} wanted={want_qty} added={add_qty} "
          f"({add_qty // LOT_SIZE} lots) | {main_action} {main_symbol} + {hedge_action} {hedge_symbol} "
          f"| combined qty now {base_qty + add_qty} (SL=breakeven, same target for all)")
    return add_qty


def findStrikePricePremium(optionName, premium, premiumType):
    name = helper.getIndexSpot(stock)
    closest_Strike_PE = ''
    closest_Strike_CE = ''
    strikeList = []
    prev_diff = 10000

    intExpiry = getSensexWeeklyExpiry()
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
            ceOptionFormat = getOptionFormatSensex(intExpiry, strike, "CE")
            ltp_option = helper.manualLTP(ceOptionFormat, fyers)
            diff = abs(ltp_option - premium)
            if diff < prev_diff:
                closest_Strike_CE = strike
                prev_diff = diff
    if optionName == "PE":
        prev_diff = 10000
        for strike in strikeList:
            peOptionFormat = getOptionFormatSensex(intExpiry, strike, "PE")
            ltp_option = helper.manualLTP(peOptionFormat, fyers)
            diff = abs(ltp_option - premium)
            if diff < prev_diff:
                closest_Strike_PE = strike
                prev_diff = diff

    atmCE = getOptionFormatSensex(intExpiry, closest_Strike_CE, "CE")
    atmPE = getOptionFormatSensex(intExpiry, closest_Strike_PE, "PE")

    if optionName == "CE":
        return atmCE
    elif optionName == "PE":
        return atmPE


def _parse_strike_from_symbol(sym):
    """
    Parse the integer strike out of a Sensex option symbol, e.g.
    'BSE:SENSEX2690377100PE' -> 77100. The numeric core = expiry(5 digits) + strike(5 digits),
    so the trailing 5 digits before the CE/PE suffix are the strike. Returns None on any
    malformed symbol (diagnostic-only, must never raise).
    """
    try:
        core = sym.replace("BSE:SENSEX", "")[:-2]  # drop prefix + CE/PE suffix
        return int(core[-5:])                       # last 5 digits = strike
    except Exception:
        return None


def _oi_pair(oi_arr, otype_arr, idx_a, idx_b):
    """
    DIAGNOSTIC/log helper: given the two adjacent option-chain legs of ONE strike
    (one CE + one PE at indices idx_a, idx_b), return ' CE_OI=<> PE_OI=<>' so both
    sides of that strike are printed side by side. Uses option_type to decide which
    index is the call and which is the put (order can vary in the raw chain).
    """
    try:
        if otype_arr[idx_a] == 'PE':
            pe_oi, ce_oi = int(oi_arr[idx_a]), int(oi_arr[idx_b])
        else:
            ce_oi, pe_oi = int(oi_arr[idx_a]), int(oi_arr[idx_b])
        return f" CE_OI={ce_oi} PE_OI={pe_oi}"
    except Exception:
        return " CE_OI=NA PE_OI=NA"


def get_support_resistance(futltp, step=500, buffer=None):
    """Support/resistance from the 500-pt grid around price.

    When USE_NO_TRADE_ZONE is True, a dead band of (midpoint ± buffer) is skipped: price in the
    LOWER part of the band -> return support (bull side), UPPER part -> resistance (bear side),
    and the middle band -> "NOTRADEZONE" (no directional trade). buffer defaults to
    NO_TRADE_ZONE_BUFFER. When False, falls back to a simple midpoint split (no dead zone).
    """
    support = (futltp // step) * step
    resistance = support + step
    middle = (support + resistance) / 2

    if USE_NO_TRADE_ZONE:
        b = NO_TRADE_ZONE_BUFFER if buffer is None else buffer
        no_trade_low = middle - b
        no_trade_high = middle + b
        if futltp <= no_trade_low:
            return support
        elif futltp >= no_trade_high:
            return resistance
        else:
            return "NOTRADEZONE"

    # Simple midpoint split (dead zone disabled)
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

    intExpiry = getSensexWeeklyExpiry()
    dataFUT = helper.getHistorical(BNFut, timeFrame, 3, fyers)
    opens = dataFUT['open'].to_numpy()
    high = dataFUT['high'].to_numpy()
    low = dataFUT['low'].to_numpy()
    close = dataFUT['close'].to_numpy()

    # Guard: need at least 3 candles (function accesses [-2] and [-3]).
    # Protects against early-session / post-holiday cases with insufficient candles.
    if len(opens) < 3:
        print("checkCriteriaAndTakeTrade: skipping — fewer than 3 candles formed (", len(opens), ")")
        return None

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

    currCEStrike = getOptionFormatSensex(intExpiry, currCandleStrike, "CE")
    currPEStrike = getOptionFormatSensex(intExpiry, currCandleStrike, "PE")
    prevCEStrike = getOptionFormatSensex(intExpiry, prevCandleStrike, "CE")
    prevPEStrike = getOptionFormatSensex(intExpiry, prevCandleStrike, "PE")

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

    sname = "BSE:SENSEX-INDEX"
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
# Observation-only: last cycle's cumulative CE/PE oich per strike, used to derive the 3-min
# INCREMENTAL (fresh) change-in-OI at the SUPP_RES strike = now_oich - prev_cycle_oich.
_prev_suppres_choi = {}   # {strike: (ce_oich, pe_oich)}
# Consecutive-streak of INCR_CHOI fresh-flow dominance at the S/R level. Flags flip True once
# the same-side dominance persists >= CHOI_DOM_STREAK consecutive cycles (mirrors the PCR
# consecutive-trend idea): PE-dominant at a SUPPORT -> isPE_DominantAtSupp (bull confirm),
# CE-dominant at a RESISTANCE -> isCE_DominantAtRes (bear confirm). Observation only for now.
_pe_supp_streak = 0
_ce_res_streak = 0
isPE_DominantAtSupp_once = False    # PE dominant at support this cycle (streak >=1)
isCE_DominantAtRes_once = False     # CE dominant at resistance this cycle (streak >=1)
isPE_DominantAtSupp_twice = False   # PE dominant at support >=2 consecutive cycles
isPE_DominantAtSupp_trice = False   # PE dominant at support >=3 consecutive cycles
isCE_DominantAtRes_twice = False    # CE dominant at resistance >=2 consecutive cycles
isCE_DominantAtRes_trice = False    # CE dominant at resistance >=3 consecutive cycles
# Latest 3-min fresh (incremental) change-in-OI at the SUPP_RES strike. Persisted so the entry
# gate can require the dominant side to be freshly ADDED (positive) rather than unwound.
_last_fresh_ce = 0
_last_fresh_pe = 0
avgOiPcr = {}
SUPP_RES_STRIKE = ''
AVGOI_PCR = 0
TOTAL_PCR = 0
avgoiPCR9 = 0     # narrow 9-strike PCR (ATM +/-4), observation-only
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
avgOiPcrList2 = []; avgOiPcr9List2 = []
isBullTrade = False
isBearTrade = False
hedgeOrderId = ''
mainOrderId = ''
tradeHedgeOption = ''
tradeATMOption = ''
tradeOptRange = None  # option candle-range median computed ONCE on the final traded strike,
                       # reused for both qty sizing and post-entry SL/Target so both use the
                       # exact same snapshot (fixes qty/SL mismatch from separate live calls).
entry_ok = True  # set False by takeEntryCredit/Debit if a leg is rejected (orphaned leg is
                  # squared off); caller checks it to reset state and skip monitoring.
entryPremium = 0  # track entry premium for trailing SL
trailTriggerPts = 0  # effective_tgt * TRAIL_TRIGGER_TARGET_FRACTION — set at entry time
slTrailed = False
pyramided = False  # one-time guard: True once we've reached the trail point on this trade
slConfirmCount = 0
spread_type_decided = False  # flag to decide spread type only once per day

# --- Realized-vs-assumed hedge offset tracking (auto-logging, sizing/logic untouched) ---
# We size lots using an offset ratio ESTIMATED at entry (from candle range or premium ratio).
# 2026-07-30 and 2026-08-10 both showed the realized ratio come in well BELOW the assumed one
# on the SL side (real losses of 7,096 and 6,480 vs a 5,000 budget), in the same direction both
# times — suggesting a structural bias (main leg gains delta faster than the hedge as spot
# approaches the main strike), not just noise. These two just log the ACTUAL outcome next to
# what was assumed at entry, so we build a real sample before touching the sizing formula.
hedgeEntryPremium = 0     # hedge leg entry premium — mirrors entryPremium for the main leg
assumedOffsetRatio = None  # the used_ratio calc_lots_by_risk actually sized with


# ============================================================
# MAIN WHILE LOOP
# ============================================================

# Guards the candle block to run ONCE per candle-minute. The entry gate is `second <= 1`
# (a 2s window) and the block ends with sleep(1), so a fast pass can loop back while still
# inside that window and process the same candle twice (was masked before by the slower
# checkCriteriaAndTakeTrade call). Tracking the last processed minute makes it deterministic.
last_candle_minute = -1

while x == 1:

    dt1 = datetime.now()
    now = datetime.now()
    custom_time = datetime(now.year, now.month, now.day, entryHour, entryMinute)
    custom_time1 = datetime(now.year, now.month, now.day, entryHour1, entryMinute1)

    if now >= custom_time1:

        if dt1.second <= 1 and dt1.minute % timeFrame == 0 and dt1.minute != last_candle_minute:
            last_candle_minute = dt1.minute   # process this candle only once
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
            sname = "BSE:SENSEX-INDEX"
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
            intExpiry = getSensexWeeklyExpiry()

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
            # NEW (observation only): narrow 9-strike PCR = ATM (pcr9) + 4 above + 4 below,
            # i.e. pcr5..pcr13. Tighter than the 17-strike avgoiPCROld — closer to spot, less
            # diluted by far strikes. Logged just below the main PCR for comparison, not wired
            # into entry yet.
            pcr9StrikeList = [pcr5, pcr6, pcr7, pcr8, pcr9, pcr10, pcr11, pcr12, pcr13]
            avgoiPCR9 = round(sum(pcr9StrikeList) / 9, 2)
            volPcrList = [volpcr1, volpcr2, volpcr3, volpcr4, volpcr5, volpcr6, volpcr7, volpcr8, volpcr9]

            print("".ljust(35), "oipcr", " ", "choipcr", " ", "CE_OI / PE_OI (same strike)")
            print("pcr1 = ", symbol[-2], " ", pcr1, " ", changOI, _oi_pair(oi, option_type, -1, -2))
            print("pcr2 = ", symbol[-4], " ", pcr2, " ", changOIpcr2, _oi_pair(oi, option_type, -3, -4))
            print("pcr3 = ", symbol[-6], " ", pcr3, " ", changOIpcr3, _oi_pair(oi, option_type, -5, -6))
            if strikecount >= 2:
                print("pcr4 = ", symbol[-8], " ", pcr4, " ", changOIpcr4, _oi_pair(oi, option_type, -7, -8))
                print("pcr5 = ", symbol[-10], " ", pcr5, " ", changOIpcr5, _oi_pair(oi, option_type, -9, -10))
                print("pcr6 = ", symbol[-12], " ", pcr6, "  ", changOIpcr6, _oi_pair(oi, option_type, -11, -12))
                print("pcr7 = ", symbol[-14], " ", pcr7, " ", changOIpcr7, _oi_pair(oi, option_type, -13, -14))
                print("pcr8 = ", symbol[-16], " ", pcr8, " ", changOIpcr8, _oi_pair(oi, option_type, -15, -16))
                print("pcr9 = ", symbol[-18], " ", pcr9, " ", changOIpcr9, _oi_pair(oi, option_type, -17, -18))
                print("pcr10 = ", symbol[-20], " ", pcr10, " ", changOIpcr10, _oi_pair(oi, option_type, -19, -20))
                print("pcr11 = ", symbol[-22], " ", pcr11, " ", changOIpcr11, _oi_pair(oi, option_type, -21, -22))
                print("pcr12 = ", symbol[-24], " ", pcr12, " ", changOIpcr12, _oi_pair(oi, option_type, -23, -24))
                print("pcr13 = ", symbol[-26], " ", pcr13, " ", changOIpcr13, _oi_pair(oi, option_type, -25, -26))
                print("pcr14 = ", symbol[-28], " ", pcr14, " ", changOIpcr14, _oi_pair(oi, option_type, -27, -28))
                print("pcr15 = ", symbol[-30], " ", pcr15, " ", changOIpcr15, _oi_pair(oi, option_type, -29, -30))
                print("pcr16 = ", symbol[-32], " ", pcr16, " ", changOIpcr16, _oi_pair(oi, option_type, -31, -32))
                print("pcr17 = ", symbol[-34], " ", pcr17, " ", changOIpcr17, _oi_pair(oi, option_type, -33, -34))

                symbolPcrMap = {
                    symbol[-2]: pcr1, symbol[-4]: pcr2, symbol[-6]: pcr3, symbol[-8]: pcr4,
                    symbol[-10]: pcr5, symbol[-12]: pcr6, symbol[-14]: pcr7, symbol[-16]: pcr8,
                    symbol[-18]: pcr9, symbol[-20]: pcr10, symbol[-22]: pcr11, symbol[-24]: pcr12,
                    symbol[-26]: pcr13, symbol[-28]: pcr14, symbol[-30]: pcr15, symbol[-32]: pcr16,
                    symbol[-34]: pcr17
                }

                # Fetch spot first (needed for the anchor). strikecount is always >=2 here, so
                # this always runs; the duplicate spot fetch below was removed to avoid a 2nd call.
                sensexIndex = helper.getIndexSpot(stock)
                spotLTP = helper.manualLTP(sensexIndex, fyers)
                FUT_LTP = helper.manualLTP(BNFut, fyers)
                # anchorLTP drives S/R, the PCR-window center, and the breakout comparisons.
                anchorLTP = spotLTP if USE_SPOT_ANCHOR else FUT_LTP
                SYNTH_FUT_STRIKE = round(anchorLTP / 100) * 100

                pcrSummation, result = sum_with_neighbors(symbolPcrMap, str(SYNTH_FUT_STRIKE))
                # print("pcrSummation=", pcrSummation, " result = ", result)  # log noise — commented

                pcrSum = round(sum(pcrList), 2)
                print("PCRSUM==", pcrSum)
                avgoiPCROld = round(pcrSum / 17, 2)

                pcrSummation = round(pcrSummation, 2)
                print("pcrSummation==", pcrSummation)
                avgoiPCR = round(pcrSummation / 9, 2)

                volpcrsum = sum(volPcrList)
                # print("VOLPCRSUM==", volpcrsum)
                avgvolPCR = round(volpcrsum / 9, 2)

                avgOiPcrList.append(avgoiPCR)
                avgOiPcrList2.append(avgoiPCROld); avgOiPcr9List2.append(avgoiPCR9)

            # ATM Strike shift detection (spotLTP already fetched above with the anchor)
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
                    avgOiPcrList2 = avgOiPcrList2[-1:]; avgOiPcr9List2 = avgOiPcr9List2[-1:]
            else:
                mapStrike[ATM_STRIKE] = ATM_STRIKE
            print("IS_ATM_STRIKE_SHIFT =", IS_ATM_STRIKE_SHIFT, " mapStrike =", mapStrike)
            print("avgOiPcrList2 =", avgOiPcrList2, "atmStrikeNotShiftedCount=", atmStrikeNotShiftedCount)
            print("avgOiPcr9List2 =", avgOiPcr9List2, "atmStrikeNotShiftedCount=", atmStrikeNotShiftedCount)
            print(IS_ATM_STRIKE_SHIFT, " ", atmStrikeNotShiftedCount, " ", len(avgOiPcrList2))

            # ===== INVARIANT-WINDOW PCR (17 + 9) — OBSERVATION / LOG ONLY =====
            # Keep the last 3 cycles' per-strike OI and re-measure every one of
            # them over the CURRENT strike set, so an ATM shift neither fakes nor
            # destroys a PCR trend. Uses the dfochain already in memory -> ZERO
            # extra API calls. Touches NO existing variable or gate; wrapped so a
            # bad cycle can never break the loop.
            #
            # Lines are BUFFERED here and printed later in the "signal check time"
            # section. The computation must stay HERE so chainOiHistory gets a
            # snapshot on EVERY cycle — a `continue` between this point and the
            # print site would otherwise leave a hole in the 3-cycle window.
            del invPcrLogLines[:]
            _ibuf = invPcrLogLines
            try:
                _inv_strikes17 = ordered_pcr_strikes(dfochain, 17)
                if _inv_strikes17:
                    chainOiHistory.append(build_strike_oi_map(dfochain))
                    del chainOiHistory[:-INV_PCR_WINDOW]
                    # Central 9 = pcr5..pcr13 (ATM pcr9 +/- 4), same as
                    # pcr9StrikeList, taken from the CURRENT ordered strikes.
                    _inv_strikes9 = _inv_strikes17[4:13]
                    _ser17, _ser9, _cov = [], [], []
                    _cov17, _cov9 = [], []
                    for _snap in chainOiHistory:
                        _a17, _c17 = invariant_avg_pcr(_snap, _inv_strikes17)
                        _a9, _c9 = invariant_avg_pcr(_snap, _inv_strikes9)
                        if _a17 is not None and _a9 is not None:
                            _ser17.append(_a17)
                            _ser9.append(_a9)
                            if INV_LOG_NARROW_17:
                                _cov.append(f"{_c17}/{len(_inv_strikes17)}"
                                            f",{_c9}/{len(_inv_strikes9)}")
                            else:
                                _cov.append(f"{_c9}/{len(_inv_strikes9)}")
                            _cov17.append(_c17)
                            _cov9.append(_c9)
                    _ibuf.append("==== INVARIANT-WINDOW PCR check (log-only) ====")
                    if INV_LOG_NARROW_17:
                        _ibuf.append(f"inv_strikes17 = {_inv_strikes17}")
                    _ibuf.append(f"inv_strikes9  = {_inv_strikes9}"
                                 "  (central 9 = pcr5..pcr13)")
                    _ibuf.append(f"inv_strike_coverage_per_cycle = {_cov}"
                                 + ("  (17-strike,9-strike found in each stored snapshot)"
                                    if INV_LOG_NARROW_17
                                    else "  (9-strike found in each stored snapshot)"))
                    # A stored snapshot only holds the 17 strikes that were in the chain
                    # at THAT time (strikecount=8 -> ATM +/- 8). After an ATM shift the
                    # older snapshots are missing the new edge strikes, so their average
                    # is taken over FEWER strikes than the current cycle's. The averages
                    # are then not strictly comparable and a "trend" may be a coverage
                    # artifact. Flag it explicitly rather than let the series look clean.
                    # The 9-strike set is the more fragile of the two: it is small, so
                    # each missing strike is ~11% of the sample.
                    if _cov17 and _cov9:
                        _deg = []
                        if INV_LOG_NARROW_17 and min(_cov17) < len(_inv_strikes17):
                            _deg.append(f"17-strike {min(_cov17)}/{len(_inv_strikes17)}")
                        if min(_cov9) < len(_inv_strikes9):
                            _deg.append(f"9-strike {min(_cov9)}/{len(_inv_strikes9)}")
                        if _deg:
                            _ibuf.append("INV_WINDOW_DEGRADED: not apples-to-apples — "
                                         f"worst coverage {' and '.join(_deg)}"
                                         " (ATM shifted; older snapshots predate the"
                                         " current edge strikes). Treat the verdicts"
                                         " below as indicative only.")
                        else:
                            _ibuf.append("INV_WINDOW_FULL: every stored snapshot covers "
                                         "all current strikes — averages are directly "
                                         "comparable.")
                    log_invariant_pcr_window("9", _ser9, buf=_ibuf)
                    # Narrow 17 is superseded by the WIDE 17 (see INV_LOG_NARROW_17).
                    if INV_LOG_NARROW_17:
                        log_invariant_pcr_window("17", _ser17, buf=_ibuf)
                else:
                    _ibuf.append("INV_WINDOW_SKIPPED: no strike_price column / strikes "
                                 "unavailable this cycle (needs helper_fyers "
                                 "getClosestOptions1 to return strike_price).")
            except Exception as _inv_e:
                _ibuf.append(f"INV_WINDOW_WARN: invariant-window block failed: "
                             f"{type(_inv_e).__name__}: {_inv_e}")

            # WIDE-CHAIN invariant 17-PCR — its own strikecount=11 fetch so the
            # 17-strike window has 3 spare strikes of buffer each side. Separate
            # from everything above: own history list, own log labels, feeds no
            # gate. Self-contained try/except inside the function.
            log_invariant_pcr_wide(sname, fyers)

            SUPP_RES = get_support_resistance(anchorLTP)
            if SUPP_RES != "NOTRADEZONE":
                SUPP_RES = round(SUPP_RES)

            print("SUPP_RES===", SUPP_RES, " Buffer=", iv_params.get("support_resistance_buffer", 30))

            # === DIAGNOSTIC (no behaviour change): spot-anchor (now PRIMARY) vs future-anchor ===
            # SUPP_RES + PCR window now use SPOT (anchorLTP). This one-liner shows the basis and
            # what the S/R WOULD be on the monthly future, so the two anchors stay comparable at a
            # glance. ZERO extra API calls (spotLTP/FUT_LTP already fetched; get_support_resistance
            # is pure).
            try:
                _sr_fut = get_support_resistance(FUT_LTP)
                if _sr_fut != "NOTRADEZONE":
                    _sr_fut = round(_sr_fut)
                _basis = round(FUT_LTP - spotLTP, 2)
                _win_spot = SYNTH_FUT_STRIKE                   # PCR-window center now (spot)
                _win_fut = round(FUT_LTP / 100) * 100          # PCR-window center if on future
                _sr_diff = (_sr_fut - SUPP_RES) if (SUPP_RES != "NOTRADEZONE" and _sr_fut != "NOTRADEZONE") else "NA"
                print(f"ANCHOR_COMPARE: spot={spotLTP} fut={FUT_LTP} basis(fut-spot)={_basis} | "
                      f"SR_spot(PRIMARY)={SUPP_RES} SR_future={_sr_fut} SR_diff(fut-spot)={_sr_diff} | "
                      f"PCRwin_spot(PRIMARY)={_win_spot} PCRwin_future={_win_fut}")
            except Exception as _anchor_err:
                print("ANCHOR_COMPARE_FAILED (non-fatal, diagnostic only):", _anchor_err)
            suppResCE = getOptionFormatSensex(intExpiry, SUPP_RES, "CE")
            suppResPE = getOptionFormatSensex(intExpiry, SUPP_RES, "PE")

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

                # === DIAGNOSTIC (observation only, no behaviour change): 3-min INCREMENTAL
                # (fresh) change-in-OI at the SUPP_RES strike. oich is cumulative-vs-prev-day,
                # so the fresh flow in THIS 3-min window = now_oich - last_cycle_oich for the
                # SAME strike. This is the "flow" (who is writing RIGHT NOW at the level) vs the
                # cumulative CHOI above (the "stock"); flow leads. Fresh PE-dominant = bullish
                # (put writers defending), fresh CE-dominant = bearish (call writers capping).
                # Keyed by strike so a SUPP_RES shift compares like-for-like. ZERO extra API.
                try:
                    # Level role: price ABOVE SUPP_RES -> it's acting as SUPPORT; price BELOW ->
                    # RESISTANCE. The confirm/warning hint depends on this role (see table): fresh
                    # PE at a support = holding (bullish); fresh CE at a support = undermined
                    # (bearish). At a resistance it's mirrored.
                    _sr_role = "SUPPORT" if spotLTP >= SUPP_RES else "RESISTANCE"
                    _prev_sr = _prev_suppres_choi.get(SUPP_RES)
                    if _prev_sr is not None:
                        _fresh_ce = round(suppResCeChOi - _prev_sr[0])
                        _fresh_pe = round(suppResPeChOi - _prev_sr[1])
                        _fdom = "CE" if _fresh_ce > _fresh_pe else ("PE" if _fresh_pe > _fresh_ce else "FLAT")
                        _fden = max(abs(_fresh_ce), abs(_fresh_pe)) or 1
                        _fgap = round(abs(_fresh_ce - _fresh_pe) / _fden * 100, 1)
                        # Map dominance -> confirm/warning given the level role.
                        if _fdom == "FLAT":
                            _hint = "neutral"
                        elif _sr_role == "SUPPORT":
                            _hint = "support HOLDING (bullish)" if _fdom == "PE" else "support UNDERMINED (bearish warning)"
                        else:  # RESISTANCE
                            _hint = "resistance HOLDING (bearish)" if _fdom == "CE" else "resistance CHALLENGED (bullish warning)"
                        print(f"INCR_CHOI: SUPP_RES={SUPP_RES} role={_sr_role} fresh_CE={_fresh_ce} fresh_PE={_fresh_pe} "
                              f"-> {_fdom}_dominant by {_fgap}% ({_hint}) | cumulative CE={suppResCeChOi} PE={suppResPeChOi}")
                        # Consecutive fresh-flow dominance streaks -> confirmation flags.
                        if _sr_role == "SUPPORT" and _fdom == "PE":
                            _pe_supp_streak += 1
                        else:
                            _pe_supp_streak = 0
                        if _sr_role == "RESISTANCE" and _fdom == "CE":
                            _ce_res_streak += 1
                        else:
                            _ce_res_streak = 0
                        isPE_DominantAtSupp_once = (_pe_supp_streak >= 1)
                        isPE_DominantAtSupp_twice = (_pe_supp_streak >= 2)
                        isPE_DominantAtSupp_trice = (_pe_supp_streak >= 3)
                        isCE_DominantAtRes_once = (_ce_res_streak >= 1)
                        isCE_DominantAtRes_twice = (_ce_res_streak >= 2)
                        isCE_DominantAtRes_trice = (_ce_res_streak >= 3)
                        # Persist this cycle's fresh flow so the entry gate can require the
                        # dominant side to be freshly ADDED (positive), not unwound.
                        _last_fresh_ce = _fresh_ce
                        _last_fresh_pe = _fresh_pe
                    else:
                        print(f"INCR_CHOI: SUPP_RES={SUPP_RES} role={_sr_role} first sighting (no prev cycle to diff) "
                              f"| cumulative CE={suppResCeChOi} PE={suppResPeChOi}")
                        # No comparable prev cycle at this strike -> cannot confirm fresh flow.
                        # Reset so the dominance entry gate stays conservative (blocks).
                        _last_fresh_ce = 0
                        _last_fresh_pe = 0
                    _prev_suppres_choi[SUPP_RES] = (suppResCeChOi, suppResPeChOi)
                except Exception as _incr_err:
                    print("INCR_CHOI_DIAG_FAILED (non-fatal, diagnostic only):", _incr_err)
            else:
                print("not found")

            # === DIAGNOSTIC (no behaviour change): FUT-based S/R for OBSERVATION. The primary
            # SUPP_RES above is now SPOT-based (anchorLTP); this logs what the S/R WOULD be on the
            # monthly future + its CHOI/total-OI, so we can keep comparing spot-vs-future anchor
            # over a few sessions. Uses the already-fetched dfochain -> ZERO extra API calls.
            # Separate _fs_* locals -> trading logic untouched. ===
            try:
                _sr_fut = get_support_resistance(FUT_LTP)
                if _sr_fut != "NOTRADEZONE":
                    _sr_fut = round(_sr_fut)
                    _fs_ce_sym = getOptionFormatSensex(intExpiry, _sr_fut, "CE")
                    _fs_pe_sym = getOptionFormatSensex(intExpiry, _sr_fut, "PE")
                    _fs_r1 = dfochain[dfochain['symbol'] == _fs_ce_sym]
                    _fs_r2 = dfochain[dfochain['symbol'] == _fs_pe_sym]
                    print("==========================")
                    print("FUT_SUPP_RES===", _sr_fut, " (FUT_LTP=", FUT_LTP, ") Buffer=", iv_params.get("support_resistance_buffer", 30))
                    if not _fs_r1.empty and not _fs_r2.empty:
                        _fs_ce_choi = _fs_r1.iloc[0]['oich']
                        _fs_pe_choi = _fs_r2.iloc[0]['oich']
                        _fs_ce_oi = int(_fs_r1.iloc[0]['oi'])
                        _fs_pe_oi = int(_fs_r2.iloc[0]['oi'])
                        _den_choi = max(abs(_fs_ce_choi), abs(_fs_pe_choi)) or 1
                        _den_oi = max(_fs_ce_oi, _fs_pe_oi) or 1
                        print("FUT CEoich val = ", _fs_ce_choi, " PEoich val = ", _fs_pe_choi, ",",
                              f"CE > PE by {round(abs(_fs_ce_choi - _fs_pe_choi) / _den_choi * 100, 1)}%" if _fs_ce_choi > _fs_pe_choi
                              else f"CE < PE by {round(abs(_fs_ce_choi - _fs_pe_choi) / _den_choi * 100, 1)}%")
                        print("FUT_SUPP_RES TOTAL OI: CE=", _fs_ce_oi, " PE=", _fs_pe_oi, ",",
                              f"CE > PE by {round(abs(_fs_ce_oi - _fs_pe_oi) / _den_oi * 100, 1)}%" if _fs_ce_oi > _fs_pe_oi
                              else f"CE < PE by {round(abs(_fs_ce_oi - _fs_pe_oi) / _den_oi * 100, 1)}%")
                    else:
                        print("FUT_SUPP_RES: strike", _sr_fut, "not found in chain (may be outside strikecount window)")
                else:
                    print("FUT_SUPP_RES=== NOTRADEZONE")
            except Exception as _fut_sr_err:
                print("FUT_SUPP_RES_DIAG_FAILED (non-fatal, diagnostic only):", _fut_sr_err)

            # (The OI-based Support/Resistance diagnostic block that used to sit here —
            # OI_SUPP_RES / OI_SUPPORT / OI_RESISTANCE — was removed. It was pure logging,
            # never wired into any gate. The live S/R remains get_support_resistance()
            # on the spot anchor, logged as SUPP_RES.)

            # Morning rule: 9:15-9:48 IST. CHOI is noisy/zero in first ~11 candles after open.
            # Use TOTAL OI direction (carried from yesterday's positioning) as the trapped-writers signal.
            IS_MORNING_WINDOW = (dt1.hour == 9 and dt1.minute < 48)
            print("IS_MORNING_WINDOW=", IS_MORNING_WINDOW, " (active 9:15-9:48 IST)")

            # CHOI validity (user rule 2026-10-22, same change already made in BTC).
            #
            # WAS: (ce > 0 and pe > 0) — both sides had to be building, and a single
            # negative side forced _choi_pct to 0, which fails every Logic 1/2/3
            # CHOI gate. On BTC that vetoed 22 of 50 cycles, because "calls
            # unwinding while puts build at support" is the NORMAL trending-day
            # flow and is directionally BULLISH (call writers covering, put
            # writers defending) rather than noise.
            #
            # NOW: valid when EITHER side is building. One side negative is a
            # MIXED-SIGN divergence and is considered; only BOTH sides unwinding
            # (or both flat) is skipped, since that carries no directional
            # conviction either way.
            #
            #   ce>0 and pe>0  -> valid (buildup both sides, as before)
            #   one side < 0   -> valid (divergence: one building, one unwinding)
            #   both <= 0      -> INVALID, cycle skipped for CHOI logic
            #
            # NOTE on the % figure: with mixed signs the gap formula runs LARGE
            # (CE=-20000, PE=10000 -> 150%). That is intended — the size of a
            # build-vs-unwind divergence IS the signal strength. Read a >100%
            # value as "the two sides are moving in OPPOSITE directions", not as
            # a buildup gap. The direction leg still orients correctly because a
            # positive always beats a negative in the PE-vs-CE comparison.
            _choi_valid = (suppResCeChOi > 0 or suppResPeChOi > 0)
            _choi_mixed = _choi_valid and (suppResCeChOi < 0 or suppResPeChOi < 0)
            if _choi_valid:
                IS_CHOI_DIFF_GT_25PERC = is_difference_greater_than_25(suppResCeChOi, suppResPeChOi)
                if _choi_mixed:
                    _bld = "PE" if suppResPeChOi > 0 else "CE"
                    _unw = "CE" if suppResPeChOi > 0 else "PE"
                    print(f"CHOI_MIXED: {_bld} building / {_unw} unwinding at SUPP_RES "
                          f"(CEchoi={suppResCeChOi}, PEchoi={suppResPeChOi}) - CONSIDERED "
                          f"(divergence); % gap is inflated by the opposite signs")
            else:
                IS_CHOI_DIFF_GT_25PERC = False
                print(f"CHOI_UNWIND: both sides unwinding/flat at SUPP_RES "
                      f"(CEchoi={suppResCeChOi}, PEchoi={suppResPeChOi}) - CHOI-based "
                      f"logic skipped this cycle (need at least one side building)")
            print("================================================")
            print("==== signal check time ====", datetime.now())
            # print("IS_CHOI_DIFF_GT_25PERC==", IS_CHOI_DIFF_GT_25PERC)

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
            # print("RSI_VAL1==", RSI_VAL1)
            # print("RSI_VAL2==", RSI_VAL2)
            # print("FUT_3m_OHLC O=", round(opens[-2], 1), " H=", round(high[-2], 1),
            #       " L=", round(low[-2], 1), " C=", round(close[-2], 1),
            #       " Range=", round(high[-2] - low[-2], 1))

            # --- Chart Pattern Detection on FUT candles ---
            log_chart_patterns(opens, high, low, close, iv_params)

            # NEW (observation only): same consecutive-trend logic on the narrow 9-strike PCR.
            # avgOiPcr9List2 shadows avgOiPcrList2's window exactly (same append/reset/trim), so
            # it always holds the matching triplet. Read-only here (no trimming — the 17-strike
            # block below manages the shared window); just reports its own inc/dec verdict + the
            # per-step % so we can compare the narrow PCR trend against the wide one.
            if not IS_ATM_STRIKE_SHIFT and atmStrikeNotShiftedCount >= 3 and len(avgOiPcr9List2) == 3:
                _q0, _q1, _q2 = avgOiPcr9List2[0], avgOiPcr9List2[1], avgOiPcr9List2[2]
                _qstep1 = round((_q1 - _q0) / _q0 * 100, 1) if _q0 else 0.0
                _qstep2 = round((_q2 - _q1) / _q1 * 100, 1) if _q1 else 0.0
                if _q0 < _q1 < _q2:
                    print("isPcrInc9 = True")
                    print(f"PCR9_SEQ_INC: {_q0} -> {_q1} -> {_q2} | pcr2 > pcr1 by +{_qstep1}% | pcr3 > pcr2 by +{_qstep2}%")
                elif _q0 > _q1 > _q2:
                    print("isPcrDecr9 = True")
                    print(f"PCR9_SEQ_DEC: {_q0} -> {_q1} -> {_q2} | pcr2 < pcr1 by {_qstep1}% | pcr3 < pcr2 by {_qstep2}%")
                else:
                    print(f"PCR9_SEQ_FLAT: {_q0} -> {_q1} -> {_q2} (not monotonic)")

            # PCR trend detection (3 consecutive values)
            if not IS_ATM_STRIKE_SHIFT and atmStrikeNotShiftedCount >= 3 and len(avgOiPcrList2) == 3:
                _p0, _p1, _p2 = avgOiPcrList2[0], avgOiPcrList2[1], avgOiPcrList2[2]
                _step1_pct = round((_p1 - _p0) / _p0 * 100, 1) if _p0 else 0.0
                _step2_pct = round((_p2 - _p1) / _p1 * 100, 1) if _p1 else 0.0
                if avgOiPcrList2[0] < avgOiPcrList2[1] < avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = True
                    print("isPcrInc =", IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2)
                    # Observation: the rising 3-value sequence and each step's % rise.
                    print(f"PCR_SEQ_INC: {_p0} -> {_p1} -> {_p2} | pcr2 > pcr1 by +{_step1_pct}% | pcr3 > pcr2 by +{_step2_pct}%")
                elif avgOiPcrList2[0] > avgOiPcrList2[1] > avgOiPcrList2[2]:
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = True
                    print("isPcrDecr =", IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2)
                    # Observation: the falling 3-value sequence and each step's % drop.
                    print(f"PCR_SEQ_DEC: {_p0} -> {_p1} -> {_p2} | pcr2 < pcr1 by {_step1_pct}% | pcr3 < pcr2 by {_step2_pct}%")
                else:
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    if avgOiPcrList2[1] < avgOiPcrList2[2] or avgOiPcrList2[1] > avgOiPcrList2[2]:
                        avgOiPcrList2 = avgOiPcrList2[1:]; avgOiPcr9List2 = avgOiPcr9List2[1:]
                        print("recent two =", avgOiPcrList2)
                    else:
                        avgOiPcrList2 = avgOiPcrList2[2:]; avgOiPcr9List2 = avgOiPcr9List2[2:]
                        print("neither =", avgOiPcrList2)
            elif len(avgOiPcrList2) == 3:
                remove = 3 - atmStrikeNotShiftedCount
                avgOiPcrList2 = avgOiPcrList2[remove:]; avgOiPcr9List2 = avgOiPcr9List2[remove:]
                print("none =", avgOiPcrList2)

            # Observation-only confirmation flags derived from INCR_CHOI consecutive dominance.
            # Printed like isPcrInc/isPcrDecr — only the ACTIVE side shows: PE-at-support (bull
            # confirm) when that streak is live, CE-at-resistance (bear confirm) when that one is.
            if _pe_supp_streak > 0:
                print("isPE_DominantAtSupp_once =", isPE_DominantAtSupp_once,
                      " isPE_DominantAtSupp_twice =", isPE_DominantAtSupp_twice,
                      " isPE_DominantAtSupp_trice =", isPE_DominantAtSupp_trice,
                      "(PE_supp_streak=", _pe_supp_streak, ")")
            elif _ce_res_streak > 0:
                print("isCE_DominantAtRes_once =", isCE_DominantAtRes_once,
                      " isCE_DominantAtRes_twice =", isCE_DominantAtRes_twice,
                      " isCE_DominantAtRes_trice =", isCE_DominantAtRes_trice,
                      "(CE_res_streak=", _ce_res_streak, ")")

            # ----- INVARIANT-WINDOW PCR output (computed earlier this cycle) -----
            # Printed here, alongside the other signal flags, rather than up where it
            # is computed. Both blocks are LOG-ONLY and feed no gate.
            for _line in invPcrLogLines:
                print(_line)
            for _line in invWideLogLines:
                print(_line)

            # print("newSynthFut = ", SYNTH_FUT_STRIKE)
            print("ATMStrike = ", ATM_STRIKE)
            print("AVG_OIPCR=", avgoiPCR)
            print("avgoiPCROld= ", avgoiPCROld)
            print("AVG_OIPCR_9STRIKE=", avgoiPCR9, " (pcr5..pcr13 = ATM +/-4, 9 strikes)")
            print("SUPP_RES =", SUPP_RES)
            print("spotLTP =", spotLTP)
            print("FUT LTP =", FUT_LTP)
            print("CEchoi  PechOi =", suppResCeChOi, "  ", suppResPeChOi)
            print("====================================")
            # print("totalOIPCR =", totalOIPCR)  # log noise — commented
            # print("AVG_VOLPCR=", avgvolPCR)
            print("atmPCR=", pcr5, " belowATM ", pcr6)
            # print("atmVolPCR=", volpcr5, " belowATM ", volpcr6)
            

            # === Direction signal — 3-tier priority system ===
            # Each logic checks FULL criteria: OI/CHOI direction + FUT vs S/R + PCR trend.
            # Cascade stops at first logic that produces a tradeable signal.
            # Logic 1: Morning trap (9:15-9:48 only)
            # Logic 2: Trend convergence (anytime) — CHOI + Total OI both same direction by 10%
            # Logic 3: CHOI trap (anytime) — CHOI dominance + 15% diff

            # _choi_pct is computed whenever _choi_valid — i.e. whenever AT LEAST
            # ONE side is building (see the _choi_valid block above). Only when
            # BOTH sides are unwinding/flat is it forced to 0, so all Logic 1/2/3
            # CHOI-diff gates (>=10%, >=15%) fail closed on a no-conviction cycle.
            # On a MIXED-SIGN cycle the figure is deliberately large (opposite
            # signs widen the gap) — that magnitude is the divergence strength.
            if _choi_valid:
                _larger_ch = max(abs(suppResCeChOi), abs(suppResPeChOi)) if max(abs(suppResCeChOi), abs(suppResPeChOi)) > 0 else 1
                _choi_pct = round(abs(suppResCeChOi - suppResPeChOi) / _larger_ch * 100, 1)
            else:
                _choi_pct = 0
            _larger_oi = max(suppResCeOi_total, suppResPeOi_total) if max(suppResCeOi_total, suppResPeOi_total) > 0 else 1
            _oi_pct = round(abs(suppResCeOi_total - suppResPeOi_total) / _larger_oi * 100, 1)

            # Mandatory direction gates (price-vs-S/R + PCR trend). anchorLTP (spot by default)
            # must be compared against SUPP_RES which is derived from the SAME anchor. The
            # _in_trade_zone guard MUST come first: when SUPP_RES == "NOTRADEZONE" (dead band),
            # it short-circuits before the numeric comparison (str vs int would raise) and blocks
            # both directions — price is in the skipped middle of the band.
            _in_trade_zone = (SUPP_RES != "NOTRADEZONE")
            _bull_mandatory = _in_trade_zone and (anchorLTP > SUPP_RES) and IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2
            _bear_mandatory = _in_trade_zone and (anchorLTP < SUPP_RES) and IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2

            # Logic 1: Morning window trap (Total OI direction + CHOI trapped writers by 15%)
            logic1_bull = IS_MORNING_WINDOW and _bull_mandatory and (suppResCeOi_total > suppResPeOi_total) and (suppResPeChOi > suppResCeChOi and _choi_pct >= 15)
            logic1_bear = IS_MORNING_WINDOW and _bear_mandatory and (suppResPeOi_total > suppResCeOi_total) and (suppResCeChOi > suppResPeChOi and _choi_pct >= 15)

            # Logic 2: Trend convergence — CHOI same direction by 10%, Total OI by 2%
            # Bull: fut>S/R, PCR up, PEchoi>CEchoi by 10%, PE_total>CE_total by 2%
            # Bear: fut<S/R, PCR down, CEchoi>PEchoi by 10%, CE_total>PE_total by 2%
            logic2_bull = _bull_mandatory and (suppResPeChOi > suppResCeChOi and _choi_pct >= 10) and (suppResPeOi_total > suppResCeOi_total and _oi_pct >= 2)
            logic2_bear = _bear_mandatory and (suppResCeChOi > suppResPeChOi and _choi_pct >= 10) and (suppResCeOi_total > suppResPeOi_total and _oi_pct >= 2)

            # Logic 3: CHOI trap — CEchoi > PEchoi for bull, reverse for bear, + 15% diff
            logic3_bull = _bull_mandatory and (suppResCeChOi > suppResPeChOi) and _choi_pct >= 15
            logic3_bear = _bear_mandatory and (suppResCeChOi < suppResPeChOi) and _choi_pct >= 15

            # Priority: first logic with a tradeable signal wins
            if logic1_bull or logic1_bear:
                bull_direction_ok = logic1_bull
                bear_direction_ok = logic1_bear
                choi_filter_bull = True
                choi_filter_bear = True
                _entry_mode = "LOGIC1_MORNING_TRAP"
            elif logic2_bull or logic2_bear:
                bull_direction_ok = logic2_bull
                bear_direction_ok = logic2_bear
                choi_filter_bull = True
                choi_filter_bear = True
                _entry_mode = "LOGIC2_TREND_CONVERGENCE"
            else:
                bull_direction_ok = logic3_bull
                bear_direction_ok = logic3_bear
                choi_filter_bull = True
                choi_filter_bear = True
                _entry_mode = "LOGIC3_CHOI_15PCT"

            # CHOI fresh-flow dominance confirmation (wired into entry via USE_CHOI_DOMINANCE_GATE).
            # BULL: at the moment PCR trends up, PE writers must be freshly DEFENDING the support
            # THIS cycle -> isPE_DominantAtSupp_once (fresh_PE > fresh_CE at a support) AND
            # fresh_PE NOT being unwound (>= 0: flat or added). The >= 0 check also excludes the
            # "both sides negative" case automatically: if PE is dominant yet fresh_PE < 0 then
            # PE is unwinding too, so it's rejected. BEAR mirrors at the resistance (fresh_CE >= 0).
            if USE_CHOI_DOMINANCE_GATE:
                _dominance_ok_bull = isPE_DominantAtSupp_once and (_last_fresh_pe >= 0)
                _dominance_ok_bear = isCE_DominantAtRes_once and (_last_fresh_ce >= 0)
            else:
                _dominance_ok_bull = True
                _dominance_ok_bear = True
            print("CHOI_DOM_GATE: gate_on=", USE_CHOI_DOMINANCE_GATE,
                  " bull_ok=", _dominance_ok_bull, " bear_ok=", _dominance_ok_bear,
                  "| isPE_DomSupp_once=", isPE_DominantAtSupp_once, " fresh_PE=", _last_fresh_pe,
                  "| isCE_DomRes_once=", isCE_DominantAtRes_once, " fresh_CE=", _last_fresh_ce)

            # === BULL ENTRY ===
            if bull_direction_ok and _dominance_ok_bull and slCount != 1 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and choi_filter_bull and anchorLTP > SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2:
                print("In Bull trade, slCount = ", slCount, " (mode=", _entry_mode, ")")

                # LOGIC3 = observation only (log signal, no real trade). LOGIC1/LOGIC2 trade live.
                if _entry_mode == "LOGIC3_CHOI_15PCT":
                    print("=" * 60)
                    print(f"LOGIC3_OBSERVE: WOULD_HAVE_ENTERED BULL (no real order — LOGIC3 is observation-only)")
                    print(f"  FUT={FUT_LTP} SUPP_RES={SUPP_RES} CEchoi={suppResCeChOi} PEchoi={suppResPeChOi}")
                    print("=" * 60)
                    # No trade taken — mirror the "no trade yet" block so avgOiPcrList2 stays in
                    # sync: reset PCR flags and slide window by one (so next cycle re-checks with 3).
                    # Do NOT clear to [] (would need 3 cycles to rebuild) and do NOT leave untouched
                    # (list would grow past 3 and PCR detection would stop firing).
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    if len(avgOiPcrList2) == 3:
                        avgOiPcrList2 = avgOiPcrList2[1:]; avgOiPcr9List2 = avgOiPcr9List2[1:]
                    continue

                # Decide spread type at entry time (real-time premium)
                # This synthetic ATM strike is resolved ONCE here and passed straight into
                # takeEntry() -> takeEntryCredit/Debit, which use it directly (no recompute).
                # That's what guarantees qty/SL sizing matches the exact strike traded.
                intExpiry_tmp = getSensexWeeklyExpiry()
                bn_ltp_tmp = helper.manualLTP(helper.getIndexSpot(stock), fyers)
                spot_strike_tmp = int(round((bn_ltp_tmp / 100), 0) * 100)
                spot_atmPE_tmp = getOptionFormatSensex(intExpiry_tmp, spot_strike_tmp, "PE")
                spot_atmCE_tmp = getOptionFormatSensex(intExpiry_tmp, spot_strike_tmp, "CE")
                spot_atm_pe_prem = helper.manualLTP(spot_atmPE_tmp, fyers)
                spot_atm_ce_prem = helper.manualLTP(spot_atmCE_tmp, fyers)
                synthetic_atm_strike_tmp = bn_ltp_tmp + spot_atm_ce_prem - spot_atm_pe_prem
                synthetic_atm_strike_tmp = int(round((synthetic_atm_strike_tmp / 100), 0) * 100)
                atmPE_tmp = getOptionFormatSensex(intExpiry_tmp, synthetic_atm_strike_tmp, "PE")
                atmCE_tmp = getOptionFormatSensex(intExpiry_tmp, synthetic_atm_strike_tmp, "CE")
                atm_pe_prem = helper.manualLTP(atmPE_tmp, fyers)
                atm_ce_prem = helper.manualLTP(atmCE_tmp, fyers)
                avg_atm_premium = (atm_pe_prem + atm_ce_prem) / 2.0
                print("SPREAD_CALC_AT_ENTRY: SyntheticATMStrike=", synthetic_atm_strike_tmp,
                      " ATM_PE=", atm_pe_prem, " ATM_CE=", atm_ce_prem,
                      " AvgATM=", round(avg_atm_premium, 2))
                # #3 diagnostic: log ATM CE/PE IV (avg + skew) for the credit/debit study —
                # NOT wired into choose_spread_type yet.
                log_atm_greeks(atmCE_tmp, atmPE_tmp, fyers)
                spread_type, spread_decision = choose_spread_type(iv_params, avg_atm_premium, fyers)
                print("SPREAD_DECISION_AT_ENTRY:", spread_decision)

                isBullTrade = True
                if not OBSERVATION_MODE:
                    st = 1
                # CREDIT places the main leg at the SPOT ATM; DEBIT at the synthetic-futures ATM.
                entry_main_strike = spot_strike_tmp if spread_type == "CREDIT" else synthetic_atm_strike_tmp
                print("ENTRY_MAIN_STRIKE:", entry_main_strike, "(", spread_type,
                      "->", "spotATM" if spread_type == "CREDIT" else "synthATM", ")")
                takeEntry(isBullTrade, False, entry_main_strike, intExpiry_tmp, fyers, papertrading)

                # Entry safety: if a leg was rejected, entry_ok is False and any orphaned leg
                # was already squared off inside takeEntry. Reset state and skip monitoring so
                # we don't manage a phantom/naked position (see 2026-07-16 margin-shortfall).
                if not OBSERVATION_MODE and not entry_ok:
                    print("ENTRY_ABORT: bull entry failed — no position held, resetting state")
                    st = 0
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    continue

                if OBSERVATION_MODE:
                    # Reset signal flags so next 3-min cycle can detect fresh signal
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    print("OBSERVATION_MODE: skipping post-entry SL/target setup")
                    continue  # skip rest of bull entry, wait for next 3-min candle

                print("after entry tradeATMOption =", tradeATMOption)

                # Position is already FILLED here. Resolve the SL/Target anchor via a fallback
                # chain that cannot raise; if no price can be obtained at all we square off
                # rather than hold a position we cannot monitor.
                anchorPremium, anchorSrc = get_entry_anchor_premium(tradeATMOption, fyers)
                if anchorPremium is None:
                    print("ANCHOR_UNAVAILABLE: cannot establish SL/Target for a LIVE position —"
                          " squaring off immediately to avoid an unmonitored trade.")
                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                    st = 0
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    continue

                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)

                # BULL: SL = median x SL_MULT_BULL (2.2), Target = median x
                # TARGET_MULT_BULL (4.5). Median is the HIGH-LOW
                # range, already sanity-checked by the low-sample guard. Reuse
                # tradeOptRange — computed ONCE inside takeEntryCredit/Debit on the exact traded
                # strike, right before qty/margin sizing, so SL/Target and qty sizing agree.
                opt_range = tradeOptRange
                if opt_range is not None and opt_range > 0:
                    effective_sl = round(opt_range * SL_MULT_BULL)
                    effective_tgt = round(opt_range * TARGET_MULT_BULL)
                    sl_source = f"OPTION_RANGE(median={opt_range},SLx{SL_MULT_BULL},Tgtx{TARGET_MULT_BULL})"
                else:
                    # Edge case: no candle data at all - use IV as absolute last resort
                    effective_sl = dynamic_sl_pt
                    effective_tgt = dynamic_sl_pt
                    sl_source = f"IV_FORMULA_LASTRESORT({dynamic_sl_pt})"
                    print("WARNING: opt_range returned None - using IV fallback. This should rarely happen.")

                # For DEBIT spread: premium rising = profit, premium falling = loss
                if spread_decision.get("type") == "DEBIT":
                    sl = anchorPremium - effective_sl  # premium drops = loss for buyer
                    target = anchorPremium + effective_tgt  # premium rises = profit for buyer
                else:
                    # Credit: premium rising = loss, premium falling = profit
                    sl = anchorPremium + effective_sl
                    target = anchorPremium - effective_tgt

                entryPremium = anchorPremium
                slTrailed = False
                pyramided = False
                slConfirmCount = 0
                trailTriggerPts = round(effective_tgt * TRAIL_TRIGGER_TARGET_FRACTION)
                print("ENTRY_SL_TGT: spread=", spread_decision.get("type"),
                      " SL=", sl, " Target=", target,
                      " EntryPrem=", entryPremium,
                      " AnchorSrc=", anchorSrc,
                      " TrailTrigger=", trailTriggerPts,
                      " SL_Source=", sl_source,
                      " (iv_pts=", dynamic_sl_pt,
                      " effective_sl=", effective_sl, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False

                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []; avgOiPcr9List2 = []

            # === BEAR ENTRY ===
            elif bear_direction_ok and _dominance_ok_bear and slCount != 1 and dt1.hour <= 15 and SUPP_RES != "NOTRADEZONE" and st == 0 and choi_filter_bear and anchorLTP < SUPP_RES and IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2:
                print("In Bear trade, slCount= ", slCount, " (mode=", _entry_mode, ")")

                # LOGIC3 = observation only (log signal, no real trade). LOGIC1/LOGIC2 trade live.
                if _entry_mode == "LOGIC3_CHOI_15PCT":
                    print("=" * 60)
                    print(f"LOGIC3_OBSERVE: WOULD_HAVE_ENTERED BEAR (no real order — LOGIC3 is observation-only)")
                    print(f"  FUT={FUT_LTP} SUPP_RES={SUPP_RES} CEchoi={suppResCeChOi} PEchoi={suppResPeChOi}")
                    print("=" * 60)
                    # No trade taken — mirror the "no trade yet" block so avgOiPcrList2 stays in
                    # sync: reset PCR flags and slide window by one (so next cycle re-checks with 3).
                    # Do NOT clear to [] (would need 3 cycles to rebuild) and do NOT leave untouched
                    # (list would grow past 3 and PCR detection would stop firing).
                    IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    if len(avgOiPcrList2) == 3:
                        avgOiPcrList2 = avgOiPcrList2[1:]; avgOiPcr9List2 = avgOiPcr9List2[1:]
                    continue

                # Decide spread type at entry time (real-time premium)
                # This synthetic ATM strike is resolved ONCE here and passed straight into
                # takeEntry() -> takeEntryCredit/Debit, which use it directly (no recompute).
                # That's what guarantees qty/SL sizing matches the exact strike traded.
                intExpiry_tmp = getSensexWeeklyExpiry()
                bn_ltp_tmp = helper.manualLTP(helper.getIndexSpot(stock), fyers)
                spot_strike_tmp = int(round((bn_ltp_tmp / 100), 0) * 100)
                spot_atmPE_tmp = getOptionFormatSensex(intExpiry_tmp, spot_strike_tmp, "PE")
                spot_atmCE_tmp = getOptionFormatSensex(intExpiry_tmp, spot_strike_tmp, "CE")
                spot_atm_pe_prem = helper.manualLTP(spot_atmPE_tmp, fyers)
                spot_atm_ce_prem = helper.manualLTP(spot_atmCE_tmp, fyers)
                synthetic_atm_strike_tmp = bn_ltp_tmp + spot_atm_ce_prem - spot_atm_pe_prem
                synthetic_atm_strike_tmp = int(round((synthetic_atm_strike_tmp / 100), 0) * 100)
                atmPE_tmp = getOptionFormatSensex(intExpiry_tmp, synthetic_atm_strike_tmp, "PE")
                atmCE_tmp = getOptionFormatSensex(intExpiry_tmp, synthetic_atm_strike_tmp, "CE")
                atm_pe_prem = helper.manualLTP(atmPE_tmp, fyers)
                atm_ce_prem = helper.manualLTP(atmCE_tmp, fyers)
                avg_atm_premium = (atm_pe_prem + atm_ce_prem) / 2.0
                print("SPREAD_CALC_AT_ENTRY: SyntheticATMStrike=", synthetic_atm_strike_tmp,
                      " ATM_PE=", atm_pe_prem, " ATM_CE=", atm_ce_prem,
                      " AvgATM=", round(avg_atm_premium, 2))
                # #3 diagnostic: log ATM CE/PE IV (avg + skew) for the credit/debit study —
                # NOT wired into choose_spread_type yet.
                log_atm_greeks(atmCE_tmp, atmPE_tmp, fyers)
                spread_type, spread_decision = choose_spread_type(iv_params, avg_atm_premium, fyers)
                print("SPREAD_DECISION_AT_ENTRY:", spread_decision)

                isBearTrade = True
                if not OBSERVATION_MODE:
                    st = 2
                # CREDIT places the main leg at the SPOT ATM; DEBIT at the synthetic-futures ATM.
                entry_main_strike = spot_strike_tmp if spread_type == "CREDIT" else synthetic_atm_strike_tmp
                print("ENTRY_MAIN_STRIKE:", entry_main_strike, "(", spread_type,
                      "->", "spotATM" if spread_type == "CREDIT" else "synthATM", ")")
                takeEntry(False, isBearTrade, entry_main_strike, intExpiry_tmp, fyers, papertrading)

                # Entry safety: if a leg was rejected, entry_ok is False and any orphaned leg
                # was already squared off inside takeEntry. Reset state and skip monitoring so
                # we don't manage a phantom/naked position (see 2026-07-16 margin-shortfall).
                if not OBSERVATION_MODE and not entry_ok:
                    print("ENTRY_ABORT: bear entry failed — no position held, resetting state")
                    st = 0
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    continue

                if OBSERVATION_MODE:
                    # Reset signal flags so next 3-min cycle can detect fresh signal
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    print("OBSERVATION_MODE: skipping post-entry SL/target setup")
                    continue  # skip rest of bear entry, wait for next 3-min candle

                # Position is already FILLED here. Resolve the SL/Target anchor via a fallback
                # chain that cannot raise; if no price can be obtained at all we square off
                # rather than hold a position we cannot monitor.
                anchorPremium, anchorSrc = get_entry_anchor_premium(tradeATMOption, fyers)
                if anchorPremium is None:
                    print("ANCHOR_UNAVAILABLE: cannot establish SL/Target for a LIVE position —"
                          " squaring off immediately to avoid an unmonitored trade.")
                    oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                    st = 0
                    IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                    mapStrike.clear()
                    IS_ATM_STRIKE_SHIFT = False
                    atmStrikeNotShiftedCount = 1
                    avgOiPcrList2 = []; avgOiPcr9List2 = []
                    continue

                dynamic_sl_pt = iv_params.get("sl_point", sl_point)
                dynamic_tgt_pt = iv_params.get("target_point", target_point)

                # BEAR: SL = median x SL_MULT_BEAR (2.2), Target = median x TARGET_MULT_BEAR (4.5)
                # — same as bull. Median is HIGH-LOW range. Reuse tradeOptRange — computed ONCE
                # inside takeEntryCredit/Debit on the exact traded strike, right before sizing.
                opt_range = tradeOptRange
                if opt_range is not None and opt_range > 0:
                    effective_sl = round(opt_range * SL_MULT_BEAR)
                    effective_tgt = round(opt_range * TARGET_MULT_BEAR)
                    sl_source = f"OPTION_RANGE(median={opt_range},SLx{SL_MULT_BEAR},Tgtx{TARGET_MULT_BEAR})"
                else:
                    # Edge case: no candle data at all - use IV as absolute last resort
                    effective_sl = dynamic_sl_pt
                    effective_tgt = dynamic_sl_pt
                    sl_source = f"IV_FORMULA_LASTRESORT({dynamic_sl_pt})"
                    print("WARNING: opt_range returned None - using IV fallback. This should rarely happen.")

                # For DEBIT spread: premium rising = profit, premium falling = loss
                if spread_decision.get("type") == "DEBIT":
                    sl = anchorPremium - effective_sl
                    target = anchorPremium + effective_tgt
                else:
                    sl = anchorPremium + effective_sl
                    target = anchorPremium - effective_tgt

                entryPremium = anchorPremium
                slTrailed = False
                pyramided = False
                slConfirmCount = 0
                trailTriggerPts = round(effective_tgt * TRAIL_TRIGGER_TARGET_FRACTION)
                print("ENTRY_SL_TGT: spread=", spread_decision.get("type"),
                      " SL=", sl, " Target=", target,
                      " EntryPrem=", entryPremium,
                      " AnchorSrc=", anchorSrc,
                      " TrailTrigger=", trailTriggerPts,
                      " SL_Source=", sl_source,
                      " (iv_pts=", dynamic_sl_pt,
                      " effective_sl=", effective_sl, ")")

                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                mapStrike.clear()
                IS_ATM_STRIKE_SHIFT = False
                atmStrikeNotShiftedCount = 1
                avgOiPcrList2 = []; avgOiPcr9List2 = []

            elif len(avgOiPcrList2) == 3:
                print("no trade yet =", avgOiPcrList2)
                IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2 = False
                IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2 = False
                avgOiPcrList2 = avgOiPcrList2[1:]; avgOiPcr9List2 = avgOiPcr9List2[1:]

            # Commented out — function makes a redundant 2nd ochain call (strikecount=3) which
            # causes parallel API contention with BN strategy and crashes with KeyError.
            # Main loop ochain block above (strikecount=8) already provides all PCR/CHOI data
            # we need for actual trade entry decisions.
            # Diagnostic-only (doNotTrade=True) — must NEVER be allowed to crash live trading.
            # On 2026-08-03 an unhandled exception here (Fyers added a 7th candle field)
            # propagated up and killed the whole process for the rest of the day with no
            # position open at the time, but it could just as easily happen mid-trade.
            # DISABLED: checkCriteriaAndTakeTrade() is diagnostic-only (never places an order)
            # and makes a redundant 2nd option-chain call (strikecount=3) + heavy log output.
            # Commented out to cut the extra API call and log noise. order_id kept as None so
            # the downstream flow is unchanged. Re-enable by uncommenting if the scalp/oipcr
            # diagnostics are needed again.
            order_id = None
            # try:
            #     order_id = checkCriteriaAndTakeTrade()
            # except Exception as _cctt_err:
            #     print("CHECK_CRITERIA_DIAGNOSTIC_FAILED (non-fatal, diagnostic-only):", _cctt_err)
            #     order_id = None

            if tradeCEoption != "":
                optionInstum = tradeCEoption
            elif tradePEoption != "":
                optionInstum = tradePEoption

            time.sleep(1)

        elif candle_formed == 1:
            if st == 1 or st == 2:
                is_debit = spread_decision.get("type") == "DEBIT"
                # Reset per-trade LTP confirmation counters at the start of monitoring.
                ltpSlConfirm = 0
                ltpTgtConfirm = 0
                while y == 1:
                    dt2 = datetime.now()

                    # EOD: stop LTP monitoring; let the time-exit block below square off.
                    if dt2.hour >= 15 and dt2.minute >= 24:
                        break

                    # === REAL-TIME LTP SL/TARGET (primary) — poll every LTP_POLL_INTERVAL sec ===
                    # Fast detection: catches adverse moves in seconds instead of waiting for
                    # the 1-min candle to close. SL/target need SL_CONFIRM_TICKS consecutive
                    # breached polls to fire (fake-spike filter). manualLTP has its own retry;
                    # wrap in try/except so a transient quote failure just skips this cycle.
                    try:
                        ltp_now = helper.manualLTP(tradeATMOption, fyers)
                    except Exception as _e:
                        ltp_now = None
                        print("LTP_POLL_FAILED:", _e, "— skipping this cycle")

                    if ltp_now is not None and (st == 1 or st == 2):
                        if not is_debit:
                            # Credit: premium rising = loss (SL), falling = profit (target)
                            # Trailing is per-direction (st==1 bull, st==2 bear); both are OFF now,
                            # so trades keep their ORIGINAL SL and run to target.
                            if (TRAIL_SL_FOR_BULL if st == 1 else TRAIL_SL_FOR_BEAR) and not slTrailed and ltp_now <= (entryPremium - trailTriggerPts):
                                sl = entryPremium
                                slTrailed = True
                                print("TRAILING SL activated! SL moved to breakeven =", sl)
                                # PYRAMIDING: scale in (add up to 2x) now that the base lot is
                                # risk-free at breakeven. Combined position rides the SAME target
                                # with SL=breakeven for all. is_debit=False (credit).
                                if PYRAMIDING_ENABLED and not pyramided:
                                    _added = pyramid_add_position(tradeATMOption, tradeHedgeOption, False, qty)
                                    if _added > 0:
                                        qty = qty + _added
                                    pyramided = True
                            sl_breach = ltp_now >= sl
                            tgt_reached = ltp_now <= target
                        else:
                            # Debit: premium falling = loss (SL), rising = profit (target)
                            # Same per-direction trail gate as the credit path.
                            if (TRAIL_SL_FOR_BULL if st == 1 else TRAIL_SL_FOR_BEAR) and not slTrailed and ltp_now >= (entryPremium + trailTriggerPts):
                                sl = entryPremium
                                slTrailed = True
                                print("DEBIT TRAILING SL activated! SL moved to breakeven =", sl)
                                # PYRAMIDING: scale in (add up to 2x) now that the base lot is
                                # risk-free at breakeven. is_debit=True (debit spread).
                                if PYRAMIDING_ENABLED and not pyramided:
                                    _added = pyramid_add_position(tradeATMOption, tradeHedgeOption, True, qty)
                                    if _added > 0:
                                        qty = qty + _added
                                    pyramided = True
                            sl_breach = ltp_now <= sl
                            tgt_reached = ltp_now >= target

                        if sl_breach:
                            ltpSlConfirm += 1
                            print(f"LTP_SL_WATCH: ltp={ltp_now} SL={sl} confirm={ltpSlConfirm}/{SL_CONFIRM_TICKS}")
                            if ltpSlConfirm >= SL_CONFIRM_TICKS:
                                print('SL Hit (LTP)')
                                st = 0
                                # Only an UNTRAILED SL is a real loss and counts toward the daily
                                # halt (now 1-SL: the entry gate blocks once slCount reaches 1).
                                # A trailed exit (SL already at breakeven) is a scratch.
                                if not slTrailed:
                                    slCount += 1
                                    print('slCount =', slCount)
                                else:
                                    print('SL at breakeven (trailed) — scratch, not counted. slCount =', slCount)
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                        else:
                            ltpSlConfirm = 0

                        if tgt_reached:
                            ltpTgtConfirm += 1
                            print(f"LTP_TGT_WATCH: ltp={ltp_now} target={target} confirm={ltpTgtConfirm}/{TGT_CONFIRM_TICKS}")
                            if ltpTgtConfirm >= TGT_CONFIRM_TICKS:
                                print('Target hit (LTP)')
                                st = -1
                                targetCount += 1
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                        else:
                            ltpTgtConfirm = 0

                        # Routine heartbeat throttled to ~once/min (near top of minute) so the
                        # 2s poll doesn't flood the log — breach lines above still log every poll.
                        if not sl_breach and not tgt_reached and dt2.second < LTP_POLL_INTERVAL:
                            print(dt2.strftime("%H:%M:%S"), "In Trade (", spread_decision.get("type"), "). No Exit. ltp=", ltp_now,
                                  " SL=", sl, " target=", target)

                    elif ltp_now is None and (st == 1 or st == 2):
                        # === FALLBACK: LTP unavailable → use last completed 1-min candle close ===
                        # Only runs when the live quote failed (rate-limit / outage). getHistorical
                        # hits a different endpoint than quotes, so it may still succeed. Exits
                        # immediately on breach (no confirmation) since we're already degraded —
                        # guarantees we're never blind to the SL for more than ~1 candle.
                        try:
                            _fb = helper.getHistorical(tradeATMOption, 1, 3, fyers)
                            _fb_close = float(_fb['close'].to_numpy()[-1])
                        except Exception as _e2:
                            _fb_close = None
                            print("CANDLE_FALLBACK_FAILED:", _e2)
                        if _fb_close is not None:
                            if not is_debit:
                                fb_sl = _fb_close >= sl
                                fb_tgt = _fb_close <= target
                            else:
                                fb_sl = _fb_close <= sl
                                fb_tgt = _fb_close >= target
                            if fb_sl:
                                print("SL Hit (CANDLE FALLBACK) close=", _fb_close, " SL=", sl)
                                st = 0
                                # Same rule as the LTP path: untrailed SL counts, trailed
                                # (breakeven) exit is a scratch and is not counted.
                                if not slTrailed:
                                    slCount += 1
                                    print('slCount =', slCount)
                                else:
                                    print('SL at breakeven (trailed) — scratch, not counted. slCount =', slCount)
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                            elif fb_tgt:
                                print("Target hit (CANDLE FALLBACK) close=", _fb_close, " target=", target)
                                st = -1
                                targetCount += 1
                                oidexit = exitSpreadPosition(tradeATMOption, tradeHedgeOption)
                                break
                            else:
                                print("LTP_FALLBACK: quotes down, using candle close=", _fb_close,
                                      " SL=", sl, " target=", target)

                    time.sleep(LTP_POLL_INTERVAL)

            # TIME EXIT 3.24 pm
            # Refresh dt1 — the LTP monitor loop above runs continuously (no longer breaks
            # every minute like the old candle loop), so dt1 from the outer loop top is stale
            # after being in a trade. Re-read now so EOD square-off actually triggers.
            dt1 = datetime.now()
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
