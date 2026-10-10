"""Claude OAuth — project-local install + the documented `claude auth` subcommands.

The Claude Code CLI lives in the shared OpenCompany packages tree at
``<DATA_DIR>/packages/node_modules/.bin/claude[.exe]`` (on-demand
``bun add`` on first use through :mod:`core.js_runtime`, alongside
``edgymeow`` / ``agent-browser`` — one ``package.json`` + ``bun.lock``
managed by bun; the shim runs on the bun runtime, no Node or npm
involved). ``CLAUDE_CONFIG_DIR`` points at
``<DATA_DIR>/claude/`` so the CLI manages its own credentials
inside the project tree, isolated from the user's own
``~/.claude/`` session. The path constants here are composed
against the generic ``core.paths`` helpers (``data_path`` /
``packages_dir``) — see the module docstring on ``core.paths`` for
the no-per-plugin-wrapper convention.

Every subprocess call goes through ``services.events.cli.run_cli_command``
— the canonical one-shot CLI helper used by Stripe / future plugins. Auth
surface follows https://code.claude.com/docs/en/cli-reference verbatim:

- ``claude auth login``  — opens the browser, writes credentials.
- ``claude auth status`` — prints JSON; exits 0 when logged in / 1 otherwise.
- ``claude auth logout`` — log out (CLI clears its own credentials).

The CLI owns its own credentials file; we never read or write it.
``login`` is a long-running one-shot (waits for the browser flow to
complete) so callers schedule it as ``asyncio.create_task`` — same shape
Stripe uses for ``stripe login --complete``.

This module lives alongside the ``claude_code_agent`` plugin so all
claude-specific code stays in one folder per the canonical plugin
pattern. Consumers in ``services/cli_agent/`` (the generic framework)
and the plugin's other modules (``_provider``, ``_pool``, ``_skills``)
import ``OPENCOMPANY_CLAUDE_DIR`` from here.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from services.process_environment import without_onepassword_environment
from typing import Any, Dict, Mapping

from core.js_runtime import add_package, shared_tree_bin
from core.logging import get_logger
from core.paths import data_path, packages_dir
from services.events.cli import run_cli_command

logger = get_logger(__name__)

# Plugin-specific subpaths composed inline against the generic
# ``core.paths`` helpers — keeps ``core.paths`` from growing one
# wrapper function per plugin. Re-exported as module constants so
# existing consumers (``from ._oauth import OPENCOMPANY_CLAUDE_DIR``)
# keep working without a function-call indirection at every call
# site. ``data_path`` / ``packages_dir`` evaluate eagerly here:
# Settings is already initialised by the time this module is first
# imported (node registry discovery runs after the FastAPI app is
# built).
#
# ``OPENCOMPANY_CLAUDE_DIR``  -> ``<DATA_DIR>/claude/``     auth state
#                                                       (CLAUDE_CONFIG_DIR)
# ``OPENCOMPANY_NPM_ROOT``    -> ``<DATA_DIR>/packages/``   shared packages tree
#                                                       (``bun add --cwd``
#                                                       target; also holds
#                                                       ``edgymeow`` /
#                                                       ``agent-browser``)
OPENCOMPANY_CLAUDE_DIR = data_path("claude")
OPENCOMPANY_NPM_ROOT = packages_dir()

# Public compatibility aliases for plugins and scripts written before the
# rebrand. New code should import the OPENCOMPANY_* names above.
MACHINA_CLAUDE_DIR = OPENCOMPANY_CLAUDE_DIR
MACHINA_NPM_ROOT = OPENCOMPANY_NPM_ROOT

# Generous timeout for browser-flow login; Stripe uses the same window.
LOGIN_TIMEOUT_SECONDS = 600.0
# One-shot status / logout return immediately.
ONESHOT_TIMEOUT_SECONDS = 30.0


def claude_binary_path() -> str:
    """Return path to the project-local claude CLI, installing on miss.

    Single source of truth shared by the auth handler (``_handlers.py``)
    AND the agent spawn (``_provider.py``) so both surfaces use the
    same binary + ``CLAUDE_CONFIG_DIR``-isolated credentials.

    Landed under the shared OpenCompany packages tree at
    ``OPENCOMPANY_NPM_ROOT`` (= ``<DATA_DIR>/packages/``) so a single
    ``package.json`` + ``bun.lock`` covers claude / edgymeow /
    agent-browser / cf / vercel.
    """
    bin_path = shared_tree_bin("claude")

    if bin_path.exists():
        return str(bin_path)

    # Pinned spec from ``config/ai_cli_providers.json`` (``package_name`` +
    # ``package_version``), the vercel / cloudflare idiom: an unpinned
    # install makes cold installs non-reproducible and lets a CLI release
    # change the stream-json contract underneath a working deployment.
    from services.cli_agent.config import get_provider_config

    cfg = get_provider_config("claude")
    npm_spec = cfg.package_name if cfg else "@anthropic-ai/claude-code"
    if cfg and cfg.package_version:
        npm_spec = f"{npm_spec}@{cfg.package_version}"

    logger.info("Installing %s into shared tree %s", npm_spec, OPENCOMPANY_NPM_ROOT)

    # The package's postinstall stages its native launcher; trust it so
    # the install matches what `npm install` used to produce.
    result = add_package(npm_spec, trust=True, root=OPENCOMPANY_NPM_ROOT)
    if result.returncode != 0:
        logger.error(f"bun add {npm_spec} failed: {result.stderr}")
        raise RuntimeError(f"Failed to install {npm_spec}: {result.stderr}")

    if not bin_path.exists():
        raise FileNotFoundError(f"Claude CLI not found at {bin_path} after install")

    logger.info(f"Claude Code CLI installed at: {bin_path}")
    return str(bin_path)


def _claude_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(OPENCOMPANY_CLAUDE_DIR)
    return without_onepassword_environment(env)


async def _run_auth(subcommand: str, *, timeout: float) -> Dict[str, Any]:
    """Run ``claude auth <subcommand>`` via the canonical helper. Returns
    the ``run_cli_command`` envelope (``{success, result, stdout, stderr,
    error}``).

    ``login`` is spawned with ``stdin=PIPE`` (un-written) so the native
    claude binary's stdin reader blocks indefinitely instead of EOFing
    against the FastAPI parent's closed stdin. Without this guard the
    CLI exits before the browser hits its localhost callback URL and
    the success page never renders — see ``run_cli_command``'s
    ``stdin`` parameter doc for the full failure mode. ``status`` and
    ``logout`` are one-shot, don't read stdin, and stay on the
    inherit-stdin default.
    """
    try:
        binary = claude_binary_path()
    except (FileNotFoundError, RuntimeError) as e:
        return {"success": False, "error": str(e), "stdout": "", "stderr": ""}

    OPENCOMPANY_CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    envelope = await run_cli_command(
        binary=binary,
        argv=["auth", subcommand],
        timeout=timeout,
        env=_claude_env(),
        stdin=asyncio.subprocess.PIPE if subcommand == "login" else None,
    )
    logger.info(
        "[claude auth %s] success=%s stdout=%r stderr=%r",
        subcommand,
        envelope.get("success"),
        (envelope.get("stdout") or "")[:512],
        (envelope.get("stderr") or "")[:512],
    )
    return envelope


async def claude_auth_status_info() -> Dict[str, Any]:
    """Return parsed JSON from ``claude auth status``. Always includes
    ``loggedIn: bool``; populates ``email``/``orgName``/``subscriptionType``
    when logged in."""
    envelope = await _run_auth("status", timeout=ONESHOT_TIMEOUT_SECONDS)
    parsed = envelope.get("result")
    if isinstance(parsed, dict):
        parsed.setdefault("loggedIn", bool(envelope.get("success")))
        return parsed

    # Status returns exit 1 when not logged in; the JSON still parses but
    # run_cli_command treats non-zero exits as failure and skips parsing.
    # Fall back to parsing stdout directly so we still surface the body.
    stdout = envelope.get("stdout") or ""
    if stdout:
        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                data.setdefault("loggedIn", False)
                return data
        except json.JSONDecodeError:
            pass
    return {"loggedIn": bool(envelope.get("success"))}


async def claude_auth_status() -> bool:
    """True iff ``claude auth status`` reports loggedIn=True."""
    info = await claude_auth_status_info()
    return bool(info.get("loggedIn"))


async def run_claude_login() -> Dict[str, Any]:
    """Run ``claude auth login`` to completion. The CLI opens the user's
    browser, runs its own callback server, and exits when the flow ends.

    Long-running: callers should schedule this via ``asyncio.create_task``
    (Stripe ``stripe login --complete`` precedent). Returns the envelope
    from ``run_cli_command``."""
    return await _run_auth("login", timeout=LOGIN_TIMEOUT_SECONDS)


async def claude_auth_logout() -> bool:
    """Run ``claude auth logout``. True on exit 0."""
    envelope = await _run_auth("logout", timeout=ONESHOT_TIMEOUT_SECONDS)
    return bool(envelope.get("success"))


#: How a claude process reaches the API other than through its login. Set in
#: the server's own environment, they are the install's choice: left alone.
_API_ENV_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


@dataclass(frozen=True)
class ApiAccess:
    """What a spawned claude needs, beyond its login, to reach the API."""

    env: Dict[str, str] = field(default_factory=dict)
    #: The env carries this run's attribution headers, so the process must
    #: not serve another run (no warm reuse).
    per_run: bool = False


async def api_access(base_env: Mapping[str, str]) -> ApiAccess:
    """Where the OpenCompany Gateway serves Anthropic, own access still comes
    first: the CLI's login (``claude auth status``: a Claude subscription or
    Console account), then the install's Anthropic key, then the gateway.
    Without the gateway, claude uses its login as always.

    On the gateway, claude calls the gateway's Anthropic route with the
    gateway key (``ANTHROPIC_BASE_URL``, ``ANTHROPIC_AUTH_TOKEN``) and sends
    the run's attribution headers (``ANTHROPIC_CUSTOM_HEADERS``).
    """
    if any(base_env.get(name) for name in _API_ENV_VARIABLES):
        return ApiAccess()
    from services.gateway.routing import gateway_route

    route = await gateway_route("anthropic")
    if route is None or await claude_auth_status():
        return ApiAccess()
    from core.container import container

    own_key = await container.auth_service().resolve_api_key("anthropic")
    if own_key:
        return ApiAccess(env={"ANTHROPIC_API_KEY": own_key})
    from services.gateway.attribution import request_headers

    headers = "\n".join(f"{name}: {value}" for name, value in request_headers().items())
    return ApiAccess(
        env={
            "ANTHROPIC_BASE_URL": route.base_url,
            "ANTHROPIC_AUTH_TOKEN": route.api_key,
            "ANTHROPIC_CUSTOM_HEADERS": headers,
        },
        per_run=True,
    )
