"""Cloudflare Action — typed core operations over the official `cf` CLI,
a command search, the GraphQL Analytics API, and a raw-command
passthrough.

The cf CLI owns its own auth (Stripe/gh pattern): no pre-flight check
here — cf uses the stored API token (injected as CLOUDFLARE_API_TOKEN),
an ambient token, or its own OAuth login, and its own "No authentication
token found" error surfaces through the NodeUserError wrap.

Argv shapes are verified against the pinned cf 1.0.0-beta.12 (``--help``
and ``--dry-run`` of every wrapped command):

- cf prints the API ``result`` as one pretty-printed JSON document on
  stdout (no ``{success, errors, result}`` envelope; status text and
  error boxes go to stderr), so parsed results flow straight into the
  output panel's JSON tree. There is no output-format flag, and unknown
  flags are rejected.
- The zone is the global ``--zone`` flag. cf resolves a domain only
  within the first page of the selected account's zones, so domains are
  looked up here with ``zones list --name`` and passed as zone IDs.
- There is no ``--account-id`` flag on most commands: the account comes
  from ``CLOUDFLARE_ACCOUNT_ID``, and with several accounts a
  non-interactive run fails until it is set.
- ``dns records create`` / ``edit`` take the record only as ``--body``
  JSON (``edit`` is a PATCH; ``update`` would overwrite the record).
- Destructive commands need ``--force``: without it a non-interactive
  run prints "Aborted." and exits 0 having changed nothing.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any, Dict, List, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.events import run_cli_command
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.params import coerce_blank_params

from ._credentials import CloudflareCredential
from ._service import GLOBAL_KEY_PREFIX

_ZONE_OPS = ["dns_records_list", "dns_record_create", "dns_record_edit", "dns_record_delete"]

_ACCOUNT = {"displayOptions": {"show": {"operation": ["zones_list", *_ZONE_OPS, "custom"]}}}
_ZONE = {"displayOptions": {"show": {"operation": list(_ZONE_OPS)}}}
_LIST = {"displayOptions": {"show": {"operation": ["zones_list", "dns_records_list"]}}}
_RECORD_TYPE = {"displayOptions": {"show": {"operation": ["dns_records_list"]}}}
_BODY = {"displayOptions": {"show": {"operation": ["dns_record_create", "dns_record_edit"]}}}
_RECORD_ID = {"displayOptions": {"show": {"operation": ["dns_record_edit", "dns_record_delete"]}}}
_GRAPHQL = {"displayOptions": {"show": {"operation": ["graphql_query"]}}}
_SEARCH = {"displayOptions": {"show": {"operation": ["search_commands"]}}}
_CUSTOM = {"displayOptions": {"show": {"operation": ["custom"]}}}

_ONESHOT_TIMEOUT = 120.0
_CUSTOM_TIMEOUT = 300.0
_GRAPHQL_TIMEOUT = 60.0
_STDERR_TAIL_CHARS = 2000

# cf passes a 32-hex zone ID (or a UUID) through as-is; anything else is
# a domain name.
_ZONE_ID_RE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# The GraphQL Analytics API is the official replacement for the
# sunsetted Zone Analytics REST API. It is a standalone endpoint OUTSIDE
# the REST/OpenAPI schema the cf CLI is generated from (cf has no graphql
# or raw-request command), so the node calls it directly. Cloudflare
# documents API tokens as the authentication method for it.
# https://developers.cloudflare.com/analytics/graphql-api/
_GRAPHQL_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"

_AUTH_MARKERS = ("no authentication token found", "not logged in", "try running: cf auth login")
_AUTH_HINT = (
    "Connect Cloudflare in Credentials -> Cloudflare: click Login (if it still shows an older login, "
    "click Disconnect first), or paste an API token there."
)
_ACCOUNT_HINT = "Set account_id to the Cloudflare account ID to use (whoami lists the accounts)."
_PERMISSION_HINT = (
    "Cloudflare refused the request: the API token or login lacks a permission for this resource "
    "(edit tokens at dash.cloudflare.com/profile/api-tokens)."
)
_BUN_HINT = (
    "This command loads a cloudflare.config.ts project file, which cf supports only on Node.js 22.18+; "
    "OpenCompany runs cf on bun."
)

# Sign-in belongs to the Credentials modal: a workflow step cannot finish
# cf's interactive device login, and a logout behind the modal's back
# would leave its "connected" badge stale.
_AUTH_STATE_COMMANDS = {"login", "logout", "create", "delete", "activate", "deactivate"}


def _label(argv: List[str]) -> str:
    """The command words of an argv, for error messages."""
    words: List[str] = []
    for tok in argv[:3]:
        if tok.startswith("-"):
            break
        words.append(tok)
    return " ".join(words) or "command"


def _blocked_reason(argv: List[str]) -> Optional[str]:
    head = [a.lower() for a in argv[:2]]
    if head[:1] == ["login"] or (head[:1] == ["auth"] and len(head) > 1 and head[1] in _AUTH_STATE_COMMANDS):
        return (
            "Cloudflare sign-in is managed in Credentials -> Cloudflare (Login / Disconnect) — a workflow "
            "step cannot complete cf's interactive device login. For unattended use, paste an API token "
            "there. ('auth whoami' works here.)"
        )
    if head[:1] == ["dev"]:
        return "'cf dev' starts a long-running local development server, which a workflow step cannot host."
    return None


class CloudflareActionParams(BaseModel):
    operation: Literal[
        "whoami",
        "search_commands",
        "zones_list",
        "dns_records_list",
        "dns_record_create",
        "dns_record_edit",
        "dns_record_delete",
        "graphql_query",
        "custom",
    ] = "whoami"

    # Sent as CLOUDFLARE_ACCOUNT_ID (cf has no --account-id flag on most
    # commands); zones_list also filters by it.
    account_id: str = Field(
        default="",
        description=(
            "Cloudflare account ID. Needed when the login can see more than one account (whoami lists them); "
            "zones_list also filters by it"
        ),
        json_schema_extra={"placeholder": "Account ID (optional)", **_ACCOUNT},
    )

    # Shared zone target for the DNS operations (global --zone flag).
    zone: str = Field(
        default="",
        description="dns_* operations: zone ID or domain name, e.g. example.com",
        json_schema_extra={"placeholder": "example.com or zone ID", **_ZONE},
    )

    # zones_list / dns_records_list filters + paging
    name_filter: str = Field(
        default="",
        description="zones_list: domain to match; dns_records_list: full record name to match, e.g. www.example.com",
        json_schema_extra={"placeholder": "example.com (optional)", **_LIST},
    )
    record_type: str = Field(
        default="",
        description="dns_records_list: only records of this type (A, AAAA, CNAME, TXT, MX, NS, SRV, CAA, ...)",
        json_schema_extra={"placeholder": "A (optional)", **_RECORD_TYPE},
    )
    page: Optional[int] = Field(
        default=None,
        ge=1,
        description="zones_list / dns_records_list: page number (default 1)",
        json_schema_extra={"placeholder": "1", **_LIST},
    )
    per_page: Optional[int] = Field(
        default=None,
        ge=1,
        description="zones_list / dns_records_list: results per page (zones: default 20, max 50; DNS records: default 100)",
        json_schema_extra={"placeholder": "Default", **_LIST},
    )

    # dns_record_create / dns_record_edit
    record_body: str = Field(
        default="",
        description="dns_record_create: the record as JSON; dns_record_edit: only the fields to change, as JSON",
        json_schema_extra={
            "rows": 4,
            "placeholder": '{"type":"A","name":"www","content":"192.0.2.1","ttl":1,"proxied":true}',
            **_BODY,
        },
    )

    # dns_record_edit / dns_record_delete
    record_id: str = Field(
        default="",
        description="dns_record_edit / dns_record_delete: DNS record ID (dns_records_list shows it)",
        json_schema_extra={"placeholder": "023e105f4ecef8ad9ca31a8372d0c353", **_RECORD_ID},
    )

    # graphql_query
    graphql_query: str = Field(
        default="",
        description="GraphQL Analytics API query (the replacement for the sunsetted Zone Analytics REST API)",
        json_schema_extra={
            "rows": 6,
            "placeholder": 'query { viewer { zones(filter: {zoneTag: "..."}) { httpRequestsAdaptiveGroups(limit: 10, filter: {...}) { count } } } }',
            **_GRAPHQL,
        },
    )
    graphql_variables: str = Field(
        default="",
        description="Optional JSON object of GraphQL variables",
        json_schema_extra={
            "rows": 3,
            "placeholder": '{"zoneTag": "023e105f4ecef8ad9ca31a8372d0c353"}',
            **_GRAPHQL,
        },
    )

    # search_commands
    search_query: str = Field(
        default="",
        description=(
            "search_commands: the task to find a cf command for, e.g. 'purge cache for a zone'. Describe the "
            "action and resource only — no names, domains or IDs"
        ),
        json_schema_extra={"placeholder": "list r2 buckets", **_SEARCH},
    )

    # custom
    command: str = Field(
        default="",
        description="custom: a cf command, as typed after 'cf ', e.g. 'r2 buckets list'",
        json_schema_extra={
            "placeholder": "workers list | r2 buckets list | dns records export --zone <zone id>",
            **_CUSTOM,
        },
    )

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce_blanks(cls, values: Any) -> Any:
        """The panel stores "" for a cleared number field — drop it so the
        field's default applies."""
        return coerce_blank_params(cls, values)

    @field_validator("record_body", "graphql_variables", mode="before")
    @classmethod
    def _coerce_json_field(cls, v: Any) -> Any:
        """LLM tool calls may pass these as real JSON objects — coerce
        to the string the CLI flag / HTTP body expects (canonical
        field_validator(mode="before") rule)."""
        if isinstance(v, (dict, list)):
            return json.dumps(v)
        return v


class CloudflareActionOutput(BaseModel):
    operation: Optional[str] = None
    success: Optional[bool] = None
    url: Optional[str] = None
    result: Optional[Any] = None
    stdout: Optional[str] = None
    stderr_tail: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CloudflareActionNode(ActionNode):
    type = "cloudflareAction"
    display_name = "Cloudflare"
    subtitle = "cf CLI"
    group = ("deployment", "tool")
    description = "Cloudflare via the official cf CLI — zones, DNS records, command search, GraphQL analytics, or any cf command"
    component_kind = "square"
    tool_name = "cloudflare"
    tool_description = (
        "Manage Cloudflare through the official cf CLI. Operations and the fields each one uses: "
        "whoami — the identity the CLI acts as and the accounts it can use. "
        "search_commands (search_query) — find the cf command for a task; returns 5 matches. Describe the "
        "action and resource only, never names, domains or IDs. "
        "zones_list (optional name_filter, account_id, page, per_page). "
        "dns_records_list (zone; optional name_filter = full record name, record_type, page, per_page). "
        "dns_record_create (zone, record_body = the record as JSON, e.g. "
        '{"type":"A","name":"www","content":"192.0.2.1","ttl":1,"proxied":true}). '
        'dns_record_edit (zone, record_id, record_body = only the fields to change, e.g. {"content":"192.0.2.2"}). '
        "dns_record_delete (zone, record_id) — deletes at once; confirm with the user first. "
        "graphql_query (graphql_query, graphql_variables) — the GraphQL Analytics API: zone traffic via "
        "httpRequestsAdaptiveGroups, Web Analytics/RUM via rumPageloadEventsAdaptiveGroups under "
        "viewer.accounts; it needs an API token or Global API Key stored in Credentials -> Cloudflare. "
        "custom (command) — any other cf command, as typed after 'cf ', e.g. 'workers list', "
        "'r2 buckets list', 'dns records export --zone <zone id>'; add --force to destructive commands "
        "(cf refuses them otherwise) and --dry-run to preview a request without sending it. "
        "zone takes a zone ID or a domain name. Set account_id when the login can see several accounts "
        "(whoami lists them). Results are JSON. "
        "Reference: https://developers.cloudflare.com/cf/"
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    # OutputPanel renders textual output preformatted (cf status text /
    # custom-command output is terminal text, not markdown).
    ui_hints = {"outputMode": "terminal"}
    credentials = (CloudflareCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = CloudflareActionParams
    Output = CloudflareActionOutput

    # ---- shared plumbing -------------------------------------------------

    async def _run(
        self,
        argv: List[str],
        *,
        account_id: str = "",
        cwd: Optional[str] = None,
        timeout: float = _ONESHOT_TIMEOUT,
    ) -> Dict[str, Any]:
        """No auth pre-flight (Stripe pattern) — cf authenticates from
        the stored API token (injected as CLOUDFLARE_API_TOKEN), an
        ambient token, or its own OAuth login; its error (including "No
        authentication token found") surfaces via the NodeUserError wrap
        below. Runs from the neutral ``cf_workdir()`` unless ``cwd`` is
        given."""
        from ._install import ensure_cf_cli
        from ._service import cf_env, cf_workdir, stored_token

        try:
            binary = str(await ensure_cf_cli())
        except Exception as e:
            raise NodeUserError(
                f"cf CLI install failed: {e}",
                hint="OpenCompany installs the Cloudflare CLI with bun on first use — check that bun is available and the machine is online.",
            ) from e

        token = await stored_token()
        result = await run_cli_command(
            binary=binary,
            argv=argv,
            timeout=timeout,
            env=cf_env(token, account_id.strip() or None),
            cwd=cwd or str(cf_workdir()),
        )
        if not result.get("success"):
            raise self._failure(argv, result, token=token, timeout=timeout)
        stderr = result.get("stderr") or ""
        if "pass --force to confirm" in stderr or any(ln.strip() == "Aborted." for ln in stderr.splitlines()):
            raise NodeUserError(
                f"cf {_label(argv)} did not run: cf asks for confirmation, which a workflow cannot give. "
                "Add --force to confirm the destructive command."
            )
        return result

    @staticmethod
    def _failure(argv: List[str], result: Dict[str, Any], *, token: Optional[str], timeout: float) -> NodeUserError:
        """cf's own error text (a box on stderr) plus a hint for the
        failures with a known fix."""
        label = _label(argv)
        stderr = (result.get("stderr") or "").strip()
        if not stderr:
            error = result.get("error") or "cf invocation failed"
            if "timed out" in error:
                return NodeUserError(f"cf {label} timed out after {timeout:g}s")
            return NodeUserError(f"cf {label} failed: {error}")
        message = f"cf {label} failed: {stderr[-_STDERR_TAIL_CHARS:]}"
        lowered = stderr.lower()
        if "more than one account" in lowered:
            return NodeUserError(message, hint=_ACCOUNT_HINT)
        if any(marker in lowered for marker in _AUTH_MARKERS):
            hint = _AUTH_HINT
            if token and token.startswith(GLOBAL_KEY_PREFIX):
                hint += " The stored Global API Key (cfk_) works only for graphql_query: the cf CLI accepts API tokens, not Global API Keys."
            return NodeUserError(message, hint=hint, requires_user_action=True)
        if "check your api token permissions" in lowered:
            return NodeUserError(message, hint=_PERMISSION_HINT)
        if "not supported on bun" in lowered:
            return NodeUserError(message, hint=_BUN_HINT)
        if "zone not found" in lowered:
            return NodeUserError(message, hint="Pass the zone ID instead of the domain (zones_list shows it).")
        return NodeUserError(message)

    async def _zone_id(self, params: "CloudflareActionParams") -> str:
        """The zone as an ID. A domain is looked up with ``zones list
        --name`` (an exact match across every account the credential can
        see) instead of letting cf resolve it: cf searches only the first
        page of the selected account's zones and needs that account picked
        first."""
        zone = params.zone.strip()
        if not zone:
            raise NodeUserError("zone is required (a zone ID or domain name)")
        if _ZONE_ID_RE.fullmatch(zone):
            return zone
        argv = ["zones", "list", "--name", zone.lower().rstrip(".")]
        account_id = params.account_id.strip()
        if account_id:
            argv += ["--account-id", account_id]
        result = await self._run(argv, account_id=account_id)
        zones = result.get("result")
        matches = [z for z in zones if isinstance(z, dict) and z.get("id")] if isinstance(zones, list) else []
        if not matches:
            raise NodeUserError(
                f"Zone not found: {zone}. Check the domain (the zone apex, e.g. example.com), or pass the zone ID (zones_list shows it)."
            )
        ids = {str(z["id"]) for z in matches}
        if len(ids) > 1:
            accounts = ", ".join(sorted({str((z.get("account") or {}).get("id") or "?") for z in matches}))
            raise NodeUserError(f"{zone} matches zones in several accounts ({accounts}) — set account_id, or pass the zone ID.")
        return ids.pop()

    @staticmethod
    def _page_flags(params: "CloudflareActionParams") -> List[str]:
        flags: List[str] = []
        if params.page:
            flags += ["--page", str(params.page)]
        if params.per_page:
            flags += ["--per-page", str(params.per_page)]
        return flags

    @staticmethod
    def _record_body(params: "CloudflareActionParams", example: str) -> str:
        body = params.record_body.strip()
        if not body:
            raise NodeUserError(f"record_body is required (JSON, e.g. {example})")
        try:
            parsed = json.loads(body)
        except ValueError as e:
            raise NodeUserError(f"record_body is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise NodeUserError(f"record_body must be a JSON object, e.g. {example}")
        return body

    @staticmethod
    def _record_id(params: "CloudflareActionParams") -> str:
        record_id = params.record_id.strip()
        if not record_id:
            raise NodeUserError("record_id is required (find it via dns_records_list)")
        return record_id

    @staticmethod
    def _shape(operation: str, result: Dict[str, Any], *, url: Optional[str] = None) -> Dict[str, Any]:
        """Output-panel shaping: when cf returned JSON (its stdout
        contract), the parsed data IS the payload — the raw stdout string
        would just duplicate it as an unreadable blob (and pre-stringified
        JSON violates the output contract). Text output (e.g. a BIND
        export) lands in ``stdout``. Keys are omitted (not None'd) when
        empty so the panel shows only meaningful fields (`exclude_unset`
        preserves this)."""
        shaped: Dict[str, Any] = {"operation": operation, "success": True}
        if url:
            shaped["url"] = url
        parsed = result.get("result")
        stdout = (result.get("stdout") or "").strip()
        if parsed is not None:
            shaped["result"] = parsed
        elif stdout:
            shaped["stdout"] = stdout
        stderr = (result.get("stderr") or "").strip()
        if stderr:
            shaped["stderr_tail"] = stderr[-_STDERR_TAIL_CHARS:]
        return shaped

    # ---- operations ------------------------------------------------------

    @Operation("whoami", cost={"service": "cloudflare", "action": "whoami", "count": 1})
    async def whoami(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        result = await self._run(["auth", "whoami"])
        return self._shape("whoami", result)

    @Operation("search_commands", cost={"service": "cloudflare", "action": "search_commands", "count": 1})
    async def search_commands(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        """``cf cli search`` — a local index of every cf command, no
        credentials or network involved."""
        query = params.search_query.strip()
        if not query:
            raise NodeUserError("search_query is required (describe the task, e.g. 'purge cache for a zone')")
        result = await self._run(["cli", "search", query])
        return self._shape("search_commands", result)

    @Operation("zones_list", cost={"service": "cloudflare", "action": "zones_list", "count": 1})
    async def zones_list(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        argv = ["zones", "list"]
        if params.name_filter.strip():
            argv += ["--name", params.name_filter.strip()]
        account_id = params.account_id.strip()
        if account_id:
            argv += ["--account-id", account_id]
        argv += self._page_flags(params)
        result = await self._run(argv, account_id=account_id)
        return self._shape("zones_list", result)

    @Operation("dns_records_list", cost={"service": "cloudflare", "action": "dns_records_list", "count": 1})
    async def dns_records_list(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        zone_id = await self._zone_id(params)
        argv = ["dns", "records", "list", "--zone", zone_id]
        if params.name_filter.strip():
            argv += ["--name", params.name_filter.strip()]
        if params.record_type.strip():
            argv += ["--type", params.record_type.strip().upper()]
        argv += self._page_flags(params)
        result = await self._run(argv, account_id=params.account_id)
        return self._shape("dns_records_list", result)

    @Operation("dns_record_create", cost={"service": "cloudflare", "action": "dns_record_create", "count": 1})
    async def dns_record_create(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        body = self._record_body(params, '{"type":"A","name":"www","content":"192.0.2.1","ttl":1}')
        zone_id = await self._zone_id(params)
        argv = ["dns", "records", "create", "--zone", zone_id, "--body", body]
        result = await self._run(argv, account_id=params.account_id)
        return self._shape("dns_record_create", result)

    @Operation("dns_record_edit", cost={"service": "cloudflare", "action": "dns_record_edit", "count": 1})
    async def dns_record_edit(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        """PATCH — only the fields in record_body change (``dns records
        update`` would overwrite the whole record)."""
        record_id = self._record_id(params)
        body = self._record_body(params, '{"content":"192.0.2.2"}')
        zone_id = await self._zone_id(params)
        argv = ["dns", "records", "edit", record_id, "--zone", zone_id, "--body", body]
        result = await self._run(argv, account_id=params.account_id)
        return self._shape("dns_record_edit", result)

    @Operation("dns_record_delete", cost={"service": "cloudflare", "action": "dns_record_delete", "count": 1})
    async def dns_record_delete(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        record_id = self._record_id(params)
        zone_id = await self._zone_id(params)
        # --force: choosing this operation IS the confirmation. Without it
        # cf prints "Aborted." and exits 0 with the record still there.
        argv = ["dns", "records", "delete", record_id, "--zone", zone_id, "--force"]
        result = await self._run(argv, account_id=params.account_id)
        return self._shape("dns_record_delete", result)

    @Operation("graphql_query", cost={"service": "cloudflare", "action": "graphql_query", "count": 1})
    async def graphql_query(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        """The GraphQL Analytics API — a standalone endpoint outside the
        cf CLI's generated REST surface, called directly. It needs a
        credential OpenCompany holds (API token or Global API Key + email):
        the cf login session stays inside the CLI."""
        query = params.graphql_query.strip()
        if not query:
            raise NodeUserError(
                "graphql_query is required (e.g. query { viewer { zones(filter: {zoneTag: $zoneTag}) "
                "{ httpRequestsAdaptiveGroups(limit: 10) { count } } } })"
            )
        variables: Dict[str, Any] = {}
        if params.graphql_variables.strip():
            try:
                variables = json.loads(params.graphql_variables)
            except ValueError as e:
                raise NodeUserError(f"graphql_variables is not valid JSON: {e}") from e

        from ._service import api_auth_headers, stored_email, stored_token

        key = await stored_token() or os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_KEY")
        email = await stored_email() or os.environ.get("CLOUDFLARE_EMAIL")
        headers = api_auth_headers(key, email)
        if headers is None:
            raise NodeUserError(
                "The GraphQL Analytics API is called directly, not through the cf CLI, so the cf login "
                "cannot be used for it. In Credentials -> Cloudflare paste either an API token "
                "(dash.cloudflare.com/profile/api-tokens, permission 'Account > Account Analytics > Read') "
                "or a Global API Key (cfk_...) together with the Account Email field.",
                requires_user_action=True,
            )

        body = await self._graphql_post(headers, {"query": query, "variables": variables})
        return self._shape("graphql_query", {"result": body, "stdout": "", "stderr": ""})

    @staticmethod
    async def _graphql_post(headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        """One POST to the official endpoint, exactly as documented
        ({query, variables} body; auth headers from api_auth_headers —
        Bearer for tokens, X-Auth-Email/X-Auth-Key for Global API
        Keys). 401/403 map to the credential fix; hard GraphQL errors
        (no data) surface as NodeUserError; partial data rides back in
        the body."""
        async with httpx.AsyncClient(timeout=_GRAPHQL_TIMEOUT) as client:
            resp = await client.post(
                _GRAPHQL_ENDPOINT,
                json=payload,
                headers=headers,
            )
        if resp.status_code in (401, 403):
            raise NodeUserError(
                f"GraphQL Analytics API rejected the credential (HTTP {resp.status_code}). "
                "API tokens need 'Account > Account Analytics > Read' (and 'Zone > Analytics > Read' "
                "for zone-scoped queries) — edit at dash.cloudflare.com/profile/api-tokens. "
                "Global API Keys need the matching Account Email."
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise NodeUserError(f"GraphQL Analytics API returned non-JSON (HTTP {resp.status_code}): {resp.text[:300]}") from e
        if resp.status_code >= 400 or (body.get("data") is None and body.get("errors")):
            errors = body.get("errors") or [{"message": f"HTTP {resp.status_code}"}]
            first = errors[0]
            message = first.get("message") if isinstance(first, dict) else str(first)
            raise NodeUserError(f"GraphQL query failed: {message}")
        return body

    @Operation("custom", cost={"service": "cloudflare", "action": "custom", "count": 1})
    async def custom(self, ctx: NodeContext, params: CloudflareActionParams) -> Any:
        """Any cf command. Runs from the workflow workspace so relative
        paths (``--body @record.json``, ``--file``, ``--dir``) resolve
        against the files the workflow wrote."""
        cmd = params.command.strip()
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            raise NodeUserError(
                f"command could not be parsed ({e}) — wrap JSON in single quotes, e.g. --body '{{\"content\":\"192.0.2.2\"}}'"
            ) from e
        if argv and argv[0].lower() in ("cf", "cloudflare"):
            argv = argv[1:]
        if not argv:
            raise NodeUserError("command is required (e.g. 'workers list', 'r2 buckets list', 'dns records export --zone <zone id>')")
        reason = _blocked_reason(argv)
        if reason:
            raise NodeUserError(reason)
        workspace = ctx.workspace_dir if ctx.workspace_dir and os.path.isdir(ctx.workspace_dir) else None
        result = await self._run(argv, account_id=params.account_id, cwd=workspace, timeout=_CUSTOM_TIMEOUT)
        stdout = (result.get("stdout") or "").strip()
        url = stdout if stdout.startswith("http") and "\n" not in stdout else None
        return self._shape("custom", result, url=url)
