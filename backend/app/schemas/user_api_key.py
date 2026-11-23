from datetime import datetime

from pydantic import BaseModel


class UserApiKeySummary(BaseModel):
    id: str
    name: str | None
    masked_key: str
    created_at: datetime


class UserApiKeySecret(BaseModel):
    api_key: str
