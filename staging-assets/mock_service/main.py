from fastapi import FastAPI, APIRouter, HTTPException, Depends, Header, Path
from pydantic import BaseModel, Field
from typing import Optional, List
import os
import datetime
import pandas as pd
from google.cloud import storage

app = FastAPI(
    title="Cymbal Bank Legacy System Mocks",
    description="Interactive Swagger Sandbox serving mock namespaced stubs for FileNet and Siebel CRM.",
    version="1.0.0"
)

# Secure Token Check Middleware (Mock bearer token check)
async def verify_token(authorization: str = Header(None), x_mock_token: str = Header(None)):
    token_val = None
    if authorization and authorization.startswith("Bearer "):
        token_val = authorization.split(" ")[1]
    elif x_mock_token:
        token_val = x_mock_token
        
    if not token_val or token_val != "mock-sandbox-token-eqf":
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API Token. Provide 'Authorization: Bearer mock-sandbox-token-eqf' or 'X-Mock-Token: mock-sandbox-token-eqf'"
        )
    return token_val

def clean_dollar_string(val):
    return val.replace("$", "").replace(",", "").strip()

# Load Ground-Truth Ledger into memory at boot
LEDGER_FILE = os.path.join(os.path.dirname(__file__), "ledger.csv")
if not os.path.exists(LEDGER_FILE):
    raise RuntimeError(f"Critical: ledger.csv database not found at path: {LEDGER_FILE}")

print(f"Loading ledger.csv into in-memory database...")
ledger_df = pd.read_csv(LEDGER_FILE)
# Strip leading/trailing whitespace from column names
ledger_df.columns = ledger_df.columns.str.strip()
print(f"Successfully indexed {len(ledger_df)} transaction pre-approval profiles.")

# GCS Configuration for FileNet Mock Broker
GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "cpe-bustosjuan-experimental-fsi-mocks")
try:
    gcs_client = storage.Client()
except Exception as e:
    print(f"Warning: Failed to initialize GCS Client locally: {e}. GCS file brokering will fail unless running on GCP.")
    gcs_client = None

# -----------------------------------------------------------------------------
# FILENET API GROUP: /api/filenet/v1
# -----------------------------------------------------------------------------
filenet_router = APIRouter(prefix="/api/filenet/v1", tags=["FileNet Document Management"])

class PackageManifest(BaseModel):
    package_id: str = Field(..., pattern="^PKG-\\d{5}$")
    package_completeness: bool
    document_links: dict
    timestamp: datetime.datetime

@filenet_router.get("/packages/{inquiry_id}", response_model=PackageManifest)
async def get_filenet_package(
    inquiry_id: str = Path(..., description="Target inquiry ID (e.g. PKG-10025)"),
    authorization: Optional[str] = Header(None)
):
    # Find package record in in-memory ledger
    pkg_records = ledger_df[ledger_df["Package_ID"] == inquiry_id]
    if pkg_records.empty:
        raise HTTPException(status_code=404, detail=f"Package ID {inquiry_id} not found in master pre-approval ledger.")
        
    record = pkg_records.iloc[0]
    is_incomplete = (str(record["Validation_Status"]).strip() == "Incomplete")
    
    if not gcs_client:
        raise HTTPException(status_code=500, detail="GCS Storage Client is not initialized on API Host container.")
        
    try:
        bucket = gcs_client.bucket(GCS_BUCKET_NAME)
        # Check GCS folders for files
        prefix = f"document_corpus/{inquiry_id}/"
        blobs = list(bucket.list_blobs(prefix=prefix))
        
        if not blobs:
            raise HTTPException(
                status_code=404,
                detail=f"Directory document_corpus/{inquiry_id}/ is empty or missing in GCS bucket {GCS_BUCKET_NAME}."
            )
            
        # Generate secure signed URLs for the files
        document_links = {}
        for blob in blobs:
            filename = blob.name.split("/")[-1]
            if not filename or filename == "style.css": # Wipes out CSS or stylesheet from link manifest
                continue
                
            # Generate secure direct HTTPS storage path (authenticated via GCP active session)
            direct_url = f"https://storage.cloud.google.com/{GCS_BUCKET_NAME}/{blob.name}"
            document_links[filename] = direct_url
            
        # Double check completeness compliance
        has_credit_app = any("credit_app" in name for name in document_links.keys())
        
        return {
            "package_id": inquiry_id,
            "package_completeness": has_credit_app and not is_incomplete,
            "document_links": document_links,
            "timestamp": datetime.datetime.utcnow()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GCS Broker Error: {e}")

# -----------------------------------------------------------------------------
# SIEBEL CRM API GROUP: /api/siebel/v1
# -----------------------------------------------------------------------------
siebel_router = APIRouter(prefix="/api/siebel/v1", tags=["Siebel CRM System of Record"])

class UnderwritingProfile(BaseModel):
    client_name: str
    client_tax_id: str = Field(..., pattern="^\\d{2}-\\d{7}$")
    approved_limit: float
    approved_asset_class: str

class ExtractedMetadata(BaseModel):
    applicant_name: str
    extracted_tax_id: str = Field(..., pattern="^\\d{2}-\\d{7}$")
    equipment_make: str
    equipment_model: str
    invoice_amount: float
    extraction_confidence: float

class StatusWriteBack(BaseModel):
    inquiry_id: str = Field(..., pattern="^PKG-\\d{5}$")
    validation_passed: bool
    processing_status: str
    extracted_metadata: ExtractedMetadata
    hitl_routing_required: bool
    discrepancy_reason: Optional[str] = None
    timestamp: datetime.datetime

@siebel_router.get("/underwriting-profiles/{tax_id}", response_model=UnderwritingProfile)
async def get_underwriting_profile(
    tax_id: str = Path(..., description="Client EIN Tax ID (formatted as XX-XXXXXXX)"),
    authorization: Optional[str] = Header(None)
):
    # Search in-memory ledger database by Tax ID (Siebel profile tax ID column)
    # Stripe leading/trailing spaces from strings
    matched_records = ledger_df[ledger_df["Tax_ID_Siebel"].str.strip() == tax_id.strip()]
    if matched_records.empty:
        raise HTTPException(
            status_code=404, 
            detail=f"No pre-approved underwriting record found for Tax ID {tax_id} inside Siebel database."
        )
        
    record = matched_records.iloc[0]
    
    # Parse limit value out of ledger string (e.g. "$206,400.00" -> 206400.00)
    limit_str = str(record["Approved_Limit"])
    limit_val = float(clean_dollar_string(limit_str))
    
    return {
        "client_name": str(record["Applicant_Name"]).strip(),
        "client_tax_id": tax_id,
        "approved_limit": limit_val,
        "approved_asset_class": str(record["Asset_Class"]).strip()
    }

@siebel_router.post("/loans/{inquiry_id}/status")
async def update_loan_status(
    inquiry_id: str = Path(..., pattern="^PKG-\\d{5}$"),
    payload: StatusWriteBack = None,
    authorization: Optional[str] = Header(None)
):
    if not payload:
        raise HTTPException(status_code=400, detail="Missing request payload.")
        
    # Validate inquiry ID matches
    if payload.inquiry_id != inquiry_id:
        raise HTTPException(status_code=400, detail="Inquiry ID mismatch between path and request body.")
        
    print(f"Write-back committed to CRM for {inquiry_id} successfully. Processing Status: {payload.processing_status}")
    
    return {
        "transaction_id": f"TXN-{inquiry_id}-{int(datetime.datetime.utcnow().timestamp())}",
        "db_commit_status": "SUCCESS",
        "timestamp": datetime.datetime.utcnow()
    }

import fitz
from fastapi.responses import StreamingResponse
import io

@app.get("/api/mocks/document_corpus/{inquiry_id}/{filename}")
async def proxy_gcs_file(inquiry_id: str, filename: str):
    if not gcs_client:
        raise HTTPException(status_code=500, detail="GCS Storage Client not initialized.")
    try:
        bucket = gcs_client.bucket(GCS_BUCKET_NAME)
        
        # If requesting a PNG, dynamically render the GCS PDF on the fly!
        if filename.endswith(".png"):
            pdf_filename = filename.replace(".png", ".pdf")
            blob_name = f"document_corpus/{inquiry_id}/{pdf_filename}"
            blob = bucket.blob(blob_name)
            if not blob.exists():
                raise HTTPException(status_code=404, detail=f"Source PDF {blob_name} not found in GCS.")
                
            pdf_bytes = blob.download_as_bytes()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            png_bytes = pix.tobytes("png")
            doc.close()
            
            return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")
            
        # Otherwise stream raw PDF content with clickjacking bypass headers E2E!
        else:
            blob_name = f"document_corpus/{inquiry_id}/{filename}"
            blob = bucket.blob(blob_name)
            if not blob.exists():
                raise HTTPException(status_code=404, detail=f"File {blob_name} not found in GCS.")
                
            file_stream = io.BytesIO()
            blob.download_to_file(file_stream)
            file_stream.seek(0)
            
            response_headers = {
                "X-Frame-Options": "ALLOWALL",
                "Content-Security-Policy": "frame-ancestors *; frame-src *",
                "Access-Control-Allow-Origin": "*"
            }
            return StreamingResponse(file_stream, media_type="application/pdf", headers=response_headers)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy Error: {str(e)}")

# Register Routers
app.include_router(filenet_router)
app.include_router(siebel_router)
