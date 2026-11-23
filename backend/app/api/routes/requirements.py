from typing import Literal
import subprocess
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_current_user
from app.api.competition_freeze import enforce_competition_freeze
from app.db.session import get_db
from app.models.user import User
from app.schemas.requirement import (
    BenchmarkDetail,
    BenchmarkSummary,
    CompetitionDetail,
    CompetitionLeaderboardEntry,
    CompetitionLeaderboardSelectionRequest,
    CompetitionSubmissionHistoryEntry,
    CompetitionSummary,
    CompetitionTemplateOptionsResponse,
    RequirementDetail,
    RequirementTests,
    RequirementSummary,
)
from app.schemas.competition_registration import (
    CompetitionRegistrationStatus,
    ConfirmCompetitionRegistrationRequest,
)
from app.services.agent_starter_service import AgentStarterService
from app.services.demo_agent_service import DemoAgentService
from app.services.requirement_catalog import RequirementCatalogService
from app.services.competition_leaderboard_query import query_competition_leaderboard
from app.services.competition_leaderboard_selection_service import CompetitionLeaderboardSelectionService
from app.services.competition_access_service import CompetitionAccessService
from app.services.competition_standings_visibility_service import PUBLIC_HACKATHON_LEADERBOARDS
from app.services.competition_template_service import CompetitionTemplateService
from app.services.competition_registration_service import CompetitionRegistrationService
from app.core.config import get_settings


def _require_competition_access(current_user: User | None, competition_id: str, db: Session) -> None:
    try:
        CompetitionAccessService(db).require_access(current_user, competition_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403 if current_user else 401, detail=str(exc)) from exc


def _competition_id_for_requirement(requirement_id: str) -> str:
    return requirement_id.split("--", 1)[0].strip().lower()


def _competition_tests_are_private(db: Session, competition_id: str) -> bool:
    normalized_id = competition_id.strip().lower()
    competition = next(
        (
            item
            for item in RequirementCatalogService.for_catalog(db, "competition").list_competitions()
            if item.id == normalized_id
        ),
        None,
    )
    return bool(competition and str(competition.type or "").strip().lower() == "official")


def _reject_private_competition_tests(db: Session, requirement_id: str) -> None:
    if _competition_tests_are_private(db, _competition_id_for_requirement(requirement_id)):
        # Return the same response as an unavailable resource. Do not reveal
        # test paths, filenames, source, archive metadata, or existence.
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(prefix="/requirements", tags=["requirements"])
competition_router = APIRouter(
    prefix="/competitions", tags=["competitions"],
    dependencies=[Depends(enforce_competition_freeze)],
)
benchmark_router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("", response_model=list[RequirementSummary])
def list_requirements(
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> list[RequirementSummary]:
    service = RequirementCatalogService.for_catalog(db, catalog)
    requirements = service.list_requirements()
    if catalog != "competition":
        return requirements
    access = CompetitionAccessService(db)
    return [
        item
        for item in requirements
        if access.is_listed_competition(_competition_id_for_requirement(item.id))
        and access.can_access(current_user, _competition_id_for_requirement(item.id))
    ]


@competition_router.get("", response_model=list[CompetitionSummary])
def list_competitions(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> list[CompetitionSummary]:
    service = RequirementCatalogService.for_catalog(db, "competition")
    access = CompetitionAccessService(db)
    return [
        item
        for item in service.list_competitions()
        if access.is_listed_competition(item.id)
    ]


@competition_router.get("/leaderboard", response_model=list[CompetitionLeaderboardEntry])
def get_competition_leaderboard(
    track: str = Query(default="all", pattern="^(all|web|mobile|kernel)$"),
    competition_id: str | None = Query(default=None),
    task_id: str | None = Query(default=None, description="Task directory name, requirement id, or 'all'"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
    funding_source: Literal["all", "self_funded", "official_evaluation"] = "all",
    classroom_id: str | None = Query(default=None),
) -> list[CompetitionLeaderboardEntry]:
    return query_competition_leaderboard(
        db, current_user, track=track, competition_id=competition_id, task_id=task_id,
        funding_source=funding_source, classroom_id=classroom_id,
    )


@competition_router.get("/{competition_id}/leaderboard-tasks")
def get_competition_leaderboard_tasks(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> list[dict[str, str]]:
    normalized_id = competition_id.strip().lower()
    if normalized_id not in PUBLIC_HACKATHON_LEADERBOARDS:
        _require_competition_access(current_user, normalized_id, db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    return [
        {"id": task.id.rsplit("--", 1)[-1], "title": task.title}
        for task in service.scan_entries()
        if task.competition_id == normalized_id
    ]


@competition_router.get("/{competition_id}/submissions", response_model=list[CompetitionSubmissionHistoryEntry])
def get_competition_submission_history(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> list[CompetitionSubmissionHistoryEntry]:
    _require_competition_access(current_user, competition_id, db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    try:
        return service.list_competition_submission_history(competition_id, current_user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@competition_router.put("/{competition_id}/leaderboard-submission")
def select_competition_leaderboard_submission(
    competition_id: str,
    payload: CompetitionLeaderboardSelectionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> dict[str, str]:
    _require_competition_access(current_user, competition_id, db)
    try:
        CompetitionLeaderboardSelectionService(db).set_selection(
            competition_id, current_user.id, payload.submission_id,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"detail": "Leaderboard submission updated"}


@competition_router.get("/{competition_id}/template-options", response_model=CompetitionTemplateOptionsResponse)
def get_competition_template_options(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> CompetitionTemplateOptionsResponse:
    _require_competition_access(current_user, competition_id, db)
    try:
        return CompetitionTemplateService(db).list_options(competition_id.strip().lower(), current_user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@competition_router.get("/{competition_id}/starter-agent")
def download_competition_starter_agent(
    competition_id: str,
    language: str = Query(default="python", pattern="^(python|javascript|typescript|nodejs|js|ts|py)$"),
    template: str = Query(default="blank", pattern="^(blank|arc|octos|codex)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> Response:
    _require_competition_access(current_user, competition_id, db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    competition = next((item for item in service.list_competitions() if item.id == competition_id), None)
    if competition is None:
        raise HTTPException(status_code=404, detail=f"Competition '{competition_id}' not found")
    try:
        evolution_templates = (
            CompetitionTemplateService(db).download_templates(competition_id, current_user)
            if competition.template_required
            else None
        )
        content, filename = AgentStarterService().build_bundle(
            task_type="web",
            language=language,
            template_kind=template,
            evolution_templates=evolution_templates,
        )
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@competition_router.get("/{competition_id}/requirements-download")
def download_competition_requirements(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> Response:
    _require_competition_access(current_user, competition_id, db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    try:
        content, filename = service.build_competition_requirements_bundle(competition_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@competition_router.get("/{competition_id}/initial-projects-download")
def download_competition_initial_projects(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> Response:
    normalized_id = competition_id.strip().lower()
    _require_competition_access(current_user, normalized_id, db)
    try:
        content, filename = CompetitionTemplateService(db).build_initial_projects_bundle(
            normalized_id,
            current_user,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@competition_router.get("/{competition_id}", response_model=CompetitionDetail)
def get_competition(
    competition_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> CompetitionDetail:
    _require_competition_access(current_user, competition_id, db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    try:
        return service.get_competition_detail(competition_id, str(request.base_url).rstrip("/"), current_user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _official_competition(db: Session, competition_id: str) -> CompetitionSummary:
    competition = next(
        (
            item
            for item in RequirementCatalogService.for_catalog(db, "competition").list_competitions()
            if item.id == competition_id.strip().lower()
        ),
        None,
    )
    if competition is None:
        raise HTTPException(status_code=404, detail=f"Competition '{competition_id}' not found")
    if competition.type != "official":
        raise HTTPException(status_code=409, detail="This competition does not require official registration")
    return competition


@competition_router.get("/{competition_id}/registration", response_model=CompetitionRegistrationStatus)
def get_competition_registration(
    competition_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> CompetitionRegistrationStatus:
    competition = _official_competition(db, competition_id)
    return CompetitionRegistrationStatus(
        **CompetitionRegistrationService(db).status(
            current_user,
            competition.id,
            0,
        )
    )


@competition_router.post("/{competition_id}/registration", response_model=CompetitionRegistrationStatus)
def confirm_competition_registration(
    competition_id: str,
    payload: ConfirmCompetitionRegistrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user),
) -> CompetitionRegistrationStatus:
    competition = _official_competition(db, competition_id)
    if not payload.confirmed:
        raise HTTPException(status_code=422, detail="Confirm the official competition rules before entering")
    if competition.id == "first-competition" and competition.status != "open":
        raise HTTPException(status_code=409, detail="This competition is not open for registration")
    try:
        result = CompetitionRegistrationService(db).confirm(
            current_user,
            competition.id,
            [str(email) for email in payload.member_emails],
            0,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CompetitionRegistrationStatus(**result)


@competition_router.get("/public/download")
def download_public_competition(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> Response:
    # The aggregate archive contains every competition, including Hackathon.
    # Only a global grant may receive it.
    if not CompetitionAccessService(db).can_access(current_user, "*"):
        raise HTTPException(status_code=403, detail="A global competition grant is required for the full archive")
    service = RequirementCatalogService.for_catalog(db, "competition")
    content, filename = service.build_public_competition_bundle()
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@competition_router.get("/public/tasks/{requirement_id}/download/{download_kind}")
def download_public_task(
    requirement_id: str,
    download_kind: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> Response:
    if download_kind in {"tests", "full"}:
        _reject_private_competition_tests(db, requirement_id)
    _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, "competition")
    try:
        if download_kind == "requirements":
            content, filename = service.build_public_task_document(requirement_id, "requirements")
            media_type = "text/markdown; charset=utf-8"
        elif download_kind == "prerequisites":
            content, filename = service.build_public_task_document(requirement_id, "prerequisites")
            media_type = "text/markdown; charset=utf-8"
        elif download_kind == "tests":
            content, filename = service.build_public_task_tests_bundle(requirement_id)
            media_type = "application/zip"
        elif download_kind == "demo":
            content, filename = service.build_public_task_demo_bundle(requirement_id)
            media_type = "application/zip"
        elif download_kind == "full":
            content, filename = service.build_public_task_bundle(requirement_id)
            media_type = "application/zip"
        else:
            raise HTTPException(status_code=404, detail="Unknown download kind")
    except (LookupError, PermissionError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@competition_router.get("/public/demo-agent")
def download_demo_agent() -> Response:
    try:
        content, filename = DemoAgentService().build_bundle()
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@benchmark_router.get("", response_model=list[BenchmarkSummary])
def list_benchmarks(request: Request, db: Session = Depends(get_db)) -> list[BenchmarkSummary]:
    service = RequirementCatalogService.for_catalog(db, "benchmark")
    return service.list_benchmarks(str(request.base_url).rstrip("/"))


@benchmark_router.get("/{benchmark_id}", response_model=BenchmarkDetail)
def get_benchmark(benchmark_id: str, request: Request, db: Session = Depends(get_db)) -> BenchmarkDetail:
    service = RequirementCatalogService.for_catalog(db, "benchmark")
    try:
        return service.get_benchmark_detail(benchmark_id, str(request.base_url).rstrip("/"))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@benchmark_router.get("/{benchmark_id}/download")
def download_benchmark_track(benchmark_id: str, db: Session = Depends(get_db)) -> Response:
    service = RequirementCatalogService.for_catalog(db, "benchmark")
    try:
        content, filename = service.build_benchmark_track_bundle(benchmark_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@benchmark_router.get("/tasks/{requirement_id}/download")
def download_benchmark_task(requirement_id: str, db: Session = Depends(get_db)) -> Response:
    service = RequirementCatalogService.for_catalog(db, "benchmark")
    try:
        content, filename = service.build_benchmark_task_bundle(requirement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{requirement_id}/starter-agent")
def download_starter_agent(
    requirement_id: str,
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    language: str = Query(default="python", pattern="^(python|javascript|typescript|nodejs|js|ts|py)$"),
    template: str = Query(default="blank", pattern="^(blank|arc|octos|codex)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> Response:
    if catalog == "competition":
        _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, catalog)
    try:
        requirement = service.get_entry(requirement_id)
        task_type = requirement.category if catalog != "competition" else "web"
        content, filename = AgentStarterService().build_bundle(task_type=task_type, language=language, template_kind=template)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/{requirement_id}/tests", response_model=RequirementTests)
def get_requirement_tests(
    requirement_id: str,
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> RequirementTests:
    if catalog == "competition":
        _reject_private_competition_tests(db, requirement_id)
        _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, catalog)
    try:
        return service.get_requirement_tests(requirement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{requirement_id}", response_model=RequirementDetail)
def get_requirement(
    requirement_id: str,
    request: Request,
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> RequirementDetail:
    if catalog == "competition":
        _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, catalog)
    try:
        return service.get_requirement_detail(requirement_id, str(request.base_url).rstrip("/"))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{requirement_id}/document")
def get_document(
    requirement_id: str,
    kind: str = Query(pattern="^(requirements|prerequisites)$"),
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> PlainTextResponse:
    if catalog == "competition":
        _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, catalog)
    try:
        return PlainTextResponse(service.get_document(requirement_id, kind))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{requirement_id}/{asset_kind}/{asset_path:path}")
def get_asset(
    requirement_id: str,
    asset_kind: str,
    asset_path: str,
    catalog: str = Query(default="playground", pattern="^(playground|competition|benchmark)$"),
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
) -> FileResponse:
    if asset_kind not in {"assets", "references"}:
        raise HTTPException(status_code=404, detail="Unknown asset kind")
    if catalog == "competition":
        _require_competition_access(current_user, _competition_id_for_requirement(requirement_id), db)
    service = RequirementCatalogService.for_catalog(db, catalog)
    try:
        return FileResponse(service.get_asset_path(requirement_id, asset_kind, asset_path))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
