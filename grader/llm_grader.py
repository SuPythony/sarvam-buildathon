"""Grade OCR'd answers against a question paper with any OpenRouter model."""
import json
import os
import re
import time

import requests

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
SUGGESTED = ["deepseek/deepseek-v4.1-flash", "qwen/qwen3.8-flash", "google/gemini-3.8-flash",
             "anthropic/claude-sonnet-5.5", "openai/gpt-6.1-sol", "anthropic/claude-haiku-5.5"]

LANGUAGES = {
    "en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "od-IN": "Odia",
    "pa-IN": "Punjabi", "ta-IN": "Tamil", "te-IN": "Telugu",
}

SYSTEM = """You are a strict but fair teacher grading a student's handwritten assignment.
The student's answers were transcribed by OCR, so ignore minor spelling/formatting noise
caused by OCR; grade the substance. Award partial credit where reasoning is partly correct.
Use the max marks stated in the question paper for each question. If marks are not stated,
assume 10 per question. If a question is unanswered, award 0 and say so.
Write all feedback in {feedback_language}, addressed to the student, warm but specific.

Respond with ONLY a JSON object matching:
{{
  "student_name": string | null,
  "questions": [
    {{"number": string, "question": string, "max_marks": number, "awarded": number,
      "student_answer": string,   // the student's answer for this question, copied from the OCR text (trim to ~800 chars)
      "feedback": string}}
  ],
  "overall_feedback": string,
  "narration": string   // 60-120 word spoken summary for the student in {feedback_language}: total score, strongest answer, what to fix. Plain sentences, no symbols or markdown, numbers written as words.
}}"""


class GraderError(RuntimeError):
    pass


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise GraderError(f"no JSON in model output: {text[:500]}")
    return json.loads(text[start:end + 1])


def _letter(pct: float) -> str:
    for cut, g in ((90, "A+"), (80, "A"), (70, "B"), (60, "C"), (50, "D")):
        if pct >= cut:
            return g
    return "F"


_models_cache: tuple[float, list[str]] = (0.0, [])


def available_models() -> list[str]:
    """OpenRouter model ids (public endpoint, cached for an hour)."""
    global _models_cache
    if time.time() - _models_cache[0] < 3600 and _models_cache[1]:
        return _models_cache[1]
    try:
        r = requests.get(MODELS_URL, timeout=20)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json()["data"])
    except Exception:
        return _models_cache[1] or SUGGESTED
    _models_cache = (time.time(), ids)
    return ids


def _chat(key: str, body: dict) -> requests.Response:
    return requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Title": "Red Pen"},
        json=body, timeout=600,
    )


def grade(questions_text: str, answers_text: str, answer_key: str | None = None,
          feedback_language: str = "en-IN", reasoning_effort: str = "medium",
          model: str | None = None) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise GraderError("OPENROUTER_API_KEY not set")
    model = model or DEFAULT_MODEL

    user = f"## QUESTION PAPER\n{questions_text}\n\n"
    if answer_key:
        user += f"## ANSWER KEY / RUBRIC\n{answer_key}\n\n"
    user += f"## STUDENT ANSWERS (OCR)\n{answers_text}"
    system = SYSTEM.format(feedback_language=LANGUAGES.get(feedback_language, "English"))

    body = {
        "model": model,
        "temperature": 0.1,
        "reasoning": {"effort": reasoning_effort},  # ignored by models without reasoning
        "max_tokens": 16000,  # reasoning tokens count against this
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    r = _chat(key, body)
    if r.status_code == 400 and "response_format" in r.text:
        body.pop("response_format")  # model lacks JSON mode; the prompt still asks for JSON
        r = _chat(key, body)
    if r.status_code == 402:
        raise GraderError(f"OpenRouter credits are too low for {model}. Add credits at "
                          "https://openrouter.ai/settings/credits or pick a cheaper model.")
    if not r.ok:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:500]
        raise GraderError(f"OpenRouter {r.status_code} ({model}): {msg}")
    data = r.json()
    if "choices" not in data:
        raise GraderError(f"openrouter: {data.get('error', data)}")
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise GraderError("model ran out of tokens; try a lower marking depth")
    result = _parse_json(choice["message"].get("content"))

    # Recompute totals so arithmetic doesn't depend on the LLM.
    qs = result.get("questions", [])
    for q in qs:
        q["max_marks"] = float(q.get("max_marks") or 0)
        q["awarded"] = min(max(float(q.get("awarded") or 0), 0.0), q["max_marks"])
    total = sum(q["awarded"] for q in qs)
    max_total = sum(q["max_marks"] for q in qs)
    pct = round(100 * total / max_total, 1) if max_total else 0.0
    result.update(total_awarded=total, total_max=max_total, percentage=pct, grade=_letter(pct),
                  model=data.get("model", model))
    return result
