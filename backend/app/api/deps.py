import secrets

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.services.auth_service import AuthService
from app.core.config import get_settings


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return AuthService(db).resolve_user_from_request(request)


def require_current_user(current_user: User | None = Depends(get_current_user)) -> User:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return current_user


def is_admin_user(user: User | None) -> bool:
    """Return whether the account has site-wide staff access by configured email."""
    if user is None:
        return False
    return user.email.strip().casefold() in set(get_settings().admin_emails)


def require_admin_user(current_user: User = Depends(require_current_user)) -> User:
    if not is_admin_user(current_user):
        raise HTTPException(status_code=403, detail="Administrator access required", headers={"Cache-Control": "no-store"})
    # A predictable signing secret would allow impersonation of a whitelisted
    # account. Fail closed for staff APIs even when development login is enabled.
    secret = get_settings().session_secret.strip()
    if secret == "arcbench-dev-session-secret" or len(secret) < 32:
        raise HTTPException(
            status_code=503, detail="Administrator access requires a secure session signing secret",
            headers={"Cache-Control": "no-store"},
        )
    return current_user


def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    configured_token = get_settings().admin_api_token
    if not configured_token:
        raise HTTPException(status_code=503, detail="Administrator API token is not configured")

    scheme, _, supplied_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied_token or not secrets.compare_digest(supplied_token, configured_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid administrator API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
