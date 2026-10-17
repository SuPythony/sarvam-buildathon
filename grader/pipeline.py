"""End-to-end: question paper + student scans → graded results."""
import io
from pathlib import Path

from pypdf import PdfReader

from .sarvam_llm import grade
from .sarvam_ocr import digitise


def question_text(data: bytes, filename: str, language: str = "en-IN", log=print) -> str:
    """Text of a question paper or answer key: PDF text layer, else Sarvam OCR."""
    if filename.lower().endswith(".pdf"):
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)
        if len(text.strip()) > 50:
            return text
        log(f"{filename} has no text layer -> Sarvam OCR")
    return digitise(data, filename, language, log=log)


def grade_submission(questions: str, data: bytes, filename: str, answer_key: str | None = None,
                     language: str = "en-IN", feedback_language: str = "en-IN",
                     reasoning_effort: str = "medium", log=print, on_stage=None) -> dict:
    on_stage = on_stage or (lambda s: None)
    on_stage("reading")
    log(f"[{filename}] OCR via Sarvam Vision")
    answers = digitise(data, filename, language, log=log)
    on_stage("marking")
    log(f"[{filename}] grading via Sarvam-105B")
    result = grade(questions, answers, answer_key, feedback_language, reasoning_effort)
    result["file"] = Path(filename).name
    result["ocr_text"] = answers
    return result
