"""
delta_config.py — Shared module-level configuration constants for the Delta
Exchange India trading bot subsystem.

This is the single source of truth for host URLs, AWS region, reference
timezone, and Credential_Store file paths used by delta_auth.py,
delta_client.py, delta_data_feed.py, helper_delta.py, Strategy_BTC_Options.py,
and trading_scheduler_delta.py. It mirrors the flat, standalone-module layout of
the existing Fyers/Sensex bot (helper_fyers.py, trading_scheduler.py) but keeps
configuration centralized so every Delta component reads the same values.

References: design.md (Overview, delta_client.py, AWS EC2 Deployment,
Credential_Store) and requirements 2.5, 2.6, 10.1.
"""

from datetime import timezone

# ============================================================
# DELTA EXCHANGE HOSTS (Requirements 2.5, 2.6)
# ============================================================
# Delta Exchange India production REST host. India API keys work ONLY against
# this host; the Delta Global host (api.delta.exchange) is not usable.
PROD_BASE_URL = "https://api.india.delta.exchange"

# Delta Exchange India testnet/demo REST host, used when testnet mode is
# configured (DELTA_TESTNET=1).
TESTNET_BASE_URL = "https://cdn-ind.testnet.deltaex.org"

# A non-empty User-Agent header is mandatory on every authenticated request;
# a missing User-Agent triggers a CDN "Forbidden" rejection.
DEFAULT_USER_AGENT = "python-delta-bot"

# requests connect/read timeout tuple, mirroring the Delta docs sample client.
DEFAULT_TIMEOUT = (3.0, 27.0)

# The signature must reach Delta within this many seconds of its timestamp.
SIGNATURE_VALIDITY_SECONDS = 5

# ============================================================
# AWS DEPLOYMENT (Requirement 12.1)
# ============================================================
# Delta's data centers are in AWS Tokyo, so default to ap-northeast-1 for the
# lowest latency (the Fyers scheduler defaulted to ap-south-1).
DEFAULT_AWS_REGION = "ap-northeast-1"

# ============================================================
# REFERENCE TIMEZONE (Requirement 10.1)
# ============================================================
# All rollover, logging, and scheduling decisions use UTC because every Delta
# Exchange API timestamp is expressed in UTC.
REFERENCE_TIMEZONE_NAME = "UTC"
REFERENCE_TIMEZONE = timezone.utc

# ============================================================
# CREDENTIAL_STORE FILE PATHS (design.md — Data Models)
# ============================================================
# API credential files (chmod 600) and the validated-session marker written by
# delta_auth.py and read by delta_client.py.
API_KEY_FILE = "delta_api_key.txt"
API_SECRET_FILE = "delta_api_secret.txt"
SESSION_STORE_FILE = "delta_session.json"

# Named environment variables that may supply credentials instead of files.
ENV_API_KEY = "DELTA_API_KEY"
ENV_API_SECRET = "DELTA_API_SECRET"

# Environment flag selecting the testnet host when set to "1".
ENV_TESTNET = "DELTA_TESTNET"

# ============================================================
# LOCAL DATA FEED (Requirement 3.5)
# ============================================================
# delta_data_feed.py serves the live LTP on this host/port by default.
DATA_FEED_HOST = "127.0.0.1"
DATA_FEED_PORT = 4001

# Price freshness window (seconds); prices older than this are treated as
# unavailable rather than served stale (Requirement 3.6).
PRICE_FRESHNESS_SECONDS = 5
