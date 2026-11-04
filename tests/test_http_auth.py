import base64
import json

import pytest
import responses

from mkt_report.auth import GOOGLE_SCOPES, MissingCredentials, google_auth_header, meta_auth_header
from mkt_report.http import SourceError, Transport
from mkt_report.mock_api import MockResponse


class Scripted:
    """A session that answers with a fixed list of responses and records what it was asked."""

    def __init__(self, *responses_):
        self.queue = list(responses_)
        self.headers_seen = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.headers_seen.append(headers)
        return self.queue.pop(0)


def test_retries_429_and_5xx_with_retry_after_then_succeeds():
    waits = []
    sess = Scripted(MockResponse(429, {"error": {"message": "slow down"}}, {"Retry-After": "7"}),
                    MockResponse(503, {"error": {"message": "unavailable"}}),
                    MockResponse(200, {"ok": True}))
    t = Transport("ga4", lambda: {"Authorization": "Bearer x"}, sess, sleep=waits.append)
    assert t.request("POST", "https://example.test/a") == {"ok": True}
    assert waits == [7.0, 2.0]  # Retry-After honoured, then exponential backoff (2**1)
    assert t.calls == 3


def test_gives_up_after_max_retries_with_clear_error():
    sess = Scripted(*[MockResponse(500, {"error": {"message": "backend error"}})] * 3)
    t = Transport("gsc", lambda: {}, sess, sleep=lambda s: None, max_retries=2)
    with pytest.raises(SourceError) as e:
        t.request("POST", "https://example.test/q", where="searchAnalytics.query")
    assert e.value.status == 500 and "gave up after retries" in str(e.value) and "backend error" in str(e.value)
    assert t.calls == 3


def test_client_errors_are_not_retried():
    sess = Scripted(MockResponse(403, {"error": {"code": 403, "message": "no access", "status": "PERMISSION_DENIED"}}))
    t = Transport("ga4", lambda: {}, sess, sleep=lambda s: pytest.fail("should not sleep"))
    with pytest.raises(SourceError, match="ga4 API 403 on runReport: no access"):
        t.request("POST", "https://example.test", where="runReport")


def test_backoff_wait_is_capped_at_60_seconds():
    waits = []
    sess = Scripted(MockResponse(429, {}, {"Retry-After": "3600"}), MockResponse(200, {}))
    Transport("meta", lambda: {}, sess, sleep=waits.append).request("GET", "https://example.test")
    assert waits == [60.0]


def test_transport_repr_never_shows_the_token():
    t = Transport("meta", lambda: {"Authorization": "Bearer SECRET123"}, Scripted())
    assert "SECRET123" not in repr(t)


def test_missing_credentials_fail_loudly():
    with pytest.raises(MissingCredentials, match="GOOGLE_APPLICATION_CREDENTIALS"):
        google_auth_header()
    with pytest.raises(MissingCredentials, match="ads_read"):
        meta_auth_header()


def test_service_account_path_that_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "nope.json"))
    with pytest.raises(MissingCredentials, match="does not exist"):
        google_auth_header()


def test_access_token_env_vars(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", "g-token")
    monkeypatch.setenv("META_ACCESS_TOKEN", "m-token")
    assert google_auth_header()() == {"Authorization": "Bearer g-token"}
    assert meta_auth_header()() == {"Authorization": "Bearer m-token"}


def _fake_service_account(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    info = {"type": "service_account", "project_id": "demo-project", "private_key_id": "abc123", "private_key": pem,
            "client_email": "reports@demo-project.iam.gserviceaccount.com", "client_id": "1",
            "token_uri": "https://oauth2.googleapis.com/token"}
    p = tmp_path / "sa.json"
    p.write_text(json.dumps(info))
    return p, key.public_key()


@responses.activate
def test_service_account_signs_a_jwt_with_read_only_scopes_and_caches_the_token(monkeypatch, tmp_path):
    """Runs the real google-auth code path; only Google's token endpoint is faked."""
    path, public_key = _fake_service_account(tmp_path)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(path))
    responses.add(responses.POST, "https://oauth2.googleapis.com/token",
                  json={"access_token": "ya29.fake", "expires_in": 3600, "token_type": "Bearer"})
    header = google_auth_header()
    assert header() == {"Authorization": "Bearer ya29.fake"}
    assert header() == {"Authorization": "Bearer ya29.fake"}
    assert len(responses.calls) == 1  # token reused until it expires

    from urllib.parse import parse_qs
    body = responses.calls[0].request.body
    form = {k: v[0] for k, v in parse_qs(body.decode() if isinstance(body, bytes) else body).items()}
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    head_b64, claims_b64, sig_b64 = form["assertion"].split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(claims_b64)))
    assert claims["iss"] == "reports@demo-project.iam.gserviceaccount.com"
    assert sorted(claims["scope"].split()) == sorted(GOOGLE_SCOPES)
    assert all(s.endswith(".readonly") for s in claims["scope"].split())
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    public_key.verify(base64.urlsafe_b64decode(pad(sig_b64)), f"{head_b64}.{claims_b64}".encode(),
                      padding.PKCS1v15(), hashes.SHA256())  # raises if the signature is wrong
