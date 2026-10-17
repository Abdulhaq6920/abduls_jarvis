"""Authentication & authorisation, exercised through the real HTTP stack."""
from datetime import timedelta

import jwt
import pytest

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, JWT_SECRET, bearer, login

PROTECTED = [("GET", "/api/agents"), ("GET", "/api/memory"), ("GET", "/api/approvals"),
             ("GET", "/api/jobs"), ("GET", "/api/audit"), ("GET", "/api/system/status"),
             ("GET", "/api/devices"), ("GET", "/api/auth/me"), ("POST", "/api/assist")]


# --------------------------------------------------------------------- the front door
@pytest.mark.parametrize("method, path", PROTECTED)
def test_every_protected_route_rejects_anonymous(client, method, path):
    assert client.request(method, path, json={}).status_code == 401


def test_public_endpoints_are_minimal(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json()["ready"] is True
    assert client.get("/metrics").status_code == 401  # needs its own token


# --------------------------------------------------------------------------- login
def test_login_success_sets_hardened_refresh_cookie(client):
    r = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/auth" in cookie
    assert "refresh_token" not in r.text  # the refresh secret never appears in the JSON body


def test_login_failures_are_indistinguishable(client):
    wrong_pw = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "x" * 14})
    no_user = client.post("/api/auth/login", json={"username": "nobody", "password": "x" * 14})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()  # no user enumeration


def test_brute_force_is_throttled(client):
    codes = [client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "wrong-pass-123"}
                         ).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert "retry-after" in client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "x"}).headers
    # Even the CORRECT password is refused while throttled: guessing gains nothing.
    assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
                       ).status_code == 429


def test_oversized_password_rejected_before_hashing(client):
    r = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": "p" * 5000})
    assert r.status_code == 422


# ------------------------------------------------------------------------ JWT checks
def forged(client, **claims):
    codec = client.app.state.container.codec
    payload = {"iss": "jarvis", "aud": "jarvis-api", "sub": "x", "role": "admin", "sid": "s",
               "jti": "j", "typ": "access", "iat": 1, "nbf": 1, "exp": 4_000_000_000, **claims}
    return payload, codec


def test_token_signed_with_wrong_secret_is_rejected(client):
    payload, codec = forged(client)
    kid = jwt.get_unverified_header(codec.issue(sub="a", role="admin", sid="s", ttl_seconds=60))["kid"]
    token = jwt.encode(payload, "an-attacker-chosen-secret-0123456789", algorithm="HS256", headers={"kid": kid})
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_alg_none_token_is_rejected(client):
    payload, codec = forged(client)
    kid = jwt.get_unverified_header(codec.issue(sub="a", role="admin", sid="s", ttl_seconds=60))["kid"]
    for headers in ({}, {"kid": kid}):
        token = jwt.encode(payload, key="", algorithm="none", headers=headers)
        assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("claims", [
    {"aud": "someone-else"}, {"iss": "evil"}, {"typ": "refresh"}, {"exp": 1},
])
def test_token_with_bad_claims_is_rejected(client, claims):
    payload, codec = forged(client, **claims)
    kid = jwt.get_unverified_header(codec.issue(sub="a", role="admin", sid="s", ttl_seconds=60))["kid"]
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256", headers={"kid": kid})
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_validly_signed_token_for_a_nonexistent_session_is_rejected(client):
    """A correct signature is not enough: the session must still be live."""
    token = client.app.state.container.codec.issue(sub="ghost", role="admin", sid="no-such-session", ttl_seconds=60)
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_previous_signing_secret_still_verifies_during_rotation(settings):
    from jarvis_backend.auth.tokens import TokenCodec
    old = TokenCodec("old-secret-" + "a1B2c3" * 6, [], "jarvis", "jarvis-api")
    rotated = TokenCodec("new-secret-" + "z9Y8x7" * 6, ["old-secret-" + "a1B2c3" * 6], "jarvis", "jarvis-api")
    assert rotated.verify(old.issue(sub="u", role="admin", sid="s", ttl_seconds=60)).sub == "u"
    from jarvis_backend.auth.tokens import TokenError
    with pytest.raises(TokenError, match="unknown signing key"):  # secret dropped => no longer valid
        TokenCodec("new-secret-" + "z9Y8x7" * 6, [], "jarvis", "jarvis-api").verify(
            old.issue(sub="u", role="admin", sid="s", ttl_seconds=60))


# ------------------------------------------------------------- logout & refresh rotation
def test_logout_revokes_the_access_token_immediately(client):
    headers = bearer(login(client))
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    # Without the dashboard's header a cookie-bearing POST looks cross-site and is refused.
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.post("/api/auth/logout", headers={**headers, "X-Requested-With": "jarvis-ui"}).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401  # not "valid until it expires"


def test_refresh_rotates_the_token_and_replay_revokes_the_family(client, monkeypatch):
    # Negative grace, not timedelta(0): the replay can land in the same clock tick as the
    # rotation, making `elapsed > timedelta(0)` false and reuse detection flaky. A negative
    # grace makes any replay (even same-instant) unambiguously past the window.
    monkeypatch.setattr("jarvis_backend.auth.service.REFRESH_REUSE_GRACE", timedelta(seconds=-1))
    assert client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).status_code == 200
    stolen = client.cookies.get("jarvis_refresh")
    ui = {"X-Requested-With": "jarvis-ui"}

    second = client.post("/api/auth/refresh", headers=ui)
    assert second.status_code == 200
    legit_access = bearer(second.json()["access_token"])
    assert client.cookies.get("jarvis_refresh") != stolen  # rotated
    assert client.get("/api/auth/me", headers=legit_access).status_code == 200

    # An attacker replays the OLD refresh token -> theft is assumed, everything is revoked.
    client.cookies.clear()
    client.cookies.set("jarvis_refresh", stolen, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=ui).status_code == 401
    assert client.get("/api/auth/me", headers=legit_access).status_code == 401
    events = [e["event"] for e in client.get("/api/audit", headers=bearer(login_after_throttle(client))).json()]
    assert "refresh_reuse_detected" in events


def login_after_throttle(client):
    return login(client)


def test_refresh_blocks_cross_site_requests(client):
    client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    assert client.post("/api/auth/refresh", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/refresh").status_code == 403  # cookie present, no UI header
    assert client.post("/api/auth/refresh", headers={"X-Requested-With": "jarvis-ui"}).status_code == 200


def test_refresh_without_a_cookie_fails(client):
    assert client.post("/api/auth/refresh", headers={"X-Requested-With": "jarvis-ui"}).status_code == 401


def test_password_change_ends_every_session(client):
    headers = bearer(login(client))
    other = bearer(client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}
                               ).json()["access_token"])
    weak = client.post("/api/auth/change-password", headers=headers,
                       json={"current_password": ADMIN_PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    wrong = client.post("/api/auth/change-password", headers=headers,
                        json={"current_password": "not-my-password", "new_password": "a brand new long passphrase"})
    assert wrong.status_code == 401
    ok = client.post("/api/auth/change-password", headers=headers,
                     json={"current_password": ADMIN_PASSWORD, "new_password": "a brand new long passphrase"})
    assert ok.status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.post("/api/auth/login", json={"username": ADMIN_USER,
                                                "password": "a brand new long passphrase"}).status_code == 200


# ----------------------------------------------------------------------------- devices
def test_device_role_is_limited(client, agent, admin):
    assert client.post("/api/assist", json={"session_id": "s", "utterance": "hello"}, headers=agent).status_code == 200
    for method, path in [("GET", "/api/memory"), ("GET", "/api/approvals"), ("GET", "/api/devices"),
                         ("GET", "/api/audit"), ("POST", "/api/devices"), ("GET", "/api/system/status")]:
        assert client.request(method, path, headers=agent, json={"name": "x"}).status_code == 403, path


def test_device_secret_is_shown_once_and_stored_hashed(client, admin, device):
    listing = client.get("/api/devices", headers=admin).json()
    assert device["secret"] not in str(listing)
    assert listing[0]["id"] == device["id"]


def test_wrong_device_secret_fails(client, device):
    r = client.post("/api/auth/device-token", json={"device_id": device["id"], "secret": "wrong"})
    assert r.status_code == 401


def test_revoking_a_device_kills_its_live_token_immediately(client, admin, device):
    assert client.post("/api/assist", json={"session_id": "s", "utterance": "hi"}, headers=device["headers"]).status_code == 200
    assert client.delete(f"/api/devices/{device['id']}", headers=admin).status_code == 204
    assert client.post("/api/assist", json={"session_id": "s", "utterance": "hi"}, headers=device["headers"]).status_code == 401
    assert client.post("/api/auth/device-token", json={"device_id": device["id"], "secret": device["secret"]}).status_code == 401


# --------------------------------------------------------------------- HTTP hardening
def test_security_headers_and_no_store(client, admin):
    r = client.get("/api/agents", headers=admin)
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store" and len(r.headers["x-request-id"]) >= 8


def test_request_id_is_propagated_when_valid_and_replaced_when_not(client):
    assert client.get("/healthz", headers={"X-Request-ID": "trace-abc-12345"}).headers["x-request-id"] == "trace-abc-12345"
    assert client.get("/healthz", headers={"X-Request-ID": "bad id\nwith injection"}).headers["x-request-id"] != "bad id"


def test_unknown_host_header_is_refused(client):
    assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "localhost:8077"}).status_code == 200


def test_container_healthcheck_works_when_only_the_public_domain_is_allowed(settings):
    """Regression: with ALLOWED_HOSTS=<public domain>, the in-container probe to 127.0.0.1
    must still succeed or every container is marked unhealthy and restart-looped."""
    from fastapi.testclient import TestClient
    from jarvis_backend.app import create_app
    strict = settings.model_copy(update={"allowed_hosts": "jarvis.example.com"})
    with TestClient(create_app(strict), base_url="http://127.0.0.1:8077") as probe:
        assert probe.get("/healthz").status_code == 200
        assert probe.get("/healthz", headers={"Host": "jarvis.example.com"}).status_code == 200
        assert probe.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400


def test_oversized_body_is_rejected(client, admin):
    r = client.post("/api/assist", content=b"x" * 200_000, headers={**admin, "content-type": "application/json"})
    assert r.status_code == 413


def test_errors_do_not_leak_internals(client, admin, monkeypatch):
    from fastapi.testclient import TestClient

    async def boom(*a, **k):
        raise RuntimeError("secret internal detail /etc/passwd")
    from jarvis_backend.core.supervisor import Supervisor
    monkeypatch.setattr(Supervisor, "handle", boom)  # class-level: a 2nd app start rebuilds the container
    # raise_server_exceptions=False: behave like a real server, which answers 500 and moves on.
    with TestClient(client.app, base_url="http://localhost", raise_server_exceptions=False) as real:
        r = real.post("/api/assist", json={"session_id": "s", "utterance": "hi"}, headers=admin)
    assert r.status_code == 500 and "passwd" not in r.text and r.json()["request_id"]
