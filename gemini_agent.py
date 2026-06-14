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

from datetime import datetime, timezone
from functools import cached_property
import json
import os
import re
from typing import Any, Dict
import uuid
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentSkill, Part
from agent_executor import AdkAgentToA2AExecutor
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import Gemini, LlmResponse
from google.adk.tools import FunctionTool
from google.genai import Client, types
from metadata_extractor import get_document_base64_on_the_fly
from prompt_builder_v08 import SYSTEM_INSTRUCTION
from templates_a2ui import generate_hydrated_dashboard


async def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Any:
  """Native ADK After-LLM Interceptor to intercept hydration metadata and emit valid A2UI."""
  # Option 2: Out-of-Band (OOB) UI Generation via A2A Executor
  # We return the original llm_response untouched so that session history remains purely text,
  # and Vertex AI doesn't reject subsequent turns due to unsupported mime_types.
  # The A2A Executor will handle extracting the ---a2ui_JSON--- delimiter and
  # emitting the 'application/json+a2ui' payload specifically for the client UI.
  return llm_response


# Import local tools
from database import db_write_transaction
from filenet import filenet_get_package
from a2a_tools import a2a_create_task
from metadata_extractor import gemini_extract_metadata, query_document
from siebel import siebel_get_profile, siebel_post_status
from validation import deterministic_validate_transaction, approve_inquiry, reject_inquiry, escalate_inquiry


class EnterpriseGemini(Gemini):

  @cached_property
  def api_client(self) -> Client:
    # Fetch target project ID dynamically
    PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
    return Client(enterprise=True, project=PROJECT_ID, location="global")


class GeminiAgent(LlmAgent):
  name: str = "loan_compliance_supervisor"
  description: str = (
      "Equipment Finance Ingestion & Compliance Auditing Assistant"
  )

  def __init__(self, **kwargs):
    model_name = os.environ.get("MODEL", "gemini-3.5-flash")
    super().__init__(
        model=EnterpriseGemini(model=model_name),
        instruction=SYSTEM_INSTRUCTION,
        after_model_callback=after_model_callback,
        tools=[
            FunctionTool(db_write_transaction),
            FunctionTool(filenet_get_package),
            FunctionTool(a2a_create_task),
            FunctionTool(gemini_extract_metadata),
            FunctionTool(query_document),
            FunctionTool(siebel_get_profile),
            FunctionTool(siebel_post_status),
            FunctionTool(deterministic_validate_transaction),
            FunctionTool(approve_inquiry),
            FunctionTool(reject_inquiry),
            FunctionTool(escalate_inquiry),
        ],
        **kwargs,
    )

  def create_agent_card(self, agent_url: str) -> "AgentCard":
    return AgentCard(
        name="Loan Compliance Supervisor",
        description=self.description,
        url=agent_url or "http://localhost:8001",
        iconUrl="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAMAAACdt4HsAAAAElBMVEUaHSRChfTqQzX7vAU0qFMRESKcLwPGAAAAdElEQVR4nO3X0Q7AEAxAUab//82LxJKmNMXbLvexOG8LS0mVTdG86+eAiIjd6FX30oBv4Wl5B+06BYg2RXMisBoNKK36sRSTnp0ARJGBnS5wATLgXSwnAVldptGjgwzMRgBGb8WlwxBgB+n+WADACInmEOAFkaQSIXcNVnUAAAAASUVORK5CYII=",
        version="1.0.0",
        defaultInputModes=["text/plain"],
        defaultOutputModes=["text/plain"],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri="https://a2ui.org/a2a-extension/a2ui/v0.8",
                    description=(
                        "Supports dynamic A2UI v0.8 standard catalog layouts."
                    ),
                    params={
                        "supportedCatalogIds": [
                            "https://a2ui.org/specification/v0_8/standard_catalog_definition.json"
                        ]
                    },
                )
            ],
        ),
        skills=[
            AgentSkill(
                id="equipment_finance_originations",
                name="Equipment Finance Originations Skill",
                description=(
                    "Automates Cymbal Bank lease originations, metadata"
                    " extraction, limit checks, and compliance."
                ),
                tags=["finance", "underwriting", "lease", "compliance"],
                examples=[
                    "Let's ingest package PKG-10001 to verify originations"
                    " completeness."
                ],
            )
        ],
    )


class EquipmentFinanceExecutor(AdkAgentToA2AExecutor):

  def __init__(self, agent):
    self._gcs_bucket = os.environ.get(
        "GCS_BUCKET_NAME", "cpe-bustosjuan-experimental-fsi-mocks"
    )
    if "FIRESTORE_DATABASE_NAME" not in os.environ:
      os.environ["FIRESTORE_DATABASE_NAME"] = "equipment-finance-db"
    if "GCS_BUCKET_NAME" not in os.environ:
      os.environ["GCS_BUCKET_NAME"] = self._gcs_bucket
    super().__init__(agent=agent)

  async def _handle_direct_action(
      self,
      context: RequestContext,
      event_queue: EventQueue,
      user_action: dict,
  ) -> bool:
    action_name = (
        user_action.get("name")
        or user_action.get("actionName")
        or user_action.get("action_name")
        or user_action.get("action", {}).get("name")
    )
    action_context = user_action.get("context") or user_action.get(
        "action", {}
    ).get("context", [])

    inquiry_id = None
    if isinstance(action_context, dict):
      inquiry_id = action_context.get("inquiry_id")
    elif isinstance(action_context, list):
      for ctx_item in action_context:
        if isinstance(ctx_item, dict) and ctx_item.get("key") == "inquiry_id":
          val = ctx_item.get("value", {})
          inquiry_id = (
              val.get("literalString") if isinstance(val, dict) else str(val)
          )
          if inquiry_id:
            inquiry_id = str(inquiry_id).upper().strip()
          break

    if inquiry_id and action_name in [
        "approve_inquiry",
        "reject_inquiry",
        "escalate_inquiry",
    ]:
      print(
          f"[Subclass-Executor] Intercepted native A2UI action '{action_name}'"
          f" for {inquiry_id}."
      )
      try:
        from validation import approve_inquiry, reject_inquiry, escalate_inquiry

        tool_res_str = None
        if action_name == "approve_inquiry":
          tool_res_str = approve_inquiry(inquiry_id)
        elif action_name == "reject_inquiry":
          tool_res_str = reject_inquiry(inquiry_id)
        elif action_name == "escalate_inquiry":
          tool_res_str = escalate_inquiry(inquiry_id)
        else:
          raise ValueError(f"Unrecognized action: {action_name}")

        print(
            "[Subclass-Executor] Direct Python tool call successful:"
            f" {tool_res_str}"
        )

        from database import db_read_transaction

        db_record_str = db_read_transaction(inquiry_id)
        db_record = json.loads(db_record_str)

        client_name = (
            db_record.get("client_name")
            or db_record.get("applicant_name")
            or "Applicant"
        )
        tax_id = db_record.get("tax_id") or "N/A"
        inv_amt = db_record.get("invoice_amount") or 0.0
        app_lmt = db_record.get("approved_limit") or 0.0
        make_val = db_record.get("equipment_make") or "N/A"
        model_val = db_record.get("equipment_model") or "N/A"
        vin_val = db_record.get("equipment_vin") or "N/A"

        action_verb = (
            "APPROVED"
            if action_name == "approve_inquiry"
            else (
                "REJECTED" if action_name == "reject_inquiry" else "ESCALATED"
            )
        )
        val_passed = action_verb == "APPROVED"

        audit_explanation = (
            "Manual Override Confirmed: The originations compliance"
            f" application for **{client_name}** has been successfully updated"
            f" to **{action_verb}** status. CRM write-back completed."
        )

        overage = max(0.0, inv_amt - app_lmt)
        overage_str = (
            f"${overage:,.2f}" if overage > 0 else "$0.00 (Within Limit)"
        )
        inv_amt_str = f"${inv_amt:,.2f}"
        app_lmt_str = f"${app_lmt:,.2f}"

        from metadata_extractor import get_document_base64_on_the_fly

        inv_b64 = get_document_base64_on_the_fly(inquiry_id, "invoice")
        w9_b64 = get_document_base64_on_the_fly(inquiry_id, "w9")
        app_b64 = get_document_base64_on_the_fly(inquiry_id, "app")

        clean_inv_b64 = (
            str(inv_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if inv_b64
            else ""
        )
        clean_w9_b64 = (
            str(w9_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if w9_b64
            else ""
        )
        clean_app_b64 = (
            str(app_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if app_b64
            else ""
        )

        from templates_a2ui import generate_hydrated_dashboard

        final_card = generate_hydrated_dashboard(
            inquiry_id=inquiry_id,
            client_name=client_name,
            tax_id=tax_id,
            inv_amt_str=inv_amt_str,
            app_lmt_str=app_lmt_str,
            overage_str=overage_str,
            make_val=make_val,
            model_val=model_val,
            vin_val=vin_val,
            res_verb=action_verb,
            val_passed=val_passed,
            clean_inv_b64=clean_inv_b64,
            clean_w9_b64=clean_w9_b64,
            clean_app_b64=clean_app_b64,
            audit_explanation=audit_explanation,
        )

        for comp in final_card["surfaceUpdate"]["components"]:
          if comp["id"] == "action_row":
            comp["component"]["Row"]["children"]["explicitList"] = []
            break

        text_msg = (
            "User action triggered.\n\n**Manual Override Confirmed**: The"
            f" originations compliance application for **{client_name}** has"
            f" been successfully updated to **{action_verb}** status. CRM"
            " write-back completed."
        )
        await self._emit_direct_action_response(
            context=context,
            event_queue=event_queue,
            text_response=text_msg,
            card_payload=final_card,
        )
        return True
      except Exception as err:
        print(f"[Subclass-Executor] Direct Action exception: {err}")
        raise err
    return False

  async def _hydrate_a2ui(self, meta: dict, query: str) -> dict | None:
    if isinstance(meta, dict) and "a2ui_target_template" in meta:
      inquiry_id = meta.get("inquiry_id") or "PKG-DEFAULT"
      client_name = meta.get("client_name") or "Applicant"
      tax_id = meta.get("tax_id") or "N/A"
      inv_amt_str = meta.get("invoice_amount") or "$0.00"
      app_lmt_str = meta.get("approved_limit") or "$0.00"
      overage_str = meta.get("overage_amount") or "$0.00"
      make_val = meta.get("equipment_make") or "N/A"
      model_val = meta.get("equipment_model") or "N/A"
      vin_val = meta.get("equipment_vin") or "N/A"
      res_verb = meta.get("result") or "PASSED"
      val_passed = res_verb == "PASSED" or "pass" in res_verb.lower()
      audit_explanation = (
          meta.get("audit_explanation") or "Transaction processed E2E."
      )

      print(
          f"[Subclass-Executor] Engaging Post-LLM Hydrator for {inquiry_id}..."
      )

      from metadata_extractor import get_document_base64_on_the_fly

      inv_b64 = get_document_base64_on_the_fly(inquiry_id, "invoice")
      w9_b64 = get_document_base64_on_the_fly(inquiry_id, "w9")
      app_b64 = get_document_base64_on_the_fly(inquiry_id, "app")

      clean_inv_b64 = (
          str(inv_b64).replace("\n", "").replace("\r", "").replace(" ", "")
          if inv_b64
          else ""
      )
      clean_w9_b64 = (
          str(w9_b64).replace("\n", "").replace("\r", "").replace(" ", "")
          if w9_b64
          else ""
      )
      clean_app_b64 = (
          str(app_b64).replace("\n", "").replace("\r", "").replace(" ", "")
          if app_b64
          else ""
      )

      from templates_a2ui import generate_hydrated_dashboard

      final_card = generate_hydrated_dashboard(
          inquiry_id=inquiry_id,
          client_name=client_name,
          tax_id=tax_id,
          inv_amt_str=inv_amt_str,
          app_lmt_str=app_lmt_str,
          overage_str=overage_str,
          make_val=make_val,
          model_val=model_val,
          vin_val=vin_val,
          res_verb=res_verb,
          val_passed=val_passed,
          clean_inv_b64=clean_inv_b64,
          clean_w9_b64=clean_w9_b64,
          clean_app_b64=clean_app_b64,
          audit_explanation=audit_explanation,
      )

      from database import db_read_transaction

      try:
        db_rec_str = db_read_transaction(inquiry_id)
        db_rec = json.loads(db_rec_str)
        proc_status = db_rec.get("processing_status", "")
        if proc_status in [
            "APPROVED",
            "REJECTED",
            "ESCALATED",
            "OVERRIDE_APPROVED",
            "OVERRIDE_REJECTED",
            "PENDING_HUMAN_REVIEW",
        ]:
          for comp in final_card["surfaceUpdate"]["components"]:
            if comp["id"] == "action_row":
              comp["component"]["Row"]["children"]["explicitList"] = []
              break
      except Exception:
        pass

      return final_card
    return None

  async def _synthesize_fallback_ui(self, query: str) -> dict | None:
    query_inquiry_id = None
    if query:
      match_q = re.search(r"(PKG-\d+)", query, re.IGNORECASE)
      if match_q:
        query_inquiry_id = match_q.group(1).strip().upper()

    if query_inquiry_id:
      print(
          "[Subclass-Executor] Engaging Fallback Visual Synthesizer for"
          f" {query_inquiry_id}..."
      )
      try:
        from database import db_read_transaction

        db_record_str = db_read_transaction(query_inquiry_id)
        db_record = json.loads(db_record_str)

        if "error" not in db_record:
          tax_id = db_record.get("tax_id")
          client_name = db_record.get("client_name") or db_record.get(
              "applicant_name"
          )
          app_lmt = db_record.get("approved_limit") or 0.0

          if tax_id and tax_id != "N/A":
            try:
              from siebel import siebel_get_profile

              crm_profile_str = siebel_get_profile(tax_id)
              crm_profile = json.loads(crm_profile_str)
              if "error" not in crm_profile:
                client_name = crm_profile.get("client_name") or client_name
                app_lmt = crm_profile.get("approved_limit") or app_lmt
            except Exception as crm_err:
              print(f"[Subclass-Executor] CRM read warning: {crm_err}")

          client_name = client_name or "Applicant"
          inv_amt = db_record.get("invoice_amount") or 0.0
          make_val = db_record.get("equipment_make") or "N/A"
          model_val = db_record.get("equipment_model") or "N/A"
          vin_val = db_record.get("equipment_vin") or "N/A"
          val_passed = db_record.get("validation_passed", False)

          res_verb = "PASSED" if val_passed else "FAILED"
          overage = max(0.0, inv_amt - app_lmt)
          overage_str = (
              f"${overage:,.2f}" if overage > 0 else "$0.00 (Within Limit)"
          )
          inv_amt_str = f"${inv_amt:,.2f}"
          app_lmt_str = f"${app_lmt:,.2f}"
          audit_explanation = (
              db_record.get("discrepancy_reason")
              or "Transaction processed successfully."
          )

          from metadata_extractor import get_document_base64_on_the_fly

          inv_b64 = db_record.get(
              "invoice_base64_png"
          ) or get_document_base64_on_the_fly(query_inquiry_id, "invoice")
          w9_b64 = get_document_base64_on_the_fly(query_inquiry_id, "w9")
          app_b64 = get_document_base64_on_the_fly(query_inquiry_id, "app")

          clean_inv_b64 = (
              str(inv_b64).replace("\n", "").replace("\r", "").replace(" ", "")
              if inv_b64
              else ""
          )
          clean_w9_b64 = (
              str(w9_b64).replace("\n", "").replace("\r", "").replace(" ", "")
              if w9_b64
              else ""
          )
          clean_app_b64 = (
              str(app_b64).replace("\n", "").replace("\r", "").replace(" ", "")
              if app_b64
              else ""
          )

          from templates_a2ui import generate_hydrated_dashboard

          synth_card = generate_hydrated_dashboard(
              inquiry_id=query_inquiry_id,
              client_name=client_name,
              tax_id=tax_id,
              inv_amt_str=inv_amt_str,
              app_lmt_str=app_lmt_str,
              overage_str=overage_str,
              make_val=make_val,
              model_val=model_val,
              vin_val=vin_val,
              res_verb=res_verb,
              val_passed=val_passed,
              clean_inv_b64=clean_inv_b64,
              clean_w9_b64=clean_w9_b64,
              clean_app_b64=clean_app_b64,
              audit_explanation=audit_explanation,
          )

          proc_status = db_record.get("processing_status", "")
          if proc_status in [
              "APPROVED",
              "REJECTED",
              "ESCALATED",
              "OVERRIDE_APPROVED",
              "OVERRIDE_REJECTED",
              "PENDING_HUMAN_REVIEW",
          ]:
            for comp in synth_card["surfaceUpdate"]["components"]:
              if comp["id"] == "action_row":
                comp["component"]["Row"]["children"]["explicitList"] = []
                break

          return synth_card
      except Exception as err:
        print(f"[Subclass-Executor] Fallback synthesis failed: {err}")
    return None

  def _get_extra_parts(self, full_text: str) -> list[Part]:
    extra_parts = []
    from a2a.types import FilePart, FileWithUri

    if "caterpillar_invoice_scanned_markup.pdf" in full_text:
      extra_parts.append(
          Part(
              root=FilePart(
                  file=FileWithUri(
                      uri=f"gs://{self._gcs_bucket}/caterpillar_invoice_scanned_markup.pdf",
                      mime_type="application/pdf",
                      name="caterpillar_invoice_scanned_markup.pdf",
                  )
              )
          )
      )
    elif "overage_invoice.pdf" in full_text:
      extra_parts.append(
          Part(
              root=FilePart(
                  file=FileWithUri(
                      uri=f"gs://{self._gcs_bucket}/overage_invoice.pdf",
                      mime_type="application/pdf",
                      name="overage_invoice.pdf",
                  )
              )
          )
      )
    return extra_parts

  async def _handle_exception_fallback(
      self,
      context: RequestContext,
      event_queue: EventQueue,
      error: Exception,
      user_action: dict,
  ) -> bool:
    is_iteration_fault = "StopAsyncIteration" in str(error) or isinstance(
        error, StopAsyncIteration
    )
    user_action_name = user_action.get("action", {}).get(
        "name"
    ) or user_action.get("name")

    inquiry_id = None
    context_list = user_action.get("context") or user_action.get(
        "action", {}
    ).get("context", [])
    if isinstance(context_list, dict):
      inquiry_id = context_list.get("inquiry_id")
    elif isinstance(context_list, list):
      for ctx_item in context_list:
        if ctx_item.get("key") == "inquiry_id":
          val = ctx_item.get("value", {})
          inquiry_id = (
              val.get("literalString") if isinstance(val, dict) else str(val)
          )
          break

    if is_iteration_fault and inquiry_id and user_action_name:
      print(
          "[Subclass-Executor] StopAsyncIteration fault fallback active for"
          f" action '{user_action_name}' and ID {inquiry_id}..."
      )
      try:
        from database import db_read_transaction

        db_record_str = db_read_transaction(inquiry_id)
        db_record = json.loads(db_record_str)

        client_name = (
            db_record.get("client_name")
            or db_record.get("applicant_name")
            or "Applicant"
        )
        action_verb = (
            "APPROVED"
            if user_action_name == "approve_inquiry"
            else (
                "REJECTED"
                if user_action_name == "reject_inquiry"
                else "ESCALATED"
            )
        )
        tax_id = db_record.get("tax_id") or "N/A"
        inv_amt = db_record.get("invoice_amount") or 0.0
        app_lmt = db_record.get("approved_limit") or 0.0
        make_val = db_record.get("equipment_make") or "N/A"
        model_val = db_record.get("equipment_model") or "N/A"
        vin_val = db_record.get("equipment_vin") or "N/A"
        val_passed = action_verb == "APPROVED"

        audit_explanation = (
            "Manual Override Confirmed: The originations compliance"
            f" application for **{client_name}** has been successfully updated"
            f" to **{action_verb}** status. CRM write-back completed."
        )

        overage = max(0.0, inv_amt - app_lmt)
        overage_str = (
            f"${overage:,.2f}" if overage > 0 else "$0.00 (Within Limit)"
        )
        inv_amt_str = f"${inv_amt:,.2f}"
        app_lmt_str = f"${app_lmt:,.2f}"

        from metadata_extractor import get_document_base64_on_the_fly

        inv_b64 = get_document_base64_on_the_fly(inquiry_id, "invoice")
        w9_b64 = get_document_base64_on_the_fly(inquiry_id, "w9")
        app_b64 = get_document_base64_on_the_fly(inquiry_id, "app")

        clean_inv_b64 = (
            str(inv_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if inv_b64
            else ""
        )
        clean_w9_b64 = (
            str(w9_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if w9_b64
            else ""
        )
        clean_app_b64 = (
            str(app_b64).replace("\n", "").replace("\r", "").replace(" ", "")
            if app_b64
            else ""
        )

        from templates_a2ui import generate_hydrated_dashboard

        fallback_card = generate_hydrated_dashboard(
            inquiry_id=inquiry_id,
            client_name=client_name,
            tax_id=tax_id,
            inv_amt_str=inv_amt_str,
            app_lmt_str=app_lmt_str,
            overage_str=overage_str,
            make_val=make_val,
            model_val=model_val,
            vin_val=vin_val,
            res_verb=action_verb,
            val_passed=val_passed,
            clean_inv_b64=clean_inv_b64,
            clean_w9_b64=clean_w9_b64,
            clean_app_b64=clean_app_b64,
            audit_explanation=audit_explanation,
        )

        for comp in fallback_card["surfaceUpdate"]["components"]:
          if comp["id"] == "action_row":
            comp["component"]["Row"]["children"]["explicitList"] = []
            break

        text_msg = (
            "User action triggered.\n\n**Manual Override Confirmed**: The"
            f" originations compliance application for **{client_name}** has"
            f" been successfully updated to **{action_verb}** status. CRM"
            " write-back completed."
        )
        await self._emit_direct_action_response(
            context=context,
            event_queue=event_queue,
            text_response=text_msg,
            card_payload=fallback_card,
        )
        return True
      except Exception as exc_err:
        print(f"[Subclass-Executor] Failed building fallback card: {exc_err}")
    return False
