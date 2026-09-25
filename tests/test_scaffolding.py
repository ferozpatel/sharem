"""
Scaffolding smoke tests for the Delta Exchange trading bot subsystem (Task 1).

These verify that the module stubs import cleanly and that the module-level
configuration constants required by this setup task are present and correct:
  * production / testnet hosts (Requirements 2.5, 2.6)
  * Reference_Timezone = UTC (Requirement 10.1)
  * default AWS region and Credential_Store file paths (design Data Models)
"""

from datetime import timezone

import delta_config


def test_hosts_are_configured():
    # Requirements 2.5, 2.6
    assert delta_config.PROD_BASE_URL == "https://api.india.delta.exchange"
    assert delta_config.TESTNET_BASE_URL == "https://cdn-ind.testnet.deltaex.org"


def test_reference_timezone_is_utc():
    # Requirement 10.1
    assert delta_config.REFERENCE_TIMEZONE == timezone.utc
    assert delta_config.REFERENCE_TIMEZONE_NAME == "UTC"


def test_default_aws_region_is_tokyo():
    # design.md — AWS EC2 Deployment (12.1)
    assert delta_config.DEFAULT_AWS_REGION == "ap-northeast-1"


def test_credential_store_paths():
    # design.md — Data Models / Credential_Store
    assert delta_config.API_KEY_FILE == "delta_api_key.txt"
    assert delta_config.API_SECRET_FILE == "delta_api_secret.txt"
    assert delta_config.SESSION_STORE_FILE == "delta_session.json"


def test_data_feed_defaults():
    # Requirement 3.5
    assert delta_config.DATA_FEED_HOST == "127.0.0.1"
    assert delta_config.DATA_FEED_PORT == 4001


def test_bot_module_stubs_import():
    # All module stubs must be syntactically valid and importable.
    import delta_auth  # noqa: F401
    import delta_client  # noqa: F401
    import delta_data_feed  # noqa: F401
    import helper_delta  # noqa: F401
    import Strategy_BTC_Options  # noqa: F401
    import trading_scheduler_delta  # noqa: F401
