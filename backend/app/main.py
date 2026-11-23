from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, api_keys, auth, health, model_proxy, notifications, requirements, submissions, teams, user_tasks, classrooms
from app.core.config import get_settings
from app.db.session import engine
from app.db.schema_validation import verify_current_schema
from app.services.submission_event_stream import SubmissionEventStream
from app.services.model_usage_refresh_service import ModelUsageRefreshWorker


settings = get_settings()
api_path_prefix = settings.api_prefix.strip("/")

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-ArcBench-Export-Incomplete", "Content-Disposition"],
)
app.add_middleware(api_keys.ApiKeyNoStoreMiddleware, api_prefix=settings.api_prefix)
app.add_middleware(admin.AdminLeaderboardNoStoreMiddleware, api_prefix=settings.api_prefix)
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(api_keys.router, prefix=settings.api_prefix)
app.include_router(requirements.router, prefix=settings.api_prefix)
app.include_router(requirements.competition_router, prefix=settings.api_prefix)
app.include_router(requirements.benchmark_router, prefix=settings.api_prefix)
app.include_router(submissions.submission_router, prefix=settings.api_prefix)
app.include_router(submissions.run_router, prefix=settings.api_prefix)
app.include_router(notifications.router, prefix=settings.api_prefix)
app.include_router(teams.router, prefix=settings.api_prefix)
app.include_router(classrooms.router, prefix=settings.api_prefix)
app.include_router(user_tasks.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(model_proxy.router, prefix=settings.api_prefix)
app.include_router(model_proxy.usage_router, prefix=settings.api_prefix)
if settings.site_assets_root.is_dir():
    app.mount("/paper-assets", StaticFiles(directory=settings.site_assets_root), name="paper-assets")

@app.on_event("startup")
def on_startup() -> None:
    settings.user_submissions_root.mkdir(parents=True, exist_ok=True)
    settings.user_tasks_root.mkdir(parents=True, exist_ok=True)
    settings.shared_storage_root.mkdir(parents=True, exist_ok=True)
    settings.executor_workspace_root.mkdir(parents=True, exist_ok=True)
    verify_current_schema(engine)
    from app.db.session import SessionLocal
    from app.services.classroom_service import ClassroomService
    with SessionLocal() as db:
        ClassroomService(db).sync_rosters()
    refresh_worker = ModelUsageRefreshWorker(settings)
    app.state.model_usage_refresh_worker = refresh_worker
    refresh_worker.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    refresh_worker = getattr(app.state, "model_usage_refresh_worker", None)
    if refresh_worker is not None:
        refresh_worker.stop()
    SubmissionEventStream.shutdown()


def _frontend_index() -> Path:
    return settings.frontend_dist / "index.html"


def _resolve_frontend_path(full_path: str) -> Path | None:
    candidate = (settings.frontend_dist / full_path).resolve()
    try:
        candidate.relative_to(settings.frontend_dist.resolve())
    except ValueError:
        return None
    return candidate


@app.get("/", include_in_schema=False)
def serve_frontend_index() -> FileResponse:
    index_file = _frontend_index()
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_file)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
    if api_path_prefix and (full_path == api_path_prefix or full_path.startswith(f"{api_path_prefix}/")):
        raise HTTPException(status_code=404, detail="API route not found")

    requested_file = _resolve_frontend_path(full_path)
    if requested_file and requested_file.is_file():
        return FileResponse(requested_file)

    index_file = _frontend_index()
    if index_file.is_file():
        return FileResponse(index_file)

    raise HTTPException(status_code=404, detail="Frontend build not found")
