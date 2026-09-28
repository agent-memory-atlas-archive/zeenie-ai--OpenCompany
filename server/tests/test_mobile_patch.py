"""Optional real-upstream compatibility check; no downloads during tests."""

import ast
import os
import shutil
from pathlib import Path

import pytest

from nodes.mobile.runtime.patch_source import PATCH_VERSION, patch


@pytest.fixture
def patched_source(tmp_path):
    default = Path(__file__).resolve().parents[2] / ".tmp" / "mobile-use-upstream"
    upstream = Path(os.environ.get("MOBILE_USE_UPSTREAM_SOURCE", default))
    if not (upstream / "minitap/mobile_use/sdk/agent.py").is_file():
        pytest.skip("Set MOBILE_USE_UPSTREAM_SOURCE to the pinned upstream checkout")
    shutil.copytree(upstream / "minitap", tmp_path / "minitap")
    patch(tmp_path)
    return tmp_path


def test_patch_applies_to_upstream_and_every_python_file_compiles(patched_source):
    assert (patched_source / ".opencompany-patch").read_text() == str(PATCH_VERSION)
    for source in (patched_source / "minitap").rglob("*.py"):
        compile(source.read_text(encoding="utf-8"), str(source), "exec")


def test_injected_branches_cover_native_bypasses(patched_source):
    base = patched_source / "minitap/mobile_use"
    expected = {
        "sdk/agent.py": ["_init_internal", "_install_apk_internal", "_install_ios_app", "get_screenshot", "clean"],
        "controllers/controller_factory.py": ["create_device_controller"],
        "controllers/platform_specific_commands_controller.py": [
            "get_device_date",
            "list_packages",
            "list_packages_async",
            "get_current_foreground_package",
            "get_current_foreground_package_async",
        ],
    }
    for file, names in expected.items():
        tree = ast.parse((base / file).read_text(encoding="utf-8"))
        for name in names:
            function = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
            body = function.body
            if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            assert isinstance(body[0], ast.If), (file, name)
            assert "local_controller" in ast.unparse(body[0].test), (file, name)
    for file in ("config.py", "sdk/agent.py"):
        tree = ast.parse((base / file).read_text(encoding="utf-8"))
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "load_dotenv" for n in ast.walk(tree))


def test_patch_refuses_second_application(patched_source):
    with pytest.raises(ValueError, match="compatibility mismatch"):
        patch(patched_source)
