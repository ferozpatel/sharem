"""
delta_auth.py — Delta Exchange authentication (Requirement 1).

Parallel to the Fyers bot's autologin.py. Reads the API key/secret from
configurable credential files or environment variables, validates them with a
signed request via DeltaSigner, persists a validated session marker to the
Credential_Store, and exits with a status the scheduler can gate on.

NOTE: This is a scaffolding stub. Credential loading/guards are implemented in
Task 3.1; validation, persistence, and exit codes in Task 3.2.
"""

import json
import os
import sys
import time

import requests

from delta_config import (
    API_KEY_FILE,
    API_SECRET_FILE,
    SESSION_STORE_FILE,
    ENV_API_KEY,
    ENV_API_SECRET,
    PROD_BASE_URL,
    TESTNET_BASE_URL,
    DEFAULT_USER_AGENT,
)
from delta_client import (
    DeltaSigner,
    DeltaAPIError,
    DeltaClockDriftError,
    DeltaIPNotWhitelistedError,
    DeltaUserAgentRequiredError,
    _env_testnet,
)


# ============================================================
# TYPED ERRORS (Requirement 1.2)
# ============================================================
class MissingCredentialError(Exception):
    """
    Raised by the missing-credential guard when the API key or secret is
    absent/empty in the configured credential source (Requirement 1.2).

    Terminates credential loading BEFORE any network/validation call and names
    which credential is missing via `credential` ('api_key' or 'api_secret')
    so the operator knows exactly which value to supply.
    """

    def __init__(self, message, credential=None, source=None):
        super().__init__(message)
        self.credential = credential
        self.source = source


class ValidationTimeoutError(Exception):
    """
    Raised when Delta does not return a validation response within the
    ``VALIDATION_TIMEOUT_SECONDS`` window (Requirement 1.12).

    Signals that credential validation was aborted on timeout so ``main()``
    terminates WITHOUT persisting any session marker and reports a distinct,
    timeout-specific failure to the scheduler.
    """


class ValidationFailedError(Exception):
    """
    Raised when Delta returns an error response to the validation call
    (Requirement 1.11).

    Carries the Delta error ``payload`` so ``main()`` can log Delta's own error
    text, and terminates BEFORE any session marker is persisted.
    """

    def __init__(self, message, code=None, status_code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload

# ============================================================
# MODULE-LEVEL CONFIGURATION CONSTANTS (Requirements 1.1, 1.10)
# ============================================================
# Credential source: named files by default, or the DELTA_API_KEY /
# DELTA_API_SECRET environment variables.
CREDENTIAL_KEY_FILE = API_KEY_FILE
CREDENTIAL_SECRET_FILE = API_SECRET_FILE
SESSION_FILE = SESSION_STORE_FILE

# Credential validation timeout in seconds (Requirement 1.12).
VALIDATION_TIMEOUT_SECONDS = 30

# Authenticated endpoint used to prove the credential works (Requirement 1.3).
# GET /v2/wallet/balances requires a valid signature, so a 200 response is
# proof the api-key/secret pair is accepted by Delta.
VALIDATION_PATH = "/v2/wallet/balances"

# Process exit codes (Requirement 1.13). 0 == success and is DISTINCT from every
# non-zero failure code below, so the scheduler can gate the workflow on a clean
# validation. Distinct codes per failure category are a nice-to-have that keeps
# scheduler logs actionable.
EXIT_SUCCESS = 0
EXIT_MISSING_CREDENTIAL = 2
EXIT_VALIDATION_FAILED = 3
EXIT_VALIDATION_TIMEOUT = 4
EXIT_IP_NOT_WHITELISTED = 5
EXIT_CLOCK_DRIFT = 6
EXIT_USER_AGENT = 7
EXIT_UNEXPECTED = 1


def load_credentials(source="files"):
    """
    Read the API key and secret from the configured credential source and
    return them as an ``(api_key, api_secret)`` tuple. (Task 3.1, Reqs 1.1, 1.2)

    Credential source (Requirement 1.1):
      * ``source="files"`` (default) — read the key from ``CREDENTIAL_KEY_FILE``
        (``delta_api_key.txt``) and the secret from ``CREDENTIAL_SECRET_FILE``
        (``delta_api_secret.txt``). Surrounding whitespace/newlines are stripped
        so a trailing newline in the file does not corrupt the signature.
      * ``source="env"`` — read the key/secret from the ``DELTA_API_KEY`` /
        ``DELTA_API_SECRET`` environment variables (also stripped).

    Missing-credential guard (Requirement 1.2):
      If the API key OR the API secret is missing or empty in the configured
      source, this raises ``MissingCredentialError`` naming which credential
      (the API key vs the API secret) is missing. A missing credential FILE is
      treated the same as an empty credential — a clear, credential-naming
      error rather than an unhandled exception. This guard runs entirely
      locally and returns/raises BEFORE any network or validation call is made.
    """
    normalized = str(source).strip().lower()

    if normalized == "files":
        api_key = _read_credential_file(CREDENTIAL_KEY_FILE)
        api_secret = _read_credential_file(CREDENTIAL_SECRET_FILE)
        key_location = CREDENTIAL_KEY_FILE
        secret_location = CREDENTIAL_SECRET_FILE
    elif normalized == "env":
        api_key = _read_credential_env(ENV_API_KEY)
        api_secret = _read_credential_env(ENV_API_SECRET)
        key_location = ENV_API_KEY
        secret_location = ENV_API_SECRET
    else:
        raise ValueError(
            "Unknown credential source {!r}; expected 'files' or 'env'."
            .format(source)
        )

    # Missing-credential guard (1.2): check the key first, then the secret, and
    # name exactly which one is missing. This happens before any network call.
    if not api_key:
        raise MissingCredentialError(
            "Delta API KEY is missing or empty in the configured credential "
            "source (expected in {}). Provide the API key before validation."
            .format(key_location),
            credential="api_key",
            source=normalized,
        )
    if not api_secret:
        raise MissingCredentialError(
            "Delta API SECRET is missing or empty in the configured credential "
            "source (expected in {}). Provide the API secret before validation."
            .format(secret_location),
            credential="api_secret",
            source=normalized,
        )

    return api_key, api_secret


def _read_credential_file(path):
    """
    Return the stripped contents of credential file `path`, or ``""`` when the
    file is absent/unreadable so the missing-credential guard can raise a clear,
    credential-naming error instead of an unhandled ``OSError`` (Req 1.2).
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, UnicodeDecodeError):
        # Missing/unreadable file is treated as a missing credential: the
        # caller's guard turns this into a key-vs-secret naming error.
        return ""


def _read_credential_env(name):
    """Return the stripped value of environment variable `name`, or ``""``."""
    return os.environ.get(name, "").strip()


def validate_credentials(signer):
    """
    Prove the credential works via a signed ``GET /v2/wallet/balances`` and
    return the parsed validation response. (Task 3.2, Reqs 1.3, 1.4, 1.11, 1.12)

    Validation (1.3, 1.4): issues a SIGNED request through `DeltaSigner`, which
    computes the HMAC-SHA256 signature and attaches the mandatory api-key,
    signature, timestamp, and User-Agent headers. A 200 response is proof the
    credential is accepted.

    30 s timeout (1.12): the caller is expected to build the signer with a
    connect/read timeout that sums to <= VALIDATION_TIMEOUT_SECONDS (the
    DeltaSigner default (3.0, 27.0) sums to exactly 30 s). `requests` raises
    ``requests.exceptions.Timeout`` when either phase exceeds its budget; that
    is mapped to ``ValidationTimeoutError`` so the caller terminates WITHOUT
    persisting a session marker.

    Validation failure (1.11): a Delta error (typed ``DeltaAPIError`` or its
    subclasses) is propagated so `main()` can surface Delta's own error
    response. No persistence happens on this path — the caller only persists
    after this function returns successfully.

    Returns the parsed validation response dict on success.
    """
    try:
        response = signer.request("GET", VALIDATION_PATH)
    except requests.exceptions.Timeout as exc:
        # No response within the 30 s validation budget (Req 1.12). Abort
        # without persisting and surface a distinct timeout error.
        raise ValidationTimeoutError(
            "Delta did not return a credential-validation response within "
            "{} seconds; validation aborted (no session persisted)."
            .format(VALIDATION_TIMEOUT_SECONDS)
        ) from exc
    except requests.exceptions.RequestException as exc:
        # Any other transport-level failure (connection error, etc.) is a
        # validation failure: terminate without persisting (Req 1.11).
        raise ValidationFailedError(
            "Delta credential validation request failed at the transport "
            "layer: {}".format(exc)
        ) from exc

    return response


def persist_session(cred, store_path=SESSION_STORE_FILE):
    """
    Write a validated-session marker to the Credential_Store for reuse by
    delta_client. (Task 3.2, Requirement 1.10)

    On a SUCCESSFUL validation, records a minimal JSON marker at `store_path`
    (default ``delta_session.json``) noting that validation succeeded and when.
    The raw API secret is NEVER written into the marker — only a boolean flag,
    the validation timestamp, and a small, non-sensitive slice of Delta's
    validation response (e.g. an account/user id when present) so delta_client
    (Task 4) can confirm a validated session exists.

    File-permission hardening (chmod 600) is handled by a later task (17.1);
    here we only write the marker.
    """
    marker = {
        "validated": True,
        "timestamp": int(time.time()),
    }

    # Record a small, non-sensitive slice of the validation response so
    # delta_client can correlate the marker without re-validating. Never copy
    # the raw secret; only surface a stable identifier when Delta provides one.
    if isinstance(cred, dict):
        result = cred.get("result")
        if isinstance(result, dict):
            for key in ("user_id", "id", "account_id"):
                value = result.get(key)
                if value is not None:
                    marker["account_id"] = value
                    break
        elif isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                for key in ("user_id", "id", "account_id"):
                    value = first.get(key)
                    if value is not None:
                        marker["account_id"] = value
                        break

    with open(store_path, "w", encoding="utf-8") as handle:
        json.dump(marker, handle)


def _build_validation_signer(api_key, api_secret):
    """
    Construct a `DeltaSigner` for validation against the correct Delta host.

    Uses the production host by default and the testnet host when
    ``DELTA_TESTNET=1`` (mirrors delta_client's selection). The default signer
    timeout ``(3.0, 27.0)`` sums to the 30 s validation budget (Req 1.12).
    """
    base_url = TESTNET_BASE_URL if _env_testnet() else PROD_BASE_URL
    return DeltaSigner(
        api_key,
        api_secret,
        base_url=base_url,
        user_agent=DEFAULT_USER_AGENT,
    )


def main():
    """
    Orchestrate load -> validate -> persist and return the process exit code:
    ``EXIT_SUCCESS`` (0) on success, a distinct non-zero code on every failure
    path. (Task 3.2, Requirement 1.13)

    The success code is 0 and every failure path returns a non-zero code, so the
    scheduler can gate the downstream workflow on a clean exit. A clear message
    is logged for each failure category (print-based, consistent with the
    existing bot). On any failure BEFORE or DURING validation, no session marker
    is persisted; persistence only runs after validation returns successfully.
    """
    # ---- Load credentials (Reqs 1.1, 1.2) -----------------------------------
    try:
        api_key, api_secret = load_credentials()
    except MissingCredentialError as exc:
        print("[delta_auth] Missing credential ({}): {}".format(
            exc.credential, exc))
        return EXIT_MISSING_CREDENTIAL
    except Exception as exc:  # pragma: no cover - defensive
        print("[delta_auth] Unexpected error loading credentials: {}"
              .format(exc))
        return EXIT_UNEXPECTED

    signer = _build_validation_signer(api_key, api_secret)

    # ---- Validate credentials (Reqs 1.3, 1.4, 1.5, 1.7, 1.9, 1.11, 1.12) ----
    try:
        validation = validate_credentials(signer)
    except ValidationTimeoutError as exc:
        print("[delta_auth] Validation timed out: {}".format(exc))
        return EXIT_VALIDATION_TIMEOUT
    except DeltaClockDriftError as exc:
        print("[delta_auth] Clock drift (SignatureExpired): {} | Delta "
              "response: {}".format(exc, exc.payload))
        return EXIT_CLOCK_DRIFT
    except DeltaIPNotWhitelistedError as exc:
        print("[delta_auth] IP not whitelisted: {} | Delta response: {}"
              .format(exc, exc.payload))
        return EXIT_IP_NOT_WHITELISTED
    except DeltaUserAgentRequiredError as exc:
        print("[delta_auth] User-Agent required: {} | Delta response: {}"
              .format(exc, exc.payload))
        return EXIT_USER_AGENT
    except DeltaAPIError as exc:
        # Any other Delta error: terminate without persisting and INCLUDE
        # Delta's own error response (Req 1.11).
        print("[delta_auth] Validation failed: {} | Delta response: {}"
              .format(exc, exc.payload))
        return EXIT_VALIDATION_FAILED
    except ValidationFailedError as exc:
        print("[delta_auth] Validation failed: {} | Delta response: {}"
              .format(exc, exc.payload))
        return EXIT_VALIDATION_FAILED
    except Exception as exc:  # pragma: no cover - defensive
        print("[delta_auth] Unexpected error during validation: {}"
              .format(exc))
        return EXIT_UNEXPECTED

    # ---- Persist validated session (Req 1.10) -------------------------------
    try:
        persist_session(validation, store_path=SESSION_FILE)
    except Exception as exc:
        print("[delta_auth] Validation succeeded but persisting the session "
              "marker to {} failed: {}".format(SESSION_FILE, exc))
        return EXIT_UNEXPECTED

    print("[delta_auth] Credential validated; session marker written to {}."
          .format(SESSION_FILE))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
