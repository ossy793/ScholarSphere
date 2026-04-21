"""
Friends / Direct Messages Router
---------------------------------
REST
  GET  /friends/search?username=   — find user by username (profile must be complete)
  GET  /friends/conversations       — list my conversations (latest message + unread count)
  POST /friends/conversations       — start or get conversation with a user by username
  GET  /friends/conversations/{id}/messages   — paginated message history
  POST /friends/conversations/{id}/messages   — send a message (REST fallback)
  PATCH /friends/conversations/{id}/read      — mark all unread messages as read

WebSocket
  WS /friends/ws/{conversation_id}?token=...  — real-time bidirectional chat
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.conversation import Conversation, DirectMessage
from ..models.notification import Notification
from ..models.user import User
from ..utils.security import get_current_user, decode_token  # noqa: F401
from ..utils.friend_email import send_friend_message_email

router = APIRouter(prefix="/friends", tags=["friends"])


# ── In-memory WebSocket manager ───────────────────────────────────────────────

class ConnectionManager:
    """Tracks open WebSocket connections keyed by (conversation_id, user_id)."""

    def __init__(self):
        # conversation_id → { user_id → WebSocket }
        self._rooms: Dict[str, Dict[str, WebSocket]] = {}

    def join(self, conv_id: str, user_id: str, ws: WebSocket):
        self._rooms.setdefault(conv_id, {})[user_id] = ws

    def leave(self, conv_id: str, user_id: str):
        room = self._rooms.get(conv_id, {})
        room.pop(user_id, None)
        if not room:
            self._rooms.pop(conv_id, None)

    def is_online(self, conv_id: str, user_id: str) -> bool:
        return user_id in self._rooms.get(conv_id, {})

    async def send_to(self, conv_id: str, user_id: str, payload: dict):
        ws = self._rooms.get(conv_id, {}).get(user_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def broadcast(self, conv_id: str, payload: dict, exclude_user: str | None = None):
        for uid, ws in list(self._rooms.get(conv_id, {}).items()):
            if uid == exclude_user:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                pass


manager = ConnectionManager()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_conversation(user_a_id, user_b_id, db: Session) -> Conversation:
    """Return existing conversation or create one. Canonical order: smaller UUID first."""
    uid1, uid2 = sorted([user_a_id, user_b_id], key=lambda u: str(u))
    conv = (
        db.query(Conversation)
        .filter(Conversation.user1_id == uid1, Conversation.user2_id == uid2)
        .first()
    )
    if not conv:
        conv = Conversation(user1_id=uid1, user2_id=uid2)
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return conv


def _other_user(conv: Conversation, me_id) -> User:
    return conv.user2 if conv.user1_id == me_id else conv.user1


def _unread_count(conv: Conversation, me_id) -> int:
    return sum(1 for m in conv.messages if m.sender_id != me_id and not m.is_read)


def _latest_message(conv: Conversation):
    return conv.messages[-1] if conv.messages else None


def _serialize_msg(m: DirectMessage) -> dict:
    return {
        "id":         str(m.id),
        "sender_id":  str(m.sender_id),
        "content":    m.content,
        "is_read":    m.is_read,
        "created_at": m.created_at.isoformat(),
    }


def _serialize_conv(conv: Conversation, me_id) -> dict:
    other = _other_user(conv, me_id)
    latest = _latest_message(conv)
    return {
        "id":           str(conv.id),
        "other_user": {
            "id":                  str(other.id),
            "full_name":           other.full_name,
            "username":            other.username,
            "profile_picture_url": other.profile_picture_url,
        },
        "unread_count":    _unread_count(conv, me_id),
        "latest_message":  _serialize_msg(latest) if latest else None,
        "created_at":      conv.created_at.isoformat(),
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class StartConversationRequest(BaseModel):
    username: str


class SendMessageRequest(BaseModel):
    content: str


# ── REST Endpoints ────────────────────────────────────────────────────────────

@router.get("/search")
def search_user(
    username: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Find a user by exact username (must have a complete profile)."""
    if not current_user.profile_completed:
        raise HTTPException(status_code=403, detail="Please complete your profile to use Ask Your Friends.")

    username = username.strip().lower()
    if username == (current_user.username or "").lower():
        raise HTTPException(status_code=400, detail="You can't message yourself.")

    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=404, detail="No user found with that username.")
    if not user.profile_completed:
        raise HTTPException(status_code=404, detail="No user found with that username.")

    return {
        "id":                  str(user.id),
        "full_name":           user.full_name,
        "username":            user.username,
        "profile_picture_url": user.profile_picture_url,
        "school":              user.school,
        "level":               user.level,
        "course":              user.course,
    }


@router.get("/conversations")
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.profile_completed:
        raise HTTPException(status_code=403, detail="Please complete your profile to use Ask Your Friends.")

    from sqlalchemy import or_
    convs = (
        db.query(Conversation)
        .filter(
            or_(
                Conversation.user1_id == current_user.id,
                Conversation.user2_id == current_user.id,
            )
        )
        .all()
    )
    # Sort by latest message (newest first)
    def _sort_key(c):
        latest = _latest_message(c)
        return latest.created_at if latest else c.created_at

    convs.sort(key=_sort_key, reverse=True)
    return [_serialize_conv(c, current_user.id) for c in convs]


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def start_conversation(
    payload: StartConversationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.profile_completed:
        raise HTTPException(status_code=403, detail="Please complete your profile to use Ask Your Friends.")

    username = payload.username.strip().lower()
    if username == (current_user.username or "").lower():
        raise HTTPException(status_code=400, detail="You can't message yourself.")

    other = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not other or not other.profile_completed:
        raise HTTPException(status_code=404, detail="User not found.")

    conv = _get_or_create_conversation(current_user.id, other.id, db)
    return _serialize_conv(conv, current_user.id)


@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: UUID,
    before: str = Query(None),   # ISO timestamp for pagination
    limit: int = Query(50, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if current_user.id not in (conv.user1_id, conv.user2_id):
        raise HTTPException(status_code=403, detail="Not your conversation.")

    q = db.query(DirectMessage).filter(DirectMessage.conversation_id == conversation_id)
    if before:
        try:
            ts = datetime.fromisoformat(before)
            q = q.filter(DirectMessage.created_at < ts)
        except ValueError:
            pass
    msgs = q.order_by(DirectMessage.created_at.desc()).limit(limit).all()
    msgs.reverse()
    return [_serialize_msg(m) for m in msgs]


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
def send_message(
    conversation_id: UUID,
    payload: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """REST fallback for sending a message when WebSocket is unavailable."""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if current_user.id not in (conv.user1_id, conv.user2_id):
        raise HTTPException(status_code=403, detail="Not your conversation.")

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(content) > 4000:
        raise HTTPException(status_code=400, detail="Message is too long (max 4000 characters).")

    msg = DirectMessage(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        content=content,
    )
    db.add(msg)

    other = _other_user(conv, current_user.id)
    _maybe_notify(conv, msg, current_user, other, db)

    db.commit()
    db.refresh(msg)
    return _serialize_msg(msg)


@router.patch("/conversations/{conversation_id}/read")
def mark_read(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if current_user.id not in (conv.user1_id, conv.user2_id):
        raise HTTPException(status_code=403, detail="Not your conversation.")

    updated = 0
    for m in conv.messages:
        if m.sender_id != current_user.id and not m.is_read:
            m.is_read = True
            updated += 1
    if updated:
        db.commit()
    return {"marked_read": updated}


# ── Notification helper ────────────────────────────────────────────────────────

def _maybe_notify(conv: Conversation, msg: DirectMessage, sender: User, recipient: User, db: Session):
    """Create in-app notification + push + optional email for a new direct message."""
    from ..services.push_service import notify_user
    title   = f"Message from {sender.full_name}"
    preview = msg.content[:200] + ("…" if len(msg.content) > 200 else "")
    notify_user(db, recipient.id, title, preview, url="/friends.html", commit=True)

    # Email when recipient is not in the WebSocket room
    if not manager.is_online(str(conv.id), str(recipient.id)):
        try:
            send_friend_message_email(
                to_email=recipient.email,
                sender_name=sender.full_name,
                message_preview=msg.content[:300],
            )
        except Exception:
            pass


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/ws/{conversation_id}")
async def ws_chat(
    websocket: WebSocket,
    conversation_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    # Authenticate via token query param
    user_id_str = decode_token(token)
    if not user_id_str:
        await websocket.close(code=4001)
        return

    user_id = uuid.UUID(user_id_str)
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        await websocket.close(code=4001)
        return

    # Verify membership
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        await websocket.close(code=4003)
        return

    conv = db.query(Conversation).filter(Conversation.id == conv_uuid).first()
    if not conv or user_id not in (conv.user1_id, conv.user2_id):
        await websocket.close(code=4003)
        return

    await websocket.accept()
    manager.join(conversation_id, user_id_str, websocket)

    # Notify the other participant that this user is now online
    other = _other_user(conv, user_id)
    other_id_str = str(other.id)
    await manager.send_to(conversation_id, other_id_str, {
        "type": "presence",
        "user_id": user_id_str,
        "online": True,
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "message":
                content = (data.get("content") or "").strip()
                if not content or len(content) > 4000:
                    continue

                # Persist
                msg = DirectMessage(
                    conversation_id=conv.id,
                    sender_id=user_id,
                    content=content,
                )
                db.add(msg)
                _maybe_notify(conv, msg, user, other, db)
                db.commit()
                db.refresh(msg)

                out = {"type": "message", **_serialize_msg(msg)}

                # Echo back to sender
                await websocket.send_json(out)
                # Push to recipient
                await manager.send_to(conversation_id, other_id_str, out)

            elif msg_type == "read":
                # Mark messages as read
                updated = 0
                for m in conv.messages:
                    if m.sender_id != user_id and not m.is_read:
                        m.is_read = True
                        updated += 1
                if updated:
                    db.commit()
                    await manager.send_to(conversation_id, other_id_str, {
                        "type": "read_receipt",
                        "reader_id": user_id_str,
                    })

            elif msg_type == "typing":
                await manager.send_to(conversation_id, other_id_str, {
                    "type": "typing",
                    "user_id": user_id_str,
                    "is_typing": bool(data.get("is_typing")),
                })

    except WebSocketDisconnect:
        pass
    finally:
        manager.leave(conversation_id, user_id_str)
        db.close()
        # Notify other user that this user went offline
        await manager.send_to(conversation_id, other_id_str, {
            "type": "presence",
            "user_id": user_id_str,
            "online": False,
        })
