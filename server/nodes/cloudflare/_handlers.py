"""Cloudflare WebSocket handlers — the cf CLI owns its own auth (gh /
Stripe CLI pattern); OpenCompany relays cf's device code to the modal.

``cloudflare_login`` spawns cf's official login::

    cf auth login --force --device --no-browser

cf 1.0 signs in with the OAuth 2.0 Device Authorization Grant (RFC
8628): it prints a verification URL and a one-time code on stderr, polls
``dash.cloudflare.com`` until the user approves (at most 5 minutes), and
exits. Source-verified banner (cf 1.0.0-beta.12, bundled
``@cloudflare/workers-auth``)::

    To authorize cf, please visit:

      <verification_uri>

    and enter the code:

      <user_code>

    You have 5 minutes to approve this request.

The handler parses the URL + code and answers ``{success, url,
verification_code}``: the frontend opens the URL in the user's own
browser and shows the code (gh's device-flow shape). ``--no-browser``
stops cf from also opening a tab on the server machine — a duplicate
tab on a desktop install, a failure on a remote server. There is no
callback server (cf's old ``localhost:8877`` flow is ``--no-device``
only), so the login works from any machine.

Single flight: while a login waits for approval, repeat clicks get the
same URL + code back instead of a second device flow. The output pumps
store the banner the moment it appears, so a click answered "still
preparing" picks the code up on the next click.

First use installs cf (~300 MB: miniflare + workerd ship with it) BEFORE
any login is spawned — in the background when the install cannot finish
inside the frontend's 30 s request window. No login runs while that
install is in flight, so cf never issues a code nobody can see.

The completion watcher never kills the login process: on Windows the
binary is bun's ``cf.exe`` launcher shim, and killing it orphans the
child bun process running the CLI. cf ends the device flow by itself.

Success gate: ``cf auth whoami`` reporting ``authenticated: true`` (cf
exits 0 in both auth states, so exit codes are never trusted). On
success we write the synthetic ``cli-managed`` marker OAuth row (flips
the catalogue's ``stored`` badge, with the whoami email as the account
label) and broadcast the generic catalogue-invalidation event. Marker +
broadcast plumbing is the shared :mod:`services.cli_agent._cli_auth`
module (claude/codex/github all use it).

OpenCompany never stores or reads the actual token — it stays in cf's
own profile store.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from core.ansi import strip_ansi
from core.logging import get_logger
from services.cli_agent._cli_auth import broadcast_credential_event, mark_logged_in, mark_logged_out
from services.events import run_cli_command

from ._install import cf_cli_path, ensure_cf_cli
from ._service import cf_workdir, login_env, whoami_snapshot

logger = get_logger(__name__)

# --force: we only spawn when whoami reports no live login, so this never
# discards a healthy one — it pushes past a stale or invalid stored token
# that would otherwise end the run with "You are already logged in.".
# --device pins cf's default flow; --no-browser leaves the URL to the
# frontend.
_LOGIN_ARGS = ["auth", "login", "--force", "--device", "--no-browser"]
# Backstop only — cf abandons the device code after 5 minutes and exits.
_LOGIN_TIMEOUT_SECONDS = 600
# How long one request waits for the URL + code on cf's output.
_BANNER_DEADLINE_SECONDS = 15
# The frontend drops WS requests after 30 s — answer within this budget.
_RESPONSE_BUDGET_SECONDS = 22
# Retained head of the CLI's output (banner parsing + failure log line).
_OUTPUT_CAP_CHARS = 32768

_URL_RE = re.compile(r"please visit:\s*(https?://\S+)", re.IGNORECASE)
_CODE_RE = re.compile(r"enter the code:\s*([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)


def _fresh_state() -> Dict[str, Any]:
    """Single-flight state: ``task`` = the pre-spawn window (whoami probe
    + spawn + banner wait), ``proc`` = the device flow until cf exits,
    ``url``/``code`` = that flow's banner, ``install`` = a first-use
    install running in the background."""
    return {"task": None, "proc": None, "url": None, "code": None, "install": None}


_active_login: Dict[str, Any] = _fresh_state()

# Strong refs for fire-and-forget tasks — asyncio holds only weak refs
# (the documented discard-set pattern from the asyncio docs).
_background_tasks: set = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _pending(message: str) -> Dict[str, Any]:
    return {"success": True, "pending": True, "message": message}


def _tail(output: List[str]) -> str:
    lines = [ln.strip() for ln in strip_ansi("".join(output)).splitlines() if ln.strip()]
    return " | ".join(lines[-5:]) or "(no output)"


def parse_login_banner(text: str) -> Optional[Tuple[str, str]]:
    """``(verification_url, user_code)`` once both appear in cf's
    accumulated output; ``None`` while still waiting."""
    clean = strip_ansi(text)
    url_m = _URL_RE.search(clean)
    code_m = _CODE_RE.search(clean)
    if not (url_m and code_m):
        return None
    return url_m.group(1).rstrip(".,;)'\""), code_m.group(1)


def _waiting_login() -> Optional[Dict[str, Any]]:
    """The answer for a device flow that is still waiting for approval."""
    proc = _active_login["proc"]
    if proc is None or proc.returncode is not None or not _active_login["url"]:
        return None
    return {
        "success": True,
        "url": _active_login["url"],
        "verification_code": _active_login["code"],
        "message": "A Cloudflare login is waiting for approval — enter the code on the page that opened.",
    }


def _login_in_progress() -> bool:
    task = _active_login["task"]
    if task is not None and not task.done():
        return True
    proc = _active_login["proc"]
    return proc is not None and proc.returncode is None


async def _mark_connected(email: Optional[str]) -> None:
    await mark_logged_in("cloudflare", email=email)
    logger.info("[Cloudflare] connected as %s — catalogue marker persisted", email or "<unknown>")
    await broadcast_credential_event("credential.oauth.connected", provider="cloudflare")


async def _install_cf() -> Optional[str]:
    """Install the pinned cf; an error string instead of raising."""
    try:
        await ensure_cf_cli()
        return None
    except Exception as e:
        logger.warning("[Cloudflare] cf CLI install failed: %s", e)
        return str(e)


def _install_in_background() -> asyncio.Task:
    task = _active_login["install"]
    if task is None or task.done():
        task = _spawn_background(_install_cf())
        _active_login["install"] = task
    return task


def _install_failed(error: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": f"cf CLI install failed ({error}). OpenCompany installs it with bun on first use — check that bun is available and the machine is online, then click Login again.",
    }


async def _spawn_login(binary: str) -> asyncio.subprocess.Process:
    """Spawn ``cf auth login`` against cf's OWN profile store
    (``login_env`` strips ambient tokens) from the neutral working
    directory. stdin=PIPE left un-written: a pipe keeps any stray stdin
    read from seeing instant EOF; cf's prompts are off without a TTY."""
    return await asyncio.create_subprocess_exec(
        binary,
        *_LOGIN_ARGS,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=login_env(),
        cwd=str(cf_workdir()),
    )


async def _read_login_banner(proc: asyncio.subprocess.Process, output: List[str]) -> Tuple[Optional[Tuple[str, str]], bool]:
    """Read cf's early output until the URL + code appear, cf closes its
    output, or the deadline passes; returns ``(banner, eof)``.

    The pumps deliberately OUTLIVE this call: they drain both pipes for
    the process lifetime (cf polls for up to 5 minutes and must never
    block on a full pipe) and store the banner in the single-flight
    state whenever it appears, so a later click can return it."""
    new_data = asyncio.Event()
    kept = 0

    async def pump(stream: Optional[asyncio.StreamReader]) -> None:
        nonlocal kept
        if stream is not None:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                if kept < _OUTPUT_CAP_CHARS:
                    text = chunk.decode(errors="replace")
                    output.append(text)
                    kept += len(text)
                    if _active_login["proc"] is proc and not _active_login["url"]:
                        parsed = parse_login_banner("".join(output))
                        if parsed:
                            _active_login["url"], _active_login["code"] = parsed
                new_data.set()
        new_data.set()

    pumps = [_spawn_background(pump(proc.stdout)), _spawn_background(pump(proc.stderr))]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BANNER_DEADLINE_SECONDS
    while True:
        # Clear BEFORE parsing: data that lands after the parse sets the
        # event again, so no wakeup is lost.
        new_data.clear()
        banner = parse_login_banner("".join(output))
        eof = all(t.done() for t in pumps)
        if banner or eof:
            return banner, eof
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None, False
        try:
            await asyncio.wait_for(new_data.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return parse_login_banner("".join(output)), False


async def _start_login_flow() -> Dict[str, Any]:
    """whoami short-circuit, then spawn cf's device-flow login and wait
    for its URL + code. Never raises — returns the WS response dict. The
    CLI owns the interaction from here; we only watch for its exit in the
    background."""
    try:
        try:
            binary = str(await ensure_cf_cli())
        except Exception as e:
            logger.warning("[Cloudflare] cf CLI install failed: %s", e)
            return _install_failed(str(e))

        # Fast path: a live login already exists (the user logged in from a
        # terminal, or a previous flow completed after the modal closed).
        info = await whoami_snapshot()
        if info:
            email = info.get("email")
            await _mark_connected(email)
            return {"success": True, "message": f"Already logged in{f' as {email}' if email else ''}."}

        proc = await _spawn_login(binary)
        _active_login.update(proc=proc, url=None, code=None)
        output: List[str] = []
        banner, eof = await _read_login_banner(proc, output)
        _spawn_background(_complete_login(proc, output))

        if banner:
            url, code = banner
            if _active_login["proc"] is proc:
                _active_login.update(url=url, code=code)
            logger.info(
                "[Cloudflare] device code issued (pid=%s) — opening on the frontend; awaiting approval in background",
                proc.pid,
            )
            return {
                "success": True,
                "url": url,
                "verification_code": code,
                "message": "Enter the code on the Cloudflare page that opened, then approve the request.",
            }
        if eof:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
        if proc.returncode is None:
            # cf is still starting (slow network); the pumps keep watching.
            return _pending("Cloudflare is still preparing the login code — click Login again in a few seconds.")
        tail = _tail(output)
        logger.warning("[Cloudflare] cf auth login exited (code=%s) before issuing a code: %s", proc.returncode, tail)
        return {"success": False, "error": f"Could not start the Cloudflare login. cf said: {tail}"}
    except Exception as e:
        logger.exception("[Cloudflare] login flow raised unexpectedly: %s", e)
        return {"success": False, "error": f"Cloudflare login failed: {e}"}


async def handle_cloudflare_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Single-flight + answer within the frontend's request window no
    matter what."""
    waiting = _waiting_login()
    if waiting:
        logger.info("[Cloudflare] login request while a device code awaits approval — re-sending it")
        return waiting
    if _login_in_progress():
        logger.info("[Cloudflare] login request ignored — a flow is already starting")
        return _pending("A Cloudflare login is already starting — click Login again in a few seconds to see the code.")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RESPONSE_BUDGET_SECONDS

    if cf_cli_path() is None:
        install = _install_in_background()
        # Continue to the login only while enough of the request window is
        # left for cf to print its code — otherwise the code would arrive
        # after this request has been answered.
        window = deadline - loop.time() - _BANNER_DEADLINE_SECONDS
        try:
            error = await asyncio.wait_for(asyncio.shield(install), timeout=max(window, 0.0))
        except asyncio.TimeoutError:
            logger.info("[Cloudflare] cf CLI install still running — answering pending")
            return _pending("Installing the Cloudflare CLI (the first install downloads about 300 MB) — click Login again when it finishes.")
        if error:
            return _install_failed(error)

    logger.info("[Cloudflare] login flow starting (cf auth login, device authorization)")
    flow = _spawn_background(_start_login_flow())
    _active_login["task"] = flow
    try:
        return await asyncio.wait_for(asyncio.shield(flow), timeout=max(deadline - loop.time(), 0.0))
    except asyncio.TimeoutError:
        logger.info("[Cloudflare] login still preparing after %ss — continuing in background", _RESPONSE_BUDGET_SECONDS)
        return _pending("Cloudflare is still preparing the login code — click Login again in a few seconds.")


async def _complete_login(proc: asyncio.subprocess.Process, output: List[str]) -> None:
    """Await cf's device-flow poll; gate success on ``cf auth whoami``
    (exit codes are not trusted — cf exits 0 either way); then the
    marker + broadcast.

    Never kills the process: the binary is bun's ``cf.exe`` launcher
    shim on Windows, and killing the launcher orphans the child bun
    process running the CLI. cf gives up on the device code itself.
    """
    try:
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "[Cloudflare] login still running after %ss — leaving it to cf's own device-code expiry",
                _LOGIN_TIMEOUT_SECONDS,
            )
            return

        info = await whoami_snapshot()
        if not info:
            logger.warning(
                "[Cloudflare] login exited (code=%s) but 'cf auth whoami' reports no login. CLI said: %s",
                returncode,
                _tail(output),
            )
            return

        await _mark_connected(info.get("email"))
    except Exception as e:
        logger.exception("[Cloudflare] login completion raised unexpectedly: %s", e)
    finally:
        if _active_login["proc"] is proc:
            _active_login.update(proc=None, url=None, code=None)


async def handle_cloudflare_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``cf auth logout`` (best-effort: revokes the OAuth token and
    deletes cf's default profile), drop the catalogue marker, broadcast
    so the modal flips immediately."""
    logger.info("[Cloudflare] logout starting")
    from ._service import resolve_cf_light

    binary = resolve_cf_light()
    result: Dict[str, Any] = {"success": True}
    if binary:
        result = await run_cli_command(
            binary=binary,
            argv=["auth", "logout"],
            timeout=20.0,
            env=login_env(),
            cwd=str(cf_workdir()),
        )
        if not result.get("success"):
            logger.warning("[Cloudflare] 'cf auth logout' failed (marker still removed): %s", result.get("error"))
            result = {"success": True, "message": "cf reported no stored login; catalogue marker removed"}
    await mark_logged_out("cloudflare")
    await broadcast_credential_event("credential.oauth.disconnected", provider="cloudflare")
    logger.info("[Cloudflare] logout complete: marker removed + catalogue broadcast sent")
    return result


async def handle_cloudflare_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Login snapshot straight from the CLI — no side effects."""
    info = await whoami_snapshot()
    connected = info is not None
    status: Dict[str, Any] = {"connected": connected, "logged_in": connected}
    if info and info.get("email"):
        status["email"] = info["email"]
    return {"success": True, "status": status}


WS_HANDLERS = {
    "cloudflare_login": handle_cloudflare_login,
    "cloudflare_logout": handle_cloudflare_logout,
    "cloudflare_status": handle_cloudflare_status,
}
