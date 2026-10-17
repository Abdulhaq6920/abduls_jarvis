"""TOTP MFA + step-up re-auth, exercised through the real HTTP stack."""
from jarvis_backend.auth import totp

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, bearer, login


def _code(secret: str) -> str:
    return totp.totp_now(secret)


def _next_code(secret: str) -> str:
    """A code for the NEXT time step. The confirmation code is now consumed (its counter is
    persisted as last_counter at enablement), so post-enrollment authentications must use a
    code from a later step; the next counter is still inside verify()'s +/-1 window."""
    return totp.totp_now(secret, counter=totp.current_counter() + 1)


def _enroll_body():
    return {"current_password": ADMIN_PASSWORD}


def enroll_mfa(client, headers):
    """Enroll + confirm MFA for the logged-in admin. Returns (secret, recovery_codes)."""
    enroll = client.post("/api/auth/mfa/enroll", headers=headers, json=_enroll_body())
    assert enroll.status_code == 200, enroll.text
    secret = enroll.json()["secret"]
    assert enroll.json()["otpauth_uri"].startswith("otpauth://totp/JARVIS:")
    verify = client.post("/api/auth/mfa/verify", headers=headers, json={"code": _code(secret)})
    assert verify.status_code == 200, verify.text
    return secret, verify.json()["recovery_codes"]


# ------------------------------------------------------------------- enrollment
def test_enrollment_requires_a_correct_code_to_enable(client, admin):
    enroll = client.post("/api/auth/mfa/enroll", headers=admin, json=_enroll_body())
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
                       json={"mfa_token": challenge["mfa_token"], "code": _next_code(secret)})
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
    # Use a post-confirmation step (the confirmation counter is already consumed), then
    # replay that same code: the last-used counter guard must reject the second attempt.
    code = _next_code(secret)
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
    # Inline code in the decision -> succeeds (next-step code; confirmation code is spent).
    ok = client.post(f"/api/approvals/{aid}/decision", headers=admin,
                     json={"granted": True, "code": _next_code(secret)})
    assert ok.status_code == 200 and ok.json()["status"] == "GRANTED"


def test_step_up_endpoint_then_grant_within_window(client, admin):
    secret, _ = enroll_mfa(client, admin)
    aid = _pending_approval(client, "DANGEROUS")
    assert client.post("/api/auth/mfa/step-up", headers=admin, json={"code": _next_code(secret)}).status_code == 204
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
    # A MALFORMED token must normalize to the SAME 401 (not a 500 from the decoder).
    malformed = client.post("/api/auth/mfa/login",
                            json={"mfa_token": "not.a.jwt", "code": bad_code})
    # An EXPIRED / VALID-BUT-UNKNOWN-USER challenge: mint a well-formed mfa_pending token
    # for a user id that does not exist. It decodes cleanly but the account load fails ->
    # the same uniform 401 body, so the cause is not distinguishable.
    codec = client.app.state.container.codec
    unknown = codec.issue_special(sub="no-such-user", sid="x", typ="mfa_pending",
                                  ttl_seconds=300)
    unknown_user = client.post("/api/auth/mfa/login",
                               json={"mfa_token": unknown, "code": bad_code})
    for r in (wrong_code, bad_challenge, malformed, unknown_user):
        assert r.status_code == 401, r.text
    bodies = {wrong_code.json()["detail"], bad_challenge.json()["detail"],
              malformed.json()["detail"], unknown_user.json()["detail"]}
    assert len(bodies) == 1  # one non-enumerating message for every failure cause


def test_cannot_re_enroll_while_mfa_enabled(client, admin):
    """A live session alone cannot clear an active factor by starting a new enrollment
    (which would otherwise drop the account to the non-MFA high-risk path)."""
    enroll_mfa(client, admin)
    r = client.post("/api/auth/mfa/enroll", headers=admin, json=_enroll_body())
    assert r.status_code == 409
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is True
    # And the high-risk gate still bites: the account is still MFA, so a grant needs step-up.
    aid = _pending_approval(client, "DANGEROUS")
    assert client.post(f"/api/approvals/{aid}/decision", headers=admin,
                       json={"granted": True}).status_code == 401


def test_enroll_requires_current_password(client, admin):
    """A bearer token ALONE cannot provision a factor: a stolen access token with no
    password must not be able to enroll MFA and then satisfy a mandatory step-up gate."""
    # No body at all -> 422 (the password field is required by the contract).
    assert client.post("/api/auth/mfa/enroll", headers=admin).status_code == 422
    # Wrong password -> 401, and NOTHING is disclosed.
    wrong = client.post("/api/auth/mfa/enroll", headers=admin,
                        json={"current_password": "not-the-password"})
    assert wrong.status_code == 401 and "secret" not in wrong.json()
    assert client.get("/api/auth/mfa/status", headers=admin).json()["enabled"] is False
    # The correct password discloses the secret.
    ok = client.post("/api/auth/mfa/enroll", headers=admin, json=_enroll_body())
    assert ok.status_code == 200 and ok.json()["secret"]


def test_enrollment_code_cannot_be_replayed_at_first_login(client, admin):
    """Finding: the confirmation TOTP must NOT remain valid for login/step-up in the same
    30s step. After enablement, replaying the confirmation code fails closed."""
    enroll = client.post("/api/auth/mfa/enroll", headers=admin, json=_enroll_body())
    secret = enroll.json()["secret"]
    confirm_code = _code(secret)
    assert client.post("/api/auth/mfa/verify", headers=admin,
                       json={"code": confirm_code}).status_code == 200
    challenge = client.post("/api/auth/login",
                            json={"username": ADMIN_USER, "password": ADMIN_PASSWORD}).json()
    # The exact code that enabled MFA is already consumed -> rejected at login.
    replay = client.post("/api/auth/mfa/login",
                         json={"mfa_token": challenge["mfa_token"], "code": confirm_code})
    assert replay.status_code == 401


def test_recovery_code_consumed_exactly_once(client, admin):
    """A recovery hash is removed exactly once: a repeat consume of the same value fails and
    the stored list shrinks by exactly one. The orchestrated interleaving below
    (``test_recovery_consume_has_exactly_one_winner_under_interleave``) proves the stronger
    property -- single-winner when two transactions read the SAME version before either
    writes -- which this serial check cannot establish on its own."""
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


def test_enable_is_bound_to_the_verified_secret(client, admin):
    """Confirmation race: a verification against pending secret A must NOT enable a
    concurrently stored secret B. enable_mfa is bound to the exact verified secret, so an
    interleaved re-enrollment makes the stale enable fail closed."""
    container = client.app.state.container

    async def interleave():
        user = await container.auth.users.by_username(ADMIN_USER)
        await container.auth.users.set_mfa_pending(user.id, "AAAAAAAAAAAAAAAA")  # secret A
        # ... caller verified A here ...
        # Concurrent re-enrollment swaps in secret B before enable runs.
        await container.auth.users.set_mfa_pending(user.id, "BBBBBBBBBBBBBBBB")  # secret B
        # Enabling on A's proof must fail (bound to A), leaving MFA still disabled.
        enabled_on_a = await container.auth.users.enable_mfa(
            user.id, ["h"], expected_secret="AAAAAAAAAAAAAAAA", last_counter=1)
        still = (await container.auth.users.by_id(user.id)).mfa_enabled
        # Enabling on the actually-pending secret B succeeds.
        enabled_on_b = await container.auth.users.enable_mfa(
            user.id, ["h"], expected_secret="BBBBBBBBBBBBBBBB", last_counter=1)
        return enabled_on_a, still, enabled_on_b

    enabled_on_a, still_after_a, enabled_on_b = _run(interleave())
    assert enabled_on_a is False and still_after_a is False
    assert enabled_on_b is True


def test_stale_enrollment_cannot_turn_off_a_just_enabled_factor(client, admin):
    """Orchestrated race for finding #1: request A starts enrollment while the account is
    DISABLED, then request B confirms and ENABLES MFA before A's pending write lands.

        A: read MFA-disabled account; verify current password
        B: confirm pending secret; commit mfa_enabled=True
        A: set_mfa_pending() -- MUST be refused, not clear the just-activated factor

    ``set_mfa_pending`` is a conditional UPDATE guarded on ``mfa_enabled IS FALSE``, so A's
    late write affects 0 rows (returns False) and the service turns it into a 409. MFA
    stays enabled. This exercises the exact interleaving the prior test missed."""
    container = client.app.state.container
    secret, _ = enroll_mfa(client, admin)  # account is now ENABLED (request B already done)

    async def stale_write():
        user = await container.auth.users.by_username(ADMIN_USER)
        # Request A, which began while the account was disabled, finally tries to store its
        # pending secret. The conditional write must be refused because B enabled MFA.
        return await container.auth.users.set_mfa_pending(user.id, "CCCCCCCCCCCCCCCC")

    accepted = _run(stale_write())
    assert accepted is False  # fail closed: the stale pending write is rejected
    status = client.get("/api/auth/mfa/status", headers=admin).json()
    assert status["enabled"] is True  # the activated factor is untouched
    # The secret A tried to store never took effect: the still-live factor is the real one.
    secret_now = _run(_read_secret(container))
    assert secret_now == secret and secret_now != "CCCCCCCCCCCCCCCC"


async def _read_secret(container):
    user = await container.auth.users.by_username(ADMIN_USER)
    return (await container.auth.users.by_id(user.id)).mfa_secret


def test_enable_confirmation_has_exactly_one_winner_under_interleave(client, admin):
    """Finding #2 (enable): two confirmations that read the SAME pending state before
    either commits must NOT both reveal a recovery set. ``enable_mfa`` is a version
    compare-and-set, so exactly one UPDATE affects a row -- on SQLite (no FOR UPDATE) too.
    Run concurrently with ``asyncio.gather`` so both SELECT the same version first."""
    import asyncio
    container = client.app.state.container

    async def race():
        user = await container.auth.users.by_username(ADMIN_USER)
        await container.auth.users.set_mfa_pending(user.id, "ZZZZZZZZZZZZZZZZ")
        # Two concurrent confirmations of the SAME pending secret, each with its OWN
        # (distinct) recovery set. Only one may win and reveal its codes.
        results = await asyncio.gather(
            container.auth.users.enable_mfa(user.id, ["setA"],
                                            expected_secret="ZZZZZZZZZZZZZZZZ", last_counter=1),
            container.auth.users.enable_mfa(user.id, ["setB"],
                                            expected_secret="ZZZZZZZZZZZZZZZZ", last_counter=1),
        )
        stored = (await container.auth.users.by_id(user.id)).mfa_recovery_hashes
        return results, stored

    results, stored = _run(race())
    assert sum(bool(r) for r in results) == 1  # exactly one confirmation enabled MFA
    assert stored in (["setA"], ["setB"])  # the single winner's set, never a mix/overwrite


def test_recovery_consume_has_exactly_one_winner_under_interleave(client, admin):
    """Finding #2 (recovery): two redemptions of the SAME code that read the SAME version
    before either writes must yield exactly ONE success. The version compare-and-set gives
    single-winner without FOR UPDATE, so it holds on SQLite. Run concurrently so both read
    the same recovery list first."""
    import asyncio

    from jarvis_backend.auth.passwords import verify_hash
    _, codes = enroll_mfa(client, admin)
    code = codes[0]
    container = client.app.state.container

    async def race():
        user = await container.auth.users.by_username(ADMIN_USER)
        hashes = (await container.auth.users.by_id(user.id)).mfa_recovery_hashes
        before = len(hashes)
        target = None
        for h in hashes:
            if await verify_hash(h, code):
                target = h
                break
        assert target is not None
        results = await asyncio.gather(
            container.auth.users.consume_recovery_hash(user.id, target),
            container.auth.users.consume_recovery_hash(user.id, target),
        )
        after = len((await container.auth.users.by_id(user.id)).mfa_recovery_hashes)
        return results, before, after

    results, before, after = _run(race())
    assert sum(bool(r) for r in results) == 1  # single-use even under concurrency
    assert after == before - 1  # exactly one hash removed


def test_stale_disable_through_the_service_keeps_a_rotated_factor(client, admin):
    """Finding #1 end-to-end: ``AuthService.disable_mfa`` binds the clear to the factor it
    authenticated. We force the exact race by rotating the factor DURING the second-factor
    check (the point between loading the user and clearing it): the disable must fail closed
    (401) and the freshly rotated factor must remain enabled."""
    container = client.app.state.container
    auth = container.auth
    secret, codes = enroll_mfa(client, admin)

    async def race():
        from jarvis_backend.auth.service import Principal
        user = await auth.users.by_username(ADMIN_USER)
        principal = Principal(subject=user.id, role="admin", sid="x", kind="user")
        original_check = auth._check_second_factor

        async def check_then_rotate(u, code):
            ok = await original_check(u, code)
            if ok:
                # Request B lands here (between A's factor check and A's clear): disable the
                # proved generation and enroll+enable a replacement, all before A's clear.
                fresh = await auth.users.by_id(u.id)
                await auth.users.disable_mfa(u.id, expected_version=fresh.mfa_state_version)
                await auth.users.set_mfa_pending(u.id, "REPLACEMENTSECRET")
                await auth.users.enable_mfa(u.id, ["replacement"],
                                            expected_secret="REPLACEMENTSECRET", last_counter=1)
            return ok

        auth._check_second_factor = check_then_rotate
        try:
            raised = None
            try:
                await auth.disable_mfa(principal, ADMIN_PASSWORD, codes[1])
            except Exception as exc:  # noqa: BLE001 - we assert on the HTTP status below
                raised = exc
            row = await auth.users.by_id(user.id)
            return raised, row.mfa_enabled, row.mfa_secret
        finally:
            auth._check_second_factor = original_check

    raised, still_enabled, secret_now = _run(race())
    assert raised is not None and getattr(raised, "status_code", None) == 401
    assert still_enabled is True  # the rotated-in replacement survived the stale clear
    assert secret_now == "REPLACEMENTSECRET"


def test_malformed_bearer_token_is_401_not_500(client):
    """A malformed JWT on a protected route normalizes to 401 (not a decoder 500)."""
    assert client.get("/api/auth/me", headers=bearer("not-a-valid-jwt")).status_code == 401
    assert client.get("/api/auth/me", headers=bearer("a.b.c")).status_code == 401


def test_totp_counter_advance_rejects_stale_previous(client, admin):
    """Compare-and-set: an advance whose ``previous`` no longer matches the stored counter
    loses, so a replayed step cannot re-advance after the counter already moved on."""
    from jarvis_backend.auth import totp
    enroll_mfa(client, admin)
    container = client.app.state.container

    async def race():
        user = await container.auth.users.by_username(ADMIN_USER)
        start = user.mfa_last_counter  # the confirmation counter, already stored
        # Both callers read the SAME ``start`` and target the SAME next counter.
        target = (start or totp.current_counter()) + 1
        first = await container.auth.users.advance_mfa_counter(user.id, target, start)
        # A second caller that read the SAME old ``start`` now loses the compare-and-set
        # (the stored counter has moved to ``target`` and ``target < target`` is false).
        second = await container.auth.users.advance_mfa_counter(user.id, target, start)
        return first, second

    first, second = _run(race())
    assert first is True and second is False
