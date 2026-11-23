from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin_user, require_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.classroom import ClassroomSummary, MyClassroomResponse
from app.services.classroom_service import ClassroomService

router = APIRouter(prefix="/classrooms", tags=["classrooms"])


@router.get("/me", response_model=MyClassroomResponse)
def get_my_classroom(db: Session = Depends(get_db), current_user: User = Depends(require_current_user)):
    return ClassroomService(db).my_class(current_user.id)


@router.get("/all", response_model=list[ClassroomSummary])
def list_all_classrooms(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    return ClassroomService(db).list_classroom_summaries()
