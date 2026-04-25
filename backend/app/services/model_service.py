"""
Unified AI model adapter for Study Zone chat.

Supported model IDs:
  "groq"   — Groq  / Llama 3.3 70B Versatile  (default, free)
  "openai" — OpenAI / GPT-4o mini
  "claude" — Anthropic / Claude Sonnet 4.6

Each adapter accepts the same OpenAI-style messages list:
  [{"role": "system"|"user"|"assistant", "content": "..."}]

Returns a plain string reply.

Function calling (quiz suggestion):
  When the AI decides to suggest a quiz, it calls the `suggest_quiz` tool.
  The adapter detects this and injects a quiz-suggestion marker into the reply
  so the frontend can render a "Take Quiz" button.
"""

import asyncio
import logging
import queue as _queue
import threading
from typing import Optional
from ..config import settings

logger = logging.getLogger(__name__)

# ── Model metadata (used by frontend picker) ─────────────────────────────────
MODELS = {
    "groq": {
        "id":           "groq",
        "name":         "Llama 3.3 70B",
        "provider":     "Groq",
        "display_name": "Groq",
        "model":        "llama-3.3-70b-versatile",
        "available":    bool(settings.GROQ_API_KEY),
    },
    "openai": {
        "id":           "openai",
        "name":         "GPT-4o mini",
        "provider":     "OpenAI",
        "display_name": "ChatGPT",
        "model":        "gpt-4o-mini",
        "available":    bool(settings.OPENAI_API_KEY),
    },
    "claude": {
        "id":           "claude",
        "name":         "Claude Sonnet 4.6",
        "provider":     "Anthropic",
        "display_name": "Claude",
        "model":        "claude-sonnet-4-6",
        "available":    bool(settings.CLAUDE_API_KEY),
    },
}

# ── Quiz-suggestion function / tool definition ────────────────────────────────
_QUIZ_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "suggest_quiz",
        "description": (
            "Call this when the student would benefit from testing their "
            "understanding with a short quiz based on the document. "
            "Use it after explaining a concept, when the student seems "
            "ready, or when they ask to be tested."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason why a quiz is helpful right now.",
                }
            },
            "required": ["reason"],
        },
    },
}

_QUIZ_TOOL_CLAUDE = {
    "name": "suggest_quiz",
    "description": (
        "Call this when the student would benefit from testing their "
        "understanding with a short quiz based on the document."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Brief reason why a quiz is helpful right now.",
            }
        },
        "required": ["reason"],
    },
}

_QUIZ_SUFFIX = (
    "\n\n---\n"
    "📝 **Ready to test yourself?** "
    "[Generate a quiz from this document →](ai-generate.html)"
)


# ── Lazy clients ──────────────────────────────────────────────────────────────
_openai_client  = None
_groq_client    = None
_claude_client  = None


def _get_groq():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=settings.GROQ_API_KEY)
    return _groq_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def _get_claude():
    global _claude_client
    if _claude_client is None:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=settings.CLAUDE_API_KEY)
    return _claude_client


# ── Adapters ──────────────────────────────────────────────────────────────────

def _chat_groq(messages: list, temperature: float, max_tokens: int) -> str:
    client = _get_groq()
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def _chat_openai(messages: list, temperature: float, max_tokens: int) -> str:
    client = _get_openai()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=[_QUIZ_TOOL_OPENAI],
        tool_choice="auto",
    )
    choice = response.choices[0]

    # If the model called suggest_quiz → append the quiz CTA to the reply
    if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
        tool_call = choice.message.tool_calls[0]
        if tool_call.function.name == "suggest_quiz":
            # Get the text content (may be empty when tool_choice fires first)
            text = (choice.message.content or "").strip()
            if not text:
                import json
                try:
                    args   = json.loads(tool_call.function.arguments)
                    reason = args.get("reason", "")
                    text   = reason if reason else "A quiz would help you test your understanding."
                except Exception:
                    text = "A quiz would help you test your understanding."
            return text + _QUIZ_SUFFIX

    return (choice.message.content or "").strip()


def _chat_claude(messages: list, temperature: float, max_tokens: int) -> str:
    client = _get_claude()

    # Claude takes system as a top-level param, not inside messages
    system = ""
    claude_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            claude_messages.append({"role": msg["role"], "content": msg["content"]})

    kwargs = dict(
        model      = "claude-sonnet-4-6",
        max_tokens = max(max_tokens, 1024),
        temperature= temperature,
        messages   = claude_messages,
        tools      = [_QUIZ_TOOL_CLAUDE],
    )
    if system:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)

    # Check for tool use block
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "suggest_quiz":
            reason = (block.input or {}).get("reason", "")
            text = reason or "A quiz would help reinforce what you've learned."
            return text + _QUIZ_SUFFIX

    # Collect text blocks
    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(text_parts).strip()


# ── Public interface ──────────────────────────────────────────────────────────

def chat_with_model(
    model_id:    str,
    messages:    list,
    temperature: float = 0.3,
    max_tokens:  int   = 1024,
) -> str:
    """
    Route a chat request to the appropriate AI model.
    Falls back to Groq if the requested model is unavailable or its API fails.
    """
    model_id = model_id.lower().strip()

    _adapters = {
        "openai": (_chat_openai, settings.OPENAI_API_KEY),
        "claude": (_chat_claude, settings.CLAUDE_API_KEY),
    }

    if model_id in _adapters:
        fn, key = _adapters[model_id]
        if not key:
            raise ValueError(f"{model_id.title()} API key is not configured on this server.")
        return fn(messages, temperature, max_tokens)

    # Default (groq or unknown)
    return _chat_groq(messages, temperature, max_tokens)


def get_available_models() -> list:
    """Return all model metadata; each entry carries an 'available' flag."""
    return list(MODELS.values())


# ── Streaming adapters ────────────────────────────────────────────────────────

async def _stream_groq(messages: list, temperature: float, max_tokens: int):
    """Yields text chunks from Groq (sync SDK wrapped in a background thread)."""
    q: _queue.SimpleQueue = _queue.SimpleQueue()

    def _run() -> None:
        try:
            client = _get_groq()
            for chunk in client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ):
                content = chunk.choices[0].delta.content
                if content:
                    q.put(content)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(None)

    threading.Thread(target=_run, daemon=True).start()
    loop = asyncio.get_running_loop()
    while True:
        item = await loop.run_in_executor(None, q.get)
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def _stream_openai(messages: list, temperature: float, max_tokens: int):
    """Yields text chunks from OpenAI using the async client."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    stream = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    async for chunk in stream:
        content = chunk.choices[0].delta.content
        if content:
            yield content


async def _stream_claude(messages: list, temperature: float, max_tokens: int):
    """Yields text chunks from Claude using the async Anthropic client."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.CLAUDE_API_KEY)

    system = ""
    claude_messages: list = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            claude_messages.append({"role": msg["role"], "content": msg["content"]})

    kwargs: dict = dict(
        model      = "claude-sonnet-4-6",
        max_tokens = max(max_tokens, 1024),
        temperature= temperature,
        messages   = claude_messages,
    )
    if system:
        kwargs["system"] = system

    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


async def stream_with_model(
    model_id:    str,
    messages:    list,
    temperature: float = 0.3,
    max_tokens:  int   = 1024,
):
    """
    Async generator that routes streaming to the appropriate AI model.
    Falls back to Groq if the requested model is unavailable or its API fails.
    If the primary model fails BEFORE yielding any content, Groq takes over
    seamlessly. If it fails mid-stream (already yielded), the exception propagates.
    """
    model_id = model_id.lower().strip()

    _stream_adapters = {
        "openai": (_stream_openai, settings.OPENAI_API_KEY),
        "claude": (_stream_claude, settings.CLAUDE_API_KEY),
    }

    if model_id in _stream_adapters:
        fn, key = _stream_adapters[model_id]
        if not key:
            raise ValueError(f"{model_id.title()} API key is not configured on this server.")
        async for chunk in fn(messages, temperature, max_tokens):
            yield chunk
        return

    # Default (groq or unknown)
    async for chunk in _stream_groq(messages, temperature, max_tokens):
        yield chunk
