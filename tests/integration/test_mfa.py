"""TOTP MFA + step-up re-auth, exercised through the real HTTP stack."""
from jarvis_backend.auth import totp

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, bearer, login


def _code(secret: str) -> str:
    return totp.totp_now(secret)


def enroll_mfa(client, headers):
    """Enroll + confirm MFA for the logged-in admin. Returns (secret, recovery_codes)."""
    enroll = client.post("/api/auth/mfa/enroll", headers=headers)
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    assert enroll.json()["otpauth_uri"].startswith("otpauth://totp/JARVIS:")
    verify = client.post("/api/auth/mfa/verify", headers=headers, json={"code": _code(secret)})
    assert verify.status_code == 200, verify.text
    return secret, verify.json()["recovery_codes"]


# ------------------------------------------------------------------- enrollment
def test_enrollment_requires_a_correct_code_to_enable(client, admin):
    enroll = client.post("/api/auth/mfa/enroll", headers=admin)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    # A wrong code must NOT enable MFA.
    bad = "000000" if _code(secret) != "000000" else "111111"
    assert client.post("/api/auth/mfa/verify", headers=admin, json={"code": bad}).status_code == 401
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is False
    # The correct code enables it and returns recovery codes once.
    ok = client.post("/api/auth/mfa/verify", headers=admin, json={"code": _code(secret)})
    assert ok.status_code == 200 and len(ok.json()["recovery_codes"]) == 10
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is True


def test_secret_is_not_returned_after_confirmation(client, admin):
    secret, codes = enroll_mfa(client, admin)
    status = client.get("/api/auth/mfa/status", headers=admin).json()
    assert "secret" not in status and secret not in str(status)
    # The audit trail must not carry the secret or any recovery code either.
    audit = client.get("/api/audit", headers=admin).text
    assert secret not in audit
    assert all(rc not in audit for rc in codes)


# ------------------------------------------------------------------- login with MFA
def test_login_with_mfa_needs_the_second_factor(client, admin):
    secret, _ = enroll_mfa(client, admin)
    r = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
    body = r.json()
    assert r.status_code == 200 and body.get("mfa_required") is True
    assert "access_token" not in body and body["mfa_token"]
    assert "set-cookie" not in {k.lower() for k in r.headers}  # no refresh cookie on the challenge


def test_mfa_pending_token_is_rejected_on_protected_routes(client, admin):
    enroll_mfa(client, admin)
    challenge = client.post("/api/auth/login",
                            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    # The single-purpose token is NOT an access token: no bypass.
    assert client.get("/api/auth/me", headers=bearer(challenge["mfa_token"])).status_code == 401
    assert client.get("/api/agents", headers=bearer(challenge["mfa_token"])).status_code == 401


def test_second_factor_completes_login(client, admin):
    secret, _ = enroll_mfa(client, admin)
    challenge = client.post("/api/auth/login",
                            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    bad = client.post("/api/auth/mfa/login",
                      json={"mfa_token": challenge["mfa_token"], "code": "123456"})
    assert bad.status_code == 401
    good = client.post("/api/auth/mfa/login",
                       json={"mfa_token": challenge["mfa_token"], "code": _code(secret)})
    assert good.status_code == 200 and good.json()["access_token"]
    cookie = good.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me", headers=bearer(good.json()["access_token"])).status_code == 200


def test_recovery_code_works_once(client, admin):
    _, codes = enroll_mfa(client, admin)
    code = codes[0]
    first = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    r1 = client.post("/api/auth/mfa/login", json={"mfa_token": first["mfa_token"], "code": code})
    assert r1.status_code == 200
    second = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    r2 = client.post("/api/auth/mfa/login", json={"mfa_token": second["mfa_token"], "code": code})
    assert r2.status_code == 401   # single-use: the same recovery code cannot be replayed


def test_replayed_totp_in_the_same_step_is_rejected(client, admin):
    secret, _ = enroll_mfa(client, admin)
    code = _code(secret)
    first = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    assert client.post("/api/auth/mfa/login", json={"mfa_token": first["mfa_token"], "code": code}).status_code == 200
    second = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    # Same code, same 30s step -> rejected by the last-used counter guard.
    assert client.post("/api/auth/mfa/login", json={"mfa_token": second["mfa_token"], "code": code}).status_code == 401


def test_mfa_step_is_rate_limited(client, admin):
    secret, _ = enroll_mfa(client, admin)
    challenge = client.post("/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    codes = [client.post("/api/auth/mfa/login",
                         json={"mfa_token": challenge["mfa_token"], "code": "000000"}).status_code
             for _ in range(6)]
    assert 429 in codes  # the three login limits apply to the MFA step too
    # Even a correct code is refused while throttled.
    assert client.post("/api/auth/mfa/login",
                       json={"mfa_token": challenge["mfa_token"], "code": _code(secret)}).status_code == 429


# ------------------------------------------------------------------- disable
def test_disable_requires_password_and_second_factor(client, admin):
    secret, codes = enroll_mfa(client, admin)
    # Wrong password -> refused even with a valid code.
    assert client.post("/api/auth/mfa/disable", headers=admin,
                       json={"current_password": "nope-nope-nope", "code": _code(secret)}).status_code == 401
    # Right password, wrong code -> refused (never password alone).
    assert client.post("/api/auth/mfa/disable", headers=admin,
                       json={"current_password": ADMIN_PASSWORD, "code": "000000"}).status_code == 401
    # Both correct (use a recovery code) -> disabled.
    ok = client.post("/api/auth/mfa/disable", headers=admin,
                     json={"current_password": ADMIN_PASSWORD, "code": codes[0]})
    assert ok.status_code == 204
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is False
    # Login is single-step again.
    assert client.post("/api/auth/login",
                       json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json().get("mfa_required") is None


# ------------------------------------------------------------------- step-up on approvals
def _run(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _pending_approval(client, risk="DANGEROUS"):
    """Create a PENDING approval directly through the store and return its id."""
    container = client.app.state.container

    async def make():
        a = await container.approvals.create("vision", "rm -rf important things", risk, {"cmd": "x"})
        return a.id

    return _run(make())


def test_high_risk_grant_requires_step_up_for_mfa_admin(client, admin):
    secret, _ = enroll_mfa(client, admin)
    aid = _pending_approval(client, "DANGEROUS")
    # No fresh step-up -> refused.
    r = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert r.status_code == 401
    # Still pending (fail closed: the grant did not go through).
    assert client.get(f"/api/approvals/{aid}", headers=admin).json()["status"] == "PENDING"
    # Inline code in the decision -> succeeds.
    ok = client.post(f"/api/approvals/{aid}/decision", headers=admin,
                     json={"granted": True, "code": _code(secret)})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_step_up_endpoint_then_grant_within_window(client, admin):
    secret, _ = enroll_mfa(client, admin)
    aid = _pending_approval(client, "DANGEROUS")
    assert client.post("/api/auth/mfa/step-up", headers=admin, json={"code": _code(secret)}).status_code == 204
    ok = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_safe_grant_needs_no_step_up(client, admin):
    enroll_mfa(client, admin)
    aid = _pending_approval(client, "SAFE")
    ok = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_non_mfa_admin_still_approves_high_risk(client, admin):
    aid = _pending_approval(client, "DANGEROUS")
    ok = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_require_mfa_flag_blocks_non_mfa_admin(client, admin):
    client.app.state.container.settings.approval_require_mfa = True
    try:
        aid = _pending_approval(client, "DANGEROUS")
        r = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
        assert r.status_code == 403
        assert client.get(f"/api/approvals/{aid}", headers=admin).json()["status"] == "PENDING"
    finally:
        client.app.state.container.settings.approval_require_mfa = False


def test_separation_of_duties_holds_for_agent(client, admin, agent):
    aid = _pending_approval(client, "DANGEROUS")
    # The agent/device role can never decide an approval, MFA or not.
    assert client.post(f"/api/approvals/{aid}/decision", headers=agent, json={"granted": True}).status_code == 403
