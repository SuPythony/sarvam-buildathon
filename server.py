"""Web app: uvicorn server:app --reload  (or: python server.py)"""
import csv
import io
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import grader  # noqa: F401  (loads .env)
from grader.pipeline import grade_submission, question_text
from grader.sarvam_llm import LANGUAGES
from grader.tts import VOICES, narrate

WEB = Path(__file__).parent / "web"
DEMO = os.environ.get("GRADER_DEMO") == "1"  # fake pipeline for UI work without API keys

app = FastAPI(title="Assignment Grader")
pool = ThreadPoolExecutor(max_workers=3)
jobs: dict[str, dict] = {}
lock = threading.Lock()


def _set(job_id, idx=None, **kw):
    with lock:
        target = jobs[job_id] if idx is None else jobs[job_id]["scripts"][idx]
        target.update(kw)


def _demo_result(name):
    time.sleep(1.5)
    return {
        "file": name, "student_name": "Aarav Sharma", "model": "demo",
        "questions": [
            {"number": "1", "question": "State Newton's second law of motion.", "max_marks": 5,
             "awarded": 4, "student_answer": "Force is equal to mass times acceleration, F = ma. "
             "The rate of change of momentum is proportional to force.",
             "feedback": "Correct statement. Mention direction of force and acceleration for full marks."},
            {"number": "2", "question": "A 2 kg body accelerates at 3 m/s². Find the force.",
             "max_marks": 5, "awarded": 5, "student_answer": "F = ma = 2 × 3 = 6 N",
             "feedback": "Perfect. Units are right."},
            {"number": "3", "question": "Explain inertia with one everyday example.", "max_marks": 10,
             "awarded": 6, "student_answer": "Inertia means body want to stay in same state. When bus "
             "stops sudenly we fall forward.",
             "feedback": "Good example. Definition is incomplete: inertia is resistance to change in "
             "state of rest or uniform motion."},
        ],
        "total_awarded": 15, "total_max": 20, "percentage": 75.0, "grade": "B",
        "overall_feedback": "Solid grasp of the laws of motion. Work on writing complete definitions.",
        "narration": "You scored fifteen out of twenty. Your numerical answer was perfect. "
                     "Next time, write the full definition of inertia.",
        "ocr_text": "1. Force is equal to mass times acceleration...\n2. F = ma = 2 x 3 = 6 N\n3. Inertia...",
    }


def _run(job_id, qp_bytes, qp_name, key_text, key_file, files, opts):
    try:
        _set(job_id, status="preparing")
        questions = "" if DEMO else question_text(qp_bytes, qp_name, opts["language"])
        if key_file and not DEMO:
            file_key = question_text(key_file[1], key_file[0], opts["language"])
            key_text = "\n\n".join(t for t in (key_text, file_key) if t)
        _set(job_id, status="running")

        def one(idx, name, data):
            try:
                if DEMO:
                    _set(job_id, idx, stage="reading"); time.sleep(1.2 + idx * .4)
                    _set(job_id, idx, stage="marking")
                    res = _demo_result(name)
                else:
                    res = grade_submission(questions, data, name, key_text, opts["language"],
                                           opts["feedback_language"], opts["reasoning_effort"],
                                           log=lambda m: None,
                                           on_stage=lambda s: _set(job_id, idx, stage=s))
                _set(job_id, idx, stage="done", result=res)
            except Exception as e:
                _set(job_id, idx, stage="error", error=str(e))

        list(pool.map(lambda a: one(*a), [(i, n, d) for i, (n, d) in enumerate(files)]))
        _set(job_id, status="done")
    except Exception as e:
        _set(job_id, status="error", error=str(e))


@app.get("/api/config")
def config():
    return {"languages": LANGUAGES, "voices": VOICES, "demo": DEMO}


@app.post("/api/jobs")
async def create_job(
    question_paper: UploadFile = File(...),
    scripts: list[UploadFile] = File(...),
    answer_key: str = Form(""),
    answer_key_file: UploadFile | None = File(None),
    language: str = Form("en-IN"),
    feedback_language: str = Form("en-IN"),
    reasoning_effort: str = Form("medium"),
):
    if language not in LANGUAGES or feedback_language not in LANGUAGES:
        raise HTTPException(400, "unknown language")
    if reasoning_effort not in ("low", "medium", "high"):
        raise HTTPException(400, "reasoning_effort must be low, medium or high")
    files = [(f.filename, await f.read()) for f in scripts]
    key_file = None
    if answer_key_file and answer_key_file.filename:
        if Path(answer_key_file.filename).suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "answer key file must be PDF, PNG or JPG")
        key_file = (answer_key_file.filename, await answer_key_file.read())
    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"id": job_id, "status": "queued", "created": time.time(),
                    "scripts": [{"file": n, "stage": "queued"} for n, _ in files]}
    opts = dict(language=language, feedback_language=feedback_language,
                reasoning_effort=reasoning_effort)
    threading.Thread(target=_run, daemon=True,
                     args=(job_id, await question_paper.read(), question_paper.filename,
                           answer_key.strip() or None, key_file, files, opts)).start()
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "job not found")
    with lock:
        return jobs[job_id]


@app.get("/api/jobs/{job_id}/csv")
def job_csv(job_id: str):
    job = get_job(job_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "student_name", "total_awarded", "total_max", "percentage", "grade", "error"])
    for s in job["scripts"]:
        r = s.get("result") or {}
        w.writerow([s["file"], r.get("student_name"), r.get("total_awarded"), r.get("total_max"),
                    r.get("percentage"), r.get("grade"), s.get("error")])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=marks-{job_id}.csv"})


class NarrateReq(BaseModel):
    text: str
    language: str
    speaker: str | None = None
    pace: float = 1.0


@app.post("/api/narrate")
def api_narrate(req: NarrateReq):
    try:
        return {"audios": narrate(req.text, req.language, req.speaker, req.pace)}
    except Exception as e:
        raise HTTPException(502, str(e))


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
