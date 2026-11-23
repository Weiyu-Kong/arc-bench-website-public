"""One-time email verification. Redis state is shared by every API worker."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets

import httpx
import redis
from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User
from app.schemas.auth import EmailVerificationChallenge, PasswordResetGrant
from app.services.auth_service import AuthService


class EmailVerificationService:
    PREFIX = "arcbench:email-verification:v1:"
    MAX_ATTEMPTS = 5

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.auth = AuthService(db)
        self.client = redis.Redis.from_url(
            self.settings.redis_url, decode_responses=True,
            socket_timeout=5, socket_connect_timeout=5,
        )

    @staticmethod
    def normalize_email(email: str) -> str:
        try:
            value = str(TypeAdapter(EmailStr).validate_python(email.strip())).lower()
        except ValidationError:
            raise HTTPException(400, "请输入有效的邮箱地址。") from None
        if len(value) > 255:
            raise HTTPException(400, "邮箱地址过长。")
        return value

    @classmethod
    def _email_key(cls, email: str) -> str:
        return cls.PREFIX + "email:" + hashlib.sha256(email.encode()).hexdigest()

    @contextmanager
    def _locked(self, email: str):
        try:
            lock = self.client.lock(self._email_key(email) + ":lock", timeout=120, blocking_timeout=1)
            if not lock.acquire():
                raise HTTPException(429, "操作正在处理中，请稍后重试。")
            try:
                yield
            finally:
                lock.release()
        except redis.RedisError:
            raise HTTPException(503, "邮箱验证服务暂时不可用，请稍后重试。") from None

    def _load(self, challenge_id: str) -> dict:
        try:
            value = self.client.get(self.PREFIX + challenge_id)
        except redis.RedisError:
            raise HTTPException(503, "邮箱验证服务暂时不可用，请稍后重试。") from None
        if not value:
            raise HTTPException(400, "验证请求已过期或失效，请返回重新获取验证码。")
        return json.loads(value)

    def _check_config(self) -> None:
        if not self.settings.email_verification_enabled:
            raise HTTPException(400, "邮箱验证尚未启用。")
        if not self.settings.resend_api_key.strip() or not self.settings.email_from.strip():
            raise HTTPException(503, "邮件发送服务尚未配置，请联系管理员。")
        secret = self.settings.session_secret.strip()
        if secret == "arcbench-dev-session-secret" or len(secret) < 32:
            raise HTTPException(503, "邮箱验证需要配置至少 32 字符的安全会话密钥。")

    def begin_registration(self, email: str, username: str, password: str, ip: str) -> EmailVerificationChallenge:
        self._check_config()
        email = self.normalize_email(email)
        username = self.auth._normalize_username(username)
        self.auth._validate_password(password)
        if self.db.scalar(select(User.id).where(User.email == email)):
            raise ValueError("Email is already registered")
        if self.db.scalar(select(User.id).where(User.username == username)):
            raise ValueError("Username is already taken")
        # A pending registration never creates or reserves an account. Only the
        # password hash is temporarily stored; the plaintext password is not.
        data = {"email": email, "username": username,
                "password_hash": self.auth._hash_password(password), "attempts": 0}
        with self._locked(email):
            return self._send(data, secrets.token_urlsafe(32), ip)

    def begin_login(self, user: User, ip: str) -> EmailVerificationChallenge:
        self._check_config()
        # The caller must authenticate the original password first.
        data = {"email": user.email, "user_id": user.id, "attempts": 0}
        with self._locked(user.email):
            return self._send(data, secrets.token_urlsafe(32), ip)

    def begin_password_reset(self, email: str, ip: str) -> EmailVerificationChallenge:
        self._check_config()
        email = self.normalize_email(email)
        user = self.db.scalar(select(User).where(User.email == email))
        if user is None:
            raise HTTPException(400, "该邮箱尚未注册，请检查邮箱地址。")
        data = {"email": user.email, "user_id": user.id, "purpose": "password_reset",
                "password_hash": user.password_hash, "attempts": 0}
        with self._locked(user.email):
            return self._send(data, secrets.token_urlsafe(32), ip)

    @staticmethod
    def _require_purpose(data: dict, password_reset: bool) -> None:
        if password_reset:
            valid = data.get("purpose") == "password_reset"
        else:
            valid = data.get("purpose") is None  # Existing registration/login challenges.
        if not valid:
            raise HTTPException(400, "验证请求用途不符，请返回重新获取验证码。")

    def resend(self, challenge_id: str, ip: str, *, password_reset: bool = False) -> EmailVerificationChallenge:
        self._check_config()
        initial = self._load(challenge_id)
        with self._locked(initial["email"]):
            data = self._load(challenge_id)
            self._require_purpose(data, password_reset)
            return self._send(data, challenge_id, ip)

    def _digest(self, challenge_id: str, code: str) -> str:
        return hmac.new(self.auth.secret, f"email-verification:{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()

    def _send(self, data: dict, challenge_id: str, ip: str) -> EmailVerificationChallenge:
        email_key = self._email_key(data["email"])
        if self.client.exists(email_key + ":cooldown"):
            raise HTTPException(429, "验证码发送过于频繁，请稍后再试。")
        # Reserve limits before contacting Resend, including failed sends. No
        # plaintext email/IP is included in rate-limit keys. Do not trust XFF.
        now = datetime.utcnow()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        counters = [
            (email_key + ":hour:" + now.strftime("%Y%m%d%H"), 5, 3600),
            (self.PREFIX + "ip:" + hashlib.sha256(ip.encode()).hexdigest() + ":" + now.strftime("%Y%m%d%H"), 30, 3600),
            (self.PREFIX + "daily:" + now.strftime("%Y%m%d"), self.settings.email_daily_send_limit,
             int((tomorrow - now).total_seconds()) + 1),
        ]
        for key, limit, ttl in counters:
            count = self.client.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                1, key, ttl,
            )
            if count > limit:
                raise HTTPException(429, "验证码发送次数已达上限，请稍后再试或联系管理员。")
        code = f"{secrets.randbelow(1000000):06d}"
        resetting = data.get("purpose") == "password_reset"
        description = "密码重置" if resetting else "邮箱验证"
        try:
            with httpx.Client(timeout=httpx.Timeout(10, connect=5), trust_env=False) as client:
                response = client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.settings.resend_api_key.strip()}"},
                    json={"from": self.settings.email_from, "to": [data["email"]],
                          "subject": f"ARC-Bench {description}验证码",
                          "text": f"您的 ARC-Bench {description}验证码是：{code}\n"
                                  f"有效期为 {self.settings.email_code_ttl_seconds // 60} 分钟。"
                                  "请勿将验证码透露给他人。如非本人操作，请忽略此邮件。"},
                )
        except httpx.HTTPError:
            raise HTTPException(503, "验证码邮件发送失败，请稍后重试。") from None
        # Never expose provider responses, credentials or codes in API logs.
        if not response.is_success:
            raise HTTPException(503, "验证码邮件发送失败，请联系管理员检查邮件服务配置或额度。")
        data["digest"] = self._digest(challenge_id, code)
        ttl = self.settings.email_code_ttl_seconds
        previous = self.client.get(email_key + ":active")
        with self.client.pipeline(transaction=True) as pipe:
            if previous and previous != challenge_id:
                pipe.delete(self.PREFIX + previous)
            pipe.set(self.PREFIX + challenge_id, json.dumps(data), ex=ttl)
            pipe.set(email_key + ":active", challenge_id, ex=ttl)
            pipe.set(email_key + ":cooldown", "1", ex=self.settings.email_resend_interval_seconds)
            pipe.execute()
        return EmailVerificationChallenge(challenge_id=challenge_id, email=data["email"],
                                          expires_in=ttl, resend_after=self.settings.email_resend_interval_seconds)

    def _validate_code(self, data: dict, challenge_id: str, code: str) -> None:
        # Caller holds the email lock; failed attempts retain the original TTL.
        if not hmac.compare_digest(data["digest"], self._digest(challenge_id, code.strip())):
            data["attempts"] += 1
            remaining = self.MAX_ATTEMPTS - data["attempts"]
            if remaining <= 0:
                self.client.delete(self.PREFIX + challenge_id)
                raise HTTPException(400, "验证码错误次数过多，请返回重新获取验证码。")
            ttl = self.client.ttl(self.PREFIX + challenge_id)
            if ttl <= 0:
                raise HTTPException(400, "验证码已过期，请重新获取。")
            self.client.set(self.PREFIX + challenge_id, json.dumps(data), ex=ttl)
            raise HTTPException(400, f"验证码错误，还可以尝试 {remaining} 次。")

    def verify(self, challenge_id: str, code: str) -> User:
        self._check_config()
        initial = self._load(challenge_id)
        with self._locked(initial["email"]):
            data = self._load(challenge_id)
            self._require_purpose(data, False)
            self._validate_code(data, challenge_id, code)
            now = datetime.utcnow()
            if data.get("user_id"):
                user = self.db.scalar(select(User).where(User.id == data["user_id"]).with_for_update())
                if not user or user.email != data["email"] or user.email_verified_at is not None:
                    self.client.delete(self.PREFIX + challenge_id)
                    raise HTTPException(400, "验证请求已失效，请重新登录。")
                user.email_verified_at = now
                self.db.commit()
                self.db.refresh(user)
            else:
                try:
                    user = self.auth.create_user_with_hash(data["email"], data["username"],
                                                          data["password_hash"], email_verified_at=now)
                except ValueError as exc:
                    self.client.delete(self.PREFIX + challenge_id)
                    raise HTTPException(409, "邮箱或用户名已被注册，请返回登录或重新注册。") from exc
            self.client.delete(self.PREFIX + challenge_id)
            return user

    def verify_password_reset(self, challenge_id: str, code: str) -> PasswordResetGrant:
        self._check_config()
        initial = self._load(challenge_id)
        with self._locked(initial["email"]):
            data = self._load(challenge_id)
            self._require_purpose(data, True)
            self._validate_code(data, challenge_id, code)
            user = self.db.get(User, data["user_id"])
            if not user or user.email != data["email"] or user.password_hash != data["password_hash"]:
                self.client.delete(self.PREFIX + challenge_id)
                raise HTTPException(400, "重置请求已失效，请重新获取验证码。")
            # The code is consumed now. This separate, short-lived capability
            # authorizes only setting a new password, never logging in.
            token = secrets.token_urlsafe(32)
            grant = {"purpose": "password_reset_grant", "email": user.email,
                     "user_id": user.id, "password_hash": user.password_hash}
            ttl = self.settings.email_code_ttl_seconds
            with self.client.pipeline(transaction=True) as pipe:
                pipe.set(self.PREFIX + token, json.dumps(grant), ex=ttl)
                pipe.delete(self.PREFIX + challenge_id)
                pipe.execute()
            return PasswordResetGrant(reset_token=token, expires_in=ttl)

    def complete_password_reset(self, reset_token: str, password: str, confirm_password: str) -> None:
        self._check_config()
        # Same validator as registration. Invalid input does not consume a grant.
        self.auth._validate_password(password)
        if password != confirm_password:
            raise ValueError("Passwords do not match")
        initial = self._load(reset_token)
        with self._locked(initial["email"]):
            data = self._load(reset_token)
            if data.get("purpose") != "password_reset_grant":
                raise HTTPException(400, "请先完成密码重置的邮箱验证。")
            user = self.db.scalar(select(User).where(User.id == data["user_id"]).with_for_update())
            if not user or user.email != data["email"] or user.password_hash != data["password_hash"]:
                self.client.delete(self.PREFIX + reset_token)
                raise HTTPException(400, "重置请求已失效，请重新获取验证码。")
            user.password_hash = self.auth._hash_password(password)
            user.session_version = (user.session_version or 0) + 1
            user.email_verified_at = user.email_verified_at or datetime.utcnow()
            self.db.commit()
            self.client.delete(self.PREFIX + reset_token)
