#!/bin/bash
# ==============================================================================
# 🚀 E2E Staging & Mock Service Deployment Orchestrator
# Standardizes GCS buckets and regional serverless endpoints inside GCP/Argolis.
# ==============================================================================

set -e

failure_handler() {
    echo "=================================================================="
    echo "❌ DEPLOYMENT FAILED!"
    echo "=================================================================="
    echo "If you encountered FAILED_PRECONDITION or 403 Forbidden errors:"
    echo "1. 🔑 Set IAM Permissions manually on your bucket by running:"
    echo "   gcloud storage buckets add-iam-policy-binding gs://$BUCKET_NAME --member=serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com --role=roles/storage.admin"
    echo "2. 👑 Alternatively, ensure your active gcloud identity has the"
    echo "   'Project IAM Admin' (roles/resourcemanager.projectIamAdmin) role so this script can apply it for you automatically."
    echo "=================================================================="
    exit 1
}

trap 'failure_handler' ERR

if [ -z "$1" ]; then
    echo "❌ Error: GCS Bucket name parameter is required."
    echo "Usage: ./deploy.sh <gcs_bucket_name> [gcp_project_id] [region] [prefix]"
    exit 1
fi

BUCKET_NAME="$1"
PROJECT_ID="${2:-$(gcloud config get-value project)}"
REGION="${3:-us-central1}"
PREFIX="${4:-}"

if [ -n "$PREFIX" ]; then
    CLEAN_PREFIX="${PREFIX%-fsi-mocks}"
    CLEAN_PREFIX="${CLEAN_PREFIX%-mocks}"
    if [[ "$CLEAN_PREFIX" =~ ^[0-9] ]]; then
        CLEAN_PREFIX="v${CLEAN_PREFIX}"
    fi
    MOCKS_SERVICE_NAME="${CLEAN_PREFIX}-fsi-mocks"
else
    MOCKS_SERVICE_NAME="fsi-mocks"
fi

echo "=================================================================="
echo "🚀 Target GCP Project: $PROJECT_ID"
echo "📂 Target GCS Bucket:  gs://$BUCKET_NAME"
echo "📡 Deployment Region:  $REGION"
echo "=================================================================="

# 1. Verify or Create GCS Bucket
if gcloud storage buckets describe "gs://$BUCKET_NAME" --project="$PROJECT_ID" &>/dev/null; then
    echo "✅ Bucket gs://$BUCKET_NAME already exists."
else
    echo "✨ Bucket gs://$BUCKET_NAME not found. Creating a new one..."
    gcloud storage buckets create "gs://$BUCKET_NAME" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --quiet
fi

# 2. Grant Cloud Build and Compute Engine Service Accounts read access to the staging bucket
echo "🔒 Securing Cloud Build IAM access to staging bucket..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.admin" \
  --project="$PROJECT_ID" --quiet

gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/storage.admin" \
  --project="$PROJECT_ID" --quiet

gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-cloudbuild.iam.gserviceaccount.com" \
  --role="roles/storage.admin" \
  --project="$PROJECT_ID" --quiet

# 3. Grant Artifact Registry Admin & Cloud Logging permissions to the Cloud Build Account
echo "🔒 Securing Artifact Registry & Cloud Logging IAM access..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/artifactregistry.admin" --quiet || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/logging.logWriter" --quiet || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/artifactregistry.admin" --quiet || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/logging.logWriter" --quiet || true

# 3. Sync pre-compiled corpus to GCS (Upload only, no deleting to protect other folders)
echo "☁️ Uploading pre-compiled scanned PDF corpus to GCS..."
gcloud storage cp -r document_corpus/* "gs://$BUCKET_NAME/document_corpus/"

# 4. Packaging Mock Service SOR container assets
echo "📦 Packaging mock service container..."
TAR_PATH="gs-source.tar.gz"

# Copy the ledger.csv to mock_service folder to ensure container has the latest database
cp ledger.csv mock_service/ledger.csv

tar -czf "$TAR_PATH" -C mock_service Dockerfile requirements.txt ledger.csv main.py

# 5. Verify or Create Google Artifact Registry Repository E2E
REPO_NAME="fsi-mocks-repo"
echo "🛠️ Verifying Google Artifact Registry for $REPO_NAME..."
gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID" --quiet || true

# 6. Submit container compilation to Google Cloud Build (Migrated to Artifact Registry)
echo "🛠️ Submitting mock service container to Google Cloud Build..."
IMAGE_TAG="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$MOCKS_SERVICE_NAME:latest"
gcloud builds submit "$TAR_PATH" \
  --tag="$IMAGE_TAG" \
  --project="$PROJECT_ID" \
  --gcs-source-staging-dir="gs://$BUCKET_NAME/source" \
  --gcs-log-dir="gs://$BUCKET_NAME/cloudbuild-logs"

# 7. Deploy Mock Service to Cloud Run passing BUCKET_NAME dynamically
echo "📡 Deploying Mock Service stubs to Cloud Run..."
gcloud run deploy "$MOCKS_SERVICE_NAME" \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --set-env-vars=GCS_BUCKET_NAME="$BUCKET_NAME" \
  --allow-unauthenticated \
  --quiet

# Retrieve Cloud Run URL
MOCKS_URL=$(gcloud run services describe "$MOCKS_SERVICE_NAME" --platform=managed --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)")

echo "=================================================================="
echo "🎉 E2E Staging Rollout completed successfully!"
echo "📂 GCS Corpus Path: gs://$BUCKET_NAME/document_corpus/"
echo "📡 Mock Service URL: $MOCKS_URL"
echo "=================================================================="

# Clean up local tarball
rm -f "$TAR_PATH"
