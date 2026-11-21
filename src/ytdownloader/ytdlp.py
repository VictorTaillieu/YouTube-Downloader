"""Pure helpers around the yt-dlp command line: argument building and output parsing.

Nothing here runs a process, so everything is unit-testable. Running is done by ``jobs.py``.

Output protocol: yt-dlp is asked to print machine-readable lines, each starting with a marker:

* ``[ytdl-progress]{...json...}`` - download progress (``--progress-template``);
* ``[ytdl-file]/path/to/final.mp4`` - final file path, after post-processing (``--print``).

Any other line is a regular log line.
"""

from __future__ import annotations

import json
import re
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
    # --flat-playlist: a playlist URL answers at once instead of resolving every video.
    return [
        *_common_args(tools),
        "--dump-single-json",
        "--flat-playlist",
        "--no-warnings",
        "--",
        url,
    ]


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
    # --ignore-errors: a subtitle refused by YouTube (often HTTP 429) must not lose the video.
    args = [
        "--ignore-errors",
        "--sleep-subtitles",
        "1",
        "--write-subs",
        "--sub-langs",
        ",".join(subs.languages),
    ]
    if subs.include_auto:
        args.append("--write-auto-subs")
    embed = subs.embed and opts.mode == "video"
    if embed:
        # Without no-keep-subs, --write-subs leaves the .srt files next to the video.
        args += ["--embed-subs", "--compat-options", "no-keep-subs"]
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
_SUBTITLE_FAILURE = re.compile(r"Unable to download video subtitles for '([^']+)'")
_YOUTUBE_BLOCKED = re.compile(r"not a bot|HTTP Error 429", re.IGNORECASE)
YOUTUBE_BLOCKED_MESSAGE = (
    "YouTube bloque temporairement les requêtes depuis ta connexion. "
    "Réessaie dans quelques minutes."
)


def postprocess_stage(line: str) -> str | None:
    """Label of the post-processing step announced by a log line, if any."""
    tag = line.split(" ", 1)[0]
    return _POSTPROCESS_STAGES.get(tag)


def is_subtitle_file(filename: str) -> bool:
    return filename.lower().endswith(SUBTITLE_EXTENSIONS)


def subtitle_failure(line: str) -> str | None:
    """Language code of a subtitle download failure announced by a log line, if any."""
    match = _SUBTITLE_FAILURE.search(line)
    return match[1] if match else None


def friendly_error(message: str) -> str:
    """Readable text for a yt-dlp error when YouTube is rate-limiting this connection."""
    return YOUTUBE_BLOCKED_MESSAGE if _YOUTUBE_BLOCKED.search(message) else message


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
    audio_channels: int | None = None

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


@dataclass(frozen=True)
class SubtitleChoice:
    code: str
    name: str
    auto: bool
    translated: bool = False
    """Machine translation of the automatic track: often refused by YouTube (HTTP 429)."""


def subtitle_choices(info: VideoInfo) -> list[SubtitleChoice]:
    """Selectable languages: manual first, then automatic with the original track on top.

    A language with a manual track is not repeated as automatic: yt-dlp prefers the manual one.
    Translations can only be told apart when YouTube identifies the original ("-orig") track.
    """
    has_original = any(code.endswith("-orig") for code in info.auto_subtitles)
    manual = [SubtitleChoice(code, name, False) for code, name in info.subtitles.items()]
    auto = [
        SubtitleChoice(code, name, True, has_original and not code.endswith("-orig"))
        for code, name in info.auto_subtitles.items()
        if code not in info.subtitles
    ]
    manual.sort(key=lambda c: c.name.casefold())
    auto.sort(key=lambda c: (not c.code.endswith("-orig"), c.name.casefold()))
    return manual + auto


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
            audio_channels=_int(f.get("audio_channels")),
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


# --------------------------------------------------------------------------- size estimate

# These orders mirror what yt-dlp selects with the "-S" sorts defined at the top of this module.
_H264 = ("avc1", "h264")
_VP9 = ("vp9", "vp09")
_AV1 = ("av01",)
_AAC = ("mp4a", "aac")
_OPUS = ("opus",)
_VIDEO_CODEC_ORDER: dict[str, tuple[tuple[str, ...], ...]] = {
    "mp4": (_H264, _VP9, _AV1),
    "webm": (_VP9, _AV1, _H264),
    "mkv": (_AV1, _VP9, _H264),
}
_AUDIO_CODEC_ORDER: dict[str, tuple[tuple[str, ...], ...]] = {
    "mp4": (_AAC, _OPUS),
    "m4a": (_AAC, _OPUS),
    "webm": (_OPUS, _AAC),
    "opus": (_OPUS, _AAC),
    "mkv": (_OPUS, _AAC),
    "best": (_OPUS, _AAC),
}
_CHANNELS_FIRST = ("mkv", "best")
"""Without an explicit codec preference, yt-dlp favours the track with the most channels."""


def _codec_rank(codec: str | None, order: tuple[tuple[str, ...], ...]) -> int:
    codec = codec or ""
    return next((i for i, prefixes in enumerate(order) if codec.startswith(prefixes)), len(order))


def _audio_size(info: VideoInfo, key: str) -> int | None:
    order = _AUDIO_CODEC_ORDER[key]
    sized = [f for f in info.formats if f.has_audio and not f.has_video and f.filesize]

    def sort_key(f: Format) -> tuple[int, int, int]:
        rank, channels = _codec_rank(f.acodec, order), -(f.audio_channels or 0)
        first, second = (channels, rank) if key in _CHANNELS_FIRST else (rank, channels)
        return first, second, -(f.filesize or 0)

    chosen = min(sized, key=sort_key, default=None)
    return chosen.filesize if chosen else None


def estimate_size(info: VideoInfo, opts: DownloadOptions) -> int | None:
    """Approximate output size in bytes, or None when it cannot be predicted.

    Audio that gets re-encoded (mp3, flac) has no reliable estimate.
    """
    if opts.mode == "audio":
        if opts.audio_format not in _AUDIO_CODEC_ORDER:
            return None
        return _audio_size(info, opts.audio_format)

    videos = [f for f in info.formats if f.has_video and f.height]
    capped = [f for f in videos if opts.max_height is None or (f.height or 0) <= opts.max_height]
    videos = capped or videos
    if not videos:
        return None
    height = max(f.height or 0 for f in videos)
    top = [f for f in videos if f.height == height]
    fps = max(f.fps or 0 for f in top)
    top = [f for f in top if (f.fps or 0) == fps]

    order = _VIDEO_CODEC_ORDER[opts.container]
    video = min(
        (f for f in top if f.filesize),
        key=lambda f: (_codec_rank(f.vcodec, order), not f.has_audio, -(f.filesize or 0)),
        default=None,
    )
    if video is None or video.filesize is None:
        return None
    if video.has_audio:
        return video.filesize
    audio = _audio_size(info, opts.container)
    return video.filesize + audio if audio is not None else None
