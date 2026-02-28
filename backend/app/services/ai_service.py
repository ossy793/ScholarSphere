import json
import re
import logging
from typing import List, Dict, Any
from groq import Groq
from ..config import settings

logger = logging.getLogger(__name__)

_client = None


def get_groq_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


PARSE_GENERATE_SYSTEM_PROMPT = """You are an expert quiz creator. You will receive educational content in any format — numbered lists, bullet points, exam papers, lecture notes, mixed Q&A, or unstructured prose.

Your task:
1. Identify and extract ALL distinct questions from the text.
2. For each question, determine the best type and generate a complete quiz entry.

Output ONLY a JSON array (no prose, no markdown fences). Each element must have:
- "question_text": string (the clean, complete question)
- "question_type": one of "mcq", "true_false", "short_answer"
- "options": exactly 4 strings for mcq; ["True","False"] for true_false; null for short_answer
- "correct_answer": string (exact option text for mcq/true_false; concise answer for short_answer)
- "explanation": string (one sentence explaining why the answer is correct)

Rules:
- Extract questions even if they appear mid-paragraph or are mixed with answers.
- If the original content already has options/answers, preserve them and fill any gaps.
- If no options exist, generate 4 plausible options for MCQ questions.
- Use "true_false" for yes/no or "is it true that…" style questions.
- Use "short_answer" for open-ended, definition, or calculation questions.
- MCQ must always have exactly 4 options.
- Start your response with [ and end with ]."""

OPTIONS_SYSTEM_PROMPT = """You are a quiz question formatter. Given a numbered list of questions, output ONLY a JSON array of the same length — no prose, no markdown fences.

Each element must be a JSON object with these exact keys:
- "question_text": string (the question, lightly cleaned if needed)
- "question_type": one of "mcq", "true_false", "short_answer"
- "options": array of strings (exactly 4 for mcq; ["True","False"] for true_false; null for short_answer)
- "correct_answer": string
- "explanation": string (one sentence)

Auto-detect the best question type:
- Use "true_false" for yes/no or is-it-true questions
- Use "mcq" for factual questions with 4 plausible options
- Use "short_answer" for open-ended or definition questions
- MCQ must have exactly 4 options
Output ONLY the raw JSON array. Start with [ and end with ]."""

SYSTEM_PROMPT = """You are a quiz question generator. Given learning material, output ONLY a JSON array — no prose, no markdown fences, no commentary before or after.

Each element in the array must be a JSON object with these exact keys:
- "question_text": string
- "question_type": one of "mcq", "true_false", "short_answer"
- "options": array of strings (required for mcq and true_false; omit for short_answer)
- "correct_answer": string — for mcq use the exact option text; for true_false use "True" or "False"; for short_answer use the expected answer
- "explanation": string — one sentence explaining the correct answer

Rules:
- MCQ must have exactly 4 options.
- true_false options must be exactly ["True", "False"].
- Mix question types across the set.
- Output ONLY the raw JSON array. Start your response with [ and end with ]."""


def _extract_json_array(raw: str) -> list:
    """
    Try multiple strategies to extract a JSON array from the model response.
    Handles: bare array, markdown fences, wrapping object with 'questions' key.
    """
    text = raw.strip()

    # Strategy 1: strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Strategy 2: try to parse the entire cleaned text as JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        # Model returned {"questions": [...]} or similar wrapper
        if isinstance(parsed, dict):
            for key in ("questions", "quiz", "items", "data"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
    except json.JSONDecodeError:
        pass

    # Strategy 3: find the outermost [...] block (greedy)
    bracket_match = re.search(r"\[[\s\S]*\]", text)
    if bracket_match:
        try:
            parsed = json.loads(bracket_match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # Strategy 4: find all individual {...} objects and wrap them
    objects = re.findall(r"\{[\s\S]*?\}", text)
    collected = []
    for obj_str in objects:
        try:
            collected.append(json.loads(obj_str))
        except json.JSONDecodeError:
            continue
    if collected:
        return collected

    logger.error("Could not extract JSON from AI response. Raw:\n%s", raw[:500])
    raise ValueError("AI did not return a valid JSON array. Try again or check the content.")


def _build_type_instruction(allowed_types: List[str]) -> str:
    """
    Convert a list of user-facing type labels into an AI prompt instruction
    that constrains which question_type values the model may use.

    User-facing labels:  "mcq", "short_answer", "essay"
    DB types:            "mcq", "true_false", "short_answer"
    """
    has_mcq   = "mcq"          in allowed_types
    has_short = "short_answer" in allowed_types
    has_essay = "essay"        in allowed_types

    if not (has_mcq or has_short or has_essay):
        # Fallback — treat as all allowed
        has_mcq = has_short = has_essay = True

    db_types: List[str] = []
    desc_parts: List[str] = []

    if has_mcq:
        db_types.extend(["mcq", "true_false"])
        desc_parts.append("multiple choice (mcq) and true/false questions")

    if has_short or has_essay:
        if "short_answer" not in db_types:
            db_types.append("short_answer")
        if has_short:
            desc_parts.append("short answer questions (concise, 1-2 sentence answers)")
        if has_essay:
            desc_parts.append("theory/essay questions (short_answer type requiring detailed explanations)")

    instruction = (
        f"ONLY generate: {', '.join(desc_parts)}. "
        f"Allowed question_type values: {', '.join(db_types)}."
    )

    if has_essay and not has_short:
        instruction += (
            " For short_answer entries, write essay-style questions that demand"
            " detailed theoretical explanations (multiple paragraphs expected as answer)."
        )
    elif has_short and has_essay:
        instruction += (
            " Mix concise short_answer questions with essay-style questions"
            " that require thorough theoretical explanations."
        )

    return instruction


def generate_questions(
    content: str,
    num_questions: int = 10,
    allowed_types: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Generate quiz questions from text content using Groq.
    allowed_types: list of "mcq", "short_answer", "essay". Defaults to all three.
    Returns a validated list of question dicts.
    """
    if not content or not content.strip():
        raise ValueError("Content is empty — nothing to generate questions from.")

    if not allowed_types:
        allowed_types = ["mcq", "short_answer", "essay"]

    type_instruction = _build_type_instruction(allowed_types)
    client = get_groq_client()

    user_prompt = (
        f"Generate exactly {num_questions} quiz questions from the content below. "
        f"{type_instruction} "
        f"Output ONLY the JSON array.\n\n"
        f"Content:\n{content}"
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.5,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("Groq raw response (first 300 chars): %s", raw[:300])

    questions = _extract_json_array(raw)
    validated = _validate_questions(questions)

    if not validated:
        raise ValueError("AI returned questions in an unexpected format. Please try again.")

    return validated


def generate_options_for_questions(questions: List[str]) -> List[Dict[str, Any]]:
    """
    Given a plain list of question strings, use Groq to detect types,
    generate options, and pick correct answers for each one.
    Returns a list of question dicts in the same order as the input.
    """
    if not questions:
        raise ValueError("No questions provided.")

    client = get_groq_client()
    questions_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))

    user_prompt = (
        f"For each of the following {len(questions)} questions, generate appropriate "
        f"options and correct answers. Return a JSON array with exactly {len(questions)} "
        f"elements in the same order.\n\nQuestions:\n{questions_text}"
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": OPTIONS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("generate_options raw (first 300): %s", raw[:300])
    items = _extract_json_array(raw)

    # Map back 1-to-1, padding with fallbacks for any missing entries
    result = []
    for i, q_text in enumerate(questions):
        if i < len(items) and isinstance(items[i], dict):
            item = items[i]
            item["question_text"] = q_text          # preserve original phrasing
            q_type = item.get("question_type", "mcq")
            if q_type not in ("mcq", "true_false", "short_answer"):
                q_type = "mcq"
                item["question_type"] = q_type
            if q_type == "true_false":
                item["options"] = ["True", "False"]
            elif q_type == "mcq":
                opts = item.get("options") or []
                while len(opts) < 4:
                    opts.append("")
                item["options"] = opts[:4]
            else:
                item["options"] = None
            result.append(item)
        else:
            result.append({
                "question_text": q_text,
                "question_type": "short_answer",
                "options": None,
                "correct_answer": "",
                "explanation": "",
            })
    return result


def parse_and_generate_from_content(content: str) -> List[Dict[str, Any]]:
    """
    Given any unstructured educational content (past papers, notes, Q&A),
    detect all questions and generate complete quiz entries with options and answers.
    """
    if not content or not content.strip():
        raise ValueError("Content is empty — nothing to analyze.")

    client = get_groq_client()

    user_prompt = (
        "Extract all questions from the following content and generate complete quiz entries "
        "(with options and correct answers) for each. Return ONLY the JSON array.\n\n"
        f"Content:\n{content}"
    )

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": PARSE_GENERATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()
    logger.debug("parse_and_generate raw (first 300): %s", raw[:300])
    items = _extract_json_array(raw)
    validated = _validate_questions(items)

    if not validated:
        raise ValueError(
            "AI could not detect any questions in the provided content. "
            "Please ensure the text contains questions and try again."
        )

    return validated


def _validate_questions(questions: list) -> List[Dict[str, Any]]:
    """Filter and normalise question objects."""
    valid = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        if not q.get("question_text") or not q.get("correct_answer"):
            continue

        q_type = q.get("question_type", "mcq")
        if q_type not in ("mcq", "true_false", "short_answer"):
            q["question_type"] = "mcq"
            q_type = "mcq"

        # Ensure options exist for choice-based questions
        if q_type == "true_false":
            q["options"] = ["True", "False"]
        elif q_type == "mcq" and not q.get("options"):
            continue  # skip MCQ with no options

        q["order_index"] = i
        valid.append(q)
    return valid
