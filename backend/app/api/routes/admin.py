"""Session-protected leaderboard exports and token-protected executor controls."""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.deps import require_admin_token, require_admin_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement import CompetitionLeaderboardEntry
from app.schemas.admin import AdminUserPage, AdminSubmissionCompetition, AdminSubmissionPage, AdminRunPage
from app.services.admin_user_service import list_admin_users
from app.services.admin_user_submission_service import AdminUserSubmissionService
from app.services.admin_leaderboard_export_service import AdminLeaderboardExportService
from app.services.competition_leaderboard_query import query_competition_leaderboard
from app.services.executor_registry import ExecutorRegistry

router = APIRouter(prefix="/admin", tags=["admin"])
leaderboard_router = APIRouter(
    prefix="/leaderboard", dependencies=[Depends(require_admin_user)],
)
users_router = APIRouter(prefix="/users", dependencies=[Depends(require_admin_user)])


class AdminLeaderboardNoStoreMiddleware:
    """Apply no-store even to authentication and request-validation failures."""
    def __init__(self, app: ASGIApp, api_prefix: str):
        self.app = app
        self.paths = tuple(f"{api_prefix.rstrip('/')}/admin/{name}" for name in ("leaderboard", "users"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not any(path == prefix or path.startswith(prefix + "/") for prefix in self.paths):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)


class LeaderboardFilters(BaseModel):
    track: Literal["all", "web", "mobile", "kernel"] = "all"
    competition_id: str | None = None
    task_id: str | None = None
    funding_source: Literal["all", "self_funded", "official_evaluation"] = "all"
    classroom_id: str | None = None


@users_router.get("", response_model=AdminUserPage)
def get_admin_users(
    page: int = Query(default=1, ge=1, le=100_000_000),
    q: str = Query(default="", max_length=255),
    classroom_id: str | None = Query(default=None, max_length=64),
    is_leader: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AdminUserPage:
    return list_admin_users(db, page=page, q=q, classroom_id=classroom_id, is_leader=is_leader)


class LeaderboardExportRequest(LeaderboardFilters):
    source_run_ids: list[str] = Field(min_length=1)


class TemporaryExportResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # The service creates this isolated directory with mkdtemp, and
            # removes it on build failures. Also clean up interrupted responses.
            with CancelScope(shield=True):
                await run_in_threadpool(shutil.rmtree, Path(self.path).parent, True)


@users_router.get("/{user_id}/submission-competitions", response_model=list[AdminSubmissionCompetition])
def get_user_submission_competitions(user_id: str, db: Session = Depends(get_db)):
    return AdminUserSubmissionService(db).competitions(user_id)


@users_router.get("/{user_id}/submissions", response_model=AdminSubmissionPage)
def get_user_submissions(
    user_id: str, page: int = Query(default=1, ge=1, le=100_000_000),
    competition_id: str | None = Query(default=None, max_length=64), db: Session = Depends(get_db),
):
    return AdminUserSubmissionService(db).submissions(user_id, page, competition_id)


@users_router.get("/{user_id}/submissions/{submission_id}/runs", response_model=AdminRunPage)
def get_user_submission_runs(
    user_id: str, submission_id: str,
    page: int = Query(default=1, ge=1, le=100_000_000), db: Session = Depends(get_db),
):
    return AdminUserSubmissionService(db).runs(user_id, submission_id, page)


@users_router.get("/{user_id}/submissions/{submission_id}/agent")
def download_user_submission_agent(user_id: str, submission_id: str, db: Session = Depends(get_db)) -> FileResponse:
    path = AdminUserSubmissionService(db).agent(user_id, submission_id)
    return FileResponse(path, media_type="application/zip", filename=f"submission-{submission_id}-agent.zip")


@users_router.get("/{user_id}/submissions/{submission_id}/runs/{run_id}/artifacts")
def download_user_run_artifacts(
    user_id: str, submission_id: str, run_id: str, db: Session = Depends(get_db),
) -> FileResponse:
    path = AdminUserSubmissionService(db).artifact(user_id, submission_id, run_id)
    return TemporaryExportResponse(path, media_type="application/zip" if path.suffix == ".zip" else "application/gzip",
                                   filename=f"run-{run_id}-{path.name}")


@leaderboard_router.get("", response_model=list[CompetitionLeaderboardEntry])
def get_admin_leaderboard(
    filters: LeaderboardFilters = Depends(),
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> list[CompetitionLeaderboardEntry]:
    return query_competition_leaderboard(db, current_user, **filters.model_dump())


@leaderboard_router.post("/export")
def export_admin_leaderboard(
    payload: LeaderboardExportRequest,
    current_user: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    filters = payload.model_dump(exclude={"source_run_ids"})
    source_ids = set(payload.source_run_ids)
    entries = query_competition_leaderboard(db, current_user, **filters)
    entry = next((item for item in entries if set(item.source_run_ids) == source_ids), None)
    if entry is None:
        raise HTTPException(409, "Leaderboard changed; refresh and try again", headers={"Cache-Control": "no-store"})
    try:
        path, incomplete = AdminLeaderboardExportService(db).build(entry, filters)
    except LookupError as exc:
        raise HTTPException(409, str(exc), headers={"Cache-Control": "no-store"}) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Administrator leaderboard export failed")
        raise HTTPException(500, "Unable to package saved artifacts; please try again", headers={"Cache-Control": "no-store"}) from exc
    return TemporaryExportResponse(
        path, media_type="application/zip", filename="leaderboard-export.zip",
        headers={"Cache-Control": "no-store", "X-ArcBench-Export-Incomplete": str(incomplete).lower()},
    )


class ExecutorControlRequest(BaseModel):
    action: Literal["pause", "resume", "shutdown"]


def _redis_client() -> Any:
    return ExecutorRegistry(executor_id="admin-read-only").client


def _hash_to_dict(client: Any, key: str) -> dict[str, Any]:
    value = client.hgetall(key)
    if not value:
        return {}
    for field in ("running_tasks",):
        if field in value:
            value[field] = int(value[field])
    if "updated_at" in value:
        value["age_seconds"] = max(0, round(time.time() - float(value["updated_at"]), 2))
    return value


@router.get("/executors")
def list_executors(_authorized: None = Depends(require_admin_token)) -> dict[str, list[dict[str, Any]]]:
    client = _redis_client()
    try:
        executors = []
        for key in client.scan_iter(match="executors:*"):
            executor = _hash_to_dict(client, key)
            if executor:
                executor["health"] = _hash_to_dict(client, f"executors_health:{executor.get('executor_id', key.split(':', 1)[1])}")
                executors.append(executor)
        return {"executors": sorted(executors, key=lambda item: str(item.get("executor_id", "")))}
    finally:
        client.close()


@router.get("/executors/{executor_id}")
def get_executor(executor_id: str, _authorized: None = Depends(require_admin_token)) -> dict[str, Any]:
    client = _redis_client()
    try:
        executor = _hash_to_dict(client, f"executors:{executor_id}")
        if not executor:
            raise HTTPException(status_code=404, detail="Executor not found")
        tasks = []
        for key in client.scan_iter(match="executingtasks:*"):
            task = _hash_to_dict(client, key)
            if task.get("executor_id") == executor_id:
                tasks.append(task)
        executor["health"] = _hash_to_dict(client, f"executors_health:{executor_id}")
        executor["tasks"] = tasks
        executor["completed_tasks"] = [
            task
            for item in client.lrange(f"executor_completed:{executor_id}", 0, -1)
            if (task := _json_dict(item))
        ]
        executor["control"] = _hash_to_dict(client, f"executor_control:{executor_id}")
        return executor
    finally:
        client.close()


@router.get("/executing-tasks")
def list_executing_tasks(_authorized: None = Depends(require_admin_token)) -> dict[str, list[dict[str, Any]]]:
    client = _redis_client()
    try:
        return {"tasks": [_hash_to_dict(client, key) for key in client.scan_iter(match="executingtasks:*")]}
    finally:
        client.close()


@router.post("/executors/{executor_id}/control", status_code=202)
def control_executor(
    executor_id: str,
    request: ExecutorControlRequest,
    _authorized: None = Depends(require_admin_token),
) -> dict[str, str]:
    client = _redis_client()
    try:
        executor = client.hgetall(f"executors:{executor_id}")
        if not executor:
            raise HTTPException(status_code=404, detail="Executor not found")
        updated_at = float(executor.get("updated_at", 0))
        settings = get_settings()
        if time.time() - updated_at > settings.executor_registry_ttl_seconds:
            raise HTTPException(status_code=409, detail="Executor is offline")

        request_id = uuid4().hex
        control_key = f"executor_control:{executor_id}"
        client.hset(
            control_key,
            mapping={
                "request_id": request_id,
                "action": request.action,
                "requested_at": str(time.time()),
                "acknowledged_request_id": "",
                "acknowledged_at": "",
                "acknowledged_action": "",
            },
        )
        client.expire(control_key, settings.executor_completed_task_ttl_seconds)
        return {"executor_id": executor_id, "action": request.action, "request_id": request_id}
    finally:
        client.close()


def _json_dict(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


# Router-wide protection also covers future leaderboard and download endpoints.
# Executor endpoints retain their separate operational-token authentication.
router.include_router(leaderboard_router)
router.include_router(users_router)
