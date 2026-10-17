"""Authentication routes: login, refresh, logout, password change, device tokens.

Token handling for the browser:

* the short-lived **access token** is returned in the JSON body and kept in JavaScript
  memory only (never localStorage, so an XSS cannot read it from storage);
* the **refresh token** lives in an ``HttpOnly; SameSite=Strict`` cookie scoped to
  ``/api/auth`` (JavaScript cannot read it; it is sent nowhere else).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from jarvis_backend.api.deps import get_container
from jarvis_backend.auth.deps import (
    admin_only,
    client_ip,
    current_principal,
    verify_origin,
)
from jarvis_backend.auth.service import IssuedTokens, MfaRequired, Principal
from jarvis_backend.container import Container
from jarvis_backend.schemas_auth import (
    ChangePasswordIn,
    DeviceTokenIn,
    LoginIn,
    MeOut,
    MfaDisableIn,
    MfaEnableOut,
    MfaEnrollIn,
    MfaEnrollOut,
    MfaLoginIn,
    MfaRequiredOut,
    MfaStatusOut,
    MfaVerifyIn,
    SessionOut,
    TokenOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE_PATH = "/api/auth"


def _set_refresh_cookie(response: Response, c: Container, issued: IssuedTokens) -> None:
    response.set_cookie(
        c.settings.cookie_name, issued.refresh_secret or "", httponly=True,
        secure=c.settings.secure_cookies, samesite="strict", path=COOKIE_PATH,
        max_age=c.settings.refresh_token_days * 86400,
    )


def _token_out(issued: IssuedTokens) -> TokenOut:
    return TokenOut(access_token=issued.access_token, expires_in=issued.expires_in,
                    username=issued.username, role=issued.role)


@router.post("/login", response_model=None)
async def login(body: LoginIn, request: Request, response: Response,
                c: Container = Depends(get_container)) -> TokenOut | MfaRequiredOut:
    """Password login. For an MFA account this returns an ``mfa_required`` challenge (no
    tokens, no refresh cookie); the caller then posts to ``/mfa/login`` with a code."""
    issued = await c.auth.login(body.username, body.password, client_ip(request),
                                request.headers.get("user-agent", ""))
    if isinstance(issued, MfaRequired):
        return MfaRequiredOut(mfa_token=issued.mfa_token, expires_in=issued.expires_in)
    _set_refresh_cookie(response, c, issued)
    return _token_out(issued)


@router.post("/mfa/login", response_model=TokenOut)
async def mfa_login(body: MfaLoginIn, request: Request, response: Response,
                    c: Container = Depends(get_container)) -> TokenOut:
    """Second login step: redeem the mfa_pending token with a TOTP or recovery code."""
    issued = await c.auth.complete_mfa_login(body.mfa_token, body.code, client_ip(request),
                                             request.headers.get("user-agent", ""))
    _set_refresh_cookie(response, c, issued)
    return _token_out(issued)


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(verify_origin)])
async def refresh(request: Request, response: Response,
                  c: Container = Depends(get_container)) -> TokenOut:
    issued = await c.auth.refresh(request.cookies.get(c.settings.cookie_name), client_ip(request),
                                  request.headers.get("user-agent", ""))
    _set_refresh_cookie(response, c, issued)
    return _token_out(issued)


@router.post("/logout", status_code=204, dependencies=[Depends(verify_origin)])
async def logout(response: Response, principal: Principal = Depends(current_principal),
                 c: Container = Depends(get_container)) -> Response:
    await c.auth.logout(principal)
    response.delete_cookie(c.settings.cookie_name, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.post("/logout-all", dependencies=[Depends(verify_origin)])
async def logout_all(principal: Principal = admin_only,
                     c: Container = Depends(get_container)) -> dict:
    return {"revoked_sessions": await c.auth.logout_everywhere(principal.subject)}


@router.get("/me", response_model=MeOut)
async def me(principal: Principal = Depends(current_principal),
             c: Container = Depends(get_container)) -> MeOut:
    username = None
    if principal.kind == "user":
        user = await c.auth.users.by_id(principal.subject)
        username = user.username if user else None
    return MeOut(subject=principal.subject, role=principal.role, kind=principal.kind, username=username)


@router.post("/change-password", status_code=204)
async def change_password(body: ChangePasswordIn, response: Response,
                          principal: Principal = admin_only,
                          c: Container = Depends(get_container)) -> Response:
    await c.auth.change_password(principal, body.current_password, body.new_password)
    response.delete_cookie(c.settings.cookie_name, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(principal: Principal = admin_only,
                   c: Container = Depends(get_container)) -> list:
    return [SessionOut.model_validate(s) for s in await c.auth.sessions.list_for_user(principal.subject)]


@router.get("/mfa/status", response_model=MfaStatusOut)
async def mfa_status(principal: Principal = admin_only,
                     c: Container = Depends(get_container)) -> MfaStatusOut:
    return MfaStatusOut(enabled=await c.auth.mfa_enabled_for(principal.subject))


@router.post("/mfa/enroll", response_model=MfaEnrollOut)
async def mfa_enroll(body: MfaEnrollIn, principal: Principal = admin_only,
                     c: Container = Depends(get_container)) -> MfaEnrollOut:
    await c.limiter.enforce(f"mfa:enroll:{principal.subject}", limit=10, window=300)
    secret, uri = await c.auth.start_mfa_enrollment(principal, body.current_password)
    return MfaEnrollOut(secret=secret, otpauth_uri=uri)


@router.post("/mfa/verify", response_model=MfaEnableOut)
async def mfa_verify(body: MfaVerifyIn, principal: Principal = admin_only,
                     c: Container = Depends(get_container)) -> MfaEnableOut:
    await c.limiter.enforce(f"mfa:verify:{principal.subject}", limit=10, window=300)
    codes = await c.auth.confirm_mfa_enrollment(principal, body.code)
    return MfaEnableOut(recovery_codes=codes)


@router.post("/mfa/disable", status_code=204)
async def mfa_disable(body: MfaDisableIn, response: Response,
                      principal: Principal = admin_only,
                      c: Container = Depends(get_container)) -> Response:
    await c.limiter.enforce(f"mfa:disable:{principal.subject}", limit=10, window=300)
    await c.auth.disable_mfa(principal, body.current_password, body.code)
    response.status_code = 204
    return response


@router.post("/mfa/step-up", status_code=204)
async def mfa_step_up(body: MfaVerifyIn, response: Response,
                      principal: Principal = admin_only,
                      c: Container = Depends(get_container)) -> Response:
    await c.limiter.enforce(f"mfa:stepup:{principal.subject}", limit=10, window=300)
    await c.auth.verify_step_up(principal, body.code)
    response.status_code = 204
    return response


@router.post("/device-token", response_model=TokenOut)
async def device_token(body: DeviceTokenIn, request: Request,
                       c: Container = Depends(get_container)) -> TokenOut:
    """Machine login for a local agent: device id + secret -> short-lived access token."""
    return _token_out(await c.auth.exchange_device(body.device_id, body.secret, client_ip(request)))
