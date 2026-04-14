"""
Unified AI model adapter for Study Zone chat.

Supported model IDs:
  "groq"   — Groq  / Llama 3.3 70B Versatile  (default, free)
  "openai" — OpenAI / GPT-4o mini
  "gemini" — Google / Gemini 2.0 Flash

Each adapter accepts the same OpenAI-style messages list:
  [{"role": "system"|"user"|"assistant", "content": "..."}]

Returns a plain string reply.

Function calling (quiz suggestion):
  When the AI decides to suggest a quiz, it calls the `suggest_quiz` tool.
  The adapter detects this and injects a quiz-suggestion marker into the reply
  so the frontend can render a "Take Quiz" button.
"""

import logging
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
    "gemini": {
        "id":           "gemini",
        "name":         "Gemini 2.0 Flash",
        "provider":     "Google",
        "display_name": "Gemini",
        "model":        "gemini-2.0-flash",
        "available":    bool(settings.GEMINI_API_KEY),
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

_QUIZ_TOOL_GEMINI = {
    "name": "suggest_quiz",
    "description": (
        "Call this when the student would benefit from testing their "
        "understanding with a short quiz based on the document."
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
}

_QUIZ_SUFFIX = (
    "\n\n---\n"
    "📝 **Ready to test yourself?** "
    "[Generate a quiz from this document →](ai-generate.html)"
)


# ── Lazy clients ──────────────────────────────────────────────────────────────
_openai_client = None
_groq_client   = None


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


def _chat_gemini(messages: list, temperature: float, max_tokens: int) -> str:
    import google.generativeai as genai
    from google.generativeai.types import FunctionDeclaration, Tool

    genai.configure(api_key=settings.GEMINI_API_KEY)

    # Convert OpenAI-style messages to Gemini format
    # Gemini uses "user" / "model" roles and a separate system_instruction
    system_instruction = ""
    gemini_history = []

    for msg in messages:
        role    = msg["role"]
        content = msg["content"]
        if role == "system":
            system_instruction = content
        elif role == "user":
            gemini_history.append({"role": "user", "parts": [content]})
        elif role == "assistant":
            gemini_history.append({"role": "model", "parts": [content]})

    quiz_fn = FunctionDeclaration(
        name        = "suggest_quiz",
        description = _QUIZ_TOOL_GEMINI["description"],
        parameters  = _QUIZ_TOOL_GEMINI["parameters"],
    )
    quiz_tool = Tool(function_declarations=[quiz_fn])

    model = genai.GenerativeModel(
        model_name          = "gemini-2.0-flash",
        system_instruction  = system_instruction or None,
        tools               = [quiz_tool],
        generation_config   = genai.types.GenerationConfig(
            temperature      = temperature,
            max_output_tokens= max_tokens,
        ),
    )

    # All messages except the last become history; the last is the new message
    if gemini_history:
        *history, last_turn = gemini_history
        chat = model.start_chat(history=history)
        response = chat.send_message(last_turn["parts"][0])
    else:
        chat = model.start_chat()
        response = chat.send_message("")

    # Check for function call
    candidate = response.candidates[0]
    for part in candidate.content.parts:
        if hasattr(part, "function_call") and part.function_call.name == "suggest_quiz":
            reason = dict(part.function_call.args).get("reason", "")
            text = reason or "A quiz would help reinforce what you've learned."
            return text + _QUIZ_SUFFIX

    return response.text.strip()


# ── Public interface ──────────────────────────────────────────────────────────

def chat_with_model(
    model_id:    str,
    messages:    list,
    temperature: float = 0.3,
    max_tokens:  int   = 1024,
) -> str:
    """
    Route a chat request to the appropriate AI model.
    Falls back to Groq if the requested model is unavailable.
    """
    model_id = model_id.lower().strip()

    if model_id == "openai":
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI key not set — falling back to Groq")
            return _chat_groq(messages, temperature, max_tokens)
        return _chat_openai(messages, temperature, max_tokens)

    if model_id == "gemini":
        if not settings.GEMINI_API_KEY:
            logger.warning("Gemini key not set — falling back to Groq")
            return _chat_groq(messages, temperature, max_tokens)
        return _chat_gemini(messages, temperature, max_tokens)

    # Default: Groq
    return _chat_groq(messages, temperature, max_tokens)


def get_available_models() -> list:
    """Return all model metadata; each entry carries an 'available' flag."""
    return list(MODELS.values())
