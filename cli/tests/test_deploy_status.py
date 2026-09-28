"""``company deploy status`` URL fallback."""

from __future__ import annotations

import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import typer

from cli.commands.deploy import status


def test_status_falls_back_to_configured_port(tmp_path) -> None:
    """No saved port and no Terraform ``url`` output: the URL is built from the
    backend port in the loaded config. This path used to raise NameError."""
    printed: list[str] = []

    def fake_output(_wd, name):
        return "203.0.113.7" if name == "external_ip" else None

    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("unreachable")

    with (
        patch.object(status, "preflight", return_value=(SimpleNamespace(backend_port=4321), tmp_path)),
        patch.object(status._state, "read_meta", return_value={"provider": "gcp"}),
        patch.object(status._state, "workdir", return_value=tmp_path),
        patch.object(status._state, "resource_name", return_value="opencompany"),
        patch.object(status._terraform, "tf_output", side_effect=fake_output),
        patch.object(status.urllib.request, "urlopen", side_effect=refuse),
        patch.object(status.console, "print", side_effect=lambda msg="": printed.append(str(msg))),
        pytest.raises(typer.Exit) as exc,
    ):
        status.status_command()

    assert exc.value.exit_code == 1
    assert any("http://203.0.113.7:4321" in line for line in printed)
