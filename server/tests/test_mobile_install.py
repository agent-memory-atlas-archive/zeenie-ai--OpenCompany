"""Optional installation boundaries; no network or SDK installation."""

import hashlib
import io
import zipfile
from unittest.mock import AsyncMock

import pytest

from nodes.mobile import _install as install
from nodes.mobile._control import MobileError
from nodes.mobile._runtime import MobileRuntime


def test_bad_download_checksum_preserves_previous_target(monkeypatch, tmp_path):
    target = tmp_path / "artifact.zip"
    target.write_bytes(b"previous verified artifact")
    monkeypatch.setattr(install.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"corrupt"))
    with pytest.raises(ValueError, match="checksum"):
        install.download("https://example.invalid/artifact", target, hashlib.sha256(b"expected").hexdigest())
    assert target.read_bytes() == b"previous verified artifact"
    assert not target.with_suffix(".zip.part").exists()


@pytest.mark.parametrize("filename,mode", [("../outside", 0o100644), ("safe/link", 0o120777)])
def test_archive_rejects_traversal_and_symlinks(tmp_path, filename, mode):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("ordinary", "must not extract before validation")
        entry = zipfile.ZipInfo(filename)
        entry.create_system = 3
        entry.external_attr = mode << 16
        source.writestr(entry, "target")
    destination = tmp_path / "unpacked"
    with pytest.raises(ValueError, match="Unsafe"):
        install.extract_zip(archive, destination)
    assert not (destination / "ordinary").exists()
    assert not (tmp_path / "outside").exists()


async def test_declining_license_never_installs(monkeypatch):
    tools = AsyncMock()
    monkeypatch.setattr(install.sys, "platform", "win32")
    monkeypatch.setattr(install.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(install, "install_sdk_tools", tools)
    with pytest.raises(ValueError, match="license"):
        await install.create_device(licenses_accepted=False)
    tools.assert_not_awaited()
    runtime = MobileRuntime()
    with pytest.raises(MobileError, match="license"):
        runtime.setup(False)
    assert runtime.setup_task is None


@pytest.mark.skipif(install.os.name != "nt", reason="Windows Java launcher contract")
def test_sdk_launch_uses_java_argv_without_command_shell(monkeypatch, tmp_path):
    java = tmp_path / "java" / "jdk-test" / "bin" / "java.exe"
    java.parent.mkdir(parents=True)
    java.touch()
    script = tmp_path / "sdk with spaces & symbols" / "bin" / "sdkmanager.bat"
    monkeypatch.setattr(install, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(install, "sdk_tool", lambda _name: script)
    args = install.sdk_command("sdkmanager", "--sdk_root=C:\\SDK with spaces", install.IMAGE)
    assert args[0] == str(java)
    assert "com.android.sdklib.tool.sdkmanager.SdkManagerCli" in args
    assert args[-1] == install.IMAGE
    assert not any(arg.lower().endswith(("cmd.exe", ".bat")) for arg in args)


def test_incomplete_environment_is_not_ready(monkeypatch, tmp_path):
    from nodes.mobile import _install
    from nodes.mobile.runtime.patch_source import PATCH_VERSION
    import json

    executable = tmp_path / "python.exe"
    executable.touch()
    monkeypatch.setattr(_install, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(_install, "runtime_python", lambda: executable)
    assert not _install.engine_ready()
    (tmp_path / "engine-ready.json").write_text(json.dumps({"source": _install.UPSTREAM_SHA, "patch": PATCH_VERSION}))
    assert _install.engine_ready()
    (tmp_path / "engine-ready.json").write_text(json.dumps({"source": "old", "patch": PATCH_VERSION}))
    assert not _install.engine_ready()
