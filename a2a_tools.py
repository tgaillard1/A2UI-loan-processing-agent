from datetime import datetime, timezone
import json
import uuid


def a2a_create_task(
    inquiry_id: str,
    task_type: str,
    assigned_to: str = "underwriter",
    description: str = None,
) -> str:
  """Creates a task notification inside Gemini Enterprise to alert underwriters when a human decision is required.

  Args:
      inquiry_id: The associated inquiry/package ID.
      task_type: The type of review required (e.g., "INCOMPLETE_PACKAGE",
        "VALIDATION_FAILED").
      assigned_to: The role or email assigned to this task. Defaults to
        'underwriter'.
      description: Human-readable details about why the review is required.
  """
  if inquiry_id:
    inquiry_id = str(inquiry_id).upper().strip()
  task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
  task_payload = {
      "task_id": task_id,
      "inquiry_id": inquiry_id,
      "task_type": task_type,
      "assigned_to": assigned_to,
      "description": description or f"Human review required for {inquiry_id}.",
      "created_at": datetime.now(timezone.utc).isoformat(),
      "status": "OPEN",
  }

  # Return the created task payload to indicate success
  return json.dumps(task_payload)
