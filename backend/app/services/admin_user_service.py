"""Read-only, database-paginated user directory for administrators."""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.classroom import Classroom, ClassroomMembership
from app.models.competition_account import Team
from app.models.user import User
from app.schemas.admin import AdminUserEntry, AdminUserPage


def list_admin_users(
    db: Session, *, page: int, q: str = "", classroom_id: str | None = None,
    is_leader: bool | None = None,
) -> AdminUserPage:
    leader = select(Team.id).where(
        Team.leader_user_id == User.id, Team.is_active.is_(True),
    ).exists()
    query = select(
        User.id, User.username, User.email, User.created_at,
        Classroom.id.label("classroom_id"), Classroom.name.label("classroom_name"),
        leader.label("is_leader"),
    ).select_from(User).outerjoin(ClassroomMembership, ClassroomMembership.user_id == User.id).outerjoin(
        Classroom, Classroom.id == ClassroomMembership.classroom_id,
    )
    search = q.strip().lower()
    if search:
        # Escape wildcard characters: search text is a literal substring.
        query = query.where(or_(
            func.lower(User.username).contains(search, autoescape=True),
            func.lower(User.email).contains(search, autoescape=True),
        ))
    if classroom_id:
        query = query.where(ClassroomMembership.classroom_id == classroom_id)
    if is_leader is not None:
        query = query.where(leader if is_leader else ~leader)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    # Materialize only the requested page; never load all accounts into Python.
    rows = db.execute(query.order_by(User.created_at.desc(), User.id.desc()).offset((page - 1) * 20).limit(20)).mappings()
    return AdminUserPage(items=[AdminUserEntry.model_validate(row) for row in rows], total=total, page=page)
