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


def _admin_session_id(client):
    """The current admin's (only) live session id, read through the store."""
    container = client.app.state.container

    async def find():
        user = await container.auth.users.by_username(ADMIN_USER)
        rows = await container.auth.sessions.list_for_user(user.id)
        return rows[0].id

    return _run(find())


def _set_step_up_at(client, session_id, moment):
    container = client.app.state.container
    _run(container.auth.sessions.mark_step_up(session_id, at=moment))


def test_stale_step_up_is_refused_but_fresh_within_window_succeeds(client, admin):
    from datetime import timedelta

    from jarvis_backend.storage.db import utcnow
    enroll_mfa(client, admin)
    sid = _admin_session_id(client)
    window = client.app.state.container.settings.step_up_window_seconds

    # A step-up older than the window must NOT authorize a high-risk grant.
    _set_step_up_at(client, sid, utcnow() - timedelta(seconds=window + 60))
    stale_aid = _pending_approval(client, "DANGEROUS")
    r = client.post(f"/api/approvals/{stale_aid}/decision", headers=admin, json={"granted": True})
    assert r.status_code == 401
    assert client.get(f"/api/approvals/{stale_aid}", headers=admin).json()["status"] == "PENDING"

    # A step-up well inside the window authorizes it.
    _set_step_up_at(client, sid, utcnow() - timedelta(seconds=1))
    fresh_aid = _pending_approval(client, "DANGEROUS")
    ok = client.post(f"/api/approvals/{fresh_aid}/decision", headers=admin, json={"granted": True})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_future_dated_step_up_is_refused(client, admin):
    from datetime import timedelta

    from jarvis_backend.storage.db import utcnow
    enroll_mfa(client, admin)
    sid = _admin_session_id(client)
    # A future step_up_at (clock rollback / tampering) has a negative age and must fail closed,
    # not be treated as "not yet expired".
    _set_step_up_at(client, sid, utcnow() + timedelta(seconds=3600))
    aid = _pending_approval(client, "DANGEROUS")
    r = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert r.status_code == 401
    assert client.get(f"/api/approvals/{aid}", headers=admin).json()["status"] == "PENDING"


def test_unknown_risk_cannot_be_granted(client, admin):
    """A stored risk outside the known enum fails closed (denied + audited), never waved through."""
    container = client.app.state.container

    async def make():
        a = await container.approvals.create("vision", "mystery op", "WEIRD", {"cmd": "x"})
        return a.id
    aid = _run(make())
    r = client.post(f"/api/approvals/{aid}/decision", headers=admin, json={"granted": True})
    assert r.status_code == 403
    assert client.get(f"/api/approvals/{aid}", headers=admin).json()["status"] == "PENDING"
    assert "approval_step_up_denied" in client.get("/api/audit", headers=admin).text


def test_disable_with_password_only_is_refused_401(client, admin):
    """Password alone (no code) must be denied with 401 -- not a 422 validation bounce, and
    never a success."""
    enroll_mfa(client, admin)
    r = client.post("/api/auth/mfa/disable", headers=admin,
                    json={"current_password": ADMIN_PASSWORD})
    assert r.status_code == 401
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is True


def test_mfa_login_failures_are_non_enumerating(client, admin):
    """An expired/invalid challenge and a valid challenge with a wrong code return the SAME
    401 body, so an attacker cannot tell which failed."""
    secret, _ = enroll_mfa(client, admin)
    challenge = client.post("/api/auth/login",
                            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    bad_code = "000000" if _code(secret) != "000000" else "111111"
    # Valid challenge + wrong code.
    wrong_code = client.post("/api/auth/mfa/login",
                             json={"mfa_token": challenge["mfa_token"], "code": bad_code})
    # A structurally valid but wrong-typ token (an access token) stands in for an
    # expired/invalid challenge: it must fail with the SAME 401 body, not a distinguishable one.
    access = admin["Authorization"].split(" ", 1)[1]
    bad_challenge = client.post("/api/auth/mfa/login",
                                json={"mfa_token": access, "code": bad_code})
    assert wrong_code.status_code == 401 and bad_challenge.status_code == 401
    assert wrong_code.json() == bad_challenge.json()


def test_cannot_re_enroll_while_mfa_enabled(client, admin):
    """A live session alone cannot clear an active factor by starting a new enrollment
    (which would otherwise drop the account to the non-MFA high-risk path)."""
    enroll_mfa(client, admin)
    r = client.post("/api/auth/mfa/enroll", headers=admin)
    assert r.status_code == 409
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is True
    # And the high-risk gate still bites: the account is still MFA, so a grant needs step-up.
    aid = _pending_approval(client, "DANGEROUS")
    assert client.post(f"/api/approvals/{aid}/decision", headers=admin,
                       json={"granted": True}).status_code == 401


def test_recovery_code_consumed_exactly_once(client, admin):
    """A recovery hash is removed exactly once: a repeat consume of the same value fails and
    the stored list shrinks by exactly one. (In-process the app serializes requests through
    the event loop and SQLite's single writer; on a concurrent backend the FOR UPDATE row
    lock serializes the read-modify-write.)"""
    from jarvis_backend.auth.passwords import verify_hash
    _, codes = enroll_mfa(client, admin)
    code = codes[0]
    container = client.app.state.container

    async def consume_twice():
        user = await container.auth.users.by_username(ADMIN_USER)
        hashes = (await container.auth.users.by_id(user.id)).mfa_recovery_hashes
        before = len(hashes)
        target = None
        for h in hashes:
            if await verify_hash(h, code):
                target = h
                break
        assert target is not None
        first = await container.auth.users.consume_recovery_hash(user.id, target)
        second = await container.auth.users.consume_recovery_hash(user.id, target)
        after = len((await container.auth.users.by_id(user.id)).mfa_recovery_hashes)
        return first, second, before, after

    first, second, before, after = _run(consume_twice())
    assert first is True and second is False  # single-use
    assert after == before - 1  # exactly one removed


def test_totp_counter_advance_rejects_stale_previous(client, admin):
    """Compare-and-set: an advance whose ``previous`` no longer matches the stored counter
    loses, so a replayed step cannot re-advance after the counter already moved on."""
    from jarvis_backend.auth import totp
    enroll_mfa(client, admin)
    container = client.app.state.container

    async def race():
        user = await container.auth.users.by_username(ADMIN_USER)
        counter = totp.current_counter()
        start = user.mfa_last_counter
        first = await container.auth.users.advance_mfa_counter(user.id, counter, start)
        # A second caller that read the SAME old ``start`` now loses the compare-and-set.
        second = await container.auth.users.advance_mfa_counter(user.id, counter, start)
        return first, second

    first, second = _run(race())
    assert first is True and second is False
