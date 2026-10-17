"""Web app: python server.py  →  http://127.0.0.1:8000"""
import csv
import io
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hmac

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import grader  # noqa: F401  (loads .env)
from grader.pipeline import grade_submission, question_text
from grader.llm_grader import DEFAULT_MODEL, LANGUAGES, SUGGESTED, _letter, available_models
from grader.sarvam_ocr import MIME
from grader.tts import VOICES, narrate

WEB = Path(__file__).parent / "web"
DATA = Path(os.environ.get("GRADER_DATA", Path(__file__).parent / "data")) / "jobs"
DEMO = os.environ.get("GRADER_DEMO") == "1"  # fake pipeline for UI work without API keys

app = FastAPI(title="Red Pen")
pool = ThreadPoolExecutor(max_workers=3)

# ---------- optional shared-password login (set APP_PASSWORD when deployed) ----------
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION = hmac.new(APP_PASSWORD.encode(), b"red-pen-session", "sha256").hexdigest() if APP_PASSWORD else ""
PUBLIC = ("/login", "/api/login", "/healthz", "/static/styles.css")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not APP_PASSWORD or path in PUBLIC or \
            hmac.compare_digest(request.cookies.get("redpen_session", ""), SESSION):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page():
    return FileResponse(WEB / "login.html")


@app.post("/api/login")
def login(request: Request, password: str = Form("")):
    if not APP_PASSWORD or not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        time.sleep(1)  # slow down guessing
        return RedirectResponse("/login?wrong=1", status_code=303)
    res = RedirectResponse("/", status_code=303)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    # Over HTTPS allow cross-site use: Hugging Face shows the app inside an iframe.
    res.set_cookie("redpen_session", SESSION, max_age=30 * 86400, httponly=True,
                   samesite="none" if https else "lax", secure=https, partitioned=https)
    return res


@app.get("/logout")
def logout():
    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie("redpen_session")
    res.delete_cookie("redpen_session", secure=True, samesite="none", partitioned=True)
    return res


@app.get("/healthz")
def healthz():
    return {"ok": True}
jobs: dict[str, dict] = {}
lock = threading.Lock()


# ---------- persistence: one folder per batch, job.json + uploaded files ----------

def _save(job_id):
    """Caller must hold `lock`."""
    d = DATA / job_id
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(jobs[job_id], ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "job.json")


def _load_all():
    for f in DATA.glob("*/job.json"):
        job = json.loads(f.read_text(encoding="utf-8"))
        # A batch interrupted by a restart can't resume its threads.
        if job["status"] not in ("done", "error"):
            job["status"] = "error"
            job["error"] = "Server restarted while marking. Start this batch again."
        jobs[job["id"]] = job


DATA.mkdir(parents=True, exist_ok=True)
_load_all()


def _set(job_id, idx=None, **kw):
    with lock:
        target = jobs[job_id] if idx is None else jobs[job_id]["scripts"][idx]
        target.update(kw)
        _save(job_id)


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
        "ocr_text": "Name: Aarav Sharma\n\n1. Force is equal to mass times acceleration, F = ma. The rate "
                    "of change of momentum is proportional to force.\n\n2. F = ma = 2 × 3 = 6 N\n\n"
                    "3. Inertia means body want to stay in same state. When bus stops sudenly we fall forward.",
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
                                           opts["model"],
                                           log=lambda m: None,
                                           on_stage=lambda s: _set(job_id, idx, stage=s))
                _set(job_id, idx, stage="done", result=res)
            except Exception as e:
                _set(job_id, idx, stage="error", error=str(e))

        list(pool.map(lambda a: one(*a), [(i, n, d) for i, (n, d) in enumerate(files)]))
        _set(job_id, status="done")
    except Exception as e:
        _set(job_id, status="error", error=str(e))


def _job(job_id):
    if job_id not in jobs:
        raise HTTPException(404, "batch not found")
    return jobs[job_id]


def _final(script):
    """Marks after teacher review, falling back to the AI's."""
    r = script.get("result")
    if not r:
        return None
    rv = script.get("review") or {}
    marks = [q.get("awarded") for q in rv.get("questions", [])]
    total = sum(m if m is not None else q["awarded"]
                for q, m in zip(r["questions"], marks + [None] * len(r["questions"])))
    pct = round(100 * total / r["total_max"], 1) if r["total_max"] else 0.0
    return {"total": total, "max": r["total_max"], "percentage": pct, "grade": _letter(pct)}


# ---------- API ----------

@app.get("/api/config")
def config():
    return {"languages": LANGUAGES, "voices": VOICES, "demo": DEMO, "auth": bool(APP_PASSWORD),
            "default_model": DEFAULT_MODEL, "suggested_models": SUGGESTED}


@app.get("/api/models")
def models():
    return available_models()


@app.get("/api/jobs")
def list_jobs():
    with lock:
        out = [{"id": j["id"], "name": j.get("name"), "created": j["created"], "status": j["status"],
                "scripts": len(j["scripts"]),
                "approved": sum(1 for s in j["scripts"] if (s.get("review") or {}).get("approved"))}
               for j in jobs.values()]
    return sorted(out, key=lambda j: -j["created"])[:20]


@app.post("/api/jobs")
async def create_job(
    question_paper: UploadFile = File(...),
    scripts: list[UploadFile] = File(...),
    answer_key: str = Form(""),
    answer_key_file: UploadFile | None = File(None),
    language: str = Form("en-IN"),
    feedback_language: str = Form("en-IN"),
    reasoning_effort: str = Form("medium"),
    model: str = Form(""),
):
    if language not in LANGUAGES or feedback_language not in LANGUAGES:
        raise HTTPException(400, "unknown language")
    if reasoning_effort not in ("low", "medium", "high"):
        raise HTTPException(400, "reasoning_effort must be low, medium or high")
    model = model.strip() or DEFAULT_MODEL
    # Fail before OCR spends credits.
    if not DEMO:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise HTTPException(400, "OPENROUTER_API_KEY is missing. Add it to .env and restart the server.")
        if model not in available_models():
            raise HTTPException(400, f"OpenRouter has no model called '{model}'. Pick one from the list.")
    files = [(Path(f.filename).name, await f.read()) for f in scripts]
    key_file = None
    if answer_key_file and answer_key_file.filename:
        if Path(answer_key_file.filename).suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg"):
            raise HTTPException(400, "answer key file must be PDF, PNG or JPG")
        key_file = (answer_key_file.filename, await answer_key_file.read())

    job_id = uuid.uuid4().hex[:10]
    up = DATA / job_id / "files"
    up.mkdir(parents=True, exist_ok=True)
    for i, (name, data) in enumerate(files):
        (up / f"{i}{Path(name).suffix.lower()}").write_bytes(data)

    with lock:
        jobs[job_id] = {"id": job_id, "name": Path(question_paper.filename).stem, "status": "queued",
                        "created": time.time(), "feedback_language": feedback_language, "model": model,
                        "scripts": [{"file": n, "stage": "queued"} for n, _ in files]}
        _save(job_id)
    opts = dict(language=language, feedback_language=feedback_language,
                reasoning_effort=reasoning_effort, model=model)
    threading.Thread(target=_run, daemon=True,
                     args=(job_id, await question_paper.read(), question_paper.filename,
                           answer_key.strip() or None, key_file, files, opts)).start()
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with lock:
        return _job(job_id)


@app.get("/api/jobs/{job_id}/scripts/{idx}/file")
def script_file(job_id: str, idx: int):
    job = _job(job_id)
    if not 0 <= idx < len(job["scripts"]):
        raise HTTPException(404, "script not found")
    ext = Path(job["scripts"][idx]["file"]).suffix.lower()
    path = DATA / job_id / "files" / f"{idx}{ext}"
    if not path.exists():
        raise HTTPException(404, "original file not kept for this batch")
    return FileResponse(path, media_type=MIME.get(ext, "application/octet-stream"),
                        headers={"Content-Disposition": "inline"})


class QuestionReview(BaseModel):
    awarded: float | None = Field(None, ge=0)
    feedback: str | None = None


class Review(BaseModel):
    questions: list[QuestionReview] = []
    overall_feedback: str | None = None
    approved: bool = False


@app.put("/api/jobs/{job_id}/scripts/{idx}/review")
def save_review(job_id: str, idx: int, review: Review):
    with lock:
        job = _job(job_id)
        if not 0 <= idx < len(job["scripts"]) or not job["scripts"][idx].get("result"):
            raise HTTPException(404, "no marked script at that position")
        qs = job["scripts"][idx]["result"]["questions"]
        if len(review.questions) > len(qs):
            raise HTTPException(400, "more questions than the script has")
        for q, rq in zip(qs, review.questions):
            if rq.awarded is not None and rq.awarded > q["max_marks"]:
                raise HTTPException(400, f"Q{q.get('number')}: marks exceed {q['max_marks']:g}")
        job["scripts"][idx]["review"] = {**review.model_dump(), "updated": time.time()}
        _save(job_id)
        return job["scripts"][idx]


@app.get("/api/jobs/{job_id}/csv")
def job_csv(job_id: str):
    job = get_job(job_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "student_name", "ai_marks", "final_marks", "max_marks", "percentage",
                "grade", "status", "error"])
    for s in job["scripts"]:
        r, f = s.get("result") or {}, _final(s) or {}
        status = "approved" if (s.get("review") or {}).get("approved") else \
                 "ai_marked" if r else s.get("stage")
        w.writerow([s["file"], r.get("student_name"), r.get("total_awarded"), f.get("total"),
                    f.get("max"), f.get("percentage"), f.get("grade"), status, s.get("error")])
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
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
