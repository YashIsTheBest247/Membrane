# Membrane proxy — the L1-L4 pipeline, forensics API, SSE feed and Telegram
# webhook route in one container. Scales to zero on Cloud Run.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY membrane ./membrane

# Run unprivileged: the proxy needs no write access to its own image.
RUN useradd --create-home --uid 10001 membrane \
 && chown -R membrane:membrane /app
USER membrane

EXPOSE 8080
ENV PORT=8080

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3   CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','8080') + '/healthz', timeout=3).status == 200 else 1)"

# JSON form so signals reach uvicorn directly; sh -c so $PORT still expands,
# which the platform supplies at run time.
CMD ["sh", "-c", "exec uvicorn membrane.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
