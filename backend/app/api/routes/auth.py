import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (AuthResponse, EmailVerificationChallenge, LoginRequest, RegisterRequest,
                              ResendEmailRequest, VerifyEmailRequest, UpdateProfileRequest, UserSummary,
                              PasswordResetRequest, PasswordResetGrant, CompletePasswordResetRequest)
from app.services.auth_service import AuthService
from app.services.email_verification_service import EmailVerificationService
from app.core.config import get_settings
from app.services.team_service import TeamService


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse | EmailVerificationChallenge)
def register(payload: RegisterRequest, response: Response, request: Request,
             db: Session = Depends(get_db)) -> AuthResponse | EmailVerificationChallenge:
    service = AuthService(db)
    try:
        if service.settings.email_verification_enabled:
            challenge = EmailVerificationService(db).begin_registration(
                payload.email, payload.username, payload.password, request.client.host if request.client else "unknown",
            )
            response.delete_cookie(key=AuthService.COOKIE_NAME, path="/")
            response.headers["Cache-Control"] = "no-store"
            return challenge
        user = service.register_user(
            email=payload.email,
            username=payload.username,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _set_session_cookie(response, service, user)
    return AuthResponse(user=UserSummary.model_validate(user, from_attributes=True))


@router.post("/login", response_model=AuthResponse | EmailVerificationChallenge)
def login(payload: LoginRequest, response: Response, request: Request,
          db: Session = Depends(get_db)) -> AuthResponse | EmailVerificationChallenge:
    service = AuthService(db)
    try:
        user = service.authenticate_user(payload.email, payload.password)
    except ValueError as exc:
        message = str(exc)
        status_code = 400 if message in {"Email is required", "Password is required"} else 401
        raise HTTPException(status_code=status_code, detail=message) from exc
    if service.settings.email_verification_enabled and user.email_verified_at is None:
        challenge = EmailVerificationService(db).begin_login(user, request.client.host if request.client else "unknown")
        response.delete_cookie(key=AuthService.COOKIE_NAME, path="/")
        response.headers["Cache-Control"] = "no-store"
        return challenge
    _set_session_cookie(response, service, user)
    return AuthResponse(user=UserSummary.model_validate(user, from_attributes=True))


@router.post("/email/verify", response_model=AuthResponse)
def verify_email(payload: VerifyEmailRequest, response: Response, db: Session = Depends(get_db)) -> AuthResponse:
    user = EmailVerificationService(db).verify(payload.challenge_id, payload.code)
    _set_session_cookie(response, AuthService(db), user)
    return AuthResponse(user=UserSummary.model_validate(user, from_attributes=True))


@router.post("/email/resend", response_model=EmailVerificationChallenge)
def resend_email(payload: ResendEmailRequest, request: Request, response: Response,
                 db: Session = Depends(get_db)) -> EmailVerificationChallenge:
    response.headers["Cache-Control"] = "no-store"
    return EmailVerificationService(db).resend(payload.challenge_id, request.client.host if request.client else "unknown")


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(key=AuthService.COOKIE_NAME, path="/")
    return {"detail": "Logged out"}


@router.post("/password-reset/request", response_model=EmailVerificationChallenge)
def request_password_reset(payload: PasswordResetRequest, request: Request, response: Response,
                           db: Session = Depends(get_db)) -> EmailVerificationChallenge:
    response.headers["Cache-Control"] = "no-store"
    return EmailVerificationService(db).begin_password_reset(
        payload.email, request.client.host if request.client else "unknown",
    )


@router.post("/password-reset/resend", response_model=EmailVerificationChallenge)
def resend_password_reset(payload: ResendEmailRequest, request: Request, response: Response,
                          db: Session = Depends(get_db)) -> EmailVerificationChallenge:
    response.headers["Cache-Control"] = "no-store"
    return EmailVerificationService(db).resend(
        payload.challenge_id, request.client.host if request.client else "unknown", password_reset=True,
    )


@router.post("/password-reset/verify", response_model=PasswordResetGrant)
def verify_password_reset(payload: VerifyEmailRequest, response: Response,
                          db: Session = Depends(get_db)) -> PasswordResetGrant:
    response.headers["Cache-Control"] = "no-store"
    return EmailVerificationService(db).verify_password_reset(payload.challenge_id, payload.code)


@router.post("/password-reset/complete")
def complete_password_reset(payload: CompletePasswordResetRequest, response: Response,
                            db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        EmailVerificationService(db).complete_password_reset(
            payload.reset_token, payload.password, payload.confirm_password,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response.delete_cookie(key=AuthService.COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"detail": "密码重置成功，请使用新密码登录。"}


@router.get("/meter/continue")
def continue_to_meter(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> RedirectResponse:
    settings = get_settings()
    meter_base_url = settings.meter_base_url.strip().rstrip("/")
    bridge_secret = settings.meter_bridge_secret.strip()
    if not meter_base_url:
        raise HTTPException(status_code=503, detail="Meter bridge is not configured")
    if not bridge_secret:
        raise HTTPException(status_code=503, detail="Meter bridge secret is not configured")

    team_payload = TeamService(db).get_my_team_payload(current_user.id)
    team = team_payload.get("team") if isinstance(team_payload, dict) else None
    subject_type = "team" if team else "user"
    subject_id = str(team.get("id") if team else current_user.id)
    display_name = str(team.get("name") if team else (current_user.display_name or current_user.username))
    payload = {
        "purpose": "meter-bridge",
        "subject_type": subject_type,
        "subject_id": subject_id,
        "user_id": current_user.id,
        "username": current_user.username,
        "team_id": team.get("id") if team else None,
        "team_name": team.get("name") if team else None,
        "display_name": display_name,
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + max(30, int(settings.meter_bridge_token_ttl_seconds or 120)),
    }
    token = _sign_meter_bridge_token(payload, bridge_secret)
    target = f"{meter_base_url}/api/user/bridge?token={quote(token, safe='')}"
    return RedirectResponse(target, status_code=303)


@router.get("/me", response_model=AuthResponse)
def me(current_user: User | None = Depends(get_current_user)) -> AuthResponse:
    if current_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return AuthResponse(user=UserSummary.model_validate(current_user, from_attributes=True))


@router.put("/profile", response_model=AuthResponse)
def update_profile(
    payload: UpdateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> AuthResponse:
    service = AuthService(db)
    try:
        user = service.update_profile(
            current_user,
            real_name=payload.real_name,
            student_id=payload.student_id,
            github_email=payload.github_email,
            github_username=payload.github_username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AuthResponse(user=UserSummary.model_validate(user, from_attributes=True))


def _set_session_cookie(response: Response, service: AuthService, user: User) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=AuthService.COOKIE_NAME,
        value=service.build_session_token(user),
        httponly=True,
        samesite="lax",
        secure=service.settings.secure_cookies,
        max_age=AuthService.SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )


def _sign_meter_bridge_token(payload: dict[str, object], secret: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    signed = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{body}.{signed}"
