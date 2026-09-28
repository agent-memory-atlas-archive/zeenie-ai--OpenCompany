"""Build-environment helpers shared by ``start`` / ``serve`` / ``dev``.

``validate_build`` refuses to launch when ``company build`` hasn't been
run. Layout knowledge lives in :mod:`cli.platform_` -- this file only
composes the documented prefixes (workspace venv, node_modules, client
dist) into a boolean check.

The long-running backend (``start`` / ``serve`` / ``dev`` / ``daemon``)
runs the server venv's interpreter directly via
:func:`cli.platform_.server_venv_python`, not ``uv run``, so no resident
``uv`` parent sits above it. :func:`cli.run.uv_run` remains for the
one-shot steps of ``company build``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from cli.colors import console
from cli.platform_ import (
    client_dist_entry,
    node_modules_dir,
    server_venv,
)


def validate_build(root: Path, *, require_client_dist: bool = False) -> None:
    """Refuse to launch if ``company build`` hasn't been run.

    Raises ``typer.Exit(1)`` with a remediation hint. ``dev`` allows a
    missing ``client/dist`` (Vite serves the source); ``start`` and
    ``serve`` opt in via ``require_client_dist=True``.

    Requiring ``node_modules`` for every caller is the open registry-install
    bug in docs-internal/errors.md #25: a ``bun add -g`` install has no
    ``node_modules`` next to the package, and only ``dev`` (Vite) needs it.
    """
    if not node_modules_dir(root).exists() or not server_venv(root).exists():
        console.print('[red]Error: Project not built. Run "company build" first.[/]')
        raise typer.Exit(code=1)
    if require_client_dist and not client_dist_entry(root).exists():
        console.print('[red]Error: Client not built. Run "company build" first.[/]')
        raise typer.Exit(code=1)
