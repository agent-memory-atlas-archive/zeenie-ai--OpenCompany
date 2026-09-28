"""Port relationships the committed env files must keep.

Port numbers live only in ``.env.template`` and the dev-only ``.env.dev``,
and nothing else ties those literals together, so a change to one file can
silently break a mode the author did not run. Three relationships are
load-bearing:

- The backend binds ``PYTHON_BACKEND_PORT`` (``build_backend_spec`` passes it
  as uvicorn's ``--port``), while code that dials the backend from inside it
  reads ``PORT`` (``Settings.port``): the Temporal workers' ``/ws/internal``
  socket and broadcast URL, and the Stripe daemon's ``--forward-to``.
  ``company start`` and ``company dev`` do not set ``PORT`` for the child,
  so each file must set both, to the same value.
- ``company dev`` runs Vite on ``VITE_CLIENT_PORT`` and moves the backend
  behind it with ``.env.dev``, so the dev backend port must differ from it.
- The app URL is the same in dev and production (``client/vite.config.js``):
  OAuth apps are registered with that one callback URL. So the template's
  ``VITE_CLIENT_PORT`` equals its ``PYTHON_BACKEND_PORT``.
"""

from __future__ import annotations

import pytest

from cli.config import _load_env_file
from cli.platform_ import project_root

ROOT = project_root()
TEMPLATE = _load_env_file(ROOT / ".env.template")
DEV = _load_env_file(ROOT / ".env.dev")


@pytest.mark.parametrize("name,env", [(".env.template", TEMPLATE), (".env.dev", DEV)])
def test_bind_port_matches_self_dial_port(name: str, env: dict[str, str]) -> None:
    assert "PORT" in env and "PYTHON_BACKEND_PORT" in env, f"{name} must set both PORT and PYTHON_BACKEND_PORT"
    assert env["PORT"] == env["PYTHON_BACKEND_PORT"], (
        f"{name}: PORT={env['PORT']} but PYTHON_BACKEND_PORT={env['PYTHON_BACKEND_PORT']}. "
        "uvicorn binds PYTHON_BACKEND_PORT, while the workers and the Stripe daemon dial PORT."
    )


def test_dev_backend_port_leaves_the_app_port_to_vite() -> None:
    app_port = TEMPLATE["VITE_CLIENT_PORT"]
    assert DEV["PYTHON_BACKEND_PORT"] != app_port, (
        f".env.dev puts the backend on the app port {app_port}, where company dev runs Vite."
    )


def test_app_port_is_the_same_in_dev_and_production() -> None:
    assert TEMPLATE["VITE_CLIENT_PORT"] == TEMPLATE["PYTHON_BACKEND_PORT"], (
        ".env.template: the dev app port (VITE_CLIENT_PORT) must equal the production app port "
        "(PYTHON_BACKEND_PORT), or OAuth callback URLs differ between the two modes."
    )
