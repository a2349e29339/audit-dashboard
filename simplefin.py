"""Minimal SimpleFIN client.

Protocol (https://www.simplefin.org/protocol.html):
  1. The user gets a *setup token* from a SimpleFIN server (e.g. SimpleFIN
     Bridge). The token is base64 of a one-time "claim URL".
  2. POSTing to the claim URL returns an *access URL* with embedded basic-auth
     credentials. This is stored locally and never leaves the machine.
  3. GET {access_url}/accounts returns accounts and transactions.
"""
import base64
import time

import requests


class SimpleFINError(Exception):
    pass


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time setup token for a permanent access URL."""
    try:
        claim_url = base64.b64decode(setup_token.strip()).decode("utf-8")
    except Exception as e:
        raise SimpleFINError(f"Setup token is not valid base64: {e}")
    if not claim_url.startswith("https://"):
        raise SimpleFINError("Decoded claim URL is not https — refusing.")
    resp = requests.post(claim_url, timeout=30)
    if resp.status_code != 200:
        raise SimpleFINError(
            f"Claim failed ({resp.status_code}): {resp.text[:200]}. "
            "Setup tokens are single-use — generate a new one if this was already claimed."
        )
    access_url = resp.text.strip()
    if not access_url.startswith("https://"):
        raise SimpleFINError("Server returned an invalid access URL.")
    return access_url


def fetch_accounts(access_url: str, start_date: int | None = None,
                   end_date: int | None = None, pending: bool = True) -> dict:
    """Fetch accounts + transactions. Dates are unix timestamps."""
    params = {}
    if start_date:
        params["start-date"] = int(start_date)
    if end_date:
        params["end-date"] = int(end_date)
    if pending:
        params["pending"] = 1
    resp = requests.get(access_url.rstrip("/") + "/accounts", params=params, timeout=120)
    if resp.status_code == 402:
        raise SimpleFINError("SimpleFIN Bridge needs payment — your Bridge subscription has lapsed. "
                             "Renew at https://beta-bridge.simplefin.org (Billing). Nothing is lost; "
                             "syncing resumes automatically once it's active.")
    if resp.status_code == 403:
        raise SimpleFINError("Access denied (403). The access URL may have been revoked — "
                             "reconnect with a new setup token.")
    if resp.status_code != 200:
        raise SimpleFINError(f"Fetch failed ({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    if data.get("errors"):
        # Non-fatal: SimpleFIN reports per-institution issues here.
        data["_warnings"] = data["errors"]
    return data


def now() -> int:
    return int(time.time())
