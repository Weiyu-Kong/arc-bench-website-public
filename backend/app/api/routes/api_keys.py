from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import require_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user_api_key import UserApiKeySecret, UserApiKeySummary
from app.services.user_api_key_service import UserApiKeyService


router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyNoStoreMiddleware:
    """Prevent caching key responses without buffering other streaming APIs."""

    def __init__(self, app: ASGIApp, api_prefix: str):
        self.app = app
        self.path = f"{api_prefix.rstrip('/')}/api-keys"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == self.path or path.startswith(f"{self.path}/")):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Includes authentication failures and missing keys/routes.
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


@router.get("", response_model=list[UserApiKeySummary])
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> list[UserApiKeySummary]:
    return UserApiKeyService(db).list_for_user(current_user.id)


@router.get("/{key_id}/secret", response_model=UserApiKeySecret)
def get_api_key_secret(
    key_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> UserApiKeySecret:
    try:
        key = UserApiKeyService(db).get_secret_for_user(key_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="API key not found") from exc
    return UserApiKeySecret(api_key=key)
