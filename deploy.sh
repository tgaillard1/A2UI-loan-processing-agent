#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Disable all interactive prompts in gcloud globally to prevent hangs in automated runners
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# --- Configuration ---
if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <PROJECT_ID> <SERVICE_NAME> [MODEL_NAME] [FIRESTORE_DATABASE_NAME]"
    echo "MODEL_NAME can be 'gemini-3.5-flash' (default), 'gemini-2.5-pro' or 'gemini-2.5-flash'."
    exit 1
fi

PROJECT_ID=$1
SERVICE_NAME=$2
MODEL_NAME=${3:-"gemini-3.5-flash"}
FIRESTORE_DATABASE_NAME=$4

# Validate model name
if [ "$MODEL_NAME" != "gemini-2.5-pro" ] && [ "$MODEL_NAME" != "gemini-2.5-flash" ] && [ "$MODEL_NAME" != "gemini-3.5-flash" ]; then
    echo "Invalid model name. Please use 'gemini-3.5-flash', 'gemini-2.5-pro' or 'gemini-2.5-flash'."
    exit 1
fi

# The region to deploy to
REGION="us-central1"

# The memory to allocate to the service
MEMORY="2Gi"

# ---------------------------------------------------------------------------
# --- Request Variables Interactively ---
# ---------------------------------------------------------------------------

# Request SIEBEL_URL if not set
if [ -z "$SIEBEL_URL" ]; then
    read -p "Enter SIEBEL_URL (e.g., https://fsi-mocks-xxxxx-uc.a.run.app): " SIEBEL_URL
    if [ -z "$SIEBEL_URL" ]; then
        echo "Error: SIEBEL_URL is required."
        exit 1
    fi
fi

# Request FILENET_URL if not set
if [ -z "$FILENET_URL" ]; then
    read -p "Enter FILENET_URL (e.g., https://fsi-mocks-xxxxx-uc.a.run.app): " FILENET_URL
    if [ -z "$FILENET_URL" ]; then
        echo "Error: FILENET_URL is required."
        exit 1
    fi
fi

# Request FIRESTORE_DATABASE_NAME if not set
if [ -z "$FIRESTORE_DATABASE_NAME" ]; then
    read -p "Enter FIRESTORE_DATABASE_NAME [fsi-agent]: " input_firestore
    FIRESTORE_DATABASE_NAME=${input_firestore:-"fsi-agent"}
fi

# Request GCS_BUCKET_NAME if not set
if [ -z "$GCS_BUCKET_NAME" ]; then
    DEFAULT_BUCKET="${PROJECT_ID}-mocks"
    read -p "Enter GCS_BUCKET_NAME [$DEFAULT_BUCKET]: " input_gcs
    GCS_BUCKET_NAME=${input_gcs:-"$DEFAULT_BUCKET"}
fi

echo "---------------------------------------------------------------------------"
echo "[Config] SIEBEL_URL:  $SIEBEL_URL"
echo "[Config] FILENET_URL: $FILENET_URL"
echo "[Config] DB NAME:     $FIRESTORE_DATABASE_NAME"
echo "[Config] GCS BUCKET:  $GCS_BUCKET_NAME"
echo "---------------------------------------------------------------------------"


# ---------------------------------------------------------------------------
# --- Enterprise Infrastructure Setup & IAM Provisioning ---
# ---------------------------------------------------------------------------
echo "[IaC] Resolving target project configuration..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
CANONICAL_SERVICE_URL="https://${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
echo "[IaC] Discovered default compute service account: $SA_EMAIL"
echo "[IaC] Configured Canonical Service URL: $CANONICAL_SERVICE_URL"


# Step 1: Enable required Google Cloud APIs to prevent interactive prompt hangs
echo "[IaC] Enabling required Google Cloud APIs (Storage, Firestore, Cloud Run, Cloud Build, IAM)..."
REQUIRED_APIS=(
  "storage.googleapis.com"
  "firestore.googleapis.com"
  "run.googleapis.com"
  "cloudbuild.googleapis.com"
  "iam.googleapis.com"
  "aiplatform.googleapis.com"
)

for API in "${REQUIRED_APIS[@]}"; do
    echo "[IaC] Ensuring API '$API' is active..."
    gcloud services enable "$API" --project="$PROJECT_ID"
done

echo "[IaC] Pausing for 15 seconds to allow Cloud API activations to fully stabilize..."
sleep 15

# Step 2: Check and create the Firestore database if it does not exist
echo "[IaC] Verifying Firestore database '$FIRESTORE_DATABASE_NAME' status..."
EXISTING_DBS=$(gcloud firestore databases list --project="$PROJECT_ID" --format="value(name)" 2>/dev/null || echo "")

if [[ "$EXISTING_DBS" != *"$FIRESTORE_DATABASE_NAME"* ]]; then
    echo "[IaC] Firestore database '$FIRESTORE_DATABASE_NAME' not found. Creating in native mode..."
    gcloud firestore databases create \
      --database="$FIRESTORE_DATABASE_NAME" \
      --location="$REGION" \
      --type="firestore-native" \
      --project="$PROJECT_ID"
    echo "[IaC] Firestore database created successfully."
else
    echo "[IaC] Firestore database already exists. Skipping database creation."
fi

# Step 3: Enable IAM policy bindings for required roles
echo "[IaC] Configuring IAM roles for service account $SA_EMAIL..."

REQUIRED_ROLES=(
  "roles/datastore.user"          # Access to Firestore read/write
  "roles/storage.objectAdmin"     # Access to GCS buckets
  "roles/run.invoker"             # Access to invoke secured mock endpoints
  "roles/iam.serviceAccountTokenCreator" # For local VM sandbox token generation
  "roles/aiplatform.user"         # Access to invoke Vertex AI Gemini models
)

grant_project_role_with_retry() {
    local PROJECT=$1
    local SA=$2
    local ROLE=$3
    local MAX_RETRIES=5
    local RETRY_DELAY=2
    
    for ((i=1; i<=MAX_RETRIES; i++)); do
        if gcloud projects add-iam-policy-binding "$PROJECT" \
            --member="serviceAccount:$SA" \
            --role="$ROLE" \
            --condition=None \
            --no-user-output-enabled 2>/dev/null; then
            echo "[IaC] Role '$ROLE' granted successfully."
            return 0
        fi
        
        if [ $i -lt $MAX_RETRIES ]; then
            echo "[IaC] IAM conflict or policy check occurred. Retrying role '$ROLE' in $RETRY_DELAY seconds..."
            sleep $RETRY_DELAY
            RETRY_DELAY=$((RETRY_DELAY * 2)) # Exponential backoff
        fi
    done
    
    # Final attempt without silencing output on exhaustion
    echo "[IaC] Retries exhausted. Executing final direct attempt for role '$ROLE'..."
    gcloud projects add-iam-policy-binding "$PROJECT" \
      --member="serviceAccount:$SA" \
      --role="$ROLE" \
      --condition=None \
      --no-user-output-enabled
}

for ROLE in "${REQUIRED_ROLES[@]}"; do
    grant_project_role_with_retry "$PROJECT_ID" "$SA_EMAIL" "$ROLE"
done

echo "[IaC] Pausing for 20 seconds to allow IAM permissions and API activations to fully propagate across GCP services..."
sleep 20

# Step 4: Verify and configure cross-project permissions for secured mock services
configure_cross_project_mock_permissions() {
    local MOCK_URL=$1
    local SA_EMAIL=$2
    
    # Check if the URL matches standard Cloud Run format (hash/number, region, run.app)
    if [[ "$MOCK_URL" =~ https://([a-zA-Z0-9_-]+)-([a-zA-Z0-9]+)\.([a-zA-Z0-9-]+)\.(a\.)?run\.app ]]; then
        local MOCK_SERVICE="${BASH_REMATCH[1]}"
        local MOCK_PROJECT="${BASH_REMATCH[2]}"
        local MOCK_REGION="${BASH_REMATCH[3]}"
        
        # Only attempt cross-project bind if mock is in a completely different project
        if [ "$MOCK_PROJECT" != "$PROJECT_NUMBER" ] && [ "$MOCK_PROJECT" != "$PROJECT_ID" ]; then
            echo "[IaC] Detected cross-project mock endpoint: Service '$MOCK_SERVICE' in Project '$MOCK_PROJECT'"
            echo "[IaC] Attempting to grant run.invoker permission on mock service for service account $SA_EMAIL..."
            if gcloud run services add-iam-policy-binding "$MOCK_SERVICE" \
                --project="$MOCK_PROJECT" \
                --region="$MOCK_REGION" \
                --member="serviceAccount:$SA_EMAIL" \
                --role="roles/run.invoker" \
                --platform="managed" \
                --no-user-output-enabled 2>/dev/null; then
                echo "[IaC] Successfully auto-configured invoker permissions on cross-project mock service '$MOCK_SERVICE'."
            else
                echo ""
                echo "------------------------------------------------------------------------"
                echo "WARNING: CROSS-PROJECT PERMISSION GRANT FAILED!"
                echo "The agent needs permission to call the secured mock endpoint at:"
                echo "  $MOCK_URL"
                echo "Please ensure that the service account:"
                echo "  $SA_EMAIL"
                echo "has the 'roles/run.invoker' role on the Cloud Run service '$MOCK_SERVICE'"
                echo "in project '$MOCK_PROJECT'."
                echo "You (or the owner of project '$MOCK_PROJECT') can grant this using:"
                echo "  gcloud run services add-iam-policy-binding $MOCK_SERVICE \\"
                echo "    --project=$MOCK_PROJECT \\"
                echo "    --region=$MOCK_REGION \\"
                echo "    --member=serviceAccount:$SA_EMAIL \\"
                echo "    --role=roles/run.invoker"
                echo "------------------------------------------------------------------------"
                echo ""
            fi
        fi
    fi
}

echo "[IaC] Checking cross-project mock bindings..."
configure_cross_project_mock_permissions "$SIEBEL_URL" "$SA_EMAIL"
configure_cross_project_mock_permissions "$FILENET_URL" "$SA_EMAIL"

echo "[IaC] Enterprise infrastructure provisioning completed successfully."


# --- Build Staging Preparation ---
STAGING_DIR="build_staging"
echo "Preparing clean minimal build staging directory '$STAGING_DIR'..."

# Create staging directory (remove first if exists)
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# Copy ONLY necessary production files for the agent
cp main.py "$STAGING_DIR/"
cp Procfile "$STAGING_DIR/"
cp requirements.txt "$STAGING_DIR/"
cp .python-version "$STAGING_DIR/"
cp gemini_agent.py "$STAGING_DIR/"
cp prompt_builder_v08.py "$STAGING_DIR/"
cp agent_executor.py "$STAGING_DIR/"
cp database.py "$STAGING_DIR/"
cp filenet.py "$STAGING_DIR/"
cp metadata_extractor.py "$STAGING_DIR/"
cp pdf_converter.py "$STAGING_DIR/"
cp siebel.py "$STAGING_DIR/"
cp validation.py "$STAGING_DIR/"
cp a2a_tools.py "$STAGING_DIR/"
cp templates_a2ui.py "$STAGING_DIR/"

echo "Minimal build staging prepared successfully. Files to be uploaded:"
ls -la "$STAGING_DIR"

# --- Deployment ---
echo "Starting deployment of service '$SERVICE_NAME' to project '$PROJECT_ID' in region '$REGION' with model '$MODEL_NAME'..."

# Deploy to Cloud Run from the STAGING directory with direct canonical AGENT_URL injection!
gcloud run deploy "$SERVICE_NAME" \
  --source "$STAGING_DIR" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory "$MEMORY" \
  --set-env-vars=GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION",GOOGLE_GENAI_USE_VERTEXAI=TRUE,MODEL="$MODEL_NAME",LOCATION="global",SIEBEL_URL="$SIEBEL_URL",FILENET_URL="$FILENET_URL",FIRESTORE_DATABASE_NAME="$FIRESTORE_DATABASE_NAME",GCS_BUCKET_NAME="$GCS_BUCKET_NAME",AGENT_URL="$CANONICAL_SERVICE_URL"

# Clean up staging directory
rm -rf "$STAGING_DIR"

echo "Deployment complete!"
echo "[Deploy] Pausing for 5 seconds to allow Cloud Run endpoint routing to warm up..."
sleep 5

echo ""
echo "------------------------------------------------------------------------"
echo "Fetching Deployed Agent Discovery Card for validation..."
echo "URL: ${CANONICAL_SERVICE_URL}/.well-known/agent-card.json"
echo "------------------------------------------------------------------------"
curl -s -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${CANONICAL_SERVICE_URL}/.well-known/agent-card.json" | jq . || echo "WARNING: Could not fetch agent discovery card. If this is a cold-start domain, DNS might take a moment to propagate."
echo "------------------------------------------------------------------------"
echo ""
