"""
Customer Support Router
-----------------------
REST endpoints, WebSocket hub, and WhatsApp webhook handlers.

Endpoints:
  GET  /support/conversations           - list conversations (admin)
  GET  /support/conversations/{id}      - detail + messages
  POST /support/conversations/{id}/send - agent sends a message
  PATCH /support/conversations/{id}/status  - change status
  PATCH /support/conversations/{id}/assign  - assign agent
  GET  /support/agents                  - list agents
  POST /support/agents                  - create agent
  PATCH /support/agents/{id}/status     - update agent status

  POST /support/webhook/twilio          - Twilio WhatsApp incoming
  GET  /support/webhook/meta            - Meta verification challenge
  POST /support/webhook/meta            - Meta WhatsApp incoming

  WS   /support/ws                      - real-time hub (JWT via ?token=)
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

import httpx
from fastapi import (
    APIRouter, Depends, Form, HTTPException, Query,
    Request, WebSocket, WebSocketDisconnect, status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.support import SupportAgent, SupportConversation, SupportMessage
from ..models.user import User  # noqa: F401 — used in WS auth query
from ..services.ai_service import get_groq_client
from ..utils.security import get_current_admin, get_current_user, decode_token as _decode_token_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["support"])

# ── WebSocket Connection Manager ──────────────────────────────────────────────

class _WsManager:
    def __init__(self):
        self._sockets: dict[str, WebSocket] = {}          # agent_user_id → ws (admin)
        self._user_sockets: dict[str, list[WebSocket]] = {}  # conv_id → [ws] (users)

    # ── Agent connections ──────────────────────────────────────────────────────
    async def connect(self, ws: WebSocket, agent_id: str):
        await ws.accept()
        self._sockets[agent_id] = ws
        logger.info("Support WS: agent %s connected (%d total)", agent_id, len(self._sockets))

    def disconnect(self, agent_id: str):
        self._sockets.pop(agent_id, None)
        logger.info("Support WS: agent %s disconnected", agent_id)

    async def broadcast(self, payload: dict):
        dead = []
        for aid, ws in list(self._sockets.items()):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(aid)
        for aid in dead:
            self._sockets.pop(aid, None)

    async def send_to(self, agent_id: str, payload: dict):
        ws = self._sockets.get(agent_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                self._sockets.pop(agent_id, None)

    # ── User (per-conversation) connections ───────────────────────────────────
    async def connect_user(self, ws: WebSocket, conv_id: str):
        await ws.accept()
        if conv_id not in self._user_sockets:
            self._user_sockets[conv_id] = []
        self._user_sockets[conv_id].append(ws)

    def disconnect_user(self, ws: WebSocket, conv_id: str):
        lst = self._user_sockets.get(conv_id, [])
        self._user_sockets[conv_id] = [w for w in lst if w is not ws]

    async def send_to_conv_users(self, conv_id: str, payload: dict):
        sockets = list(self._user_sockets.get(conv_id, []))
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            self._user_sockets[conv_id] = [w for w in sockets if w not in dead]

ws_manager = _WsManager()

# ── AI Support System ─────────────────────────────────────────────────────────

_SUPPORT_SYSTEM = (
    "You are a friendly customer support agent for Pritis, an AI-powered quiz and learning platform. "
    "Help users with: account issues, premium subscription, platform features, quiz generation, "
    "technical problems, and study tools. "
    "Keep replies concise (under 150 words) and helpful. "
    "If a problem is complex, the user is frustrated, or they ask for a human, "
    "end your reply with exactly: [ESCALATE]"
)

_ESCALATION_KEYWORDS = {
    "human", "agent", "person", "manager", "supervisor", "real person",
    "not working", "broken", "bug", "error", "refund", "cancel", "complaint",
    "angry", "frustrated", "upset", "urgent", "help me now", "useless",
}


def _needs_escalation(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _ESCALATION_KEYWORDS)


async def _ai_reply(conversation_id: UUID, user_message: str, db: Session) -> tuple[str, bool]:
    """
    Generate AI response. Returns (reply_text, should_escalate).
    Escalates when message contains trigger words OR AI tags [ESCALATE].
    """
    if not settings.SUPPORT_AI_ENABLED:
        return "Thanks for reaching out! A human agent will assist you shortly.", True

    # Build history from last 10 messages
    recent = (
        db.query(SupportMessage)
        .filter(SupportMessage.conversation_id == conversation_id)
        .order_by(SupportMessage.created_at.desc())
        .limit(10)
        .all()
    )
    history = []
    for m in reversed(recent):
        role = "user" if m.sender_type == "user" else "assistant"
        history.append({"role": role, "content": m.content})

    # Check escalation keywords first (fast path)
    if _needs_escalation(user_message):
        return (
            "I understand your concern and want to make sure you get the best help possible. "
            "I'm connecting you with a human agent right now. Please hold on!",
            True,
        )

    client = get_groq_client()
    try:
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SUPPORT_SYSTEM},
                *history,
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
            max_tokens=300,
        )
        reply = resp.choices[0].message.content or ""
        escalate = "[ESCALATE]" in reply
        clean_reply = reply.replace("[ESCALATE]", "").strip()
        if escalate:
            clean_reply += "\n\nI'm connecting you with a human agent who can better assist you. Please hold on!"
        return clean_reply, escalate
    except Exception as exc:
        logger.error("Support AI error: %s", exc)
        return "I'm having a little trouble right now. A human agent will be with you shortly!", True


# ── WhatsApp Senders ──────────────────────────────────────────────────────────

async def _send_whatsapp(to_phone: str, body: str):
    """Send a WhatsApp message back to the user. Tries Meta first, then Twilio."""
    # Normalise phone: ensure it's in E.164 format (no whatsapp: prefix)
    phone = to_phone.replace("whatsapp:", "").strip()

    if settings.META_WHATSAPP_TOKEN and settings.META_PHONE_NUMBER_ID:
        await _send_meta(phone, body)
    elif settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN:
        await _send_twilio(phone, body)
    else:
        logger.warning("No WhatsApp provider configured — message not sent to %s", phone)


async def _send_meta(phone: str, body: str):
    url = f"https://graph.facebook.com/v19.0/{settings.META_PHONE_NUMBER_ID}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.META_WHATSAPP_TOKEN}",
                     "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": body},
            },
        )
        if r.status_code >= 400:
            logger.error("Meta send failed %d: %s", r.status_code, r.text)


async def _send_twilio(phone: str, body: str):
    from_num = settings.TWILIO_WHATSAPP_FROM
    if not from_num.startswith("whatsapp:"):
        from_num = f"whatsapp:{from_num}"
    to_num = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            data={"From": from_num, "To": to_num, "Body": body},
        )
        if r.status_code >= 400:
            logger.error("Twilio send failed %d: %s", r.status_code, r.text)


# ── Shared message-processing logic ──────────────────────────────────────────

async def _process_incoming(phone: str, text: str, channel: str, db: Session):
    """
    Handle an inbound message from any channel (webhook or web).
    Creates/finds conversation → stores message → AI or queues for agent.
    """
    # Find or create conversation
    conv = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.user_phone == phone,
            SupportConversation.status.in_(["ai", "waiting", "human"]),
        )
        .order_by(SupportConversation.created_at.desc())
        .first()
    )
    if not conv:
        conv = SupportConversation(user_phone=phone, channel=channel, status="ai")
        db.add(conv)
        db.flush()

    # Store user message
    user_msg = SupportMessage(
        conversation_id=conv.id,
        sender_type="user",
        content=text,
    )
    db.add(user_msg)
    conv.last_message_at   = datetime.utcnow()
    conv.last_message_text = text[:200]
    conv.unread_count      = (conv.unread_count or 0) + 1
    db.commit()

    # Broadcast new message to all connected agents
    await ws_manager.broadcast({
        "type": "new_message",
        "conversation_id": str(conv.id),
        "message": {
            "id": str(user_msg.id),
            "sender_type": "user",
            "content": text,
            "created_at": user_msg.created_at.isoformat(),
        },
        "conv_preview": {
            "id": str(conv.id),
            "user_phone": conv.user_phone,
            "status": conv.status,
            "last_message_text": conv.last_message_text,
            "last_message_at": conv.last_message_at.isoformat(),
            "unread_count": conv.unread_count,
        },
    })

    # AI layer (only when status is "ai")
    if conv.status == "ai":
        reply_text, escalate = await _ai_reply(conv.id, text, db)

        # Store AI message
        ai_msg = SupportMessage(
            conversation_id=conv.id,
            sender_type="ai",
            sender_name="Pritis AI",
            content=reply_text,
        )
        db.add(ai_msg)

        if escalate:
            conv.status = "waiting"
            # Find available agent and assign
            agent = _get_available_agent(db)
            if agent:
                conv.assigned_agent_id = agent.id
                conv.status = "human"

        conv.last_message_at   = datetime.utcnow()
        conv.last_message_text = reply_text[:200]
        db.commit()

        # Broadcast AI response
        await ws_manager.broadcast({
            "type": "new_message",
            "conversation_id": str(conv.id),
            "message": {
                "id": str(ai_msg.id),
                "sender_type": "ai",
                "sender_name": "Pritis AI",
                "content": reply_text,
                "created_at": ai_msg.created_at.isoformat(),
            },
            "conv_preview": {
                "id": str(conv.id),
                "user_phone": conv.user_phone,
                "status": conv.status,
                "last_message_text": conv.last_message_text,
                "last_message_at": conv.last_message_at.isoformat(),
                "unread_count": conv.unread_count,
            },
        })

        if escalate:
            await ws_manager.broadcast({
                "type": "status_changed",
                "conversation_id": str(conv.id),
                "status": conv.status,
                "assigned_agent_id": str(conv.assigned_agent_id) if conv.assigned_agent_id else None,
            })

        # Send reply back to WhatsApp
        if channel == "whatsapp":
            await _send_whatsapp(phone, reply_text)

    return conv


def _get_available_agent(db: Session) -> Optional[SupportAgent]:
    """Round-robin: pick the online agent with fewest open conversations."""
    agents = db.query(SupportAgent).filter(SupportAgent.status == "online").all()
    if not agents:
        return None
    # Count open conversations per agent
    best, best_count = None, 999
    for a in agents:
        count = (
            db.query(SupportConversation)
            .filter(
                SupportConversation.assigned_agent_id == a.id,
                SupportConversation.status.in_(["human", "waiting"]),
            )
            .count()
        )
        if count < best_count:
            best, best_count = a, count
    return best


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    content: str

class StatusRequest(BaseModel):
    status: str    # ai | human | waiting | closed

class AssignRequest(BaseModel):
    agent_id: Optional[str] = None

class AgentCreateRequest(BaseModel):
    name:    str
    email:   Optional[str] = None
    user_id: Optional[str] = None

class AgentStatusRequest(BaseModel):
    status: str   # online | offline | busy

class WebConversationRequest(BaseModel):
    phone:   str
    message: str
    name:    Optional[str] = None

class WebStartRequest(BaseModel):
    issue: Optional[str] = None

class UserMessageRequest(BaseModel):
    content: str


# ── REST: Conversations ───────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(
    status_filter: Optional[str] = Query(None, alias="status"),
    search:        Optional[str] = Query(None),
    limit:         int           = Query(50, le=200),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    q = db.query(SupportConversation).order_by(SupportConversation.last_message_at.desc())
    if status_filter and status_filter != "all":
        q = q.filter(SupportConversation.status == status_filter)
    if search:
        q = q.filter(SupportConversation.user_phone.ilike(f"%{search}%"))
    convs = q.limit(limit).all()
    return [_conv_summary(c) for c in convs]


@router.get("/conversations/{conv_id}")
def get_conversation(
    conv_id:       UUID,
    current_admin: User = Depends(get_current_admin),
    db:            Session = Depends(get_db),
):
    conv = _get_conv_or_404(conv_id, db)
    return {
        **_conv_summary(conv),
        "messages": [_msg_dict(m) for m in conv.messages],
    }


@router.post("/conversations/{conv_id}/send", status_code=201)
async def agent_send_message(
    conv_id: UUID,
    payload: SendMessageRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conv = _get_conv_or_404(conv_id, db)
    if conv.status == "closed":
        raise HTTPException(400, "Conversation is closed.")

    msg = SupportMessage(
        conversation_id=conv.id,
        sender_type="agent",
        sender_name=current_admin.full_name or current_admin.username or "Agent",
        content=payload.content,
    )
    db.add(msg)
    conv.status            = "human"
    conv.last_message_at   = datetime.utcnow()
    conv.last_message_text = payload.content[:200]
    conv.unread_count      = 0
    db.commit()

    msg_dict = _msg_dict(msg)
    await ws_manager.broadcast({
        "type": "new_message",
        "conversation_id": str(conv.id),
        "message": msg_dict,
        "conv_preview": _conv_summary(conv),
    })
    # Push message to user's browser WebSocket in real-time
    await ws_manager.send_to_conv_users(str(conv.id), {
        "type": "message",
        **msg_dict,
        "conv_status": conv.status,
    })

    # Send back to WhatsApp if channel is whatsapp
    if conv.channel == "whatsapp":
        asyncio.create_task(_send_whatsapp(conv.user_phone, payload.content))

    return msg_dict


@router.patch("/conversations/{conv_id}/status")
async def update_conv_status(
    conv_id: UUID,
    payload: StatusRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    valid = {"ai", "human", "waiting", "closed"}
    if payload.status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    conv = _get_conv_or_404(conv_id, db)
    conv.status = payload.status
    db.commit()
    event = {"type": "status_changed", "conversation_id": str(conv.id), "status": conv.status}
    await ws_manager.broadcast(event)
    await ws_manager.send_to_conv_users(str(conv.id), event)
    return _conv_summary(conv)


@router.patch("/conversations/{conv_id}/assign")
async def assign_conversation(
    conv_id: UUID,
    payload: AssignRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    conv = _get_conv_or_404(conv_id, db)
    if payload.agent_id:
        agent = db.query(SupportAgent).filter_by(id=UUID(payload.agent_id)).first()
        if not agent:
            raise HTTPException(404, "Agent not found.")
        conv.assigned_agent_id = agent.id
        conv.status = "human"
    else:
        conv.assigned_agent_id = None
    db.commit()
    return _conv_summary(conv)


# Incoming web messages (for testing without WhatsApp)
@router.post("/conversations/web", status_code=201)
async def web_incoming(
    payload: WebConversationRequest,
    db: Session = Depends(get_db),
):
    """Simulate incoming message via web (no auth — use for widget / testing)."""
    conv = await _process_incoming(payload.phone, payload.message, "web", db)
    return {"conversation_id": str(conv.id)}


# ── REST: Agents ──────────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    agents = db.query(SupportAgent).order_by(SupportAgent.name).all()
    return [_agent_dict(a, db) for a in agents]


@router.post("/agents", status_code=201)
def create_agent(
    payload: AgentCreateRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if payload.email:
        exists = db.query(SupportAgent).filter_by(email=payload.email).first()
        if exists:
            raise HTTPException(400, "An agent with this email already exists.")
    agent = SupportAgent(
        name=payload.name,
        email=payload.email or None,
        user_id=UUID(payload.user_id) if payload.user_id else None,
    )
    db.add(agent)
    db.commit()
    return _agent_dict(agent, db)


@router.patch("/agents/{agent_id}/status")
async def update_agent_status(
    agent_id: UUID,
    payload: AgentStatusRequest,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    valid = {"online", "offline", "busy"}
    if payload.status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    agent = db.query(SupportAgent).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(404, "Agent not found.")
    agent.status = payload.status
    db.commit()
    await ws_manager.broadcast({
        "type": "agent_status",
        "agent_id": str(agent.id),
        "status": agent.status,
    })
    return _agent_dict(agent, db)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(
    agent_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    agent = db.query(SupportAgent).filter_by(id=agent_id).first()
    if not agent:
        raise HTTPException(404, "Agent not found.")
    db.delete(agent)
    db.commit()


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
def support_stats(
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    total   = db.query(SupportConversation).count()
    by_ai   = db.query(SupportConversation).filter_by(status="ai").count()
    waiting = db.query(SupportConversation).filter_by(status="waiting").count()
    human   = db.query(SupportConversation).filter_by(status="human").count()
    closed  = db.query(SupportConversation).filter_by(status="closed").count()
    agents  = db.query(SupportAgent).count()
    online  = db.query(SupportAgent).filter_by(status="online").count()
    msgs    = db.query(SupportMessage).count()
    return {
        "total_conversations": total,
        "ai": by_ai, "waiting": waiting, "human": human, "closed": closed,
        "total_agents": agents, "online_agents": online,
        "total_messages": msgs,
    }


# ── User-facing Web Chat Endpoints ───────────────────────────────────────────

@router.post("/web/start", status_code=201)
async def start_web_chat(
    payload: WebStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create or resume an authenticated user's web support conversation."""
    conv = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.user_id == current_user.id,
            SupportConversation.status.in_(["ai", "waiting", "human"]),
        )
        .order_by(SupportConversation.created_at.desc())
        .first()
    )
    if not conv:
        phone = current_user.email or str(current_user.id)
        name  = current_user.full_name or current_user.email or "User"
        conv  = SupportConversation(
            user_id=current_user.id,
            user_phone=phone,
            user_name=name,
            channel="web",
            status="ai",
        )
        db.add(conv)
        db.flush()

    # Process initial issue message if provided
    if payload.issue:
        user_msg = SupportMessage(
            conversation_id=conv.id,
            sender_type="user",
            content=payload.issue,
        )
        db.add(user_msg)
        conv.last_message_at   = datetime.utcnow()
        conv.last_message_text = payload.issue[:200]
        conv.unread_count      = (conv.unread_count or 0) + 1
        db.flush()

        reply_text, escalate = await _ai_reply(conv.id, payload.issue, db)
        ai_msg = SupportMessage(
            conversation_id=conv.id,
            sender_type="ai",
            sender_name="Pritis AI",
            content=reply_text,
        )
        db.add(ai_msg)
        if escalate:
            conv.status = "waiting"
            agent = _get_available_agent(db)
            if agent:
                conv.assigned_agent_id = agent.id
                conv.status = "human"
        conv.last_message_at   = datetime.utcnow()
        conv.last_message_text = reply_text[:200]

    db.commit()
    db.refresh(conv)

    await ws_manager.broadcast({"type": "new_conversation", "conv": _conv_summary(conv)})
    return {**_conv_summary(conv), "messages": [_msg_dict(m) for m in conv.messages]}


@router.get("/web/chat")
def get_web_chat(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current user's active support conversation (if any)."""
    conv = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.user_id == current_user.id,
            SupportConversation.status.in_(["ai", "waiting", "human"]),
        )
        .order_by(SupportConversation.created_at.desc())
        .first()
    )
    if not conv:
        return {"active": False}
    return {"active": True, **_conv_summary(conv), "messages": [_msg_dict(m) for m in conv.messages]}


@router.post("/web/message", status_code=201)
async def user_send_message(
    payload: UserMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User sends a message in their active support conversation."""
    conv = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.user_id == current_user.id,
            SupportConversation.status.in_(["ai", "waiting", "human"]),
        )
        .order_by(SupportConversation.created_at.desc())
        .first()
    )
    if not conv:
        raise HTTPException(404, "No active support conversation.")

    msg = SupportMessage(
        conversation_id=conv.id,
        sender_type="user",
        content=payload.content,
    )
    db.add(msg)
    conv.last_message_at   = datetime.utcnow()
    conv.last_message_text = payload.content[:200]
    conv.unread_count      = (conv.unread_count or 0) + 1
    db.flush()

    msg_dict = _msg_dict(msg)
    await ws_manager.broadcast({
        "type": "new_message",
        "conversation_id": str(conv.id),
        "message": msg_dict,
        "conv_preview": _conv_summary(conv),
    })

    ai_response = None
    if conv.status == "ai":
        reply_text, escalate = await _ai_reply(conv.id, payload.content, db)
        ai_msg = SupportMessage(
            conversation_id=conv.id,
            sender_type="ai",
            sender_name="Pritis AI",
            content=reply_text,
        )
        db.add(ai_msg)
        if escalate:
            conv.status = "waiting"
            agent = _get_available_agent(db)
            if agent:
                conv.assigned_agent_id = agent.id
                conv.status = "human"
        conv.last_message_at   = datetime.utcnow()
        conv.last_message_text = reply_text[:200]
        ai_response = _msg_dict(ai_msg)

        await ws_manager.broadcast({
            "type": "new_message",
            "conversation_id": str(conv.id),
            "message": ai_response,
            "conv_preview": _conv_summary(conv),
        })
        await ws_manager.send_to_conv_users(str(conv.id), {
            "type": "message", **ai_response, "conv_status": conv.status,
        })
        if escalate:
            status_event = {"type": "status_changed", "conversation_id": str(conv.id), "status": conv.status}
            await ws_manager.broadcast(status_event)
            await ws_manager.send_to_conv_users(str(conv.id), status_event)

    db.commit()
    return {"message": msg_dict, "ai_response": ai_response, "conv_status": conv.status}


@router.post("/web/close", status_code=200)
async def close_web_chat(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User closes their active support conversation."""
    conv = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.user_id == current_user.id,
            SupportConversation.status.in_(["ai", "waiting", "human"]),
        )
        .order_by(SupportConversation.created_at.desc())
        .first()
    )
    if not conv:
        return {"closed": False}
    conv.status = "closed"
    db.commit()
    event = {"type": "status_changed", "conversation_id": str(conv.id), "status": "closed"}
    await ws_manager.broadcast(event)
    await ws_manager.send_to_conv_users(str(conv.id), event)
    return {"closed": True}


@router.websocket("/user-ws/{conv_id}")
async def user_websocket(
    ws:      WebSocket,
    conv_id: UUID,
    token:   str = Query(...),
    db:      Session = Depends(get_db),
):
    """Real-time WebSocket for user — receives agent/AI messages and status changes."""
    user_id_str = _decode_token_str(token)
    user = db.query(User).filter_by(id=user_id_str).first() if user_id_str else None
    if not user:
        await ws.close(code=4001)
        return

    conv = db.query(SupportConversation).filter_by(id=conv_id).first()
    if not conv or str(conv.user_id) != str(user.id):
        await ws.close(code=4003)
        return

    conv_id_str = str(conv_id)
    await ws_manager.connect_user(ws, conv_id_str)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect_user(ws, conv_id_str)


# ── WhatsApp Webhooks ─────────────────────────────────────────────────────────

@router.post("/webhook/twilio")
async def twilio_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive inbound WhatsApp message from Twilio."""
    form = await request.form()
    from_num = str(form.get("From", "")).replace("whatsapp:", "").strip()
    body     = str(form.get("Body", "")).strip()
    if not from_num or not body:
        return {"status": "ignored"}
    await _process_incoming(from_num, body, "whatsapp", db)
    # Return TwiML empty response
    return {"status": "ok"}


@router.get("/webhook/meta")
def meta_verify(
    hub_mode:       str = Query(..., alias="hub.mode"),
    hub_challenge:  str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
):
    """Meta verification challenge."""
    if hub_verify_token != settings.META_WEBHOOK_VERIFY_TOKEN:
        raise HTTPException(403, "Invalid verify token.")
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(hub_challenge)


@router.post("/webhook/meta")
async def meta_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive inbound WhatsApp message from Meta Cloud API."""
    try:
        data = await request.json()
    except Exception:
        return {"status": "bad_request"}

    try:
        entry    = data["entry"][0]
        change   = entry["changes"][0]
        value    = change["value"]
        messages = value.get("messages", [])
        for m in messages:
            if m.get("type") != "text":
                continue
            phone = m["from"]
            text  = m["text"]["body"]
            await _process_incoming(phone, text, "whatsapp", db)
    except (KeyError, IndexError) as exc:
        logger.warning("Meta webhook parse error: %s | payload: %s", exc, data)
    return {"status": "ok"}


# ── WebSocket Hub ─────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def support_websocket(
    ws:    WebSocket,
    token: str = Query(...),
    db:    Session = Depends(get_db),
):
    """
    Real-time hub for agents.
    Connect with: ws://.../api/support/ws?token=<JWT>
    Events received: new_message, new_conversation, status_changed, agent_status
    Events sent by client: ping (keepalive)
    """
    # Authenticate via JWT query param
    user_id_str = _decode_token_str(token)
    user = db.query(User).filter_by(id=user_id_str).first() if user_id_str else None
    if not user or not user.is_admin:
        await ws.close(code=4001)
        return

    agent_id = str(user.id)
    await ws_manager.connect(ws, agent_id)

    # Mark linked SupportAgent as online
    agent = db.query(SupportAgent).filter_by(user_id=user.id).first()
    if agent and agent.status != "online":
        agent.status = "online"
        db.commit()
        await ws_manager.broadcast({"type": "agent_status", "agent_id": str(agent.id), "status": "online"})

    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(agent_id)
        if agent:
            agent.status = "offline"
            db.commit()
            await ws_manager.broadcast({"type": "agent_status", "agent_id": str(agent.id), "status": "offline"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conv_or_404(conv_id: UUID, db: Session) -> SupportConversation:
    conv = db.query(SupportConversation).filter_by(id=conv_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    return conv


def _msg_dict(m: SupportMessage) -> dict:
    return {
        "id":          str(m.id),
        "sender_type": m.sender_type,
        "sender_name": m.sender_name,
        "content":     m.content,
        "created_at":  m.created_at.isoformat(),
    }


def _conv_summary(c: SupportConversation) -> dict:
    return {
        "id":                str(c.id),
        "user_phone":        c.user_phone,
        "user_name":         c.user_name,
        "channel":           c.channel,
        "status":            c.status,
        "assigned_agent_id": str(c.assigned_agent_id) if c.assigned_agent_id else None,
        "assigned_agent":    c.agent.name if c.agent else None,
        "unread_count":      c.unread_count or 0,
        "last_message_text": c.last_message_text,
        "last_message_at":   c.last_message_at.isoformat() if c.last_message_at else None,
        "created_at":        c.created_at.isoformat(),
    }


def _agent_dict(a: SupportAgent, db: Session) -> dict:
    open_convs = (
        db.query(SupportConversation)
        .filter(
            SupportConversation.assigned_agent_id == a.id,
            SupportConversation.status.in_(["human", "waiting"]),
        )
        .count()
    )
    return {
        "id":         str(a.id),
        "name":       a.name,
        "email":      a.email,
        "status":     a.status,
        "user_id":    str(a.user_id) if a.user_id else None,
        "open_conversations": open_convs,
        "created_at": a.created_at.isoformat(),
    }
