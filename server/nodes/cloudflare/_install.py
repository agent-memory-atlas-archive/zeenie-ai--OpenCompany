"""Cloudflare CLI (`cf`) auto-installer — bun, pinned, project-local.

The official Cloudflare CLI ships as the npm package ``cf`` (open beta
since 2026-09-28, "the next version of Wrangler"). It lands in the
shared OpenCompany packages tree at :func:`core.paths.packages_dir`
(``<DATA_DIR>/packages/``), the same single ``package.json`` +
``node_modules/`` that holds ``@anthropic-ai/claude-code`` / ``edgymeow``
/ ``vercel``. :func:`core.js_runtime.add_package` (``bun add --cwd
<packages_dir> <spec>``) extends the shared tree idempotently and the
bin shim executes on the bun runtime; no Node or npm is involved. cf
1.0 pulls in miniflare + workerd for its local-dev commands, so the
first install downloads about 300 MB; no lifecycle script needs
``--trust``.

The system-global ``cf`` is deliberately NEVER consulted (the gh
philosophy): the command surface is generated from Cloudflare's
OpenAPI schema and drifts between releases (0.2.0 -> 1.0 removed
``context`` / ``agent-context``, moved the auth store and made device
authorization the default login), and this plugin's argv builders are
verified against the pinned version only.

**The pin is enforced, not just installed once.** An existing shim says
nothing about which version it runs, so :func:`ensure_cf_cli` compares
the installed ``node_modules/cf/package.json`` version with the pin and
re-runs ``bun add`` when they differ. A version change can strand the
CLI's login (1.0 does not migrate 0.2.0's ``auth.jsonc``), so the
catalogue's "connected" marker is dropped on upgrade; the login
handler's ``whoami`` short-circuit re-marks it at once if the session
survived.

Bump recipe: change ``_NPM_VERSION``, re-check every wrapped command
with ``cf <cmd> --help`` (and ``--dry-run``), adjust argv builders and
tests. cf declares ``engines.node >= 22``; bun reports a Node version
that passes that guard. Commands that load a ``cloudflare.config.ts``
need real Node 22.18+, which is why ops run from a neutral working
directory (``_service.cf_workdir``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from core.js_runtime import add_package, installed_version, shared_tree_bin
from core.logging import get_logger

logger = get_logger(__name__)

_NPM_NAME = "cf"
_NPM_VERSION = "1.0.0-beta.12"
_NPM_SPEC = f"{_NPM_NAME}@{_NPM_VERSION}"

_cached_path: Optional[Path] = None
_install_lock = asyncio.Lock()


def _shared_tree_bin() -> Path:
    return shared_tree_bin("cf")


def _installed_version() -> Optional[str]:
    return installed_version(_NPM_NAME)


def cf_cli_path() -> Optional[Path]:
    """Sync getter for the project-local binary — the installed shim at
    the PINNED version, without installing. ``None`` when cf was never
    installed or an older version is still in place (the next op or
    login upgrades it)."""
    global _cached_path
    if _cached_path and _cached_path.exists():
        return _cached_path
    target = _shared_tree_bin()
    if target.exists() and _installed_version() == _NPM_VERSION:
        _cached_path = target
        return target
    return None


def _install() -> Path:
    """Blocking ``bun add`` into the shared tree. Raises on failure."""
    bin_path = _shared_tree_bin()
    logger.info("[Cloudflare] installing %s into the shared packages tree", _NPM_SPEC)
    result = add_package(_NPM_SPEC)
    if result.returncode != 0 or not bin_path.exists():
        raise RuntimeError(f"bun add {_NPM_SPEC} failed: {result.stderr.strip()[:500]}")
    logger.info("[Cloudflare] cf CLI %s installed at %s", _NPM_VERSION, bin_path)
    return bin_path


async def _forget_cli_login(previous: str) -> None:
    """A version change can strand the CLI's stored login — drop the
    catalogue marker so the modal offers Login again instead of a stale
    "connected". Best-effort: the upgrade itself already succeeded."""
    try:
        from services.cli_agent._cli_auth import broadcast_credential_event, mark_logged_out

        await mark_logged_out("cloudflare")
        await broadcast_credential_event("credential.oauth.disconnected", provider="cloudflare")
        logger.info("[Cloudflare] cf upgraded %s -> %s; cleared the login marker (log in again if cf asks)", previous, _NPM_VERSION)
    except Exception as e:  # pragma: no cover - best-effort bookkeeping
        logger.warning("[Cloudflare] could not clear the login marker after the cf upgrade: %s", e)


async def ensure_cf_cli() -> Path:
    """Return absolute path to the project-local cf binary, installing
    the pinned release on miss or when another version is installed.
    Idempotent + concurrent-safe."""
    global _cached_path
    if _cached_path and _cached_path.exists():
        return _cached_path

    async with _install_lock:
        if _cached_path and _cached_path.exists():
            return _cached_path
        target = _shared_tree_bin()
        previous = _installed_version()
        if target.exists() and previous == _NPM_VERSION:
            _cached_path = target
            return target
        # The install blocks for tens of seconds — keep the event loop free.
        installed = await asyncio.to_thread(_install)
        _cached_path = installed
        if previous is not None and previous != _NPM_VERSION:
            await _forget_cli_login(previous)
        return installed


__all__ = ["cf_cli_path", "ensure_cf_cli"]
