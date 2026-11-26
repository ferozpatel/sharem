# Sensex Options Strategy — Logic Guide

> Companion to `Strategy_Sensex_May_2026.py`. Explains the full decision path: data,
> entry, sizing, exits, and every toggle. Update this doc whenever the logic changes.
> Values below reflect the current config (Nov 2026).

---

## 1. Big Picture

Intraday **credit spread** strategy on BSE SENSEX weekly options.

- Sell 1 ATM option, buy 1 far-OTM option as a hedge (margin benefit + loss cap).
- Direction is decided from **Open Interest change (CHOI)**, **PCR trend**, and price vs a
  500-point **support/resistance (S/R) band**.
- **One real trade per day** — the day ends on the first SL or target (smart exits are the
  exception; see §7).
- Runs on a 3-minute decision cycle; once in a trade it monitors the short-leg LTP every 2 seconds.

Core thesis: price travels toward the strike with the largest OI build-up (the opposing S/R edge)
and tends to **reverse** there. Entries are taken near one edge; exits try to bank before the
reversal.

---

## 2. Market Data & Anchors

- **Anchor** = spot index by default (`USE_SPOT_ANCHOR`). S/R, PCR window, and breakout checks
  all key off the same anchor.
- **S/R band** (`get_support_resistance`, step 500):
  - `support = (spot // 500) * 500`, `resistance = support + 500`, `midpoint` between them.
  - **NO-TRADE ZONE** (`USE_NO_TRADE_ZONE`, buffer `NO_TRADE_ZONE_BUFFER=20`): the middle of the
    band (midpoint ± 20) is skipped. Price in the **lower** part → `SUPP_RES = support` (bull side);
    **upper** part → `SUPP_RES = resistance` (bear side); middle → `"NOTRADEZONE"` (no trade).
  - So a **bull** enters near support, a **bear** near resistance — meaning the opposing target
    edge is always ≥ ~250 points away.

---

## 3. The Three Entry Logics (modes)

The strategy evaluates one of three "modes" per cycle (logged as `mode=`):

- **LOGIC1 / LOGIC2 (LOGIC2_TREND_CONVERGENCE)** — real, live trades. PCR trend + CHOI + S/R all
  converge. This is the main edge.
- **LOGIC3 (LOGIC3_CHOI_15PCT)** — **observation only**. Logs `LOGIC3_OBSERVE: WOULD_HAVE_ENTERED`
  but places **no order**. Used to study a looser CHOI-based signal without risking capital.

---

## 4. Entry Gate (what must be true to take a trade)

A **BULL** entry (sell PE) requires ALL of:
- `bull_direction_ok` (mode's directional signal)
- `_dominance_ok_bull` = **PE writers dominant at support this cycle** (`isPE_DominantAtSupp_once`)
  when `USE_CHOI_DOMINANCE_GATE=True`. Dominance-only: both-sides-unwinding is accepted if the
  correct side dominates.
- `slCount != 1` (no SL yet today)
- `st == 0` (flat — not already in a trade / not day-halted)
- `SUPP_RES != "NOTRADEZONE"` and `anchorLTP > SUPP_RES` (price above support)
- `choi_filter_bull`
- `IS_CONSECUTIVELY_2TIMES_PCR_INCREASED2` (PCR trending up over consecutive cycles)
- time ≤ 15:00

A **BEAR** entry (sell CE) is the mirror: CE dominant at resistance, `anchorLTP < SUPP_RES`,
`IS_CONSECUTIVELY_2TIMES_PCR_DECREASED2`.

On entry the spread type is chosen (`ALWAYS_CREDIT=True` → always CREDIT), the synthetic-ATM strike
is resolved once, and `takeEntry → takeEntryCredit` places the two legs. Hedge is delta-selected
(`select_hedge_by_delta`, target ≈ half the main leg's |delta|, snapped to a 500 strike far OTM).

---

## 5. Position Sizing & the Median

Everything keys off one number: the **option candle median** (`get_option_candle_range`).

- Pull today's 3-min candles, **skip the first candle** (opening spike) **and the current
  in-progress candle**. Take HIGH−LOW range of each.
- **Sizing median = median of the LAST 5 completed candle ranges** (`SIZING_MEDIAN_CANDLES=5`).
- The 15-candle and 20-candle medians are **logged only** (`median15`, `median20`) for comparison.
- **Low-sample halving is DISABLED** (`LOW_SAMPLE_MEDIAN_HALVE_ENABLED=False`) — raw median is used
  even on thin windows. (Guard + logging remain; just the halving action is off.)
- **Target-reachability cap** (`apply_target_reachability_cap`, CREDIT only): if the median is so
  large the target would go to zero/negative, cap it to `premium × MAX_TGT_PREMIUM_FRACTION(0.75) /
  TARGET_MULT`. Logged as `MEDIAN_CAPPED`.
- **S/R structural median** (`apply_sr_structural_median`): **LOG-ONLY** right now
  (`SR_STRUCTURAL_MEDIAN_APPLY=False`). It computes what median would make the target land exactly
  at the opposing S/R edge and logs `SR_STRUCT_MEDIAN`. See §9.

Lots (`calc_lots_by_risk`): sized so an SL loses ≈ `FIXED_RISK_PER_TRADE` **net of hedge**.
- `net_sl_points = effective_sl × (1 − hedge_offset_ratio)`, hedge offset from the delta ratio
  (`|hedge_delta| / |main_delta|`), fallback to range/premium ratio.
- `lots = FIXED_RISK_PER_TRADE / (net_sl_points × LOT_SIZE)`, round up if frac ≥
  `LOT_ROUNDUP_FRACTION (0.75)`, then capped by live margin (`apply_margin_cap`).
- `FIXED_RISK_PER_TRADE = 7000` in the repo (note: was run at 5000 manually on EC2).

---

## 6. SL & Target (base)

Both derive from the **same sizing median**, so they always agree.

- `effective_sl  = median × SL_MULT  (2.0)`
- `effective_tgt = median × TARGET_MULT (4.25)` → base R:R ≈ **1 : 2.1**
- CREDIT: `SL = entry + effective_sl` (upside), `Target = entry − effective_tgt` (downside).
- Monitoring: short-leg LTP polled every `LTP_POLL_INTERVAL=2`s; needs `SL_CONFIRM_TICKS=2` /
  `TGT_CONFIRM_TICKS=2` consecutive breached polls to fire (filters fake spikes). Logged as
  `LTP_SL_WATCH` / `LTP_TGT_WATCH`.
- Fixed breakeven trailing is **off** (`TRAIL_SL_FOR_BULL/BEAR=False`) — the ladder replaces it.

---

## 7. Exits (all paths, first to fire wins)

1. **Point SL** — short-leg LTP hits `SL` for 2 polls → loss. `slCount=1`, `st=0` → day ends
   (gate blocks on `slCount != 1`). Logged `SL Hit (LTP)`.
2. **Laddered target / trailing SL** (`LADDER_TRAIL_ENABLED=True`):
   - Rungs `LADDER_TARGET_MULTS = [4.25, 4.5, … 8.0]` (0.25 step), `LADDER_LOCK_GAP=0.25`.
   - When premium reaches rung **T**, DON'T exit — ratchet SL to lock profit at **(T − 0.25)** and
     move target to the next rung. Snaps straight to the **highest rung reached** on a fast spike.
   - Once any rung is hit the SL is in profit; a later SL breach = **booked win** (`st=-1`,
     `targetCount++`, slCount untouched). Logged `LADDER_TRAIL` / `LADDER_EXIT`.
   - Rungs only go as high as the premium stays positive (`_ladder_max_usable_idx`).
3. **Smart target exit** (`SMART_TARGET_EXIT_ENABLED=True`): locks the entry SUPP_RES strike; each
   3-min cycle, if the **opposite PCR** is live AND **both** the strike's CHOI-gap% and total-OI-gap%
   **shrink** vs the previous cycle → exit. Logged `SMART_EXIT_WATCH` / `SMART_TGT_EXIT`.
4. **Reversal smart exit** (`SMART_EXIT_ON_REVERSAL=True`): if the **opposite direction's full entry
   criteria** are met this cycle (the `REVERSAL_SIGNAL` condition) → exit before the reversal plays
   out. Logged `REVERSAL_SIGNAL` → `REVERSAL_SMART_EXIT`.
5. **EOD square-off** — held to ~15:24 if nothing else fired.
6. **Net-rupee hard stop** (`RUPEE_NET_SL_ENABLED=False`, **DISABLED**) — legacy combined-P&L stop.

### Smart-exit accounting (3 & 4)
- A smart exit is **NOT** an SL and **NOT** a target — it increments `smartExitCount` only.
- If `smartExitCount < SMART_EXIT_MAX_PER_DAY (2)` → `st=0`, **re-arm** for another trade.
- At `smartExitCount >= 2` → `st=-1`, day ends.
- Both smart exits only run **before the first ladder rung locks** (`not ladderActive`); once the
  SL is trailing in profit the ladder manages the exit.

### One-trade-per-day summary
- Real **SL** → day ends. **Target / ladder win** → day ends. **Smart exit** → re-arm (up to 2),
  then day ends. Logged at EOD as `DAILY_SUMMARY: ... SLCount= TargetCount=`.

---

## 8. In-Trade Diagnostics

- `IN_TRADE_3MIN_LOG=True`: at each 3-min boundary while in a trade, the monitor yields to print
  the full diagnostic block (option table, CHOI, VOL_PRESSURE, SR watches), then resumes 2s polling.
  Costs a ~2–3s monitoring gap at the boundary (only when no exit confirm is in progress).
- `LOG_VOL_PRESSURE=True`: per-cycle change-in-volume PCR and change-in-OI PCR at ATM±1 and the
  SUPP_RES strike (`chInVol pcr`, `ChInOi pcr`, `INCR_CHOI`, `INCR_VOLPCR`, `INCR_CHOI_ATM`).

---

## 9. S/R Structural Median (log-only study)

Idea: set the target to land at the opposing S/R edge (where price reverses), so it's reachable.

```
BULL: target_level = SUPP_RES + 500 − BUFFER(15);  distance = target_level − anchor
BEAR: target_level = SUPP_RES − 500 + BUFFER(15);  distance = anchor − target_level
expected_drop     = |main_delta| × distance
structural_median = expected_drop / TARGET_MULT
candidate         = min(5-candle median, structural_median)
```

- **LIVE** (`SR_STRUCTURAL_MEDIAN_APPLY=True`) — sizing uses `min(5-candle median, structural median)`.
- Backtest (Sep–Nov) showed it rescues unreachable/too-far targets on wide-median days without
  flipping any clean winner to a loss (one caveat: 11-09, a volatile EOD-held day, would stop early).
- To make it live: set `SR_STRUCTURAL_MEDIAN_APPLY=True`.

---

## 10. Config Quick Reference (current)

| Flag | Value | Meaning |
|------|-------|---------|
| `OBSERVATION_MODE` | False | live trading (True = paper) |
| `ALWAYS_CREDIT` | True | always credit spread |
| `SL_MULT_BULL/BEAR` | 2.0 | SL = median × 2.0 |
| `TARGET_MULT_BULL/BEAR` | 4.25 | first target = median × 4.25 |
| `SIZING_MEDIAN_CANDLES` | 5 | sizing uses last-5-candle median |
| `LOW_SAMPLE_MEDIAN_HALVE_ENABLED` | False | halving off |
| `LADDER_TRAIL_ENABLED` | True | laddered trailing target/SL |
| `LADDER_TARGET_MULTS` | 4.25 … 8.0 | rungs, 0.25 step |
| `LADDER_LOCK_GAP` | 0.25 | SL locks one rung behind |
| `SMART_TARGET_EXIT_ENABLED` | True | gaps-shrinking smart exit |
| `SMART_EXIT_ON_REVERSAL` | True | reversal-signal smart exit |
| `SMART_EXIT_MAX_PER_DAY` | 2 | smart exits before day ends |
| `SR_STRUCTURAL_MEDIAN_ENABLED` | True | compute + log structural median |
| `SR_STRUCTURAL_MEDIAN_APPLY` | True | LIVE — sizing uses min(5-candle, structural) |
| `SR_TARGET_BUFFER` / `SR_BAND_WIDTH` | 15 / 500 | structural buffer / band |
| `RUPEE_NET_SL_ENABLED` | False | net-rupee stop disabled |
| `FIXED_RISK_PER_TRADE` | 7000 | ₹ net risk per trade |
| `MAX_TGT_PREMIUM_FRACTION` | 0.75 | target-reachability cap |
| `USE_NO_TRADE_ZONE` / `NO_TRADE_ZONE_BUFFER` | True / 20 | dead band mid-S/R |
| `LTP_POLL_INTERVAL` | 2s | in-trade poll cadence |
| `SL_CONFIRM_TICKS` / `TGT_CONFIRM_TICKS` | 2 / 2 | breach confirmation polls |

---

## 11. Log Line Glossary

| Log tag | What it tells you |
|---------|-------------------|
| `In Bull/Bear trade, slCount= ... (mode=...)` | entry fired, which logic |
| `SPREAD_CALC_AT_ENTRY` / `SPREAD_DECISION_AT_ENTRY` | ATM strike + credit/debit choice |
| `DELTA_CHECK` / `HEDGE_BY_DELTA` / `HEDGE_OFFSET` | leg deltas + hedge selection + offset ratio |
| `OPT_RANGE_DEBUG` | candle ranges + `median_sizing5` + `median15` |
| `OPT_MEDIAN20_LOG` | median20 / median15 / sizing median5 |
| `MEDIAN_CAPPED` | target-reachability cap fired |
| `SR_STRUCT_MEDIAN` | structural median (log-only) + `APPLY=` flag |
| `RISK_SIZING` / `LOT_ROUNDING` / `MARGIN_CHECK` | qty sizing path |
| `ENTRY_SL_TGT` | final SL / Target / EntryPrem / median / mults used |
| `LADDER_TRAIL armed` / `LADDER_TRAIL` / `LADDER_EXIT` | ladder arm, rung hits, booked win |
| `LTP_SL_WATCH` / `LTP_TGT_WATCH` | 2-poll SL/target confirmation |
| `SL Hit (LTP)` / `Target hit (LTP)` | base exits |
| `SMART_EXIT_WATCH` / `SMART_TGT_EXIT` | gaps-shrinking smart exit |
| `REVERSAL_SIGNAL` / `REVERSAL_SMART_EXIT` | reversal detected / reversal exit |
| `SMART_EXIT_COUNT=` | smart-exit tally + re-arm vs day-end |
| `DAILY_SUMMARY` | end-of-day SLCount / TargetCount |

---

## 12. Change Log (recent, Nov 2026)
- Disabled low-sample halving (`f843627`).
- Sizing switched to 5-candle median; 15/20 logged (`6701e3d`).
- Risk 7k; smart exit only until first ladder lock (`dda8605`).
- Ladder starts at 4.25× (SL locks 4.0×); TARGET_MULT 4.5→4.25 (`6079789`).
- Smart exit no longer counts as SL/target — `smartExitCount`, 2/day (`982213d`).
- S/R structural median added (`689f1b4`), then set **log-only** (`3e44133`).
- Reversal smart exit added (`33b8db9`).
