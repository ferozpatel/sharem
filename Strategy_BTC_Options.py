"""
Strategy_BTC_Options.py — BTC daily-options credit-spread strategy (Requirement 9).

Parallel to the Fyers bot's Strategy_Sensex_May_2026.py. Mirrors the Sensex
options credit-spread structure (PCR bias, S/R band, no-trade zone,
consecutive-confirm monitoring, trailing) adapted to BTC daily options, and runs
in observation mode by default.

NOTE: This is a scaffolding stub. The startup/signal/entry/monitoring/expiry
logic is implemented across Task 14.
"""

from delta_config import REFERENCE_TIMEZONE

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


def run():
    """
    Strategy entrypoint: establish the client, initialize config, then run the
    observation/signal/entry/monitoring/expiry loop. (Task 14)
    """
    raise NotImplementedError("Implemented in Task 14")


if __name__ == "__main__":
    run()
