#!/bin/bash
# ==============================================================================
# 🔐 AI Compliance Agent Service Account IAM Orchestrator
# Automates the assignment of Invoker, Storage, and Datastore roles in GCP/Argolis.
# ==============================================================================

set -e

failure_handler() {
    echo "=================================================================="
    echo "❌ IAM PERMISSION ASSIGNMENT FAILED!"
    echo "=================================================================="
    echo "If you encountered 403 Forbidden or Permission Denied errors:"
    echo "1. Ensure your active gcloud identity ('$(gcloud config get-value account)')"
    echo "   has the 'Project IAM Admin' (roles/resourcemanager.projectIamAdmin)"
    echo "   or 'Owner' role on project '$PROJECT_ID'."
    echo "2. If in an Argolis environment, verify that the organizational policy"
    echo "   'constraints/iam.allowedPolicyMemberDomains' permits binding this account."
    echo "=================================================================="
    exit 1
}

trap 'failure_handler' ERR

if [ -z "$1" ]; then
    echo "❌ Error: Target Agent Service Account parameter is required."
    echo "Usage: ./assign_agent_permissions.sh <service_account_email> [project_id] [gcs_bucket_name] [service_name]"
    echo "Example: ./assign_agent_permissions.sh 1060655894179-compute@developer.gserviceaccount.com testenvironment-497615 test-bucket-testenvironment-497615 v05272026-fsi-mocks"
    exit 1
fi

AGENT_SA="$1"
PROJECT_ID="${2:-$(gcloud config get-value project)}"
BUCKET_NAME="${3:-test-bucket-${PROJECT_ID}}"
SERVICE_NAME="${4:-v05272026-fsi-mocks}"
REGION="${5:-us-central1}"

# Smart Principal Member Type Prefix Detection (serviceAccount: vs user:)
CLEAN_SA="${AGENT_SA#serviceAccount:}"
CLEAN_SA="${CLEAN_SA#user:}"

if [[ "$CLEAN_SA" == *".gserviceaccount.com" ]]; then
    MEMBER_ID="serviceAccount:$CLEAN_SA"
else
    MEMBER_ID="user:$CLEAN_SA"
fi

# 1. Assign Cloud Run Invoker Role
echo "🔒 Assigning Cloud Run Invoker permissions for $SERVICE_NAME to $MEMBER_ID..."
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --member="$MEMBER_ID" \
  --role="roles/run.invoker" \
  --region="$REGION" \
  --project="$PROJECT_ID" --quiet

# 2. Assign GCS Storage Object Viewer Role
echo "🔒 Assigning Cloud Storage Object Viewer permissions for gs://$BUCKET_NAME..."
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="$MEMBER_ID" \
  --role="roles/storage.objectViewer" \
  --project="$PROJECT_ID" --quiet

# 3. Assign Cloud Datastore User Role (For Firestore transaction persistence)
echo "🔒 Assigning Cloud Datastore User permissions on project $PROJECT_ID..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="$MEMBER_ID" \
  --role="roles/datastore.user" \
  --quiet

echo "=================================================================="
echo "🎉 AI Agent IAM Permissions assigned successfully!"
echo "=================================================================="
