import json
import re
import logging
from typing import List, Dict, Any
from groq import Groq
from ..config import settings

logger = logging.getLogger(__name__)

_client = None
_openai_client = None


def get_groq_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
        logger.info("OpenAI client initialised for quiz generation (gpt-4o-mini)")
    return _openai_client


PARSE_GENERATE_SYSTEM_PROMPT = """You are an expert quiz creator. You will receive educational content in any format — numbered lists, bullet points, exam papers, lecture notes, mixed Q&A, or unstructured prose.

Your task:
1. Identify and extract ALL distinct questions from the text.
2. For each question, determine the best type and generate a complete quiz entry.

Output ONLY a JSON array (no prose, no markdown fences). Each element must have:
- "question_text": string (the clean, complete question)
- "question_type": one of "mcq", "true_false", "short_answer"
- "options": array of strings for mcq — preserve the EXACT number of options from the document (typically 2–6); do NOT pad to 4 if fewer exist, do NOT truncate to 4 if more exist; null for short_answer
- "correct_answer": string (exact option text for mcq/true_false; concise answer for short_answer)
- "explanation": string (one sentence explaining why the answer is correct)

CRITICAL — ANSWER EXTRACTION PRIORITY:
Many past question documents already contain answers. You MUST detect and use existing answers FIRST. Only generate an answer when none can be found.

Detect these common answer formats:
1. ANSWER KEY AT END: A section like "1. C  2. A  3. True  4. B" or "Q1: C, Q2: A" — match each answer back to its question by number.
2. INLINE AFTER QUESTION: Answer appears right after the question, e.g. "Answer: C", "Ans: B", "Correct: True", "The answer is D".
3. MARKED OPTION: One option is explicitly marked — bold, asterisk (*), "ANS:", "✓", or any label indicating it is correct.
4. OPTION ANNOTATION: An option has a note like "(correct)", "(right answer)", or similar beside it.

General rules:
- Extract questions even if they appear mid-paragraph or are mixed with answers.
- Preserve ALL original options exactly as written; only fill gaps if fewer than 4 options exist for MCQ.
- If no options exist for an MCQ-style question, generate 4 plausible options.
- Use "short_answer" for open-ended, definition, or calculation questions, including yes/no style questions.
- MCQ must preserve the original option count from the document (minimum 2, maximum 6).
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
- Use "short_answer" for open-ended, definition, or yes/no questions
- MCQ must have exactly 4 options
Output ONLY the raw JSON array. Start with [ and end with ]."""

SYSTEM_PROMPT = """You are an expert academic quiz question generator. Given learning material, output ONLY a JSON array — no prose, no markdown fences, no commentary before or after.

Each element in the array must be a JSON object with these exact keys:
- "question_text": string
- "question_type": one of "mcq", "short_answer"
- "options": array of strings (required for mcq; omit for short_answer)
- "correct_answer": string — for mcq use the exact option text; for true_false use "True" or "False"; for short_answer use the expected answer
- "explanation": string — one sentence explaining the correct answer

QUALITY RULES — follow these strictly:
1. NO REPETITION: Every question must test a distinct concept or fact. Do NOT generate two questions that ask the same thing with different wording. Do NOT repeat the same correct answer across multiple MCQ questions.
2. LOGICAL QUESTIONS ONLY: Each question must be clear, unambiguous, and directly answerable from the content. Avoid vague or trick questions.
3. SPREAD COVERAGE: Cover different sections, topics, and difficulty levels across the entire document — not just the beginning.
4. MCQ DISTRACTORS: Wrong options must be plausible but clearly incorrect. Avoid obviously wrong options like "None of the above" or random unrelated words.
5. MCQ must have exactly 4 distinct options with only one correct answer.
6. short_answer questions must have a concise, factually precise expected answer.
7. Mix question types and difficulty levels (recall, application, analysis) across the set.
8. Output ONLY the raw JSON array. Start your response with [ and end with ]."""


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
        db_types.extend(["mcq"])
        desc_parts.append("multiple choice (mcq)")

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


# ── Function-calling tool definition ─────────────────────────────────────────
# Groq's Llama 3.3 supports OpenAI-compatible function calling.
# Forcing the model to call submit_quiz_questions guarantees the output is
# valid JSON with the correct schema — no fragile text parsing needed.

_QUIZ_FC_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_quiz_questions",
        "description": "Submit the complete list of generated quiz questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "All generated quiz questions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question_text":  {"type": "string"},
                            "question_type":  {"type": "string", "enum": ["mcq", "short_answer"]},
                            "options":        {"type": "array",  "items": {"type": "string"}},
                            "correct_answer": {"type": "string"},
                            "explanation":    {"type": "string"},
                        },
                        "required": ["question_text", "question_type", "correct_answer", "explanation"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}


def _call_with_fc(messages: list, temperature: float, max_tokens: int) -> list:
    """
    Call OpenAI gpt-4o-mini with function-calling forced to submit_quiz_questions.
    Returns the raw questions list from the tool arguments.
    Falls back to text-based extraction if the tool call is missing.
    """
    client = _get_openai_client()
    response = client.chat.completions.create(
        model      = "gpt-4o-mini",
        messages   = messages,
        tools      = [_QUIZ_FC_TOOL],
        tool_choice= {"type": "function", "name": "submit_quiz_questions"},
        temperature= temperature,
        max_tokens = max_tokens,
    )
    choice = response.choices[0]

    # Primary path: tool call with guaranteed-valid JSON arguments
    if choice.message.tool_calls:
        tc = choice.message.tool_calls[0]
        if tc.function.name == "submit_quiz_questions":
            args = json.loads(tc.function.arguments)
            questions = args.get("questions", [])
            if questions:
                logger.debug("FC: received %d questions via tool call", len(questions))
                return questions

    # Fallback: model returned plain text instead of calling the tool
    raw = choice.message.content or ""
    logger.warning("FC: tool call absent — falling back to text extraction")
    return _extract_json_array(raw)


def generate_questions(
    content: str,
    num_questions: int = 10,
    allowed_types: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Generate quiz questions from text content using Groq function calling.
    allowed_types: list of "mcq", "short_answer", "essay". Defaults to all three.
    Returns a validated list of question dicts.
    """
    if not content or not content.strip():
        raise ValueError("Content is empty — nothing to generate questions from.")

    if not allowed_types:
        allowed_types = ["mcq", "short_answer", "essay"]

    type_instruction = _build_type_instruction(allowed_types)

    user_prompt = (
        f"Generate exactly {num_questions} quiz questions from the content below. "
        f"{type_instruction} "
        f"Each question must cover a DIFFERENT concept — no two questions should test the same idea. "
        f"Spread questions across the full content, not just the beginning. "
        f"Call submit_quiz_questions with all questions.\n\n"
        f"Content:\n{content}"
    )

    questions = _call_with_fc(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.5,
        max_tokens=4096,
    )

    validated = _validate_questions(questions)
    if not validated:
        raise ValueError("AI returned questions in an unexpected format. Please try again.")
    return validated


def generate_options_for_questions(questions: List[str]) -> List[Dict[str, Any]]:
    """
    Given a plain list of question strings, use Groq function calling to detect
    types, generate options, and pick correct answers for each one.
    Returns a list of question dicts in the same order as the input.
    """
    if not questions:
        raise ValueError("No questions provided.")

    questions_text = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))

    user_prompt = (
        f"For each of the following {len(questions)} questions, detect the best type, "
        f"generate options, and pick the correct answer. "
        f"Call submit_quiz_questions with exactly {len(questions)} entries in the same order.\n\n"
        f"Questions:\n{questions_text}"
    )

    items = _call_with_fc(
        messages=[
            {"role": "system", "content": OPTIONS_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.4,
        max_tokens=4096,
    )

    logger.debug("generate_options FC: %d items returned", len(items))

    # Map back 1-to-1, padding with fallbacks for any missing entries
    result = []
    for i, q_text in enumerate(questions):
        if i < len(items) and isinstance(items[i], dict):
            item = items[i]
            item["question_text"] = q_text          # preserve original phrasing
            q_type = item.get("question_type", "mcq")
            if q_type not in ("mcq", "short_answer"):
                q_type = "mcq"
                item["question_type"] = q_type
            if q_type == "mcq":
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


def parse_and_generate_from_content(content: str, answer_hint: str = "") -> List[Dict[str, Any]]:
    """
    Given any unstructured educational content (past papers, notes, Q&A),
    detect all questions and generate complete quiz entries with options and answers.
    answer_hint: optional user instruction about how answers are structured in the document.
    """
    if not content or not content.strip():
        raise ValueError("Content is empty — nothing to analyze.")

    hint_line = f"\n\nUSER INSTRUCTION ABOUT ANSWER FORMAT: {answer_hint.strip()}" if answer_hint and answer_hint.strip() else ""

    # Split large content into batches of ~15,000 chars to avoid token limits
    BATCH_SIZE = 15000
    chunks = [content[i:i+BATCH_SIZE] for i in range(0, len(content), BATCH_SIZE)]

    all_validated = []
    for chunk in chunks:
        chunk_prompt = (
            "Extract ALL questions from the content below and generate complete quiz entries "
            "(with options and correct answers). "
            "Use existing answers from the document wherever possible. "
            "Do NOT generate duplicate questions — each must test a different concept. "
            "Call submit_quiz_questions with every question found."
            f"{hint_line}\n\nContent:\n{chunk}"
        )
        items = _call_with_fc(
            messages=[
                {"role": "system", "content": PARSE_GENERATE_SYSTEM_PROMPT},
                {"role": "user",   "content": chunk_prompt},
            ],
            temperature=0.4,
            max_tokens=8192,
        )
        logger.debug("parse_and_generate FC chunk: %d items", len(items))
        validated = _validate_questions(items)
        all_validated.extend(validated)

    if not all_validated:
        raise ValueError(
            "AI could not detect any questions in the provided content. "
            "Please ensure the text contains questions and try again."
        )

    return all_validated


RECOMMENDATIONS_SYSTEM_PROMPT = """You are an expert educational analyst specialising in personalised study plans.
You will receive a student's quiz performance data grouped by course and topic, including their actual wrong answers.

Output ONLY a JSON array (no prose, no markdown fences). Each element represents one quiz topic and must have these exact keys:
- "course_name": string
- "topic_name": string (the quiz topic/title)
- "performance_score": number (score percentage 0-100, one decimal place)
- "priority": exactly one of "High Priority", "Medium Priority", "Low Priority"
- "weak_concepts": array of 2-4 specific concept strings inferred from the wrong answers
- "recommended_concepts": array of 2-4 closely related concepts the student should study next
- "study_focus": string (1-2 sentences of specific, actionable advice based on the actual errors — NOT generic)
- "study_time": string — exactly "2 hours" for High Priority, "1 hour" for Medium Priority, "30 mins" for Low Priority

Priority rules (strictly enforced):
- score < 50  → "High Priority"
- score 50-70 → "Medium Priority"
- score > 70  → "Low Priority"

Critical requirements:
- weak_concepts must reflect the actual questions the student got wrong — be specific, not generic
- recommended_concepts must directly address the identified weak areas
- study_focus must be tailored to the student's specific errors — avoid boilerplate advice
- Output ONLY the raw JSON array. Start with [ and end with ]."""


def generate_recommendations(performance_data: list) -> List[Dict[str, Any]]:
    """
    Given course-grouped performance data (including wrong answers with question text),
    call Groq to produce a structured recommendation table.
    Returns a list of recommendation dicts ready to send to the frontend.
    """
    if not performance_data:
        return []

    # Format performance data as readable text for the prompt
    lines = []
    for course in performance_data:
        lines.append(f"\nCourse: {course['course_name']}")
        for quiz in course["quizzes"]:
            lines.append(
                f"  Topic: {quiz['quiz_title']} | "
                f"Score: {quiz['score']}% ({quiz['correct']}/{quiz['total']} correct)"
            )
            wrong = quiz.get("wrong_questions", [])
            if wrong:
                lines.append("  Wrong answers:")
                for i, w in enumerate(wrong, 1):
                    lines.append(
                        f"    {i}. Q: {w['question']} | "
                        f"Student answered: {w['user_answer']} | "
                        f"Correct: {w['correct_answer']}"
                    )
            else:
                lines.append("  (No wrong answers recorded — high scorer)")

    performance_text = "\n".join(lines)
    client = get_groq_client()
    user_prompt = (
        "Generate personalised study recommendations for the following student performance data. "
        "Return one recommendation entry per quiz topic listed. "
        "Output ONLY the JSON array.\n\n"
        f"Performance Data:\n{performance_text}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": RECOMMENDATIONS_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        raw = response.choices[0].message.content.strip()
        logger.debug("Recommendations raw (first 300): %s", raw[:300])
        return _extract_json_array(raw)
    except Exception as exc:
        logger.error("Recommendation generation failed: %s", exc)
        raise ValueError(f"AI recommendation generation failed: {exc}")


def _normalize_text(text: str) -> str:
    """Lowercase, strip punctuation and whitespace for similarity comparison."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _validate_questions(questions: list) -> List[Dict[str, Any]]:
    """Filter, normalise, and deduplicate question objects."""
    valid = []
    seen_texts: list[str] = []

    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            continue
        if not q.get("question_text") or not q.get("correct_answer"):
            continue

        q_type = q.get("question_type", "mcq")
        if q_type not in ("mcq", "short_answer"):
            q["question_type"] = "mcq"
            q_type = "mcq"

        # Ensure options exist for MCQ
        if q_type == "mcq" and not q.get("options"):
            continue  # skip MCQ with no options

        # Deduplicate: skip if question text is too similar to an already-accepted one
        norm = _normalize_text(q["question_text"])
        norm_words = set(norm.split())
        is_duplicate = False
        for seen in seen_texts:
            seen_words = set(seen.split())
            if not seen_words:
                continue
            overlap = len(norm_words & seen_words) / max(len(norm_words), len(seen_words), 1)
            if overlap > 0.75:  # >75% word overlap = likely duplicate
                is_duplicate = True
                break

        if is_duplicate:
            logger.debug("Skipping duplicate question: %s", q["question_text"][:80])
            continue

        seen_texts.append(norm)
        q["order_index"] = i
        valid.append(q)

    return valid


_SEMANTIC_GRADE_PROMPT = """\
You are a strict but fair quiz grader. You will receive a list of short-answer or theory questions, \
the expected correct answer for each, and the student's answer.

Your job: decide if each student answer is CORRECT or INCORRECT based on meaning, not exact wording.

Rules:
- CORRECT if the student's answer captures the core concept, even if phrased differently or uses synonyms.
- CORRECT if the student gives additional correct detail beyond the expected answer.
- INCORRECT if the student's answer is missing the key concept, is vague, or is factually wrong.
- INCORRECT if the student left the answer blank or wrote something irrelevant.
- Do NOT penalise for spelling mistakes, grammar, or different sentence structure.
- Do NOT require the exact words from the expected answer.

Output ONLY a JSON array of objects, one per question, in the same order received.
Each object must have exactly two keys:
  "index": the integer index of the question (0-based)
  "correct": true or false

No prose, no explanation, no markdown. Only the JSON array.\
"""


def grade_short_answers(questions: list[dict]) -> list[bool]:
    """
    Semantically grade a batch of short-answer / theory questions using AI.

    Each item in `questions` must have:
      - "index": int
      - "question": str
      - "expected": str
      - "user_answer": str

    Returns a list of bools in the same order as the input.
    Falls back to exact-match if the AI call fails.
    """
    if not questions:
        return []

    client = get_groq_client()

    user_content = json.dumps(questions, ensure_ascii=False)

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SEMANTIC_GRADE_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        raw = response.choices[0].message.content.strip()
        results = _extract_json_array(raw)
        if not isinstance(results, list):
            raise ValueError("Expected JSON array")
        # Build index → correct map
        grade_map = {item["index"]: bool(item["correct"]) for item in results if "index" in item}
        return [grade_map.get(q["index"], False) for q in questions]
    except Exception as exc:
        logger.warning("Semantic grading failed, falling back to exact match: %s", exc)
        # Fallback: case-insensitive exact match
        return [
            q["user_answer"].strip().lower() == q["expected"].strip().lower()
            for q in questions
        ]


# ── Visual question generation (scanned / image-based PDFs) ───────────────────

def _build_vision_image_blocks(page_images: list) -> list:
    """Convert a list of JPEG image bytes into Groq vision content blocks."""
    import base64
    blocks = []
    for img_bytes in page_images:
        b64 = base64.b64encode(img_bytes).decode()
        blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        del b64
    return blocks


def _run_vision_request(content: list, max_tokens: int = 4096) -> str:
    """
    Send a multi-image + text message to the best available Groq vision model.
    Falls back through models in _GROQ_VISION_MODELS order.
    Raises ValueError if all models fail.
    """
    import os
    from ..utils.file_parser import _GROQ_VISION_MODELS

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set — visual processing unavailable.")

    from groq import Groq
    client = Groq(api_key=api_key)

    image_blocks = [b for b in content if b.get("type") == "image_url"]
    text_block   = next((b for b in content if b.get("type") == "text"), None)

    for model in _GROQ_VISION_MODELS:
        try:
            # Llama 3.2 only supports 1 image — send the middle page
            if "3.2" in model and len(image_blocks) > 1:
                mid = len(image_blocks) // 2
                msg_content = [image_blocks[mid]] + ([text_block] if text_block else [])
            else:
                msg_content = content

            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": msg_content}],
                max_tokens=max_tokens,
                temperature=0.4,
            )
            raw = response.choices[0].message.content.strip()
            if raw:
                logger.info("Vision request succeeded with model %s", model)
                return raw
        except Exception as exc:
            logger.warning("Vision request failed with %s: %s", model, exc)
            continue

    raise ValueError("All Groq vision models failed. Please try again.")


def generate_questions_from_images(
    page_images: list,
    num_questions: int = 10,
    allowed_types: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """
    Generate quiz questions directly from rendered PDF page images (no text extraction).
    Used as a fallback for scanned / image-based PDFs in the AI Generate feature.

    Processes pages in batches of 5 (Llama 4 multi-image limit) and combines results.
    """
    if not page_images:
        raise ValueError("No page images provided for visual question generation.")

    if not allowed_types:
        allowed_types = ["mcq", "short_answer", "essay"]

    type_instruction = _build_type_instruction(allowed_types)
    BATCH = 5   # max images per Groq vision request

    all_validated: List[Dict[str, Any]] = []
    total_pages = len(page_images)
    num_batches = max(1, (total_pages + BATCH - 1) // BATCH)
    questions_per_batch = max(2, num_questions // num_batches)

    for i in range(0, total_pages, BATCH):
        batch = page_images[i:i + BATCH]
        page_range = f"pages {i + 1}–{min(i + BATCH, total_pages)}"

        image_blocks = _build_vision_image_blocks(batch)
        text_block = {
            "type": "text",
            "text": (
                f"The images show {page_range} from a student's academic document (scanned PDF). "
                f"Read the content carefully and generate exactly {questions_per_batch} quiz questions "
                f"based on what you can see on these pages. "
                f"{type_instruction} "
                f"Each question must test a DIFFERENT concept from the document. "
                f"Output ONLY a JSON array. Each element must have: "
                f"'question_text' (string), 'question_type' (mcq/true_false/short_answer), "
                f"'options' (array for mcq/true_false; null for short_answer), "
                f"'correct_answer' (string), 'explanation' (one sentence). "
                f"MCQ must have exactly 4 options. true_false options must be [\"True\",\"False\"]. "
                f"Start your response with [ and end with ]."
            ),
        }

        try:
            raw = _run_vision_request(image_blocks + [text_block])
            items = _extract_json_array(raw)
            validated = _validate_questions(items)
            all_validated.extend(validated)
        except Exception as exc:
            logger.warning("Visual question generation failed for %s: %s", page_range, exc)
            continue

    if not all_validated:
        raise ValueError(
            "Could not generate questions from this scanned document. "
            "The document may be too blurry or low-resolution. Please try a clearer scan."
        )

    # Deduplicate across batches then trim to requested count
    final = _validate_questions(all_validated)   # re-runs dedup across all batches
    return final[:num_questions]


def parse_questions_from_images(
    page_images: list,
    answer_hint: str = "",
) -> List[Dict[str, Any]]:
    """
    Extract existing questions from rendered PDF page images (Input Questions feature).
    Used as a fallback for scanned past-paper PDFs where text extraction is poor.

    Processes pages in batches of 5 and combines all detected questions.
    """
    if not page_images:
        raise ValueError("No page images provided for visual question parsing.")

    BATCH = 5
    hint_line = f"\nUSER INSTRUCTION: {answer_hint.strip()}" if answer_hint and answer_hint.strip() else ""
    all_validated: List[Dict[str, Any]] = []

    for i in range(0, len(page_images), BATCH):
        batch = page_images[i:i + BATCH]
        page_range = f"pages {i + 1}–{min(i + BATCH, len(page_images))}"

        image_blocks = _build_vision_image_blocks(batch)
        text_block = {
            "type": "text",
            "text": (
                f"The images show {page_range} from a scanned academic exam paper or question document. "
                f"Extract ALL questions you can read from these pages. "
                f"For each question, generate a complete quiz entry with type, options, and correct answer. "
                f"Use any answer keys, markings, or annotations visible on the pages to detect correct answers. "
                f"{hint_line}"
                f"Do NOT generate duplicate questions — each question must test a different concept. "
                f"Output ONLY a JSON array. Each element must have: "
                f"'question_text' (string), 'question_type' (mcq/true_false/short_answer), "
                f"'options' (array for mcq/true_false; null for short_answer), "
                f"'correct_answer' (string), 'explanation' (one sentence). "
                f"MCQ: preserve the original number of options (2–6). "
                f"Start with [ and end with ]."
            ),
        }

        try:
            raw = _run_vision_request(image_blocks + [text_block], max_tokens=8192)
            items = _extract_json_array(raw)
            validated = _validate_questions(items)
            all_validated.extend(validated)
        except Exception as exc:
            logger.warning("Visual question parsing failed for %s: %s", page_range, exc)
            continue

    if not all_validated:
        raise ValueError(
            "Could not detect any questions in this scanned document. "
            "Please ensure the document contains readable questions and try again."
        )

    return _validate_questions(all_validated)   # final dedup across all batches
