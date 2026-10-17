"""Sarvam Vision (Document Intelligence) OCR for handwritten assignments."""
import io
import os
import time
import zipfile
from pathlib import Path

import requests
from pypdf import PdfReader, PdfWriter

BASE_URL = "https://api.sarvam.ai/doc-ai/v1"
MAX_PAGES = 10  # Sarvam limit per PDF
TERMINAL = {"completed", "partially_completed", "failed", "rejected"}


class SarvamError(RuntimeError):
    pass


def _headers():
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise SarvamError("SARVAM_API_KEY not set")
    return {"api-subscription-key": key}


def _split_pdf(data: bytes) -> list[bytes]:
    reader = PdfReader(io.BytesIO(data))
    n = len(reader.pages)
    if n <= MAX_PAGES:
        return [data]
    chunks = []
    for start in range(0, n, MAX_PAGES):
        w = PdfWriter()
        for i in range(start, min(start + MAX_PAGES, n)):
            w.add_page(reader.pages[i])
        buf = io.BytesIO()
        w.write(buf)
        chunks.append(buf.getvalue())
    return chunks


def _extract_text(blob: bytes) -> str:
    """Download may be a zip of per-page files or a single md file."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = sorted(z.namelist())
            # Page text lives in .md files; .json alongside is job metadata.
            pages = [n for n in names if n.lower().endswith(".md")] or \
                    [n for n in names if n.lower().endswith((".txt", ".html"))]
            return "\n\n".join(z.read(n).decode("utf-8", errors="replace") for n in pages)
    return blob.decode("utf-8", errors="replace")


def _digitise_bytes(data: bytes, filename: str, mime: str, language: str,
                    poll: float = 3.0, timeout: float = 600, log=print) -> str:
    r = requests.post(
        f"{BASE_URL}/job/digitise",
        headers=_headers(),
        files={"file": (filename, data, mime)},
        data={"language": language, "output_format": "md"},
        timeout=120,
    )
    if not r.ok:
        raise SarvamError(f"digitise {r.status_code}: {r.text}")
    job_id = r.json()["job_id"]
    log(f"  sarvam job {job_id} submitted")

    deadline = time.time() + timeout
    while True:
        s = requests.get(f"{BASE_URL}/job/{job_id}/status", headers=_headers(), timeout=60)
        if not s.ok:
            raise SarvamError(f"status {s.status_code}: {s.text}")
        st = s.json()
        status = str(st.get("status", "")).lower()
        if status in TERMINAL:
            break
        if time.time() > deadline:
            raise SarvamError(f"job {job_id} timed out (last status {status})")
        time.sleep(poll)

    if status not in ("completed", "partially_completed"):
        raise SarvamError(f"job {job_id} {status}: {st}")
    if status == "partially_completed":
        log(f"  warning: job {job_id} partially completed")

    d = requests.get(f"{BASE_URL}/job/{job_id}/download-url", headers=_headers(), timeout=60)
    if not d.ok:
        raise SarvamError(f"download-url {d.status_code}: {d.text}")
    info = d.json()
    blob = requests.request(info.get("method", "GET"), info["url"], timeout=120)
    blob.raise_for_status()
    return _extract_text(blob.content)


MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".zip": "application/zip"}


def digitise(data: bytes, filename: str, language: str = "en-IN", log=print) -> str:
    """OCR a PDF/image/zip of handwritten pages → markdown text."""
    ext = Path(filename).suffix.lower()
    mime = MIME.get(ext)
    if not mime:
        raise SarvamError(f"unsupported file type: {ext}")
    if ext != ".pdf":
        return _digitise_bytes(data, filename, mime, language, log=log)
    chunks = _split_pdf(data)
    texts = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            log(f"  chunk {i + 1}/{len(chunks)}")
        texts.append(_digitise_bytes(chunk, f"part{i}_{filename}", mime, language, log=log))
    return "\n\n".join(texts)
