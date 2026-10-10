"""FastAPI dependencies: who is calling, and are they allowed."""
from __future__ import annotations

import hmac
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status
from jarvis_shared.log import principal_var

from jarvis_backend.auth.service import ADMIN, AGENT, AuthService, Principal

__all__ = ["ADMIN", "AGENT", "admin_only", "agent_only", "any_role", "client_ip", "current_principal",
           "require_role", "verify_metrics_token", "verify_origin"]


def _bearer(request: Request) -> Optional[str]:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def client_ip(request: Request) -> str:
    """The caller's address. X-Forwarded-For is honoured ONLY for the number of trusted
    proxy hops configured, counted from the right (the left side is client-controlled)."""
    hops = request.app.state.container.settings.trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops][:64]
    return (request.client.host if request.client else "unknown")[:64]


async def current_principal(request: Request) -> Principal:
    token = _bearer(request)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})
    auth: AuthService = request.app.state.container.auth
    principal = await auth.authenticate(token)
    request.state.principal = principal
    principal_var.set(f"{principal.role}:{principal.subject[:13]}")
    return principal


def require_role(*allowed: str) -> Callable[..., Principal]:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do that.")
        return principal

    return dependency


any_role = Depends(require_role(ADMIN, AGENT))
admin_only = Depends(require_role(ADMIN))
agent_only = Depends(require_role(AGENT))


def verify_origin(request: Request) -> None:
    """CSRF defence for the one endpoint family authenticated by a cookie (refresh/logout).

    The cookie is SameSite=Strict already; this is the second layer. A browser always sends
    Origin on cross-site POSTs, so a mismatched or missing-with-cookie Origin is rejected.
    """
    origin = request.headers.get("origin")
    expected = request.app.state.container.settings.public_origin.rstrip("/")
    if origin is None:
        # Non-browser clients (curl, tests) omit Origin; they cannot be CSRF'd. But a
        # browser form post that omits it must not slip through: require our header.
        if request.headers.get("x-requested-with") != "jarvis-ui" and request.cookies:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")
        return
    allowed = {expected} if expected else {f"{request.url.scheme}://{request.headers.get('host', '')}"}
    if origin.rstrip("/") not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-site request blocked.")


def verify_metrics_token(request: Request) -> None:
    settings = request.app.state.container.settings
    expected = settings.secret("metrics_token")
    presented = _bearer(request) or ""
    if not expected or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Metrics token required.",
                            headers={"WWW-Authenticate": "Bearer"})
