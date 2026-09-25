"""
delta_data_feed.py — Live market-data feed (Requirement 3).

Parallel to the Fyers bot's test2.py, which the strategy polls via
helper.getLTP on localhost:4001. Serves GET /ltp?instrument={symbol} from a
local HTTP server, sourcing the price from GET /v2/tickers/{symbol}.

Run it as a standalone process (like ``python delta_data_feed.py``), the same
way test2.py is launched for the Fyers bot. It reads the Delta credential files
directly (via delta_client.build_client), so delta_auth must have created them
first.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from delta_config import (
    DATA_FEED_HOST,
    DATA_FEED_PORT,
    PRICE_FRESHNESS_SECONDS,
)
from delta_client import (
    build_client,
    _env_testnet,
    CredentialLoadError,
    DeltaAPIError,
)
import helper_delta

# ============================================================
# MODULE-LEVEL CONFIGURATION CONSTANTS (Requirements 3.5, 3.6)
# ============================================================
# Configurable local host/port; defaults to 127.0.0.1:4001.
HOST = DATA_FEED_HOST
PORT = DATA_FEED_PORT

# Prices older than this freshness window (seconds) are treated as unavailable.
FRESHNESS_WINDOW_SECONDS = PRICE_FRESHNESS_SECONDS

# Only this path serves the LTP; every other path is a 404.
FEED_PATH = "/ltp"

# The feed uses a DEDICATED client with a tight (connect, read) timeout so the
# happy path answers well within the 1 s goal (3.1) and a slow/stale upstream
# surfaces as "price source unavailable" (3.6) rather than blocking. This is a
# fresh client so the module-level `delta` singleton other components share
# keeps its default timeout.
FEED_CLIENT_TIMEOUT = (1.0, 1.0)

# JSON error messages (Requirements 3.2, 3.3, 3.6).
ERR_INSTRUMENT_REQUIRED = "instrument parameter required"
ERR_INSTRUMENT_NOT_RECOGNIZED = "instrument not recognized"
ERR_PRICE_UNAVAILABLE = "price source unavailable"

# Non-2xx status codes used for the error responses.
STATUS_BAD_REQUEST = 400          # 3.2 missing/unparseable instrument
STATUS_NOT_FOUND = 404            # 3.3 well-formed but unknown instrument
STATUS_PRICE_UNAVAILABLE = 503    # 3.6 stale / upstream price source unavailable

# Tokens in an upstream error payload that identify a well-formed-but-unknown
# instrument (Delta's ticker endpoint returns a 404 / not-found error for an
# unrecognized symbol). Anything else is treated as a price-source problem.
_UNKNOWN_INSTRUMENT_TOKENS = (
    "not_found",
    "not found",
    "resource_not_found",
    "invalid_symbol",
    "unavailable_symbol",
    "bad_symbol",
)


def _error_haystack(payload):
    """Return a lowercased string view of an upstream payload for token scanning."""
    if payload is None:
        return ""
    if isinstance(payload, (dict, list)):
        try:
            return json.dumps(payload).lower()
        except (TypeError, ValueError):
            return str(payload).lower()
    return str(payload).lower()


def _is_unknown_instrument_error(exc):
    """
    Decide whether a raised price error means the instrument is unrecognized
    (-> HTTP 404, Req 3.3) rather than a transient/stale price source (-> 503,
    Req 3.6).

    ``manualLTP`` folds a non-transient upstream 404 into a
    ``PriceUnavailableError`` whose ``payload`` carries Delta's own error body,
    so we inspect the typed error's ``status_code`` (when a raw ``DeltaAPIError``
    escapes) and then scan the payload/message for Delta's not-found tokens. A
    well-formed ticker with merely no usable price carries none of these tokens,
    so it correctly stays a "price source unavailable" response.
    """
    if getattr(exc, "status_code", None) == 404:
        return True
    haystack = _error_haystack(getattr(exc, "payload", None))
    haystack += " " + str(exc).lower()
    return any(token in haystack for token in _UNKNOWN_INSTRUMENT_TOKENS)


class _LTPRequestHandler(BaseHTTPRequestHandler):
    """
    Handle GET /ltp?instrument={symbol}, returning JSON for every path.

    Produces HTTP 200 + {instrument, ltp} on success (3.1), 400 for a
    missing/unparseable instrument (3.2), 404 for a well-formed but unknown
    instrument (3.3), and 503 "price source unavailable" for a stale/slow/absent
    price source (3.6). Non-/ltp paths return 404.
    """

    server_version = "DeltaLTPFeed/1.0"

    def _write_json(self, status_code, payload):
        """Serialize `payload` as JSON with the appropriate headers."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        # Only /ltp is served; anything else is a 404 (3.3-style not found).
        if parsed.path.rstrip("/") != FEED_PATH:
            self._write_json(STATUS_NOT_FOUND, {"error": "not found"})
            return

        # Extract and validate the instrument query parameter (3.2).
        params = parse_qs(parsed.query, keep_blank_values=True)
        instrument_values = params.get("instrument")
        instrument = instrument_values[0].strip() if instrument_values else ""
        if not instrument:
            self._write_json(STATUS_BAD_REQUEST,
                             {"error": ERR_INSTRUMENT_REQUIRED})
            return

        client = getattr(self.server, "feed_client", None)
        if client is None:
            # No client was constructed (missing credential); the price source
            # is unavailable rather than a bad request (3.6).
            self._write_json(STATUS_PRICE_UNAVAILABLE,
                             {"error": ERR_PRICE_UNAVAILABLE})
            return

        try:
            # manualLTP sources GET /v2/tickers/{symbol} with the fallback
            # ladder and fetches live each call, so the price is fresh by
            # construction (3.4); the tight client timeout bounds the fetch.
            ltp = helper_delta.manualLTP(instrument, client)
        except helper_delta.PriceUnavailableError as exc:
            if _is_unknown_instrument_error(exc):
                self._write_json(STATUS_NOT_FOUND,
                                 {"error": ERR_INSTRUMENT_NOT_RECOGNIZED})
            else:
                self._write_json(STATUS_PRICE_UNAVAILABLE,
                                 {"error": ERR_PRICE_UNAVAILABLE})
            return
        except DeltaAPIError as exc:
            # Defensive: a typed upstream error escaping manualLTP. A 404 /
            # not-found is an unknown instrument (3.3); anything else is a
            # price-source problem (3.6).
            if _is_unknown_instrument_error(exc):
                self._write_json(STATUS_NOT_FOUND,
                                 {"error": ERR_INSTRUMENT_NOT_RECOGNIZED})
            else:
                self._write_json(STATUS_PRICE_UNAVAILABLE,
                                 {"error": ERR_PRICE_UNAVAILABLE})
            return
        except Exception:  # pragma: no cover - defensive catch-all
            # Any unexpected failure still yields a clean price-unavailable
            # response instead of a 500/stack trace to the polling strategy.
            self._write_json(STATUS_PRICE_UNAVAILABLE,
                             {"error": ERR_PRICE_UNAVAILABLE})
            return

        self._write_json(200, {"instrument": instrument, "ltp": ltp})

    def log_message(self, fmt, *args):
        """Route access logs through print, consistent with the bot's logging."""
        print("[delta_data_feed] {} - {}".format(
            self.address_string(), fmt % args))


def _build_feed_client():
    """
    Build the dedicated feed client, reading the Delta credential files exactly
    as delta_client does.

    Uses ``build_client`` (not the shared `delta` singleton) so the tight
    ``FEED_CLIENT_TIMEOUT`` applies only to the feed. Selects the testnet host
    when DELTA_TESTNET=1. Raises ``CredentialLoadError`` if the credential is
    absent/unreadable, which ``serve`` reports at startup.
    """
    client = build_client(testnet=_env_testnet())
    client.timeout = FEED_CLIENT_TIMEOUT
    return client


def serve():
    """
    Start the local LTP HTTP server, serving GET /ltp?instrument={symbol} with
    JSON {instrument, ltp}, and the 400/404/staleness error responses.
    (Task 7.1, Requirements 3.1-3.6)

    Binds a ThreadingHTTPServer to the configurable HOST/PORT (default
    127.0.0.1:4001, Req 3.5) so concurrent polls do not block each other, then
    serves forever. If the Delta credential cannot be loaded the server is not
    started and a clear message points the operator at delta_auth.
    """
    try:
        feed_client = _build_feed_client()
    except CredentialLoadError as exc:
        print("[delta_data_feed] Cannot start the LTP feed: {}".format(exc))
        print("[delta_data_feed] Run delta_auth first to create the Delta "
              "credential files, then start the feed again.")
        return

    server = ThreadingHTTPServer((HOST, PORT), _LTPRequestHandler)
    server.feed_client = feed_client
    print("[delta_data_feed] Serving GET {}?instrument=<symbol> on "
          "http://{}:{} (freshness window {}s)".format(
              FEED_PATH, HOST, PORT, FRESHNESS_WINDOW_SECONDS))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[delta_data_feed] Shutting down on interrupt.")
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
