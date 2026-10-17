# Red Pen — handwritten assignment marking

Sarvam Vision reads handwritten scripts → an LLM of your choice (via OpenRouter) marks them against the question paper →
optional Bulbul v4 Flash narrates the feedback.

## Run

```
pip install -r requirements.txt
copy .env.example .env        # set SARVAM_API_KEY and OPENROUTER_API_KEY
python server.py              # http://127.0.0.1:8000
```

`GRADER_DEMO=1 python server.py` serves fake results, for UI work without spending API credits.

Batch from the command line:

```
python cli.py --questions qp.pdf --submissions scans/ [--key answers.txt] [--effort high] [--feedback-language hi-IN]
```

## Teacher review

Each marked script has three views: the marked sheet, the student's writing as Sarvam Vision
read it, and the original scan. **Review marks** lets the teacher change any mark or remark
(the AI's mark stays visible beside it), then save a draft or approve. The CSV lists AI marks
and final marks side by side. Batches and reviews are saved under `data/` and survive restarts.

## Notes

- Question paper: a PDF's text layer is used if present, otherwise it is OCR'd with Sarvam Vision.
- Scripts: PDF, PNG, JPG or ZIP of page images. PDFs over 10 pages are split (Sarvam limit).
- Totals, percentage and letter grade are computed in code, not by the model.
- Narration voices exist for every language except Malayalam and Odia.
- Grades vary between runs (the model is not deterministic). Spot-check before releasing marks.
