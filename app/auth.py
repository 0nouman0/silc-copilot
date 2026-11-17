"""Session identity. The user id ALWAYS comes from a signed server-issued cookie —
never from the chat text or request body.

Demo mode offers a "sign in as demo applicant" picker. Production mode must
replace `demo_login` with the institution's real authentication (OIDC/SSO/token).
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from .config import integration_mode, session_secret

COOKIE = "silc_session"


def _sig(value: str) -> str:
    return hmac.new(session_secret().encode(), value.encode(), hashlib.sha256).hexdigest()


def sign(user_id: str) -> str:
    return f"{user_id}.{_sig(user_id)}"


def verify(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    user_id, sig = token.rsplit(".", 1)
    return user_id if hmac.compare_digest(sig, _sig(user_id)) else None


def current_user_id(request: Request) -> str | None:
    return verify(request.cookies.get(COOKIE))


def demo_login_allowed() -> bool:
    return integration_mode() == "demo"
