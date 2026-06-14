from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, Optional

# Initialize Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("database")

FALLBACK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "originations_firestore_fallback.json",
)

# Try initializing Firestore
db = None
use_fallback = False

try:
  project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
  if not project_id:
    raise ValueError("GOOGLE_CLOUD_PROJECT environment variable is not set.")

  from google.cloud import firestore

  db_name = os.environ.get("FIRESTORE_DATABASE_NAME", "(default)")
  db = firestore.Client(project=project_id, database=db_name)

  logger.info(
      f"Firestore client initialized successfully on database '{db_name}'."
  )
except Exception as e:
  logger.warning(
      f"Failed to initialize Firestore client: {e}. Falling back to local JSON"
      " file."
  )
  use_fallback = True


def _read_fallback_db() -> Dict[str, Any]:
  if not os.path.exists(FALLBACK_FILE):
    return {}
  try:
    with open(FALLBACK_FILE, "r") as f:
      return json.load(f)
  except Exception as e:
    logger.error(f"Failed to read fallback file: {e}")
    return {}


def _write_fallback_db(data: Dict[str, Any]):
  try:
    with open(FALLBACK_FILE, "w") as f:
      json.dump(data, f, indent=2)
  except Exception as e:
    logger.error(f"Failed to write to fallback file: {e}")


def init_db():
  """Initializes the database.

  For Firestore, this is a no-op or simple check.
  """
  global use_fallback
  if use_fallback:
    if not os.path.exists(FALLBACK_FILE):
      _write_fallback_db({})
    return "Fallback database initialized."
  try:
    # Return success
    return "Firestore database initialized successfully."
  except Exception as e:
    logger.warning(
        f"Firestore initialization check failed: {e}. Switching to fallback."
    )
    use_fallback = True
    if not os.path.exists(FALLBACK_FILE):
      _write_fallback_db({})
    return "Fallback database initialized after Firestore check failed."


def db_write_transaction(
    inquiry_id: str,
    client_name: str = None,
    tax_id: str = None,
    equipment_make: str = None,
    equipment_model: str = None,
    equipment_vin: str = None,
    invoice_amount: float = None,
    approved_limit: float = None,
    package_completeness: bool = None,
    extraction_confidence: float = None,
    validation_passed: bool = None,
    processing_status: str = None,
    discrepancy_reason: str = None,
    retry_count: int = None,
    invoice_base64_png: str = None,
) -> str:
  """Inserts or updates a loan record in Firestore (or fallback JSON)."""
  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()

  if package_completeness is False:
    processing_status = "PENDING_HUMAN_REVIEW"

  fields = {
      "inquiry_id": inquiry_id,
      "client_name": client_name,
      "tax_id": tax_id,
      "equipment_make": equipment_make,
      "equipment_model": equipment_model,
      "equipment_vin": equipment_vin,
      "invoice_amount": invoice_amount,
      "approved_limit": approved_limit,
      "package_completeness": package_completeness,
      "extraction_confidence": extraction_confidence,
      "validation_passed": validation_passed,
      "processing_status": processing_status,
      "discrepancy_reason": discrepancy_reason,
      "retry_count": retry_count,
      "invoice_base64_png": invoice_base64_png,
  }

  # Filter out None values
  update_data = {k: v for k, v in fields.items() if v is not None}

  # Always set last_updated
  now_str = datetime.now(timezone.utc).isoformat()

  global use_fallback
  if not use_fallback:
    try:
      doc_ref = db.collection("inquiries").document(inquiry_id)
      doc = doc_ref.get()
      if not doc.exists:
        # For a new document, ensure default values are set
        if "retry_count" not in update_data:
          update_data["retry_count"] = 0
        update_data["last_updated"] = datetime.now(timezone.utc)
        doc_ref.set(update_data)
      else:
        # Update existing document
        update_data["last_updated"] = datetime.now(timezone.utc)
        doc_ref.update(update_data)
      return (
          f"Successfully updated Firestore state for inquiry_id {inquiry_id}."
      )
    except Exception as e:
      logger.warning(
          f"Firestore write failed: {e}. Falling back to local JSON file."
      )
      use_fallback = True
      # Fall through to fallback logic below

  # Fallback logic
  if use_fallback:
    data = _read_fallback_db()
    if inquiry_id not in data:
      # New document fallback
      default_doc = {
          "inquiry_id": inquiry_id,
          "client_name": None,
          "tax_id": None,
          "equipment_make": None,
          "equipment_model": None,
          "equipment_vin": None,
          "invoice_amount": None,
          "approved_limit": None,
          "package_completeness": None,
          "extraction_confidence": None,
          "validation_passed": None,
          "processing_status": None,
          "discrepancy_reason": None,
          "retry_count": 0,
          "invoice_base64_png": None,
      }
      default_doc.update(update_data)
      default_doc["last_updated"] = now_str
      data[inquiry_id] = default_doc
    else:
      # Update existing document fallback
      data[inquiry_id].update(update_data)
      data[inquiry_id]["last_updated"] = now_str

    _write_fallback_db(data)
    return (
        "Successfully updated local fallback state for inquiry_id"
        f" {inquiry_id}."
    )


def db_read_transaction(inquiry_id: str) -> str:
  """Queries current transaction fields and processing status for a given inquiry_id."""
  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()
  global use_fallback
  if not use_fallback:
    try:
      doc_ref = db.collection("inquiries").document(inquiry_id)
      doc = doc_ref.get()
      if doc.exists:
        doc_dict = doc.to_dict()
        # Convert datetime objects to ISO string for JSON serialization compatibility
        if "last_updated" in doc_dict and isinstance(
            doc_dict["last_updated"], datetime
        ):
          doc_dict["last_updated"] = doc_dict["last_updated"].isoformat()
        return json.dumps(doc_dict)
      else:
        return json.dumps({"error": f"Inquiry ID {inquiry_id} not found."})
    except Exception as e:
      logger.warning(
          f"Firestore read failed: {e}. Falling back to local JSON file."
      )
      use_fallback = True
      # Fall through to fallback logic

  if use_fallback:
    data = _read_fallback_db()
    if inquiry_id in data:
      return json.dumps(data[inquiry_id])
    return json.dumps({"error": f"Inquiry ID {inquiry_id} not found."})


def db_get_latest_inquiry_id() -> str:
  """Queries the latest updated inquiry_id from the database."""
  global use_fallback
  if not use_fallback:
    try:
      inquiries_ref = db.collection("inquiries")
      # Query ordered by last_updated descending, limit 1
      query = inquiries_ref.order_by(
          "last_updated", direction=firestore.Query.DESCENDING
      ).limit(1)
      results = list(query.stream())
      if results:
        return results[0].id
      return None
    except Exception as e:
      logger.warning(
          f"Firestore get latest inquiry ID failed: {e}. Falling back to local"
          " JSON file."
      )
      use_fallback = True
      # Fall through to fallback logic

  if use_fallback:
    data = _read_fallback_db()
    if not data:
      return None
    # Sort by last_updated string descending
    sorted_inquiries = sorted(
        data.values(), key=lambda x: x.get("last_updated", ""), reverse=True
    )
    if sorted_inquiries:
      return sorted_inquiries[0].get("inquiry_id")
    return None


# Run initialization on import
init_db()
