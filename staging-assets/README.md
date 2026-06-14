# Cymbal Bank Equipment Finance: Scanned PDF Staging & Mock Service Toolkit

This toolkit provides a self-contained, pre-compiled staging environment to evaluate and test the **Cymbal Bank Equipment Finance Originations compliance agent**.

It contains a database of **300 high-fidelity mock packages** (200 original + 100 new messy/discrepant items) with pre-compiled, pixel-flattened, organically degraded scanned PDF documents, alongside a dynamic serverless mock service serving stubs for both FileNet and Siebel CRM.

---

## 📂 Directory Layout & Architecture

```text
fsi_staging_kit/
├── README.md               # This developer guide and operational runbook
├── deploy.sh               # E2E staging and mock service deployment orchestrator script
├── ledger.csv              # Master ground-truth pre-approval database (300 records)
├── document_corpus/        # 300 folders (PKG-10001 to PKG-10300) containing pre-compiled scanned PDFs:
│   ├── PKG-XXXXX/
│   │   ├── invoice.pdf     # Digitized commercial invoice (grayscaled, skewed, and blurred)
│   │   ├── credit_app.pdf  # IRS Commercial Credit Application
│   │   └── w9.pdf          # Handwritten/signed Form W-9
└── mock_service/           # Serverless Cloud Run container source assets:
    ├── Dockerfile          # Container specification
    ├── requirements.txt    # Python dependencies (fastapi, uvicorn, pymupdf)
    ├── main.py             # FastAPI mock server (serves stubs and renders PDFs on-the-fly to PNG)
    └── ledger.csv          # Local copy of the database embedded in the container
```

### 🔄 The Two `ledger.csv` Copies Explained:
1.  **Root `ledger.csv` (Master Database)**: This is your main database to view, inspect, or edit pre-approval profiles locally.
2.  **`mock_service/ledger.csv` (Container Copy)**: The mock server runs inside a Docker container. Docker cannot copy files from outside its build context (no `../ledger.csv` inside the Dockerfile). 
3.  **Sync Automagic**: You **only** need to modify the root `ledger.csv` master. The orchestrator script **`deploy.sh`** automatically synchronizes the two copies (`cp ledger.csv mock_service/ledger.csv`) right before submitting the container build to Google Cloud!

---

## 🚀 E2E Deployment Runbook (Standard or Argolis GCP Projects)

The deployment script (`deploy.sh`) compiles all container assets and deploys GCS files and Cloud Run stubs dynamically. 

### Step 1: Unzip the Toolkit
```bash
unzip fsi_staging_kit.zip
cd fsi_staging_kit
```

### Step 2: Execute Deployment
Run `deploy.sh` passing your target **GCS Bucket Name** as the first parameter. If the bucket does not exist in your project, the script will automatically create it:
```bash
bash deploy.sh <my-staging-gcs-bucket-name>
```
*   *Optional Overrides (Project, Region, Prefix)*: You can explicitly override the GCP Project ID, deployment region, and add a custom prefix for resource isolation (highly useful for avoiding naming conflicts in shared team projects or Argolis subdomains):
    ```bash
    bash deploy.sh <my-staging-gcs-bucket-name> [gcp-project-id] [gcp-region] [prefix]
    ```

### 🛡️ Script Steps Executed:
1.  **Bucket Guard**: Describes the bucket in GCS; if missing, creates a new one in the deployment region.
2.  **GCS Sync**: Uploads the pre-compiled `document_corpus/` to GCS (`gs://<bucket>/document_corpus/`).
3.  **Container Build**: Compiles the FastAPI stubs and builds the container on **Google Cloud Build** (prefixed if parameter is passed).
4.  **Cloud Run Deploy**: Deploys the **`fsi-mocks`** container (prefixed as **`<prefix>-fsi-mocks`** if parameter is passed) on Cloud Run, setting the `GCS_BUCKET_NAME` environment variable dynamically.
5.  **Prerequisites Catch**: If any step fails, the script catches the failure and prints a clear prerequisites troubleshooting dashboard.

---

## 📋 GCP Project Prerequisites

The toolkit deploys on **any standard GCP project** (not limited to Argolis) where you have active billing.

### 1. Required APIs Enabled
Verify that the following APIs are enabled in your target GCP project:
*   **Cloud Storage API** (`storage.googleapis.com`)
*   **Cloud Build API** (`cloudbuild.googleapis.com`)
*   **Cloud Run Admin API** (`run.googleapis.com`)
*   **Container/Artifact Registry**

### 2. Required IAM Roles
Your active `gcloud` authenticated identity must have these standard project-level roles:
*   **Storage Admin** (to create GCS buckets and sync PDFs)
*   **Cloud Build Editor** (to submit container builds)
*   **Cloud Run Admin** (to deploy and configure stubs)
*   **Service Account User** (to bind service accounts to the serverless revision)

### 3. Required Service Account Roles (For AI Agents E2E)
If deploying autonomous compliance agents connecting to this staging environment, ensure the Agent's Service Account is granted these three roles:
*   **Cloud Run Invoker** (`roles/run.invoker`) - To invoke FileNet package ingestion and Siebel outcome POST writebacks.
*   **Storage Object Viewer** (`roles/storage.objectViewer`) - To invoke native `google-cloud-storage` Python SDK downloads for PDF extraction.
*   **Cloud Datastore User** (`roles/datastore.user`) - To read and write state variables to the Firestore `inquiries` database.

---

## 🧪 The Operational Test Suite (Dataset Splits)

The **300-item dataset** is divided into three systematic test groups to evaluate agent robustness:

### 1. Happy Path (PKG-10001 to PKG-10120, PKG-10201 to PKG-10240)
*   **Description**: Pristine digital documents (`DIGITAL_CLEAN`). All limit parameters and Tax IDs match.
*   **Expected Agent Behavior**: Autonomous metadata extraction, deterministic math checks, database status updated directly to **`APPROVED`**, and CRM writebacks pushed.

### 2. Messy Set (PKG-10121 to PKG-10160, PKG-10241 to PKG-10270)
*   **Description**: Degraded scanned PDFs (`MEDIUM_SCAN` or `BAD_SCAN`) containing optical noises, dust specks, coffee ring stains, and:
    *   **Handwritten model name corrections** (original model crossed out, new model written in blue pen).
    *   **ellipse fingerprint smudges** bleeding over VIN or Price metadata cells to drop extraction confidence below the 88% failsafe threshold.
    *   **Handwritten signatures** dynamically overlaid on IRS Form W-9 Part II lines.
*   **Expected Agent Behavior**: Triggers exactly **one (1) retry** with adjusted parameters. If confidence remains low, halts, writes **`PENDING_HUMAN_REVIEW`** / `EXTRACTION_FAILED` to the DB, and dispatches a Gemini Enterprise A2A task alert.

### 3. Discrepancy Set (PKG-10161 to PKG-10200, PKG-10271 to PKG-10300)
*   **Description**: Specific business guideline failures to test safety net intercepts:
    *   **Incomplete Packages**: Lacking mandatory files (e.g. credit application is missing) -> Halts as **`PENDING_HUMAN_REVIEW`** and alerts.
    *   **Limit Overage <= $500.00**: Exceeds pre-approved credit limit slightly -> Halts as **`PENDING_HUMAN_REVIEW`**. Renders interactive **"Override & Approve"** and **"Reject Application"** A2UI action buttons.
    *   **Limit Overage > $500.00**: Exceeds limit severely -> Halts as hard validation failure (**`REJECTED`**). **Locks the layout**—the "Override & Approve" button is hidden, forcing rejection.
    *   **Tax ID Mismatch**: The extracted Tax ID from the invoice does not match the CRM profile -> Halts as hard validation failure (**`REJECTED`**).

---

## 🧪 Testing and Verification (Stubs OpenAPI endpoints)

Once your mock service is deployed and running (which outputs your active `Mocks Service URL`), you can test all integration endpoints using the following `curl` commands. These commands are pre-configured to pass your **Google OIDC Bearer Identity Token** for secure ingress:

### 1. Retrieve FileNet Package Metadata (GET)
Queries the FileNet mock broker for a complete package manifest and GCS direct document links:
```bash
curl -s -H "Authorization: Bearer \$(gcloud auth print-identity-token)" \
     "<MOCKS_SERVICE_URL>/api/filenet/v1/packages/PKG-10001" | json_pp
```

### 2. Retrieve Siebel CRM Underwriting Profile (GET)
Queries the Siebel stubs for pre-approved limit parameters:
```bash
curl -s -H "Authorization: Bearer \$(gcloud auth print-identity-token)" \
     "<MOCKS_SERVICE_URL>/api/siebel/v1/underwriting-profiles/13-5614226" | json_pp
```

### 3. Test Dynamic GCS PDF-to-PNG Rendering (GET)
Pulls `invoice.pdf` from GCS, renders it on-the-fly to PNG, and downloads it locally as `sample_invoice.png` for visual analysis:
```bash
curl -o sample_invoice.png \
     -H "Authorization: Bearer \$(gcloud auth print-identity-token)" \
     "<MOCKS_SERVICE_URL>/api/mocks/document_corpus/PKG-10001/invoice.png"
```

### 4. Submit Underwriting CRM Status Writeback (POST)
Simulates committing an `APPROVED` or `REJECTED` transaction record directly back to Siebel:
```bash
curl -X POST "<MOCKS_SERVICE_URL>/api/siebel/v1/loans/PKG-10001/status" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer \$(gcloud auth print-identity-token)" \
     -d '{
       "inquiry_id": "PKG-10001",
       "validation_passed": true,
       "processing_status": "APPROVED",
       "extracted_metadata": {
         "applicant_name": "Pioneer Excavation",
         "extracted_tax_id": "13-5614226",
         "equipment_make": "GE Healthcare",
         "equipment_model": "Revolution CT",
         "invoice_amount": 191861.38,
         "extraction_confidence": 0.97
       },
       "hitl_routing_required": false,
       "discrepancy_reason": null,
       "timestamp": "2026-05-26T19:30:00Z"
     }' | json_pp
```

---

## 📖 Mock OpenAPI Endpoints & JSON Schemas

The `fsi-mocks` microservice exposes stubs for FileNet document management, Siebel CRM system of record, and the dynamic GCS rendering engine. Below is the complete API contract reference:

### 1. GET `/api/filenet/v1/packages/{inquiry_id}`
*   **Description**: Retrieves a loan package manifest and direct authenticated storage paths.
*   **URL Parameter**: `inquiry_id` (String matching `^PKG-\d{5}$`, e.g. `PKG-10001`).
*   **Response Schema (`PackageManifest`)**:
    ```json
    {
      "package_id": "string (inquiry ID)",
      "package_completeness": "boolean (true if credit application exists)",
      "document_links": {
        "invoice.pdf": "string (direct authenticated storage URL)",
        "credit_app.pdf": "string (direct authenticated storage URL)",
        "w9.pdf": "string (direct authenticated storage URL)"
      },
      "timestamp": "string (ISO 8601 UTC timestamp)"
    }
    ```

### 2. GET `/api/siebel/v1/underwriting-profiles/{tax_id}`
*   **Description**: Retrieves the pre-approved credit limit and allowed asset categories for an applicant.
*   **URL Parameter**: `tax_id` (String matching `^\d{2}-\d{7}$`, e.g. `13-5614226`).
*   **Response Schema (`UnderwritingProfile`)**:
    ```json
    {
      "client_name": "string (Applicant Name)",
      "client_tax_id": "string (EIN Tax ID)",
      "approved_limit": "number (double, maximum approved lease amount)",
      "approved_asset_class": "string (Heavy Equipment, Agriculture, Medical, IT Hardware, Commercial Printing, Manufacturing)"
    }
    ```

### 3. POST `/api/siebel/v1/loans/{inquiry_id}/status`
*   **Description**: Commits the final compliance validation status and metadata back to Siebel CRM.
*   **URL Parameter**: `inquiry_id` (String matching `^PKG-\d{5}$`, e.g. `PKG-10001`).
*   **Request Payload Schema (`StatusWriteBack`)**:
    ```json
    {
      "inquiry_id": "string (e.g. PKG-10001)",
      "validation_passed": "boolean (true if all deterministic checks passed)",
      "processing_status": "string (APPROVED, REJECTED, PENDING_HUMAN_REVIEW, VALIDATION_FAILED)",
      "extracted_metadata": {
        "applicant_name": "string (extracted company name)",
        "extracted_tax_id": "string (extracted EIN)",
        "equipment_make": "string (extracted manufacturer)",
        "equipment_model": "string (extracted model)",
        "invoice_amount": "number (extracted total transaction price)",
        "extraction_confidence": "number (float, average confidence score)"
      },
      "hitl_routing_required": "boolean (true if sent to human underwriter)",
      "discrepancy_reason": "string | null (explanation for any failed checks)",
      "timestamp": "string (ISO 8601 UTC timestamp)"
    }
    ```
*   **Response Schema**:
    ```json
    {
      "transaction_id": "string (e.g. TXN-PKG-10001-1779756400)",
      "db_commit_status": "SUCCESS",
      "timestamp": "string (ISO 8601 UTC timestamp)"
    }
    ```

### 4. GET `/api/mocks/document_corpus/{inquiry_id}/{filename}`
*   **Description**: Streams files directly from GCS. If the requested filename ends in `.png`, the service dynamically renders page 1 of the target PDF to a rasterized PNG on-the-fly (using PyMuPDF) and streams it.
*   **URL Parameters**:
    *   `inquiry_id` (String, e.g. `PKG-10001`)
    *   `filename` (String, e.g. `invoice.pdf` or `invoice.png`)
*   **Response**: Binary stream (`application/pdf` or `image/png`).
