from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserSummary(BaseModel):
    id: str
    email: str
    username: str
    github_email: str | None = None
    github_username: str | None = None
    registration_source: str = "standard"
    display_name: str | None = None
    real_name: str | None = None
    student_id: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    email_verified_at: datetime | None = None
    is_admin: bool = False


class RegisterRequest(BaseModel):
    email: str = Field(default="", max_length=255)
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=128)
    # Account classification is managed independently of public registration.
    model_config = {"extra": "forbid"}


class LoginRequest(BaseModel):
    email: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=128)


class AuthResponse(BaseModel):
    user: UserSummary


class EmailVerificationChallenge(BaseModel):
    verification_required: bool = True
    challenge_id: str
    email: str
    expires_in: int
    resend_after: int


class VerifyEmailRequest(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=128)
    code: str = Field(min_length=1, max_length=32)


class ResendEmailRequest(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=1, max_length=255)


class PasswordResetGrant(BaseModel):
    reset_token: str
    expires_in: int


class CompletePasswordResetRequest(BaseModel):
    reset_token: str = Field(min_length=32, max_length=128)
    password: str = Field(max_length=1024)
    confirm_password: str = Field(max_length=1024)


class UpdateProfileRequest(BaseModel):
    real_name: str | None = Field(default=None, max_length=120)
    student_id: str | None = Field(default=None, max_length=64)
    github_email: EmailStr | None = None
    github_username: str | None = Field(default=None, min_length=1, max_length=255)
