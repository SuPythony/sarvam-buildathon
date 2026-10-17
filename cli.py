"""Batch-grade handwritten assignments.

Usage:
  python cli.py --questions qp.pdf --submissions scans/ [--model openai/gpt-6.1-sol] [--key key.txt] [--effort high]
"""
import argparse
import csv
import json
from pathlib import Path

from grader.pipeline import grade_submission, question_text
from grader.sarvam_ocr import MIME


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--questions", required=True, help="question paper PDF (with marks)")
    p.add_argument("--submissions", required=True, help="file or folder of student scans")
    p.add_argument("--model", help="OpenRouter model id (default: OPENROUTER_MODEL)")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--feedback-language", default="en-IN")
    p.add_argument("--key", help="optional answer key / rubric (txt or pdf)")
    p.add_argument("--language", default="en-IN")
    p.add_argument("--out", default="results")
    a = p.parse_args()

    qp = Path(a.questions)
    questions = question_text(qp.read_bytes(), qp.name, a.language)
    answer_key = None
    if a.key:
        k = Path(a.key)
        answer_key = question_text(k.read_bytes(), k.name, a.language) if k.suffix.lower() != ".txt" \
            else k.read_text(encoding="utf-8")

    src = Path(a.submissions)
    files = [src] if src.is_file() else sorted(f for f in src.iterdir() if f.suffix.lower() in MIME)
    out = Path(a.out)
    out.mkdir(exist_ok=True)

    rows = []
    for f in files:
        try:
            r = grade_submission(questions, f.read_bytes(), f.name, answer_key, a.language,
                                 a.feedback_language, a.effort, a.model)
        except Exception as e:  # keep batch going
            print(f"[{f.name}] FAILED: {e}")
            rows.append({"file": f.name, "error": str(e)})
            continue
        (out / f"{f.stem}.json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[{f.name}] {r['total_awarded']}/{r['total_max']} ({r['percentage']}%) grade {r['grade']}")
        rows.append({k: r.get(k) for k in ("file", "student_name", "total_awarded", "total_max",
                                           "percentage", "grade", "model")})

    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["file", "student_name", "total_awarded", "total_max",
                                           "percentage", "grade", "model", "error"])
        w.writeheader()
        w.writerows(rows)
    print(f"done -> {out}/summary.csv")


if __name__ == "__main__":
    main()
