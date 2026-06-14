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

import asyncio
from datetime import datetime, timezone
import json
import os
import re
import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.types import (
    Artifact,
    DataPart,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from google.adk.artifacts.gcs_artifact_service import GcsArtifactService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.integrations.firestore.firestore_session_service import FirestoreSessionService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai.types import Content
from google.cloud import firestore as firestore_async


def make_firestore_safe(obj):
  """Recursively sanitizes any object to be 100% Firestore-serializable and pure JSON-safe."""
  if isinstance(obj, dict):
    return {k: make_firestore_safe(v) for k, v in obj.items()}
  elif isinstance(obj, list):
    return [make_firestore_safe(x) for x in obj]
  elif isinstance(obj, (str, int, float, bool)) or obj is None:
    return obj
  else:
    try:
      json.dumps(obj)
      return obj
    except Exception:
      return str(obj)


def get_part_mime_type(pt):
  """Recursively scans any part object or dict to extract its MIME type."""
  if not pt:
    return ""

  def extract_mime(obj):
    if not obj:
      return ""
    if isinstance(obj, dict):
      return str(obj.get("mime_type") or obj.get("mimeType") or "")
    return str(getattr(obj, "mime_type", None) or getattr(obj, "mimeType", None) or "")

  if isinstance(pt, dict):
    inline = pt.get("inline_data") or pt.get("inlineData")
    if inline:
      return extract_mime(inline)
    root = pt.get("root")
    if isinstance(root, dict):
      meta = root.get("metadata") or {}
      return extract_mime(meta)
    meta = pt.get("metadata") or {}
    return extract_mime(meta)

  inline = getattr(pt, "inline_data", None) or getattr(pt, "inlineData", None)
  if inline:
    return extract_mime(inline)

  root = getattr(pt, "root", None)
  if root:
    meta = getattr(root, "metadata", None) or {}
    return extract_mime(meta)

  meta = getattr(pt, "metadata", None) or {}
  return extract_mime(meta)


class TaskResultAggregator:
  """Aggregates the task status updates and provides the final task state."""

  def __init__(self):
    self._task_state = TaskState.working
    self._task_status_message = None
    self._full_text = ""

  def process_event(self, event: Event):
    """Process an event from the agent run and detect signals about the task status."""
    event_parts = []
    if hasattr(event, "parts") and event.parts:
      event_parts = event.parts
    elif hasattr(event, "content") and event.content:
      if hasattr(event.content, "parts") and event.content.parts:
        event_parts = event.content.parts
      elif hasattr(event.content, "text") and event.content.text:
        pass

    for part in event_parts:
      if part.text:
        self._full_text += part.text

    if isinstance(event, TaskStatusUpdateEvent):
      if event.status.state == TaskState.failed:
        self._task_state = TaskState.failed
        self._task_status_message = event.status.message
      elif (
          event.status.state == TaskState.auth_required
          and self._task_state != TaskState.failed
      ):
        self._task_state = TaskState.auth_required
        self._task_status_message = event.status.message
      elif (
          event.status.state == TaskState.input_required
          and self._task_state
          not in (TaskState.failed, TaskState.auth_required)
      ):
        self._task_state = TaskState.input_required
        self._task_status_message = event.status.message
      elif self._task_state == TaskState.working:
        self._task_status_message = event.status.message

  @property
  def task_state(self) -> TaskState:
    return self._task_state

  @property
  def task_status_message(self) -> Message | None:
    return self._task_status_message

  @property
  def full_text(self) -> str:
    return self._full_text


class AdkAgentToA2AExecutor(AgentExecutor):

  def __init__(self, agent, session_service=None, artifact_service=None):
    self._agent = agent

    if session_service is not None and artifact_service is not None:
      session_svc = session_service
      artifact_svc = artifact_service
      print(
          "[Executor] Initialized using custom injected Session and Artifact"
          " services."
      )
    else:
      use_fallback = (
          os.environ.get("USE_LOCAL_FALLBACK", "false").lower() == "true"
      )
      if not use_fallback:
        try:
          project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
          db_name = os.environ.get("FIRESTORE_DATABASE_NAME")
          bucket_name = os.environ.get("GCS_BUCKET_NAME")

          if db_name and bucket_name:
            async_client = firestore_async.AsyncClient(
                project=project_id, database=db_name
            )
            session_svc = FirestoreSessionService(client=async_client)
            artifact_svc = GcsArtifactService(bucket_name=bucket_name)
            print(
                "[Executor] Initialized Production Cloud Persistence for DB"
                f" '{db_name}' and Bucket '{bucket_name}'."
            )
          else:
            session_svc = InMemorySessionService()
            artifact_svc = InMemoryArtifactService()
            print(
                "[Executor] Missing environment variables"
                " (FIRESTORE_DATABASE_NAME, GCS_BUCKET_NAME). Initialized Local"
                " Fallback."
            )
        except Exception as init_err:
          print(
              f"[Executor] Production setup failed: {init_err}. Defaulting to"
              " Local Fallback."
          )
          session_svc = InMemorySessionService()
          artifact_svc = InMemoryArtifactService()
      else:
        session_svc = InMemorySessionService()
        artifact_svc = InMemoryArtifactService()
        print(
            "[Executor] Initialized Local Fallback (USE_LOCAL_FALLBACK=true)."
        )

    self._runner = Runner(
        app_name=self._agent.name,
        agent=self._agent,
        session_service=session_svc,
        artifact_service=artifact_svc,
        memory_service=InMemoryMemoryService(),
    )
    self._user_id = "remote_agent"

  # --- Subclass Hooks ---

  async def _handle_direct_action(
      self,
      context: RequestContext,
      event_queue: EventQueue,
      user_action: dict,
  ) -> bool:
    """Override in agent subclass to intercept manual manual override clicks.

    Returns True if intercepted and executed successfully, False to let request
    flow to the model.
    """
    return False

  def _get_extra_parts(self, full_text: str) -> list[Part]:
    """Override in agent subclass to append auxiliary files/parts (e.g.

    PDFs) based on the final response text.
    """
    return []

  async def _hydrate_a2ui(self, meta: dict, query: str) -> dict | None:
    """Override in agent subclass to generate a complete, hydrated A2UI card from post-LLM delimiter metadata."""
    return None

  async def _synthesize_fallback_ui(self, query: str) -> dict | None:
    """Override in agent subclass to construct an A2UI card programmatically if the model fails to output one."""
    return None

  async def _handle_exception_fallback(
      self,
      context: RequestContext,
      event_queue: EventQueue,
      error: Exception,
      user_action: dict,
  ) -> bool:
    """Override in agent subclass to render custom error/termination visual components when exception is raised."""
    return False

  # --- Common Generic Helpers ---

  async def _emit_direct_action_response(
      self,
      context: RequestContext,
      event_queue: EventQueue,
      text_response: str,
      card_payload: dict,
  ) -> None:
    """Convenience helper to cleanly emit direct action responses with Firestore OCC Retry Gate built-in."""
    max_occ_retries = 3
    for attempt in range(max_occ_retries):
      try:
        task = context.current_task
        if not task and context.message:
          task = new_task(context.message)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                status=TaskStatus(
                    state=TaskState.submitted,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
                context_id=context.context_id,
                final=False,
            )
        )

        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task.id,
                context_id=context.context_id,
                last_chunk=True,
                artifact=Artifact(
                    artifact_id=str(uuid.uuid4()),
                    name="response",
                    parts=[
                        Part(root=TextPart(text=text_response)),
                        Part(
                            root=DataPart(
                                data=card_payload,
                                metadata={"mimeType": "application/json+a2ui"},
                            )
                        ),
                    ],
                ),
            )
        )

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task.id,
                status=TaskStatus(
                    state=TaskState.completed,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    message=None,
                ),
                context_id=context.context_id,
                final=True,
            )
        )
        print(
            "[Executor-Direct-Action] Direct UI action emitted successfully on"
            f" attempt {attempt+1}. Exiting execution."
        )
        return
      except Exception as occ_err:
        if "modified in storage" in str(
            occ_err
        ) or "session has been modified" in str(occ_err):
          print(
              "[Executor-OCC-Gate] Firestore concurrency lock on attempt"
              f" {attempt+1}/{max_occ_retries}. Backing off and retrying..."
          )
          await asyncio.sleep(2.5)
          try:
            if hasattr(context, "reload"):
              await context.reload()
          except Exception:
            pass
        else:
          raise occ_err

  def _extract_user_action(
      self, context: RequestContext, body: bytes = None
  ) -> dict | None:
    """Parse user action dictionary generically from context DataPart parts or raw Request body."""
    user_action = None
    if context.message and context.message.parts:
      for part in context.message.parts:
        if isinstance(part.root, DataPart):
          data = part.root.data
          if isinstance(data, dict):
            if "userAction" in data:
              return data["userAction"]
            elif "action" in data:
              return data["action"]
            elif "actionName" in data:
              return data
          elif isinstance(data, str):
            try:
              parsed = json.loads(data)
              if "userAction" in parsed:
                return parsed["userAction"]
              elif "action" in parsed:
                return parsed["action"]
              elif "actionName" in parsed:
                return parsed
            except Exception:
              pass
    if body:
      try:
        body_str = (
            body.decode("utf-8") if isinstance(body, bytes) else str(body)
        )
        start_idx = body_str.find("{")
        end_idx = body_str.rfind("}")
        if start_idx != -1 and end_idx != -1:
          json_str = body_str[start_idx : end_idx + 1]
          parsed_body = json.loads(json_str)
          if "userAction" in parsed_body:
            return parsed_body["userAction"]
          elif "action" in parsed_body:
            return parsed_body["action"]
          elif "actionName" in parsed_body:
            return parsed_body
      except Exception as err:
        print(
            "[Executor] Warning: Extract action from request body failed:"
            f" {err}"
        )
    return None

  # --- Main A2A Protocol Execution Loop ---

  async def execute(
      self,
      context: RequestContext,
      event_queue: EventQueue,
  ) -> None:
    body = None
    try:
      if hasattr(context, "request") and context.request:
        body = await context.request.body()
    except Exception:
      pass

    user_action = self._extract_user_action(context, body)
    if user_action:
      print(f"[Executor] Extracted user action: {user_action}")
      try:
        action_handled = await self._handle_direct_action(
            context, event_queue, user_action
        )
        if action_handled:
          print(
              "[Executor] Direct action handled natively by subclass hooks."
              " Terminating loop."
          )
          return
      except Exception as action_err:
        print(f"[Executor] Error executing direct action hook: {action_err}")

    query = context.get_user_input()
    print(f"[Executor] Execute run triggered. Input text query: '{query}'")
    task = context.current_task

    if not task:
      if not context.message:
        return
      task = new_task(context.message)
      await event_queue.enqueue_event(
          TaskStatusUpdateEvent(
              task_id=task.id,
              status=TaskStatus(
                  state=TaskState.submitted,
                  message=context.message,
                  timestamp=datetime.now(timezone.utc).isoformat(),
              ),
              context_id=context.context_id,
              final=False,
          )
      )

    session_id = task.context_id
    session = await self._runner.session_service.get_session(
        app_name=self._agent.name,
        user_id=self._user_id,
        session_id=session_id,
    )
    if session is None:
      session = await self._runner.session_service.create_session(
          app_name=self._agent.name,
          user_id=self._user_id,
          state={},
          session_id=session_id,
      )

    await event_queue.enqueue_event(
        TaskStatusUpdateEvent(
            task_id=task.id,
            status=TaskStatus(
                state=TaskState.working,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            context_id=task.context_id,
            final=False,
        )
    )

    content = Content(role="user", parts=[{"text": query}])
    response_artifact_id = str(uuid.uuid4())
    task_result_aggregator = TaskResultAggregator()
    is_json_streaming = False

    try:
      async for event in self._runner.run_async(
          user_id=self._user_id, session_id=session.id, new_message=content
      ):
        task_result_aggregator.process_event(event)

        event_parts = []
        if hasattr(event, "parts") and event.parts:
          event_parts = event.parts
        elif hasattr(event, "content") and event.content:
          if hasattr(event.content, "parts") and event.content.parts:
            event_parts = event.content.parts
          elif hasattr(event.content, "text") and event.content.text:
            pass

        for part in event_parts:
          if part.function_call:
            tool_call = part.function_call
            tool_text = (
                f"A2A: Calling {tool_call.name} with args: {tool_call.args}"
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task.id,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=Message(
                            message_id=str(uuid.uuid4()),
                            role=Role.agent,
                            parts=[TextPart(text=tool_text)],
                        ),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ),
                    context_id=task.context_id,
                    final=False,
                )
            )

          if part.text:
            text_chunk = part.text
            if "---a2ui_JSON---" in text_chunk:
              is_json_streaming = True
              text_chunk = text_chunk.split("---a2ui_JSON---")[0]

            if not is_json_streaming and text_chunk:
              await event_queue.enqueue_event(
                  TaskArtifactUpdateEvent(
                      task_id=task.id,
                      context_id=task.context_id,
                      artifact=Artifact(
                          artifact_id=response_artifact_id,
                          name="response",
                          parts=[Part(root=TextPart(text=text_chunk))],
                      ),
                      last_chunk=False,
                  )
              )

      full_text = task_result_aggregator.full_text
      cleaned_text = full_text

      # Gather any auxiliary visual files
      extra_parts = self._get_extra_parts(full_text)

      # Gather A2UI card payloads
      if "---a2ui_JSON---" in full_text:
        parts_split = full_text.split("---a2ui_JSON---")
        cleaned_text = parts_split[0].strip()
        a2ui_block = parts_split[1].strip()
        a2ui_block = (
            a2ui_block.replace("```json", "")
            .replace("```a2ui", "")
            .replace("```", "")
            .strip()
        )

        try:
          meta = json.loads(a2ui_block)
          card_payload = await self._hydrate_a2ui(meta, query)
          if card_payload:
            extra_parts.append(
                Part(
                    root=DataPart(
                        data=card_payload,
                        metadata={"mimeType": "application/json+a2ui"},
                    )
                )
            )
        except Exception as e:
          print(
              f"[Executor] Parsing A2UI delimiter failed: {e}. Invoking"
              " fallback dashboard synthesis hooks..."
          )
          card_payload = await self._synthesize_fallback_ui(query)
          if card_payload:
            extra_parts.append(
                Part(
                    root=DataPart(
                        data=card_payload,
                        metadata={"mimeType": "application/json+a2ui"},
                    )
                )
            )
      else:
        print(
            "[Executor] Delimiter missing from response stream. Engaging"
            " fallback UI synthesis..."
        )
        card_payload = await self._synthesize_fallback_ui(query)
        if card_payload:
          extra_parts.append(
              Part(
                  root=DataPart(
                      data=card_payload,
                      metadata={"mimeType": "application/json+a2ui"},
                  )
              )
          )

      response_parts = [Part(root=TextPart(text=""))] + extra_parts
      try:
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task.id,
                context_id=task.context_id,
                last_chunk=True,
                artifact=Artifact(
                    artifact_id=response_artifact_id,
                    name="response",
                    parts=response_parts,
                ),
            )
        )
      except Exception as final_emit_err:
        print(f"[Executor] Final event enqueue warning: {final_emit_err}")

      await event_queue.enqueue_event(
          TaskStatusUpdateEvent(
              task_id=task.id,
              status=TaskStatus(
                  state=TaskState.completed,
                  timestamp=datetime.now(timezone.utc).isoformat(),
                  message=None,
              ),
              context_id=task.context_id,
              final=True,
          )
      )

    except Exception as e:
      if user_action:
        try:
          handled = await self._handle_exception_fallback(
              context, event_queue, e, user_action
          )
          if handled:
            print(
                "[Executor] Exception fallback visual card emitted"
                " successfully."
            )
            return
        except Exception as fallback_err:
          print(f"[Executor] Visual fallback exception error: {fallback_err}")

      await event_queue.enqueue_event(
          TaskStatusUpdateEvent(
              task_id=task.id,
              status=TaskStatus(
                  state=TaskState.failed,
                  timestamp=datetime.now(timezone.utc).isoformat(),
                  message=Message(
                      message_id=str(uuid.uuid4()),
                      role=Role.agent,
                      parts=[TextPart(text=f"Task failed with error: {e}")],
                  ),
              ),
              context_id=task.context_id,
              final=True,
          )
      )

  async def cancel(
      self, context: RequestContext, event_queue: EventQueue
  ) -> None:
    raise ServerError(error=UnsupportedOperationError())
