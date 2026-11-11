"""Copy JARVIS itself onto a device connected to this computer by cable.

What gets copied is JARVIS's committed source — exactly the files published to GitHub. `git`
decides what's included, so keys (.env), memory, paired devices and downloaded models can never
leak onto a drive or phone: they aren't tracked, so they aren't in the copy.

- a copy on a USB drive or SD card is ready to use on another computer: macOS or Linux (run ./run.sh)
  or Windows (run run.bat);
- a copy on an Android phone is a zip in the Download folder, for transport or backup. Phones run
  JARVIS as the Pocket web app (ui/), not from these files.
"""
import asyncio
import io
import os
import tarfile
import tempfile
import time
from pathlib import Path

from . import config
from .devices import ADB_INSTALL, DeviceError, _adb, _pick, adb_path, android_devices, external_drives

NAME = "Jarvis-ULTIMATE-2"


async def _git(*args: str, capture: bool = True) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(config.ROOT),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode:
        raise DeviceError("Couldn't package JARVIS: " + (err or out).decode(errors="replace").strip()[:200])
    return out


def _extract(tar: bytes, root: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(root, filter="data")   # refuses links or paths that leave the folder
        else:
            t.extractall(root)                   # older Python; the archive comes from our own git


async def to_drive(name: str | None) -> str:
    drives = external_drives()
    if not drives:
        raise DeviceError("No USB drive or SD card is connected. Plug one in and make sure it shows up in Finder, then try again.")
    want = (name or "").lower().strip()
    if want:
        hits = [d for d in drives if want in (d["name"].lower(), d["path"].lower())] or \
               [d for d in drives if want in d["name"].lower()]
        if not hits:
            raise DeviceError(f"No drive matches {name!r}. Connected drives: {', '.join(d['name'] for d in drives)}.")
    elif len(drives) > 1:
        return "Which drive should I copy JARVIS onto? Connected: " + ", ".join(d["name"] for d in drives)
    else:
        hits = drives
    drive = hits[0]
    root = Path(drive["path"])
    if not os.access(root, os.W_OK):
        raise DeviceError(f"{drive['name']} is read-only (NTFS drives are read-only on a Mac, for example). "
                          "Use another drive, or reformat this one as exFAT, which every system can write.")
    # Don't overwrite an existing copy.
    folder = NAME if not (root / NAME).exists() else f"{NAME}-{time.strftime('%Y%m%d-%H%M%S')}"
    tar = await _git("archive", "--format=tar", f"--prefix={folder}/", "HEAD")
    try:
        await asyncio.to_thread(lambda: _extract(tar, root))
    except OSError as e:
        raise DeviceError(f"Couldn't write to {drive['name']}: {e.strerror or e}")
    dest = root / folder
    if not dest.is_dir():
        raise DeviceError("The drive didn't receive the files; it may have run out of space or been unplugged.")
    count = sum(1 for p in dest.rglob("*") if p.is_file())
    return (f"Copied JARVIS onto {drive['name']} at {dest} ({count} files). On another computer, open that folder and "
            "run ./run.sh (macOS, Linux) or run.bat (Windows). You'll add your own .env with API keys there — it was "
            "deliberately left out.")


async def to_android(serial: str | None) -> str:
    if not adb_path():
        raise DeviceError(f"Android needs Google's adb, which isn't installed. Offer to install it with: {ADB_INSTALL}")
    serial = await _pick(serial)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / f"{NAME}.zip"
        await _git("archive", "--format=zip", f"--prefix={NAME}/", "-o", str(zip_path), "HEAD")
        size_kb = zip_path.stat().st_size // 1024
        await _adb("push", str(zip_path), "/sdcard/Download/", serial=serial, timeout=300)
    return (f"Copied JARVIS to the phone as /sdcard/Download/{NAME}.zip ({size_kb} KB) — in the Download folder, for "
            "transport or backup. On the phone itself, JARVIS runs as the Pocket web app.")


async def targets() -> dict:
    drives, phones = external_drives(), await android_devices()
    return {"drives": [d["name"] for d in drives],
            "android": [p.get("model") or p["serial"] for p in phones if p["wired"] and p["state"] == "device"]}


async def run(inp: dict) -> str:
    target = (inp.get("target") or "").lower().strip()
    if target == "drive":
        return await to_drive(inp.get("name"))
    if target == "android":
        return await to_android(inp.get("serial"))
    if target in ("", "list", "auto"):
        t = await targets()
        where = ["drive: " + d for d in t["drives"]] + ["android: " + a for a in t["android"]]
        if len(where) == 1:                       # only one place it could go: just do it
            return await (to_drive(None) if t["drives"] else to_android(None))
        if not where:
            raise DeviceError("Nothing to copy onto is connected. Plug in a USB drive, or an Android phone with USB "
                              "debugging turned on, then tell me where to put JARVIS.")
        return "Where should I put JARVIS? Connected: " + ", ".join(where)
    raise DeviceError(f"Unknown target {target!r}; use 'drive' or 'android'.")
