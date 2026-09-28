---
name: android-phone-skill
description: Use the shared local Android phone through the Android tool, with visible progress and safe manual handoff.
allowed-tools: android
metadata:
  author: opencompany
  version: "1.0"
  category: android
---

# Shared Android phone

Call `android` with a single `prompt` describing one clear goal, the relevant app, and what result to verify. The tool controls the phone visible in Workspace > Mobile. It returns the result after its internal phone steps finish.

- Use one call for a coherent task; avoid one model-driven call per tap. Each call starts a phone agent and has model latency.
- Do not invent low-level operations or pass model, credential, timeout, or step-budget arguments. These come from the saved phone settings.
- Global model selection is the default. An optional connected model overrides it. OpenAI, Claude and Gemini are supported; Gemini Express credentials automatically route to Vertex.
- A phone task is one tool call in the parent agent's turn. Its internal steps appear on the Android node and in Workspace; they are not extra parent-agent turns. Do not claim completion while the tool is still running.
- If the owner takes control, wait for them to resume the task. Ask them to enter passwords and handle interactive login themselves. Never request passwords in chat.
- If the phone is off, direct the owner to Workspace > Mobile > Start phone. Do not install another emulator or attempt raw ADB access.
- Read returned errors. Authentication, quota, unsupported-model, and connection failures need their stated remedy; do not repeat failed calls in a loop or treat an error payload as success.
- Terminal source `mobile` shows the execution ID, provider, model backend, model wait, and device timing. Report the error and relevant phase without asking the owner to reveal API keys.

Example: `{"prompt":"Open Settings, find the Android version, and report it without changing settings."}`
