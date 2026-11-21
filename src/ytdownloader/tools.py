"""Locate, install and update the external tools used by the app.

Three binaries are involved:

* ``yt-dlp``  - the downloader itself (official standalone build, self-updatable with ``-U``);
* ``deno``    - JavaScript runtime that yt-dlp needs to fully support YouTube;
* ``ffmpeg``  - merges video+audio streams, converts audio, embeds subtitles.

Managed copies live in ``<data dir>/bin`` and take precedence over the ``PATH``: a system
yt-dlp (apt, pip...) is often outdated and cannot be updated without root.

This module has no Qt dependency so it can be used from the command line::

    python -m ytdownloader.tools --check
    python -m ytdownloader.tools --install all
    python -m ytdownloader.tools --update
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "ytdownloader"
DATA_DIR_ENV = "YTDOWNLOADER_DATA_DIR"

YT_DLP = "yt-dlp"
DENO = "deno"
FFMPEG = "ffmpeg"
TOOL_NAMES = (YT_DLP, DENO, FFMPEG)

ProgressCallback = Callable[[int, int | None], None]
"""Called with (downloaded bytes, total bytes or None) while fetching a tool."""

_GITHUB = "https://github.com"
_YTDLP_URL = f"{_GITHUB}/yt-dlp/yt-dlp/releases/latest/download/"
_DENO_URL = f"{_GITHUB}/denoland/deno/releases/latest/download/"
_FFMPEG_URL = f"{_GITHUB}/yt-dlp/FFmpeg-Builds/releases/download/latest/"


class ToolError(RuntimeError):
    """Raised when a tool cannot be installed, updated or run."""


@dataclass(frozen=True)
class Tool:
    name: str
    path: Path
    version: str
    managed: bool
    """True when the binary lives in the app data dir (and can be updated by the app)."""


@dataclass(frozen=True)
class Platform:
    os: str  # "windows" | "linux" | "macos"
    arch: str  # "x86_64" | "aarch64"

    @property
    def exe_suffix(self) -> str:
        return ".exe" if self.os == "windows" else ""


def current_platform() -> Platform:
    if sys.platform.startswith("win"):
        os_name = "windows"
    elif sys.platform == "darwin":
        os_name = "macos"
    else:
        os_name = "linux"
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("aarch64", "arm64") else "x86_64"
    return Platform(os_name, arch)


def data_dir() -> Path:
    """Per-user data directory of the app (override with $YTDOWNLOADER_DATA_DIR)."""
    if override := os.environ.get(DATA_DIR_ENV):
        return Path(override)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def bin_dir() -> Path:
    return data_dir() / "bin"


def managed_path(name: str, plat: Platform | None = None) -> Path:
    plat = plat or current_platform()
    return bin_dir() / f"{name}{plat.exe_suffix}"


# --------------------------------------------------------------------------- discovery


def _run_version(path: Path, name: str) -> str | None:
    arg = "-version" if name == FFMPEG else "--version"
    try:
        result = subprocess.run(
            [str(path), arg],
            capture_output=True,
            text=True,
            timeout=30,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return parse_version(name, result.stdout)


def parse_version(name: str, output: str) -> str:
    """Extract a short version string from ``<tool> --version`` output."""
    first = output.strip().splitlines()[0].strip() if output.strip() else ""
    parts = first.split()
    if name == FFMPEG and len(parts) >= 3 and parts[0] == "ffmpeg" and parts[1] == "version":
        return parts[2]  # "ffmpeg version 8.0.1-3ubuntu2 Copyright ..."
    if name == DENO and len(parts) >= 2 and parts[0] == "deno":
        return parts[1]  # "deno 2.5.6 (stable, release, x86_64-unknown-linux-gnu)"
    return first


def find_tool(name: str) -> Tool | None:
    """Return the tool to use: the managed copy first, then whatever is on the PATH."""
    candidates: list[tuple[Path, bool]] = []
    managed = managed_path(name)
    if managed.is_file():
        candidates.append((managed, True))
    if found := shutil.which(name):
        candidates.append((Path(found), False))
    for path, is_managed in candidates:
        if version := _run_version(path, name):
            return Tool(name, path, version, is_managed)
    return None


def find_all() -> dict[str, Tool | None]:
    return {name: find_tool(name) for name in TOOL_NAMES}


# --------------------------------------------------------------------------- installation


def download_url(name: str, plat: Platform | None = None) -> str:
    """URL of the latest official build of ``name`` for the given platform."""
    plat = plat or current_platform()
    if name == YT_DLP:
        asset = {
            ("windows", "x86_64"): "yt-dlp.exe",
            ("windows", "aarch64"): "yt-dlp_arm64.exe",
            ("linux", "x86_64"): "yt-dlp_linux",
            ("linux", "aarch64"): "yt-dlp_linux_aarch64",
            ("macos", "x86_64"): "yt-dlp_macos",
            ("macos", "aarch64"): "yt-dlp_macos",
        }[(plat.os, plat.arch)]
        return _YTDLP_URL + asset
    if name == DENO:
        target = {
            "windows": "pc-windows-msvc",
            "linux": "unknown-linux-gnu",
            "macos": "apple-darwin",
        }[plat.os]
        return f"{_DENO_URL}deno-{plat.arch}-{target}.zip"
    if name == FFMPEG:
        asset = {
            ("windows", "x86_64"): "ffmpeg-master-latest-win64-gpl.zip",
            ("windows", "aarch64"): "ffmpeg-master-latest-winarm64-gpl.zip",
            ("linux", "x86_64"): "ffmpeg-master-latest-linux64-gpl.tar.xz",
            ("linux", "aarch64"): "ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
        }.get((plat.os, plat.arch))
        if asset is None:
            raise ToolError("No ffmpeg build available for macOS: install it with Homebrew.")
        return _FFMPEG_URL + asset
    raise ValueError(f"Unknown tool: {name}")


def _fetch(url: str, dest: Path, progress: ProgressCallback | None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}-tool-installer"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, dest.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0) or None
            done = 0
            while chunk := response.read(256 * 1024):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except OSError as exc:  # URLError is an OSError
        raise ToolError(f"Download failed ({url}): {exc}") from exc


def _extract_member(archive: Path, wanted: str, dest: Path) -> None:
    """Extract the file whose basename is ``wanted`` from a zip / tar archive to ``dest``."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if not info.is_dir() and Path(info.filename).name == wanted:
                    with zf.open(info) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    return
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.isfile() and Path(member.name).name == wanted:
                    src = tf.extractfile(member)
                    assert src is not None
                    with src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    return
    raise ToolError(f"{wanted} not found in {archive.name}")


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install(name: str, progress: ProgressCallback | None = None) -> Tool:
    """Download the latest official build of ``name`` into the managed bin dir."""
    plat = current_platform()
    url = download_url(name, plat)
    target_dir = bin_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # Binaries to put in bin/ : ffmpeg comes with ffprobe, used by yt-dlp for post-processing.
    wanted = [name] + (["ffprobe"] if name == FFMPEG else [])

    with tempfile.TemporaryDirectory(dir=target_dir, prefix=".install-") as tmp:
        tmp_dir = Path(tmp)
        download = tmp_dir / url.rsplit("/", 1)[1]
        _fetch(url, download, progress)

        staged: list[tuple[Path, Path]] = []
        for binary in wanted:
            filename = f"{binary}{plat.exe_suffix}"
            if name == YT_DLP:
                extracted = download  # bare executable, no archive
            else:
                extracted = tmp_dir / f"extracted-{filename}"
                _extract_member(download, filename, extracted)
            _make_executable(extracted)
            staged.append((extracted, target_dir / filename))

        for src, dest in staged:
            os.replace(src, dest)  # atomic: a half-downloaded binary never replaces a good one

    tool = find_tool(name)
    if tool is None or not tool.managed:
        raise ToolError(f"{name} was installed in {target_dir} but does not run.")
    return tool


def update_yt_dlp(tool: Tool) -> str:
    """Self-update a managed yt-dlp binary; return its output."""
    if not tool.managed:
        raise ToolError(
            f"{tool.path} is not managed by the app; install a managed copy to enable updates."
        )
    result = subprocess.run(
        [str(tool.path), "-U"],
        capture_output=True,
        text=True,
        timeout=300,
        **_no_window_kwargs(),
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise ToolError(output or f"yt-dlp -U exited with code {result.returncode}")
    return output


def missing_tool_hint(name: str, plat: Platform | None = None) -> str:
    """Human-readable advice when a tool is missing."""
    plat = plat or current_platform()
    if name == FFMPEG and plat.os == "linux":
        return "Install it with: sudo apt install ffmpeg (or let the app download a static build)."
    if name == FFMPEG and plat.os == "macos":
        return "Install it with: brew install ffmpeg"
    return "The app can download the official build for you."


def _no_window_kwargs() -> dict:
    """Avoid flashing a console window for each subprocess on Windows."""
    if sys.platform.startswith("win"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]
    return {}


# --------------------------------------------------------------------------- CLI


def _print_progress(done: int, total: int | None) -> None:
    if total:
        print(f"\r  {done * 100 // total:3d} % ({done / 1e6:.1f} / {total / 1e6:.1f} MB)", end="")
    else:
        print(f"\r  {done / 1e6:.1f} MB", end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ytdownloader.tools", description=__doc__)
    parser.add_argument("--check", action="store_true", help="show the tools found")
    parser.add_argument("--install", choices=[*TOOL_NAMES, "all"], help="download a tool")
    parser.add_argument("--update", action="store_true", help="update the managed yt-dlp")
    args = parser.parse_args(argv)

    if args.install:
        for name in TOOL_NAMES if args.install == "all" else (args.install,):
            print(f"Installing {name} from {download_url(name)}")
            tool = install(name, _print_progress)
            print(f"\n  -> {tool.path} ({tool.version})")
    if args.update:
        tool = find_tool(YT_DLP)
        if tool is None:
            print("yt-dlp not found.")
            return 1
        print(update_yt_dlp(tool))
    if args.check or not (args.install or args.update):
        print(f"Data dir: {data_dir()}")
        for name, tool in find_all().items():
            if tool:
                origin = "managed" if tool.managed else "system"
                print(f"  {name:7} {tool.version:30} {origin:8} {tool.path}")
            else:
                print(f"  {name:7} MISSING - {missing_tool_hint(name)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ToolError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
