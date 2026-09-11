FROM python:3.12-slim

WORKDIR /app

# Runtime + data-science pack deps (numpy/pandas; the sandbox runner imports them).
COPY requirements.txt requirements-packs.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-packs.txt

COPY app ./app

# Compute-agnostic defaults: SQLite (zero-config durable) + the self-hosted
# OpenAI-compatible provider. Point OPENAI_BASE_URL at any local/institutional
# model endpoint — no lock-in, no external API required.
ENV PROVIDER=openai_compatible \
    TUTOR_PACK=datascience \
    STORE_BACKEND=sql \
    DATABASE_URL=sqlite:///./qimvp.db \
    OPENAI_BASE_URL=http://host.docker.internal:11434/v1 \
    OPENAI_API_KEY=ollama \
    MODEL_FAST=llama3.2 \
    MODEL_STRONG=llama3.2

EXPOSE 8000

# Run the preflight doctor (informational), then serve. For a hard pre-serve
# check use `python -m app.preflight` (exits non-zero on any failure).
CMD ["sh", "-c", "python -m app.preflight --skip-provider || true; uvicorn app.main:app --host 0.0.0.0 --port 8000"]
