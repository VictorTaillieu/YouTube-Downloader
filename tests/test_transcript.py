from ytdownloader.transcript import unique_path, vtt_to_text

ROLLING = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.990 align:start position:0%

hello<00:00:00.500><c> world</c>

00:00:01.990 --> 00:00:02.000 align:start position:0%
hello world


00:00:02.000 --> 00:00:04.000 align:start position:0%
hello world
this<00:00:02.500><c> is</c><00:00:03.000><c> fine</c>

00:00:04.000 --> 00:00:04.010 align:start position:0%
this is fine


00:00:09.000 --> 00:00:10.000 align:start position:0%
this is fine
after<00:00:09.500><c> pause</c>
"""


def test_rolling_captions_are_deduplicated():
    assert vtt_to_text(ROLLING) == "hello world this is fine\n\nafter pause\n"


def test_manual_chorus_is_kept():
    vtt = """WEBVTT

00:01.000 --> 00:02.000
La la la

00:02.000 --> 00:03.000
La la la
"""
    assert vtt_to_text(vtt) == "La la la La la la\n"


def test_markup_annotations_and_speakers_are_removed():
    vtt = """WEBVTT

NOTE this is ignored

intro
00:00:01.000 --> 00:00:02.000 line:90%
&gt;&gt; <i>Tom</i> &amp; Jerry [Music]

00:00:02.000 --> 00:00:03.000
[Applause]

00:00:03.000 --> 00:00:04.000
1 &lt; 2 >> ok
"""
    assert vtt_to_text(vtt) == "Tom & Jerry 1 < 2 ok\n"


def test_pause_starts_a_paragraph():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:02.000
One.

00:00:03.900 --> 00:00:04.000
Two.

00:00:06.000 --> 00:00:07.000
Three.
"""
    assert vtt_to_text(vtt) == "One. Two.\n\nThree.\n"


def test_track_without_speech_is_empty():
    assert vtt_to_text("WEBVTT\n\n00:01.000 --> 00:02.000\n[Music]\n") == ""


def test_unique_path_never_reuses_a_file(tmp_path):
    target = tmp_path / "titre.fr.txt"
    assert unique_path(target) == target
    target.touch()
    assert unique_path(target).name == "titre.fr (2).txt"
    (tmp_path / "titre.fr (2).txt").touch()
    assert unique_path(target).name == "titre.fr (3).txt"
