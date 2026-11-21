"""Pure helpers around the yt-dlp command line: argument building and output parsing.

Nothing here runs a process, so everything is unit-testable. Running is done by ``jobs.py``.

Output protocol: yt-dlp is asked to print machine-readable lines, each starting with a marker:

* ``[ytdl-progress]{...json...}`` - download progress (``--progress-template``);
* ``[ytdl-file]/path/to/final.mp4`` - final file path, after post-processing (``--print``).

Any other line is a regular log line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

PROGRESS_MARKER = "[ytdl-progress]"
FILE_MARKER = "[ytdl-file]"

DEFAULT_OUTPUT_TEMPLATE = "%(title)s.%(ext)s"

Mode = Literal["video", "audio"]
Container = Literal["mp4", "mkv", "webm"]
AudioFormat = Literal["best", "mp3", "m4a", "opus", "flac"]

VIDEO_CONTAINERS: tuple[Container, ...] = ("mp4", "mkv", "webm")
AUDIO_FORMATS: tuple[AudioFormat, ...] = ("best", "mp3", "m4a", "opus", "flac")

# Codec preferences (yt-dlp "-S" sort) so the chosen streams fit the container without
# re-encoding and play everywhere: H.264/AAC for mp4, VP9/Opus for webm, best quality for mkv.
_VIDEO_SORT: dict[str, str] = {
    "mp4": "res,fps,vcodec:h264,acodec:aac",
    "webm": "res,fps,vcodec:vp9,acodec:opus",
    "mkv": "res,fps",
}
_AUDIO_SORT: dict[str, str] = {
    "m4a": "acodec:aac",
    "opus": "acodec:opus",
}


# --------------------------------------------------------------------------- options


@dataclass
class SubtitleOptions:
    languages: list[str] = field(default_factory=list)
    """Language codes (e.g. ["fr", "en"]). Empty list = no subtitles."""
    include_auto: bool = False
    """Also accept YouTube automatic captions when no manual subtitle exists."""
    embed: bool = False
    """Embed in the video file instead of writing separate .srt files (video mode only)."""


@dataclass
class DownloadOptions:
    url: str
    output_dir: Path
    mode: Mode = "video"
    max_height: int | None = None
    """Maximum video height (1080, 720...). None = best available."""
    container: Container = "mp4"
    audio_format: AudioFormat = "mp3"
    audio_quality: str = "0"
    """yt-dlp --audio-quality: 0 (best VBR) .. 10 (worst), or a bitrate such as "192K"."""
    subtitles: SubtitleOptions = field(default_factory=SubtitleOptions)
    output_template: str = DEFAULT_OUTPUT_TEMPLATE


@dataclass
class ToolPaths:
    """Locations of the helper binaries, passed explicitly to yt-dlp when known."""

    ffmpeg: Path | None = None
    deno: Path | None = None


# --------------------------------------------------------------------------- arguments


def _common_args(tools: ToolPaths | None) -> list[str]:
    args = ["--no-playlist", "--color", "never"]
    if tools and tools.ffmpeg:
        args += ["--ffmpeg-location", str(tools.ffmpeg)]
    if tools and tools.deno:
        args += ["--js-runtimes", f"deno:{tools.deno}"]
    return args


def format_selector(max_height: int | None) -> str:
    """yt-dlp ``-f`` expression for video mode, capped at ``max_height`` when given.

    Falls back to the best available format so a download never fails just because
    the video has no stream under the requested height.
    """
    if max_height is None:
        return "bv*+ba/b"
    h = f"[height<={max_height}]"
    return f"bv*{h}+ba/b{h}/bv*+ba/b"


def build_info_args(url: str, tools: ToolPaths | None = None) -> list[str]:
    """Arguments to fetch the video metadata as a single JSON document on stdout."""
    return [*_common_args(tools), "--dump-single-json", "--no-warnings", "--", url]


def build_download_args(opts: DownloadOptions, tools: ToolPaths | None = None) -> list[str]:
    """Arguments (without the executable) to download ``opts.url``."""
    args = _common_args(tools)
    args += [
        "--newline",
        "--progress",
        "--progress-template",
        f"download:{PROGRESS_MARKER}%(progress)j",
        "--print",
        f"after_move:{FILE_MARKER}%(filepath)s",
        "--no-quiet",  # --print implies --quiet, which would hide the useful log lines
        "--no-mtime",  # file date = download date, not upload date
        "--paths",
        str(opts.output_dir),
        "--output",
        opts.output_template,
    ]

    if opts.mode == "audio":
        args += ["--format", "ba/b", "--extract-audio"]
        if sort := _AUDIO_SORT.get(opts.audio_format):
            args += ["--format-sort", sort]
        if opts.audio_format != "best":
            args += ["--audio-format", opts.audio_format, "--audio-quality", opts.audio_quality]
    else:
        args += [
            "--format",
            format_selector(opts.max_height),
            "--format-sort",
            _VIDEO_SORT[opts.container],
            "--merge-output-format",
            opts.container,
            "--remux-video",
            opts.container,
        ]

    args += _subtitle_args(opts)
    args += ["--", opts.url]
    return args


def _subtitle_args(opts: DownloadOptions) -> list[str]:
    subs = opts.subtitles
    if not subs.languages:
        return []
    args = ["--write-subs", "--sub-langs", ",".join(subs.languages)]
    if subs.include_auto:
        args.append("--write-auto-subs")
    embed = subs.embed and opts.mode == "video"
    if embed:
        args.append("--embed-subs")
    # webm can only hold WebVTT subtitles: keep the original format when embedding there.
    if not (embed and opts.container == "webm"):
        args += ["--convert-subs", "srt"]
    return args


# --------------------------------------------------------------------------- output parsing


@dataclass(frozen=True)
class Progress:
    status: str
    """"downloading", "finished" or "error"."""
    downloaded_bytes: int | None
    total_bytes: int | None
    """Exact size when known, otherwise yt-dlp's estimate."""
    speed: float | None
    """Bytes per second."""
    eta: int | None
    """Seconds."""
    filename: str | None

    @property
    def fraction(self) -> float | None:
        """Progress in [0, 1], or None when the total size is unknown."""
        if self.status == "finished":
            return 1.0
        if self.downloaded_bytes is None or not self.total_bytes:
            return None
        return min(self.downloaded_bytes / self.total_bytes, 1.0)


@dataclass(frozen=True)
class FinalFile:
    path: Path


OutputEvent = Progress | FinalFile


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def parse_output_line(line: str) -> OutputEvent | None:
    """Parse one stdout line of a download; None for regular log lines."""
    line = line.rstrip("\r\n")
    if line.startswith(FILE_MARKER):
        return FinalFile(Path(line[len(FILE_MARKER) :]))
    if not line.startswith(PROGRESS_MARKER):
        return None
    try:
        data = json.loads(line[len(PROGRESS_MARKER) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return Progress(
        status=str(data.get("status", "downloading")),
        downloaded_bytes=_int(data.get("downloaded_bytes")),
        total_bytes=_int(data.get("total_bytes")) or _int(data.get("total_bytes_estimate")),
        speed=_num(data.get("speed")),
        eta=_int(data.get("eta")),
        filename=data.get("filename"),
    )


_POSTPROCESS_STAGES = {
    "[Merger]": "Fusion de l'audio et de la vidéo…",
    "[ExtractAudio]": "Conversion audio…",
    "[VideoRemuxer]": "Finalisation du conteneur…",
    "[SubtitlesConvertor]": "Conversion des sous-titres…",
    "[EmbedSubtitle]": "Intégration des sous-titres…",
}
SUBTITLE_EXTENSIONS = (".vtt", ".srt", ".ass", ".ttml", ".srv1", ".srv2", ".srv3", ".json3")


def postprocess_stage(line: str) -> str | None:
    """Label of the post-processing step announced by a log line, if any."""
    tag = line.split(" ", 1)[0]
    return _POSTPROCESS_STAGES.get(tag)


def is_subtitle_file(filename: str) -> bool:
    return filename.lower().endswith(SUBTITLE_EXTENSIONS)


def is_youtube_url(text: str) -> bool:
    """True for an http(s) URL on a YouTube host (used for clipboard auto-paste)."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return False
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return host in ("youtu.be", "youtube.com") or host.endswith(".youtube.com")


def format_size(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("o", "Ko", "Mo"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1000
    return f"{value:.2f} Go".replace(".", ",")


def format_eta(seconds: int) -> str:
    minutes, secs = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def format_progress(progress: Progress) -> str:
    """Status text such as ``42 % · 3,1 Mo/s · ETA 0:12``."""
    parts: list[str] = []
    if (fraction := progress.fraction) is not None:
        parts.append(f"{fraction * 100:.0f} %")
    elif progress.downloaded_bytes is not None:
        parts.append(format_size(progress.downloaded_bytes))
    if progress.speed:
        parts.append(f"{format_size(progress.speed)}/s")
    if progress.eta is not None and progress.status == "downloading":
        parts.append(f"ETA {format_eta(progress.eta)}")
    return " · ".join(parts)


# --------------------------------------------------------------------------- metadata


@dataclass(frozen=True)
class Format:
    format_id: str
    ext: str
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    filesize: int | None
    """Exact or approximate size in bytes."""

    @property
    def has_video(self) -> bool:
        return self.vcodec not in (None, "none")

    @property
    def has_audio(self) -> bool:
        return self.acodec not in (None, "none")


@dataclass(frozen=True)
class VideoInfo:
    id: str
    title: str
    uploader: str | None
    duration: int | None
    """Seconds."""
    thumbnail: str | None
    webpage_url: str | None
    formats: list[Format]
    subtitles: dict[str, str]
    """Manual subtitles: language code -> language name."""
    auto_subtitles: dict[str, str]
    """Automatic captions: language code -> language name."""

    @property
    def heights(self) -> list[int]:
        """Available video heights, highest first."""
        return sorted({f.height for f in self.formats if f.has_video and f.height}, reverse=True)


def _sub_languages(tracks: Any) -> dict[str, str]:
    if not isinstance(tracks, dict):
        return {}
    languages: dict[str, str] = {}
    for code, entries in tracks.items():
        if code == "live_chat":
            continue
        name = code
        if isinstance(entries, list):
            name = next((e["name"] for e in entries if isinstance(e, dict) and e.get("name")), code)
        languages[code] = name
    return languages


def _codec(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def parse_info(data: dict[str, Any]) -> VideoInfo:
    """Build a ``VideoInfo`` from yt-dlp's ``--dump-single-json`` document."""
    formats = [
        Format(
            format_id=str(f.get("format_id", "")),
            ext=str(f.get("ext", "")),
            height=_int(f.get("height")),
            fps=_num(f.get("fps")),
            vcodec=_codec(f.get("vcodec")),
            acodec=_codec(f.get("acodec")),
            filesize=_int(f.get("filesize")) or _int(f.get("filesize_approx")),
        )
        for f in data.get("formats") or []
        if isinstance(f, dict)
    ]
    return VideoInfo(
        id=str(data.get("id", "")),
        title=str(data.get("title") or data.get("id") or ""),
        uploader=data.get("uploader") or data.get("channel"),
        duration=_int(data.get("duration")),
        thumbnail=data.get("thumbnail"),
        webpage_url=data.get("webpage_url"),
        formats=formats,
        subtitles=_sub_languages(data.get("subtitles")),
        auto_subtitles=_sub_languages(data.get("automatic_captions")),
    )


def parse_info_json(text: str) -> VideoInfo:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("yt-dlp returned unexpected JSON")
    return parse_info(data)
