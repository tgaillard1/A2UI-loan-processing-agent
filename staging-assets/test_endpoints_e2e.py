#!/usr/bin/env python3
"""
FSI Mock APIs - E2E Integration Tester Script
Validates FileNet stubs, Siebel underwriting, POST writebacks, and GCS PNG proxies.
Supports standard Bearer authentication and Gcloud service account impersonation.
"""

import argparse
import datetime
import json
import requests
import subprocess
import sys

def print_header(title):
    print("=" * 70)
    print(f"🚀 {title}")
    print("=" * 70)

def get_bearer_token(impersonate_sa=None):
    """Generates a fresh Google OIDC Identity Token using local gcloud credentials."""
    try:
        cmd = ["gcloud", "auth", "print-identity-token"]
        if impersonate_sa:
            cmd.append(f"--impersonate-service-account={impersonate_sa}")
            print(f"🔑 Generating OIDC token impersonating Service Account: {impersonate_sa}")
        else:
            print("🔑 Generating OIDC token using active gcloud developer credential...")
        
        token = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8").strip()
        return token
    except subprocess.CalledProcessError:
        print("❌ Error: Failed to retrieve OIDC token from gcloud.")
        print("   Please run 'gcloud auth login' or ensure your SA impersonation privileges are set.")
        sys.exit(1)

def run_test_step(name, method, url, headers, json_payload=None, stream=False):
    print(f"\n👉 Test: {name}")
    print(f"   Request: {method} {url}")
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, stream=stream)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=json_payload)
        else:
            raise ValueError(f"Unsupported method {method}")
            
        print(f"   Status:  {response.status_code}")
        
        if response.status_code in [200, 201]:
            print("   Result:  ✅ PASSED")
            if stream:
                # For image streaming, verify content type and size
                print(f"   Content: {response.headers.get('Content-Type')} ({len(response.content)} bytes)")
            else:
                try:
                    parsed = response.json()
                    print("   Response JSON:")
                    print(json.dumps(parsed, indent=4))
                except json.JSONDecodeError:
                    print(f"   Response Raw: {response.text[:200]}...")
            return True
        else:
            print("   Result:  ❌ FAILED")
            print(f"   Error:   {response.text}")
            return False
            
    except Exception as e:
        print("   Result:  💥 CRASHED")
        print(f"   Exception: {str(e)}")
        return False

def main():
    parser = argparse.ArgumentParser(description="E2E Test Runner for serverless FSI Mock APIs.")
    parser.add_argument("-url", "--base-url", required=True, help="Target base URL of the mock service (no trailing slash).")
    parser.add_argument("-sa", "--impersonate-sa", help="Optional service account email to impersonate for OIDC token generation.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    token = get_bearer_token(args.impersonate_sa)
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    all_passed = True

    # --- TEST STEP 1: FileNet Happy Path Ingestion (PKG-10001) ---
    print_header("TEST 1: FileNet Ingestion manifest (Happy Path)")
    success = run_test_step(
        name="Ingestion Manifest PKG-10001",
        method="GET",
        url=f"{base_url}/api/filenet/v1/packages/PKG-10001",
        headers=headers
    )
    if not success: all_passed = False

    # --- TEST STEP 2: FileNet Incomplete Package (PKG-10170) ---
    print_header("TEST 2: FileNet Ingestion manifest (Incomplete/Missing credit app)")
    success = run_test_step(
        name="Ingestion Manifest PKG-10170",
        method="GET",
        url=f"{base_url}/api/filenet/v1/packages/PKG-10170",
        headers=headers
    )
    if not success: all_passed = False

    # --- TEST STEP 3: Siebel Underwriting profile search ---
    print_header("TEST 3: Siebel CRM Profile Retrieval")
    success = run_test_step(
        name="CRM Underwriting Profile EIN 13-5614226",
        method="GET",
        url=f"{base_url}/api/siebel/v1/underwriting-profiles/13-5614226",
        headers=headers
    )
    if not success: all_passed = False

    # --- TEST STEP 4: Siebel Credit Status write-back ---
    print_header("TEST 4: CRM Audit Status Writeback")
    writeback_payload = {
        "inquiry_id": "PKG-10001",
        "validation_passed": True,
        "processing_status": "APPROVED",
        "extracted_metadata": {
            "applicant_name": "Pioneer Excavation",
            "extracted_tax_id": "13-5614226",
            "equipment_make": "GE Healthcare",
            "equipment_model": "Revolution CT",
            "invoice_amount": 191861.38,
            "extraction_confidence": 0.97
        },
        "hitl_routing_required": False,
        "discrepancy_reason": None,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }
    success = run_test_step(
        name="Audit Writeback POST PKG-10001",
        method="POST",
        url=f"{base_url}/api/siebel/v1/loans/PKG-10001/status",
        headers=headers,
        json_payload=writeback_payload
    )
    if not success: all_passed = False

    # --- TEST STEP 5: Dynamic image rasterization proxy ---
    print_header("TEST 5: On-the-fly PDF to PNG Rendering")
    success = run_test_step(
        name="Dynamic PNG Rasterization PKG-10001/invoice.png",
        method="GET",
        url=f"{base_url}/api/mocks/document_corpus/PKG-10001/invoice.png",
        headers=headers,
        stream=True
    )
    if not success: all_passed = False

    print_header("E2E RUN SUMMARY")
    if all_passed:
        print("🎉 All integrated API endpoints are fully operational & secured!")
    else:
        print("❌ Some testing steps failed. Check standard logging outputs above for details.")

if __name__ == "__main__":
    main()
