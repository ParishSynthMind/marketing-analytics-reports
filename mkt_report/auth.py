"""Credentials, all from environment variables, all read-only scopes.

Google (GA4 + Search Console), in order of preference:
  GOOGLE_APPLICATION_CREDENTIALS  path to a service-account JSON key (the usual way for a scheduled job)
  GOOGLE_OAUTH_ACCESS_TOKEN       a short-lived OAuth access token (handy for a one-off run)
Meta:
  META_ACCESS_TOKEN               a token with the ads_read permission
"""
from __future__ import annotations

import os
from typing import Callable, Dict

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]


class MissingCredentials(RuntimeError):
    pass


def google_auth_header() -> Callable[[], Dict[str, str]]:
    """Return a callable producing a fresh Authorization header (service-account tokens refresh themselves)."""
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if key_path:
        if not os.path.exists(key_path):
            raise MissingCredentials(f"GOOGLE_APPLICATION_CREDENTIALS points at a file that does not exist: {key_path}")
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(key_path, scopes=GOOGLE_SCOPES)

        def header() -> Dict[str, str]:
            if not creds.valid:
                creds.refresh(Request())
            return {"Authorization": f"Bearer {creds.token}"}
        return header
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if token:
        return lambda: {"Authorization": f"Bearer {token}"}
    raise MissingCredentials("No Google credentials: set GOOGLE_APPLICATION_CREDENTIALS (service-account JSON) "
                             "or GOOGLE_OAUTH_ACCESS_TOKEN")


def meta_auth_header() -> Callable[[], Dict[str, str]]:
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        raise MissingCredentials("No Meta credentials: set META_ACCESS_TOKEN (needs the ads_read permission)")
    return lambda: {"Authorization": f"Bearer {token}"}
