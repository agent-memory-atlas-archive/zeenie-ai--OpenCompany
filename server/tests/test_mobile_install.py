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


def _package(root, package, revision="37.1.11", dependency="35.4.9"):
    path = root.joinpath(*package.split(";"))
    path.mkdir(parents=True, exist_ok=True)
    props = f"Pkg.Revision={revision}\n"
    if package == install.IMAGE:
        props += f"AndroidVersion.ApiLevel=36\nSystemImage.Abi=x86_64\nSystemImage.TagId=google_apis_playstore\nPkg.Dependencies=emulator#{dependency}\n"
        files = ("system.img", "vendor.img", "ramdisk.img", "kernel-ranchu")
    elif package == "emulator":
        files = ("emulator.exe", "qemu/windows-x86_64/qemu-system-x86_64.exe")
    else:
        files = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")
    (path / "source.properties").write_text(props)
    for name in files:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"installed")
    return path


def test_complete_packages_are_reused_but_incomplete_files_are_not(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "sdk_root", lambda: tmp_path)
    for package in ("platform-tools", install.IMAGE, "emulator"):
        _package(tmp_path, package)
        assert install.sdk_package_ready(package)
    (tmp_path / "platform-tools" / "AdbWinApi.dll").unlink()
    assert not install.sdk_package_ready("platform-tools")
    image = tmp_path.joinpath(*install.IMAGE.split(";"))
    (image / "system.img").write_bytes(b"")
    assert not install.sdk_package_ready(install.IMAGE)


def test_emulator_minimum_tracks_installed_image_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(install, "sdk_root", lambda: tmp_path)
    _package(tmp_path, "emulator", revision="35.4.8")
    assert not install.sdk_package_ready("emulator")
    _package(tmp_path, "emulator", revision="37.1.11")
    assert install.sdk_package_ready("emulator")
    _package(tmp_path, install.IMAGE, dependency="38.0.1")
    assert not install.sdk_package_ready("emulator")


@pytest.mark.parametrize("missing", [None, install.IMAGE])
async def test_setup_only_installs_missing_packages_and_preserves_avd(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(install.sys, "platform", "win32")
    monkeypatch.setattr(install.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(install, "mobile_root", lambda: tmp_path)
    monkeypatch.setattr(install, "sdk_root", lambda: tmp_path / "sdk")
    tools = AsyncMock()
    monkeypatch.setattr(install, "install_sdk_tools", tools)
    ready = {p for p in ("platform-tools", install.IMAGE, "emulator") if p != missing}
    monkeypatch.setattr(install, "sdk_package_ready", lambda p: p in ready)
    monkeypatch.setattr(install, "sdk_command", lambda name, *args: [name, *args])
    avd = tmp_path / "avd"
    avd.mkdir()
    (avd / f"{install.AVD_NAME}.ini").write_text("persistent-device")

    async def execute(argv, **kwargs):
        assert argv[0] == "sdkmanager"
        assert kwargs["timeout"] == 7200
        ready.add(argv[-1])
        kwargs["progress"]("Installing 50%")

    command = AsyncMock(side_effect=execute)
    monkeypatch.setattr(install, "command", command)
    events = []
    await install.create_device(licenses_accepted=True, progress=events.append)
    assert command.await_count == int(missing is not None)
    if missing:
        assert command.await_args.args[0][-1] == missing
        assert "Installing 50%" in events
    assert (avd / f"{install.AVD_NAME}.ini").read_text() == "persistent-device"
    assert events[-1].startswith("Android setup complete")
    tools.assert_awaited_once_with(progress=events.append)


async def test_download_progress_callback_runs_on_event_loop_thread(monkeypatch, tmp_path):
    import asyncio
    import threading

    loop_thread = threading.get_ident()
    worker_threads = []
    observed = []

    def download(*_, progress, **kwargs):
        worker_threads.append(threading.get_ident())
        progress("Downloading 50%")

    monkeypatch.setattr(install, "download", download)
    await install._download(
        "https://example.invalid", tmp_path / "artifact", "digest", lambda message: observed.append((threading.get_ident(), message))
    )
    await asyncio.sleep(0)
    assert worker_threads[0] != loop_thread
    assert observed == [(loop_thread, "Downloading 50%")]
