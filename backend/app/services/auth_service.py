from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User
from app.services.lock_service import hold_advisory_xact_lock


class AuthService:
    COOKIE_NAME = "arcbench_session"
    SESSION_TTL_DAYS = 14

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.secret = self._get_secret()

    def register_user(
        self,
        email: str,
        username: str,
        password: str,
    ) -> User:
        normalized_email = email.strip().lower()
        if "@" not in normalized_email:
            raise ValueError("Email must include @")
        normalized_username = self._normalize_username(username)
        self._validate_password(password)
        return self.create_user_with_hash(normalized_email, normalized_username, self._hash_password(password))

    def create_user_with_hash(
        self, normalized_email: str, normalized_username: str, password_hash: str,
        *, email_verified_at: datetime | None = None,
    ) -> User:
        # Public registration must not claim a reserved administrator identity
        # simply by supplying its email while global verification is disabled.
        if email_verified_at is None and normalized_email.strip().casefold() in set(self.settings.admin_emails):
            raise ValueError("Administrator email requires verified registration")
        with hold_advisory_xact_lock(self.db, f"auth:email:{normalized_email}", f"auth:username:{normalized_username}"):
            existing_user = self.db.scalar(select(User).where(User.email == normalized_email))
            if existing_user:
                raise ValueError("Email is already registered")
            if self.db.scalar(select(User).where(User.username == normalized_username)):
                raise ValueError("Username is already taken")

            user = User(
                id=uuid.uuid4().hex,
                email=normalized_email,
                username=normalized_username,
                password_hash=password_hash,
                email_verified_at=email_verified_at,
                registration_source="standard",
                display_name=normalized_username,
            )
            self.db.add(user)
            self.db.flush()
            from app.services.classroom_service import ClassroomService
            ClassroomService(self.db).sync_rosters(commit=False)
            self.db.commit()
            self.db.refresh(user)
        return user

    def authenticate_user(self, email: str, password: str) -> User:
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise ValueError("Email is required")
        if not password:
            raise ValueError("Password is required")
        user = self.db.scalar(select(User).where(User.email == normalized_email))
        if not user:
            raise ValueError("User not found")
        if not self._verify_password(password, user.password_hash):
            raise ValueError("Incorrect password")
        return user

    def update_profile(
        self,
        user: User,
        *,
        real_name: str | None,
        student_id: str | None,
        github_email: str | None,
        github_username: str | None,
    ) -> User:
        user.real_name = self._normalize_optional_profile_text(real_name, field_name="Real name", max_length=120)
        user.student_id = self._normalize_optional_profile_text(student_id, field_name="Student ID", max_length=64)
        user.github_email = self._normalize_optional_email(github_email)
        user.github_username = self._normalize_optional_github_username(github_username)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def build_session_token(self, user: User) -> str:
        if self.settings.email_verification_enabled and user.email_verified_at is None:
            raise ValueError("Email verification is required")
        expires_at = int((datetime.utcnow() + timedelta(days=self.SESSION_TTL_DAYS)).timestamp())
        nonce = secrets.token_hex(16)
        payload = f"{user.id}:{expires_at}:{nonce}"
        signature = self._session_signature(payload, user)
        return f"{payload}:{signature}"

    def resolve_user_from_request(self, request: Request) -> User | None:
        token = request.cookies.get(self.COOKIE_NAME)
        if not token:
            return None
        parts = token.split(":")
        if len(parts) != 4:
            return None
        user_id, expires_at_raw, nonce, signature = parts
        payload = f"{user_id}:{expires_at_raw}:{nonce}"
        try:
            expires_at = int(expires_at_raw)
        except ValueError:
            return None
        if expires_at < int(datetime.utcnow().timestamp()):
            return None
        user = self.db.get(User, user_id)
        if user is None or not hmac.compare_digest(signature, self._session_signature(payload, user)):
            return None
        if user and self.settings.email_verification_enabled and user.email_verified_at is None:
            return None
        return user

    def _session_signature(self, payload: str, user: User) -> str:
        # Version zero preserves existing cookies until the first password reset.
        version = user.session_version or 0
        signed_payload = f"{payload}:session-version:{version}" if version else payload
        return hmac.new(self.secret, signed_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _normalize_username(username: str) -> str:
        normalized = "".join(username.strip().split())
        if len(normalized) < 3 or len(normalized) > 32:
            raise ValueError("Username must be between 3 and 32 characters")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if any(char not in allowed for char in normalized):
            raise ValueError("Username can only contain letters, numbers, underscore, and hyphen")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(password) > 128:
            raise ValueError("Password must be 128 characters or fewer")

    @staticmethod
    def _normalize_optional_email(email: str | None) -> str | None:
        if email is None:
            return None
        normalized = email.strip().lower()
        return normalized or None

    @staticmethod
    def _normalize_optional_github_username(username: str | None) -> str | None:
        if username is None:
            return None
        normalized = username.strip()
        if not normalized:
            return None
        if len(normalized) > 255:
            raise ValueError("GitHub username must be 255 characters or fewer")
        return normalized

    @staticmethod
    def _normalize_optional_profile_text(value: str | None, *, field_name: str, max_length: int) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None
        if len(normalized) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")
        return normalized

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
        return f"{salt}${digest.hex()}"

    @staticmethod
    def _verify_password(password: str, stored_hash: str) -> bool:
        try:
            salt, digest = stored_hash.split("$", 1)
        except ValueError:
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000).hex()
        return hmac.compare_digest(candidate, digest)

    def _get_secret(self) -> bytes:
        secret = self.settings.session_secret.strip()
        if not secret:
            secret = "arcbench-dev-session-secret"
        return secret.encode("utf-8")
