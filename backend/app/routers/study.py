"""
Study Strategy Router
---------------------
REST API for Notes and Study Schedule (timetable).

Notes
  GET    /study/notes                  — list notes (filter by folder)
  POST   /study/notes                  — create note
  PATCH  /study/notes/{id}             — update note (title / content / folder)
  DELETE /study/notes/{id}             — delete note
  GET    /study/notes/folders          — distinct folder names for this user

Schedule
  GET    /study/schedule               — list upcoming schedules
  POST   /study/schedule               — create schedule entry
  PATCH  /study/schedule/{id}          — update schedule (resets notify flags if time changes)
  DELETE /study/schedule/{id}          — delete schedule
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import distinct
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.study import Note, StudySchedule
from ..models.user import User
from ..utils.security import get_current_user

router = APIRouter(prefix="/study", tags=["study"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title:   str = "Untitled"
    content: str = ""
    folder:  Optional[str] = None


class NoteUpdate(BaseModel):
    title:   Optional[str] = None
    content: Optional[str] = None
    folder:  Optional[str] = None


class ScheduleCreate(BaseModel):
    course:           str
    description:      Optional[str] = None
    scheduled_time:   datetime
    duration_minutes: Optional[int] = None


class ScheduleUpdate(BaseModel):
    course:           Optional[str]      = None
    description:      Optional[str]      = None
    scheduled_time:   Optional[datetime] = None
    duration_minutes: Optional[int]      = None


# ── Notes ─────────────────────────────────────────────────────────────────────

@router.get("/notes/folders")
def list_folders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return sorted list of distinct folder names for this user."""
    rows = (
        db.query(distinct(Note.folder))
        .filter(Note.user_id == current_user.id, Note.folder.isnot(None))
        .all()
    )
    return sorted([r[0] for r in rows if r[0]])


@router.get("/notes")
def list_notes(
    folder: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Note).filter(Note.user_id == current_user.id)
    if folder == "__none__":
        q = q.filter(Note.folder.is_(None))
    elif folder is not None:
        q = q.filter(Note.folder == folder)
    notes = q.order_by(Note.updated_at.desc()).all()
    return [_ser_note(n) for n in notes]


@router.post("/notes", status_code=status.HTTP_201_CREATED)
def create_note(
    payload: NoteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    folder = payload.folder.strip() if payload.folder else None
    note = Note(
        user_id=current_user.id,
        title=payload.title.strip() or "Untitled",
        content=payload.content,
        folder=folder,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _ser_note(note)


@router.patch("/notes/{note_id}")
def update_note(
    note_id: UUID,
    payload: NoteUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    note = _get_note(note_id, current_user.id, db)
    if payload.title   is not None: note.title   = payload.title.strip() or "Untitled"
    if payload.content is not None: note.content = payload.content
    if payload.folder  is not None: note.folder  = payload.folder.strip() or None
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return _ser_note(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    note = _get_note(note_id, current_user.id, db)
    db.delete(note)
    db.commit()


# ── Schedule ──────────────────────────────────────────────────────────────────

@router.get("/schedule")
def list_schedules(
    upcoming_only: bool = Query(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(StudySchedule).filter(StudySchedule.user_id == current_user.id)
    if upcoming_only:
        q = q.filter(StudySchedule.scheduled_time >= datetime.utcnow())
    scheds = q.order_by(StudySchedule.scheduled_time.asc()).all()
    return [_ser_sched(s) for s in scheds]


@router.post("/schedule", status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.scheduled_time <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Scheduled time cannot be in the past.")

    sched = StudySchedule(
        user_id=current_user.id,
        course=payload.course.strip(),
        description=payload.description,
        scheduled_time=payload.scheduled_time,
        duration_minutes=payload.duration_minutes,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return _ser_sched(sched)


@router.patch("/schedule/{schedule_id}")
def update_schedule(
    schedule_id: UUID,
    payload: ScheduleUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sched = _get_sched(schedule_id, current_user.id, db)

    if payload.course      is not None: sched.course      = payload.course.strip()
    if payload.description is not None: sched.description = payload.description
    if payload.duration_minutes is not None: sched.duration_minutes = payload.duration_minutes

    if payload.scheduled_time is not None:
        if payload.scheduled_time <= datetime.utcnow():
            raise HTTPException(status_code=400, detail="Scheduled time cannot be in the past.")
        sched.scheduled_time = payload.scheduled_time
        # Reset notification flags when time is rescheduled
        sched.notified_30 = False
        sched.notified_15 = False
        sched.notified_5  = False

    db.commit()
    db.refresh(sched)
    return _ser_sched(sched)


@router.delete("/schedule/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sched = _get_sched(schedule_id, current_user.id, db)
    db.delete(sched)
    db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_note(note_id, user_id, db):
    n = db.query(Note).filter(Note.id == note_id, Note.user_id == user_id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Note not found.")
    return n


def _get_sched(schedule_id, user_id, db):
    s = db.query(StudySchedule).filter(
        StudySchedule.id == schedule_id, StudySchedule.user_id == user_id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return s


def _ser_note(n: Note) -> dict:
    return {
        "id":         str(n.id),
        "folder":     n.folder,
        "title":      n.title,
        "content":    n.content or "",
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
    }


def _ser_sched(s: StudySchedule) -> dict:
    return {
        "id":               str(s.id),
        "course":           s.course,
        "description":      s.description,
        "scheduled_time":   s.scheduled_time.isoformat() + "Z",   # mark as UTC
        "duration_minutes": s.duration_minutes,
        "created_at":       s.created_at.isoformat(),
    }
