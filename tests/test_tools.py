import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from ytdownloader import tools
from ytdownloader.tools import Platform

WIN = Platform("windows", "x86_64")
LINUX = Platform("linux", "x86_64")
LINUX_ARM = Platform("linux", "aarch64")
MAC = Platform("macos", "aarch64")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(tools.DATA_DIR_ENV, str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- paths


def test_data_dir_override(data_dir):
    assert tools.data_dir() == data_dir
    assert tools.bin_dir() == data_dir / "bin"


def test_managed_path_suffix(data_dir):
    assert tools.managed_path("yt-dlp", WIN).name == "yt-dlp.exe"
    assert tools.managed_path("yt-dlp", LINUX).name == "yt-dlp"


# --------------------------------------------------------------------------- download URLs


@pytest.mark.parametrize(
    ("name", "plat", "suffix"),
    [
        ("yt-dlp", WIN, "yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"),
        ("yt-dlp", LINUX, "yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux"),
        ("yt-dlp", LINUX_ARM, "yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux_aarch64"),
        ("deno", WIN, "deno-x86_64-pc-windows-msvc.zip"),
        ("deno", LINUX, "deno-x86_64-unknown-linux-gnu.zip"),
        ("deno", MAC, "deno-aarch64-apple-darwin.zip"),
        ("ffmpeg", WIN, "ffmpeg-master-latest-win64-gpl.zip"),
        ("ffmpeg", LINUX, "ffmpeg-master-latest-linux64-gpl.tar.xz"),
    ],
)
def test_download_url(name, plat, suffix):
    url = tools.download_url(name, plat)
    assert url.startswith("https://github.com/")
    assert url.endswith(suffix)


def test_no_ffmpeg_build_for_macos():
    with pytest.raises(tools.ToolError):
        tools.download_url("ffmpeg", MAC)


# --------------------------------------------------------------------------- versions


@pytest.mark.parametrize(
    ("name", "output", "expected"),
    [
        ("yt-dlp", "2026.10.26\n", "2026.10.26"),
        ("deno", "deno 2.9.8 (stable, release, x86_64-unknown-linux-gnu)\nv8 14\n", "2.9.8"),
        ("ffmpeg", "ffmpeg version 8.0.1-3ubuntu2 Copyright (c) 2000-2025\n", "8.0.1-3ubuntu2"),
        ("ffmpeg", "something unexpected\n", "something unexpected"),
    ],
)
def test_parse_version(name, output, expected):
    assert tools.parse_version(name, output) == expected


# --------------------------------------------------------------------------- discovery


def _fake_tool(path: Path, version_line: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{version_line}'\n")
    path.chmod(0o755)
    return path


@pytest.mark.skipif(tools.current_platform().os == "windows", reason="uses shell scripts")
def test_find_tool_prefers_managed_copy(data_dir, tmp_path, monkeypatch):
    system_dir = tmp_path / "system"
    _fake_tool(system_dir / "yt-dlp", "2020.01.01")
    monkeypatch.setenv("PATH", str(system_dir))

    tool = tools.find_tool("yt-dlp")
    assert tool is not None and not tool.managed and tool.version == "2020.01.01"

    _fake_tool(tools.managed_path("yt-dlp"), "2026.10.26")
    tool = tools.find_tool("yt-dlp")
    assert tool is not None and tool.managed and tool.version == "2026.10.26"


@pytest.mark.skipif(tools.current_platform().os == "windows", reason="uses shell scripts")
def test_find_tool_skips_broken_binary(data_dir, tmp_path, monkeypatch):
    broken = tools.managed_path("deno")
    broken.parent.mkdir(parents=True)
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert tools.find_tool("deno") is None


def test_update_refuses_unmanaged_tool():
    tool = tools.Tool("yt-dlp", Path("/usr/bin/yt-dlp"), "2020.01.01", managed=False)
    with pytest.raises(tools.ToolError):
        tools.update_yt_dlp(tool)


# --------------------------------------------------------------------------- archives


def test_extract_member_from_zip(tmp_path):
    archive = tmp_path / "deno.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "readme")
        zf.writestr("nested/deno", "binary")
    dest = tmp_path / "deno"
    tools._extract_member(archive, "deno", dest)
    assert dest.read_text() == "binary"


def test_extract_member_from_tar_xz(tmp_path):
    archive = tmp_path / "ffmpeg.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        for name, content in [("ffmpeg-x/bin/ffmpeg", b"ff"), ("ffmpeg-x/bin/ffprobe", b"fp")]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    dest = tmp_path / "ffprobe"
    tools._extract_member(archive, "ffprobe", dest)
    assert dest.read_bytes() == b"fp"


def test_extract_member_missing(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("other", "x")
    with pytest.raises(tools.ToolError):
        tools._extract_member(archive, "deno", tmp_path / "deno")
