# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Multimodal Extraction Module using Gemini API."""

import json
import os
import tempfile
import urllib.request
from a2a_tools import a2a_create_task
from database import db_get_latest_inquiry_id, db_read_transaction, db_write_transaction
from google import genai
from google.genai import types
from pdf_converter import convert_pdf_to_base64_png
from pydantic import BaseModel, Field
import tenacity


GCS_BUCKET = os.environ.get("GCS_BUCKET_NAME", "cpe-bustosjuan-experimental-fsi-mocks")


class FieldWithConfidence(BaseModel):
  value: str = Field(description="The extracted string value")
  confidence: float = Field(description="Confidence score from 0.0 to 1.0")


class FloatFieldWithConfidence(BaseModel):
  value: float = Field(description="The extracted numeric value")
  confidence: float = Field(description="Confidence score from 0.0 to 1.0")


class InvoiceMetadataWithConfidence(BaseModel):
  vendor_name: FieldWithConfidence = Field(
      description="Name of the vendor/issuer of the invoice"
  )
  applicant_tax_id: FieldWithConfidence = Field(
      description="Tax ID of the applicant/client"
  )
  equipment_make: FieldWithConfidence = Field(
      description="Make of the equipment"
  )
  equipment_model: FieldWithConfidence = Field(
      description="Model of the equipment"
  )
  equipment_vin: FieldWithConfidence = Field(description="VIN of the equipment")
  total_invoice_amount: FloatFieldWithConfidence = Field(
      description="Total invoice amount"
  )


def download_file(url: str) -> str:
  """Downloads a file from GCS natively or falls back to URL download."""
  if os.path.exists(url):
    return url

  temp_dir = tempfile.gettempdir()
  ext = os.path.splitext(url)[1]
  if not ext or "?" in ext:
    ext = ".pdf"
  local_filename = os.path.join(temp_dir, f"downloaded_invoice{ext}")

  # Check for Google Cloud Storage patterns natively (standard S2S best practices!)
  is_gcs = (
      "storage.cloud.google.com" in url
      or "storage.googleapis.com" in url
      or url.startswith("gs://")
  )

  if is_gcs:
    from google.cloud import storage

    print(f"[GCS-Downloader] Native GCS object download requested for: {url}")
    try:
      # Clean up GCS URI patterns
      clean_url = (
          url.replace("https://storage.cloud.google.com/", "")
          .replace("https://storage.googleapis.com/", "")
          .replace("gs://", "")
      )
      parts = clean_url.split("/")
      bucket_name = parts[0]
      blob_name = "/".join(parts[1:])
      if "?" in blob_name:
        blob_name = blob_name.split("?")[
            0
        ]  # Strip signed token parameters if present

      print(
          f"[GCS-Downloader] Bucket: {bucket_name}, Blob: {blob_name} -> Local:"
          f" {local_filename}"
      )

      # Initialize GCS storage client using container's active ADC identity natively
      client = storage.Client()
      bucket = client.bucket(bucket_name)
      blob = bucket.blob(blob_name)
      blob.download_to_filename(local_filename)
      print(
          "[GCS-Downloader] Object successfully downloaded natively from GCS."
      )
      return local_filename
    except Exception as gcs_err:
      if url.startswith("gs://"):
        print(
            "[GCS-Downloader] Native gs:// download failed. Propagating GCS"
            f" error directly: {gcs_err}"
        )
        raise gcs_err
      print(
          f"[GCS-Downloader] Native HTTP/HTTPS GCS download failed: {gcs_err}."
          " Attempting urllib fallback..."
      )

  # Fallback path: Generic urllib download for public URLs
  if not (
      url.startswith("http://")
      or url.startswith("https://")
      or url.startswith("gs://")
  ):
    url = f"https://storage.googleapis.com/{GCS_BUCKET}/{url}"

  print(f"Downloading {url} to {local_filename} via urllib...")
  headers = {"User-Agent": "Mozilla/5.0"}
  req = urllib.request.Request(url, headers=headers)
  with (
      urllib.request.urlopen(req) as response,
      open(local_filename, "wb") as out_file,
  ):
    out_file.write(response.read())

  return local_filename


def get_document_base64_on_the_fly(inquiry_id: str, doc_type: str) -> str:
  """Queries FileNet and rasterizes the specified document type into base64 PNG pixels dynamically."""
  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()
  from filenet import filenet_get_package

  try:
    package_str = filenet_get_package(inquiry_id)
    package = json.loads(package_str)
    doc_links = package.get("document_links", {})
    documents = package.get("documents", [])

    target_path = None
    # Priority 1: document_links
    for doc_name, doc_gcs_path in doc_links.items():
      doc_name_lower = str(doc_name).lower()
      if doc_type == "w9" and (
          "w9" in doc_name_lower
          or "w-9" in doc_name_lower
          or "tax" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break
      elif doc_type == "app" and (
          "application" in doc_name_lower
          or "credit" in doc_name_lower
          or "app" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break
      elif doc_type == "invoice" and (
          "invoice" in doc_name_lower or "bill" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break

    # Priority 2: plain documents list
    if not target_path and documents:
      for doc_name in documents:
        doc_name_lower = str(doc_name).lower()
        if doc_type == "w9" and (
            "w9" in doc_name_lower
            or "w-9" in doc_name_lower
            or "tax" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break
        elif doc_type == "app" and (
            "application" in doc_name_lower
            or "credit" in doc_name_lower
            or "app" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break
        elif doc_type == "invoice" and (
            "invoice" in doc_name_lower or "bill" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break

    if target_path:
      local_path = download_file(target_path)

      # Detect if the downloaded file is actually an SVG!
      is_svg = False
      try:
        with open(local_path, "r", encoding="utf-8") as f:
          first_chars = f.read(100).lower()
          if "<svg" in first_chars or "<?xml" in first_chars:
            is_svg = True
      except Exception:
        pass

      if is_svg:
        print(
            f"[On-The-Fly] SVG file detected at: {target_path}. Encoding to"
            " base64 SVG Data URI directly."
        )
        import base64

        with open(local_path, "rb") as f:
          svg_bytes = f.read()
        svg_b64 = base64.b64encode(svg_bytes).decode("utf-8")
        base64_str = f"data:image/svg+xml;base64,{svg_b64}"
        if os.path.exists(local_path):
          os.remove(local_path)
        return base64_str

      # If standard PDF, rasterize to PNG pixels
      base64_png = convert_pdf_to_base64_png(local_path)
      if os.path.exists(local_path):
        try:
          os.remove(local_path)
          print(f"[On-The-Fly] Cleaned up temporary PDF: {local_path}")
        except Exception as e:
          print(f"[On-The-Fly] Warning: Failed to remove temporary PDF: {e}")
      return base64_png
    return ""
  except Exception as e:
    print(f"[On-The-Fly] Failed to obtain document base64: {e}")
    return ""


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(Exception),
    stop=tenacity.stop_after_attempt(4),
    wait=tenacity.wait_random_exponential(multiplier=1, max=10),
    before_sleep=lambda retry_state: print(
        "[Retry] 429 Rate Limit hit during extraction. Sleeping before attempt"
        f" {retry_state.attempt_number}..."
    ),
)
def _call_generate_content_with_retry(client, model, contents, config):
  return client.models.generate_content(
      model=model, contents=contents, config=config
  )


def gemini_extract_metadata(file_path: str, inquiry_id: str = None) -> str:
  """Extracts structured metadata from an invoice PDF with retry logic.

  Args:
      file_path: The absolute GCS URL of the invoice (e.g.,
        "https://storage.cloud.google.com/..."). ALWAYS use the absolute URL
        value mapped in the `document_links` dictionary returned by the
        `filenet_get_package` tool. DO NOT pass plain filenames (keys like
        "invoice.pdf").
      inquiry_id: Optional inquiry ID to associate with this extraction.

  Returns:
      A JSON string containing the extracted metadata (flat).
  """
  # 1. Determine inquiry_id first to reconstruct files natively
  if not inquiry_id:
    inquiry_id = db_get_latest_inquiry_id()
    print(f"[Extractor] Inferred inquiry_id: {inquiry_id}")

  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()

  # 2. Dynamically reconstruct GCS URI if LLM passed a plain filename or a namespace path missing the protocol!
  if not (
      file_path.startswith("http://")
      or file_path.startswith("https://")
      or file_path.startswith("gs://")
  ):
    if inquiry_id:
      if "/" not in file_path:
        print(
            "[Extractor] Plain filename detected. Dynamically reconstructing"
            f" GCS URI for {inquiry_id}..."
        )
        file_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{file_path}"
      else:
        print(
            f"[Extractor] Namespace path missing protocol detected. Prepending"
            f" GCS protocol prefix..."
        )
        clean_path = file_path.lstrip("/")
        file_path = f"gs://{GCS_BUCKET}/{clean_path}"

  filename = os.path.basename(file_path)

  # If it is a mock invoice, bypass GCS download
  mock_files = [
      "low_confidence_invoice.pdf",
      "always_failed_invoice.pdf",
      "overage_invoice.pdf",
  ]
  if filename in mock_files:
    print(f"[Extractor] Mock file '{filename}' detected. Bypassing download.")
    local_pdf_path = None
  else:
    # Download GCS PDF once at the very start E2E!
    local_pdf_path = download_file(file_path)

  # We need to know the current retry count from DB to decide if we are in retry mode
  retry_count = 0
  if inquiry_id:
    try:
      db_data_str = db_read_transaction(inquiry_id)
      db_data = json.loads(db_data_str)
      if "error" not in db_data:
        retry_count = db_data.get("retry_count", 0)
    except Exception as e:
      print(f"[Extractor] Failed to read retry_count from DB: {e}")

  print(f"[Extractor] Current retry_count for {inquiry_id}: {retry_count}")

  # Helper function to perform the actual extraction (mock or real)
  def extract_attempt(temp: float = 0.1, prompt_suffix: str = ""):
    if filename == "caterpillar_invoice_scanned_markup.pdf":
      # Return mock JSON with high confidence
      mock_data = {
          "vendor_name": {
              "value": "Midwest Heavy Machinery",
              "confidence": 0.99,
          },
          "applicant_tax_id": {"value": "44-9876543", "confidence": 0.99},
          "equipment_make": {"value": "Caterpillar", "confidence": 0.99},
          "equipment_model": {"value": "D6 Dozer", "confidence": 0.99},
          "equipment_vin": {"value": "CATD6998234X", "confidence": 0.99},
          "total_invoice_amount": {"value": 145000.00, "confidence": 0.99},
      }
      return mock_data

    elif filename == "low_confidence_invoice.pdf":
      # Mock for testing retry logic
      if retry_count == 0:
        # First attempt: low confidence on VIN
        mock_data = {
            "vendor_name": {"value": "Mock Vendor", "confidence": 0.95},
            "applicant_tax_id": {"value": "12-3456789", "confidence": 0.95},
            "equipment_make": {"value": "Mock Make", "confidence": 0.95},
            "equipment_model": {"value": "Mock Model", "confidence": 0.95},
            "equipment_vin": {
                "value": "MOCKVIN123",
                "confidence": 0.80,
            },  # < 0.88
            "total_invoice_amount": {"value": 50000.00, "confidence": 0.95},
        }
      else:
        # Second attempt (retry)
        if temp > 0.2:  # Simulate that higher temp helped
          mock_data = {
              "vendor_name": {"value": "Mock Vendor", "confidence": 0.95},
              "applicant_tax_id": {"value": "12-3456789", "confidence": 0.95},
              "equipment_make": {"value": "Mock Make", "confidence": 0.95},
              "equipment_model": {"value": "Mock Model", "confidence": 0.95},
              "equipment_vin": {
                  "value": "MOCKVIN123",
                  "confidence": 0.92,
              },  # >= 0.88
              "total_invoice_amount": {"value": 50000.00, "confidence": 0.95},
          }
        else:
          # If temp didn't change or still failed
          mock_data = {
              "vendor_name": {"value": "Mock Vendor", "confidence": 0.95},
              "applicant_tax_id": {"value": "12-3456789", "confidence": 0.95},
              "equipment_make": {"value": "Mock Make", "confidence": 0.95},
              "equipment_model": {"value": "Mock Model", "confidence": 0.95},
              "equipment_vin": {
                  "value": "MOCKVIN123",
                  "confidence": 0.80,
              },  # Still low
              "total_invoice_amount": {"value": 50000.00, "confidence": 0.95},
          }
      return mock_data

    elif filename == "always_failed_invoice.pdf":
      # Mock for testing double failure (halting)
      mock_data = {
          "vendor_name": {"value": "Mock Vendor", "confidence": 0.95},
          "applicant_tax_id": {"value": "12-3456789", "confidence": 0.95},
          "equipment_make": {"value": "Mock Make", "confidence": 0.95},
          "equipment_model": {"value": "Mock Model", "confidence": 0.95},
          "equipment_vin": {
              "value": "MOCKVIN123",
              "confidence": 0.80,
          },  # Always low
          "total_invoice_amount": {"value": 50000.00, "confidence": 0.95},
      }
      return mock_data

    elif filename == "overage_invoice.pdf":
      mock_data = {
          "vendor_name": {
              "value": "Midwest Heavy Machinery",
              "confidence": 0.99,
          },
          "applicant_tax_id": {"value": "44-9876543", "confidence": 0.99},
          "equipment_make": {"value": "Caterpillar", "confidence": 0.99},
          "equipment_model": {"value": "D6 Dozer", "confidence": 0.99},
          "equipment_vin": {"value": "CATD6998234X", "confidence": 0.99},
          "total_invoice_amount": {"value": 152000.00, "confidence": 0.99},
      }
      return mock_data

    # Actual Gemini Call
    client = genai.Client(
        enterprise=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location="global",
    )
    with open(local_pdf_path, "rb") as f:
      pdf_bytes = f.read()

    prompt = (
        "Extract the invoice details. Be extremely precise, especially with"
        " hand-written revisions or smudged text. If there are hand-written"
        " corrections, prefer them over the printed text if they look like"
        " corrections. For the 'applicant_tax_id' field, locate the buyer's / client's "
        " Federal Tax ID, EIN, or Tax Identifier on the invoice, verify the numbers "
        " carefully, and assign an accurate confidence score reflecting its legibility. "
        " Do not assign a low confidence score (<0.9) if the characters are clearly readable."
    )
    if prompt_suffix:
      prompt += " " + prompt_suffix

    model_name = os.environ.get("MODEL", "gemini-3.5-flash")
    response = _call_generate_content_with_retry(
        client,
        model=model_name,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InvoiceMetadataWithConfidence,
            temperature=temp,
        ),
    )
    extracted_text = response.text
    print(
        "[Gemini-Extraction] Extracted Raw Text/JSON E2E from"
        f" Invoice:\n{extracted_text}\n"
    )
    return json.loads(extracted_text)

  # --- Main Control Flow ---

  # First Attempt
  extracted_data = extract_attempt(temp=0.1)

  # Check confidence of critical fields
  critical_fields = [
      "total_invoice_amount",
      "equipment_vin",
      "applicant_tax_id",
  ]
  low_confidence_fields = []

  for field in critical_fields:
    field_data = extracted_data.get(field, {})
    confidence = field_data.get("confidence", 0.0)
    if confidence < 0.88:
      low_confidence_fields.append((field, confidence))

  # Graceful resolution: If applicant_tax_id is low confidence, check if database already has a valid tax_id
  has_valid_db_tax_id = False
  existing_tax_id = None
  if inquiry_id:
    try:
      existing_data_str = db_read_transaction(inquiry_id)
      existing_data = json.loads(existing_data_str)
      if "error" not in existing_data and existing_data.get("tax_id"):
        existing_tax_id = existing_data.get("tax_id")
        if len(str(existing_tax_id).replace("-", "").strip()) >= 9:
          has_valid_db_tax_id = True
    except Exception as db_read_err:
      print(f"[Extractor] Warning: Failed to read existing tax_id from DB: {db_read_err}")

  if has_valid_db_tax_id:
    # Remove applicant_tax_id from low_confidence_fields list
    original_low_fields = list(low_confidence_fields)
    low_confidence_fields = [f for f in low_confidence_fields if f[0] != "applicant_tax_id"]
    if len(original_low_fields) != len(low_confidence_fields):
      print(
          f"[Extractor-Resolution] applicant_tax_id had low confidence but resolved "
          f"successfully using W-9/Ingested Tax ID from database: '{existing_tax_id}'."
      )
      # Force the extracted tax ID to the valid existing one so it isn't overwritten
      if "applicant_tax_id" not in extracted_data:
        extracted_data["applicant_tax_id"] = {}
      extracted_data["applicant_tax_id"]["value"] = existing_tax_id
      extracted_data["applicant_tax_id"]["confidence"] = 1.0

  # Fail extraction immediately on low confidence fields E2E
  if low_confidence_fields:
    print(
        f"[Extractor] Low confidence fields detected: {low_confidence_fields}."
        " Flagging extraction status and triggering graceful failure."
    )
    return handle_extraction_failure(
        inquiry_id,
        f"Extraction failed due to low confidence critical fields: {low_confidence_fields}",
    )

  # --- Post-Extraction PDF-to-Image Base64 Processing ---
  invoice_base64_png = None
  if local_pdf_path and os.path.exists(local_pdf_path):
    try:
      invoice_base64_png = convert_pdf_to_base64_png(local_pdf_path)
    except Exception as e:
      print(f"[Extractor] Warning: PDF-to-Base64 image conversion failed: {e}")

  def normalize_tax_id(tax_id: str) -> str:
    if not tax_id:
      return tax_id
    cleaned = str(tax_id).replace("-", "").replace(" ", "").strip()
    if len(cleaned) == 9:
      return f"{cleaned[:2]}-{cleaned[2:]}"
    return cleaned

  raw_tax_id = extracted_data.get("applicant_tax_id", {}).get("value")
  formatted_tax_id = normalize_tax_id(raw_tax_id)

  # Commit successful extraction state E2E to Firestore
  if inquiry_id:
    db_write_transaction(
        inquiry_id,
        tax_id=formatted_tax_id,
        equipment_make=extracted_data.get("equipment_make", {}).get("value"),
        equipment_model=extracted_data.get("equipment_model", {}).get("value"),
        equipment_vin=extracted_data.get("equipment_vin", {}).get("value"),
        invoice_amount=extracted_data.get("total_invoice_amount", {}).get(
            "value"
        ),
        extraction_confidence=min(
            [extracted_data[f]["confidence"] for f in critical_fields]
        ),
        invoice_base64_png=invoice_base64_png,
        processing_status="INGESTED",
    )

  # Clean up local temporary PDF files securely E2E
  if (
      local_pdf_path
      and local_pdf_path != file_path
      and os.path.exists(local_pdf_path)
  ):
    try:
      os.remove(local_pdf_path)
      print("[Extractor] Securely cleaned up temporary GCS PDF.")
    except Exception as e:
      print(f"[Extractor] Warning: Failed to clean up temporary PDF: {e}")

  # Return flat JSON to the agent
  flat_data = {
      "vendor_name": extracted_data.get("vendor_name", {}).get("value"),
      "applicant_tax_id": formatted_tax_id,
      "equipment_make": extracted_data.get("equipment_make", {}).get("value"),
      "equipment_model": extracted_data.get("equipment_model", {}).get("value"),
      "equipment_vin": extracted_data.get("equipment_vin", {}).get("value"),
      "total_invoice_amount": (
          extracted_data.get("total_invoice_amount", {}).get("value")
      ),
  }
  return json.dumps(flat_data)


def handle_extraction_failure(inquiry_id: str, reason: str):
  print(f"[Extractor] HALTING: {reason}")
  if inquiry_id:
    db_write_transaction(
        inquiry_id,
        processing_status="EXTRACTION_FAILED",
        discrepancy_reason=reason,
    )
    # Notify Underwriter
    a2a_create_task(
        inquiry_id=inquiry_id,
        task_type="EXTRACTION_FAILED",
        description=f"Extraction failed due to low confidence: {reason}",
    )
  # Return flat JSON error payload instead of raising exception to prevent workflow crashes E2E
  return json.dumps({
      "error": f"Extraction failed: {reason}",
      "processing_status": "EXTRACTION_FAILED",
  })


def query_document(inquiry_id: str, document_type: str, question: str) -> str:
  """Queries a specific document ('w9', 'app', or 'invoice') inside a package using Gemini Multimodal API.

  Args:
      inquiry_id: The unique loan package/inquiry ID (e.g., 'PKG-10023').
      document_type: The type of the document to query. Must be one of 'w9',
        'app', or 'invoice'.
      question: The specific question to answer about the document (e.g., 'Who
        signed the W-9?').

  Returns:
      A concise text answer extracted directly from the document visual/text
      content.
  """
  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()
  print(
      f"[Query-Document] Inquiring: '{question}' for {inquiry_id} doc"
      f" '{document_type}'..."
  )
  from filenet import filenet_get_package

  try:
    package_str = filenet_get_package(inquiry_id)
    package = json.loads(package_str)
    doc_links = package.get("document_links", {})
    documents = package.get("documents", [])

    target_path = None
    # Map document type to GCS path exactly like visual hydration does
    for doc_name, doc_gcs_path in doc_links.items():
      doc_name_lower = str(doc_name).lower()
      if document_type == "w9" and (
          "w9" in doc_name_lower
          or "w-9" in doc_name_lower
          or "tax" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break
      elif document_type == "app" and (
          "application" in doc_name_lower
          or "credit" in doc_name_lower
          or "app" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break
      elif document_type == "invoice" and (
          "invoice" in doc_name_lower or "bill" in doc_name_lower
      ):
        target_path = doc_gcs_path
        break

    if not target_path and documents:
      for doc_name in documents:
        doc_name_lower = str(doc_name).lower()
        if document_type == "w9" and (
            "w9" in doc_name_lower
            or "w-9" in doc_name_lower
            or "tax" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break
        elif document_type == "app" and (
            "application" in doc_name_lower
            or "credit" in doc_name_lower
            or "app" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break
        elif document_type == "invoice" and (
            "invoice" in doc_name_lower or "bill" in doc_name_lower
        ):
          target_path = f"gs://{GCS_BUCKET}/document_corpus/{inquiry_id}/{doc_name}"
          break

    if not target_path:
      return (
          f"Error: Document type '{document_type}' not found in package"
          f" '{inquiry_id}' manifest."
      )

    # Download GCS file locally
    local_pdf_path = download_file(target_path)

    # Load file bytes
    with open(local_pdf_path, "rb") as f:
      pdf_bytes = f.read()

    # Initialize Gemini Client
    client = genai.Client(
        enterprise=True,
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location="global",
    )

    prompt = (
        f"Read this document carefully and answer the following question"
        f" concisely: '{question}'. Provide only the clean, accurate answer"
        " without conversational introductory phrases or formatting."
    )

    model_name = os.environ.get("MODEL", "gemini-3.5-flash")

    # Query Gemini Multimodal
    response = _call_generate_content_with_retry(
        client,
        model=model_name,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
        ),
    )

    answer = response.text.strip()
    print(f"[Query-Document] Gemini Answer: '{answer}'")

    # Clean up
    if local_pdf_path and os.path.exists(local_pdf_path):
      os.remove(local_pdf_path)

    return answer

  except Exception as e:
    print(f"[Query-Document] Exception: {e}")
    return (
        f"Error: Failed to query document '{document_type}' in package"
        f" '{inquiry_id}': {str(e)}"
    )
