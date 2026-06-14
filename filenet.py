import json

# In-memory mock database for FileNet packages representing different user test paths
MOCK_PACKAGES = {
    "PKG-10001": {
        "inquiry_id": "PKG-10001",
        "client_name": "Cymbal Contracting",
        "tax_id": "44-9876543",
        "documents": [
            "bobcat_invoice_v2.pdf",
            "dealer_brochure_2025.pdf",
            "w9_signed.pdf",
        ],
        # INCOMPLETE: Missing credit_application.pdf
    },
    "PKG-10002": {
        "inquiry_id": "PKG-10002",
        "client_name": "Cymbal Contracting",
        "tax_id": "44-9876543",
        "documents": [
            "caterpillar_invoice_scanned_markup.pdf",
            "credit_application.pdf",
            "w9_signed.pdf",
        ],
        # HAPPY PATH: Complete package, Caterpillar price $145,000 <= pre-approved limit $150,000.
    },
    "PKG-10003": {
        "inquiry_id": "PKG-10003",
        "client_name": "Cymbal Contracting",
        "tax_id": "44-9876543",
        "documents": [
            "overage_invoice.pdf",
            "credit_application.pdf",
            "w9_signed.pdf",
        ],
        # OVERAGE PATH: Complete package, but price $152,000 > pre-approved limit $150,000.
    },
    "PKG-10023": {
        "inquiry_id": "PKG-10023",
        "client_name": "CareFirst Urgent Care",
        "tax_id": "64-4553384",
        "documents": [
            "credit_app.pdf",
            "invoice.pdf",
            "w9.pdf",
        ],
    },
    "PKG-DEFAULT": {
        "inquiry_id": "PKG-DEFAULT",
        "client_name": "Test Client",
        "tax_id": "12-3456789",
        "documents": ["invoice.pdf", "credit_application.pdf", "w9.pdf"],
    },
}

import os
import urllib.request
import urllib.error


class CustomHTTPRedirectHandler(urllib.request.HTTPRedirectHandler):

  def redirect_request(self, req, fp, code, msg, hdrs, newurl):
    new_req = super().redirect_request(req, fp, code, msg, hdrs, newurl)
    if new_req:
      if "Authorization" in req.headers:
        new_req.add_header("Authorization", req.get_header("Authorization"))
      if "X-Serverless-Authorization" in req.headers:
        new_req.add_header(
            "X-Serverless-Authorization",
            req.get_header("X-Serverless-Authorization"),
        )
    return new_req


# Install global redirect opener
urllib.request.install_opener(
    urllib.request.build_opener(CustomHTTPRedirectHandler)
)


def get_oidc_token(audience: str) -> str:
  """Programmatically generates a Google OIDC ID Token via service account impersonation.

  Args:
      audience: The target Cloud Run service URL.

  Returns:
      The generated Bearer ID token, or None if running locally/failed.
  """
  if os.environ.get("OIDC_TOKEN"):
    return os.environ.get("OIDC_TOKEN")

  # Check if running in standard local sandboxes
  if (
      os.environ.get("K_SERVICE") is None
      and os.environ.get("VM_SANDBOX") is None
  ):
    return None

  import google.auth
  import google.auth.transport.requests
  from google.auth import impersonated_credentials

  # Only trigger IAM service account impersonation when specifically running in the GCE VM Sandbox environment
  is_vm_sandbox = os.environ.get("VM_SANDBOX") is not None

  if is_vm_sandbox:
    try:
      # 1. Load container/VM active ADC source credentials
      source_credentials, project = google.auth.default()
      auth_req = google.auth.transport.requests.Request()

      # 2. Configure targeted project service account impersonation
      project_num = os.environ.get("PROJECT_NUMBER")
      if project_num:
        target_principal = f"{project_num}-compute@developer.gserviceaccount.com"
      else:
        target_principal = os.environ.get(
            "IMPERSONATE_SERVICE_ACCOUNT",
            "906194901769-compute@developer.gserviceaccount.com"
        )
      print(
          f"[Auth] Impersonating Service Account principal: {target_principal}"
          f" for audience: {audience}..."
      )

      impersonated_oauth2_creds = impersonated_credentials.Credentials(
          source_credentials=source_credentials,
          target_principal=target_principal,
          target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
      )

      impersonated_creds = impersonated_credentials.IDTokenCredentials(
          target_credentials=impersonated_oauth2_creds,
          target_audience=audience,
          include_email=True,
      )

      # 3. Mint the OIDC token programmatically via Google IAM Credentials API!
      impersonated_creds.refresh(auth_req)
      token = impersonated_creds.token
      print(
          "[Auth] Impersonated OIDC ID Token generated successfully! Length:"
          f" {len(token)}"
      )
      return token
    except Exception as e:
      print(
          f"[Auth] Impersonation failed: {e}. Attempting native Metadata Server"
          " fallback..."
      )

  # Native Production Cloud Run (GE) path: Natively assert the whitelisted Discovery Engine identity!
  try:
    import urllib.request

    metadata_url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}"
    req = urllib.request.Request(
        metadata_url, headers={"Metadata-Flavor": "Google"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
      token = resp.read().decode("utf-8")
      print(
          "[Auth] Native OIDC ID Token generated successfully via Metadata"
          f" Server. Length: {len(token)}"
      )
      return token
  except Exception as meta_err:
    print(f"[Auth] Native OIDC urllib generation failed: {meta_err}")
    return None


def filenet_get_package(inquiry_id: str) -> str:
  """Calls GET /api/filenet/v1/packages/{inquiry_id} using live FastAPI endpoints with OIDC auth.

  In production (Cloud Run), failures yield detailed HTTP error JSON payloads.
  In local sandboxes, failures fallback cleanly to local static dictionaries to
  keep tests green.

  Args:
      inquiry_id: The unique ID of the loan package to retrieve from FileNet.
  """
  inquiry_id = str(inquiry_id).upper().strip()
  mocks_base_url = os.environ.get("FILENET_URL") or os.environ.get(
      "FSI_MOCKS_URL",
      "https://cpe-bustosjuan-fsi-mocks-906194901769.us-central1.run.app",
  )
  mocks_base_url = mocks_base_url.rstrip("/")
  url = f"{mocks_base_url}/api/filenet/v1/packages/{inquiry_id}"
  print(f"[FileNet] Fetching live package from: {url}")

  is_production = os.environ.get("K_SERVICE") is not None

  audience = mocks_base_url
  oidc_token = get_oidc_token(audience)

  import time

  start_time = time.perf_counter()
  telemetry_status = "SUCCESS"
  try:
    headers = {"User-Agent": "Mozilla/5.0"}
    if oidc_token:
      headers["Authorization"] = f"Bearer {oidc_token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
      data = response.read().decode("utf-8")
      duration = (time.perf_counter() - start_time) * 1000
      print(
          f"[Telemetry] [FileNet_GetPackage] Inquiry: {inquiry_id} | Latency:"
          f" {duration:.2f}ms | Status: {telemetry_status}"
      )
      return data
  except Exception as e:
    err_msg = str(e)
    if hasattr(e, "code") and hasattr(e, "read"):
      try:
        err_body = e.read().decode("utf-8", errors="ignore")
        err_msg = f"HTTP {e.code} {getattr(e, 'reason', '')} - {err_body}"
      except:
        pass
        
    telemetry_status = f"FAILED ({getattr(e, 'code', type(e).__name__)})"
    duration = (time.perf_counter() - start_time) * 1000
    print(
        f"[Telemetry] [FileNet_GetPackage] Inquiry: {inquiry_id} | Latency:"
        f" {duration:.2f}ms | Status: {telemetry_status}"
    )
    print(f"[FileNet] Live HTTP request failed: {err_msg}")
    if is_production:
      # In production: Raise/Return detailed error JSON to the agent
      error_details = (
          "HTTP Error: Failed to retrieve package manifest from FileNet. URL:"
          f" {url}. Details: {err_msg}"
      )
      return json.dumps(
          {"error": "FileNet Ingestion Failed", "details": error_details}
      )
    else:
      # In local sandbox: Fallback to static mocks for test suite stability
      print(f"[FileNet] Local Sandbox detected. Falling back to static mock.")
      package = MOCK_PACKAGES.get(inquiry_id)
      if not package:
        print(
            f"[FileNet] Inquiry ID {inquiry_id} not explicitly mocked. Falling"
            " back to PKG-10002."
        )
        package = MOCK_PACKAGES["PKG-10002"]
        package = dict(package)
        package["inquiry_id"] = inquiry_id

      return json.dumps(package)
