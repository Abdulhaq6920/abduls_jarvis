"""Unit tests for the stdlib RFC 6238 TOTP module and the special token kinds."""
import pytest
from jarvis_backend.auth import totp
from jarvis_backend.auth.tokens import ACCESS, MFA_PENDING, TokenCodec, TokenError

SECRET_KEY = "unit-test-signing-key-" + "q9Wx2" * 6


def test_generate_verify_round_trip():
    secret = totp.generate_secret()
    code = totp.totp_now(secret)
    assert totp.verify(secret, code) is not None


def test_wrong_code_is_rejected():
    secret = totp.generate_secret()
    now = totp.current_counter()
    bad = "000000" if totp.totp_now(secret) != "000000" else "111111"
    assert totp.verify(secret, bad) is None
    assert totp.verify(secret, "12345") is None   # too short
    assert totp.verify(secret, "abcdef") is None   # not digits
    # A code from well outside the window is rejected.
    assert totp.verify(secret, totp.totp_now(secret, now + 10)) is None


def test_window_tolerates_one_step_of_drift():
    secret = totp.generate_secret()
    now = totp.current_counter()
    assert totp.verify(secret, totp.totp_now(secret, now - 1)) == now - 1
    assert totp.verify(secret, totp.totp_now(secret, now + 1)) == now + 1


def test_replay_in_the_same_step_is_rejected():
    secret = totp.generate_secret()
    counter = totp.current_counter()
    code = totp.totp_now(secret, counter)
    matched = totp.verify(secret, code)
    assert matched == counter
    # Presenting the same (or an earlier) counter again is refused once it has been seen.
    assert totp.verify(secret, code, last_counter=matched) is None


def test_otpauth_uri_shape():
    uri = totp.otpauth_uri("ABCDEF234567", account="owner", issuer="JARVIS")
    assert uri.startswith("otpauth://totp/JARVIS:owner?")
    assert "secret=ABCDEF234567" in uri and "issuer=JARVIS" in uri
    assert "algorithm=SHA1" in uri and "digits=6" in uri and "period=30" in uri


# ----------------------------------------------------------------- token separation
def _codec() -> TokenCodec:
    return TokenCodec(SECRET_KEY, [], "jarvis", "jarvis-api")


def test_verify_rejects_an_mfa_pending_token_as_access():
    codec = _codec()
    token = codec.issue_special(sub="u", sid="s", typ=MFA_PENDING, ttl_seconds=60)
    with pytest.raises(TokenError, match="wrong token type"):
        codec.verify(token)   # an mfa_pending token can NEVER authenticate a request


def test_verify_special_requires_its_own_typ():
    codec = _codec()
    access = codec.issue(sub="u", role="admin", sid="s", ttl_seconds=60)
    pending = codec.issue_special(sub="u", sid="s", typ=MFA_PENDING, ttl_seconds=60)
    assert codec.verify_special(pending, expected_typ=MFA_PENDING).sub == "u"
    with pytest.raises(TokenError, match="wrong token type"):
        codec.verify_special(access, expected_typ=MFA_PENDING)
    with pytest.raises(TokenError, match="wrong token type"):
        codec.verify_special(pending, expected_typ=ACCESS)   # access is never a "special" typ
