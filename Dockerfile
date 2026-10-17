FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADER_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY grader ./grader
COPY web ./web
COPY server.py cli.py ./

# One worker only: batch progress lives in this process's memory.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
