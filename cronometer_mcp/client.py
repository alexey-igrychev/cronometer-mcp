"""Cronometer API client using the GWT-RPC protocol.

Authenticates via the web login flow, then exports nutrition data
(servings, daily summaries, exercises, biometrics, notes) as CSV.

NOTE: Cronometer has no public API. This client uses the same GWT-RPC
protocol as the web app. The GWT magic values (permutation hash, header)
may change when Cronometer deploys new builds. See README for details.
"""

import csv
import io
import logging
import os
import re
from datetime import date

import requests

logger = logging.getLogger(__name__)

# URLs
LOGIN_HTML_URL = "https://cronometer.com/login/"
LOGIN_API_URL = "https://cronometer.com/login"
GWT_BASE_URL = "https://cronometer.com/cronometer/app"
EXPORT_URL = "https://cronometer.com/export"

# GWT magic values (may need updating if Cronometer deploys new builds)
DEFAULT_GWT_CONTENT_TYPE = "text/x-gwt-rpc; charset=UTF-8"
DEFAULT_GWT_MODULE_BASE = "https://cronometer.com/cronometer/"
DEFAULT_GWT_PERMUTATION = "7B121DC5483BF272B1BC1916DA9FA963"
DEFAULT_GWT_HEADER = "2D6A926E3729946302DC68073CB0D550"

GWT_AUTHENTICATE = (
    "7|0|5|https://cronometer.com/cronometer/|"
    "2D6A926E3729946302DC68073CB0D550|"
    "com.cronometer.shared.rpc.CronometerService|"
    "authenticate|java.lang.Integer/3438268394|"
    "1|2|3|4|1|5|5|-300|"
)

GWT_GENERATE_AUTH_TOKEN = (
    "7|0|8|https://cronometer.com/cronometer/|"
    "2D6A926E3729946302DC68073CB0D550|"
    "com.cronometer.shared.rpc.CronometerService|"
    "generateAuthorizationToken|java.lang.String/2004016611|"
    "I|com.cronometer.shared.user.AuthScope/2065601159|"
    "{nonce}|1|2|3|4|4|5|6|6|7|8|{user_id}|3600|7|2|"
)

EXPORT_TYPES = {
    "servings": "servings",
    "daily_summary": "dailySummary",
    "exercises": "exercises",
    "biometrics": "biometrics",
    "notes": "notes",
}


class CronometerClient:
    """Client for the Cronometer GWT-RPC API.

    Credentials are read from CRONOMETER_USERNAME and CRONOMETER_PASSWORD
    environment variables, or can be passed directly.
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        gwt_permutation: str | None = None,
        gwt_header: str | None = None,
    ):
        self.username = username or os.environ.get("CRONOMETER_USERNAME", "")
        self.password = password or os.environ.get("CRONOMETER_PASSWORD", "")
        self.gwt_permutation = gwt_permutation or DEFAULT_GWT_PERMUTATION
        self.gwt_header = gwt_header or DEFAULT_GWT_HEADER

        if not self.username or not self.password:
            raise ValueError(
                "Cronometer credentials required. Set CRONOMETER_USERNAME and "
                "CRONOMETER_PASSWORD environment variables, or pass them directly."
            )

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cronometer-mcp/0.1"})
        self.nonce: str | None = None
        self.user_id: str | None = None
        self._authenticated = False

    def _get_anticsrf(self) -> str:
        """Step 1: Fetch the login page and extract the anti-CSRF token."""
        resp = self.session.get(LOGIN_HTML_URL)
        resp.raise_for_status()
        match = re.search(r'name="anticsrf"\s+value="([^"]+)"', resp.text)
        if not match:
            raise RuntimeError("Could not find anti-CSRF token on login page")
        return match.group(1)

    def _login(self, anticsrf: str) -> None:
        """Step 2: POST credentials to the login endpoint."""
        resp = self.session.post(
            LOGIN_API_URL,
            data={
                "anticsrf": anticsrf,
                "username": self.username,
                "password": self.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("error"):
            raise RuntimeError(f"Login failed: {result['error']}")
        if not (result.get("success") or result.get("redirect")):
            raise RuntimeError(f"Login failed: unexpected response {result}")

        # Extract sesnonce cookie
        self.nonce = self.session.cookies.get("sesnonce")
        if not self.nonce:
            raise RuntimeError("Login succeeded but no sesnonce cookie received")
        logger.info("Login successful")

    def _gwt_authenticate(self) -> None:
        """Step 3: GWT authentication to get user ID."""
        resp = self.session.post(
            GWT_BASE_URL,
            data=GWT_AUTHENTICATE,
            headers={
                "content-type": DEFAULT_GWT_CONTENT_TYPE,
                "x-gwt-module-base": DEFAULT_GWT_MODULE_BASE,
                "x-gwt-permutation": self.gwt_permutation,
            },
        )
        resp.raise_for_status()

        match = re.search(r"OK\[(\d+),", resp.text)
        if not match:
            raise RuntimeError(
                f"GWT authenticate failed to extract user ID. "
                f"Response: {resp.text[:200]}"
            )
        self.user_id = match.group(1)

        # Update nonce from cookies
        new_nonce = self.session.cookies.get("sesnonce")
        if new_nonce:
            self.nonce = new_nonce
        logger.info("GWT auth successful, user_id=%s", self.user_id)

    def _generate_auth_token(self) -> str:
        """Step 4: Generate a short-lived auth token for export requests."""
        body = GWT_GENERATE_AUTH_TOKEN.replace("{nonce}", self.nonce or "")
        body = body.replace("{user_id}", self.user_id or "")

        resp = self.session.post(
            GWT_BASE_URL,
            data=body,
            headers={
                "content-type": DEFAULT_GWT_CONTENT_TYPE,
                "x-gwt-module-base": DEFAULT_GWT_MODULE_BASE,
                "x-gwt-permutation": self.gwt_permutation,
            },
        )
        resp.raise_for_status()

        match = re.search(r'"([^"]+)"', resp.text)
        if not match:
            raise RuntimeError(
                f"Failed to extract auth token. Response: {resp.text[:200]}"
            )
        token = match.group(1)
        logger.info("Auth token generated")
        return token

    def authenticate(self) -> None:
        """Full authentication flow (steps 1-3)."""
        if self._authenticated:
            return
        anticsrf = self._get_anticsrf()
        self._login(anticsrf)
        self._gwt_authenticate()
        self._authenticated = True

    def export_raw(
        self,
        export_type: str,
        start: date | None = None,
        end: date | None = None,
    ) -> str:
        """Export raw CSV data from Cronometer.

        Args:
            export_type: One of 'servings', 'daily_summary', 'exercises',
                        'biometrics', 'notes'.
            start: Start date (defaults to today).
            end: End date (defaults to today).

        Returns:
            Raw CSV text.
        """
        self.authenticate()
        token = self._generate_auth_token()

        if start is None:
            start = date.today()
        if end is None:
            end = date.today()

        generate_value = EXPORT_TYPES.get(export_type, export_type)

        resp = self.session.get(
            EXPORT_URL,
            params={
                "nonce": token,
                "generate": generate_value,
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
            },
            headers={
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
            },
        )
        resp.raise_for_status()
        return resp.text

    def export_parsed(
        self,
        export_type: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Export and parse CSV data into a list of dicts.

        Args:
            export_type: One of 'servings', 'daily_summary', 'exercises',
                        'biometrics', 'notes'.
            start: Start date (defaults to today).
            end: End date (defaults to today).

        Returns:
            List of dicts, one per CSV row.
        """
        raw = self.export_raw(export_type, start, end)
        reader = csv.DictReader(io.StringIO(raw))
        return list(reader)

    def get_food_log(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Get detailed food log (servings) for a date range."""
        return self.export_parsed("servings", start, end)

    def get_daily_summary(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict]:
        """Get daily nutrition summary for a date range."""
        return self.export_parsed("daily_summary", start, end)
