# Red Pen — handwritten assignment marking

Red Pen marks handwritten assignments the way a teacher would, then hands the marks back to the
teacher to check. Upload a question paper and a stack of scanned answer scripts; Red Pen reads
the handwriting, marks every answer against the question paper (and an answer key, if you have
one), writes feedback for the student, and shows the result as a checked answer sheet in red pen.
The teacher reviews, corrects and approves the marks before anything is released.

Live demo: https://red-pen.onrender.com

Built for the Sarvam AI buildathon, with a focus on Indian classrooms: handwriting can be read in
English and 10 Indian languages, feedback can be written in any of them, and narration reads it
aloud.

## How it works

```
question paper ─┐
answer key ─────┤  (PDF text layer, or Sarvam Vision OCR for scans)
                ▼
student scans ──► Sarvam Vision ──► OCR text ──► LLM via OpenRouter ──► marks + feedback (JSON)
                  (handwriting)                  (marks each answer)            │
                                                                                ▼
                                       teacher review ◄── marked sheet in the web app
                                                │
                                                ▼
                                     approved marks ──► CSV export
                                                └────► Bulbul v4 Flash narration (optional)
```

1. **Reading.** Each script (PDF, photo or ZIP of page photos) is sent to Sarvam Vision's
   Document Intelligence API, which turns the handwriting into text. PDFs longer than Sarvam's
   10-page limit are split and read in parts. Question papers and answer keys with a text layer
   are read directly; scanned ones go through Sarvam Vision too.
2. **Marking.** The question paper, the optional answer key and the student's answers go to an
   LLM through OpenRouter, so any model can be used (default: DeepSeek V4.1 Flash). The model
   returns marks, the student's answer for each question, per-question feedback, an overall
   remark and a short spoken summary, all in the chosen feedback language. It follows the max
   marks printed on the question paper and the rules in the answer key.
3. **Checking the arithmetic.** Marks are capped at each question's maximum, and the total,
   percentage and letter grade are computed in code, not trusted to the model.
4. **Teacher review.** The teacher sees each script as a marked answer sheet, changes any mark or
   remark, and approves it. The AI's original mark stays visible next to every change.
5. **Narration (optional).** Bulbul v4 Flash reads the feedback aloud in the student's language,
   using narration and edtech voices.

## Features

**Marking**
- Question paper as PDF or photo, with marks per question.
- Answer key typed in, attached as a PDF or photo, or both.
- Batches of scripts, marked three at a time, with live progress per student.
- Handwriting in English, Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi,
  Tamil or Telugu; feedback in any of the same.
- Marking depth (Quick / Standard / Thorough) sets how much the model reasons.
- Any OpenRouter model, picked from the live catalogue in the app.

**The marked sheet**
- Each script appears as a ruled answer sheet: the student's answers in blue handwriting, marks
  in red pen in the margin, ticks, feedback under each answer, and a circled total with the grade.
- **Student's writing** tab: the full transcript Sarvam Vision produced, in handwriting on ruled
  paper, to check what was read.
- **Original scan** tab: the uploaded image or PDF, to compare against the transcript.
- Class summary: scripts marked, scripts checked, class average, lowest and highest score.

**Teacher review**
- **Review marks** makes every mark and remark editable; the total and grade update as you type.
- Marks above a question's maximum are blocked.
- Save a draft or approve. Approved scripts get a "Checked" stamp, and the next unchecked script
  opens automatically.
- CSV export lists AI marks and final marks side by side, with each script's status.
- Batches and reviews are saved to disk and can be reopened from **Earlier batches** or the URL.

**Narration**
- Optional "Read feedback aloud" switch with a choice of Bulbul v4 Flash voices.
- Listen to a spoken summary of the whole script or to the feedback on a single answer.
- Uses the teacher's edited remarks once they've been changed.

**Access**
- Optional staff password: set `APP_PASSWORD` and every page and API call requires sign-in.

## Tech stack

| Part | Choice |
|---|---|
| Handwriting OCR | Sarvam Vision (Document Intelligence API) |
| Marking | Any LLM on OpenRouter, default `deepseek/deepseek-v4.1-flash` |
| Narration | Sarvam Bulbul v4 Flash text-to-speech |
| Backend | Python, FastAPI, background threads, JSON files on disk |
| Frontend | Plain HTML, CSS and JavaScript, no build step |
| Packaging | Docker; `render.yaml` Blueprint for Render |

## Run locally

```
pip install -r requirements.txt
copy .env.example .env        # fill in SARVAM_API_KEY and OPENROUTER_API_KEY
python server.py              # http://127.0.0.1:8000
```

`GRADER_DEMO=1 python server.py` serves fake results, for working on the UI without spending API
credits.

### Command line

Marks a folder of scripts without the web app and writes one JSON per student plus
`summary.csv`:

```
python cli.py --questions qp.pdf --submissions scans/ [--key answers.pdf] [--model qwen/qwen3.8-flash] [--effort high] [--feedback-language hi-IN]
```

### Configuration

| Variable | Required | Purpose |
|---|---|---|
| `SARVAM_API_KEY` | Yes | Sarvam Vision OCR and Bulbul narration |
| `OPENROUTER_API_KEY` | Yes | Marking |
| `OPENROUTER_MODEL` | No | Default marking model (`deepseek/deepseek-v4.1-flash`) |
| `APP_PASSWORD` | No | Staff sign-in; leave unset for open access |
| `GRADER_DATA` | No | Where batches are saved (default `./data`) |
| `GRADER_DEMO` | No | `1` serves fake results |

## Project structure

```
server.py            FastAPI app: batches, review, CSV, narration, sign-in
cli.py               Batch marking from the command line
grader/
  sarvam_ocr.py      Sarvam Vision jobs: submit, poll, download, split long PDFs
  llm_grader.py      OpenRouter marking prompt, JSON parsing, totals and grades
  tts.py             Bulbul v4 Flash narration and voice list
  pipeline.py        Question paper / answer key reading, OCR then marking
web/
  index.html         The app
  app.js             Upload, progress, marked sheet, review, narration
  styles.css         Answer-sheet look, light and dark themes
  login.html         Staff sign-in page
Dockerfile           Container image (one worker: batch progress lives in memory)
render.yaml          Render Blueprint
```

## Limitations

- Marks vary between runs because LLM output isn't deterministic. Treat AI marks as a first draft
  and review before releasing them.
- OCR quality depends on the scan. Check the **Student's writing** tab when a mark looks wrong.
- Bulbul v4 Flash has no Malayalam or Odia voices, so narration is off for those languages.
- A batch interrupted by a server restart has to be started again; finished batches are kept.
- On hosts without a persistent disk (such as Render's free plan) saved batches are lost on
  restart, so download the CSV after reviewing.
