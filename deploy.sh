#!/usr/bin/env bash
# StoreMind — Phase 1 GCP setup + Cloud Run deploy.
#
# Run this from a machine with `gcloud` installed and network access
# (not the sandbox this was generated in). Edit the variables below first.
#
# Usage: ./deploy.sh

set -euo pipefail

PROJECT_ID="your-gcp-project-id"       # <-- EDIT
REGION="us-central1"                   # <-- EDIT if needed
SERVICE_NAME="storemind-ingest"
FIRESTORE_LOCATION="us-central"        # <-- must match REGION's multi-region

echo "== 1. Set active project =="
gcloud config set project "${PROJECT_ID}"

echo "== 2. Enable required APIs =="
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

echo "== 3. Create Firestore database (native mode) — skip if it already exists =="
gcloud firestore databases create --location="${FIRESTORE_LOCATION}" || \
  echo "Firestore database may already exist — continuing."

echo "== 4. Build and deploy to Cloud Run (source-based build, no manual Docker push needed) =="
cd app
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global" \
  --memory 512Mi \
  --timeout 60

echo "== Done =="
echo "Grab the service URL from the output above, then test with:"
echo "  curl \$(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)')/health"

# Notes:
# - The deploying account / Cloud Run's default service account needs:
#     roles/datastore.user        (Firestore read/write)
#     roles/aiplatform.user       (Gemini calls via Vertex AI)
#   If you hit permission errors, run:
#     gcloud projects add-iam-policy-binding ${PROJECT_ID} \
#       --member="serviceAccount:$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
#       --role="roles/datastore.user"
#   and the same with roles/aiplatform.user.
# - Gemini model access in Vertex AI is enabled by default for most projects,
#   but confirm at: https://console.cloud.google.com/vertex-ai/model-garden
