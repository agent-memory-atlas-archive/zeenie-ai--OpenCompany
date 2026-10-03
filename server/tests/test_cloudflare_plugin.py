"""Cloudflare plugin contract tests.

Locks the plugin's load-bearing seams under the CLI-owns-auth (Stripe /
gh) pattern, verified against the pinned cf 1.0.0-beta.12:

- env builders: stored API token injected, Global API Key never (cf
  ignores it); ``CF_DELEGATION`` + telemetry opt-out; the account via
  ``CLOUDFLARE_ACCOUNT_ID``; the login env strips ambient tokens; ops run
  from the neutral ``cf_workdir``.
- install resolution: pinned npm spec into the shared packages tree,
  reinstalled when another version is in place (clearing the stale login
  marker); the system cf is never consulted.
- op argv (``--help`` / ``--dry-run`` verified): ``--force`` on delete,
  PATCH edit, domain -> zone ID lookup, filters + paging, failure hints,
  the custom passthrough's guards.
- the device-flow login handler (URL + code relayed to the modal, single
  flight, install before any login, never killed, whoami-gated
  completion) and the marker-token + CloudEvents broadcast contract
  (``inspect.getsource`` introspection, ``test_credential_broadcasts``
  style).
- the catalogue entry shape and the plugin folder assets.

No real cf binary is ever started: ``run_cli_command`` and the login
spawn are faked.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import typing
from pathlib import Path

import pytest

import nodes.cloudflare._handlers as cf_handlers
import nodes.cloudflare._install as cf_install
import nodes.cloudflare._service as cf_service
import nodes.cloudflare.cloudflare_action as cf_action_mod
from nodes.cloudflare import WS_HANDLERS
from nodes.cloudflare._credentials import CloudflareCredential
from nodes.cloudflare.cloudflare_action import CloudflareActionNode, CloudflareActionParams
from services.plugin import NodeContext
from services.plugin.base import NodeUserError

PLUGIN_DIR = Path(cf_service.__file__).parent
CONFIG_PATH = Path(cf_service.__file__).parents[2] / "config" / "credential_providers.json"

ZONE_ID = "023e105f4ecef8ad9ca31a8372d0c353"
GLOBAL_KEY = "cfk_" + "y" * 48

# Verbatim shape of cf 1.0's device-flow banner (stderr).
BANNER = (
    "Attempting to login via OAuth Device Authorization Grant...\n"
    "To authorize cf, please visit:\n"
    "\n"
    "  https://dash.cloudflare.com/oauth2/device\n"
    "\n"
    "and enter the code:\n"
    "\n"
    "  ABCD-EFGH\n"
    "\n"
    "You have 5 minutes to approve this request.\n"
)


# --- _service env builders ----------------------------------------------------


def test_cf_env_sets_cli_hygiene_and_keeps_ambient_token(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    env = cf_service.cf_env()
    assert env["NO_COLOR"] == "1"
    # The pinned cf must not hand the command to another cf copy it can
    # resolve (bun's install cache included), and telemetry stays off.
    assert env["CF_DELEGATION"] == "1"
    assert env["CF_SEND_TELEMETRY"] == "false"
    # Ambient env token is left alone for ops — cf's documented precedence
    # (and the headless path on remote deployments).
    assert env["CLOUDFLARE_API_TOKEN"] == "cf_ambient"
    assert "CLOUDFLARE_ACCOUNT_ID" not in env


def test_cf_env_stored_token_overrides_ambient(monkeypatch):
    # Explicit user config (the credentials-modal token) beats the
    # server environment.
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    env = cf_service.cf_env("cf_stored")
    assert env["CLOUDFLARE_API_TOKEN"] == "cf_stored"


def test_cf_env_never_injects_a_global_api_key(monkeypatch):
    """cf refuses the Global API Key (the CLOUDFLARE_API_KEY +
    CLOUDFLARE_EMAIL pair is ignored), so a stored cfk_ key is not
    injected under any name — cf falls back to ambient env / its login."""
    for var in ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "CLOUDFLARE_API_KEY", "CLOUDFLARE_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    env = cf_service.cf_env(GLOBAL_KEY)
    assert "CLOUDFLARE_API_TOKEN" not in env
    assert "CLOUDFLARE_API_KEY" not in env
    assert "CLOUDFLARE_EMAIL" not in env

    # ...and an ambient token is not dropped on its behalf.
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    assert cf_service.cf_env(GLOBAL_KEY)["CLOUDFLARE_API_TOKEN"] == "cf_ambient"


def test_cf_env_routes_account_id(monkeypatch):
    # cf has no --account-id flag on most commands; the account is
    # CLOUDFLARE_ACCOUNT_ID.
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    assert cf_service.cf_env(None, "acct1")["CLOUDFLARE_ACCOUNT_ID"] == "acct1"


def test_api_auth_headers_route_by_prefix():
    assert cf_service.api_auth_headers(GLOBAL_KEY, "o@x.dev") == {"X-Auth-Email": "o@x.dev", "X-Auth-Key": GLOBAL_KEY}
    assert cf_service.api_auth_headers(GLOBAL_KEY, None) is None
    assert cf_service.api_auth_headers("cfut_tok", None) == {"Authorization": "Bearer cfut_tok"}
    assert cf_service.api_auth_headers(None, "o@x.dev") is None


def test_login_env_strips_ambient_credential_vars(monkeypatch):
    """With CLOUDFLARE_API_TOKEN set, `cf auth whoami` reports the env
    token instead of the OAuth login and `cf auth login` refuses to
    start — the login/status/logout paths must consult cf's OWN store."""
    for var in cf_service._AMBIENT_CREDENTIAL_VARS:
        monkeypatch.setenv(var, "ambient")
    env = cf_service.login_env()
    for var in cf_service._AMBIENT_CREDENTIAL_VARS:
        assert var not in env
    assert env["NO_COLOR"] == "1"
    assert env["CF_DELEGATION"] == "1"


def test_cf_workdir_is_a_dedicated_data_dir(monkeypatch, tmp_path):
    """cf reads .env files and looks for cloudflare.config.ts from its
    working directory — ops run from a plugin-owned directory, never the
    server's own cwd."""
    import core.paths

    monkeypatch.setattr(core.paths, "data_path", lambda sub="": tmp_path / str(sub))
    workdir = cf_service.cf_workdir()
    assert workdir == tmp_path / "cloudflare"
    assert workdir.is_dir()


# --- _install resolution --------------------------------------------------------


def test_npm_spec_is_pinned():
    # @latest would make cold installs non-reproducible and silently
    # change the argv surface this plugin was verified against.
    assert cf_install._NPM_SPEC == f"cf@{cf_install._NPM_VERSION}"
    assert "latest" not in cf_install._NPM_SPEC


def _fake_tree(monkeypatch, tmp_path, *, installed):
    """A shared tree whose cf shim exists iff ``installed`` is a version."""
    from core import js_runtime

    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / js_runtime.bin_shim_name("cf")
    state = {"version": installed, "installs": 0, "forgot": []}
    if installed:
        shim.write_text("", encoding="utf-8")

    def fake_install():
        state["installs"] += 1
        shim.write_text("", encoding="utf-8")
        state["version"] = cf_install._NPM_VERSION
        return shim

    async def fake_forget(previous):
        state["forgot"].append(previous)

    monkeypatch.setattr(cf_install, "_cached_path", None)
    monkeypatch.setattr(cf_install, "shared_tree_bin", lambda name: bin_dir / js_runtime.bin_shim_name(name))
    monkeypatch.setattr(cf_install, "_installed_version", lambda: state["version"])
    monkeypatch.setattr(cf_install, "_install", fake_install)
    monkeypatch.setattr(cf_install, "_forget_cli_login", fake_forget)
    return shim, state


async def test_ensure_cf_cli_installs_into_shared_tree(monkeypatch, tmp_path):
    """The system-global cf is never consulted — the binary comes from
    the pinned ``bun add`` into the shared packages tree."""
    shim, state = _fake_tree(monkeypatch, tmp_path, installed=None)
    assert await cf_install.ensure_cf_cli() == shim
    assert state["installs"] == 1
    # cached for subsequent calls without re-installing
    assert await cf_install.ensure_cf_cli() == shim
    assert state["installs"] == 1
    # a first install strands no login
    assert state["forgot"] == []


async def test_ensure_cf_cli_reinstalls_when_another_version_is_installed(monkeypatch, tmp_path):
    """An existing shim says nothing about its version: an older cf is
    upgraded to the pin, and the login marker is cleared because the new
    version may not see the old login."""
    shim, state = _fake_tree(monkeypatch, tmp_path, installed="0.2.0")
    assert await cf_install.ensure_cf_cli() == shim
    assert state["installs"] == 1
    assert state["forgot"] == ["0.2.0"]


async def test_ensure_cf_cli_reuses_a_matching_install(monkeypatch, tmp_path):
    shim, state = _fake_tree(monkeypatch, tmp_path, installed=cf_install._NPM_VERSION)
    assert await cf_install.ensure_cf_cli() == shim
    assert state["installs"] == 0
    assert state["forgot"] == []


def test_cf_cli_path_ignores_an_older_install(monkeypatch, tmp_path):
    shim, state = _fake_tree(monkeypatch, tmp_path, installed="0.2.0")
    assert cf_install.cf_cli_path() is None
    state["version"] = cf_install._NPM_VERSION
    assert cf_install.cf_cli_path() == shim


def test_upgrade_clears_the_login_marker_and_broadcasts():
    src = inspect.getsource(cf_install._forget_cli_login)
    assert 'mark_logged_out("cloudflare")' in src
    assert "credential.oauth.disconnected" in src


def test_cf_binary_never_resolved_from_system_path():
    # Project-local contract: PATH lookup is allowed for bun itself
    # (inside core.js_runtime), never for the cf binary.
    src_install = inspect.getsource(cf_install)
    src_service = inspect.getsource(cf_service)
    assert "which(" not in src_install
    assert "which(" not in src_service
    # resolution goes through the shared-tree shim only
    assert 'shared_tree_bin("cf")' in inspect.getsource(cf_install._shared_tree_bin)


# --- operations (no auth pre-flight — Stripe pattern) ---------------------------


def _wire(monkeypatch, tmp_path, run_impl):
    async def fake_ensure():
        return tmp_path / "cf"

    async def nothing_stored():
        return None

    monkeypatch.setattr(cf_install, "ensure_cf_cli", fake_ensure)
    monkeypatch.setattr(cf_action_mod, "run_cli_command", run_impl)
    # ops resolve the optional modal credential from the credentials DB
    # — absent in unit tests, so stub the lookups to "nothing stored".
    monkeypatch.setattr(cf_service, "stored_token", nothing_stored)
    monkeypatch.setattr(cf_service, "stored_email", nothing_stored)
    monkeypatch.setattr(cf_service, "cf_workdir", lambda: tmp_path / "cfwork")


def _ok(result=None, stdout=None, stderr=""):
    if stdout is None:
        stdout = json.dumps(result, indent=2) if result is not None else ""
    return {"success": True, "result": result, "stdout": stdout, "stderr": stderr, "error": None}


def _recorder(*replies):
    """A fake ``run_cli_command`` that records every call and answers
    with ``replies`` in order (the last one repeats)."""
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return replies[min(len(calls), len(replies)) - 1]

    return calls, fake_run


def _ctx(tmp_path) -> NodeContext:
    return NodeContext(node_id="cf1", node_type="cloudflareAction", workspace_dir=str(tmp_path))


def _flag(argv, name):
    return argv[argv.index(name) + 1]


async def test_whoami_argv_env_and_cwd(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"authenticated": True, "email": "o@x.dev", "accounts": []}))
    _wire(monkeypatch, tmp_path, fake_run)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    out = await CloudflareActionNode().whoami(_ctx(tmp_path), CloudflareActionParams(operation="whoami"))

    (call,) = calls
    assert call["argv"] == ["auth", "whoami"]
    # env comes from cf_env() — no stored token, so nothing injected
    assert call["env"]["NO_COLOR"] == "1"
    assert call["env"]["CF_DELEGATION"] == "1"
    assert "CLOUDFLARE_API_TOKEN" not in call["env"]
    # typed ops run from the neutral working directory
    assert call["cwd"] == str(tmp_path / "cfwork")
    assert out["result"]["email"] == "o@x.dev"


async def test_ops_inject_stored_api_token(monkeypatch, tmp_path):
    """The optional credentials-modal token rides every op as
    CLOUDFLARE_API_TOKEN (cf's first-priority credential source)."""
    calls, fake_run = _recorder(_ok([]))
    _wire(monkeypatch, tmp_path, fake_run)

    async def fake_token():
        return "cf_tok_123"

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    await CloudflareActionNode().zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert calls[0]["env"]["CLOUDFLARE_API_TOKEN"] == "cf_tok_123"


async def test_ops_never_inject_a_stored_global_key(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok([]))
    _wire(monkeypatch, tmp_path, fake_run)
    for var in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY", "CLOUDFLARE_EMAIL"):
        monkeypatch.delenv(var, raising=False)

    async def fake_token():
        return GLOBAL_KEY

    async def fake_email():
        return "o@x.dev"

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    monkeypatch.setattr(cf_service, "stored_email", fake_email)
    await CloudflareActionNode().zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    env = calls[0]["env"]
    assert "CLOUDFLARE_API_TOKEN" not in env
    assert "CLOUDFLARE_API_KEY" not in env
    assert "CLOUDFLARE_EMAIL" not in env


async def test_search_commands_runs_cli_search(monkeypatch, tmp_path):
    matches = [{"command": "cf dns records create", "summary": "Create DNS Record"}]
    calls, fake_run = _recorder(_ok(matches))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="search_query"):
        await node.search_commands(_ctx(tmp_path), CloudflareActionParams(operation="search_commands"))

    out = await node.search_commands(
        _ctx(tmp_path),
        CloudflareActionParams(operation="search_commands", search_query="create a dns record"),
    )
    # one argv element: cf joins the words itself
    assert calls[0]["argv"] == ["cli", "search", "create a dns record"]
    assert out["result"] == matches


async def test_zones_list_builds_filter_and_paging_argv(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok([{"id": ZONE_ID, "name": "example.com"}]))
    _wire(monkeypatch, tmp_path, fake_run)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    node = CloudflareActionNode()

    await node.zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert calls[0]["argv"] == ["zones", "list"]
    assert "CLOUDFLARE_ACCOUNT_ID" not in calls[0]["env"]

    out = await node.zones_list(
        _ctx(tmp_path),
        CloudflareActionParams(operation="zones_list", name_filter="example.com", account_id="acct1", page=2, per_page=50),
    )
    argv = calls[1]["argv"]
    assert argv[:2] == ["zones", "list"]
    assert _flag(argv, "--name") == "example.com"
    assert _flag(argv, "--account-id") == "acct1"
    assert _flag(argv, "--page") == "2"
    assert _flag(argv, "--per-page") == "50"
    assert calls[1]["env"]["CLOUDFLARE_ACCOUNT_ID"] == "acct1"
    assert out["result"] == [{"id": ZONE_ID, "name": "example.com"}]


async def test_dns_records_list_requires_zone_and_builds_filters(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok([{"id": "r1"}]))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="zone"):
        await node.dns_records_list(_ctx(tmp_path), CloudflareActionParams(operation="dns_records_list"))
    assert calls == []

    await node.dns_records_list(
        _ctx(tmp_path),
        CloudflareActionParams(
            operation="dns_records_list",
            zone=ZONE_ID,
            name_filter="www.example.com",
            record_type="cname",
            per_page=100,
        ),
    )
    # A zone ID needs no lookup — one call.
    (call,) = calls
    argv = call["argv"]
    assert argv[:3] == ["dns", "records", "list"]
    assert _flag(argv, "--zone") == ZONE_ID
    assert _flag(argv, "--name") == "www.example.com"
    assert _flag(argv, "--type") == "CNAME"
    assert _flag(argv, "--per-page") == "100"
    assert "--page" not in argv


async def test_domain_zone_is_resolved_to_its_id(monkeypatch, tmp_path):
    """cf only searches the first page of the selected account's zones
    for a domain — the node looks it up exactly with zones list --name
    and passes the ID."""
    calls, fake_run = _recorder(
        _ok([{"id": ZONE_ID, "name": "example.com", "account": {"id": "acct1"}}]),
        _ok([{"id": "r1"}]),
    )
    _wire(monkeypatch, tmp_path, fake_run)
    await CloudflareActionNode().dns_records_list(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_records_list", zone="Example.com.", account_id="acct1"),
    )
    lookup, listing = calls
    assert lookup["argv"] == ["zones", "list", "--name", "example.com", "--account-id", "acct1"]
    assert listing["argv"][:5] == ["dns", "records", "list", "--zone", ZONE_ID]
    assert listing["env"]["CLOUDFLARE_ACCOUNT_ID"] == "acct1"


async def test_domain_zone_resolution_errors(monkeypatch, tmp_path):
    node = CloudflareActionNode()
    params = CloudflareActionParams(operation="dns_records_list", zone="example.com")

    calls, fake_run = _recorder(_ok([]))
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="Zone not found"):
        await node.dns_records_list(_ctx(tmp_path), params)
    assert len(calls) == 1

    twins = [
        {"id": ZONE_ID, "name": "example.com", "account": {"id": "acctA"}},
        {"id": "f" * 32, "name": "example.com", "account": {"id": "acctB"}},
    ]
    calls, fake_run = _recorder(_ok(twins))
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="account_id"):
        await node.dns_records_list(_ctx(tmp_path), params)


async def test_dns_record_create_validates_body_and_builds_argv(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"id": "new1"}))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()
    body = '{"type":"A","name":"www","content":"192.0.2.1","ttl":1,"proxied":true}'

    with pytest.raises(NodeUserError, match="record_body"):
        await node.dns_record_create(_ctx(tmp_path), CloudflareActionParams(operation="dns_record_create", zone=ZONE_ID))
    with pytest.raises(NodeUserError, match="not valid JSON"):
        await node.dns_record_create(
            _ctx(tmp_path),
            CloudflareActionParams(operation="dns_record_create", zone=ZONE_ID, record_body="{oops"),
        )
    with pytest.raises(NodeUserError, match="JSON object"):
        await node.dns_record_create(
            _ctx(tmp_path),
            CloudflareActionParams(operation="dns_record_create", zone=ZONE_ID, record_body="[1, 2]"),
        )
    # Local validation runs before any CLI call (zone lookup included).
    assert calls == []

    await node.dns_record_create(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_record_create", zone=ZONE_ID, record_body=body),
    )
    # cf 1.0: the record is --body JSON only — no per-field flags.
    assert calls[0]["argv"] == ["dns", "records", "create", "--zone", ZONE_ID, "--body", body]


def test_record_body_coerces_llm_dict_args():
    # LLM tool calls may pass the record as a real JSON object.
    params = CloudflareActionParams(
        operation="dns_record_create",
        zone="example.com",
        record_body={"type": "A", "name": "www"},
    )
    assert json.loads(params.record_body) == {"type": "A", "name": "www"}


def test_page_params_accept_panel_blanks():
    # The panel stores "" for a cleared number field.
    params = CloudflareActionParams(operation="zones_list", page="", per_page="")
    assert params.page is None and params.per_page is None
    assert CloudflareActionParams(operation="zones_list", page="2").page == 2


async def test_dns_record_edit_is_a_patch_with_positional_id(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"id": "r1", "content": "192.0.2.2"}))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()
    body = '{"content":"192.0.2.2"}'

    with pytest.raises(NodeUserError, match="record_id"):
        await node.dns_record_edit(
            _ctx(tmp_path),
            CloudflareActionParams(operation="dns_record_edit", zone=ZONE_ID, record_body=body),
        )
    with pytest.raises(NodeUserError, match="record_body"):
        await node.dns_record_edit(
            _ctx(tmp_path),
            CloudflareActionParams(operation="dns_record_edit", zone=ZONE_ID, record_id="r1"),
        )

    await node.dns_record_edit(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_record_edit", zone=ZONE_ID, record_id="r1", record_body=body),
    )
    # `edit` is the PATCH (only the given fields change); `update` would overwrite.
    assert calls[0]["argv"] == ["dns", "records", "edit", "r1", "--zone", ZONE_ID, "--body", body]


async def test_dns_record_delete_forces_and_uses_positional_id(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"id": "r1"}))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="record_id"):
        await node.dns_record_delete(_ctx(tmp_path), CloudflareActionParams(operation="dns_record_delete", zone=ZONE_ID))

    await node.dns_record_delete(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_record_delete", zone=ZONE_ID, record_id="r1"),
    )
    # Without --force a non-interactive cf prints "Aborted." and exits 0
    # with the record still there.
    assert calls[0]["argv"] == ["dns", "records", "delete", "r1", "--zone", ZONE_ID, "--force"]


async def test_aborted_confirmation_is_an_error(monkeypatch, tmp_path):
    aborted = _ok(stdout="", stderr="? This permanently deletes the resource. Continue?\n  (non-interactive; pass --force to confirm)\nAborted.")
    calls, fake_run = _recorder(aborted)
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="--force"):
        await CloudflareActionNode().custom(
            _ctx(tmp_path),
            CloudflareActionParams(operation="custom", command="kv namespaces delete ns1"),
        )


async def test_cf_auth_error_surfaces_verbatim_with_login_hint(monkeypatch, tmp_path):
    """No pre-flight: cf's own error IS the auth error, and only the
    owner can fix it."""
    stderr = (
        "┌ Error\n│ No authentication token found. Either:\n│ 1. Set the CLOUDFLARE_API_TOKEN environment variable\n"
        "│ 2. Run 'cf auth login' to authenticate via OAuth\n└"
    )
    calls, fake_run = _recorder({"success": False, "stdout": "", "stderr": stderr, "error": stderr})
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="cf auth login") as exc:
        await CloudflareActionNode().zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert exc.value.requires_user_action is True
    assert "Credentials -> Cloudflare" in exc.value.hint


async def test_global_key_auth_failure_explains_the_cli_limit(monkeypatch, tmp_path):
    calls, fake_run = _recorder({"success": False, "stdout": "", "stderr": "No authentication token found.", "error": "exit 1"})
    _wire(monkeypatch, tmp_path, fake_run)

    async def fake_token():
        return GLOBAL_KEY

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    with pytest.raises(NodeUserError) as exc:
        await CloudflareActionNode().zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert "Global API Key" in exc.value.hint


async def test_multi_account_error_points_at_account_id(monkeypatch, tmp_path):
    stderr = "More than one account available but unable to select one in non-interactive mode."
    calls, fake_run = _recorder({"success": False, "stdout": "", "stderr": stderr, "error": stderr})
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="More than one account") as exc:
        await CloudflareActionNode().custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command="workers list"))
    assert "account_id" in exc.value.hint
    assert exc.value.requires_user_action is False


async def test_timeout_names_the_command(monkeypatch, tmp_path):
    calls, fake_run = _recorder({"success": False, "error": f"{tmp_path / 'cf'} timed out (120.0s)"})
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match=r"cf zones list timed out after 120s"):
        await CloudflareActionNode().zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))


async def test_install_failure_is_a_user_error(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok([]))
    _wire(monkeypatch, tmp_path, fake_run)

    async def broken_install():
        raise RuntimeError("bun add cf@x failed: offline")

    monkeypatch.setattr(cf_install, "ensure_cf_cli", broken_install)
    with pytest.raises(NodeUserError, match="cf CLI install failed"):
        await CloudflareActionNode().whoami(_ctx(tmp_path), CloudflareActionParams(operation="whoami"))
    assert calls == []


async def test_custom_requires_command_and_shlex_splits(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"ok": True}))
    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError):
        await node.custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command="  "))
    with pytest.raises(NodeUserError):
        await node.custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command="cf"))

    await node.custom(
        _ctx(tmp_path),
        CloudflareActionParams(
            operation="custom",
            command="cf dns records update r1 --zone example.com --body '{\"content\":\"192.0.2.2\"}'",
        ),
    )
    # A leading "cf " is tolerated; the rest passes through verbatim.
    assert calls[0]["argv"] == ["dns", "records", "update", "r1", "--zone", "example.com", "--body", '{"content":"192.0.2.2"}']
    assert calls[0]["timeout"] == cf_action_mod._CUSTOM_TIMEOUT
    # custom runs from the workflow workspace so relative file paths resolve there
    assert calls[0]["cwd"] == str(tmp_path)


async def test_custom_without_a_workspace_runs_from_the_neutral_dir(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok([]))
    _wire(monkeypatch, tmp_path, fake_run)
    ctx = NodeContext(node_id="cf1", node_type="cloudflareAction", workspace_dir=None)
    await CloudflareActionNode().custom(ctx, CloudflareActionParams(operation="custom", command="workers list"))
    assert calls[0]["cwd"] == str(tmp_path / "cfwork")


async def test_custom_rejects_unbalanced_quotes(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({}))
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError, match="single quotes"):
        await CloudflareActionNode().custom(
            _ctx(tmp_path),
            CloudflareActionParams(operation="custom", command="dns records create --body '{\"type\":\"A\""),
        )
    assert calls == []


@pytest.mark.parametrize("command", ["auth login", "login", "auth logout", "auth create work", "AUTH LOGIN --force", "dev"])
async def test_custom_blocks_interactive_auth_and_dev(monkeypatch, tmp_path, command):
    calls, fake_run = _recorder(_ok({}))
    _wire(monkeypatch, tmp_path, fake_run)
    with pytest.raises(NodeUserError):
        await CloudflareActionNode().custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command=command))
    assert calls == []


async def test_custom_allows_read_only_auth_commands(monkeypatch, tmp_path):
    calls, fake_run = _recorder(_ok({"authenticated": False}))
    _wire(monkeypatch, tmp_path, fake_run)
    await CloudflareActionNode().custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command="auth whoami"))
    assert calls[0]["argv"] == ["auth", "whoami"]


async def test_graphql_requires_a_credential(monkeypatch, tmp_path):
    """The cf login stays inside the CLI — graphql_query must fail with
    the credential guidance (token OR global key + email), not a bare
    403."""
    _wire(monkeypatch, tmp_path, None)
    for var in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY", "CLOUDFLARE_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(NodeUserError, match="Account Analytics") as exc:
        await CloudflareActionNode().graphql_query(
            _ctx(tmp_path),
            CloudflareActionParams(operation="graphql_query", graphql_query="query { viewer { zones { __typename } } }"),
        )
    assert exc.value.requires_user_action is True


async def test_graphql_posts_official_body_shape(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, None)

    async def fake_token():
        return "cf_tok"

    captured = {}

    async def fake_post(headers, payload):
        captured["headers"] = headers
        captured["payload"] = payload
        return {"data": {"viewer": {"zones": []}}}

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    monkeypatch.setattr(CloudflareActionNode, "_graphql_post", staticmethod(fake_post))
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="graphql_query is required"):
        await node.graphql_query(_ctx(tmp_path), CloudflareActionParams(operation="graphql_query"))

    with pytest.raises(NodeUserError, match="not valid JSON"):
        await node.graphql_query(
            _ctx(tmp_path),
            CloudflareActionParams(operation="graphql_query", graphql_query="query { viewer }", graphql_variables="{oops"),
        )

    out = await node.graphql_query(
        _ctx(tmp_path),
        CloudflareActionParams(
            operation="graphql_query",
            graphql_query="query Q($zoneTag: string) { viewer }",
            graphql_variables='{"zoneTag": "z1"}',
        ),
    )
    # The official request body shape: {query, variables}, Bearer token.
    assert captured["headers"] == {"Authorization": "Bearer cf_tok"}
    assert captured["payload"] == {
        "query": "query Q($zoneTag: string) { viewer }",
        "variables": {"zoneTag": "z1"},
    }
    assert out["result"] == {"data": {"viewer": {"zones": []}}}


async def test_graphql_post_maps_403_to_permission_guidance(monkeypatch):
    class _Resp:
        status_code = 403
        text = "forbidden"

        @staticmethod
        def json():
            return {"data": None, "errors": [{"message": "not authorized for that account"}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            assert url == cf_action_mod._GRAPHQL_ENDPOINT
            assert kwargs["headers"]["Authorization"] == "Bearer tok"
            return _Resp()

    monkeypatch.setattr(cf_action_mod.httpx, "AsyncClient", _Client)
    with pytest.raises(NodeUserError, match="Account Analytics"):
        await CloudflareActionNode._graphql_post({"Authorization": "Bearer tok"}, {"query": "q", "variables": {}})


def test_graphql_endpoint_is_the_official_one():
    assert cf_action_mod._GRAPHQL_ENDPOINT == "https://api.cloudflare.com/client/v4/graphql"


def test_shape_prefers_parsed_result_then_stdout():
    shaped = CloudflareActionNode._shape("op", {"result": {"a": 1}, "stdout": '{"a": 1}', "stderr": ""})
    assert shaped["result"] == {"a": 1}
    assert "stdout" not in shaped

    # Text output (e.g. a BIND export) ships as stdout.
    shaped = CloudflareActionNode._shape("op", {"result": None, "stdout": "plain text", "stderr": "warn"})
    assert shaped["stdout"] == "plain text"
    assert "result" not in shaped
    assert shaped["stderr_tail"] == "warn"

    # A change that returns no data prints nothing — only the status keys.
    assert CloudflareActionNode._shape("op", {"result": None, "stdout": "", "stderr": ""}) == {"operation": "op", "success": True}


def test_node_has_no_auth_preflight():
    # Stripe-strict: the CLI owns auth; the node must not pre-check it.
    assert not hasattr(CloudflareActionNode, "_preflight")
    src = inspect.getsource(cf_action_mod)
    assert "PermissionError" not in src


def test_every_operation_has_a_method():
    declared = set(typing.get_args(CloudflareActionParams.model_fields["operation"].annotation))
    implemented = {spec.name for spec in CloudflareActionNode._operations.values()}
    assert declared == implemented


def test_tool_description_names_every_operation():
    # The LLM sees no description on the operation enum (and Gemini drops
    # displayOptions), so the tool description is where it learns them.
    for op in typing.get_args(CloudflareActionParams.model_fields["operation"].annotation):
        assert op in CloudflareActionNode.tool_description


# --- login handler --------------------------------------------------------------


def _reset_login_state(monkeypatch):
    monkeypatch.setattr(cf_handlers, "_active_login", cf_handlers._fresh_state())


class _FakeProc:
    """Just enough of asyncio.subprocess.Process for the login flow."""

    def __init__(self, stderr_text: str = ""):
        self.pid = 4242
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._exited = asyncio.Event()
        if stderr_text:
            self.stderr.feed_data(stderr_text.encode())

    def finish(self, code: int = 0) -> None:
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = code
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode


async def _until(condition, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        assert loop.time() < end, "condition not reached"
        await asyncio.sleep(0.01)


def _installed_cli(monkeypatch, tmp_path):
    async def fake_ensure():
        return tmp_path / "cf"

    async def no_login():
        return None

    monkeypatch.setattr(cf_handlers, "cf_cli_path", lambda: tmp_path / "cf")
    monkeypatch.setattr(cf_handlers, "ensure_cf_cli", fake_ensure)
    monkeypatch.setattr(cf_handlers, "whoami_snapshot", no_login)


def test_parse_login_banner_reads_cf_device_banner():
    assert cf_handlers.parse_login_banner(BANNER) == ("https://dash.cloudflare.com/oauth2/device", "ABCD-EFGH")
    # still waiting for the code
    assert cf_handlers.parse_login_banner(BANNER.split("and enter")[0]) is None
    # colour codes never leak into the URL or code
    coloured = BANNER.replace("https://", "\x1b[36mhttps://").replace("device\n", "device\x1b[39m\n")
    assert cf_handlers.parse_login_banner(coloured) == ("https://dash.cloudflare.com/oauth2/device", "ABCD-EFGH")


def test_login_args_use_the_device_flow_without_a_server_browser():
    # Device flow: no callback server, works from any machine; the
    # frontend opens the URL, so cf must not open a second tab.
    assert cf_handlers._LOGIN_ARGS[:2] == ["auth", "login"]
    assert "--device" in cf_handlers._LOGIN_ARGS
    assert "--no-browser" in cf_handlers._LOGIN_ARGS
    assert "--no-device" not in cf_handlers._LOGIN_ARGS
    # pushes past a stale stored token (only spawned when whoami says no login)
    assert "--force" in cf_handlers._LOGIN_ARGS


async def test_login_relays_device_url_and_code_then_marks_on_approval(monkeypatch, tmp_path):
    _reset_login_state(monkeypatch)
    _installed_cli(monkeypatch, tmp_path)
    proc = _FakeProc(BANNER)
    spawned = []

    async def fake_spawn(binary):
        spawned.append(binary)
        return proc

    monkeypatch.setattr(cf_handlers, "_spawn_login", fake_spawn)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert res["url"] == "https://dash.cloudflare.com/oauth2/device"
    assert res["verification_code"] == "ABCD-EFGH"
    assert len(spawned) == 1

    # A repeat click while the code awaits approval returns the same code
    # instead of starting a second device flow.
    again = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert again["verification_code"] == "ABCD-EFGH"
    assert len(spawned) == 1

    # Approval: cf exits, whoami reports the login -> marker + broadcast.
    marked = []

    async def fake_mark(email):
        marked.append(email)

    async def logged_in():
        return {"authenticated": True, "email": "o@x.dev"}

    monkeypatch.setattr(cf_handlers, "_mark_connected", fake_mark)
    monkeypatch.setattr(cf_handlers, "whoami_snapshot", logged_in)
    proc.finish(0)
    await _until(lambda: marked == ["o@x.dev"])
    await _until(lambda: cf_handlers._active_login["proc"] is None)


async def test_login_reports_cf_output_when_no_code_is_issued(monkeypatch, tmp_path):
    _reset_login_state(monkeypatch)
    _installed_cli(monkeypatch, tmp_path)
    proc = _FakeProc("┌ Error\n│ fetch failed: dash.cloudflare.com unreachable\n└\n")

    async def fake_spawn(binary):
        proc.finish(1)
        return proc

    monkeypatch.setattr(cf_handlers, "_spawn_login", fake_spawn)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is False
    assert "unreachable" in res["error"]


async def test_login_short_circuits_when_already_logged_in(monkeypatch, tmp_path):
    _reset_login_state(monkeypatch)
    _installed_cli(monkeypatch, tmp_path)
    marked = []

    async def live_login():
        return {"authenticated": True, "email": "o@x.dev"}

    async def fake_mark(email):
        marked.append(email)

    async def boom(binary):
        raise AssertionError("a live login must not spawn a device flow")

    monkeypatch.setattr(cf_handlers, "whoami_snapshot", live_login)
    monkeypatch.setattr(cf_handlers, "_mark_connected", fake_mark)
    monkeypatch.setattr(cf_handlers, "_spawn_login", boom)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert "Already logged in" in res["message"]
    assert marked == ["o@x.dev"]


async def test_login_installs_cf_before_any_login_is_spawned(monkeypatch, tmp_path):
    """First use: a device code issued after the request was answered
    would be invisible — the install runs alone, and Login is clicked
    again once it finishes."""
    _reset_login_state(monkeypatch)
    release = asyncio.Event()
    installs = []

    async def slow_install():
        installs.append(1)
        await release.wait()
        return tmp_path / "cf"

    async def boom(binary):
        raise AssertionError("no login may start while cf is installing")

    monkeypatch.setattr(cf_handlers, "cf_cli_path", lambda: None)
    monkeypatch.setattr(cf_handlers, "ensure_cf_cli", slow_install)
    monkeypatch.setattr(cf_handlers, "_spawn_login", boom)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)

    first = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert first["success"] is True and first.get("pending") is True
    second = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert second.get("pending") is True
    # one install, however often Login is clicked
    assert installs == [1]
    release.set()
    await _until(lambda: cf_handlers._active_login["install"].done())


async def test_login_answers_within_budget_when_flow_stalls(monkeypatch, tmp_path):
    _reset_login_state(monkeypatch)
    _installed_cli(monkeypatch, tmp_path)

    async def stalled_flow():
        await asyncio.sleep(30)
        return {"success": True, "message": "done"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", stalled_flow)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert res.get("pending") is True
    cf_handlers._active_login["task"].cancel()


async def test_login_is_single_flight(monkeypatch, tmp_path):
    # A repeat click while a flow is starting must not spawn again.
    _reset_login_state(monkeypatch)
    _installed_cli(monkeypatch, tmp_path)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_flow():
        started.set()
        await release.wait()
        return {"success": True, "message": "done"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", slow_flow)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)

    first = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert first.get("pending") is True
    await started.wait()

    def boom():
        raise AssertionError("second click must not spawn a second flow")

    monkeypatch.setattr(cf_handlers, "_start_login_flow", boom)
    second = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert second["success"] is True
    assert second.get("pending") is True
    assert "already starting" in second["message"].lower()

    release.set()


def test_login_never_kills_the_cf_process():
    # The installed binary is a bun bin shim on Windows (a launcher .exe
    # that runs the CLI in a child bun process): killing it orphans that
    # child. cf ends the device flow by itself.
    for fn in (cf_handlers._complete_login, cf_handlers._start_login_flow, cf_handlers._read_login_banner):
        src = inspect.getsource(fn)
        assert ".kill(" not in src
        assert ".terminate(" not in src


# --- marker-token + broadcast contract (source introspection) -------------------


def test_login_success_gates_on_whoami_then_marks_and_broadcasts():
    # Marker + broadcast plumbing is the SHARED module (claude/codex/github/cloudflare).
    assert cf_handlers.mark_logged_in.__module__ == "services.cli_agent._cli_auth"
    complete = inspect.getsource(cf_handlers._complete_login)
    # exit code alone is never trusted — cf exits 0 logged-in AND logged-out;
    # `cf auth whoami` JSON is the gate (and the account-label email source).
    assert "whoami_snapshot" in complete
    assert "_mark_connected" in complete
    marked = inspect.getsource(cf_handlers._mark_connected)
    assert 'mark_logged_in("cloudflare"' in marked
    assert "credential.oauth.connected" in marked
    snapshot = inspect.getsource(cf_service.whoami_snapshot)
    assert '"auth", "whoami"' in snapshot
    assert "authenticated" in snapshot


def test_logout_removes_marker_and_broadcasts():
    assert cf_handlers.mark_logged_out.__module__ == "services.cli_agent._cli_auth"
    src = inspect.getsource(cf_handlers.handle_cloudflare_logout)
    assert 'mark_logged_out("cloudflare")' in src
    assert "credential.oauth.disconnected" in src


def test_login_uses_stripped_env():
    # login/status/logout must consult cf's OWN store, never env tokens
    # — and the stored API token must never be injected there either.
    for fn in (cf_handlers._spawn_login, cf_handlers.handle_cloudflare_logout):
        src = inspect.getsource(fn)
        assert "login_env" in src
        assert "cf_workdir" in src
        assert "stored_token" not in src
    assert "login_env" in inspect.getsource(cf_service.whoami_snapshot)


def test_ws_handlers_registered():
    assert set(WS_HANDLERS) == {"cloudflare_login", "cloudflare_logout", "cloudflare_status"}


# --- catalogue + assets ----------------------------------------------------------


def test_catalogue_entry_is_vercel_dual_path_shape():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "deployment" in config["categories"]
    cloudflare = config["providers"]["cloudflare"]
    assert cloudflare["kind"] == "oauth"
    assert cloudflare["category"] == "deployment"
    assert cloudflare["icon_ref"] == "lobehub:Cloudflare"
    assert cloudflare["ws"] == {
        "login": "cloudflare_login",
        "logout": "cloudflare_logout",
        "status": "cloudflare_status",
    }
    # Dual-path (vercel shape): OPTIONAL fields only — required would
    # gate the Login button (OAuthConnect counts required fields only),
    # and the OAuth login must keep working without a credential.
    # The primary key is the canonical `apiKey` (accepts an API token
    # OR a cfk_ Global API Key): the panel maps it to the provider id
    # for storage, so the base Credential.validate scaffold (which
    # stores under cls.id) needs no override, and the shared
    # ApiKeyInput validate flow lights up automatically. The secondary
    # field carries the account email Global API Keys authenticate with.
    fields = cloudflare["fields"]
    assert [f["key"] for f in fields] == ["apiKey", "cloudflare_email"]
    token_field = fields[0]
    assert token_field["required"] is False
    assert token_field["secret"] is True
    assert token_field["type"] == "password"
    email_field = fields[1]
    assert email_field["required"] is False


def test_credential_class_shape():
    assert CloudflareCredential.id == "cloudflare"
    assert CloudflareCredential.auth == "custom"


async def test_credential_resolve_returns_optional_credential_rows(monkeypatch):
    from services.plugin import deps as plugin_deps

    class _Auth:
        def __init__(self, rows):
            self._rows = rows

        async def get_api_key(self, key):
            # Token stored under the provider id (canonical `apiKey`
            # field, mapped to config.id by the panel); the email
            # companion under its own field key.
            assert key in ("cloudflare", "cloudflare_email")
            return self._rows.get(key)

    monkeypatch.setattr(plugin_deps, "get_auth_service", lambda: _Auth({"cloudflare": "tok"}))
    assert await CloudflareCredential.resolve() == {"cloudflare_api_token": "tok"}

    monkeypatch.setattr(
        plugin_deps,
        "get_auth_service",
        lambda: _Auth({"cloudflare": GLOBAL_KEY, "cloudflare_email": "o@x.dev"}),
    )
    assert await CloudflareCredential.resolve() == {
        "cloudflare_api_token": GLOBAL_KEY,
        "cloudflare_email": "o@x.dev",
    }

    monkeypatch.setattr(plugin_deps, "get_auth_service", lambda: _Auth({}))
    assert await CloudflareCredential.resolve() == {}


async def test_probe_hits_official_verify_endpoint(monkeypatch):
    """The Validate button flows through the base Credential.validate
    scaffold to this probe — one GET to Cloudflare's documented
    token-verify endpoint, nothing else."""
    import nodes.cloudflare._credentials as cf_credentials

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": {"id": "t1", "status": captured["status"]}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["auth"] = kwargs["headers"]["Authorization"]
            return _Resp()

    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)

    captured["status"] = "active"
    result = await CloudflareCredential._probe("tok123")
    assert result.valid is True
    assert captured["url"] == "https://api.cloudflare.com/client/v4/user/tokens/verify"
    assert captured["auth"] == "Bearer tok123"

    captured["status"] = "disabled"
    result = await CloudflareCredential._probe("tok123")
    assert result.valid is False
    assert "disabled" in result.message


async def test_probe_global_key_without_email_gives_guidance(monkeypatch):
    """cfk_ is Cloudflare's documented Global API Key prefix — legacy
    X-Auth-Email/X-Auth-Key auth that is NEVER valid as a Bearer token.
    Without the stored email companion the probe must explain the fix
    up front (no network call) instead of relaying the API's generic
    1000/9109 rejection."""
    import nodes.cloudflare._credentials as cf_credentials

    async def no_email():
        return None

    class _Boom:
        def __init__(self, **kwargs):
            raise AssertionError("cfk_ without email must not hit the network")

    monkeypatch.setattr(cf_credentials, "stored_email", no_email)
    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Boom)
    result = await CloudflareCredential._probe(GLOBAL_KEY)
    assert result.valid is False
    assert "Account Email" in result.message


async def test_probe_validates_global_key_with_email(monkeypatch):
    """cfk_ + stored email validate via the documented legacy header
    pair against GET /user."""
    import nodes.cloudflare._credentials as cf_credentials

    async def has_email():
        return "o@x.dev"

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": {"id": "u1", "email": "o@x.dev"}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            return _Resp()

    monkeypatch.setattr(cf_credentials, "stored_email", has_email)
    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)
    result = await CloudflareCredential._probe(GLOBAL_KEY)
    assert result.valid is True
    assert "o@x.dev" in result.message
    assert captured["url"] == "https://api.cloudflare.com/client/v4/user"
    assert captured["headers"] == {"X-Auth-Email": "o@x.dev", "X-Auth-Key": GLOBAL_KEY}


async def test_probe_verifies_account_tokens_via_accounts_read(monkeypatch):
    """cfat_ account-owned tokens cannot verify at /user/tokens/verify
    (that endpoint is user-token-only) — validity is proven with a
    lightweight authenticated read instead."""
    import nodes.cloudflare._credentials as cf_credentials

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": [{"id": "acct1"}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["auth"] = kwargs["headers"]["Authorization"]
            return _Resp()

    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)
    token = "cfat_" + "y" * 48
    result = await CloudflareCredential._probe(token)
    assert result.valid is True
    assert captured["url"] == "https://api.cloudflare.com/client/v4/accounts"
    assert captured["auth"] == f"Bearer {token}"


def test_plugin_folder_assets():
    # Icon is the official lobehub brand glyph via visuals.json — a
    # co-located icon.svg would silently override it.
    assert not (PLUGIN_DIR / "icon.svg").exists()
    meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["color"] == "#F38020"

    visuals = json.loads((PLUGIN_DIR.parent / "visuals.json").read_text(encoding="utf-8"))
    entry = visuals["cloudflareAction"]
    assert entry["icon"] == "lobehub:Cloudflare"
    assert entry["skill"] == "cloudflare-skill"
    # tool_name ("cloudflare") != snake_case(node type) — the lowercase
    # alias carries icon + color for the Master Skill row.
    alias = visuals["cloudflare"]
    assert alias["icon"] == "lobehub:Cloudflare"
    assert alias["color"].startswith("#")

    skill_md = PLUGIN_DIR.parents[1] / "skills" / "cloudflare" / "cloudflare-skill" / "SKILL.md"
    assert skill_md.exists()
