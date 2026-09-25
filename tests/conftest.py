"""
Shared pytest configuration for the Delta Exchange trading bot tests.

Ensures the bot modules (delta_config, delta_auth, delta_client,
delta_data_feed, helper_delta, Strategy_BTC_Options, trading_scheduler_delta)
are importable from the tests directory by placing the parent bot directory on
sys.path. All REST/WebSocket transport is mocked in the tests so no network
call occurs.
"""

import os
import sys

# Put the bot directory (parent of tests/) on the import path.
BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)
