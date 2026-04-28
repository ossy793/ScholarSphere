"""
Challenge Router — real-time quiz competitions.

REST:
  POST /challenges              - host creates challenge
  POST /challenges/join         - participant joins by code
  POST /challenges/{id}/start   - host starts the challenge
  GET  /challenges/{id}         - challenge detail + participants
  GET  /challenges/{id}/leaderboard - results (full for host, limited for participants)
  GET  /challenges/my           - user's recent challenges

WS:
  WS  /challenges/{id}/ws       - real-time hub (?token=<JWT>)

WS protocol (client → server):
  {type: "ping"}
  {type: "answer", question_id, question_idx, answer, response_time_ms}

WS protocol (server → client):
  Broadcast: participant_joined, challenge_started, question, question_ended, challenge_ended
  Personal:  pong, answer_result, question_responses (spectator host only)
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models.challenge import Challenge, ChallengeParticipant, ChallengeResponse
from ..models.question import Question
from ..models.quiz import Quiz
from ..models.user import User
from ..utils.security import get_current_user
from ..utils.security import decode_token as _decode_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/challenges", tags=["challenges"])

# ── WebSocket room manager ─────────────────────────────────────────────────────

class _Room:
    def __init__(self):
        self._sockets: dict[str, WebSocket] = {}   # user_id → ws
        self._next_event: asyncio.Event = asyncio.Event()

    async def add(self, user_id: str, ws: WebSocket):
        self._sockets[user_id] = ws

    def remove(self, user_id: str):
        self._sockets.pop(user_id, None)

    @property
    def count(self) -> int:
        return len(self._sockets)

    async def broadcast(self, payload: dict):
        dead = []
        for uid, ws in list(self._sockets.items()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self._sockets.pop(uid, None)

    async def send_to(self, user_id: str, payload: dict):
        ws = self._sockets.get(user_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                self._sockets.pop(user_id, None)

    def reset_next(self):
        self._next_event.clear()

    async def wait_for_next(self):
        await self._next_event.wait()

    def signal_next(self):
        self._next_event.set()


class _RoomManager:
    def __init__(self):
        self._rooms: dict[str, _Room]          = {}
        self._tasks: dict[str, asyncio.Task]   = {}

    def room(self, cid: str) -> _Room:
        if cid not in self._rooms:
            self._rooms[cid] = _Room()
        return self._rooms[cid]

    def close(self, cid: str):
        self._rooms.pop(cid, None)
        task = self._tasks.pop(cid, None)
        if task and not task.done():
            task.cancel()

    def set_task(self, cid: str, task: asyncio.Task):
        old = self._tasks.pop(cid, None)
        if old and not old.done():
            old.cancel()
        self._tasks[cid] = task


_mgr = _RoomManager()

# ── Scoring ────────────────────────────────────────────────────────────────────

_BASE   = 100
_SPEED  = 50   # max speed bonus

def _calc_points(correct: bool, response_ms: float, duration_ms: float) -> int:
    if not correct:
        return 0
    ratio = max(0.0, 1.0 - response_ms / max(duration_ms, 1))
    return _BASE + round(_SPEED * ratio)

# ── Schemas ────────────────────────────────────────────────────────────────────

class CreateRequest(BaseModel):
    quiz_id:          str
    duration_seconds: int          = 30
    timer_mode:       str          = "per_question"
    max_participants: Optional[int] = None
    host_mode:        str          = "participant"   # participant | spectator

class JoinRequest(BaseModel):
    code: str

# ── REST ───────────────────────────────────────────────────────────────────────

@router.get("/my")
def my_challenges(
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    """Return the 10 most recent challenges the user hosted or joined."""
    hosted = (
        db.query(Challenge)
        .filter_by(host_id=current_user.id)
        .order_by(Challenge.created_at.desc())
        .limit(10)
        .all()
    )
    joined_rows = (
        db.query(ChallengeParticipant)
        .filter_by(user_id=current_user.id)
        .order_by(ChallengeParticipant.joined_at.desc())
        .limit(10)
        .all()
    )
    joined = [r.challenge for r in joined_rows if r.challenge]

    seen = set()
    result = []
    for ch in hosted + joined:
        if str(ch.id) in seen:
            continue
        seen.add(str(ch.id))
        result.append(_ch_summary(ch, current_user.id, db))

    return sorted(result, key=lambda x: x["created_at"], reverse=True)[:10]


@router.post("", status_code=201)
def create_challenge(
    payload:      CreateRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    quiz = db.query(Quiz).filter_by(id=UUID(payload.quiz_id)).first()
    if not quiz:
        raise HTTPException(404, "Quiz not found.")
    if str(quiz.user_id) != str(current_user.id):
        raise HTTPException(403, "You can only challenge with your own quizzes.")

    q_count = db.query(Question).filter_by(quiz_id=quiz.id).count()
    if q_count == 0:
        raise HTTPException(400, "This quiz has no questions.")
    if payload.timer_mode not in ("per_question", "full_quiz"):
        raise HTTPException(400, "timer_mode must be per_question or full_quiz.")

    host_mode = payload.host_mode if payload.host_mode in ("participant", "spectator") else "participant"

    ch = Challenge(
        host_id=current_user.id,
        quiz_id=quiz.id,
        duration_seconds=max(10, min(300, payload.duration_seconds)),
        timer_mode=payload.timer_mode,
        max_participants=payload.max_participants,
        host_mode=host_mode,
    )
    db.add(ch)
    db.flush()

    # Host is participant #1 only when participating (not spectating)
    if host_mode == "participant":
        db.add(ChallengeParticipant(challenge_id=ch.id, user_id=current_user.id))
    db.commit()

    return {
        "id":               str(ch.id),
        "code":             ch.code,
        "quiz_title":       quiz.title,
        "duration_seconds": ch.duration_seconds,
        "timer_mode":       ch.timer_mode,
        "host_mode":        ch.host_mode,
        "status":           ch.status,
        "question_count":   q_count,
        "is_host":          True,
    }


@router.post("/join")
def join_challenge(
    payload:      JoinRequest,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    ch = db.query(Challenge).filter_by(code=payload.code.upper().strip()).first()
    if not ch:
        raise HTTPException(404, "Challenge not found. Check the code and try again.")
    if ch.status == "completed":
        raise HTTPException(400, "This challenge has already ended.")
    if ch.status == "active":
        raise HTTPException(400, "Challenge already started — you cannot join mid-game.")

    existing = db.query(ChallengeParticipant).filter_by(
        challenge_id=ch.id, user_id=current_user.id
    ).first()

    q_count = db.query(Question).filter_by(quiz_id=ch.quiz_id).count()

    if not existing:
        if ch.max_participants:
            count = db.query(ChallengeParticipant).filter_by(challenge_id=ch.id).count()
            if count >= ch.max_participants:
                raise HTTPException(400, "Challenge is full.")
        db.add(ChallengeParticipant(challenge_id=ch.id, user_id=current_user.id))
        db.commit()

        # Notify host that someone joined
        if str(ch.host_id) != str(current_user.id):
            from ..services.push_service import notify_user
            quiz_title = ch.quiz.title if ch.quiz else "your challenge"
            notify_user(
                db, ch.host_id,
                title=f"🎯 {current_user.full_name} joined your challenge!",
                body=f"Someone joined '{quiz_title}'. Head back to start the game.",
                url="/challenge.html",
                commit=True,
            )

    return {
        "id":               str(ch.id),
        "code":             ch.code,
        "quiz_title":       ch.quiz.title if ch.quiz else "",
        "duration_seconds": ch.duration_seconds,
        "timer_mode":       ch.timer_mode,
        "host_mode":        ch.host_mode,
        "status":           ch.status,
        "question_count":   q_count,
        "is_host":          str(ch.host_id) == str(current_user.id),
    }


@router.post("/{challenge_id}/start", status_code=200)
async def start_challenge(
    challenge_id: UUID,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    ch = _get_or_404(challenge_id, db)
    if str(ch.host_id) != str(current_user.id):
        raise HTTPException(403, "Only the host can start the challenge.")
    if ch.status != "waiting":
        raise HTTPException(400, f"Challenge is already {ch.status}.")

    questions = (
        db.query(Question)
        .filter_by(quiz_id=ch.quiz_id)
        .order_by(Question.order_index)
        .all()
    )
    if not questions:
        raise HTTPException(400, "No questions in this quiz.")

    ch.status     = "active"
    ch.started_at = datetime.utcnow()
    db.commit()

    q_list    = [_q_public(q) for q in questions]
    q_answers = {str(q.id): q.correct_answer for q in questions}

    room = _mgr.room(str(challenge_id))
    await room.broadcast({
        "type":             "challenge_started",
        "total_questions":  len(questions),
        "timer_mode":       ch.timer_mode,
        "duration_seconds": ch.duration_seconds,
        "host_mode":        ch.host_mode,
    })

    task = asyncio.create_task(
        _run_questions(
            str(challenge_id), q_list, q_answers,
            ch.duration_seconds, ch.host_mode, str(ch.host_id),
        )
    )
    _mgr.set_task(str(challenge_id), task)

    return {"status": "active"}


@router.get("/{challenge_id}")
def get_challenge(
    challenge_id: UUID,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    ch = _get_or_404(challenge_id, db)
    participants = db.query(ChallengeParticipant).filter_by(challenge_id=ch.id).all()
    q_count = db.query(Question).filter_by(quiz_id=ch.quiz_id).count()

    return {
        "id":               str(ch.id),
        "code":             ch.code,
        "quiz_id":          str(ch.quiz_id),
        "quiz_title":       ch.quiz.title if ch.quiz else "",
        "duration_seconds": ch.duration_seconds,
        "timer_mode":       ch.timer_mode,
        "host_mode":        ch.host_mode,
        "status":           ch.status,
        "question_count":   q_count,
        "current_question_idx": ch.current_question_idx,
        "is_host":          str(ch.host_id) == str(current_user.id),
        "participants": [
            {
                "user_id": str(p.user_id),
                "name":    _display_name(p.user),
                "score":   p.score,
                "avatar":  _avatar(p.user),
                "is_host": str(p.user_id) == str(ch.host_id),
            }
            for p in participants
        ],
    }


@router.get("/{challenge_id}/leaderboard")
def get_leaderboard(
    challenge_id: UUID,
    current_user: User    = Depends(get_current_user),
    db:           Session = Depends(get_db),
):
    ch = _get_or_404(challenge_id, db)
    is_host = str(ch.host_id) == str(current_user.id)

    ranked = (
        db.query(ChallengeParticipant)
        .filter_by(challenge_id=ch.id)
        .order_by(ChallengeParticipant.score.desc())
        .all()
    )

    all_responses = db.query(ChallengeResponse).filter_by(challenge_id=ch.id).all()
    resp_by_p: dict[str, list] = {}
    for r in all_responses:
        resp_by_p.setdefault(str(r.participant_id), []).append(r)

    full = []
    for i, p in enumerate(ranked):
        resps   = resp_by_p.get(str(p.id), [])
        correct = sum(1 for r in resps if r.is_correct)
        answered = len(resps)
        avg_ms  = int(sum(r.response_time_ms for r in resps) / answered) if answered else 0
        full.append({
            "rank":         i + 1,
            "user_id":      str(p.user_id),
            "name":         _display_name(p.user),
            "score":        p.score,
            "avatar":       _avatar(p.user),
            "correct":      correct,
            "wrong":        answered - correct,
            "answered":     answered,
            "avg_speed_ms": avg_ms,
            "is_you":       str(p.user_id) == str(current_user.id),
        })

    if is_host:
        return {"is_host": True, "leaderboard": full}

    my = next((e for e in full if e["is_you"]), None)
    return {
        "is_host":    False,
        "leaderboard": full,
        "top3":       full[:3],
        "your_rank":  my["rank"]  if my else None,
        "your_score": my["score"] if my else 0,
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────

@router.websocket("/{challenge_id}/ws")
async def challenge_ws(
    challenge_id: UUID,
    ws:    WebSocket,
    token: str = Query(...),
):
    # Accept first — required before any send/close
    await ws.accept()

    db = SessionLocal()
    uid  = None
    room = None
    try:
        # Auth
        user_id_str = _decode_token(token)
        user = db.query(User).filter(User.id == user_id_str).first() if user_id_str else None
        if not user:
            await ws.send_json({"type": "error", "detail": "Unauthorized"})
            await ws.close(code=4001)
            return

        ch = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not ch:
            await ws.send_json({"type": "error", "detail": "Challenge not found"})
            await ws.close(code=4004)
            return

        is_spectator_host = (ch.host_mode == "spectator" and str(ch.host_id) == str(user.id))

        participant = db.query(ChallengeParticipant).filter_by(
            challenge_id=ch.id, user_id=user.id
        ).first()

        if not participant and not is_spectator_host:
            await ws.send_json({"type": "error", "detail": "Not a participant"})
            await ws.close(code=4003)
            return

        is_host = str(ch.host_id) == str(user.id)
        uid     = str(user.id)
        name    = _display_name(user)
        room    = _mgr.room(str(challenge_id))

        await room.add(uid, ws)

        # Announce presence (spectator host excluded from participant count)
        p_count = db.query(ChallengeParticipant).filter_by(challenge_id=ch.id).count()
        await room.broadcast({
            "type":    "participant_joined",
            "user_id": uid,
            "name":    name,
            "avatar":  _avatar(user),
            "is_host": is_host,
            "is_spectator": is_spectator_host,
            "count":   p_count,
        })

        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await ws.send_json({"type": "pong"})

            elif mtype == "next_question":
                if is_host:
                    room.signal_next()

            elif mtype == "answer":
                # Spectator host cannot submit answers
                if is_spectator_host:
                    continue
                if not participant:
                    continue
                db2 = SessionLocal()
                try:
                    p2 = db2.query(ChallengeParticipant).filter_by(id=participant.id).first()
                    if p2:
                        await _handle_answer(
                            challenge_id=ch.id,
                            participant=p2,
                            question_id=msg.get("question_id"),
                            question_idx=int(msg.get("question_idx", 0)),
                            answer=str(msg.get("answer", "")),
                            response_time_ms=float(msg.get("response_time_ms", 0)),
                            uid=uid,
                            db=db2,
                        )
                finally:
                    db2.close()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("challenge_ws error: %s", exc, exc_info=True)
    finally:
        if room and uid:
            room.remove(uid)
            try:
                await room.broadcast({
                    "type":    "presence",
                    "user_id": uid,
                    "online":  False,
                    "count":   room.count,
                })
            except Exception:
                pass
        db.close()


# ── Game logic ─────────────────────────────────────────────────────────────────

async def _run_questions(
    cid:          str,
    questions:    list,
    q_answers:    dict,   # question_id → correct_answer
    duration_sec: int,
    host_mode:    str = "participant",
    host_id:      str = "",
):
    """Server-driven question progression. Runs as a background asyncio task."""
    db = SessionLocal()
    try:
        room   = _mgr.room(cid)
        dur_ms = duration_sec * 1000

        for idx, q in enumerate(questions):
            # Update current index
            ch = db.query(Challenge).filter_by(id=cid).first()
            if not ch or ch.status != "active":
                break
            ch.current_question_idx = idx
            db.commit()

            # Broadcast question (NO correct_answer)
            await room.broadcast({
                "type":        "question",
                "idx":         idx,
                "total":       len(questions),
                "question":    q,
                "duration_ms": dur_ms,
                "server_ts":   time.time(),
            })

            await asyncio.sleep(duration_sec)

            # Snapshot scores after question
            ranked = (
                db.query(ChallengeParticipant)
                .filter_by(challenge_id=cid)
                .order_by(ChallengeParticipant.score.desc())
                .all()
            )
            scores = [
                {"user_id": str(p.user_id), "name": _display_name(p.user),
                 "score": p.score, "rank": i + 1}
                for i, p in enumerate(ranked)
            ]

            await room.broadcast({
                "type":           "question_ended",
                "idx":            idx,
                "correct_answer": q_answers.get(q["id"], ""),
                "scores":         scores,
                "next_in":        3 if idx < len(questions) - 1 else 0,
            })

            # For spectator host: send per-participant responses after each question
            if host_mode == "spectator" and host_id:
                parts = (
                    db.query(ChallengeParticipant)
                    .filter_by(challenge_id=cid)
                    .all()
                )
                correct_ans = q_answers.get(q["id"], "")
                responses_data = []
                for p in parts:
                    resp = (
                        db.query(ChallengeResponse)
                        .filter_by(participant_id=p.id, question_idx=idx)
                        .first()
                    )
                    responses_data.append({
                        "user_id":          str(p.user_id),
                        "name":             _display_name(p.user),
                        "answer":           resp.answer           if resp else None,
                        "is_correct":       resp.is_correct       if resp else False,
                        "response_time_ms": resp.response_time_ms if resp else None,
                        "points":           resp.points_earned    if resp else 0,
                    })
                await room.send_to(host_id, {
                    "type":           "question_responses",
                    "idx":            idx,
                    "correct_answer": correct_ans,
                    "responses":      responses_data,
                })

            if idx < len(questions) - 1:
                room.reset_next()
                try:
                    await asyncio.wait_for(room.wait_for_next(), timeout=300.0)
                except asyncio.TimeoutError:
                    pass  # auto-advance after 5 min if host is gone

        # Mark completed
        ch = db.query(Challenge).filter_by(id=cid).first()
        if ch:
            ch.status   = "completed"
            ch.ended_at = datetime.utcnow()
            db.commit()

        # Final leaderboard — differentiated by role
        ranked = (
            db.query(ChallengeParticipant)
            .filter_by(challenge_id=cid)
            .order_by(ChallengeParticipant.score.desc())
            .all()
        )
        full_lb = [
            {"rank": i + 1, "user_id": str(p.user_id),
             "name": _display_name(p.user), "score": p.score, "avatar": _avatar(p.user)}
            for i, p in enumerate(ranked)
        ]

        # Host: full leaderboard (works for both participant and spectator host)
        await room.send_to(host_id, {
            "type": "challenge_ended", "is_host": True, "leaderboard": full_lb,
        })

        # Participants: top 3 + own rank
        for p in ranked:
            uid = str(p.user_id)
            if uid == host_id:
                continue
            entry = next((e for e in full_lb if e["user_id"] == uid), None)
            await room.send_to(uid, {
                "type":       "challenge_ended",
                "is_host":    False,
                "top3":       full_lb[:3],
                "your_rank":  entry["rank"]  if entry else None,
                "your_score": entry["score"] if entry else 0,
            })

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Challenge %s error: %s", cid, exc)
    finally:
        db.close()


async def _handle_answer(
    challenge_id:    UUID,
    participant:     ChallengeParticipant,
    question_id:     Optional[str],
    question_idx:    int,
    answer:          str,
    response_time_ms: float,
    uid:             str,
    db:              Session,
):
    ch = db.query(Challenge).filter_by(id=challenge_id).first()
    if not ch or ch.status != "active":
        return

    # Prevent duplicate answer for the same question
    if db.query(ChallengeResponse).filter_by(
        participant_id=participant.id, question_idx=question_idx
    ).first():
        return

    q = None
    if question_id:
        try:
            q = db.query(Question).filter_by(id=UUID(question_id)).first()
        except Exception:
            pass

    is_correct = _check_answer(answer, q.correct_answer, q.question_type) if q else False
    dur_ms     = ch.duration_seconds * 1000
    points     = _calc_points(is_correct, response_time_ms, dur_ms)

    db.add(ChallengeResponse(
        participant_id=participant.id,
        challenge_id=challenge_id,
        question_id=q.id if q else None,
        question_idx=question_idx,
        answer=answer,
        is_correct=is_correct,
        points_earned=points,
        response_time_ms=response_time_ms,
    ))
    participant.score = (participant.score or 0) + points
    db.commit()

    room = _mgr.room(str(challenge_id))
    await room.send_to(uid, {
        "type":       "answer_result",
        "correct":    is_correct,
        "points":     points,
        "your_score": participant.score,
    })


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_404(challenge_id: UUID, db: Session) -> Challenge:
    ch = db.query(Challenge).filter_by(id=challenge_id).first()
    if not ch:
        raise HTTPException(404, "Challenge not found.")
    return ch


def _q_public(q: Question) -> dict:
    """Question data broadcast to clients — correct_answer intentionally omitted."""
    return {
        "id":      str(q.id),
        "text":    q.question_text,
        "type":    q.question_type,
        "options": q.options or [],
    }


def _check_answer(submitted: str, correct: str, q_type) -> bool:
    s = submitted.strip().lower()
    c = correct.strip().lower()
    if str(q_type) in ("mcq", "true_false"):
        return s == c
    return s == c or s in c or c in s


def _display_name(user) -> str:
    return user.full_name or user.username or user.email.split("@")[0] if user else "?"


def _avatar(user) -> str:
    return (_display_name(user) or "?")[0].upper()


def _ch_summary(ch: Challenge, user_id, db: Session) -> dict:
    q_count = db.query(Question).filter_by(quiz_id=ch.quiz_id).count()
    p_count = db.query(ChallengeParticipant).filter_by(challenge_id=ch.id).count()
    return {
        "id":         str(ch.id),
        "code":       ch.code,
        "quiz_title": ch.quiz.title if ch.quiz else "",
        "status":     ch.status,
        "host_mode":  ch.host_mode,
        "is_host":    str(ch.host_id) == str(user_id),
        "participants": p_count,
        "question_count": q_count,
        "created_at": ch.created_at.isoformat(),
    }
