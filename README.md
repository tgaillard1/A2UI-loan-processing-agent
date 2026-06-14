# A2UI-Loan-Processing Agent

This directory contains the final, highly optimized, enterprise-grade E2E Loan Processing Compliance supervisor.

---

## 📂 Architecture & Key Capabilities

The Loan processing compliance supervisor acts as a highly structured, conservative compliance auditing officer verifying underwriting portfolios, extracting invoice and collateral metadata, checking lease limits deterministically, and posting real-time CRM status writebacks.

### 1. Generic Visual Executor Decoupling (`agent_executor.py`, `gemini_agent.py`)
- **A2UI Portability**: Visual formatting is decoupled from backend executor code. Layout and visual component rendering are dynamically built using lightweight post-LLM metadata block parsed via `---a2ui_JSON---` delimiter.
- **Dynamic Exposure Comparison**: Visual dashboard renders real-time comparison bar charts comparing **Requested Invoice Amount** against **Approved Credit Limit** dynamically.
- **Dynamic Checklists**: Tax ID matching, Credit Limit alignment, and Collateral Approval status render as interactive visual badges with status indicators (🔴/🟢/🚨).
- **Html Parser Explainer**: Seamless regex translation converted bold markings and itemized list symbols inside the plain-text explanation audit block into clean HTML blocks dynamically inside dashboard frames.

### 2. Session-Security & Concurrency Safety
- **Firestore Sanitizer (`make_firestore_safe`)**: Recursively scans session histories and payloads to sanitize nested entities into primitive structures, preventing Firestore `400 Property event_data contains an invalid nested entity` database schema exceptions.
- **Vertex AI Custom MIME-Type Purger**: Nested GenAI `inline_data` is filtered natively to cleanly purge `application/json+a2ui` visual frames from historical multi-turn messages. This prevents Vertex AI Generative Model API rejections of type `400 Unable to submit request because it has a mimeType parameter application/json+a2ui which is not supported`.

### 3. Low Confidence Halting Trigger
- Critical metadata fields extracted from scanned PDF invoices are continuously monitored. If any key extraction parameter falls below a strict confidence threshold (`< 0.88`), deterministic processing halts immediately. The inquiry is recorded as `EXTRACTION_FAILED` and dispatched cleanly to manual HITL review pipelines.

---

## ⚙️ Parametrizing Integration Endpoints (`deploy.sh`)

The compliance agent communicates with secure CRM mocks servers. You can set up those assets by going to the "staging-assets" directoyr in this repo.  To prevent URL-routing double slashes or case mismatch mismatches, integration endpoints are fully parameterized.

### Configuration parameters:
1. **`SIEBEL_URL`**: The target Siebel CRM System of Record endpoint (default: Regional Mocks Service).
2. **`FILENET_URL`**: The FileNet packages manifest endpoint (default: Regional Mocks Service).

Both endpoints are trimmed of trailing slashes dynamically inside `siebel.py` and `filenet.py` using `.rstrip("/")`, and all Inquiry IDs are auto-uppercased using `.upper().strip()` to comply with OpenAPI schemas (`^PKG-\d{5}$`), preventing **404 Not Found** routing errors.

---

## 🚀 Deployment & Upgrades

To deploy the service cleanly to Google Cloud Run:

```bash
# Standard deployment using regional mocks default endpoint:
./deploy.sh cpe-bustosjuan-experimental equipment-finance-compliance-agent-v6 gemini-3.5-flash

# Overriding integration endpoints dynamically during deployment:
SIEBEL_URL="https://cpe-bustosjuan-fsi-mocks-906194901769.us-central1.run.app" FILENET_URL="https://cpe-bustosjuan-fsi-mocks-906194901769.us-central1.run.app" \
./deploy.sh cpe-bustosjuan-experimental equipment-finance-compliance-agent-v6 gemini-3.5-flash
```

> [!IMPORTANT]
> **Memory Constraints**: To support downloading large scanned underwriting documents concurrently and converting pages cleanly to base64 images without crash, the container instance memory allocation is upgraded to **`2Gi`** (`MEMORY="2Gi"` in `deploy.sh`).

---

## 🧪 E2E Automated Evaluation Suite (`eval_runner.py`)

An E2E automated black-box testing suite is included to verify correct compliance behaviors across 6 distinct live cases for the three main failure categories:

1. **Category 1: Incomplete Packages** (Missing Credit Application)
2. **Category 2: Limit Overage** (Invoice Amount > Lease Limit)
3. **Category 3: Tax ID Mismatch** (Integrity Breach Verification)

### Running Evaluations:
The runner executes transactions directly against the deployed endpoint using secure Google OIDC impersonation:

```bash
# Run evaluations:
python3 scratch/eval_runner.py
```
