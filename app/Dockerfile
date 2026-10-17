FROM python:3.12-slim

WORKDIR /app

# requirements-gcp.txt is included here on purpose — this container is
# what actually runs on Cloud Run, so it needs google-cloud-firestore and
# google-genai regardless of which requirements files were used for local
# testing. Missing this caused every /ingest call to fail with a bare
# 500 (ModuleNotFoundError on the vertexai/firestore backends).
COPY requirements.txt requirements-gcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-gcp.txt

COPY . .

# Cloud Run injects PORT; default to 8080 for local runs.
ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
