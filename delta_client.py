"""
delta_client.py — Delta Exchange signed-request layer and client connection.

Parallel to the Fyers bot's fyers_client.py. This module hosts:
  * DeltaSigner — the HMAC-SHA256 signed-request transport reused by
    delta_auth.py and helper_delta.py (Requirements 1.3-1.9, 5.9).
  * Client construction from the persisted Credential_Store, exposing a single
    module-level `delta` singleton for helper_delta and the strategy to import
    (Requirement 2).

NOTE: This is a scaffolding stub. Signing, transport, and client construction
logic are implemented in Task 2 (DeltaSigner) and Task 4 (client connection).
"""

import hashlib
import hmac
import json
import os
import time

import requests

from delta_config import (
    PROD_BASE_URL,
    TESTNET_BASE_URL,
    DEFAULT_USER_AGENT,
    DEFAULT_TIMEOUT,
    API_KEY_FILE,
    API_SECRET_FILE,
    SESSION_STORE_FILE,
    ENV_API_KEY,
    ENV_API_SECRET,
    ENV_TESTNET,
    SIGNATURE_VALIDITY_SECONDS,
)


# ============================================================
# TYPED ERRORS (Requirements 1.5, 1.7, 1.9)
# ============================================================
class DeltaAPIError(Exception):
    """
    Base class for typed Delta Exchange transport errors.

    Carries the parsed Delta error `code`, the offending HTTP `status_code`
    when known, and the raw response `payload`/`body` so callers (delta_auth,
    helper_delta) can log Delta's own error text without re-parsing.
    """

    def __init__(self, message, code=None, status_code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload


class DeltaClockDriftError(DeltaAPIError):
    """
    Raised on a `SignatureExpired` response: the host clock has drifted beyond
    the signature validity window, so the signed timestamp no longer reaches
    Delta in time. Advise NTP re-sync. (Requirement 1.7)
    """


class DeltaIPNotWhitelistedError(DeltaAPIError):
    """
    Raised on an `ip_not_whitelisted_for_api_key` response. Exposes the
    offending IP (`offending_ip`) when Delta includes it in the error context,
    so the operator knows which address to whitelist. (Requirement 1.9)
    """

    def __init__(self, message, offending_ip=None, code=None,
                 status_code=None, payload=None):
        super().__init__(message, code=code, status_code=status_code,
                         payload=payload)
        self.offending_ip = offending_ip


class DeltaUserAgentRequiredError(DeltaAPIError):
    """
    Raised on a CDN `Forbidden` rejection, which is what Delta's edge returns
    when the mandatory non-empty `User-Agent` header is absent. (Requirement 1.5)
    """


class CredentialLoadError(Exception):
    """
    Raised when the persisted credential cannot be loaded from the
    Credential_Store before a client is constructed (Requirement 2.3).

    This is the named error that signals the credential is absent, or is
    present but cannot be read/parsed. The message and the `expected_file`
    attribute name the exact credential file the operator is expected to
    supply (via delta_auth), so the failure is actionable rather than an
    unhandled ``OSError``. When this is raised, NO client object is
    constructed or exposed. `credential` records which value was missing
    ('api_key' or 'api_secret') when known.
    """

    def __init__(self, message, expected_file=None, credential=None):
        super().__init__(message)
        self.expected_file = expected_file
        self.credential = credential

# ============================================================
# MODULE-LEVEL CONFIGURATION CONSTANTS (Requirements 2.5, 2.6)
# ============================================================
# Production host is the default; testnet host is selected when DELTA_TESTNET=1.
BASE_URL = PROD_BASE_URL
TESTNET_URL = TESTNET_BASE_URL


class DeltaSigner:
    """
    HMAC-SHA256 signed-request layer for Delta Exchange (Requirements 1.3-1.9).

    Computes the `signature` header as the hex-encoded HMAC-SHA256, keyed by the
    API secret, over the prehash string:
        prehash = method + timestamp + requestPath + query_string + body
    and attaches the api-key, signature, timestamp (unix seconds), and
    User-Agent headers to every authenticated request.
    """

    def __init__(self, api_key, api_secret, base_url=PROD_BASE_URL,
                 user_agent=DEFAULT_USER_AGENT, timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.user_agent = user_agent
        self.timeout = timeout

    @staticmethod
    def _generate_signature(secret, message):
        """
        Return the hex-encoded HMAC-SHA256 of `message` keyed by `secret`
        (Requirement 1.3).

        This is the exact signature Delta expects in the `signature` header:
        an HMAC using SHA-256, with the API secret as the key and the prehash
        string as the message, rendered as a lowercase hex digest.
        """
        return hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, method, path, query_string, body):
        """
        Build the signed request headers for an authenticated Delta call
        (Requirements 1.3, 1.4).

        The prehash string is the concatenation
            method + timestamp + path + query_string + body
        where:
          * `method` is the upper-case HTTP verb (GET, POST, DELETE),
          * `timestamp` is unix time in SECONDS as a string, computed once here
            and reused in both the prehash and the `timestamp` header so the
            signed value and the transmitted value are identical,
          * `path` is the request path only (e.g. `/v2/orders`),
          * `query_string` is the leading-`?` query string for GETs, or `''`,
          * `body` is the exact JSON body string for POST/DELETE, or `''`.

        Returns the dict of headers required on every authenticated request:
        `api-key`, `signature`, `timestamp`, `User-Agent`, and `Content-Type`.
        A non-empty `User-Agent` is mandatory — a missing one triggers a CDN
        `Forbidden` rejection.
        """
        method = method.upper()
        query_string = query_string or ""
        body = body or ""
        timestamp = str(int(time.time()))

        prehash = method + timestamp + path + query_string + body
        signature = self._generate_signature(self.api_secret, prehash)

        return {
            "api-key": self.api_key,
            "signature": signature,
            "timestamp": timestamp,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
        }

    # Bound the 429 retry loop so a stuck quota window can never spin forever
    # (Requirement 5.9). Each retry waits the exact X-RATE-LIMIT-RESET window.
    _MAX_RATE_LIMIT_RETRIES = 3

    @staticmethod
    def _build_query_string(params):
        """
        Render `params` into a leading-`?` query string (e.g.
        `?product_id=1&state=open`), or `''` when there are no params.

        The SAME string is fed into the signature prehash and appended to the
        transmitted URL, so the signed query and the wire query stay identical
        (a mismatch would make Delta reject the signature).
        """
        if not params:
            return ""
        pairs = []
        for key, value in params.items():
            if value is None:
                continue
            pairs.append("{}={}".format(key, value))
        if not pairs:
            return ""
        return "?" + "&".join(pairs)

    @staticmethod
    def _canonical_body(body_obj):
        """
        Serialize `body_obj` to a canonical JSON string ONCE, or return `''`
        when there is no body (GET / no-body calls).

        Using fixed, compact separators makes the encoding deterministic so the
        exact same bytes are both signed and transmitted. Callers must never
        re-serialize `body_obj`; the returned string is the single source of
        truth for the request body.
        """
        if body_obj is None:
            return ""
        return json.dumps(body_obj, separators=(",", ":"))

    def request(self, method, path, params=None, body_obj=None, signed=True):
        """
        Send a (optionally signed) request, centralizing rate-limit handling,
        timeouts, and typed error mapping (Requirements 1.5, 1.7, 1.9, 5.9).

        The body is serialized to a canonical JSON string exactly once and both
        signed and transmitted verbatim (`data=body_string`, not `json=`), so
        the bytes Delta hashes match the bytes on the wire. The query string is
        built with a leading `?` and shared between the signature and the URL.
        On HTTP 429 the `X-RATE-LIMIT-RESET` header (milliseconds) is honoured
        with a bounded sleep-and-retry. Delta error codes are mapped to typed
        exceptions:
          * `SignatureExpired`               -> DeltaClockDriftError (1.7)
          * `ip_not_whitelisted_for_api_key` -> DeltaIPNotWhitelistedError (1.9)
          * CDN `Forbidden` (missing UA)     -> DeltaUserAgentRequiredError (1.5)

        Returns the parsed JSON dict on success.
        """
        method = method.upper()
        query_string = self._build_query_string(params)
        body_string = self._canonical_body(body_obj)
        url = self.base_url + path + query_string

        for attempt in range(self._MAX_RATE_LIMIT_RETRIES + 1):
            if signed:
                headers = self._headers(method, path, query_string, body_string)
            else:
                headers = {
                    "User-Agent": self.user_agent,
                    "Content-Type": "application/json",
                }

            response = requests.request(
                method,
                url,
                headers=headers,
                data=body_string,
                timeout=self.timeout,
            )

            # ---- Rate limiting (Requirement 5.9) ------------------------
            if response.status_code == 429:
                if attempt < self._MAX_RATE_LIMIT_RETRIES:
                    reset_ms = response.headers.get("X-RATE-LIMIT-RESET")
                    time.sleep(self._reset_seconds(reset_ms))
                    continue
                raise DeltaAPIError(
                    "Delta rate limit (HTTP 429) still active after "
                    "{} retries.".format(self._MAX_RATE_LIMIT_RETRIES),
                    status_code=429,
                    payload=self._safe_json(response),
                )

            # ---- CDN Forbidden -> User-Agent required (Requirement 1.5) -
            # A missing/blank User-Agent is rejected at Delta's edge (CDN)
            # with a 403 Forbidden that is typically NOT JSON.
            if response.status_code == 403 and not self._error_code(response):
                raise DeltaUserAgentRequiredError(
                    "Delta returned a CDN 'Forbidden' response; a non-empty "
                    "'User-Agent' header is required on every request.",
                    code="Forbidden",
                    status_code=403,
                    payload=self._safe_text(response),
                )

            payload = self._safe_json(response)
            error_code = self._error_code(response, payload)

            if error_code:
                self._raise_for_error_code(error_code, response, payload)

            if not response.ok:
                raise DeltaAPIError(
                    "Delta request failed (HTTP {}): {}".format(
                        response.status_code, self._safe_text(response)),
                    code=error_code,
                    status_code=response.status_code,
                    payload=payload if payload is not None
                    else self._safe_text(response),
                )

            return payload if payload is not None else {}

        # Unreachable: the loop either returns or raises on every path.
        raise DeltaAPIError("Delta request exhausted retries without a result.")

    # --------------------------------------------------------------------
    # Error-mapping helpers
    # --------------------------------------------------------------------
    @staticmethod
    def _reset_seconds(reset_ms):
        """Convert the X-RATE-LIMIT-RESET header (ms) to a safe sleep in secs."""
        try:
            return max(0.0, float(reset_ms) / 1000.0)
        except (TypeError, ValueError):
            # Header missing/garbled: fall back to the signature window so we
            # still back off rather than hammering the endpoint.
            return float(SIGNATURE_VALIDITY_SECONDS)

    @staticmethod
    def _safe_json(response):
        """Return the parsed JSON body, or None if the body is not JSON."""
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _safe_text(response):
        """Return the raw response text, guarding against decode errors."""
        try:
            return response.text
        except Exception:  # pragma: no cover - defensive
            return ""

    @classmethod
    def _error_code(cls, response, payload=None):
        """
        Extract Delta's error `code` string from a response, if any.

        Delta error bodies look like
            {"success": false, "error": {"code": "...", "context": {...}}}
        Older/edge shapes may put the token directly under `error`.
        """
        if payload is None:
            payload = cls._safe_json(response)
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("code")
        if isinstance(error, str):
            return error
        return None

    @classmethod
    def _raise_for_error_code(cls, error_code, response, payload):
        """Map a Delta error code to a typed exception (Reqs 1.5, 1.7, 1.9)."""
        normalized = str(error_code)

        if normalized.lower() == "signatureexpired":
            raise DeltaClockDriftError(
                "Delta rejected the request with 'SignatureExpired': the host "
                "system clock has drifted beyond the {}-second signature "
                "validity window. Re-sync the clock (NTP).".format(
                    SIGNATURE_VALIDITY_SECONDS),
                code=normalized,
                status_code=response.status_code,
                payload=payload,
            )

        if normalized == "ip_not_whitelisted_for_api_key":
            offending_ip = cls._extract_offending_ip(payload)
            suffix = " (offending IP: {})".format(offending_ip) \
                if offending_ip else ""
            raise DeltaIPNotWhitelistedError(
                "Delta rejected the request with "
                "'ip_not_whitelisted_for_api_key': the host IP address is not "
                "whitelisted on the API key{}.".format(suffix),
                offending_ip=offending_ip,
                code=normalized,
                status_code=response.status_code,
                payload=payload,
            )

        # Any other Delta error code: surface it with Delta's own response.
        raise DeltaAPIError(
            "Delta request failed with error '{}': {}".format(
                normalized, payload),
            code=normalized,
            status_code=response.status_code,
            payload=payload,
        )

    @staticmethod
    def _extract_offending_ip(payload):
        """
        Pull the offending IP out of Delta's error context when present.

        Delta typically nests it as error.context.ip_address (or similar);
        fall back to scanning common keys so a schema tweak does not drop it.
        """
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        context = error.get("context")
        if isinstance(context, dict):
            for key in ("ip_address", "ip", "client_ip", "offending_ip"):
                value = context.get(key)
                if value:
                    return value
        # Some payloads place the IP directly on the error object.
        for key in ("ip_address", "ip", "client_ip"):
            value = error.get(key)
            if value:
                return value
        return None


def _env_testnet():
    """Return True when the DELTA_TESTNET environment flag selects testnet."""
    return os.environ.get(ENV_TESTNET, "").strip() == "1"


def _read_credential_source(file_path, env_name):
    """
    Return the stripped credential value from `file_path`, falling back to the
    `env_name` environment variable, or ``""`` when neither yields a value.

    Mirrors the file-then-strip approach delta_auth uses (`_read_credential_file`
    / `_read_credential_env`) so both modules read the Credential_Store the same
    way. A trailing newline in the file would corrupt the signature, so the
    value is stripped. This module deliberately does NOT import delta_auth:
    delta_auth imports from delta_client, so importing back would create a
    circular import. The read is duplicated here instead.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
            if value:
                return value
    except (OSError, UnicodeDecodeError):
        # Missing/unreadable file: fall through to the environment variable.
        pass
    return os.environ.get(env_name, "").strip()


def _load_persisted_credential(store_path=SESSION_STORE_FILE):
    """
    Read the persisted credential from the Credential_Store BEFORE any client is
    constructed, and raise a named error if it is absent/unreadable.
    (Task 4.1, Requirements 2.1, 2.3)

    "Persisted credential" here is the API key + secret needed to build the
    signed-request client. They are read from the credential files
    (``delta_api_key.txt`` / ``delta_api_secret.txt``) written into the
    Credential_Store, falling back to the ``DELTA_API_KEY`` / ``DELTA_API_SECRET``
    environment variables — the same sources delta_auth loads from. When a
    validated-session marker (`store_path`, default ``delta_session.json``) is
    present it is parsed to confirm a validated session exists; a present-but-
    corrupt marker is treated as an unparseable credential and rejected.

    Absent/unreadable/unparseable (Requirement 2.3): if the API key or secret is
    missing, or the session marker exists but cannot be parsed, this raises
    ``CredentialLoadError`` whose message and `expected_file` attribute NAME the
    exact credential file the operator must supply. No client is constructed on
    this path.

    Returns the loaded credential as a ``{"api_key": ..., "api_secret": ...}``
    dict on success.
    """
    api_key = _read_credential_source(API_KEY_FILE, ENV_API_KEY)
    api_secret = _read_credential_source(API_SECRET_FILE, ENV_API_SECRET)

    # Name exactly which credential file is missing so the failure is
    # actionable and no client is constructed (Req 2.3).
    if not api_key:
        raise CredentialLoadError(
            "Delta API key could not be loaded from the Credential_Store "
            "(expected in {!r}, or the {} environment variable). Run delta_auth "
            "to create it; no client was constructed.".format(
                API_KEY_FILE, ENV_API_KEY),
            expected_file=API_KEY_FILE,
            credential="api_key",
        )
    if not api_secret:
        raise CredentialLoadError(
            "Delta API secret could not be loaded from the Credential_Store "
            "(expected in {!r}, or the {} environment variable). Run delta_auth "
            "to create it; no client was constructed.".format(
                API_SECRET_FILE, ENV_API_SECRET),
            expected_file=API_SECRET_FILE,
            credential="api_secret",
        )

    # If a validated-session marker is present, it must be parseable. A present
    # but corrupt marker is an unparseable credential and is rejected (Req 2.3);
    # an absent marker is tolerated here (the key/secret are the credential the
    # client actually needs, and delta_auth owns marker creation).
    if os.path.exists(store_path):
        try:
            with open(store_path, "r", encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise CredentialLoadError(
                "Delta session marker {!r} is present but could not be read or "
                "parsed ({}); no client was constructed.".format(
                    store_path, exc),
                expected_file=store_path,
            ) from exc

    return {"api_key": api_key, "api_secret": api_secret}


def build_client(testnet=False):
    """
    Construct a single authenticated DeltaSigner-backed client against the
    production host by default, or the testnet host when `testnet` is set.
    (Task 4.1, Requirements 2.1, 2.2, 2.5, 2.6)

    The persisted credential is loaded FIRST via `_load_persisted_credential`
    (Req 2.1); if it is absent/unreadable that call raises ``CredentialLoadError``
    and no client is built (Req 2.3). On success a single ``DeltaSigner`` is
    constructed with that credential (Req 2.2) against ``PROD_BASE_URL`` by
    default (Req 2.5), or ``TESTNET_BASE_URL`` when `testnet` is True (Req 2.6),
    using the mandatory non-empty User-Agent and the default timeout.
    """
    cred = _load_persisted_credential()
    base_url = TESTNET_BASE_URL if testnet else PROD_BASE_URL
    return DeltaSigner(
        cred["api_key"],
        cred["api_secret"],
        base_url=base_url,
        user_agent=DEFAULT_USER_AGENT,
        timeout=DEFAULT_TIMEOUT,
    )


# Module-level singleton client (Requirement 2.4): helper_delta and the strategy
# import `delta` directly. Per the design it is constructed on import via
# build_client(testnet=_env_testnet()).
#
# Chicken-and-egg guard: delta_auth (which runs FIRST to CREATE the credential
# files/session marker) imports DeltaSigner, _env_testnet, and the typed errors
# from this module. If the module-level construction raised on a bare import
# when credentials are absent, importing delta_client would crash delta_auth
# before it could ever write the credential. So the auto-construction is wrapped
# in a try/except: on CredentialLoadError we leave `delta = None` and stash the
# error in `_delta_load_error`. build_client() itself ALWAYS raises the named
# error (Req 2.3) — only this convenience auto-construction is guarded. Code
# that actually needs the client can call require_delta() to surface the named
# error at point of use.
_delta_load_error = None
try:
    delta = build_client(testnet=_env_testnet())
except CredentialLoadError as exc:
    delta = None
    _delta_load_error = exc


def require_delta():
    """
    Return the constructed module-level `delta` client, or re-raise the named
    ``CredentialLoadError`` from import-time construction (Requirements 2.3, 2.4).

    Callers that must have a live client (helper_delta, the strategy) use this
    so an absent credential surfaces the named credential-file error at the
    point of use, instead of a bare ``AttributeError`` on ``None``.
    """
    if delta is None:
        if _delta_load_error is not None:
            raise _delta_load_error
        # Defensive: credential state changed since import — rebuild so the
        # named error (or a fresh client) is produced from current files.
        return build_client(testnet=_env_testnet())
    return delta
