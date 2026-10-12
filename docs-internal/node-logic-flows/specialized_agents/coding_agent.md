# Coding Agent (`coding_agent`)

| Field | Value |
|------|-------|
| **Category** | specialized_agents |
| **Plugin** | [`server/nodes/agent/coding_agent/__init__.py`](../../../server/nodes/agent/coding_agent/__init__.py) defines the node; new executions route to [`PiCodingAgentWorkflow`](../../../server/services/temporal/pi_workflow.py). [`SpecializedAgentBase.execute_op`](../../../server/nodes/agent/_specialized.py) remains the legacy native operation. |
| **Theme color** | `dracula.cyan` |
| **Icon** | laptop (U+1F4BB) |
| **Tests** | [`server/tests/nodes/test_specialized_agents.py`](../../../server/tests/nodes/test_specialized_agents.py) |

## Purpose

Coding Agent uses the pinned Pi SDK for newly admitted executions, with
Temporal retaining model calls, tool effects, approvals, Context and durable
Stop/Resume. Existing native generations retain their recorded runtime.
`aiAgent` and the other specialist runtimes are unchanged.

## What is unique to this node

- **Tool mode**: `connected` (default) exposes connected tools and scoped
  progressive skills. `workspace` additionally fills missing `fileRead`,
  `fileModify`, `fsSearch` and `shell` capabilities in the workflow workspace.
  A connected node of the same type overrides its automatic counterpart.
  Connect executors or `processManager` explicitly when needed.
- **Runtime**: `PiCodingAgentWorkflow` with a private Bun SDK driver. New
  executions require Temporal, including manual runs and delegated work.
- **Intended skills**: `server/skills/coding_agent/` (python-skill,
  javascript-skill, file-read-skill, file-modify-skill, fs-search-skill).
- **Frontend theming**: cyan dracula accent.

## Behaviour

Preparation, provider settings, handles, skills and the output envelope follow
the **[Generic Specialized Agent Pattern](./_pattern.md)**. Pi owns each
assistant/tool round; existing Temporal leaf Activities own all business I/O.
The SDK receives no credentials and discovers no ambient project/home tools.

See **[Pi Coding Agent](../../pi_coding_agent.md)** for protocol, recovery,
packaging and rollout. Models continue through OpenCompany's existing native
providers and gateway. Pi has its own retries, compaction and background cache
warming disabled; OpenCompany retains those policies.

## Related

- **Pattern doc**: [`_pattern.md`](./_pattern.md)
- **Skills**: [`server/skills/coding_agent/`](../../../server/skills/coding_agent/)
