from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.user import User
from ..services import analytics_service
from ..utils.security import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def get_overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return analytics_service.get_overview(current_user.id, db)


@router.get("/trend")
def get_score_trend(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return analytics_service.get_score_trend(current_user.id, db)


@router.get("/courses/{course_id}")
def get_course_analytics(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return analytics_service.get_course_analytics(course_id, current_user.id, db)


@router.get("/courses-summary")
def get_courses_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    return analytics_service.get_courses_summary(current_user.id, db)
