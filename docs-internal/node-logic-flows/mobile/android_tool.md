# Android (`android_tool`)

| Field | Value |
| --- | --- |
| **Category** | tool |
| **Backend handler** | [`AndroidTool.execute_op`](../../../server/nodes/mobile/_tool.py) via `ToolNode` |
| **Tests** | [`test_android_tool.py`](../../../server/tests/test_android_tool.py) |
| **Tool name** | `android` |

## Purpose

Give an existing AI agent a natural-language tool for the shared local Android phone. Setup and startup happen explicitly in Workspace → Mobile. This is separate from the legacy Android relay services.

## Inputs (handles)

| Handle | Required | Purpose |
| --- | --- | --- |
| `input-model` | Yes | Exactly one enabled OpenAI, Anthropic, or Gemini model connector for the embedded mobile-use engine |

## Parameters

The AI-visible schema accepts only `prompt`: a required string, 1–20,000 characters. Whitespace-only requests are rejected by the shared execution path. Extra tool arguments are forbidden.

Saved configuration uses `MobileParams`: `prompt` defaults to empty, `max_steps` defaults to 40 (1–200), and `timeout_s` defaults to 900 (30–3600 seconds). In tool mode, the model supplies the request while the saved configuration controls the step/time limits. A parent agent's model connection does not replace `input-model` on this node.

## Outputs (handles)

`output-tool` connects to an AI agent's `input-tools`. The result contains `response`, `outcome`, `run_id`, and `artifacts` (currently empty). Standard tool dispatch returns a flat result or error; there are no main-flow handles.

## Logic Flow

1. Standard tool dispatch validates the model input separately from saved configuration.
2. The operation combines the prompt with saved limits and calls the shared Mobile Agent execution path.
3. That path verifies local owner access, saved workflow/execution identity, and the model connector.
4. The shared runtime queues the task and grants a fenced device lease before starting the isolated mobile-use worker.
5. The worker returns its result; cleanup revokes capabilities and drains pending device operations.

## Decision Logic

Missing setup or a stopped phone produces an actionable error; execution never accepts SDK licenses or starts installation. Human control pauses agent use. Tasks use the same queue, cancellation and manual-resume rules as Mobile Agent. Automatic activity retries are disabled to avoid replaying phone actions.

## Side Effects

Model API requests, device reads and mutations through the broker, a task worker subprocess, and bounded operational logs. The plugin reads saved model configuration and provider credentials server-side. Prompts and screenshots reach the selected model provider but are excluded from diagnostic logs.

## External Dependencies

The installed optional mobile-use environment, managed Android SDK/AVD, local embedded Temporal worker, and a supported model credential. Node import only registers capabilities; it starts no emulator or downloads.

## Edge cases & known limits

Windows x64 managed Android only; one shared phone and one video viewer. No iOS, remote device farm, arbitrary device attachment, or independent worker process. The task schema cannot override workflow scope, device serial, credentials, or saved execution limits.

## Related

- [Mobile Agent](mobile_use_agent.md)
- [Mobile Workspace setup, controls, logging and limitations](../../../docs/mobile-workspace.md)
