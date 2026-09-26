FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VERA_REQUIRE_AUTH=true \
    PORT=8080

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 vera

COPY app/ ./app/
COPY bot.py ./
USER vera
EXPOSE 8080

# One worker is essential: the challenge's context and conversations are in memory.
CMD ["sh", "-c", "exec python -m uvicorn bot:app --host 0.0.0.0 --port \"${PORT:-8080}\" --workers 1"]
