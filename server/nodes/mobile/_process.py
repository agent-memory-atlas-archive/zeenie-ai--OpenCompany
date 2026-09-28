"""Bounded, shell-free optional runtime subprocesses."""

from __future__ import annotations
import asyncio
import os
import subprocess


def hidden_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


async def command(
    argv: list[str],
    *,
    timeout: float = 30,
    env: dict | None = None,
    cwd: str | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *map(str, argv),
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=cwd,
        **hidden_options(),
    )
    try:
        data, _ = await asyncio.wait_for(proc.communicate(input_text.encode() if input_text is not None else None), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    text = data.decode("utf-8", "replace")
    if check and proc.returncode:
        raise RuntimeError(f"{os.path.basename(str(argv[0]))} failed ({proc.returncode}): {text[-1500:]}")
    return text
