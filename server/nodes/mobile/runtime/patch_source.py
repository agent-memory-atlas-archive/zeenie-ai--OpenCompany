"""Auditable source patch for the pinned mobile-use archive; not runtime monkeypatching."""

from __future__ import annotations
import ast
from pathlib import Path

PATCH_VERSION = 1


def patch(root: Path) -> None:
    base = root / "minitap" / "mobile_use"

    def replace(file: str, old: str, new: str) -> None:
        path = base / file
        source = path.read_text(encoding="utf-8")
        if source.count(old) != 1:
            raise ValueError(f"Upstream compatibility mismatch: {file}: {old[:60]}")
        path.write_text(source.replace(old, new), encoding="utf-8")

    def prepend(file: str, name: str, body: str) -> None:
        path = base / file
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        matches = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one {name} in {file}")
        statements = matches[0].body
        first = (
            statements[1]
            if isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
            else statements[0]
        )
        lines = source.splitlines(keepends=True)
        indent = " " * first.col_offset
        lines.insert(first.lineno - 1, "".join(indent + line + "\n" for line in body.splitlines()))
        path.write_text("".join(lines), encoding="utf-8")

    replace("sdk/types/agent.py", "from typing import Literal", "from typing import Any, Literal")
    replace(
        "sdk/types/agent.py",
        "    agent_profiles: dict[str, AgentProfile]",
        "    local_controller: Any = None\n    local_device_context: Any = None\n    agent_profiles: dict[str, AgentProfile]",
    )
    replace("context.py", "from typing import Literal", "from typing import Any, Literal")
    replace("context.py", "    trace_id: str", "    local_controller: Any = None\n    trace_id: str")
    replace("context.py", 'Literal["WINDOWS", "LINUX"]', 'Literal["WINDOWS", "LINUX", "MACOS"]')
    prepend(
        "sdk/agent.py",
        "_init_internal",
        "if self._config.local_controller is not None:\n    self._device_context = self._config.local_device_context\n    self._adb_client = self._ui_adb_client = self._ios_client = None\n    self._cloud_controller = None\n    self._initialized = True\n    return True",
    )
    replace(
        "sdk/agent.py",
        "        context = MobileUseContext(\n",
        "        context = MobileUseContext(\n            local_controller=self._config.local_controller,\n",
    )
    prepend(
        "controllers/controller_factory.py",
        "create_device_controller",
        "if ctx.local_controller is not None:\n    return ctx.local_controller",
    )
    for name, operation, asynchronous in [
        ("get_device_date", "date", False),
        ("list_packages", "packages", False),
        ("list_packages_async", "packages", True),
        ("get_current_foreground_package", "foreground", False),
        ("get_current_foreground_package_async", "foreground", True),
    ]:
        call = f'await ctx.local_controller.read("{operation}")' if asynchronous else f'ctx.local_controller.read_sync("{operation}")'
        prepend("controllers/platform_specific_commands_controller.py", name, f"if ctx.local_controller is not None:\n    return {call}")
    prepend(
        "sdk/agent.py",
        "_install_apk_internal",
        "if self._config.local_controller is not None:\n    raise RuntimeError('Install applications through OpenCompany Workspace, not an agent task')",
    )
    prepend(
        "sdk/agent.py",
        "_install_ios_app",
        "if self._config.local_controller is not None:\n    raise RuntimeError('Install applications through OpenCompany Workspace')",
    )
    prepend(
        "sdk/agent.py",
        "get_screenshot",
        "if self._config.local_controller is not None:\n    import base64\n    return Image.open(BytesIO(base64.b64decode(await self._config.local_controller.screenshot())))",
    )
    prepend(
        "sdk/agent.py",
        "clean",
        "if self._config.local_controller is not None:\n    await self._config.local_controller.cleanup()\n    self._initialized = False\n    return",
    )
    prepend(
        "clients/ui_automator_client.py",
        "_ensure_maestro_not_installed",
        "if _is_package_installed(device_id, MAESTRO_PACKAGE):\n    raise RuntimeError('Maestro conflicts with this automation driver. Remove its helper manually or use another device.')\nreturn",
    )
    replace("config.py", '"env_file": ".env"', '"env_file": None')
    # Both SDK and config call load_dotenv; remove discovery even if dependency
    # versions change the meaning of PYTHON_DOTENV_DISABLED.
    for name in ("config.py", "sdk/agent.py"):
        path = base / name
        text = path.read_text(encoding="utf-8")
        text = text.replace("load_dotenv(verbose=True)", "# OpenCompany supplies explicit process configuration.").replace(
            "load_dotenv()", "# OpenCompany supplies explicit process configuration."
        )
        ast.parse(text)
        path.write_text(text, encoding="utf-8")
    (root / ".opencompany-patch").write_text(str(PATCH_VERSION), encoding="ascii")
