from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_api_key import UserApiKey
from app.schemas.user_api_key import UserApiKeySummary


class UserApiKeyService:
    def __init__(self, db: Session):
        self.db = db

    def list_for_user(self, user_id: str) -> list[UserApiKeySummary]:
        records = self.db.scalars(
            select(UserApiKey)
            .where(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc(), UserApiKey.id.desc())
        )
        return [
            UserApiKeySummary(
                id=record.id,
                name=record.name,
                masked_key=self._mask(record.api_key),
                created_at=record.created_at,
            )
            for record in records
        ]

    def get_secret_for_user(self, key_id: str, user_id: str) -> str:
        # Ownership belongs in the query, including for admin and team accounts.
        key = self.db.scalar(
            select(UserApiKey.api_key).where(
                UserApiKey.id == key_id,
                UserApiKey.user_id == user_id,
            )
        )
        if key is None:
            raise LookupError("API key not found")
        return key

    @staticmethod
    def _mask(key: str) -> str:
        return f"{key[:4]}********{key[-4:]}" if len(key) > 8 else "********"
