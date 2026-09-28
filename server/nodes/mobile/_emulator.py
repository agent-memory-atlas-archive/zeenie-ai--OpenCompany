"""Verified emulator recovery and a process-wide lease for one writable AVD."""

import asyncio
import os
import re
from pathlib import Path

import psutil

from ._control import MobileError
from ._process import command


class DeviceLock:
    def __init__(self, root: Path):
        self.file = (root / "device.lock").open("a+b")
        try:
            self.file.seek(0, 2)
            if not self.file.tell():
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise MobileError("device_in_use", "Another OpenCompany backend is using this phone. Stop that backend before trying again.") from None

    def close(self):
        self.file.close()  # The OS releases the lock, including after a crash.


class RecoveredProcess:
    """Small asyncio.Process-compatible handle with psutil PID-reuse protection."""

    def __init__(self, process):
        self.process = process
        self.pid = process.pid

    @property
    def returncode(self):
        return None if self.process.is_running() else 0

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.2)
        return self.returncode

    def kill(self):
        try:
            self.process.kill()
        except psutil.NoSuchProcess:
            pass


def owned_children(pid):
    if not isinstance(pid, int) or pid <= 0:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except (psutil.Error, TypeError):
        return []


async def stop_children(children):
    for child in children:
        try:
            child.kill()  # psutil checks process identity before signalling.
        except psutil.NoSuchProcess:
            pass
    _, alive = await asyncio.to_thread(psutil.wait_procs, children, timeout=5)
    if alive:
        raise MobileError("stop_failed", "Android is still closing. Wait a moment and retry Stop phone.")


async def recover_emulator(adb: Path, emulator: Path, avd: Path):
    """Require both the console's exact AVD path and an SDK process match."""
    output = await command([str(adb), "devices"], timeout=10)
    matches = []
    for line in output.splitlines():
        match = re.fullmatch(r"(emulator-(\d+))\s+device", line.strip())
        if not match:
            continue
        serial, port = match.groups()
        try:
            identity = await command([str(adb), "-s", serial, "emu", "avd", "path"], timeout=5)
            paths = [s.strip() for s in identity.splitlines() if s.strip() and s.strip() != "OK"]
            if len(paths) != 1 or Path(paths[0]).resolve() != avd.resolve():
                continue
            for proc in psutil.process_iter(["exe", "cmdline"]):
                try:
                    args = proc.info["cmdline"] or []
                    exe = Path(proc.info["exe"] or "").resolve()
                    if not exe.is_relative_to(emulator.parent.resolve()):
                        continue
                    if "-port" in args and args[args.index("-port") + 1] == port and exe.name.lower().startswith("qemu-system"):
                        matches.append((RecoveredProcess(proc), serial))
                except (psutil.Error, IndexError, OSError):
                    continue
        except (OSError, RuntimeError, TimeoutError):
            continue
    if len(matches) > 1:
        raise MobileError("device_conflict", "More than one process owns this phone. Close the duplicate emulator before retrying.")
    return matches[0] if matches else None


def boot_error(log: Path, offset: int, returncode) -> MobileError:
    try:
        with log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(offset, stream.tell() - 16384))
            tail = stream.read().decode("utf-8", "replace").lower()
    except OSError:
        tail = ""
    if "multiple emulators with the same avd" in tail or "already running" in tail:
        return MobileError("device_in_use", "This phone is already open, but could not be safely reconnected. Close the existing OpenCompany emulator and try Start phone again. Your apps and data are preserved.")
    if "not enough" in tail and ("disk" in tail or "space" in tail):
        return MobileError("disk_full", "Android could not start because disk space is low. Free space on the phone's drive and try again.")
    if "acceleration" in tail and ("required" in tail or "not installed" in tail):
        return MobileError("acceleration_unavailable", "Android hardware acceleration is unavailable. Open Host readiness for setup instructions, then restart the computer.")
    return MobileError("boot_failed", f"Android closed during startup (exit code {returncode}). Try Start phone again. Diagnostic details: {log}")
