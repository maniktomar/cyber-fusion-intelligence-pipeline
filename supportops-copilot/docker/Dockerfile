FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
# The knowledge base is not optional: without it every triage request fails.
# It was missing from the image until a container smoke test caught it.
COPY data/knowledge_base.json ./data/knowledge_base.json

# Run unprivileged; the token store lives on a mounted volume owned by this user.
RUN useradd --create-home --uid 10001 copilot && \
    mkdir -p /data && chown copilot:copilot /data
USER copilot

# BuildKit flags this as SecretsUsedInArgOrEnv because the name contains
# "TOKEN". It is a false positive: this is a filesystem path, not a credential.
# Kept rather than renamed, because dropping it would send a bare `docker run`
# to a path outside the volume and silently lose the token on restart.
ENV TOKEN_STORE_PATH=/data/tokens.enc

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
