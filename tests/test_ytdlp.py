import json
from pathlib import Path

import pytest

from ytdownloader import ytdlp
from ytdownloader.ytdlp import DownloadOptions, SubtitleOptions, ToolPaths

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
OUT = Path("/videos")


def _value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


# --------------------------------------------------------------------------- format selector


def test_format_selector_best():
    assert ytdlp.format_selector(None) == "bv*+ba/b"


def test_format_selector_capped_with_fallback():
    assert ytdlp.format_selector(720) == "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"


# --------------------------------------------------------------------------- download args


def test_download_args_video_defaults():
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT))
    assert args[-2:] == ["--", URL]
    assert _value(args, "--paths") == str(OUT)
    assert _value(args, "--output") == "%(title)s.%(ext)s"
    assert _value(args, "--format") == "bv*+ba/b"
    assert _value(args, "--merge-output-format") == "mp4"
    assert _value(args, "--remux-video") == "mp4"
    assert "vcodec:h264" in _value(args, "--format-sort")
    assert "--no-playlist" in args
    assert "--extract-audio" not in args
    assert "--write-subs" not in args


def test_download_args_machine_readable_output():
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT))
    assert _value(args, "--progress-template").startswith(f"download:{ytdlp.PROGRESS_MARKER}")
    assert _value(args, "--print").startswith(f"after_move:{ytdlp.FILE_MARKER}")
    # --print implies --quiet; logs and progress must stay visible.
    assert "--no-quiet" in args and "--progress" in args and "--newline" in args


@pytest.mark.parametrize("container", ytdlp.VIDEO_CONTAINERS)
def test_download_args_video_container(container):
    args = ytdlp.build_download_args(
        DownloadOptions(URL, OUT, max_height=1080, container=container)
    )
    assert _value(args, "--merge-output-format") == container
    assert _value(args, "--remux-video") == container
    assert "[height<=1080]" in _value(args, "--format")


def test_download_args_audio_mp3():
    opts = DownloadOptions(URL, OUT, mode="audio", audio_format="mp3", audio_quality="192K")
    args = ytdlp.build_download_args(opts)
    assert _value(args, "--format") == "ba/b"
    assert "--extract-audio" in args
    assert _value(args, "--audio-format") == "mp3"
    assert _value(args, "--audio-quality") == "192K"
    assert "--merge-output-format" not in args


def test_download_args_audio_best_keeps_original():
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, mode="audio", audio_format="best"))
    assert "--extract-audio" in args
    assert "--audio-format" not in args


def test_download_args_audio_m4a_prefers_aac():
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, mode="audio", audio_format="m4a"))
    assert _value(args, "--format-sort") == "acodec:aac"


def test_tool_paths_are_forwarded():
    tools = ToolPaths(ffmpeg=Path("/opt/bin/ffmpeg"), deno=Path("/opt/bin/deno"))
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT), tools)
    assert _value(args, "--ffmpeg-location") == "/opt/bin/ffmpeg"
    assert _value(args, "--js-runtimes") == "deno:/opt/bin/deno"


def test_no_tool_paths_by_default():
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT))
    assert "--ffmpeg-location" not in args
    assert "--js-runtimes" not in args


def test_url_after_double_dash_even_if_it_looks_like_an_option():
    args = ytdlp.build_download_args(DownloadOptions("-weird", OUT))
    assert args[-2:] == ["--", "-weird"]


# --------------------------------------------------------------------------- subtitles


def test_subtitles_separate_files():
    subs = SubtitleOptions(["fr", "en"], include_auto=True)
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, subtitles=subs))
    assert "--write-subs" in args
    assert "--write-auto-subs" in args
    assert _value(args, "--sub-langs") == "fr,en"
    assert _value(args, "--convert-subs") == "srt"
    assert "--embed-subs" not in args


def test_subtitles_embedded_in_mp4():
    subs = SubtitleOptions(["fr"], embed=True)
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, subtitles=subs))
    assert "--embed-subs" in args
    assert "--write-auto-subs" not in args
    assert _value(args, "--convert-subs") == "srt"


def test_subtitles_embedded_in_webm_stay_vtt():
    subs = SubtitleOptions(["fr"], embed=True)
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, container="webm", subtitles=subs))
    assert "--embed-subs" in args
    assert "--convert-subs" not in args


def test_subtitle_failure_does_not_abort_and_embed_leaves_no_file():
    subs = SubtitleOptions(["fr"], embed=True)
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, subtitles=subs))
    assert "--ignore-errors" in args
    assert _value(args, "--compat-options") == "no-keep-subs"
    plain = ytdlp.build_download_args(DownloadOptions(URL, OUT))
    assert "--ignore-errors" not in plain


def test_subtitle_failure_line():
    line = "WARNING: Unable to download video subtitles for 'pt-BR': HTTP Error 429"
    assert ytdlp.subtitle_failure(line) == "pt-BR"
    assert ytdlp.subtitle_failure("[info] Writing video subtitles") is None


def test_subtitle_choices_order_and_dedup():
    info = ytdlp.parse_info(
        {
            "id": "x",
            "subtitles": {"fr": [{"name": "French"}], "de": [{"name": "German"}]},
            "automatic_captions": {
                "fr": [{"name": "French"}],
                "ab": [{"name": "Abkhazian"}],
                "en-orig": [{"name": "English (Original)"}],
            },
        }
    )
    choices = [(c.code, c.auto) for c in ytdlp.subtitle_choices(info)]
    assert choices == [("fr", False), ("de", False), ("en-orig", True), ("ab", True)]


def test_translations_need_an_identified_original_track():
    with_original = ytdlp.parse_info(
        {"id": "x", "automatic_captions": {"en-orig": [], "en": [], "fr": []}}
    )
    flags = {c.code: c.translated for c in ytdlp.subtitle_choices(with_original)}
    assert flags == {"en-orig": False, "en": True, "fr": True}
    without = ytdlp.parse_info({"id": "x", "automatic_captions": {"en": [], "fr": []}})
    assert not any(c.translated for c in ytdlp.subtitle_choices(without))


def test_friendly_error():
    bot = "[youtube] abc: Sign in to confirm you're not a bot. Use --cookies"
    assert ytdlp.friendly_error(bot) == ytdlp.YOUTUBE_BLOCKED_MESSAGE
    assert ytdlp.friendly_error("HTTP Error 429: Too Many Requests") == (
        ytdlp.YOUTUBE_BLOCKED_MESSAGE
    )
    assert ytdlp.friendly_error("Video unavailable") == "Video unavailable"


def test_subtitles_never_embedded_in_audio():
    subs = SubtitleOptions(["fr"], embed=True)
    args = ytdlp.build_download_args(DownloadOptions(URL, OUT, mode="audio", subtitles=subs))
    assert "--write-subs" in args
    assert "--embed-subs" not in args


# --------------------------------------------------------------------------- info args


def test_info_args():
    args = ytdlp.build_info_args(URL)
    assert "--dump-single-json" in args
    assert "--no-playlist" in args
    assert args[-2:] == ["--", URL]


# --------------------------------------------------------------------------- output parsing


def test_parse_regular_log_line():
    assert ytdlp.parse_output_line("[youtube] jNQXAC9IVRw: Downloading webpage\n") is None


def test_parse_final_file():
    event = ytdlp.parse_output_line(f"{ytdlp.FILE_MARKER}/videos/Me at the zoo.mp4\n")
    assert event == ytdlp.FinalFile(Path("/videos/Me at the zoo.mp4"))


def test_parse_progress_downloading():
    payload = {
        "status": "downloading",
        "downloaded_bytes": 250,
        "total_bytes": 1000,
        "speed": 512.5,
        "eta": 3,
        "filename": "/videos/a.mp4",
        "_percent_str": " 25.0%",
    }
    event = ytdlp.parse_output_line(ytdlp.PROGRESS_MARKER + json.dumps(payload))
    assert isinstance(event, ytdlp.Progress)
    assert event.fraction == 0.25
    assert event.speed == 512.5
    assert event.eta == 3
    assert event.filename == "/videos/a.mp4"


def test_parse_progress_uses_estimate_and_tolerates_nulls():
    payload = {
        "status": "downloading",
        "downloaded_bytes": 10,
        "total_bytes": None,
        "total_bytes_estimate": 40.0,
        "speed": None,
        "eta": None,
    }
    event = ytdlp.parse_output_line(ytdlp.PROGRESS_MARKER + json.dumps(payload))
    assert isinstance(event, ytdlp.Progress)
    assert event.total_bytes == 40
    assert event.fraction == 0.25
    assert event.speed is None and event.eta is None


def test_parse_progress_unknown_total():
    payload = {"status": "downloading", "downloaded_bytes": 10}
    event = ytdlp.parse_output_line(ytdlp.PROGRESS_MARKER + json.dumps(payload))
    assert isinstance(event, ytdlp.Progress)
    assert event.fraction is None


def test_parse_progress_finished_is_complete():
    event = ytdlp.parse_output_line(ytdlp.PROGRESS_MARKER + '{"status": "finished"}')
    assert isinstance(event, ytdlp.Progress)
    assert event.fraction == 1.0


@pytest.mark.parametrize("payload", ["{not json", "[1, 2]", "null"])
def test_parse_progress_garbage(payload):
    assert ytdlp.parse_output_line(ytdlp.PROGRESS_MARKER + payload) is None


# --------------------------------------------------------------------------- metadata

INFO = {
    "id": "jNQXAC9IVRw",
    "title": "Me at the zoo",
    "uploader": "jawed",
    "duration": 19,
    "thumbnail": "https://i.ytimg.com/vi/jNQXAC9IVRw/maxresdefault.jpg",
    "webpage_url": URL,
    "formats": [
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "filesize": 250000},
        {
            "format_id": "160",
            "ext": "mp4",
            "height": 144,
            "fps": 15,
            "vcodec": "avc1.4d400b",
            "acodec": "none",
            "filesize_approx": 120000,
        },
        {
            "format_id": "133",
            "ext": "mp4",
            "height": 240,
            "vcodec": "avc1.4d400c",
            "acodec": "none",
        },
        {
            "format_id": "18",
            "ext": "mp4",
            "height": 240,
            "vcodec": "avc1.42001E",
            "acodec": "mp4a.40.2",
        },
    ],
    "subtitles": {
        "en": [{"ext": "vtt", "name": "English"}],
        "de": [{"ext": "vtt", "name": "German"}],
        "live_chat": [{"ext": "json"}],
    },
    "automatic_captions": {"fr": [{"ext": "vtt", "name": "French"}], "xx": []},
}


def test_parse_info_basics():
    info = ytdlp.parse_info(INFO)
    assert info.id == "jNQXAC9IVRw"
    assert info.title == "Me at the zoo"
    assert info.uploader == "jawed"
    assert info.duration == 19
    assert len(info.formats) == 5


def test_parse_info_heights_unique_and_sorted():
    assert ytdlp.parse_info(INFO).heights == [240, 144]


def test_parse_info_format_sizes_and_kinds():
    formats = {f.format_id: f for f in ytdlp.parse_info(INFO).formats}
    assert formats["251"].filesize == 250000
    assert formats["160"].filesize == 120000  # filesize_approx fallback
    assert formats["251"].has_audio and not formats["251"].has_video
    assert formats["18"].has_audio and formats["18"].has_video


def test_parse_info_subtitles():
    info = ytdlp.parse_info(INFO)
    assert info.subtitles == {"en": "English", "de": "German"}  # live_chat ignored
    assert info.auto_subtitles == {"fr": "French", "xx": "xx"}  # code used when no name


def test_parse_info_minimal_document():
    info = ytdlp.parse_info({"id": "abc"})
    assert info.title == "abc"
    assert info.formats == [] and info.heights == []
    assert info.subtitles == {} and info.auto_subtitles == {}


def test_parse_info_json_rejects_non_object():
    with pytest.raises(ValueError):
        ytdlp.parse_info_json("[]")


def test_is_youtube_url():
    from ytdownloader.ytdlp import is_youtube_url

    for url in (
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "http://m.youtube.com/watch?v=abc",
        "https://music.youtube.com/watch?v=abc",
    ):
        assert is_youtube_url(url)
    for text in (
        "",
        "hello",
        "ftp://youtube.com/x",
        "https://notyoutube.com/x",
        "https://youtube.com.evil.org/x",
        "https://youtu.be/a b",
    ):
        assert not is_youtube_url(text)


def test_format_progress():
    from ytdownloader.ytdlp import Progress, format_progress

    progress = Progress("downloading", 42, 100, 3_100_000.0, 72, "a.mp4")
    assert format_progress(progress) == "42 % · 3,1 Mo/s · ETA 1:12"
    unknown = Progress("downloading", 2_500_000, None, None, None, "a.mp4")
    assert format_progress(unknown) == "2,5 Mo"


def test_postprocess_stage():
    from ytdownloader.ytdlp import postprocess_stage

    assert postprocess_stage('[Merger] Merging formats into "a.mp4"').startswith("Fusion")
    assert postprocess_stage("[youtube] abc: Downloading webpage") is None


def _size_info():
    from ytdownloader.ytdlp import Format, VideoInfo

    formats = [
        Format("avc", "mp4", 1080, 30, "avc1.64", "none", 100),
        Format("vp9", "webm", 1080, 30, "vp9", "none", 80),
        Format("low", "mp4", 480, 30, "avc1.4d", "none", 30),
        Format("aac", "m4a", None, None, "none", "mp4a.40.2", 10),
        Format("opus", "webm", None, None, "none", "opus", 12),
    ]
    return VideoInfo("i", "t", None, 1, None, None, formats, {}, {})


def test_estimate_size_follows_options():
    from pathlib import Path

    from ytdownloader.ytdlp import DownloadOptions, estimate_size

    info = _size_info()
    base = {"url": "u", "output_dir": Path("/o")}
    assert estimate_size(info, DownloadOptions(**base)) == 110
    assert estimate_size(info, DownloadOptions(**base, container="webm")) == 92
    assert estimate_size(info, DownloadOptions(**base, max_height=720)) == 40
    assert estimate_size(info, DownloadOptions(**base, max_height=144)) == 110  # same fallback
    audio = {**base, "mode": "audio"}
    assert estimate_size(info, DownloadOptions(**audio, audio_format="m4a")) == 10
    assert estimate_size(info, DownloadOptions(**audio, audio_format="opus")) == 12
    assert estimate_size(info, DownloadOptions(**audio, audio_format="mp3")) is None


def test_estimate_size_mirrors_yt_dlp_codec_choices():
    from pathlib import Path

    from ytdownloader.ytdlp import DownloadOptions, Format, VideoInfo, estimate_size

    formats = [
        Format("av1", "mp4", 1080, 30, "av01.0", "none", 50),
        Format("vp9", "webm", 1080, 30, "vp9", "none", 70),
        Format("avc", "mp4", 1080, 30, "avc1.64", "none", 90),
        Format("18", "mp4", 360, 30, "avc1.42", "mp4a.40.2", 9),
        Format("v360", "mp4", 360, 30, "avc1.4d", "none", 20),
        Format("aac2", "m4a", None, None, "none", "mp4a.40.2", 10, 2),
        Format("aac6", "m4a", None, None, "none", "mp4a.40.2", 30, 6),
        Format("opus", "webm", None, None, "none", "opus", 8, 2),
    ]
    info = VideoInfo("i", "t", None, 1, None, None, formats, {}, {})
    base = {"url": "u", "output_dir": Path("/o")}
    assert estimate_size(info, DownloadOptions(**base, container="mkv")) == 80  # AV1 + 5.1 AAC
    assert estimate_size(info, DownloadOptions(**base, container="webm")) == 78  # VP9 + Opus
    assert estimate_size(info, DownloadOptions(**base, max_height=360)) == 9  # combined stream
    audio = {**base, "mode": "audio"}
    assert estimate_size(info, DownloadOptions(**audio, audio_format="best")) == 30
    assert estimate_size(info, DownloadOptions(**audio, audio_format="opus")) == 8
