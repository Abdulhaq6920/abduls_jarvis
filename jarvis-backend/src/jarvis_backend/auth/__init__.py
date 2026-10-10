"""Authentication: passwords, JWT tokens, sessions, device credentials."""
from jarvis_backend.auth.deps import ADMIN, AGENT, admin_only, any_role, require_role
from jarvis_backend.auth.service import AuthService, Principal

__all__ = ["ADMIN", "AGENT", "AuthService", "Principal", "admin_only", "any_role", "require_role"]
